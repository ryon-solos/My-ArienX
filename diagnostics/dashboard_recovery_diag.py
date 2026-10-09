"""Offline startup regression: python3 -m diagnostics.dashboard_recovery_diag.
No ports are opened, accounts accessed, or microphone streams created.
"""
import ast
import asyncio
from pathlib import Path
import re
import types
import uvicorn

ROOT=Path(__file__).resolve().parents[1]
server_tree=ast.parse((ROOT/'dashboard/server.py').read_text())
server_cls=next(n for n in server_tree.body if getattr(n,'name','')=='DashboardServer')
main_tree=ast.parse((ROOT/'main.py').read_text())
main_cls=next(n for n in main_tree.body if getattr(n,'name','')=='JarvisLive')
scope={'asyncio':asyncio,'uvicorn':uvicorn,'runtime_event':lambda *a,**kw:None,'re':re,
       'get_response_detail':lambda:'short'}
for cls,names in ((server_cls,('_serve_uvicorn','_serve_alias')),
                  (main_cls,('_run_dashboard','request_reconnect','_on_text_command'))):
    for name in names:
        node=next(n for n in cls.body if getattr(n,'name','')==name)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'startup-regression','exec'),scope)


async def main():
    async def app(scope,receive,send):pass
    loop=asyncio.get_running_loop();original_create=loop.create_server
    async def occupied(*args,**kwargs):
        raise OSError(98,'Address already in use')
    loop.create_server=occupied
    logs=[];requests=[]
    dashboard=types.SimpleNamespace(last_error='',app=app)
    dashboard._serve_uvicorn=lambda cfg:scope['_serve_uvicorn'](dashboard,cfg)
    async def serve():
        # Real Uvicorn startup takes its actual bind-error/sys.exit(3) branch.
        await dashboard._serve_uvicorn(uvicorn.Config(app,host='127.0.0.1',port=8000,
                                                     lifespan='off',log_level='critical'))
    dashboard.serve=serve
    async def send(**kw):requests.append(kw)
    agent=types.SimpleNamespace(**{name:(lambda *a:'') for name in
        ('research_directive','execution_directive','fusion_directive',
         'orchestration_directive','vision_directive')})
    owner=types.SimpleNamespace(_dashboard=dashboard,_loop=loop,session=types.SimpleNamespace(send_client_content=send),
        ui=types.SimpleNamespace(write_log=logs.append,pet_event=lambda *a:None),
        _wake_enabled=False,_agent_core=agent,_chat_store=types.SimpleNamespace(context_for=lambda *a:{}),
        _active_chat='test',_note_user_request=lambda text:None)
    async def voice_task():
        await asyncio.sleep(.02)
        return 'voice task still alive'
    voice=asyncio.create_task(voice_task())
    try:
        await scope['_run_dashboard'](owner)
        assert 'port 8000' in dashboard.last_error
        assert not loop.is_closed() and await voice=='voice task still alive'
        scope['_on_text_command'](owner,'Please answer this message')
        await asyncio.sleep(.02)
        assert requests[0]['turn_complete'] and requests[0]['turns']['parts'][0]['text'].startswith('Please answer')
        assert logs and 'Desktop voice and text remain enabled' in logs[-1]
    finally:
        loop.create_server=original_create
    print('PASS: real Uvicorn occupied-port failure stays inside dashboard; voice task and actual text submission survive')

    class FailedServer:
        def __init__(self,cfg):pass
        async def serve(self):raise SystemExit(3)
    scope['uvicorn']=types.SimpleNamespace(Server=FailedServer,Config=lambda *a,**kw:types.SimpleNamespace(**kw))
    scope.update(BASE_DIR=ROOT,PORT=8000,_ensure_network_access=lambda port:None)
    dashboard._ip='127.0.0.1'
    await scope['_serve_alias'](dashboard)
    assert 'port 8001' in dashboard.last_error
    print('PASS: secondary dashboard port also contains startup SystemExit')

    async def cancelled():raise asyncio.CancelledError()
    dashboard.serve=cancelled
    try:await scope['_run_dashboard'](owner)
    except asyncio.CancelledError:pass
    else:raise AssertionError('shutdown cancellation swallowed')
    print('PASS: shutdown cancellation propagates normally')

    closed=asyncio.new_event_loop();closed.close();owner._loop=closed
    owner._reconnect_event=asyncio.Event()
    scope['request_reconnect'](owner,keep_context=False,reason='new chat')
    before=len(requests);scope['_on_text_command'](owner,'closed-loop message')
    assert len(requests)==before and 'not connected yet' in logs[-1]
    print('PASS: closed-loop chat changes do not crash; disconnected text shows connection status')


if __name__=='__main__':asyncio.run(main())
