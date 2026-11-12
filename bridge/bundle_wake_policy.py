"""Bounded Wake-on-LAN policy for the configured Android TV."""
import math,os,socket,time
from bundle_catalog import BundleError
TV_IP=os.environ.get('YASMINA_TV_IP','192.0.2.10').strip()
TV_PORT=int(os.environ.get('YASMINA_TV_ADB_PORT','5555'))
TV_BROADCAST=os.environ.get('YASMINA_TV_BROADCAST','255.255.255.255').strip()
_mac=os.environ.get('YASMINA_TV_MAC','00:00:00:00:00:00').replace(':','').replace('-','').strip()
try: TV_MAC=bytes.fromhex(_mac)
except ValueError as exc: raise RuntimeError('YASMINA_TV_MAC_invalid') from exc
if len(TV_MAC)!=6: raise RuntimeError('YASMINA_TV_MAC_invalid')

def wants_wake(action,value):
 if action in ('on','open_app'): return True
 if action!='batch': return False
 return isinstance(value,list) and any(isinstance(v,dict) and v.get('action') in ('on','open_app') for v in value)

def request_deadline(obj,now=None):
 now=time.time() if now is None else now
 expires=obj.get('expires_at',now+2.7)
 if isinstance(expires,bool) or not isinstance(expires,(int,float)) or not math.isfinite(expires): raise BundleError('invalid_tv_deadline')
 if expires<=now or expires-now>4.0: raise BundleError('tv_request_expired_or_future')
 return expires

def remaining(deadline,reserve=0):
 left=deadline-time.time()-reserve
 if left<=0: raise BundleError('tv_request_expired_no_dispatch')
 return left

def reachable(timeout=.07):
 try:
  with socket.create_connection((TV_IP,TV_PORT),timeout=timeout): return True
 except OSError: return False

def send_registered_wake():
 packet=b'\xff'*6+TV_MAC*16
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
  s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
  s.sendto(packet,(TV_IP,9))
  if TV_BROADCAST and TV_BROADCAST!=TV_IP: s.sendto(packet,(TV_BROADCAST,9))
 return 2 if TV_BROADCAST and TV_BROADCAST!=TV_IP else 1

def ensure_awake_route(action,value,deadline,*,probe=reachable,sender=send_registered_wake,clock=time.time,sleep=time.sleep):
 if not wants_wake(action,value): return {'wake_attempted':False,'reason':'not_explicit_on_or_app'}
 if deadline-clock()<.8: raise BundleError('insufficient_wake_budget')
 if probe(.07): return {'wake_attempted':False,'reason':'tv_connection_available'}
 sent=sender(); started=clock()
 while deadline-clock()>.30:
  if probe(.07): return {'wake_attempted':True,'packets_sent':sent,'network_ready':True,'wait_seconds':round(clock()-started,3)}
  sleep(.04)
 raise BundleError('tv_wake_not_ready_before_deadline')
