"""Private Yango native-provider service; public paths contain no HA credential.
Production device actions remain disabled until private-skill linking is verified.
No scenario-history polling, initial acknowledgement, or additional TTS channel.
"""
from __future__ import annotations
import base64,datetime as dt,html,http.cookies,json,os,pathlib,signal,socket,threading,time,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from core import HomeAssistant
from native_auth import OAuthError,CLIENT_ID,REDIRECT,SCOPE
from bundle_oauth import ScopedOAuthStore as OAuthStore,FULL_SCOPE,canonical_scope
from native_provider import NativeProvider,NativeLedger,NativeError
from bundle_transport import LightingHA
from bundle_lighting import LightingExecutor
from bundle_native import ExpandedNativeProvider
from bundle_tv_socket import SOCKET
from bundle_tv_ha_worker import HAWorkerTV
from bundle_worker_supervisor import WorkerSupervisor
from bundle_journal import BundleLedger
from bundle_tv_native import TVNative
from bundle_unified import UnifiedProvider
from bundle_scope_guard import DeviceScopeError
from bundle_catalog import GROUP_ID,DEVICE_TARGETS
ROOT=pathlib.Path(os.environ.get('YASMINA_BRIDGE_ROOT',str(pathlib.Path(__file__).resolve().parent))).resolve()
HOST=os.environ.get('YASMINA_PUBLIC_HOST','example.invalid').strip();BASE='https://'+HOST;PORT=int(os.environ.get('YASMINA_LISTEN_PORT','8794'))
STORE=None
LEDGER=None
PROVIDER=None
BUNDLE_LEDGERS={}
SUPERVISOR=None
CONFIG=ROOT/'native_config.json'

def enabled():
    try:
        d=json.loads(CONFIG.read_text())
        gates=('actions_enabled','private_skill_verified','account_link_verified',
               'device_discovery_verified','speaker_account_verified')
        return all(d.get(key) is True for key in gates) and GROUP_ID in d.get('exposed_devices',[])
    except Exception:return False

def bundle_published():
    try:return json.loads(CONFIG.read_text()).get('bundle_published') is True
    except Exception:return False

def bundle_active():
    try:return enabled() and bundle_published() and json.loads(CONFIG.read_text()).get('bundle_active') is True
    except Exception:return False

def dispatch(method,*args,claims=None):
    if getattr(PROVIDER,'scoped',False):return getattr(PROVIDER,method)(*args,claims)
    return getattr(PROVIDER,method)(*args)

def initialize(root=None,adapter=None,policy=None):
    global ROOT,CONFIG,STORE,LEDGER,PROVIDER,BUNDLE_LEDGERS,SUPERVISOR
    if root is not None:ROOT=pathlib.Path(root)
    (ROOT/'runtime').mkdir(mode=0o700,parents=True,exist_ok=True)
    CONFIG=ROOT/'native_config.json'
    STORE=OAuthStore(ROOT/'secrets');STORE.provision()
    LEDGER=NativeLedger(ROOT/'runtime/native_requests.sqlite3')
    PROVIDER=NativeProvider(adapter if adapter is not None else HomeAssistant(ROOT/'secrets/ha_token',request_timeout=.3,write_timeout=.9),
                           LEDGER,policy if policy is not None else enabled)
    BUNDLE_LEDGERS={}
    cfg=json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if cfg.get('bundle_version')=='1.0.0' and adapter is None:
        legacy=PROVIDER
        BUNDLE_LEDGERS={name:BundleLedger(ROOT/'runtime'/('unified_'+name+'.sqlite3')) for name in ('lights','tv')}
        lighting_adapter=LightingHA(ROOT/'secrets/ha_token',writes_enabled=bundle_active)
        engine=LightingExecutor(lighting_adapter,BUNDLE_LEDGERS['lights'],enabled=bundle_active)
        lighting=ExpandedNativeProvider(legacy,engine,enabled=bundle_published)
        tv_transport=HAWorkerTV(ROOT/'secrets/ha_token',read_authorized=bundle_published,write_authorized=bundle_active)
        tv=TVNative(tv_transport,BUNDLE_LEDGERS['tv'],enabled=bundle_active)
        PROVIDER=UnifiedProvider(lighting,tv,published=bundle_published,active=bundle_active)
        SUPERVISOR=WorkerSupervisor(ROOT/'secrets/ha_token',SOCKET)



def unique_pairs(pairs):
    d={}
    for k,v in pairs:
        if k in d:raise ValueError('duplicate_key')
        d[k]=v
    return d

def public_state():
    return {'service':'yasmina-home-assistant-bridge','at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'actions_enabled':enabled(),'oauth':STORE.public_status(),'ledger':LEDGER.summary(),
      'single_group_only':not bundle_published(),'bundle_published':bundle_published(),'bundle_active':bundle_active(),
      'full_scope_device_count':(len(DEVICE_TARGETS)+2) if bundle_published() else 1,'bundle_ledgers':{k:v.summary() for k,v in BUNDLE_LEDGERS.items()},
      'extra_tts_requests':0,'scenario_ack_requests':0,
      'tv_worker':SUPERVISOR.status if SUPERVISOR else None,'raw_utterance_available':False,'live_yasmina_call_verified':False,
      'recent_requests':LEDGER.recent(),'bundle_recent_requests':{k:v.recent() for k,v in BUNDLE_LEDGERS.items()},
      'protocol':'Yango Smart Home v1.0','bind':'127.0.0.1:8794'}

def save_status():
    try:
        p=ROOT/'runtime/native_provider_status.json';tmp=p.with_suffix('.tmp')
        tmp.write_text(json.dumps(public_state(),ensure_ascii=False,indent=2));tmp.replace(p)
    except Exception:pass

def audit_protocol(kind, **fields):
    # Only fixed labels/counts/bools; NEVER headers, bodies, query strings or tokens.
    allowed=('http_status','origin_class','cookie_present','device_count','error_code')
    clean={k:v for k,v in fields.items() if k in allowed and isinstance(v,(str,int,bool))}
    record={'at':dt.datetime.now(dt.timezone.utc).isoformat(),'kind':kind,**clean}
    try:
        with (ROOT/'runtime/native_protocol_events.jsonl').open('a',encoding='utf8') as f:
            f.write(json.dumps(record,ensure_ascii=False)+'\n')
    except Exception:pass

def audit_validation(handler, error, body=None):
    # Never stores Authorization, cookie, any token or arbitrary string value.
    def shape(value, depth=0):
        if depth>7:return type(value).__name__
        if isinstance(value,dict):
            return {k:shape(v,depth+1) for k,v in value.items()
                    if isinstance(k,str) and len(k)<64 and not any(w in k.lower() for w in ('token','secret','password','cookie','auth'))}
        if isinstance(value,list):return [shape(v,depth+1) for v in value[:3]]
        return type(value).__name__
    path=urllib.parse.urlsplit(handler.path).path
    if path not in ('/v1.0/user/devices/action','/v1.0/user/devices/query'):return
    reason=str(error)
    if not reason.replace('_','').isalnum() or len(reason)>80:reason=type(error).__name__
    record={'at':dt.datetime.now(dt.timezone.utc).isoformat(),'path':path,'error_code':reason,
        'authenticated':getattr(handler,'provider_authenticated',False),
        'transfer_encoding_present':bool(handler.headers.get('Transfer-Encoding')),
        'content_type':handler.headers.get_content_type(),
        'request_id_present':bool(handler.headers.get('X-Request-Id')),
        'body_schema':shape(body),'physical_actions':0}
    file=ROOT/'runtime/native_validation_latest.json'
    file.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    audit_protocol('provider_request_rejected',http_status=400,error_code=reason)

class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    slots=threading.BoundedSemaphore(16)
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            try:request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
            finally:self.shutdown_request(request)
            return
        try:super().process_request(request,client_address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.slots.release()

class Handler(BaseHTTPRequestHandler):
    server_version='Yasmina-Home/1';protocol_version='HTTP/1.1'
    def setup(self):
        super().setup();self.connection.settimeout(3)
    def log_message(self,*args):pass
    def send(self,code,payload=None,headers=None,ctype='application/json; charset=utf-8'):
        raw=b'' if payload is None else payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        self.send_response(code)
        self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('Connection','close')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY')
        # HTML form navigation under no-referrer sends Origin:null in Chromium.
        # Preserve same-origin form provenance without leaking to external sites;
        # exact Origin + session-cookie + CSRF + one-use owner code remain required.
        form_page=(self.command=='GET' and code==200 and
                   urllib.parse.urlsplit(self.path).path=='/oauth/authorize' and ctype.startswith('text/html'))
        self.send_header('Referrer-Policy','same-origin' if form_page else 'no-referrer')
        # Chromium also checks form-action against the POST redirect destination.
        # Allow only the exact registered OAuth callback, never a wildcard host.
        oauth_redirect=(self.command=='POST' and code==302 and
                        urllib.parse.urlsplit(self.path).path=='/oauth/authorize')
        form_targets="'self'"+(' '+REDIRECT if form_page or oauth_redirect else '')
        self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action "+form_targets+"; frame-ancestors 'none'; base-uri 'none'")
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(raw)
        self.close_connection=True
    def valid_host(self):return self.headers.get('Host','') in (HOST,HOST+':443','127.0.0.1:8794','localhost:8794')
    def body(self,form=False):
        if self.headers.get('Transfer-Encoding'):raise ValueError('transfer_encoding_not_supported')
        n=int(self.headers.get('Content-Length','0'))
        if not 0<n<=16384:raise ValueError('invalid_size')
        expected='application/x-www-form-urlencoded' if form else 'application/json'
        if self.headers.get_content_type()!=expected:raise ValueError('wrong_content_type')
        raw=self.rfile.read(n)
        if len(raw)!=n:raise ValueError('incomplete_body')
        if form:
            pairs=urllib.parse.parse_qsl(raw.decode(),keep_blank_values=True,max_num_fields=15)
            return unique_pairs(pairs)
        return json.loads(raw,object_pairs_hook=unique_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite')))
    def token(self):
        value=self.headers.get('Authorization','')
        if not value.startswith('Bearer '):raise OAuthError('invalid_token')
        token=value[7:];self.auth_claims=STORE.authenticate_claims(token);return token
    def do_HEAD(self):
        if not self.valid_host():return self.send(400)
        if urllib.parse.urlsplit(self.path).path in ('/v1.0/','/v1.0'):return self.send(200)
        return self.send(404)
    def do_GET(self):
        try:
            if not self.valid_host():return self.send(400,{'error':'invalid_host'})
            parsed=urllib.parse.urlsplit(self.path);path=parsed.path
            if path=='/':return self.send(200,{'service':'Yasmina Home private connector','device_control_requires_account_linking':True})
            if path=='/internal/status':
                if self.headers.get('Host') not in ('127.0.0.1:8794','localhost:8794'):return self.send(404)
                save_status();return self.send(200,public_state())
            if path=='/v1.0/user/devices':
                self.token();result=dispatch('discover',self.headers.get('X-Request-Id'),claims=self.auth_claims)
                audit_protocol('discovery_authenticated',http_status=200,device_count=len(result['payload']['devices']))
                return self.send(200,result)
            if path=='/oauth/authorize':
                if self.headers.get('Host')!=HOST:return self.send(400,{'error':'https_public_host_required'})
                if not STORE.allow_attempt('authorize',40):return self.send(429,{'error':'rate_limited'})
                q=unique_pairs(urllib.parse.parse_qsl(parsed.query,keep_blank_values=True,max_num_fields=12))
                scope=canonical_scope(q.get('scope') or SCOPE)
                sid,csrf=STORE.begin(q.get('client_id'),q.get('redirect_uri'),q.get('response_type'),q.get('state'),scope)
                page='''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>إيما هوم — ربط خاص</title><style>body{font:18px/1.8 Tahoma,Arial;background:#f3f1ec;color:#213442;margin:0}main{max-width:480px;margin:8vh auto;background:white;border-radius:20px;padding:30px}input,button{font:inherit;padding:12px;width:100%;box-sizing:border-box;margin:10px 0;border-radius:10px}input{border:1px solid #bfcbd2}button{background:#365b75;color:white;border:0}small{color:#647785}</style><main><h1>إيما هوم</h1><p>ربط خاص بحسابك للتحكم في نور الصالة فقط.</p><p>لا تتم مشاركة رمز Home Assistant أو تسجيلات صوتك.</p><form method="post" action="/oauth/authorize"><input type="hidden" name="csrf" value="'''+html.escape(csrf,quote=True)+'''"><label for="code">رمز الربط الخاص</label><input id="code" name="link_code" type="password" autocomplete="off" required maxlength="150"><button type="submit">السماح وربط الحساب</button></form><small>هذا الربط لا يمنح الوصول للتلفزيون أو الأقفال أو الميكروفونات.</small></main></html>'''
                if scope==FULL_SCOPE:
                    page=page.replace('للتحكم في نور الصالة فقط.','للتحكم في إضاءة الصالة والتليفزيون وتطبيقاته الأربعة المعتمدة.').replace('هذا الربط لا يمنح الوصول للتلفزيون أو الأقفال أو الميكروفونات.','يشمل تشغيل وإطفاء التليفزيون والصوت والتطبيقات المعتمدة. لا يشمل الأقفال أو الكاميرات أو الميكروفونات. التفعيل الفعلي يظل خاضعًا لجاهزية الحزمة.')
                return self.send(200,page.encode(),{'Set-Cookie':'yasmina_link='+sid+'; HttpOnly; Secure; SameSite=Lax; Path=/oauth; Max-Age=600'},'text/html; charset=utf-8')
            return self.send(404,{'error':'not_found'})
        except OAuthError as e:return self.send(401,{'error':str(e)},{'WWW-Authenticate':'Bearer'})
        except (NativeError,ValueError,TypeError):return self.send(400,{'error':'invalid_request'})
        except Exception:return self.send(503,{'error':'temporarily_unavailable'})
    def do_POST(self):
        body=None
        self.provider_authenticated=False
        try:
            if not self.valid_host():return self.send(400,{'error':'invalid_host'})
            path=urllib.parse.urlsplit(self.path).path
            if path=='/oauth/authorize':
                origin=self.headers.get('Origin')
                if origin!=BASE:
                    audit_protocol('owner_origin_rejected',http_status=403,
                        origin_class='missing' if origin is None else 'null' if origin=='null' else 'other')
                    return self.send(403,{'error':'origin_not_allowed'})
                audit_protocol('owner_origin_verified',origin_class='expected',cookie_present='yasmina_link=' in self.headers.get('Cookie',''))
                if not STORE.allow_attempt('approve',6):return self.send(429,{'error':'rate_limited'})
                form=self.body(True);cookie=http.cookies.SimpleCookie(self.headers.get('Cookie',''))
                sid=cookie['yasmina_link'].value if 'yasmina_link' in cookie else ''
                approved=STORE.approve(sid,form.get('csrf',''),form.get('link_code',''))
                audit_protocol('owner_code_approved',http_status=302)
                location=approved.pop('redirect_uri')+'?'+urllib.parse.urlencode(approved)
                return self.send(302,None,{'Location':location,'Set-Cookie':'yasmina_link=; Secure; HttpOnly; SameSite=Lax; Path=/oauth; Max-Age=0'})
            if path=='/oauth/token':
                if not STORE.allow_attempt('token',40):return self.send(429,{'error':'rate_limited'})
                form=self.body(True);client=form.get('client_id');secret=form.get('client_secret')
                auth=self.headers.get('Authorization','')
                if auth.startswith('Basic '):
                    decoded=base64.b64decode(auth[6:],validate=True).decode();client,secret=decoded.split(':',1)
                    client=urllib.parse.unquote(client);secret=urllib.parse.unquote(secret)
                result=STORE.exchange(form.get('grant_type'),client,secret,form.get('code'),form.get('redirect_uri'),form.get('refresh_token'))
                audit_protocol('oauth_token_exchanged',http_status=200)
                save_status();return self.send(200,result)
            if path not in ('/v1.0/user/devices/query','/v1.0/user/devices/action','/v1.0/user/unlink'):
                return self.send(404,{'error':'not_found'})
            token=self.token()
            self.provider_authenticated=True
            if path=='/v1.0/user/unlink':
                STORE.unlink(token);save_status();return self.send(200,{'request_id':self.headers.get('X-Request-Id','')})
            body=self.body();rid=self.headers.get('X-Request-Id')
            result=dispatch('query' if path.endswith('/query') else 'action',rid,body,claims=self.auth_claims)
            audit_protocol('devices_query_authenticated' if path.endswith('/query') else 'devices_action_authenticated',http_status=200)
            save_status();return self.send(200,result)
        except DeviceScopeError:return self.send(403,{'error':'device_scope_denied'})
        except OAuthError as e:return self.send(401 if str(e)=='invalid_token' else 400,{'error':str(e)})
        except (NativeError,ValueError,TypeError) as exc:
            audit_validation(self,exc,body)
            return self.send(400,{'error':'invalid_request'})
        except Exception:return self.send(503,{'error':'temporarily_unavailable'})

def main():
    os.umask(0o077);initialize();server=Server(('127.0.0.1',PORT),Handler)
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    save_status()
    if SUPERVISOR:SUPERVISOR.start()
    try:server.serve_forever(poll_interval=.3)
    finally:
        if SUPERVISOR:SUPERVISOR.stop()
        server.server_close();save_status()

if __name__=='__main__':main()
