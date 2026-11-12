"""TV contracts and corrected read parsing. No ADB execution on import.
Transport and installed-app evidence are injected. No arbitrary shell command.
TV remains separately gated from the existing home:lights authorization.
"""
from __future__ import annotations
import dataclasses,re,time
from bundle_catalog import BundleError,number,CORE_APPS,TV_ENTITY

@dataclasses.dataclass(frozen=True)
class TVState:
    power: bool | None
    volume: float | None = None
    muted: bool | None = None
    playback: str | None = None
    app: str | None = None
    observed_at: float = 0.0
    source: str = 'read_only_device_diagnostics'

def parse_power(text):
    if not isinstance(text,str) or len(text)>100000:raise BundleError('invalid_power_reading')
    # Match semantic fields, never character positions affected by stderr noise.
    awake=re.findall(r'\bmWakefulness\s*=\s*(Awake|Asleep|Dozing|Dreaming)\b',text)
    screens=re.findall(r'\bmScreenState\s*=\s*(ON|OFF|DOZE|DOZE_SUSPEND)\b',text)
    screens+=re.findall(r'Display Power:\s*state=(ON|OFF|DOZE|DOZE_SUSPEND)\b',text)
    legacy=re.findall(r'\bmScreenOn\s*=\s*(true|false)\b',text)
    if len(set(awake))>1 or len(set(screens))>1 or len(set(legacy))>1:
        raise BundleError('conflicting_power_readings')
    screen=None
    if screens:screen=screens[-1]=='ON'
    elif legacy:screen=legacy[-1]=='true'
    wake=None if not awake else awake[-1] in ('Awake','Dreaming')
    if screen is not None:
        if screen and wake is False:raise BundleError('conflicting_power_readings')
        return screen
    # Screen unreported is not a confirmed power reading.
    return None

def tv_plan(action,value=None,*,installed=(),before:TVState|None=None):
    if action not in ('on','off','pause','resume','volume','volume_step','mute','home','back','next','open_app'):
        raise BundleError('unsupported_tv_action')
    if action in ('on','off','pause','resume','home','back','next') and value is not None:
        raise BundleError('unexpected_tv_value')
    if action=='mute' and type(value) is not bool:raise BundleError('explicit_mute_required')
    if action=='volume':value=number(value,0,100)
    if action=='volume_step':
        value=number(value,-100,100)
        if before is None or before.volume is None:raise BundleError('current_volume_required')
        value=max(0,min(100,before.volume+value));action='volume'
    if action=='open_app':
        if value not in CORE_APPS:raise BundleError('app_not_approved')
        if CORE_APPS[value]['package'] not in installed:raise BundleError('app_installation_not_verified')
    command={'target':TV_ENTITY,'action':action,'value':value,'repeat_allowed':False}
    if action in ('on','off'):command['service']='media_player.turn_'+action
    elif action=='pause':command['service']='media_player.media_pause'
    elif action=='resume':command['service']='media_player.media_play'
    elif action=='volume':command.update(service='media_player.volume_set',volume_level=value/100)
    elif action=='mute':command.update(service='media_player.volume_mute',is_volume_muted=value)
    elif action=='open_app':command.update(service='media_player.select_source',source=CORE_APPS[value]['package'])
    else:
        # Exact key names; HOME is never sent as an unrecognized shell executable.
        command.update(service='androidtv.adb_command',command={'home':'HOME','back':'BACK','next':'MEDIA_NEXT'}[action])
    return command

def tv_verified(command,state):
    if not isinstance(state,TVState):return False
    action=command['action'];value=command['value']
    if action=='on':return state.power is True
    if action=='off':return state.power is False
    if action=='mute':return state.muted is value
    if action=='volume':return state.volume is not None and abs(state.volume-value)<=1
    if action=='pause':return state.playback=='paused'
    if action=='resume':return state.playback=='playing'
    if action=='open_app':return state.app==command['source'] and state.power is True
    # Navigation cannot be certified just from an HTTP acknowledgement.
    return False

class TVExecutor:
    def __init__(self,transport,ledger,installed=(),enabled=lambda:False,clock=time.time):
        self.transport=transport;self.ledger=ledger;self.installed=tuple(installed)
        self.enabled=enabled;self.clock=clock
    def execute(self,identity,action,value=None):
        if not self.enabled():return {'status':'BLOCKED','reason':'tv_scope_or_acceptance_pending','physical_writes':0}
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,160}',identity):raise BundleError('invalid_request_id')
        try:before=self.transport.snapshot()
        except Exception:return {'status':'ERROR','reason':'tv_read_failed','physical_writes':0}
        if not isinstance(before,TVState) or not 0<=self.clock()-before.observed_at<=3:
            return {'status':'ERROR','reason':'tv_state_not_fresh','physical_writes':0}
        command=tv_plan(action,value,installed=self.installed,before=before)
        claimed,old=self.ledger.claim(identity,command)
        if not claimed:
            import json
            return json.loads(old['response']) if old.get('response') else {'status':'ERROR','reason':'in_progress_no_retry'}
        def finish(result):self.ledger.finish(identity,result);return result
        if tv_verified(command,before):return finish({'status':'DONE','reason':'already_satisfied','physical_writes':0})
        self.ledger.phase(identity,'dispatching',write=True)
        try:self.transport.apply(command)
        except Exception as exc:
            if str(exc) in ('ha_http_401','ha_http_403','tv_authorization_pending','tv_command_not_allowed'):
                return finish({'status':'ERROR','reason':'tv_dispatch_rejected','physical_writes':1})
        for _ in range(3):
            try:after=self.transport.snapshot()
            except Exception:continue
            if isinstance(after,TVState) and after.observed_at>=before.observed_at and tv_verified(command,after):
                return finish({'status':'DONE','reason':'tv_result_verified','physical_writes':1})
        return finish({'status':'ERROR','reason':'tv_result_unverified_no_retry','physical_writes':1})
