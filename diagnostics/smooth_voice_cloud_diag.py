"""Offline regression: python3 -m diagnostics.smooth_voice_cloud_diag.
No accounts, network, user files, microphone or speakers are used.
"""
import ast
import asyncio
import copy
import os
from pathlib import Path
import threading
import time
import types as pytypes
from google.genai import types
import numpy as np
from core import cloud_bridge as cloud
from core.interruption import Interruption

ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'main.py').read_text())
cls=next(n for n in tree.body if getattr(n,'name','')=='JarvisLive')
events=[]
scope={'asyncio':asyncio,'time':time,'np':np,'types':types,'runtime_event':lambda *args,**kw:events.append((args,kw)),
       '_clean_transcript':lambda s:s,'_is_repeat_chunk':lambda *args:False,
       'speaker_metadata_diagnostics_enabled':lambda:False,'_REPEAT_MIN':40}
scope.update(_LEVEL_FLOOR=60.,_LEVEL_FULL=2600.)
node=next(n for n in tree.body if getattr(n,'name','')=='_pcm_level')
exec(compile(ast.Module(body=[node],type_ignores=[]),'main.py','exec'),scope)
for name in ('interrupt','_enqueue_speech_events','_send_realtime','_receive_audio','_process_live_tools','_run_cloud_heartbeat','_run_cloud_bridge'):
    node=next(n for n in cls.body if getattr(n,'name','')==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'main.py','exec'),scope)


async def interruptions():
    calls=[];logs=[];tool_started=asyncio.Event();tool_release=asyncio.Event();tool_replies=[]
    ack=pytypes.SimpleNamespace(interrupted=True,turn_complete=False,
        output_transcription=None,input_transcription=None)
    class Session:
        reads=0
        async def send_realtime_input(self,**kw):calls.append(kw)
        async def send_tool_response(self,**kw):tool_replies.append(kw)
        async def receive(self):
            self.reads+=1
            if self.reads>1:raise asyncio.CancelledError()
            yield pytypes.SimpleNamespace(data=None,server_content=None,
                tool_call=pytypes.SimpleNamespace(function_calls=[types.FunctionCall(id='slow',name='fake',args={})]))
            await asyncio.sleep(.01)  # let the tool worker begin slow execution
            yield pytypes.SimpleNamespace(data=None,server_content=None,tool_call=None,
                tool_call_cancellation=pytypes.SimpleNamespace(ids=['slow']))
            for data,sc in ((None,ack),(b'new response pcm',None),
                (None,pytypes.SimpleNamespace(interrupted=False,turn_complete=True,
                    output_transcription=pytypes.SimpleNamespace(text='New reply'),input_transcription=None))):
                yield pytypes.SimpleNamespace(data=data,server_content=sc,tool_call=None)
    owner=pytypes.SimpleNamespace(_loop=asyncio.get_running_loop(),_interrupted=False,
        _response_pending=True,_is_speaking=False,_active_tools=0,_proactive_turn=False,
        out_queue=asyncio.Queue(),audio_in_queue=asyncio.Queue(),_turn_done_event=asyncio.Event(),
        _last_live_response_at=0.,_last_voice_forwarded_at=0.,_last_user_speech=0.,
        _speech=Interruption(.5),_visemes=pytypes.SimpleNamespace(reset=lambda:None,feed_text=lambda _:None),
        _awake=True,ui=pytypes.SimpleNamespace(muted=False,write_log=lambda text,**kw:logs.append(text),pet_event=lambda _:None),
        _tail_active=lambda:False,set_speaking=lambda _:None,session=Session(),_dashboard=None,
        _last_out_logged='',_asst_name='Gojo',_session_log=[],_note_user_request=lambda _:None,
        _remember_chat_turn=lambda *args:None)
    owner._live_tool_queue=asyncio.Queue(maxsize=32);owner._cancelled_live_tools=set()
    async def execute(fc,**kw):
        tool_started.set();await tool_release.wait()
        return types.FunctionResponse(id=fc.id,name=fc.name,response={'result':'done'})
    async def flush():pass
    owner._execute_tool=execute;owner._flush_pending_vision=flush
    worker=asyncio.create_task(scope['_process_live_tools'](owner))
    owner.interrupt=lambda **kw:scope['interrupt'](owner,**kw)
    owner.request_reconnect=lambda **kw:(_ for _ in ()).throw(AssertionError('interruption reconnects'))
    await owner.out_queue.put({'data':b'stale mic noise','speech':False})
    await owner.audio_in_queue.put(b'obsolete answer')
    scope['_enqueue_speech_events'](owner,owner.out_queue,[('interrupt',None),('start',None),('audio',b'first syllable'),('audio',b'command tail')])
    owner.interrupt(origin='voice')
    assert owner.out_queue.qsize()==3 and owner.audio_in_queue.empty()
    sender=asyncio.create_task(scope['_send_realtime'](owner))
    await asyncio.sleep(.02)
    assert 'activity_start' in calls[0]
    assert [m['audio'].data for m in calls if 'audio' in m]==[b'first syllable',b'command tail']
    sender.cancel()
    try:await sender
    except asyncio.CancelledError:pass
    try:await scope['_receive_audio'](owner)
    except asyncio.CancelledError:pass
    assert not owner._interrupted and await owner.audio_in_queue.get()==b'new response pcm'
    assert 'Gojo: New reply' in logs,'new response was discarded after interrupt acknowledgement'
    assert tool_started.is_set() and not tool_release.is_set(),'receive path waited for slow tool'
    tool_release.set();await asyncio.sleep(.01)
    assert not tool_replies,'canceled tool reply was sent back to the server'
    await owner._live_tool_queue.put((owner.session,[types.FunctionCall(id='valid',name='fake',args={})],False))
    await asyncio.sleep(.01)
    assert tool_replies[0]['function_responses'][0].id=='valid'
    worker.cancel()
    try:await worker
    except asyncio.CancelledError:pass
    class UnconfirmedSession:
        async def receive(self):
            yield pytypes.SimpleNamespace(data=None,server_content=ack,tool_call=None)
            raise asyncio.CancelledError()
    owner.session=UnconfirmedSession();owner._last_confirmed_input_at=-10.
    owner._interrupted=False
    await owner.audio_in_queue.put(b'keep playback')
    try:await scope['_receive_audio'](owner)
    except asyncio.CancelledError:pass
    assert not owner._interrupted and await owner.audio_in_queue.get()==b'keep playback'
    assert any(args[0]=='unconfirmed_server_interrupt' for args,kw in events)
    owner.session=Session()
    calls.clear();owner._response_pending=True;owner.interrupt(origin='ui')
    sender=asyncio.create_task(scope['_send_realtime'](owner));await asyncio.sleep(.02)
    sender.cancel()
    try:await sender
    except asyncio.CancelledError:pass
    assert any('activity_start' in m for m in calls) and 'activity_end' in calls[-1]
    owner._interrupted=False;owner._response_pending=False
    owner.interrupt(origin='ui');assert not owner._interrupted,'idle stop suppresses next answer'
    print('PASS: interruption drains stale audio, preserves command prefix, is idempotent, accepts new response after interrupted-only acknowledgement and UI stop never reconnects; slow tools do not block receipt and canceled calls never send replies')


def authentication():
    saved={'cloud_bridge':cloud.BridgeConfig(url='https://cloud.invalid',device_id='device',enabled=True,
        access_token='expired-at-server',refresh_token='refresh',token_expires_at=int(time.time())+3600).__dict__}
    old_load,old_patch=cloud.load_api_keys,cloud._patch_config
    cloud.load_api_keys=lambda:copy.deepcopy(saved)
    cloud._patch_config=lambda **kw:saved.update(copy.deepcopy(kw))
    class Response:
        def __init__(self,status,data):self.status_code=status;self.ok=status==200;self.data=data
        def json(self):return self.data
    class Session:
        def __init__(self):self.calls=[];self.cookies=pytypes.SimpleNamespace(set=lambda *args,**kw:None)
        def get(self,url,**kw):
            self.calls.append(kw['headers']['Authorization'])
            return Response(200,{'authenticated':True}) if self.calls[-1]=='Bearer renewed' else Response(401,{'error':'unauthorized'})
        def request(self,method,url,**kw):return getattr(self,method.lower())(url,**kw)
        def post(self,url,**kw):
            assert kw['data']['grant_type']=='refresh_token'
            return Response(200,{'access_token':'renewed','refresh_token':'rotated','token_type':'bearer','expires_in':3600})
    try:
        bridge=cloud.CloudBridge();bridge._session=Session()
        assert bridge.verify_session() and len(bridge._session.calls)==2
        assert saved['cloud_bridge']['refresh_token']=='rotated'
        bridge.cfg.token_expires_at=time.time()+40;saved['cloud_bridge']['token_expires_at']=bridge.cfg.token_expires_at
        bridge._session.post=lambda *a,**kw:Response(503,{})
        assert bridge._user_headers()['Authorization']=='Bearer renewed','valid access discarded during temporary renewal outage'
        for _ in range(100):bridge._record('offline',Response(200,{}))
        assert len(bridge.last_trace)==32
    finally:cloud.load_api_keys,cloud._patch_config=old_load,old_patch
    print('PASS: server-side 401 renews once and retries, rotated session persists, valid access survives temporary renewal failure, traces stay bounded')


async def cloud_lifetime():
    original=cloud.get_config;cfg=pytypes.SimpleNamespace(enabled=True)
    cloud.get_config=lambda:cfg
    beats=[];registers=[];sync_started=threading.Event();release_sync=threading.Event()
    class Bridge:
        def __init__(self):self.cfg=cfg;self.last_trace=[];self.last_error=''
        @property
        def configured(self):return self.cfg.enabled
        def heartbeat(self,_):
            beats.append(time.monotonic());self.last_trace=[{'status':0}];self.last_error='timeout'
            return False  # timeouts must retain pairing, not register repeatedly
        def register(self,*args):registers.append(args);return True
        def publish_mobile_snapshot(self):sync_started.set();release_sync.wait(1)
        def sync_cloud_safe(self):return 'unchanged'
        def poll_tasks(self):return []
    owner=pytypes.SimpleNamespace(_cloud_bridge=Bridge(),session=object(),
        ui=pytypes.SimpleNamespace(write_log=lambda _:None),_sync_cloud_conversations=lambda:None)
    async def fast_sleep(seconds):await asyncio.sleep(.02 if seconds==20 else .03)
    scope['CloudBridge']=Bridge
    original_asyncio=scope['asyncio'];scope['asyncio']=pytypes.SimpleNamespace(sleep=fast_sleep,to_thread=asyncio.to_thread)
    workers=[]
    try:
        workers=[asyncio.create_task(scope['_run_cloud_bridge'](owner)),asyncio.create_task(scope['_run_cloud_heartbeat'](owner))]
        await asyncio.sleep(.04);assert sync_started.is_set()
        owner.session=None  # voice disconnected while cloud sync is still blocked
        await asyncio.sleep(.10)
        assert len(beats)>=4 and not registers,'slow sync starved heartbeat or timeout churned pairing'
        count=len(beats);cfg.enabled=False;await asyncio.sleep(.05);assert len(beats)==count
    finally:
        release_sync.set()
        for task in workers:task.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
        scope['asyncio']=original_asyncio;cloud.get_config=original
    run=next(n for n in cls.body if getattr(n,'name','')=='run')
    code=ast.unparse(run)
    assert 'tg.create_task(self._run_cloud_bridge())' not in code
    assert 'asyncio.create_task(self._run_cloud_heartbeat())' in code
    print('PASS: real background threads keep heartbeats running during blocked sync and voice disconnect, timeout retains pairing, manual disconnect stops traffic')


def movement():
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QPoint,QPointF,QEvent
    from PyQt6.QtGui import QMovie,QEnterEvent
    from core.task_pet import TaskPet
    app=QApplication.instance() or QApplication([])
    pet=TaskPet(ROOT/'assets/pets/gojo');pet.show();app.processEvents()
    assert pet._movie.state()!=QMovie.MovieState.Running
    pet._scan_perches=lambda:None
    pet._walk_target=pet.pos()+QPoint(40,0);pet._walk_direction=1;pet._sync_pose()
    assert pet._movie.fileName().endswith('running-right.gif') and pet._movie.state()==QMovie.MovieState.Running
    pet._walk_target=pet.pos()-QPoint(40,0);pet._walk_direction=-1;pet._sync_pose()
    assert pet._movie.fileName().endswith('running-left.gif')
    pet._walk_target=pet.pos();pet._motion_step()
    assert pet._movie.fileName().endswith('idle.gif') and pet._movie.state()!=QMovie.MovieState.Running
    pet._start_wandering();pet.enterEvent(QEnterEvent(QPointF(pet.pos()),QPointF(pet.pos()),QPointF(pet.pos())))
    assert pet._movie.state()!=QMovie.MovieState.Running and pet._walk_target is None
    pet.close()
    print('PASS: supplied directional GIFs play only while moving; arrival, hover and hiding stop decoding')


if __name__=='__main__':
    asyncio.run(interruptions());authentication();asyncio.run(cloud_lifetime());movement()
