"""Offline regression: python3 -m diagnostics.interruption_diag.
Synthetic waveforms and fake Live service only; no recordings or account access.
"""
import ast
import asyncio
from pathlib import Path
import threading
import time
import types as pytypes
import numpy as np
from google.genai import types
from core.interruption import Interruption

ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'main.py').read_text())
cls=next(n for n in tree.body if getattr(n,'name','')=='JarvisLive')
scope={'np':np,'time':time,'asyncio':asyncio,'types':types,'runtime_event':lambda *args,**kw:None,
       '_LEVEL_FLOOR':60.,'_LEVEL_FULL':2600.}
node=next(n for n in tree.body if getattr(n,'name','')=='_pcm_level')
exec(compile(ast.Module(body=[node],type_ignores=[]),'main.py','exec'),scope)
for name in ('interrupt','_enqueue_speech_events','_send_realtime','_relay_phone_audio'):
    node=next(n for n in cls.body if getattr(n,'name','')==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'main.py','exec'),scope)
listen=next(n for n in cls.body if getattr(n,'name','')=='_listen_audio')
callback=next(n for n in listen.body if getattr(n,'name','')=='callback')
exec(compile(ast.Module(body=[callback],type_ignores=[]),'main.py','exec'),scope)


def waveform(pitch,seconds=5,amplitude=2500):
    t=np.arange(int(seconds*16000))/16000
    phase=np.cumsum(2*np.pi*(pitch+23*np.sin(t*4.3))/16000)
    return (sum(np.sin(k*phase)/k for k in range(1,11))*amplitude*
            (.3+.7*np.maximum(0,np.sin(t*17)))).astype(np.int16)


def formant_speech(pitch,seconds=5,amplitude=2500):
    # Changing vowel resonances, unvoiced consonants and syllable gaps expose
    # false correlations which a steady harmonic test tone cannot detect.
    rng=np.random.default_rng(int(pitch));t=np.arange(int(seconds*16000))/16000
    phase=np.cumsum(2*np.pi*(pitch+19*np.sin(t*4.3))/16000)
    source=sum(np.sin(k*phase) for k in range(1,30));result=np.zeros(len(t))
    vowels=((350,950,2200),(550,1700,2500),(750,1200,2700),(300,2100,2800))
    for i in range(0,len(t),1024):
        x=source[i:i+1024].copy();n=len(x);freq=np.fft.rfftfreq(n,1/16000)
        gain=sum(np.exp(-.5*((freq-center)/110)**2) for center in vowels[(i//4096)%4])
        x=np.fft.irfft(np.fft.rfft(x)*gain,n)
        if i//1024%7==4:x=rng.normal(0,.6,n)
        result[i:i+n]=x*np.clip(np.sin(t[i:i+n]*17),0,1)**.6
    result*=amplitude/max(1,np.sqrt(np.mean(result**2)))
    return np.clip(result,-12000,12000).astype(np.int16)


def acoustics(mode='echo',delay=600,formants=False):
    generate=formant_speech if formants else waveform
    far=generate(215);human=generate(147,amplitude=1800)
    t=np.arange(len(far))/16000;near=np.roll(far.astype(float),delay)*.6+np.roll(far.astype(float),delay+1200)*.13
    near[:delay+1200]=0
    rng=np.random.default_rng(38)
    if mode=='volume':near[t>2]*=3
    if mode=='clipped':near=3500*np.tanh(near/1300)
    if mode=='colored':near=np.convolve(near,[1,-.65,.3],mode='same')
    if mode=='noise':near+=rng.normal(0,60,len(near))+100*np.sin(2*np.pi*60*t)
    if mode=='click':near[33000:33040]+=12000
    if mode=='human':near[t>2.5]+=human[t>2.5]
    if mode=='headphone':near[:]=0;near[t>2.5]=human[t>2.5]
    if mode=='missing_reference':near[:]=human
    engine=Interruption();clock=[0.];real_process=engine.process
    engine.process=lambda pcm,speaking=False:real_process(pcm,speaking,now=clock[0])
    owner=pytypes.SimpleNamespace(_speech=engine,_is_speaking=True,_speaking_lock=threading.Lock(),
        _wake_enabled=False,_phone_active=False,_ptt_enabled=False,_interrupted=False,_loop=None,
        _awake=True,_active_tools=0,_response_pending=True,_proactive_turn=False,_turn_done_event=None,
        ui=pytypes.SimpleNamespace(muted=False,set_audio_level=lambda _:None,write_log=lambda _:None),
        _visemes=pytypes.SimpleNamespace(reset=lambda:None),_speech_diag_at=0.,
        session=object(),out_queue=asyncio.Queue(maxsize=200),audio_in_queue=asyncio.Queue())
    trips=[]
    def interrupt(**kw):
        trips.append(clock[0]);scope['interrupt'](owner,**kw)
    owner.interrupt=interrupt
    owner.set_speaking=lambda value:setattr(owner,'_is_speaking',value)
    owner._enqueue_speech_events=lambda *args:scope['_enqueue_speech_events'](owner,*args)
    scope['self']=owner;scope['loop']=pytypes.SimpleNamespace(call_soon_threadsafe=lambda f,*a:f(*a))
    far24=np.interp(np.arange(120000)*2/3,np.arange(len(far)),far).astype(np.int16)
    ci=pi=0;capture=.064;play=0.;start=time.perf_counter()
    while ci+1024<len(near):
        if play<=capture and pi+1200<len(far24):
            if mode!='missing_reference':engine.playback(far24[pi:pi+1200],now=play)
            pi+=1200;play+=.05
        else:
            clock[0]=capture
            scope['callback'](np.clip(near[ci:ci+1024],-32768,32767).astype(np.int16).reshape(-1,1),1024,None,None)
            assert engine.clean_rms<=engine.raw_rms+1.,'filter amplified microphone'
            assert 0.<=engine.match<=1.,'invalid reference correlation'
            assert not any(stamp<2.5 for stamp in trips),f'{mode} self-interrupted at {trips}'
            ci+=1024;capture+=.064
    if mode in ('human','headphone'):
        assert len(trips)==1 and trips[0]<3.4,f'user speech failed to interrupt: {mode} {trips}'
        queued=list(owner.out_queue._queue)
        assert queued[0]['kind']=='start' and any(item['kind']=='audio' for item in queued)
    else:
        assert not trips and owner.out_queue.empty(),f'{mode} opened Live input activity'
    print('PASS:',mode,'formants' if formants else 'harmonics','delay',delay,'CPU ms',round((time.perf_counter()-start)*1000))


def room_coloration(shape,overlap=False):
    # This case reproduced a false interruption in the installed scalar filter.
    # Fractional delay, speaker equalization, reflections and noise must not
    # masquerade as a second voice just because scalar correlation is modest.
    far=formant_speech(215,seconds=7).astype(float)
    human=formant_speech(147,seconds=7,amplitude=1800).astype(float)
    far24=np.interp(np.arange(7*24000)*2/3,np.arange(len(far)),far).astype(np.int16)
    colored=np.convolve(far,shape,mode='same')
    colored*=1.2*np.sqrt(np.mean(far**2))/max(1,np.sqrt(np.mean(colored**2)))
    index=np.arange(len(far))
    near=np.interp(index-650.7,index,colored,left=0)*.55+np.interp(index-2210.3,index,colored,left=0)*.14
    near+=np.random.default_rng(3).normal(0,65,len(far))
    if overlap:near[index/16000>3.5]+=human[index/16000>3.5]
    engine=Interruption();ci=pi=0;capture=.064;play=0.;trips=[]
    while ci+1024<len(near):
        if play<=capture and pi+1200<len(far24):
            engine.playback(far24[pi:pi+1200],now=play);pi+=1200;play+=.05
        else:
            events=engine.process(np.clip(near[ci:ci+1024],-32768,32767).astype(np.int16),speaking=True,now=capture)
            assert engine.clean_rms<=engine.raw_rms+1.,'room filter amplified mic'
            if any(kind=='interrupt' for kind,_ in events):trips.append(capture)
            ci+=1024;capture+=.064
    if overlap:assert len(trips)==1 and 3.5<trips[0]<4.3,('room user interruption failed',shape,trips)
    else:assert not trips,('colored playback self-interrupted',shape,trips)
    print('PASS: room coloration',shape,'overlapping user' if overlap else 'echo only')


async def transport():
    recorded=[]
    class Session:
        async def send_realtime_input(self,**kw):recorded.append(kw)
    owner=pytypes.SimpleNamespace(out_queue=asyncio.Queue(),_speech=Interruption(.5),
        ui=pytypes.SimpleNamespace(muted=False),session=Session(),_is_speaking=False,
        _last_live_response_at=0.,_last_voice_forwarded_at=0.)
    worker=asyncio.create_task(scope['_send_realtime'](owner))
    try:
        # The engine's actual emitted boundaries are sent without a second VAD.
        speech=waveform(147,seconds=.5)
        for i in range(0,len(speech)-1024,1024):
            for kind,data in owner._speech.process(speech[i:i+1024]):
                await owner.out_queue.put({'kind':kind,'data':data})
            await asyncio.sleep(.005)
        assert 'activity_start' in recorded[0] and any('audio' in item for item in recorded)
        assert owner._last_voice_forwarded_at>0
        await asyncio.sleep(.65);assert 'activity_end' in recorded[-1]
        before=len(recorded);owner.ui.muted=True
        await owner.out_queue.put({'kind':'start','data':None});await asyncio.sleep(.02)
        assert len(recorded)==before,'mute opened an input turn'
        await owner.out_queue.put({'control':'stop'});await asyncio.sleep(.02)
        assert 'activity_start' in recorded[-2] and 'activity_end' in recorded[-1]
    finally:
        worker.cancel()
        try:await worker
        except asyncio.CancelledError:pass
    print('PASS: actual sender activity/audio/end, pause tolerance, mute and explicit stop')


async def phone_relay():
    queue=asyncio.Queue();owner=pytypes.SimpleNamespace(
        _dashboard=pytypes.SimpleNamespace(_phone_audio_queue=queue),
        _is_speaking=True,ui=pytypes.SimpleNamespace(muted=False),out_queue=asyncio.Queue())
    worker=asyncio.create_task(scope['_relay_phone_audio'](owner))
    try:
        chunk={'data':bytes(2048),'mime_type':'audio/pcm;rate=16000'}
        await queue.put(chunk)
        assert await asyncio.wait_for(owner.out_queue.get(),.3)==chunk
        assert owner._phone_active
        owner.ui.muted=True;await queue.put(chunk);await asyncio.sleep(.02)
        assert owner.out_queue.empty(),'muted phone audio bypassed speech control'
    finally:
        worker.cancel()
        try:await worker
        except asyncio.CancelledError:pass
    print('PASS: phone speech reaches the single controller during playback; mute holds')


def listening():
    engine=Interruption();voice=waveform(147,seconds=.7);events=[]
    for i in range(0,len(voice)-1024,1024):events+=engine.process(voice[i:i+1024],now=i/16000)
    assert any(kind=='start' for kind,_ in events) and not any(kind=='interrupt' for kind,_ in events)
    assert engine.poll(now=2)==[('end',None)]
    engine.reset()
    for i in range(30):
        t=(np.arange(1024)+i*1024)/16000
        assert not engine.process((150*np.sin(t*2*np.pi*240)).astype(np.int16),now=i*.064),'fan tone became speech'
    print('PASS: ordinary user speech, silence completion and fan-tone rejection')


if __name__=='__main__':
    for delay in (128,600,1600,4800):acoustics(delay=delay)
    for mode in ('volume','clipped','colored','noise','click','human','headphone','missing_reference'):acoustics(mode)
    for mode in ('echo','volume','clipped','noise','human'):acoustics(mode,delay=650,formants=True)
    for shape in ([1,-.9],[1,-1.6,.7],[.2,.7,-.8,.2],[1,.8,.4,.2],[.2,-.3,1,-.7,.2]):
        room_coloration(shape);room_coloration(shape,overlap=True)
    listening();asyncio.run(transport());asyncio.run(phone_relay())
