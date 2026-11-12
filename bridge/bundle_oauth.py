"""Backward-compatible scoped authorization for the approved unified package.
Existing home:lights tokens remain lights-only. New TV access requires an actual
owner authorization-code flow; refresh never silently increases token scope.
"""
from __future__ import annotations
import hashlib,hmac,json,os,pathlib,secrets,time
from native_auth import OAuthStore,OAuthError,CLIENT_ID,REDIRECT,OWNER,digest,valid_secret
LIGHT_SCOPE='home:lights'
FULL_SCOPE='home:lights home:tv'
ALLOWED_SCOPES=(LIGHT_SCOPE,FULL_SCOPE)

def canonical_scope(scope):
    if not isinstance(scope,str):raise OAuthError('invalid_scope')
    parts=scope.split()
    if len(parts)!=len(set(parts)):raise OAuthError('invalid_scope')
    if set(parts)=={LIGHT_SCOPE}:return LIGHT_SCOPE
    if set(parts)=={'home:lights','home:tv'}:return FULL_SCOPE
    raise OAuthError('invalid_scope')

class ScopedOAuthStore(OAuthStore):
    def begin(self,client_id,redirect_uri,response_type,state,scope=LIGHT_SCOPE):
        scope=canonical_scope(scope)
        if client_id!=CLIENT_ID or redirect_uri!=REDIRECT or response_type!='code':raise OAuthError('invalid_request')
        if not isinstance(state,str) or not 1<=len(state)<=4096:raise OAuthError('invalid_state')
        sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(self.clock(),))
            if db.execute('SELECT count(*) FROM sessions').fetchone()[0]>=100:raise OAuthError('temporarily_unavailable')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)',
                (digest(sid),digest(csrf),client_id,redirect_uri,scope,state,self.clock()+600))
        return sid,csrf
    def provision_scope_grant(self,scope=FULL_SCOPE):
        """Prepare a one-use owner code, not a grant or token. No secret output."""
        scope=canonical_scope(scope)
        if scope!=FULL_SCOPE:raise OAuthError('invalid_scope')
        path=self.root/'bundle_scope_registration.json'
        with self.db() as db:
            conf=dict(db.execute('SELECT key,value FROM config'))
            if conf.get('bundle_code_used')=='0' and float(conf.get('bundle_code_expires','0'))>self.clock() and path.is_file():
                return {'created':False,'file_exists':True,'scope':scope}
            code=secrets.token_urlsafe(28);expires=self.clock()+3600
            for key,value in [('bundle_code_hash',digest(code)),('bundle_code_expires',str(expires)),('bundle_code_used','0'),('bundle_code_scope',scope)]:
                db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',(key,value))
            data={'client_id':CLIENT_ID,'redirect_uri':REDIRECT,'scope':scope,'link_code':code,'expires_epoch':expires}
            tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(data,f)
            tmp.replace(path)
        return {'created':True,'file_exists':True,'scope':scope}
    def approve(self,sid,csrf,link_code):
        if not all(isinstance(v,str) for v in (sid,csrf,link_code)):raise OAuthError('access_denied')
        with self.db() as db:
            session=db.execute('SELECT * FROM sessions WHERE hash=?',(digest(sid),)).fetchone()
            if not session or session['expires']<self.clock() or not hmac.compare_digest(session['csrf'],digest(csrf)):raise OAuthError('access_denied')
            scope=canonical_scope(session['scope']);prefix='bundle_code' if scope==FULL_SCOPE else 'link_code'
            conf=dict(db.execute('SELECT key,value FROM config'))
            if conf.get(prefix+'_used')!='0' or self.clock()>float(conf.get(prefix+'_expires','0')) or not hmac.compare_digest(conf.get(prefix+'_hash',''),digest(link_code)):
                raise OAuthError('access_denied')
            if scope==FULL_SCOPE and conf.get('bundle_code_scope')!=FULL_SCOPE:raise OAuthError('access_denied')
            code=secrets.token_urlsafe(32)
            db.execute('INSERT INTO codes VALUES (?,?,?,?,?,0)',(digest(code),session['client'],session['redirect'],scope,self.clock()+120))
            db.execute('UPDATE config SET value=? WHERE key=?',('1',prefix+'_used'))
            db.execute('DELETE FROM sessions WHERE hash=?',(digest(sid),))
            return {'code':code,'state':session['state'],'client_id':session['client'],'scope':scope,'redirect_uri':session['redirect']}
    def exchange(self,grant,client_id,client_secret,code=None,redirect_uri=None,refresh_token=None):
        with self.db() as db:
            self._check_client(db,client_id,client_secret)
            if grant=='authorization_code':
                if not valid_secret(code) or redirect_uri!=REDIRECT:raise OAuthError('invalid_grant')
                row=db.execute('SELECT * FROM codes WHERE hash=?',(digest(code),)).fetchone()
                if not row or row['used'] or row['expires']<self.clock() or row['client']!=client_id or row['redirect']!=redirect_uri:raise OAuthError('invalid_grant')
                db.execute('UPDATE codes SET used=1 WHERE hash=?',(digest(code),))
            elif grant=='refresh_token':
                if not valid_secret(refresh_token):raise OAuthError('invalid_grant')
                row=db.execute("SELECT * FROM tokens WHERE hash=? AND kind='refresh'",(digest(refresh_token),)).fetchone()
                if not row or row['revoked'] or row['expires']<self.clock() or row['client']!=client_id:raise OAuthError('invalid_grant')
                db.execute('UPDATE tokens SET revoked=1 WHERE hash=?',(digest(refresh_token),))
            else:raise OAuthError('unsupported_grant_type')
            scope=canonical_scope(row['scope'])
            access=secrets.token_urlsafe(36);refresh=secrets.token_urlsafe(36)
            db.executemany('INSERT INTO tokens VALUES (?,?,?,?,?,?,0)',[
                (digest(access),'access',client_id,OWNER,scope,self.clock()+3600),
                (digest(refresh),'refresh',client_id,OWNER,scope,self.clock()+86400*30)])
            return {'access_token':access,'token_type':'Bearer','expires_in':3600,'refresh_token':refresh,'scope':scope}
    def authenticate_claims(self,token):
        if not valid_secret(token):raise OAuthError('invalid_token')
        with self.db() as db:row=db.execute("SELECT * FROM tokens WHERE hash=? AND kind='access'",(digest(token),)).fetchone()
        if not row or row['revoked'] or row['expires']<self.clock() or row['owner']!=OWNER or row['scope'] not in ALLOWED_SCOPES:raise OAuthError('invalid_token')
        return {'owner':OWNER,'scope':row['scope'],'scopes':tuple(row['scope'].split())}
    def authenticate(self,token):return self.authenticate_claims(token)['owner']
    def public_status(self):
        with self.db() as db:
            rows=db.execute("SELECT kind,scope,count(*) AS count FROM tokens WHERE revoked=0 AND expires>? GROUP BY kind,scope",(self.clock(),)).fetchall()
        return {'account_linked':bool(rows),'valid_access_tokens':sum(r['count'] for r in rows if r['kind']=='access'),
                'client_id':CLIENT_ID,'supported_scopes':list(ALLOWED_SCOPES),
                'active_scopes':sorted(set(r['scope'] for r in rows)),
                'tv_scope_linked':any(r['scope']==FULL_SCOPE for r in rows),'credential_values_exposed':False}
