"""Private persistent ADB worker inside HA; UNIX socket only, no TCP listener.
Uses the already authorized TV key in place. Accepts typed catalogue actions,
never raw shell text or file paths. No playback/volume action on startup.
"""
from pathlib import Path
import dataclasses,fcntl,hashlib,json,os,re,signal,socket,socketserver,sys,threading,time

def _load_env_file():
 path=Path(os.environ.get('YASMINA_ENV_FILE','/config/yasmina_bridge.env'))
 if not path.is_file():return
 for raw in path.read_text(encoding='utf8').splitlines():
  line=raw.strip()
  if not line or line.startswith('#') or '=' not in line:continue
  key,value=line.split('=',1);key=key.strip();value=value.strip()
  if key and key.replace('_','').isalnum() and key not in os.environ:os.environ[key]=value

_load_env_file()
BASE=Path(os.environ.get('YASMINA_HA_CONFIG_DIR','/config'));MODULES=Path(os.environ.get('YASMINA_BUNDLE_DIR',str(BASE/'yasmina_bundle')));sys.path.insert(0,str(MODULES))
TV_IP=os.environ.get('YASMINA_TV_IP','192.0.2.10').strip();TV_PORT=int(os.environ.get('YASMINA_TV_ADB_PORT','5555'))
from bundle_tv_fast import build_probe,parse_snapshot,POWER_READ
from bundle_catalog import BundleError,CORE_APPS,TV_PROGRAMS
from bundle_wake_policy import ensure_awake_route,request_deadline,remaining
from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
SOCKET=Path(os.environ.get('YASMINA_TV_SOCKET',str(BASE/'.yasmina_tv_worker.sock')))
UID=int(os.environ.get('YASMINA_SOCKET_UID',str(os.getuid())));GID=int(os.environ.get('YASMINA_SOCKET_GID',str(os.getgid())))
LOCK=threading.Lock();DEVICE=None
LAST_STATE={'at':0.0,'power':None}
COUNTS={'read_requests':0,'write_requests':0,'connections':0,'errors':0,'wake_requests':0,'wake_packets':0,'sequence_requests':0,'sequence_completed':0,'sequence_failed':0}
JOBS={};JOB_LOCK=threading.Lock();JOBS_PATH=BASE/'.yasmina_tv_jobs.json'

def connect():
 global DEVICE
 if DEVICE is not None and DEVICE.available:return DEVICE
 with (BASE/'.storage/androidtv_adbkey').open() as f:private=f.read()
 with (BASE/'.storage/androidtv_adbkey.pub').open() as f:public=f.read()
 d=AdbDeviceTcp(TV_IP,TV_PORT,default_transport_timeout_s=.8)
 d.connect(rsa_keys=[PythonRSASigner(public,private)],auth_timeout_s=1.2,transport_timeout_s=.8)
 DEVICE=d;COUNTS['connections']+=1;return d

def close_device():
 global DEVICE
 if DEVICE:
  try:DEVICE.close()
  except Exception:pass
 DEVICE=None


def persist_jobs():
 safe=[]
 with JOB_LOCK:
  for jid,j in sorted(JOBS.items(),key=lambda x:x[1].get('created',0),reverse=True)[:20]:
   safe.append({k:j.get(k) for k in ('id','program','status','created','started','finished','wake_packets','result','error')})
 tmp=JOBS_PATH.with_suffix('.tmp')
 tmp.write_text(json.dumps(safe,separators=(',',':')))
 os.chmod(tmp,0o600);tmp.replace(JOBS_PATH)

def job_finish(job_id,status,**extra):
 with JOB_LOCK:
  job=JOBS[job_id];job.update(status=status,finished=time.time(),**extra)
 if status=='done':COUNTS['sequence_completed']+=1
 else:COUNTS['sequence_failed']+=1
 persist_jobs()

def mark_state(probe):
 LAST_STATE['at']=time.monotonic();LAST_STATE['power']=probe.state.power

def run_sequence(job_id):
 with JOB_LOCK:job=dict(JOBS[job_id])
 program=job['program'];expires=job['expires_at'];nonce=hashlib.sha256(job_id.encode()).hexdigest()[:32]
 try:
  with JOB_LOCK:JOBS[job_id]['started']=time.time();JOBS[job_id]['status']='running'
  persist_jobs()
  # Connection polling is not an appliance action. Never repeat WoL or app command.
  connected=False
  while time.time()<expires-2.2:
   if not LOCK.acquire(timeout=.08):
    time.sleep(.06);continue
   try:
    try:
     device=connect();connected=True;break
    except Exception:
     close_device()
   finally:
    if not connected:LOCK.release()
   time.sleep(.28)
  if not connected:raise BundleError('tv_sequence_network_timeout')
  try:
   if time.time()>=expires-1.8:raise BundleError('tv_sequence_expired')
   if program=='wake':
    command='echo YASMINA_TV_'+nonce+'_START; '+POWER_READ+'; echo YASMINA_TV_'+nonce+'_END'
   else:
    if program not in CORE_APPS:raise BundleError('tv_sequence_program_invalid')
    # Give the Android launcher a short settling period after network return.
    time.sleep(min(.35,max(0,expires-time.time()-1.5)))
    command=build_probe(nonce,'open_app',program)
   left=max(.2,expires-time.time())
   text=device.shell(command,transport_timeout_s=min(.8,left),read_timeout_s=min(2.4,left),timeout_s=min(2.45,left))
   probe=parse_snapshot(text,nonce,0);mark_state(probe)
   if probe.state.power is not True:raise BundleError('tv_sequence_power_not_verified')
   if program!='wake' and probe.state.app!=CORE_APPS[program]['package']:raise BundleError('tv_sequence_app_not_verified')
   job_finish(job_id,'done',result={'power':True,'app':probe.state.app})
  finally:LOCK.release()
 except Exception as exc:
  close_device()
  code=str(exc) if isinstance(exc,BundleError) else type(exc).__name__
  job_finish(job_id,'error',error=code)

def accept_sequence(obj):
 if set(obj)!={'op','job_id','program','expires_at'} or obj.get('op')!='sequence':
  raise BundleError('invalid_sequence_request')
 jid=obj['job_id'];program=obj['program'];expires=obj['expires_at']
 if not isinstance(jid,str) or not 8<=len(jid)<=128 or not all(c.isalnum() or c in '_.:-' for c in jid):raise BundleError('invalid_sequence_id')
 if program not in TV_PROGRAMS.values():raise BundleError('invalid_sequence_program')
 if type(expires) not in (int,float) or not time.time()+2.0<expires<=time.time()+10.0:raise BundleError('invalid_sequence_deadline')
 with JOB_LOCK:
  if jid in JOBS:return {'ok':True,'accepted':True,'job_id':jid,'existing':True,'status':JOBS[jid]['status']}
  if any(j.get('status') in ('queued','running') for j in JOBS.values()):raise BundleError('tv_sequence_busy')
  wake_packets=0
  # Only explicit voice programs may wake. Fresh confirmed ON skips a redundant WoL.
  fresh_on=(LAST_STATE['power'] is True and time.monotonic()-LAST_STATE['at']<2.0) or (LAST_STATE['power'] is None and DEVICE is not None and DEVICE.available)
  if not fresh_on:
   from bundle_wake_policy import send_registered_wake
   wake_packets=send_registered_wake();COUNTS['wake_requests']+=1;COUNTS['wake_packets']+=wake_packets;close_device()
  JOBS[jid]={'id':jid,'program':program,'expires_at':expires,'status':'queued','created':time.time(),'wake_packets':wake_packets}
 COUNTS['sequence_requests']+=1;persist_jobs()
 threading.Thread(target=run_sequence,args=(jid,),daemon=True,name='tv-sequence-'+jid[:8]).start()
 return {'ok':True,'accepted':True,'job_id':jid,'existing':False,'wake_packets':wake_packets}

class Handler(socketserver.StreamRequestHandler):
 def handle(self):
  self.request.settimeout(3.2)
  reply={}
  try:
   raw=self.rfile.readline(8193)
   if len(raw)>8192 or not raw.endswith(b'\n'):raise BundleError('invalid_worker_request')
   obj=json.loads(raw)
   if not isinstance(obj,dict):raise BundleError('invalid_worker_request')
   if obj=={'op':'health'}:
    with JOB_LOCK:jobs={k:{x:v.get(x) for x in ('program','status','created','finished','result','error')} for k,v in list(JOBS.items())[-5:]}
    reply={'ok':True,'service':'private_tv_worker','persistent_connection':bool(DEVICE and DEVICE.available),'counts':dict(COUNTS),'version':'1.0.0','pid':os.getpid(),'jobs':jobs}
   elif obj.get('op')=='sequence':
    reply=accept_sequence(obj)
   else:
    if set(obj)-{'op','nonce','action','value','expires_at'} or not {'op','nonce','action','value'}<=set(obj) or obj['op']!='transaction':raise BundleError('invalid_worker_request')
    nonce=obj['nonce'];action=obj['action'];value=obj['value']
    # Catalogue validation before connection or command.
    command=build_probe(nonce,action,value)
    deadline=request_deadline(obj)
    if not LOCK.acquire(timeout=.03):raise BundleError('tv_busy')
    started=time.monotonic()
    try:
     remaining(deadline,.2)
     def send_once():
      from bundle_wake_policy import send_registered_wake
      count=send_registered_wake();COUNTS['wake_requests']+=1;COUNTS['wake_packets']+=count;return count
     wake=ensure_awake_route(action,value,deadline,sender=send_once)
     if wake.get('wake_attempted'):
      close_device()
      if action=='on':
       # WoL already requested wake. Do not send a redundant key event or wait;
       # success still requires fresh named screen/power fields from the TV.
       command='echo YASMINA_TV_'+nonce+'_START; '+POWER_READ+'; echo YASMINA_TV_'+nonce+'_END'
     remaining(deadline,.15)
     device=connect()
     remaining(deadline,.08)
     if action is None:COUNTS['read_requests']+=1
     else:COUNTS['write_requests']+=1
     time_left=remaining(deadline,.06)
     text=device.shell(command,transport_timeout_s=min(.8,time_left),read_timeout_s=min(2.4,time_left),timeout_s=min(2.45,time_left))
     if 'YASMINA_MUTE_STATE_UNKNOWN' in text or 'YASMINA_VOLUME_STATE_UNKNOWN' in text:raise BundleError('tv_audio_state_unknown')
     probe=parse_snapshot(text,nonce,time.monotonic()-started);mark_state(probe)
     reply={'ok':True,'probe':dataclasses.asdict(probe),'wake':wake}
    except Exception:
     close_device();raise
    finally:LOCK.release()
  except Exception as exc:
   COUNTS['errors']+=1
   code=str(exc) if isinstance(exc,BundleError) else type(exc).__name__
   reply={'ok':False,'error':code,'action_not_retried':True}
  try:self.wfile.write(json.dumps(reply,separators=(',',':')).encode()+b'\n')
  except OSError:pass

class Server(socketserver.ThreadingUnixStreamServer):
 daemon_threads=True
 request_queue_size=8

def keepalive(stop):
 while not stop.wait(20):
  if not LOCK.acquire(blocking=False):continue
  try:
   if DEVICE and DEVICE.available:
    DEVICE.shell('echo YASMINA_KEEPALIVE',transport_timeout_s=.5,read_timeout_s=.7,timeout_s=.7)
  except Exception:close_device()
  finally:LOCK.release()

def main():
 os.umask(0o077)
 lock=(BASE/'.yasmina_tv_worker.lock').open('a+')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:return
 # Only the process holding the exclusive lock may replace a stale socket.
 if SOCKET.exists():SOCKET.unlink()
 server=Server(str(SOCKET),Handler);os.chown(SOCKET,UID,GID);os.chmod(SOCKET,0o600)
 stop=threading.Event();threading.Thread(target=keepalive,args=(stop,),daemon=True).start()
 def shutdown(*_):stop.set();threading.Thread(target=server.shutdown,daemon=True).start()
 signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
 print(json.dumps({'event':'started','unix_socket':str(SOCKET),'tcp_ports_opened':0,'startup_device_actions':0}),flush=True)
 try:server.serve_forever(poll_interval=.3)
 finally:
  stop.set();server.server_close();close_device();SOCKET.unlink(missing_ok=True)
if __name__=='__main__':main()
