"""Keep the private worker available after HA restarts. Never controls the TV.
Only one fixed HA bootstrap command, rate-limited, and private UNIX health reads.
"""
from pathlib import Path
import http.client,json,socket,threading,time,stat,os
class WorkerSupervisor:
 def __init__(self,key_path,socket_path):
  self.key_path=Path(key_path);self.path=Path(socket_path);self.stop_event=threading.Event();self.thread=None;self.last_start=0;self.status={'ready':False}
 def health(self):
  try:
   from bundle_tv_ha_worker import worker_call
   data=worker_call(self.key_path,{'op':'health'},timeout=1.0)
   self.status={'ready':True,'persistent_connection':data.get('persistent_connection'),'counts':data.get('counts',{}),'checked_at_epoch':time.time(),'route':'authenticated_HA_API'}
   return True
  except Exception as exc:
   self.status={'ready':False,'checked_at_epoch':time.time(),'error':str(exc) if str(exc).replace('_','').isalnum() else type(exc).__name__}
   return False
 def bootstrap(self):
  if time.monotonic()-self.last_start<60:return
  self.last_start=time.monotonic()
  p=self.key_path
  if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o077:return
  token=p.read_text().strip();c=http.client.HTTPConnection('127.0.0.1',8123,timeout=3)
  try:
   c.request('POST','/api/services/shell_command/tv_cmd?return_response',body=b'{"cmd":"__yasmina_start_worker__"}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
   response=c.getresponse();response.read(16384)
  except Exception:pass
  finally:c.close()
 def loop(self):
  while not self.stop_event.is_set():
   if not self.health():self.bootstrap()
   self.stop_event.wait(15)
 def start(self):
  if self.thread and self.thread.is_alive():return
  self.thread=threading.Thread(target=self.loop,name='tv-worker-health',daemon=True);self.thread.start()
 def stop(self):self.stop_event.set()
