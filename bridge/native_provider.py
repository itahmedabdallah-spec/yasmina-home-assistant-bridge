"""Yango Smart Home protocol adapter; input is typed device intent, NOT raw speech.
One exposed group; no virtual microphone, synthetic user transcript or local TTS.
A DONE response requires all configured bulb states to match; no write retries.
"""
from __future__ import annotations
import contextlib,hashlib,json,pathlib,re,sqlite3,threading,time
from core import ALLOWED, MEMBERS, GROUP, ControlError, error_code
from bundle_catalog import GROUP_ID
from native_auth import OWNER

DEVICE_ID=GROUP_ID
CAP='devices.capabilities.on_off'

class NativeError(ValueError):pass

def request_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,160}',value):raise NativeError('invalid_request_id')
    return value

def error_result(code='DEVICE_UNREACHABLE'):
    return {'status':'ERROR','error_code':code}

def capability_result(value):
    return {'id':DEVICE_ID,'capabilities':[{'type':CAP,'state':{'instance':'on','action_result':value}}]}

class NativeLedger:
    def __init__(self,path):
        self.path=pathlib.Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY,hash TEXT,phase TEXT,response TEXT,created REAL,write_attempts INTEGER DEFAULT 0)')
            if 'diagnostics' not in {row[1] for row in db.execute('PRAGMA table_info(requests)')}:
                db.execute('ALTER TABLE requests ADD COLUMN diagnostics TEXT')
            db.execute("UPDATE requests SET phase='interrupted' WHERE phase IN ('claimed','dispatching')")
        self.path.chmod(0o600)
    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=.2);db.row_factory=sqlite3.Row
        try:yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()
    def claim(self,rid,payload):
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.db() as db:
            changed=db.execute('INSERT OR IGNORE INTO requests (id,hash,phase,response,created,write_attempts) VALUES (?,?,?,?,?,0)',(rid,fingerprint,'claimed',None,time.time())).rowcount
            row=db.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
        if row['hash']!=fingerprint:raise NativeError('request_id_payload_conflict')
        return bool(changed),dict(row)
    def phase(self,rid,phase,write=False):
        with self.db() as db:db.execute('UPDATE requests SET phase=?,write_attempts=write_attempts+? WHERE id=?',(phase,int(write),rid))
    def finish(self,rid,response,diagnostics=None):
        with self.db() as db:
            db.execute('UPDATE requests SET phase=?,response=?,diagnostics=? WHERE id=?',
                       ('finished',json.dumps(response),json.dumps(diagnostics) if diagnostics is not None else None,rid))
    def summary(self):
        with self.db() as db:
            return {'received_action_requests':db.execute('SELECT count(*) FROM requests').fetchone()[0],
                'physical_write_attempts':db.execute('SELECT coalesce(sum(write_attempts),0) FROM requests').fetchone()[0]}

    def recent(self,limit=10):
        with self.db() as db:
            rows=db.execute('SELECT id,phase,response,created,write_attempts,diagnostics FROM requests ORDER BY created DESC LIMIT ?',
                            (max(1,min(int(limit),30)),)).fetchall()
        result=[]
        for row in rows:
            response=json.loads(row['response']) if row['response'] else None
            outcome=None
            if response:
                try:outcome=response['payload']['devices'][0]['capabilities'][0]['state']['action_result']
                except (KeyError,IndexError,TypeError):outcome={'status':'UNKNOWN'}
            result.append({'request_id':row['id'],'phase':row['phase'],'created_epoch':row['created'],
                'physical_write_attempts':row['write_attempts'],'result':outcome,
                'source':'authenticated_native_device_command','voice_origin_not_distinguishable_from_app':True,
                'diagnostics':json.loads(row['diagnostics']) if row['diagnostics'] else None})
        return result

class NativeProvider:
    def __init__(self,ha,ledger,enabled=lambda:False):
        self.ha=ha;self.ledger=ledger;self.enabled=enabled;self.lock=threading.Lock()
    def discover(self,rid):
        request_id(rid)
        return {'request_id':rid,'payload':{'user_id':OWNER,'devices':[{
            'id':DEVICE_ID,'name':'نور الصالة','description':'أنوار الصالة الستة عبر Home Assistant',
            'room':'الصالة','type':'devices.types.light','status_info':{'reportable':False},
            'capabilities':[{'type':CAP,'retrievable':True,'reportable':False}],
            'device_info':{'manufacturer':'Yasmina private integration','model':'Living room six-light group','sw_version':'1.0'}
        }]}}
    def query(self,rid,body):
        request_id(rid)
        if not isinstance(body,dict) or set(body)-{'devices'} or not isinstance(body.get('devices'),list) or not 1<=len(body['devices'])<=6:
            raise NativeError('invalid_query')
        requested=body['devices'];states=None
        for item in requested:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or len(item['id'])>120:raise NativeError('invalid_device')
        if any(x['id']==DEVICE_ID for x in requested):
            try:states=self.ha.states()
            except Exception:pass
        result=[]
        for item in requested:
            did=item['id']
            if did!=DEVICE_ID:result.append({'id':did,'error_code':'DEVICE_NOT_FOUND'});continue
            if not states or set(states)!=set(ALLOWED) or any(s not in ('on','off') for s in states.values()):
                result.append({'id':did,'error_code':'DEVICE_UNREACHABLE'});continue
            result.append({'id':did,'capabilities':[{'type':CAP,'state':{'instance':'on','value':any(states[e]=='on' for e in MEMBERS)}}]})
        return {'request_id':rid,'payload':{'devices':result}}
    def desired(self,body):
        if not isinstance(body,dict) or set(body)!={'payload'}:raise NativeError('invalid_action')
        payload=body['payload']
        # Live Yango adds a tracking action_id not shown in the minimal example.
        # Allow this single bounded metadata field; it never selects devices.
        if not isinstance(payload,dict) or 'devices' not in payload or set(payload)-{'devices','action_id'}:
            raise NativeError('invalid_action')
        if 'action_id' in payload:
            action_id=payload['action_id']
            if not isinstance(action_id,str) or not 1<=len(action_id)<=256 or not all(32<=ord(c)<127 for c in action_id):
                raise NativeError('invalid_action_id')
        devices=payload['devices']
        if not isinstance(devices,list) or len(devices)!=1:raise NativeError('invalid_device_count')
        device=devices[0]
        if not isinstance(device,dict) or device.get('id')!=DEVICE_ID or set(device)-{'id','capabilities','custom_data'}:
            raise NativeError('device_not_allowed')
        # custom_data is never trusted to select an entity or modify routing.
        caps=device.get('capabilities')
        if not isinstance(caps,list) or len(caps)!=1 or not isinstance(caps[0],dict):raise NativeError('invalid_capabilities')
        cap=caps[0]
        if set(cap)!={'type','state'} or cap['type']!=CAP:raise NativeError('capability_not_allowed')
        state=cap['state']
        if not isinstance(state,dict) or set(state)!={'instance','value'} or state['instance']!='on' or type(state['value']) is not bool:
            raise NativeError('invalid_state')
        return 'on' if state['value'] else 'off'
    def action(self,rid,body):
        request_id(rid);desired=self.desired(body)
        def response(result):return {'request_id':rid,'payload':{'devices':[capability_result(result)]}}
        if not self.enabled():return response(error_result('DEVICE_UNREACHABLE'))
        started=time.monotonic();deadline=started+2.25
        diagnostic={'desired':desired,'before':None,'after':None,'stage_ms':{}}
        action_id=body['payload'].get('action_id')
        key='action_'+hashlib.sha256(action_id.encode()).hexdigest() if action_id is not None else rid
        claimed,prior=self.ledger.claim(key,body)
        if not claimed:
            replay=json.loads(prior['response']) if prior['response'] else response(error_result())
            replay['request_id']=rid
            return replay
        def finish(result,reason,exc=None):
            diagnostic.update(reason=reason,elapsed_ms=round((time.monotonic()-started)*1000,2))
            if exc is not None:diagnostic['error']=error_code(exc)
            out=response(result);self.ledger.finish(key,out,diagnostic);return out
        if not self.lock.acquire(timeout=.05):return finish(error_result(),'another_request_in_progress')
        try:
            stage=time.monotonic()
            try:before=self.ha.states()
            except Exception as exc:return finish(error_result(),'preflight_read_failed',exc)
            diagnostic['stage_ms']['preflight']=round((time.monotonic()-stage)*1000,2)
            diagnostic['before']=before
            if set(before)!=set(ALLOWED) or any(v not in ('on','off') for v in before.values()):
                return finish(error_result(),'member_unavailable_or_missing')
            if all(v==desired for v in before.values()):
                return finish({'status':'DONE'},'already_in_requested_state')
            if deadline-time.monotonic()<1.0:
                return finish(error_result(),'preflight_exhausted_time_budget')
            self.ledger.phase(key,'dispatching',write=True)
            stage=time.monotonic()
            dispatch_uncertain=False
            try:self.ha.set_light(desired)
            except Exception as exc:
                diagnostic['stage_ms']['dispatch']=round((time.monotonic()-stage)*1000,2)
                code=error_code(exc)
                # A POST timeout is not proof that the lamps did not change.
                # Reconcile with GETs only; never send the physical command again.
                recoverable=(isinstance(exc,(TimeoutError,OSError)) or code in
                             ('ha_transport_error','ha_http_500','ha_http_502','ha_http_503','ha_http_504'))
                if not recoverable:
                    return finish(error_result(),'dispatch_rejected_no_retry',exc)
                dispatch_uncertain=True
                diagnostic['dispatch_error']=code
            diagnostic['stage_ms']['dispatch']=round((time.monotonic()-stage)*1000,2)
            diagnostic['dispatch_transport_confirmed']=not dispatch_uncertain
            stage=time.monotonic()
            for attempt in range(12):
                delay=min(.06*(1.5**attempt),.18)
                if deadline-time.monotonic()<delay+.35:break
                time.sleep(delay)
                diagnostic['verification_reads']=attempt+1
                try:after=self.ha.states()
                except Exception as exc:
                    diagnostic['last_verification_error']=error_code(exc)
                    continue
                diagnostic['after']=after
                if set(after)==set(ALLOWED) and all(v==desired for v in after.values()):
                    diagnostic['stage_ms']['verification']=round((time.monotonic()-stage)*1000,2)
                    diagnostic['outcome_verified']=True
                    reason='all_six_states_verified_after_uncertain_dispatch' if dispatch_uncertain else 'all_six_states_verified'
                    return finish({'status':'DONE'},reason)
            diagnostic['stage_ms']['verification']=round((time.monotonic()-stage)*1000,2)
            diagnostic['outcome_verified']=False
            reason='dispatch_unconfirmed_no_retry' if dispatch_uncertain else 'post_dispatch_states_not_verified'
            return finish(error_result(),reason)
        finally:self.lock.release()
