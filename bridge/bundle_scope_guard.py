"""Per-request authorization boundary for the unified native provider.
This module is staging-only. Input claims MUST come from
ScopedOAuthStore.authenticate_claims(token), never an HTTP request body.
No account linking, token issuance, network calls or appliance execution.
"""
from bundle_catalog import DEVICE_TARGETS, TV_ID, LIGHT_PROGRAM_ID
from native_auth import OWNER

class DeviceScopeError(ValueError):
    pass

LIGHT_IDS=frozenset(DEVICE_TARGETS)|{LIGHT_PROGRAM_ID}
KNOWN_IDS=LIGHT_IDS|{TV_ID}

def verified_scopes(claims):
    if not isinstance(claims,dict) or claims.get('owner')!=OWNER:
        raise DeviceScopeError('authenticated_owner_required')
    scope=claims.get('scope')
    if scope not in ('home:lights','home:lights home:tv'):
        raise DeviceScopeError('unsupported_token_scope')
    declared=claims.get('scopes')
    if not isinstance(declared,(tuple,list)) or any(not isinstance(x,str) for x in declared):
        raise DeviceScopeError('invalid_verified_claims')
    if len(declared)!=len(set(declared)) or set(declared)!=set(scope.split()):
        raise DeviceScopeError('inconsistent_verified_claims')
    return frozenset(declared)

def authorize_request(claims,device_ids,*,tv_release_ready=False):
    """Prevalidate the entire batch before ANY part is dispatched.
    A linked full-scope account does not upgrade another lights-only token.
    A granted TV token does not bypass the separate release-readiness gate.
    """
    scopes=verified_scopes(claims)
    if not isinstance(device_ids,(list,tuple)) or not 1<=len(device_ids)<=len(KNOWN_IDS):
        raise DeviceScopeError('invalid_device_batch')
    if any(not isinstance(x,str) or x not in KNOWN_IDS for x in device_ids):
        raise DeviceScopeError('unknown_or_forbidden_device')
    if len(device_ids)!=len(set(device_ids)):
        raise DeviceScopeError('duplicate_device_id')
    if 'home:lights' not in scopes:
        raise DeviceScopeError('lighting_scope_required')
    if TV_ID in device_ids:
        if 'home:tv' not in scopes:raise DeviceScopeError('television_scope_required')
        if tv_release_ready is not True:raise DeviceScopeError('television_release_not_ready')
    return tuple(device_ids)

def visible_device_ids(claims,*,lighting_expansion_ready=False,tv_release_ready=False):
    scopes=verified_scopes(claims)
    from bundle_catalog import GROUP_ID,BULB_IDS
    result=[GROUP_ID]
    if lighting_expansion_ready is True:result.extend(BULB_IDS)
    if tv_release_ready is True and 'home:tv' in scopes:result.append(TV_ID)
    return tuple(result)
