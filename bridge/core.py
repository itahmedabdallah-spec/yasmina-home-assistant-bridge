"""Yasmina natural controller: explicit intents, durable once-only execution.
No microphone access, cloud credentials, device discovery, or speech side effects.
A receipt acknowledgement is never generated. Physical writes require both
policy permission and a separately provisioned Home Assistant credential.
"""
from __future__ import annotations
import concurrent.futures, contextlib, dataclasses, datetime as dt, json, math, pathlib, os
import re, sqlite3, threading, time, unicodedata, urllib.request, urllib.error, http.client
from typing import Any

from bundle_catalog import GROUP,MEMBERS
ALLOWED=(GROUP,)+MEMBERS
MUTE_ENTITY=os.environ.get('YASMINA_VOICE_MUTE_ENTITY','switch.voice_assistant_mute').strip()
READABLE=ALLOWED+(MUTE_ENTITY,)


class ControlError(RuntimeError):
    pass

@dataclasses.dataclass(frozen=True)
class Policy:
    actions_enabled: bool = False
    speech_enabled: bool = False
    max_event_age_seconds: float = 45.0
    future_tolerance_seconds: float = 3.0
    verification_attempts: int = 3
    verification_delay_seconds: float = 0.35


def normalize(text: str) -> str:
    if not isinstance(text, str) or not 1 <= len(text) <= 400:
        raise ControlError('invalid_text')
    text = unicodedata.normalize('NFKC', text)
    text = re.sub('[\u064b-\u065f\u0670\u0640]', '', text)
    text = text.translate(str.maketrans({'أ':'ا','إ':'ا','آ':'ا','ٱ':'ا','ى':'ي','ة':'ه'}))
    text = re.sub(r'[،,.!؟?؛:]', ' ', text)
    text = ' '.join(text.split())
    text = re.sub(r'^(?:ياسمينا|ياسمينه|ياسمين)\s+', '', text)
    text = re.sub(r'^(?:(?:ممكن|لو سمحتي|لو سمحت|من فضلك|من فضلكي)\s+)+', '', text)
    text = re.sub(r'\s+(?:لو سمحتي|لو سمحت|من فضلك|من فضلكي)$', '', text)
    return text.strip()


def interpret(text: str) -> dict[str, Any]:
    normalized = normalize(text)
    off = r'(?:اطفي|اطفئ|اطفئي|طفي|طفيلي|اطفيلي|اقفل|اقفلي|اقفللي|اقفليلي)'
    on = r'(?:شغل|شغلي|شغللي|شغليلي|ولع|ولعي|ولعلي|ولعيلي|افتح|افتحي)'
    thing = r'(?:النور|نور|الانوار|انوار|الاضاءه|اضاءه|اللمبات|لمبات|الضوء|ضوء)'
    place = r'(?:(?:في|بتاع|بتاعت)\s+)?(?:الصاله|صاله|غرفه المعيشه|غرفه الجلوس|الريسبشن)'
    for verb, desired in ((off, 'off'), (on, 'on')):
        if re.fullmatch(verb + r'\s+' + thing + r'\s+' + place, normalized):
            return {'supported':True,'intent':'light.set','target':GROUP,'members':list(MEMBERS),
                    'desired':desired,'original_text':text,'normalized_text':normalized,
                    'requires_second_name':False,'receipt_ack':None,
                    'success_reply':('طفيت نور الصالة.' if desired=='off' else 'شغلت نور الصالة.'),
                    'parser':'allowlisted_arabic_grammar_not_llm','actual_yasmina_utterance_received':False}
    return {'supported':False,'intent':None,'target':None,'original_text':text,
            'normalized_text':normalized,'reason':'unsupported_or_ambiguous',
            'reply':'الأمر ده مش مدعوم في المسار الحالي، أو محتاج تحديد أوضح.',
            'actual_yasmina_utterance_received':False}


class Ledger:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, received REAL NOT NULL, source TEXT NOT NULL,
                phase TEXT NOT NULL, plan TEXT NOT NULL, result TEXT, updated REAL NOT NULL)''')
            # An interrupted attempt is never silently replayed after a restart.
            db.execute("UPDATE events SET phase='interrupted_no_retry', updated=? WHERE phase IN ('claimed','dispatching')", (time.time(),))
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def claim(self, identity: str, source: str, plan: dict) -> bool:
        with self.connection() as db:
            return db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)',
                (identity,time.time(),source,'claimed',json.dumps(plan,ensure_ascii=False),None,time.time())).rowcount == 1

    def finish(self, identity: str, phase: str, result: dict | None = None):
        with self.connection() as db:
            db.execute('UPDATE events SET phase=?,result=?,updated=? WHERE event_id=?',
                (phase,json.dumps(result,ensure_ascii=False) if result is not None else None,time.time(),identity))

    def get(self, identity: str) -> dict | None:
        with self.connection() as db:
            row = db.execute('SELECT * FROM events WHERE event_id=?',(identity,)).fetchone()
        if not row:
            return None
        obj = dict(row)
        obj['plan'] = json.loads(obj['plan'])
        obj['result'] = json.loads(obj['result']) if obj['result'] else None
        return obj

    def recent(self, limit: int = 20) -> list[dict]:
        with self.connection() as db:
            ids = [r[0] for r in db.execute('SELECT event_id FROM events ORDER BY received DESC LIMIT ?', (max(1,min(limit,50)),))]
        return [self.get(identity) for identity in ids]


class HomeAssistant:
    """Only the existing local HA API is contacted; no redirects or state spoofing."""
    def __init__(self, token_path: pathlib.Path, base: str = 'http://127.0.0.1:8123', request_timeout: float = 3.0, write_timeout: float | None = None):
        if base != 'http://127.0.0.1:8123':
            raise ControlError('ha_host_not_allowlisted')
        if not 0.05 <= request_timeout <= 3.0:
            raise ControlError('ha_timeout_invalid')
        if write_timeout is None:
            write_timeout=request_timeout
        if isinstance(write_timeout,bool) or not isinstance(write_timeout,(int,float)) or not 0.05 <= write_timeout <= 3.0:
            raise ControlError('ha_write_timeout_invalid')
        self.request_timeout = request_timeout
        self.write_timeout = float(write_timeout)
        self.base, self.token_path = base, pathlib.Path(token_path)

    def request(self, path: str, payload: dict | None = None):
        read_paths = ('/api/', '/api/services') + tuple('/api/states/'+e for e in READABLE)
        if payload is None:
            if path not in read_paths:
                raise ControlError('ha_read_path_not_allowed')
        elif (path not in ('/api/services/light/turn_on','/api/services/light/turn_off') or
              payload != {'entity_id':list(MEMBERS)}):
            raise ControlError('ha_write_not_allowlisted')
        if self.token_path.is_symlink():
            raise ControlError('ha_credential_symlink_refused')
        if not self.token_path.is_file():
            raise ControlError('ha_credential_missing')
        if self.token_path.stat().st_mode & 0o077:
            raise ControlError('ha_credential_permissions_too_broad')
        token = self.token_path.read_text().strip()
        if not token.startswith('eyJ') or token.count('.') != 2:
            raise ControlError('ha_credential_invalid')
        # This API was already fixed to loopback HTTP. Do not construct a
        # general-purpose HTTPS opener (and a fresh system trust store) for each
        # of seven parallel local reads: CPU throttling can consume the entire
        # native action deadline before an OFF request is even dispatched.
        # Direct HTTPConnection does not use proxies, DNS, TLS, or redirects.
        connection = http.client.HTTPConnection('127.0.0.1',8123,timeout=self.write_timeout if payload is not None else self.request_timeout)
        try:
            connection.request('POST' if payload is not None else 'GET',path,
                body=json.dumps(payload).encode() if payload is not None else None,
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',
                         'Accept':'application/json','Connection':'close'})
            response=connection.getresponse()
            if 300 <= response.status < 400:
                raise ControlError('ha_redirect_refused')
            if not 200 <= response.status < 300:
                raise ControlError('ha_http_'+str(response.status))
            raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:
                raise ControlError('ha_response_too_large')
            return json.loads(raw)
        except (OSError,ValueError,http.client.HTTPException):
            raise ControlError('ha_transport_error') from None
        finally:
            connection.close()


    def read_state(self, entity: str) -> dict:
        if entity not in READABLE:
            raise ControlError('entity_not_allowed')
        obj = self.request('/api/states/'+entity)
        if not isinstance(obj,dict) or obj.get('entity_id') != entity or not isinstance(obj.get('state'),str):
            raise ControlError('ha_entity_invalid')
        return obj

    def _state(self, entity: str) -> tuple[str,str]:
        if entity not in ALLOWED:
            raise ControlError('entity_not_allowed')
        obj = self.read_state(entity)
        if obj.get('state') not in ('on','off'):
            raise ControlError('ha_entity_unavailable')
        if entity == GROUP and set(obj.get('attributes',{}).get('entity_id',[])) != set(MEMBERS):
            raise ControlError('ha_group_members_changed')
        return entity, obj['state']

    def states(self) -> dict[str,str]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ALLOWED)) as pool:
            return dict(pool.map(self._state,ALLOWED))

    def set_light(self, desired: str):
        if desired not in ('on','off'):
            raise ControlError('action_not_allowed')
        # Recheck group identity immediately before sending; target only the six
        # original entities even if somebody edits the group concurrently.
        self._state(GROUP)
        return self.request('/api/services/light/turn_'+desired, {'entity_id':list(MEMBERS)})


class Controller:
    def __init__(self, adapter, ledger: Ledger, policy: Policy, boot_at: float | None = None):
        self.adapter, self.ledger, self.policy = adapter, ledger, policy
        self.boot_at = time.time() if boot_at is None else boot_at
        self._action_lock = threading.Lock()

    def validate_event(self, event: dict) -> dict:
        if not isinstance(event,dict):
            raise ControlError('invalid_event')
        identity = event.get('id')
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',identity):
            raise ControlError('invalid_event_id')
        if event.get('source') not in ('dashboard_text','authenticated_text_adapter','simulation'):
            raise ControlError('unverified_input_source')
        stamp = event.get('created_at')
        if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp):
            raise ControlError('invalid_timestamp')
        age = time.time()-stamp
        if stamp < self.boot_at or age > self.policy.max_event_age_seconds or age < -self.policy.future_tolerance_seconds:
            raise ControlError('stale_or_future_event')
        return interpret(event.get('text'))

    def execute(self, event: dict) -> dict:
        plan = self.validate_event(event)
        identity = event['id']
        if not self.ledger.claim(identity,event['source'],plan):
            previous = self.ledger.get(identity)
            return {'phase':'duplicate_ignored','previous_phase':previous['phase'],
                    'device_writes':0,'reply':None,'speech_requests':0}
        def done(phase, reply=None, **values):
            result = {'phase':phase,'reply':reply,'receipt_ack':None,'speech_requests':0,
                      'spoken':False,'voice_origin_proven':False,**values}
            self.ledger.finish(identity,phase,result)
            return result
        if not plan['supported']:
            return done('unsupported',plan['reply'],device_writes=0)
        if not self.policy.actions_enabled:
            return done('blocked_by_commissioning', 'التنفيذ الفعلي مقفول أثناء التجهيز.', device_writes=0)
        with self._action_lock:
            try:
                self.validate_event(event)  # Reject an event that expired while queued.
                before = self.adapter.states()
                if set(before) != set(ALLOWED) or any(s not in ('on','off') for s in before.values()):
                    raise ControlError('incomplete_or_unavailable_state')
            except Exception as exc:
                return done('preflight_failed','مش قادر أتأكد من حالة الإضاءة دلوقتي.',
                            device_writes=0,error=error_code(exc))
            desired = plan['desired']
            if all(s==desired for s in before.values()):
                return done('already_in_requested_state',
                    'نور الصالة مطفي بالفعل.' if desired=='off' else 'نور الصالة شغال بالفعل.',
                    device_writes=0,verified=True,after=before)
            self.ledger.finish(identity,'dispatching')
            try:
                self.adapter.set_light(desired)  # Exactly one request, no automatic resend.
            except Exception as exc:
                return done('dispatch_uncertain','ما قدرتش أتأكد إن الأمر اتنفّذ.',
                            device_writes_attempted=1,verified=False,error=error_code(exc))
            after = None
            for _ in range(self.policy.verification_attempts):
                if self.policy.verification_delay_seconds:
                    time.sleep(self.policy.verification_delay_seconds)
                try:
                    after = self.adapter.states()
                    if set(after)==set(ALLOWED) and all(s==desired for s in after.values()):
                        return done('verified',plan['success_reply'],device_writes=1,verified=True,after=after)
                except Exception:
                    pass
            return done('unverified','بعت الأمر، لكن ما قدرتش أتأكد إن كل الإضاءة اتغيّرت.',
                        device_writes=1,verified=False,after=after)


def error_code(exc: Exception) -> str:
    # Never pass arbitrary provider bodies, headers, URLs or credentials into logs.
    code = str(exc)
    if isinstance(exc,ControlError) and re.fullmatch(r'[a-z0-9_]{1,90}',code):
        return code
    return type(exc).__name__


class SimulatedHomeAssistant:
    """Deterministic in-memory fixtures; deliberately contains no network client."""
    def __init__(self, before='on', outcome='success'):
        self.values = {e:before for e in ALLOWED}
        self.outcome, self.writes, self.reads = outcome, 0, 0

    def states(self):
        self.reads += 1
        if self.outcome=='unavailable':
            raise ControlError('ha_entity_unavailable')
        return dict(self.values)

    def set_light(self, desired):
        self.writes += 1
        if self.outcome=='timeout':
            raise TimeoutError('simulated')
        if self.outcome=='partial':
            for e in ALLOWED[:-1]: self.values[e]=desired
        elif self.outcome=='success':
            self.values = {e:desired for e in ALLOWED}
        return []
