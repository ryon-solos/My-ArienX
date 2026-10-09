"""Offline checks: python3 -m diagnostics.persistence_diag. No credentials or user writes."""
import sys,os,importlib.util,tempfile,time,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM']='offscreen'
from core import cloud_bridge as b
saved={'cloud_bridge':b.BridgeConfig(url='https://cloud.example',device_id='device',enabled=True,access_token='old',refresh_token='old-refresh',token_expires_at=1).__dict__.copy()}
b.load_api_keys=lambda:copy.deepcopy(saved)
b._patch_config=lambda **fields:saved.update(copy.deepcopy(fields))
class Response:
 ok=True;status_code=200
 def json(self):return {'access_token':'renewed','refresh_token':'rotated','token_type':'bearer','expires_in':3600}
class Session:
 def __init__(self):self.calls=[];self.cookies=type('Cookies',(),{'set':lambda *a,**kw:None})()
 def post(self,url,**kw):
  self.calls.append(kw)
  assert url.endswith('/.netlify/identity/token')
  assert kw['data']=={'grant_type':'refresh_token','refresh_token':'old-refresh'}
  assert kw['headers']['Content-Type']=='application/x-www-form-urlencoded'
  return Response()
x,y=b.CloudBridge(),b.CloudBridge();x._session=Session();y._session=Session()
assert x._user_headers()['Authorization']=='Bearer renewed'
assert y._user_headers()['Authorization']=='Bearer renewed' and not y._session.calls
assert saved['cloud_bridge']['refresh_token']=='rotated'
y.cfg.access_token='stale';y.cfg.refresh_token='stale';y.cfg.token_expires_at=1;b._save(y.cfg)
assert saved['cloud_bridge']['refresh_token']=='rotated'
# Transient failures preserve pairing and retry with the same credentials.
saved['cloud_bridge']['token_expires_at']=1
z=b.CloudBridge();z._session=Session()
class Unavailable(Response):
 ok=False;status_code=503
z._session.post=lambda *a,**kw:Unavailable()
assert z._user_headers() is None and z.cfg.enabled and z.cfg.refresh_token=='rotated'
assert 'temporarily' in z.last_error
print('PASS: form refresh, rotated-token persistence/reuse, stale-save protection and outage retention')
import core.personality
from core.personality import personality_manager as pm
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'memory';p.mkdir();(p/'personality_profiles.json').write_text('{"gojo":{"name":"bad profile","traits":["bad"]}}')
 manager=pm.PersonalityManager(d)
 for request in ('talk like Batman','clear personality','talk normally','switch to Naruto'):
  assert manager.handle(request)['event']=='locked'
  assert manager.active_name=='Gojo' and 'Gojo-inspired' in manager.render_block()
 assert 'measured pacing' in manager.render_block()
 from core import identity as ident
 assert ident.CREATOR_NAME=='Ryon' and 'cannot be changed' in ident.IDENTITY_POLICY
print('PASS: code-owned Gojo style survives cached overrides and requests to change/clear; Ryon identity fixed')
from core import task_pet as petmod
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QMovie
app=QApplication([]);pet=petmod.TaskPet(Path(__file__).resolve().parents[1]/'assets/pets/gojo');pet.show();app.processEvents()
assert pet._movie.state()!=QMovie.MovieState.Running and not pet._movie.currentPixmap().isNull()
assert pet._motion.isActive() and pet._wander.isActive()
pet.task_event('started','task');assert pet.animation=='running' and pet._movie.state()!=QMovie.MovieState.Running
pet.task_event('completed','task');assert pet._movie.state()!=QMovie.MovieState.Running
pet.hide();assert not pet._motion.isActive() and not pet._wander.isActive()
pet.close()
print('PASS: static pose, lightweight motion and hidden timer shutdown')
