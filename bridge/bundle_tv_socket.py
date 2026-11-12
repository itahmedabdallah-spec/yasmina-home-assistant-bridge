"""Host-side typed client for the private HA UNIX-socket TV worker.
No key transfer, HTTP listener, arbitrary commands or retries of physical actions.
"""
from pathlib import Path
import dataclasses,json,os,socket,stat,time,uuid
from bundle_catalog import BundleError
from bundle_tv_fast import Probe,build_probe
from bundle_tv import TVState
SOCKET=Path(os.environ.get('YASMINA_TV_SOCKET','/config/.yasmina_tv_worker.sock'))
class SocketTV:
 def __init__(self,*,read_authorized=lambda:False,write_authorized=lambda:False,path=SOCKET):
  self.path=Path(path);self.read_authorized=read_authorized;self.write_authorized=write_authorized;self.physical_dispatches=0
 def transaction(self,action=None,value=None,timeout=2.7):
  if not self.read_authorized():raise BundleError('tv_read_authorization_pending')
  if action is not None and not self.write_authorized():raise BundleError('tv_write_authorization_pending')
  nonce=uuid.uuid4().hex;build_probe(nonce,action,value) # validate typed catalogue before socket
  st=self.path.lstat()
  if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077:raise BundleError('tv_socket_permissions_invalid')
  request={'op':'transaction','nonce':nonce,'action':action,'value':value}
  started=time.monotonic()
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
   s.settimeout(timeout)
   try:
    s.connect(str(self.path))
    if action is not None:self.physical_dispatches+=1
    s.sendall(json.dumps(request,separators=(',',':')).encode()+b'\n')
    raw=b''
    while not raw.endswith(b'\n') and len(raw)<32000:
     chunk=s.recv(4096)
     if not chunk:break
     raw+=chunk
    obj=json.loads(raw)
   except (OSError,ValueError):raise BundleError('tv_transport_uncertain_no_retry') from None
  if not isinstance(obj,dict) or obj.get('ok') is not True:raise BundleError(str(obj.get('error','tv_worker_failed')))
  p=obj.get('probe',{})
  if p.get('nonce')!=nonce:raise BundleError('tv_response_not_current')
  state=p.get('state',{})
  if type(state.get('power')) is not bool:raise BundleError('tv_power_unknown')
  state['source']='persistent_ADB_worker_readback'
  st=TVState(**state)
  result=Probe(st,nonce,time.monotonic()-started,p.get('volume_min'),p.get('volume_max'),p.get('focus'),p.get('key_delivery_verified') is True,p.get('requested_volume'))
  return result
