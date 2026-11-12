"""Typed native TV control. One dispatch, verified result, persistent deduplication.
Uses only the separately consented TV route; no free-form speech or content search.
"""
from __future__ import annotations
import hashlib,json,threading,time
from bundle_catalog import TV_ID,CORE_APPS,BundleError,number
from bundle_native import ON,RANGE
from native_provider import NativeError
TOGGLE='devices.capabilities.toggle'
MODE='devices.capabilities.mode'
APP_MODES={'one':'youtube','two':'shahid','three':'watchit','four':'yango'}
KEY_MODES={'one':'home','two':'back','three':'next'}

def tv_device():
    return {'id':TV_ID,'name':'التليفزيون','room':'الصالة','type':'devices.types.media_device.tv',
      'description':'تلفزيون شاومي: الطاقة والصوت والتشغيل والتطبيقات الأربعة',
      'capabilities':[
       {'type':ON,'retrievable':True,'reportable':False},
       {'type':RANGE,'retrievable':True,'reportable':False,'parameters':{'instance':'volume','unit':'unit.percent','random_access':True,'range':{'min':0,'max':100,'precision':1}}},
       {'type':TOGGLE,'retrievable':True,'reportable':False,'parameters':{'instance':'mute'}},
       {'type':TOGGLE,'retrievable':True,'reportable':False,'parameters':{'instance':'pause'}},
       {'type':MODE,'retrievable':True,'reportable':False,'parameters':{'instance':'input_source','modes':[{'value':v} for v in APP_MODES]}},
       {'type':MODE,'retrievable':False,'reportable':False,'parameters':{'instance':'program','modes':[{'value':v} for v in KEY_MODES]}}
      ]}

def parse_tv(row):
    if not isinstance(row,dict) or row.get('id')!=TV_ID or set(row)-{'id','capabilities','custom_data'}:
        raise NativeError('invalid_tv_device')
    caps=row.get('capabilities')
    if not isinstance(caps,list) or not 1<=len(caps)<=6:raise NativeError('invalid_tv_capabilities')
    actions=[];seen=set()
    for c in caps:
        if not isinstance(c,dict) or set(c)!={'type','state'}:raise NativeError('invalid_tv_capability')
        s=c['state']
        if not isinstance(s,dict) or not isinstance(c['type'],str) or not isinstance(s.get('instance'),str):raise NativeError('invalid_tv_state')
        key=(c['type'],s['instance'])
        if key in seen:raise NativeError('duplicate_tv_capability')
        seen.add(key)
        if key in ((ON,'on'),(TOGGLE,'mute'),(TOGGLE,'pause')):
            if set(s)!={'instance','value'} or type(s['value']) is not bool:raise NativeError('invalid_tv_boolean')
            action=('on' if s['value'] else 'off') if key==(ON,'on') else 'mute' if key==(TOGGLE,'mute') else 'pause' if s['value'] else 'resume'
            actions.append({'action':action,'value':s['value'] if action=='mute' else None})
        elif key==(RANGE,'volume'):
            if set(s)-{'instance','value','relative'} or type(s.get('relative',False)) is not bool:raise NativeError('invalid_tv_volume')
            relative=s.get('relative',False);value=number(s.get('value'),-100 if relative else 0,100,integer=True)
            actions.append({'action':'volume_relative' if relative else 'volume','value':value})
        elif key in ((MODE,'input_source'),(MODE,'program')):
            if set(s)!={'instance','value'} or not isinstance(s['value'],str):raise NativeError('invalid_tv_mode')
            modes=APP_MODES if key==(MODE,'input_source') else KEY_MODES
            if s['value'] not in modes:raise NativeError('unsupported_tv_mode')
            actions.append({'action':'open_app' if key==(MODE,'input_source') else modes[s['value']],
                            'value':modes[s['value']] if key==(MODE,'input_source') else None})
        else:raise NativeError('unsupported_tv_capability')
    kinds={a['action'] for a in actions}
    if 'off' in kinds and len(actions)>1:raise NativeError('off_conflicts_with_tv_settings')
    if 'pause' in kinds and 'open_app' in kinds:raise NativeError('pause_during_app_change_refused')
    order={'on':0,'open_app':1,'home':1,'back':1,'next':1,'volume':2,'volume_relative':2,'mute':3,'pause':4,'resume':4,'off':0}
    return sorted(actions,key=lambda a:order[a['action']])

def satisfied(action,probe):
    s=probe.state;a=action['action'];v=action['value']
    if a=='on':return s.power is True
    if a=='off':return s.power is False
    if a=='mute':return s.muted is v
    if a=='volume':return s.volume is not None and abs(s.volume-v)<=1
    if a=='volume_relative':return probe.requested_volume is not None and s.volume is not None and abs(s.volume-probe.requested_volume)<=1
    if a=='open_app':return s.power is True and s.app==CORE_APPS[v]['package']
    if a=='pause':return s.playback=='paused'
    if a=='resume':return s.playback=='playing'
    # Navigation is an explicit non-retrievable key action; a device-side input
    # success marker proves key delivery, not a claim about content or screen.
    if a in ('home','back','next'):return probe.key_delivery_verified is True
    return False

class TVNative:
    def __init__(self,transport,ledger,enabled=lambda:False):
        self.transport=transport;self.ledger=ledger;self.enabled=enabled
        self.lock=threading.Lock();self.cache=None;self.cache_at=0
    def read(self,max_age=1.0):
        if self.cache and time.monotonic()-self.cache_at<=max_age:return self.cache
        if not self.lock.acquire(timeout=.02):
            if self.cache and time.monotonic()-self.cache_at<=2:return self.cache
            raise BundleError('tv_busy')
        try:
            p=self.transport.transaction(timeout=2.65);self.cache=p;self.cache_at=time.monotonic();return p
        finally:self.lock.release()
    def query(self):
        try:p=self.read();s=p.state
        except Exception:return {'id':TV_ID,'error_code':'DEVICE_UNREACHABLE'}
        caps=[{'type':ON,'state':{'instance':'on','value':s.power}}]
        if s.volume is not None:caps.append({'type':RANGE,'state':{'instance':'volume','value':round(s.volume)}})
        if s.muted is not None:caps.append({'type':TOGGLE,'state':{'instance':'mute','value':s.muted}})
        if s.playback in ('playing','paused'):caps.append({'type':TOGGLE,'state':{'instance':'pause','value':s.playback=='paused'}})
        mode=next((m for m,a in APP_MODES.items() if CORE_APPS[a]['package']==s.app),None)
        if mode:caps.append({'type':MODE,'state':{'instance':'input_source','value':mode}})
        return {'id':TV_ID,'capabilities':caps}
    def execute(self,identity,actions):
        if not self.enabled():return {'status':'ERROR','error_code':'DEVICE_UNREACHABLE','reason':'tv_not_enabled'}
        claimed,old=self.ledger.claim(identity,{'tv_actions':actions})
        if not claimed:
            return json.loads(old['response']) if old.get('response') else {'status':'ERROR','error_code':'DEVICE_UNREACHABLE','reason':'in_progress_no_retry'}
        started=time.monotonic();diag={'actions':actions,'physical_dispatches':0}
        def done(ok,reason,error='DEVICE_UNREACHABLE'):
            result={'status':'DONE' if ok else 'ERROR','reason':reason,'elapsed_ms':round((time.monotonic()-started)*1000,2)}
            if not ok:result['error_code']=error
            diag['elapsed_ms']=result['elapsed_ms'];self.ledger.finish(identity,result,diag);return result
        if not self.lock.acquire(timeout=.05):return done(False,'tv_busy')
        try:
            actual=[dict(a) for a in actions]
            # A cached exact state can prove idempotent requests, but never claims
            # arbitrary navigation or application state from stale information.
            if self.cache and time.monotonic()-self.cache_at<.4 and all(satisfied(a,self.cache) for a in actual):
                return done(True,'already_satisfied_fresh_read')
            # Power-on and app launch may require several seconds after WoL. Accept one
            # bounded, deduplicated worker sequence quickly, then verify in worker journal.
            if len(actual)==1 and actual[0]['action'] in ('on','open_app'):
                program='wake' if actual[0]['action']=='on' else actual[0]['value']
                self.ledger.phase(identity,'dispatching',write=True);diag['physical_dispatches']=1
                self.cache=None
                try:
                    accepted=self.transport.sequence(identity,program,expires_at=time.time()+8.0)
                except Exception as exc:
                    diag['transport_error']=str(exc) if isinstance(exc,BundleError) else type(exc).__name__
                    return done(False,'deferred_sequence_not_accepted')
                if not isinstance(accepted,dict) or accepted.get('accepted') is not True:
                    diag['transport_error']='sequence_not_accepted'
                    return done(False,'deferred_sequence_not_accepted')
                diag['deferred_program']=program;diag['deferred_job_id']=accepted.get('job_id');diag['wake_packets']=accepted.get('wake_packets')
                return done(True,'deferred_sequence_accepted')
            remaining=2.75-(time.monotonic()-started)
            if remaining<.9:return done(False,'insufficient_budget_no_dispatch')
            self.ledger.phase(identity,'dispatching',write=True);diag['physical_dispatches']=1
            self.cache=None
            try:
                p=self.transport.transaction(actual[0]['action'],actual[0]['value'],timeout=remaining) if len(actual)==1 else self.transport.transaction('batch',actual,timeout=remaining)
            except Exception as exc:
                diag['transport_error']=str(exc) if isinstance(exc,BundleError) else type(exc).__name__
                # Never repeat action. This deadline cannot safely certify a result
                # after an uncertain transport return; a subsequent QUERY can read it.
                return done(False,'dispatch_or_readback_unconfirmed_no_retry')
            self.cache=p;self.cache_at=time.monotonic();diag['readback']=p.safe()
            if all(satisfied(a,p) for a in actual):return done(True,'device_result_verified')
            if any(a['action'] in ('pause','resume') for a in actual) and p.state.playback is None:
                return done(False,'no_active_media_state','NOT_SUPPORTED_IN_CURRENT_MODE')
            return done(False,'requested_state_not_verified')
        finally:self.lock.release()
