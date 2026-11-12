"""Native Yango lighting expansion; does not pretend to receive raw speech.
Keeps the established group on/off path unchanged. Extra features are gated.
"""
from __future__ import annotations
import hashlib,json
from bundle_catalog import GROUP_ID,MEMBERS,BULB_IDS,DEVICE_TARGETS,NAMES,BundleError,number
from bundle_lighting import LightingExecutor
from native_provider import request_id,NativeError

ON='devices.capabilities.on_off'
RANGE='devices.capabilities.range'
COLOR='devices.capabilities.color_setting'

def specs():
    return [
     {'type':ON,'retrievable':True,'reportable':False},
     {'type':RANGE,'retrievable':True,'reportable':False,'parameters':{
      'instance':'brightness','unit':'unit.percent','random_access':True,
      'range':{'min':0,'max':100,'precision':1}}},
     {'type':COLOR,'retrievable':True,'reportable':False,'parameters':{
      'temperature_k':{'min':2700,'max':6500}}}]

class ExpandedNativeProvider:
    def __init__(self,legacy,engine,enabled=lambda:False):
        self.legacy=legacy;self.engine=engine;self.enabled=enabled
        # Serialize both legacy and expanded paths through the same action lock.
        self.engine.lock=legacy.lock
    def discover(self,rid):
        result=self.legacy.discover(rid)
        if not self.enabled():return result
        group=result['payload']['devices'][0]
        group['capabilities']=specs()
        group['description']='مجموعة الست لمبات؛ تشغيل وإطفاء وسطوع ودرجة الأبيض'
        group['device_info']['sw_version']='bundle-1.0.0'
        for number_,did in enumerate(BULB_IDS,1):
            result['payload']['devices'].append({'id':did,'name':NAMES[did],
             'room':'الصالة',
             'type':'devices.types.light','status_info':{'reportable':False},
             'capabilities':specs()})
        return result
    def query(self,rid,body):
        request_id(rid)
        if not self.enabled():return self.legacy.query(rid,body)
        if not isinstance(body,dict) or set(body)!={'devices'} or not isinstance(body['devices'],list) or not 1<=len(body['devices'])<=7:
            raise NativeError('invalid_query')
        for row in body['devices']:
            if not isinstance(row,dict) or set(row)-{'id','custom_data'} or not isinstance(row.get('id'),str):raise NativeError('invalid_device')
        try:snapshot=self.engine.adapter.snapshot()
        except Exception:snapshot=None
        result=[]
        for row in body['devices']:
            did=row['id']
            if did not in DEVICE_TARGETS:result.append({'id':did,'error_code':'DEVICE_NOT_FOUND'});continue
            if snapshot is None:result.append({'id':did,'error_code':'DEVICE_UNREACHABLE'});continue
            states=[snapshot[e] for e in DEVICE_TARGETS[did]]
            if any(x.power is None for x in states):
                result.append({'id':did,'error_code':'DEVICE_UNREACHABLE'});continue
            caps=[{'type':ON,'state':{'instance':'on','value':any(x.power for x in states)}}]
            lit=[s for s in states if s.power]
            if not lit:brightness=0
            elif all(s.brightness is not None for s in lit):brightness=sum(s.brightness for s in lit)/len(lit)
            else:brightness=None
            if brightness is not None:caps.append({'type':RANGE,'state':{'instance':'brightness','value':round(brightness,1)}})
            temps=[s.kelvin for s in (lit or states)]
            # No invented white temperature for an unknown or mixed group.
            if temps and None not in temps and max(temps)-min(temps)<=100:
                caps.append({'type':COLOR,'state':{'instance':'temperature_k','value':round(sum(temps)/len(temps))}})
            result.append({'id':did,'capabilities':caps})
        return {'request_id':rid,'payload':{'devices':result}}
    def parse(self,body):
        if not isinstance(body,dict) or set(body)!={'payload'}:raise NativeError('invalid_action')
        payload=body['payload']
        if not isinstance(payload,dict) or 'devices' not in payload or set(payload)-{'devices','action_id'}:raise NativeError('invalid_action')
        aid=payload.get('action_id')
        if 'action_id' in payload and (not isinstance(aid,str) or not 1<=len(aid)<=256 or not all(32<=ord(c)<127 for c in aid)):
            raise NativeError('invalid_action_id')
        rows=payload['devices']
        if not isinstance(rows,list) or not 1<=len(rows)<=7:raise NativeError('invalid_device_count')
        intents=[]
        for row in rows:
            if not isinstance(row,dict) or set(row)-{'id','capabilities','custom_data'} or row.get('id') not in DEVICE_TARGETS:raise NativeError('device_not_allowed')
            caps=row.get('capabilities')
            if not isinstance(caps,list) or not 1<=len(caps)<=3:raise NativeError('invalid_capabilities')
            seen=set()
            for cap in caps:
                if not isinstance(cap,dict) or set(cap)!={'type','state'}:raise NativeError('invalid_capability')
                state=cap.get('state')
                if not isinstance(state,dict) or 'instance' not in state or 'value' not in state:raise NativeError('invalid_state')
                key=(cap['type'],state['instance'])
                if key in seen:raise NativeError('duplicate_capability')
                seen.add(key);ids=list(DEVICE_TARGETS[row['id']])
                if key==(ON,'on'):
                    if set(state)!={'instance','value'} or type(state['value']) is not bool:raise NativeError('invalid_state')
                    intents.append({'op':'power','targets':ids,'power':state['value']})
                elif key==(RANGE,'brightness'):
                    if set(state)-{'instance','value','relative'} or type(state.get('relative',False)) is not bool:raise NativeError('invalid_state')
                    relative=state.get('relative',False)
                    value=number(state['value'],-100 if relative else 0,100)
                    intents.append({'op':'brightness','targets':ids,'brightness':value,'relative':relative})
                elif key==(COLOR,'temperature_k'):
                    if set(state)!={'instance','value'}:raise NativeError('invalid_state')
                    intents.append({'op':'temperature','targets':ids,'kelvin':number(state['value'],2700,6500,integer=True)})
                else:raise NativeError('capability_not_allowed')
        return intents
    def action(self,rid,body):
        request_id(rid)
        if not self.enabled():return self.legacy.action(rid,body)
        intents=self.parse(body)
        # Preserve the proven single-group path, including acknowledgement reconciliation.
        rows=body['payload']['devices']
        if len(rows)==1 and rows[0]['id']==GROUP_ID and len(intents)==1 and intents[0]['op']=='power':
            return self.legacy.action(rid,body)
        aid=body['payload'].get('action_id',rid)
        identity='bundle_'+hashlib.sha256(aid.encode()).hexdigest()
        result=self.engine.execute(identity,intents)
        status={'status':'DONE'} if result.get('status')=='DONE' else {'status':'ERROR','error_code':'DEVICE_UNREACHABLE'}
        devices=[]
        for row in rows:
            caps=[{'type':c['type'],'state':{'instance':c['state']['instance'],'action_result':dict(status)}} for c in row['capabilities']]
            devices.append({'id':row['id'],'capabilities':caps})
        return {'request_id':rid,'payload':{'devices':devices}}
