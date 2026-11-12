"""Versioned, environment-configurable smart-home catalogue."""
from __future__ import annotations
import dataclasses, math, os

def _env(name, default):
    value=os.environ.get(name,default).strip()
    if not value: raise RuntimeError(f'{name}_empty')
    return value

BUNDLE_VERSION='1.0.0'
DEVICE_PREFIX=_env('YASMINA_DEVICE_PREFIX','yasmina-home')
GROUP=_env('YASMINA_LIGHT_GROUP_ENTITY','light.living_room')
MEMBERS=tuple(x.strip() for x in _env(
    'YASMINA_LIGHT_ENTITIES',
    'light.living_room_1,light.living_room_2,light.living_room_3,light.living_room_4,light.living_room_5,light.living_room_6'
).split(',') if x.strip())
if not 1 <= len(MEMBERS) <= 6 or len(set(MEMBERS)) != len(MEMBERS):
    raise RuntimeError('YASMINA_LIGHT_ENTITIES_must_contain_1_to_6_unique_entities')

GROUP_ID=f'{DEVICE_PREFIX}-lights-v1'
BULB_IDS=tuple(f'{DEVICE_PREFIX}-bulb-{i}-v1' for i in range(1,len(MEMBERS)+1))
DEVICE_TARGETS={GROUP_ID:MEMBERS, **{did:(eid,) for did,eid in zip(BULB_IDS,MEMBERS)}}
GROUP_NAME=_env('YASMINA_LIGHT_GROUP_NAME','Living room lights')
BULB_NAME_PREFIX=_env('YASMINA_BULB_NAME_PREFIX','Living room bulb')
NAMES={GROUP_ID:GROUP_NAME, **{did:f'{BULB_NAME_PREFIX} {i}' for i,did in enumerate(BULB_IDS,1)}}

TV_ENTITY=_env('YASMINA_TV_ENTITY','media_player.android_tv')
TV_ID=f'{DEVICE_PREFIX}-tv-v1'
CORE_APPS={
 'youtube':{'name':'YouTube','package':_env('YASMINA_APP_YOUTUBE_PACKAGE','com.google.android.youtube.tv')},
 'shahid':{'name':'Shahid','package':_env('YASMINA_APP_SHAHID_PACKAGE','net.mbc.shahidTV')},
 'watchit':{'name':'Watch It','package':_env('YASMINA_APP_WATCHIT_PACKAGE','com.watchit.vod')},
 'yango':{'name':'Yango Play','package':_env('YASMINA_APP_YANGO_PACKAGE','com.yango.saft.tv')},
}
OPTIONAL_APPS={
 'tod':'com.todtv.tod','disney':'com.disney.disneyplus','osn':'com.osn.go',
 'ytmusic':'com.google.android.youtube.tvmusic','starz':'com.parsifal.starz',
 'appletv':'com.apple.atve.androidtv.appletv','jawwy':'com.intigral.jawwytv',
 'plex':'com.plexapp.android','iqiyi':'com.iqiyi.i18n.tv'}
EXCLUDED_DEFAULT_APPS={'netflix':'com.netflix.ninja'}

class BundleError(ValueError):
    """Only short, fixed error codes should enter logs."""

def number(value, minimum, maximum, *, integer=False):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise BundleError('invalid_number')
    if not minimum <= value <= maximum or (integer and int(value)!=value):
        raise BundleError('number_out_of_range')
    return int(value) if integer else float(value)

def targets(value):
    if not isinstance(value,(tuple,list)) or not value or len(value)>len(MEMBERS):
        raise BundleError('invalid_targets')
    if any(not isinstance(e,str) or e not in MEMBERS for e in value):
        raise BundleError('target_outside_allowlist')
    if len(set(value))!=len(value): raise BundleError('duplicate_target')
    return tuple(e for e in MEMBERS if e in value)

@dataclasses.dataclass(frozen=True)
class LampState:
    power: bool | None
    brightness: float | None = None
    kelvin: int | None = None
    def __post_init__(self):
        if self.power is not None and type(self.power) is not bool: raise BundleError('invalid_lamp_state')
        if self.brightness is not None: number(self.brightness,0,100)
        if self.kelvin is not None: number(self.kelvin,2700,6500,integer=True)

def check_snapshot(snapshot):
    if not isinstance(snapshot,dict) or set(snapshot)!=set(MEMBERS):
        raise BundleError('member_snapshot_required')
    if not all(isinstance(s,LampState) for s in snapshot.values()):
        raise BundleError('invalid_snapshot')
    return snapshot

LIGHT_PROGRAM_ID=f'{DEVICE_PREFIX}-light-programs-v1'
LIGHT_PROGRAMS={'one':('count',1),'two':('count',2),'three':('count',3),'four':('count',4),'five':('count',5),'six':('count',6),'seven':('add_count',1),'eight':('add_count',-1)}
TV_PROGRAMS={'one':'wake','two':'youtube','three':'shahid','four':'watchit','five':'yango'}
