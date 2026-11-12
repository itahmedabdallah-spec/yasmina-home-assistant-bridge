"""Fixed Home Assistant dispatcher for the private TV worker.
Only typed bridge operations are accepted. Arbitrary ADB/shell text is rejected.
"""
from pathlib import Path
import json,os,signal,socket,subprocess,sys,time

def _load_env_file():
    path=Path(os.environ.get('YASMINA_ENV_FILE','/config/yasmina_bridge.env'))
    if not path.is_file():
        return
    for raw in path.read_text(encoding='utf8').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key,value=line.split('=',1)
        key=key.strip();value=value.strip()
        if key and key.replace('_','').isalnum() and key not in os.environ:
            os.environ[key]=value

_load_env_file()
BUNDLE_DIR=Path(os.environ.get('YASMINA_BUNDLE_DIR','/config/yasmina_bundle'))
SOCKET=Path(os.environ.get('YASMINA_TV_SOCKET','/config/.yasmina_tv_worker.sock'))
WORKER=Path(os.environ.get('YASMINA_TV_WORKER_PATH','/config/yasmina_tv_worker.py'))
LOG=Path(os.environ.get('YASMINA_TV_WORKER_LOG','/config/.yasmina_tv_worker.log'))

def _validate_request(obj):
    if obj=={'op':'health'}:
        return
    sys.path.insert(0,str(BUNDLE_DIR))
    if not isinstance(obj,dict):
        raise ValueError('invalid_native_tv_request')
    if obj.get('op')=='transaction':
        if set(obj)-{'op','nonce','action','value','expires_at'} or not {'op','nonce','action','value'}<=set(obj):
            raise ValueError('invalid_native_tv_request')
        from bundle_tv_fast import build_probe
        build_probe(obj['nonce'],obj['action'],obj['value'])
        return
    if obj.get('op')=='sequence':
        if set(obj)!={'op','job_id','program','expires_at'}:
            raise ValueError('invalid_native_tv_sequence')
        from bundle_catalog import TV_PROGRAMS
        if obj['program'] not in TV_PROGRAMS.values():
            raise ValueError('invalid_native_tv_sequence')
        return
    raise ValueError('invalid_native_tv_request')

def _worker_request(obj):
    _validate_request(obj)
    raw=json.dumps(obj,separators=(',',':')).encode()+b'\n'
    if len(raw)>8192:
        raise ValueError('native_tv_request_too_large')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
        channel.settimeout(3.0)
        channel.connect(str(SOCKET))
        channel.sendall(raw)
        response=b''
        while not response.endswith(b'\n') and len(response)<32000:
            piece=channel.recv(4096)
            if not piece: break
            response+=piece
    parsed=json.loads(response)
    if not isinstance(parsed,dict):
        raise ValueError('invalid_worker_response')
    return parsed

def _stop_worker():
    target=str(WORKER).encode()
    for process_dir in Path('/proc').iterdir():
        if not process_dir.name.isdigit():
            continue
        try:
            args=(process_dir/'cmdline').read_bytes().split(b'\0')
            if target in args:
                os.kill(int(process_dir.name),signal.SIGTERM)
        except (OSError,ValueError):
            pass
    for _ in range(25):
        if not SOCKET.exists(): break
        time.sleep(.05)

def _start_worker():
    if SOCKET.exists():
        try:
            result=_worker_request({'op':'health'})
            if result.get('ok'):
                return 'YASMINA_WORKER_ALREADY_READY'
        except Exception:
            pass
    with LOG.open('ab',buffering=0) as log:
        subprocess.Popen(
            [sys.executable,'-u',str(WORKER)],
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            start_new_session=True,close_fds=True)
    for _ in range(20):
        if SOCKET.exists():
            return 'YASMINA_WORKER_SOCKET_READY'
        time.sleep(.05)
    return 'YASMINA_WORKER_START_PENDING'

def main():
    if len(sys.argv)>=2 and sys.argv[1]=='__yasmina_tv_native__':
        if len(sys.argv)!=3:
            print(json.dumps({'ok':False,'error':'invalid_native_tv_request'}))
            return 2
        try:
            result=_worker_request(json.loads(sys.argv[2]))
            print(json.dumps(result,separators=(',',':')))
            return 0
        except Exception as exc:
            code=str(exc)
            if not code.replace('_','').isalnum():
                code=type(exc).__name__
            print(json.dumps({'ok':False,'error':code}))
            return 2

    command=' '.join(sys.argv[1:])
    if command=='__yasmina_start_worker__':
        print(_start_worker());return 0
    if command=='__yasmina_restart_worker__':
        _stop_worker();print(_start_worker());return 0

    print('UNSUPPORTED_FIXED_COMMAND')
    return 2

if __name__=='__main__':
    raise SystemExit(main())
