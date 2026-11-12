"""Restricted light attribute transport; reuses the existing HA credential.
No generic service endpoint, external network, script/shell execution or TV scope.
"""
from __future__ import annotations
import concurrent.futures,http.client,json
from core import HomeAssistant,ControlError
from bundle_catalog import GROUP,MEMBERS,LampState,BundleError,targets,number

class LightingHA(HomeAssistant):
    def __init__(self,token_path,*,writes_enabled=lambda:False):
        super().__init__(token_path,request_timeout=.3,write_timeout=.9)
        self.writes_enabled=writes_enabled
    def membership(self):
        group=self.read_state(GROUP)
        members=group.get('attributes',{}).get('entity_id',[])
        if not isinstance(members,list) or len(members)!=6 or set(members)!=set(MEMBERS):
            raise BundleError('ha_group_members_changed')
        return group
    def snapshot(self):
        # Group changes must fail closed, but the group aggregate is not a seventh lamp.
        self.membership()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            records=list(pool.map(self.read_state,MEMBERS))
        result={}
        for row in records:
            state=row.get('state');a=row.get('attributes',{})
            if state not in ('on','off'):
                result[row['entity_id']]=LampState(None,None,None)
                continue
            b=a.get('brightness');k=a.get('color_temp_kelvin')
            brightness=None if b is None else number(b,0,255)/255*100
            kelvin=None if k is None else number(k,2700,6500,integer=True)
            result[row['entity_id']]=LampState(state=='on',brightness,kelvin)
        return result
    def apply(self,entities,change):
        if not self.writes_enabled():raise BundleError('physical_execution_locked')
        entities=targets(entities)
        if not isinstance(change,dict) or not change or set(change)-{'power','brightness','kelvin'}:
            raise BundleError('invalid_write_fields')
        power=change.get('power')
        if type(power) is not bool:raise BundleError('explicit_power_required')
        if power is False and set(change)!={'power'}:raise BundleError('invalid_write_fields')
        payload={'entity_id':list(entities)}
        if 'brightness' in change:payload['brightness_pct']=number(change['brightness'],1,100)
        if 'kelvin' in change:payload['color_temp_kelvin']=number(change['kelvin'],2700,6500,integer=True)
        try:self.membership()
        except ControlError as exc:raise BundleError(str(exc)) from None
        path='/api/services/light/'+('turn_on' if power else 'turn_off')
        p=self.token_path
        if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o077:raise BundleError('credential_file_invalid')
        token=p.read_text().strip()
        if not token.startswith('eyJ') or token.count('.')!=2:raise BundleError('credential_format_invalid')
        connection=http.client.HTTPConnection('127.0.0.1',8123,timeout=.9)
        try:
            connection.request('POST',path,body=json.dumps(payload).encode(),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Connection':'close'})
            response=connection.getresponse()
            if not 200<=response.status<300:raise BundleError('ha_http_'+str(response.status))
            raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise BundleError('ha_response_too_large')
            return json.loads(raw)
        except (OSError,ValueError,http.client.HTTPException) as exc:
            if isinstance(exc,BundleError):raise
            raise BundleError('ha_transport_error') from None
        finally:connection.close()
