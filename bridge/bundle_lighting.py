"""Pure lighting plans and bounded execution. Never retries physical commands."""
from __future__ import annotations
import concurrent.futures,dataclasses,json,threading,time
from bundle_catalog import MEMBERS,LampState,BundleError,check_snapshot,number,targets

@dataclasses.dataclass(frozen=True)
class Plan:
    changes: dict
    intent_summary: tuple
    def serializable(self):return {'changes':self.changes,'intent_summary':list(self.intent_summary)}

def fields(obj,allowed,required):
    if not isinstance(obj,dict) or set(obj)-set(allowed) or set(required)-set(obj):raise BundleError('invalid_intent_fields')

def plan(snapshot,intents):
    check_snapshot(snapshot)
    if not isinstance(intents,list) or not 1<=len(intents)<=24:raise BundleError('invalid_intent_batch')
    changes={};summary=[]
    def put(e,k,v):
        row=changes.setdefault(e,{})
        if k in row and row[k]!=v:raise BundleError('conflicting_overlap')
        row[k]=v
    def count(n):
        lit=[e for e in MEMBERS if snapshot[e].power]
        unlit=[e for e in MEMBERS if not snapshot[e].power]
        selected=set((lit+unlit)[:n])
        for e in MEMBERS:put(e,'power',e in selected)
    for item in intents:
        if not isinstance(item,dict):raise BundleError('invalid_intent')
        op=item.get('op');summary.append(op)
        if op in ('count','add_count','configure') and any(v.power is None for v in snapshot.values()):
            raise BundleError('unavailable_lamp_in_group_operation')
        if op=='count':
            fields(item,('op','count'),('op','count'));count(number(item['count'],0,6,integer=True));continue
        if op=='configure':
            fields(item,('op','count','brightness','kelvin','only_on'),('op',))
            if 'only_on' in item and type(item['only_on']) is not bool:raise BundleError('invalid_only_on')
            values={}
            if 'brightness' in item:values['brightness']=number(item['brightness'],1,100)
            if 'kelvin' in item:values['kelvin']=number(item['kelvin'],2700,6500,integer=True)
            if 'count' in item:
                n=number(item['count'],0,6,integer=True);count(n)
                selected=[e for e in MEMBERS if changes[e].get('power') is True]
            elif item.get('only_on',True):selected=[e for e in MEMBERS if snapshot[e].power]
            else:selected=list(MEMBERS)
            if not values and 'count' not in item:raise BundleError('empty_configuration')
            for e in selected:
                for key,value in values.items():put(e,key,value)
                if values:put(e,'power',True)
            continue
        if op=='add_count':
            fields(item,('op','delta'),('op','delta'))
            n=sum(s.power for s in snapshot.values())+number(item['delta'],-6,6,integer=True)
            if not 0<=n<=6:raise BundleError('count_would_exceed_available')
            count(n);continue
        if op not in ('power','brightness','temperature','step','set'):raise BundleError('unsupported_intent')
        fields(item,('op','targets','power','brightness','kelvin','delta','relative','only_on'),('op','targets'))
        ids=targets(item['targets'])
        if any(snapshot[e].power is None for e in ids):raise BundleError('unavailable_target_lamp')
        for boolean in ('only_on','relative'):
            if boolean in item and type(item[boolean]) is not bool:raise BundleError('invalid_'+boolean)
        if op=='power':
            if set(item)-{'op','targets','power'} or type(item.get('power')) is not bool:raise BundleError('invalid_power')
            for e in ids:put(e,'power',item['power'])
        elif op=='step':
            if set(item)-{'op','targets','delta'}:raise BundleError('invalid_step')
            delta=number(item.get('delta'),-100,100)
            if delta==0:continue
            for e in ids:
                if not snapshot[e].power:continue
                if snapshot[e].brightness is None:raise BundleError('brightness_unavailable')
                value=max(0.0,min(100.0,snapshot[e].brightness+delta))
                if value==0:put(e,'power',False)
                else:put(e,'brightness',value);put(e,'power',True)
        elif op=='brightness':
            if set(item)-{'op','targets','brightness','relative','only_on'}:raise BundleError('invalid_brightness')
            relative=item.get('relative',False);value=number(item.get('brightness'),-100 if relative else 0,100)
            if relative or item.get('only_on',False):ids=tuple(e for e in ids if snapshot[e].power)
            for e in ids:
                v=value
                if relative:
                    if snapshot[e].brightness is None:raise BundleError('brightness_unavailable')
                    v=max(0.0,min(100.0,snapshot[e].brightness+value))
                if v==0:put(e,'power',False)
                else:put(e,'brightness',v);put(e,'power',True)
        elif op=='temperature':
            if set(item)-{'op','targets','kelvin','only_on'}:raise BundleError('invalid_temperature')
            kelvin=number(item.get('kelvin'),2700,6500,integer=True)
            if item.get('only_on',False):ids=tuple(e for e in ids if snapshot[e].power)
            for e in ids:put(e,'kelvin',kelvin);put(e,'power',True)
        else:
            if set(item)-{'op','targets','power','brightness','kelvin','only_on'}:raise BundleError('invalid_set')
            values={k:item[k] for k in ('power','brightness','kelvin') if k in item}
            if not values:raise BundleError('empty_set')
            if 'power' in values and type(values['power']) is not bool:raise BundleError('invalid_power')
            if values.get('power') is False and any(k in values for k in ('brightness','kelvin')):raise BundleError('off_with_light_attributes')
            if 'brightness' in values:values['brightness']=number(values['brightness'],1,100)
            if 'kelvin' in values:values['kelvin']=number(values['kelvin'],2700,6500,integer=True)
            if item.get('only_on',False):ids=tuple(e for e in ids if snapshot[e].power)
            if any(k in values for k in ('brightness','kelvin')):values['power']=True
            for e in ids:
                for k,v in values.items():put(e,k,v)
    for row in changes.values():
        if row.get('power') is False and any(k in row for k in ('brightness','kelvin')):raise BundleError('conflicting_overlap')
    return Plan({e:changes[e] for e in MEMBERS if e in changes},tuple(summary))

def matches(state,change):
    if not isinstance(state,LampState) or state.power is None:return False
    if 'power' in change and state.power!=change['power']:return False
    if 'brightness' in change and (state.brightness is None or abs(state.brightness-change['brightness'])>1.0):return False
    if 'kelvin' in change and (state.kelvin is None or abs(state.kelvin-change['kelvin'])>100):return False
    return True

def grouped_writes(snapshot,planned):
    groups={}
    for e,change in planned.changes.items():
        if matches(snapshot[e],change):continue
        key=json.dumps(change,sort_keys=True,separators=(',',':'));groups.setdefault(key,[]).append(e)
    result=[{'targets':tuple(ids),'change':json.loads(key)} for key,ids in groups.items()]
    return sorted(result,key=lambda r:(r['change'].get('power') is not False,MEMBERS.index(r['targets'][0])))

def safe_error(exc):
    code=str(exc)
    if isinstance(exc,BundleError) and code.replace('_','').isalnum() and len(code)<80:return code
    return type(exc).__name__

class LightingExecutor:
    def __init__(self,adapter,ledger,enabled=lambda:False,budget=2.25):
        self.adapter=adapter;self.ledger=ledger;self.enabled=enabled;self.budget=budget;self.lock=threading.Lock()
    def execute(self,identity,intents):
        if not isinstance(identity,str) or not 8<=len(identity)<=160 or not all(c.isalnum() or c in '_:-.' for c in identity):raise BundleError('invalid_request_identity')
        if not isinstance(intents,list):raise BundleError('invalid_intents')
        if not self.enabled():return {'status':'BLOCKED','reason':'bundle_not_activated','physical_writes':0}
        claimed,old=self.ledger.claim(identity,{'bundle_v1_intents':intents})
        if not claimed:
            if old.get('response'):
                result=json.loads(old['response']);result['replayed']=True;return result
            return {'status':'ERROR','reason':'interrupted_or_in_progress_no_retry','physical_writes':0}
        start=time.monotonic();deadline=start+self.budget;diag={'planned_targets':[],'writes':0,'transport_errors':[]}
        def finish(status,reason,**extra):
            result={'status':status,'reason':reason,'physical_writes':diag['writes'],'elapsed_ms':round((time.monotonic()-start)*1000,2),**extra}
            self.ledger.finish(identity,result,diag);return result
        if not self.lock.acquire(timeout=.05):return finish('ERROR','another_request_in_progress')
        try:
            try:
                before=check_snapshot(self.adapter.snapshot());planned=plan(before,intents)
                diag['planned_targets']=list(planned.changes);writes=grouped_writes(before,planned)
            except Exception as exc:return finish('ERROR','preflight_or_plan_failed',error=safe_error(exc))
            if not writes:return finish('DONE','already_satisfied',verified=True)
            if deadline-time.monotonic()<1.1:return finish('ERROR','insufficient_time_before_dispatch')
            def send(row):
                if deadline-time.monotonic()<.8:return ('not_started','deadline_before_dispatch')
                self.ledger.phase(identity,'dispatching',write=True)
                try:self.adapter.apply(row['targets'],row['change']);return ('sent',None)
                except Exception as exc:return ('uncertain',safe_error(exc))
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:outcomes=list(pool.map(send,writes))
            diag['writes']=sum(kind!='not_started' for kind,error in outcomes)
            diag['transport_errors']=[error for kind,error in outcomes if error]
            fatal=('ha_http_401','ha_http_403','target_outside_allowlist','ha_group_members_changed','invalid_write_fields')
            if any(error in fatal for _,error in outcomes):return finish('ERROR','dispatch_rejected')
            if any(kind=='not_started' for kind,_ in outcomes):return finish('ERROR','partial_dispatch_due_to_deadline')
            while deadline-time.monotonic()>.36:
                time.sleep(.06)
                try:after=check_snapshot(self.adapter.snapshot())
                except Exception as exc:diag['last_read_error']=safe_error(exc);continue
                if all(matches(after[e],v) for e,v in planned.changes.items()):
                    return finish('DONE','target_states_verified',verified=True,reconciled=any(kind=='uncertain' for kind,_ in outcomes))
            return finish('ERROR','result_not_fully_verified',verified=False)
        finally:self.lock.release()
