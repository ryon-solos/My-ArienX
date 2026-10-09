"""Offline companion checks. python3 -m diagnostics.living_companion_diag.
Synthetic audio exercises the real callback and outgoing Live protocol;
it cannot certify a physical room, microphone or speaker volume.
"""
import ast
import asyncio
import os
from pathlib import Path
import threading
import time
import types as pytypes
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from core.interruption import Interruption
from actions.proactive import ProactiveEngine
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT/'main.py').read_text())
cls = next(n for n in tree.body if getattr(n, 'name', '') == 'JarvisLive')
scope = {'runtime_event':lambda *args,**kw:None, 'asyncio':asyncio, 'time':time, 'np':np, 'types':types,
         'SEND_SAMPLE_RATE':16000}
for name in ('_pcm_level',):
    node=next(n for n in tree.body if getattr(n,'name','')==name)
    scope.update(_LEVEL_FLOOR=60., _LEVEL_FULL=2600.)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'main.py','exec'),scope)
for name in ('_proactive_allowed', '_run_proactive_mode'):
    node=next(n for n in cls.body if getattr(n,'name','')==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'main.py','exec'),scope)
def proactive():
    engine=ProactiveEngine();now=time.monotonic();engine._started_at=now-21
    owner=pytypes.SimpleNamespace(session=object(),_awake=True,ui=pytypes.SimpleNamespace(muted=False),
        _phone_active=False,_ptt_enabled=False,_interrupted=False,_active_tools=0,_proactive_turn=False,
        _task=pytypes.SimpleNamespace(status='completed'),_speaking_lock=threading.Lock(),_is_speaking=False,
        _tail_active=lambda:False,audio_in_queue=None,_speech=Interruption(),_voice_turn_started_at=now-30,
        _last_live_response_at=0,_proactive=engine,_last_user_speech=now-21)
    assert scope['_proactive_allowed'](owner),'stale unanswered noise blocks first opener'
    owner._speech.active=True;assert not scope['_proactive_allowed'](owner)
    owner._speech.active=False;engine.mark_triggered();assert not engine.should_trigger(now-500)
    engine._last_triggered=now-301;assert engine.should_trigger(now-121)
    assert 'Do not return an empty reply' in engine.build_prompt({})
    print('PASS: opener without user greeting, stale-noise recovery, active-voice gate and five-minute cooldown')


async def proactive_dispatch():
    engine=ProactiveEngine();now=time.monotonic();engine._started_at=now-21
    requests=[];logs=[]
    class Session:
        async def send_client_content(self,**kw):
            requests.append(kw)
            raise asyncio.CancelledError()  # Stop the otherwise infinite production loop.
    owner=pytypes.SimpleNamespace(session=Session(),_awake=True,
        ui=pytypes.SimpleNamespace(muted=False,write_log=logs.append),
        _phone_active=False,_ptt_enabled=False,_interrupted=False,_active_tools=0,
        _proactive_turn=False,_proactive_pending_at=0.,_session_log=[],
        _task=pytypes.SimpleNamespace(status='completed'),_speaking_lock=threading.Lock(),
        _is_speaking=False,_tail_active=lambda:False,audio_in_queue=None,
        _speech=Interruption(),_voice_turn_started_at=0,_last_live_response_at=0,
        _proactive=engine,_last_user_speech=now-21)
    owner._proactive_allowed=lambda:scope['_proactive_allowed'](owner)
    async def immediate(_):pass
    async def fake_read(function):return function()
    original=scope['asyncio']
    scope.update(asyncio=pytypes.SimpleNamespace(sleep=immediate,to_thread=fake_read),
        get_proactive_chat_enabled=lambda:True,load_memory=lambda:{},list_monitors=lambda:[])
    try:
        try:await scope['_run_proactive_mode'](owner)
        except asyncio.CancelledError:pass
    finally:scope['asyncio']=original
    assert len(requests)==1 and requests[0]['turn_complete'] and owner._proactive_turn
    assert 'do not wait for the user' in requests[0]['turns']['parts'][0]['text']
    assert logs and engine._last_triggered>0
    print('PASS: actual proactive loop sends a completed spoken-opener request without any user turn')


def pet():
    from PyQt6.QtWidgets import QApplication,QWidget
    from PyQt6.QtGui import QMovie
    from PyQt6.QtCore import QRect
    from core.task_pet import TaskPet
    app=QApplication.instance() or QApplication([])
    window=QWidget();window.show()
    companion=TaskPet(ROOT/'assets/pets/gojo',parent=window);companion.show();app.processEvents()
    window.showMinimized();app.processEvents();assert companion.isVisible()
    window.close()
    assert companion._movie.state()!=QMovie.MovieState.Running and companion._motion.isActive()
    companion._scan_perches=lambda:None
    companion._start_wandering();assert companion._walk_target is not None
    old=companion.pos();companion._motion_step();assert companion.pos()!=old
    companion._hovered=True;old=companion.pos();companion._motion_step();assert companion.pos()==old
    companion._accept_perches([(100,400,600,300)]);assert companion._perches==[QRect(100,400,600,300)]
    companion.hide();assert not companion._motion.isActive() and not companion._wander.isActive()
    companion.close()
    print('PASS: static GIF pose, subtle motion, roaming, hover stop, perches and hidden timer shutdown')


if __name__=='__main__':
    proactive();asyncio.run(proactive_dispatch());pet()
