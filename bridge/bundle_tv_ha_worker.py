"""Typed worker access through authorized HA API; service confinement unchanged.
Uses existing shell_command dispatcher only with one fixed validated helper mode.
No new port, no raw user command, no credentials in output, no write retries.
"""
from pathlib import Path
import http.client,json,shlex,time,uuid
from bundle_catalog import BundleError
from bundle_tv_fast import Probe,build_probe
from bundle_tv import TVState

def worker_call(key_path,request,timeout=2.7):
 if request!={'op':'health'}:
  if not isinstance(request,dict):raise BundleError('invalid_worker_request')
  if request.get('op')=='transaction':
   if set(request)-{'op','nonce','action','value','expires_at'} or not {'op','nonce','action','value'}<=set(request):raise BundleError('invalid_worker_request')
   build_probe(request['nonce'],request['action'],request['value'])
  elif request.get('op')=='sequence':
   if set(request)!={'op','job_id','program','expires_at'}:raise BundleError('invalid_worker_request')
  else:raise BundleError('invalid_worker_request')
 p=Path(key_path)
 if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o077:raise BundleError('credential_file_invalid')
 token=p.read_text().strip()
 if not token.startswith('eyJ') or token.count('.')!=2:raise BundleError('credential_format_invalid')
 command='__yasmina_tv_native__ '+shlex.quote(json.dumps(request,separators=(',',':')))
 connection=http.client.HTTPConnection('127.0.0.1',8123,timeout=timeout)
 try:
  connection.request('POST','/api/services/shell_command/tv_cmd?return_response',body=json.dumps({'cmd':command}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Connection':'close'})
  response=connection.getresponse()
  if response.status!=200:raise BundleError('ha_http_'+str(response.status))
  data=json.loads(response.read(64000));result=data.get('service_response',{})
  if result.get('returncode')!=0:raise BundleError('tv_worker_bridge_failed')
  parsed=json.loads(result.get('stdout',''))
  if not isinstance(parsed,dict) or parsed.get('ok') is not True:raise BundleError(str(parsed.get('error','tv_worker_failed')))
  return parsed
 except (OSError,http.client.HTTPException,json.JSONDecodeError):raise BundleError('tv_transport_uncertain_no_retry') from None
 finally:connection.close()

class HAWorkerTV:
 def __init__(self,key_path,*,read_authorized=lambda:False,write_authorized=lambda:False):
  self.key_path=Path(key_path);self.read_authorized=read_authorized;self.write_authorized=write_authorized;self.physical_dispatches=0
 def transaction(self,action=None,value=None,timeout=2.7):
  if not self.read_authorized():raise BundleError('tv_read_authorization_pending')
  if action is not None and not self.write_authorized():raise BundleError('tv_write_authorization_pending')
  nonce=uuid.uuid4().hex;build_probe(nonce,action,value)
  request={'op':'transaction','nonce':nonce,'action':action,'value':value,'expires_at':time.time()+min(timeout-.05,3.8)}
  start=time.monotonic()
  if action is not None:self.physical_dispatches+=1
  obj=worker_call(self.key_path,request,timeout);p=obj.get('probe',{})
  if p.get('nonce')!=nonce:raise BundleError('tv_response_not_current')
  state=p.get('state',{})
  if type(state.get('power')) is not bool:raise BundleError('tv_power_unknown')
  state['source']='authenticated_HA_persistent_worker'
  return Probe(TVState(**state),nonce,time.monotonic()-start,p.get('volume_min'),p.get('volume_max'),p.get('focus'),p.get('key_delivery_verified') is True,p.get('requested_volume'),p.get('playback_evidence'))

 def sequence(self,identity,program,expires_at):
  if not self.read_authorized() or not self.write_authorized():raise BundleError('tv_write_authorization_pending')
  request={'op':'sequence','job_id':identity,'program':program,'expires_at':expires_at}
  self.physical_dispatches+=1
  return worker_call(self.key_path,request,timeout=1.2)
