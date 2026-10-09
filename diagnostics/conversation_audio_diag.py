"""Offline checks: python3 -m diagnostics.conversation_audio_diag. No real audio or provider calls."""
import os,sys,ast,asyncio,threading,types,time,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];STAGE=ROOT
sys.path.insert(0,str(ROOT))
from actions import proactive as pro
tree=ast.parse((STAGE/'main.py').read_text());cls=next(n for n in tree.body if getattr(n,'name','')=='JarvisLive')
scope={'time':time,'asyncio':asyncio,'types':types.SimpleNamespace(FunctionResponse=lambda **kw:types.SimpleNamespace(**kw))}
for name in ('_proactive_allowed','_execute_tool'):
 node=next(n for n in cls.body if getattr(n,'name','')==name);exec(compile(ast.Module(body=[node],type_ignores=[]),'main.py','exec'),scope)
engine=pro.ProactiveEngine();assert engine.min_silence_secs==120 and engine.check_cooldown==300
engine._started_at=time.monotonic()-21
now=time.monotonic();assert not engine.should_trigger(now);assert engine.should_trigger(now-130)
engine.mark_triggered();assert not engine.should_trigger(now-1000)
engine._last_triggered=now-700;assert engine.should_trigger(now-130)
owner=types.SimpleNamespace(session=object(),_awake=True,ui=types.SimpleNamespace(muted=False),_phone_active=False,_ptt_enabled=False,_interrupted=False,_active_tools=0,_proactive_turn=False,_task=types.SimpleNamespace(status='completed'),_speaking_lock=threading.Lock(),_is_speaking=False,_tail_active=lambda:False,audio_in_queue=None,_last_voice_forwarded_at=now-20,_voice_turn_started_at=0,_last_live_response_at=now,_proactive=engine,_last_user_speech=now-130,_speech=types.SimpleNamespace(active=False))
assert scope['_proactive_allowed'](owner)
for field,value in (('_awake',False),('_phone_active',True),('_ptt_enabled',True),('_interrupted',True),('_active_tools',1),('_proactive_turn',True),('_is_speaking',True)):
 old=getattr(owner,field);setattr(owner,field,value);assert not scope['_proactive_allowed'](owner),field;setattr(owner,field,old)
owner._speech.active=True;assert not scope['_proactive_allowed'](owner);owner._speech.active=False
owner.ui.muted=True;assert not scope['_proactive_allowed'](owner);owner.ui.muted=False
owner._task.status='awaiting_confirmation';assert not scope['_proactive_allowed'](owner)
async def tools():
 owner._proactive_turn=True
 result=await scope['_execute_tool'](owner,types.SimpleNamespace(name='file_controller',id='test'),proactive=True)
 assert '[PROACTIVE_NO_TOOLS]' in result.response['result']
asyncio.run(tools())
prompt=engine.build_prompt({},recent_turns=['User: नमस्ते'])
assert 'नमस्ते' in prompt and 'Do NOT call any tools' in prompt and 'Gojo-inspired' in prompt
source=(STAGE/'main.py').read_text();assert 'tg.create_task(self._run_proactive_mode())' in source
assert 'automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)' in source and 'self._speech.process(indata' in source
print('PASS: proactive idle/cooldown, mute/sleep/PTT/work gates, memory/language context, tool refusal and task wiring')
