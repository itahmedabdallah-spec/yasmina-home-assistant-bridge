"""One native provider for approved lighting/TV features; per-token scope checks.
Logical count programs are explicitly identified, not disguised brightness values.
No speech output or free-form input is implemented by this structured-device API.
"""
from __future__ import annotations
import concurrent.futures,hashlib,json
from bundle_catalog import GROUP_ID,TV_ID,DEVICE_TARGETS,LIGHT_PROGRAM_ID,LIGHT_PROGRAMS,BundleError
from bundle_native import ExpandedNativeProvider
from bundle_tv_native import tv_device,parse_tv,MODE
from bundle_scope_guard import authorize_request,verified_scopes,DeviceScopeError
from native_provider import NativeError,request_id

class UnifiedProvider:
    scoped=True
    def __init__(self,lighting,tv,*,published=lambda:False,active=lambda:False):
        self.lighting=lighting;self.tv=tv;self.published=published;self.active=active
    def discover(self,rid,claims):
        scopes=verified_scopes(claims)
        result=self.lighting.discover(rid)
        if not self.published():return result
        result['payload']['devices'].append({
            'id':LIGHT_PROGRAM_ID,'name':'تنظيم إضاءة الصالة','room':'الصالة',
            'type':'devices.types.other',
            'description':'تحكم منطقي: البرامج ١–٦ لعدد اللمبات، ٧ زيادة لمبة، ٨ تقليل لمبة. ليس جهاز إضاءة إضافيًا.',
            'capabilities':[{'type':MODE,'retrievable':False,'reportable':False,
                'parameters':{'instance':'program','modes':[{'value':v} for v in LIGHT_PROGRAMS]}}]})
        if 'home:tv' in scopes:result['payload']['devices'].append(tv_device())
        return result
    def query(self,rid,body,claims):
        request_id(rid)
        if not isinstance(body,dict) or set(body)!={'devices'} or not isinstance(body['devices'],list):raise NativeError('invalid_query')
        rows=body['devices']
        if any(not isinstance(r,dict) or set(r)-{'id','custom_data'} for r in rows):raise NativeError('invalid_query_device')
        ids=[r.get('id') for r in rows]
        authorize_request(claims,ids,tv_release_ready=self.published())
        if not self.published():return self.lighting.legacy.query(rid,body)
        lights=[r for r in rows if r['id'] in DEVICE_TARGETS]
        def light_query():return self.lighting.query(rid,{'devices':lights})['payload']['devices'] if lights else []
        result=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            lf=pool.submit(light_query);tf=pool.submit(self.tv.query) if TV_ID in ids else None
            result.extend(lf.result())
            if tf:result.append(tf.result())
        if LIGHT_PROGRAM_ID in ids:result.append({'id':LIGHT_PROGRAM_ID,'capabilities':[]})
        indexed={x['id']:x for x in result}
        return {'request_id':rid,'payload':{'devices':[indexed[i] for i in ids]}}
    @staticmethod
    def count_intent(row):
        if set(row)-{'id','capabilities','custom_data'}:raise NativeError('invalid_count_device')
        caps=row.get('capabilities')
        if not isinstance(caps,list) or len(caps)!=1:raise NativeError('invalid_count_capability')
        c=caps[0]
        if not isinstance(c,dict) or set(c)!={'type','state'} or c['type']!=MODE:raise NativeError('invalid_count_capability')
        s=c['state']
        if not isinstance(s,dict) or set(s)!={'instance','value'} or s['instance']!='program' or s.get('value') not in LIGHT_PROGRAMS:
            raise NativeError('invalid_count_program')
        op,value=LIGHT_PROGRAMS[s['value']]
        return {'op':op,'count' if op=='count' else 'delta':value}
    def action(self,rid,body,claims):
        request_id(rid)
        if not isinstance(body,dict) or set(body)!={'payload'}:raise NativeError('invalid_action')
        payload=body['payload']
        if not isinstance(payload,dict) or 'devices' not in payload or set(payload)-{'devices','action_id'}:raise NativeError('invalid_payload')
        rows=payload['devices']
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise NativeError('invalid_devices')
        ids=[r.get('id') for r in rows]
        # Validate the ENTIRE mixed-device batch, including scope and all values,
        # before dispatching any part. Caller-supplied custom_data is ignored.
        authorize_request(claims,ids,tv_release_ready=self.published())
        aid=payload.get('action_id',rid)
        if not isinstance(aid,str) or not 1<=len(aid)<=256 or not all(32<=ord(c)<127 for c in aid):raise NativeError('invalid_action_id')
        lights=[r for r in rows if r['id'] in DEVICE_TARGETS]
        programs=[r for r in rows if r['id']==LIGHT_PROGRAM_ID]
        televisions=[r for r in rows if r['id']==TV_ID]
        light_body={'payload':{'devices':lights,'action_id':aid}}
        intents=self.lighting.parse(light_body) if lights else []
        if programs:
            if lights:raise NativeError('count_and_explicit_lamps_conflict')
            intents=[self.count_intent(programs[0])]
        tv_actions=parse_tv(televisions[0]) if televisions else []
        if not self.published():return self.lighting.legacy.action(rid,body)
        def formatted(row,result):
            ok=result.get('status')=='DONE'
            status={'status':'DONE'} if ok else {'status':'ERROR','error_code':result.get('error_code','DEVICE_UNREACHABLE')}
            return {'id':row['id'],'capabilities':[{'type':c['type'],'state':{'instance':c['state']['instance'],'action_result':dict(status)}} for c in row['capabilities']]}
        # Existing proven group works even when expanded features are paused.
        if len(rows)==1 and rows[0]['id']==GROUP_ID and len(intents)==1 and intents[0]['op']=='power':
            return self.lighting.legacy.action(rid,body)
        if not self.active():return {'request_id':rid,'payload':{'devices':[formatted(r,{'status':'ERROR'}) for r in rows]}}
        key='unified_'+hashlib.sha256(aid.encode()).hexdigest()
        results={}
        def perform_lights():
            # Use one planner for group/individual overlaps. Count uses same
            # durable ledger so incremental programs cannot repeat on retries.
            return self.lighting.engine.execute(key+'_lights',intents)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            light_future=pool.submit(perform_lights) if intents else None
            tv_future=pool.submit(self.tv.execute,key+'_tv',tv_actions) if tv_actions else None
            if light_future:
                outcome=light_future.result()
                for row in lights+programs:results[row['id']]=formatted(row,outcome)
            if tv_future:results[TV_ID]=formatted(televisions[0],tv_future.result())
        return {'request_id':rid,'payload':{'devices':[results[i] for i in ids]}}
