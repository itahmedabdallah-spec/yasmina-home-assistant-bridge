"""TV transport using the existing trusted HA tv_cmd helper with return_response.
Commands are built exclusively from a fixed catalogue and bounded numeric values.
One physical dispatch, followed by same-connection readback; no blind retries.
Never accepts free-form text, uploads, installs, shell substitutions or URLs.
"""
from __future__ import annotations
import dataclasses,http.client,json,re,shlex,threading,time,uuid
from pathlib import Path
from bundle_catalog import BundleError,CORE_APPS,TV_ENTITY,number
from bundle_tv import TVState,parse_power

POWER_READ='dumpsys power 2>/dev/null | grep -E "mWakefulness=|mScreenOn="; dumpsys display 2>/dev/null | grep "mScreenState="'
AUDIO_READ='dumpsys audio 2>/dev/null | sed -n "/^- STREAM_MUSIC:/,/^- STREAM_ALARM:/p"'
VOLUME_READ='cmd media_session volume --stream 3 --get'
FOCUS_READ='dumpsys window 2>/dev/null | grep mCurrentFocus; dumpsys activity activities 2>/dev/null | grep -E "mResumedActivity|topResumedActivity"'
SESSION_READ='dumpsys media_session 2>/dev/null | grep -E "package=|active=|state=PlaybackState"'
SESSION_READ += '; echo YASMINA_ACTIVE_AUDIO_START; dumpsys audio 2>/dev/null | sed -n "/^  players:/,/^  ducked players piids:/p" | grep AudioPlaybackConfiguration; echo YASMINA_ACTIVE_AUDIO_END; cmd package list packages -U | grep -E "^package:(net\\.mbc\\.shahidTV|com\\.google\\.android\\.youtube\\.tv|com\\.watchit\\.vod|com\\.yango\\.saft\\.tv) uid:[0-9]+$"'

KEYS={'on':224,'off':223,'pause':127,'resume':126,'home':3,'back':4,'next':87}
SAFE_APPS={k:v['package'] for k,v in CORE_APPS.items()}
LAUNCH_COMPONENTS={
 'youtube':'com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity',
 'shahid':'net.mbc.shahidTV/.MainActivity',
 'watchit':'com.watchit.vod/.refactor.splash.ui.tv.TvSplashActivity',
 'yango':'com.yango.saft.tv/ru.kinopoisk.tv.presentation.splash.SplashActivity'}

@dataclasses.dataclass(frozen=True)
class Probe:
    state:TVState
    nonce:str
    elapsed:float
    volume_min:int|None=None
    volume_max:int|None=None
    focus:str|None=None
    key_delivery_verified:bool=False
    requested_volume:int|None=None
    playback_evidence:str|None=None
    def safe(self):
        return {'power':self.state.power,'volume':self.state.volume,'muted':self.state.muted,
                'playback':self.state.playback,'app':self.state.app,'focus':self.focus,
                'elapsed_seconds':round(self.elapsed,3),'source':self.state.source,
                'volume_range':[self.volume_min,self.volume_max],'playback_evidence':self.playback_evidence}

def audio_muted(text):
    matches=re.findall(r'^\s*Muted:\s*(true|false)\s*$',text,re.M)
    if len(matches)!=1:return None
    return matches[0]=='true'

def parse_snapshot(raw,nonce,elapsed):
    if not isinstance(raw,str) or len(raw)>120000:raise BundleError('tv_invalid_response')
    if not re.fullmatch('[0-9a-f]{32}',nonce):raise BundleError('invalid_probe_identity')
    start='YASMINA_TV_'+nonce+'_START';end='YASMINA_TV_'+nonce+'_END'
    if raw.count(start)!=1 or raw.count(end)!=1 or raw.index(start)>=raw.index(end):raise BundleError('tv_response_not_current')
    text=raw.split(start,1)[1].split(end,1)[0]
    power=parse_power(text)
    if power is None:raise BundleError('tv_power_unknown')
    volumes=re.findall(r'\bvolume is (\d+) in range \[(\d+)\.\.(\d+)\]',text)
    volume=None;minimum=None;maximum=None
    if len(volumes)==1:
        v,minimum,maximum=map(int,volumes[0])
        if not 0<=minimum<maximum or not minimum<=v<=maximum:raise BundleError('invalid_tv_volume_reading')
        # getStreamVolume and vendor streamVolume become zero while muted.
        # The per-output Current map retains the selected output's stored index.
        active=re.findall(r'^\s*Devices:\s*([A-Za-z0-9_]+)\(([0-9a-f]+)\)\s*$',text,re.M)
        stored=None
        if len(active)==1:
            output_id=active[0][1]
            current=re.search(r'^\s*Current:\s*(.+)$',text,re.M)
            if current:
                indices=re.findall(r'(?:^|,\s*)'+re.escape(output_id)+r'\s*\([^)]+\):\s*(\d+)(?:,|$)',current.group(1))
                if len(indices)==1:stored=int(indices[0])
        if stored is not None:
            if not minimum<=stored<=maximum:raise BundleError('invalid_configured_volume')
            v=stored
        else:
            configured=re.findall(r'^\s*streamVolume:(\d+)\s*$',text,re.M)
            if len(configured)==1:
                stored=int(configured[0])
                if not minimum<=stored<=maximum:raise BundleError('invalid_configured_volume')
                v=stored
        volume=(v-minimum)/(maximum-minimum)*100
    focus=re.findall(r'mCurrentFocus=Window\{[^\n]*?\s([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)\}',text)
    app=focus[-1].split('/',1)[0] if focus else None
    resumed=re.findall(r'(?:mResumedActivity|topResumedActivity)[^\n]*?\s([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)',text)
    if resumed and len({x.split('/',1)[0] for x in resumed})==1:app=resumed[-1].split('/',1)[0]
    playback=None;current_package=None;active=False
    for line in text.splitlines():
        m=re.search(r'package=([A-Za-z0-9_.]+)',line)
        if m:current_package=m.group(1);active=False
        m=re.search(r'active=(true|false)',line)
        if m:active=m.group(1)=='true'
        m=re.search(r'state=PlaybackState\s*\{state=(?:[A-Z_]+\()?([0-9]+)\)?',line)
        if m and active and current_package==app:
            playback={'2':'paused','3':'playing'}.get(m.group(1))
    playback_evidence='active_media_session' if playback is not None else None
    if playback is None and power is True and app in SAFE_APPS.values():
        mappings=dict(re.findall(r'^package:([A-Za-z0-9_.]+) uid:(\d+)$',text,re.M))
        uid=mappings.get(app)
        if uid and text.count('YASMINA_ACTIVE_AUDIO_START')==1 and text.count('YASMINA_ACTIVE_AUDIO_END')==1:
            current=text.split('YASMINA_ACTIVE_AUDIO_START',1)[1].split('YASMINA_ACTIVE_AUDIO_END',1)[0]
            audio_states=[]
            for line in current.splitlines():
                match=re.search(r'AudioPlaybackConfiguration.*?u/pid:(\d+)/\d+ state:([a-z]+).*usage=USAGE_MEDIA',line)
                if match and match.group(1)==uid:audio_states.append(match.group(2))
            if 'started' in audio_states:playback='playing'
            elif audio_states and set(audio_states)=={'paused'}:playback='paused'
            if playback is not None:playback_evidence='foreground_app_audio_players'
    state=TVState(power,volume,audio_muted(text),playback,app,time.time(),'device_readback_via_existing_HA_helper')
    target=re.findall(r'YASMINA_TARGET_VOLUME=(\d+)',raw)
    requested=int(target[-1]) if len(target)==1 and 0<=int(target[0])<=100 else None
    return Probe(state,nonce,elapsed,minimum,maximum,focus[-1] if focus else None,
                 'YASMINA_KEY_DISPATCH_OK' in raw and 'YASMINA_KEY_DISPATCH_FAILED' not in raw,requested,playback_evidence)

def build_probe(nonce,action=None,value=None):
    if not isinstance(nonce,str) or not re.fullmatch('[0-9a-f]{32}',nonce):raise BundleError('invalid_probe_identity')
    before=''
    if action=='batch':
        if not isinstance(value,list) or not 1<=len(value)<=5:raise BundleError('invalid_tv_batch')
        seen=set();prefixes=[]
        for row in value:
            if not isinstance(row,dict) or set(row)!={'action','value'} or row['action'] in seen or row['action']=='batch':raise BundleError('invalid_tv_batch')
            seen.add(row['action'])
            part=build_probe(nonce,row['action'],row['value'])
            prefixes.append(part.split('echo YASMINA_TV_'+nonce+'_START;',1)[0].replace('sleep 0.35; ',''))
        if 'off' in seen and len(seen)>1:raise BundleError('conflicting_tv_batch')
        reads=build_probe(nonce,None,None)
        return ''.join(prefixes)+'sleep 0.35; '+reads
    if action is None:
        if value is not None:raise BundleError('invalid_probe_value')
    elif action in KEYS:
        if value is not None:raise BundleError('unexpected_tv_value')
        before='(input keyevent '+str(KEYS[action])+' && echo YASMINA_KEY_DISPATCH_OK) || echo YASMINA_KEY_DISPATCH_FAILED; '
    elif action=='volume':
        value=number(value,0,100,integer=True)
        before='cmd media_session volume --stream 3 --set '+str(value)+'; '
    elif action=='volume_relative':
        delta=number(value,-100,100,integer=True)
        before=('audio_state=$('+AUDIO_READ+'); '
          'output_id=$(printf "%s\n" "$audio_state" | sed -n "s/.*Devices: *[^(]*(\\([0-9a-f]*\\)).*/\\1/p"); '
          'case "$output_id" in ""|*[!0-9a-f]*) echo YASMINA_VOLUME_STATE_UNKNOWN;; *) '
          'stored_volume=$(printf "%s\n" "$audio_state" | sed -n "s/.*[ ,]$output_id ([^)]*): *\\([0-9][0-9]*\\).*/\\1/p"); '
          'case "$stored_volume" in ""|*[!0-9]*) echo YASMINA_VOLUME_STATE_UNKNOWN;; *) '
          'target_volume=$((stored_volume+('+str(delta)+'))); '
          '[ "$target_volume" -lt 0 ] && target_volume=0; [ "$target_volume" -gt 100 ] && target_volume=100; '
          'cmd media_session volume --stream 3 --set "$target_volume"; echo YASMINA_TARGET_VOLUME=$target_volume;; esac;; esac; ')
    elif action=='mute':
        if type(value) is not bool:raise BundleError('explicit_mute_required')
        desired='true' if value else 'false'
        # Toggle key is used only after checking real device state in this same
        # connection, and its result is read back; repeating desired mute is no-op.
        before='current_muted=$('+AUDIO_READ+' | sed -n "s/^ *Muted: *//p"); if [ "$current_muted" = "'+desired+'" ]; then :; elif [ "$current_muted" = "'+('false' if value else 'true')+'" ]; then input keyevent 164; else echo YASMINA_MUTE_STATE_UNKNOWN; fi; '
    elif action=='open_app':
        if value not in SAFE_APPS:raise BundleError('tv_app_not_approved')
        before='am start -n '+LAUNCH_COMPONENTS[value]+' -a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER; '
    else:raise BundleError('unsupported_tv_action')
    # Small settling allowance is only inside the same transaction, never a resend.
    if before:before+=('sleep 0.12; ' if action in ('on','off') else 'sleep 0.35; ')
    # Read only the evidence required for this action; a regular snapshot still
    # includes all fields. Avoid spending the native deadline on unrelated dumps.
    reads=POWER_READ
    if action is None or action in ('mute','volume','volume_relative'):
        reads+='; '+AUDIO_READ+'; '+VOLUME_READ
    if action is None or action in ('open_app','home','back','next','pause','resume'):
        reads+='; '+FOCUS_READ
    if action is None or action in ('pause','resume'):
        reads+='; '+SESSION_READ
    return before+'echo YASMINA_TV_'+nonce+'_START; '+reads+'; echo YASMINA_TV_'+nonce+'_END'

class FastTV:
    def __init__(self,token_path,*,read_authorized=lambda:False,write_authorized=lambda:False):
        self.path=Path(token_path);self.read_authorized=read_authorized;self.write_authorized=write_authorized
        self.lock=threading.Lock();self.physical_dispatches=0
    def transaction(self,action=None,value=None,timeout=4.0):
        if not self.read_authorized():raise BundleError('tv_read_authorization_pending')
        if action is not None and not self.write_authorized():raise BundleError('tv_write_authorization_pending')
        nonce=uuid.uuid4().hex;command=build_probe(nonce,action,value)
        p=self.path
        if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o077:raise BundleError('credential_file_invalid')
        token=p.read_text().strip()
        if not token.startswith('eyJ') or token.count('.')!=2:raise BundleError('credential_format_invalid')
        if not self.lock.acquire(timeout=.05):raise BundleError('tv_busy')
        start=time.monotonic();c=http.client.HTTPConnection('127.0.0.1',8123,timeout=timeout)
        try:
            if action is not None:self.physical_dispatches+=1
            c.request('POST','/api/services/shell_command/tv_cmd?return_response',
                body=json.dumps({'cmd':shlex.quote(command)}).encode(),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Connection':'close'})
            response=c.getresponse()
            if response.status!=200:raise BundleError('ha_http_'+str(response.status))
            raw=response.read(150000)
            if len(raw)>=150000:raise BundleError('tv_response_too_large')
            data=json.loads(raw);result=data.get('service_response',{})
            if result.get('returncode')!=0:raise BundleError('tv_helper_failed')
            text=result.get('stdout')
            if isinstance(text,str) and ('YASMINA_MUTE_STATE_UNKNOWN' in text or 'YASMINA_VOLUME_STATE_UNKNOWN' in text):raise BundleError('tv_audio_state_unknown')
            return parse_snapshot(text,nonce,time.monotonic()-start)
        except (OSError,http.client.HTTPException,json.JSONDecodeError):raise BundleError('tv_transport_uncertain_no_retry') from None
        finally:c.close();self.lock.release()
