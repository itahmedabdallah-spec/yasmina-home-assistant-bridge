"""Single-owner OAuth 2.0 authorization-code provider for private Yango linking.
Separate credentials from HA/Yandex. Codes are one-use and bound to the exact
registered client, callback and scope. Access/refresh tokens are stored hashed.
"""
from __future__ import annotations
import contextlib,hashlib,hmac,json,os,pathlib,secrets,sqlite3,time

CLIENT_ID=os.environ.get('YASMINA_OAUTH_CLIENT_ID','yasmina-home-native').strip()
REDIRECT=os.environ.get('YASMINA_OAUTH_REDIRECT','https://social.yango.com/broker/redirect').strip()
SCOPE=os.environ.get('YASMINA_OAUTH_SCOPE','home:lights').strip()
OWNER=os.environ.get('YASMINA_OWNER_ID','local-owner').strip()

class OAuthError(ValueError):
    pass

def digest(value:str)->str:
    return hashlib.sha256(value.encode('utf8')).hexdigest()

def valid_secret(value):
    return isinstance(value,str) and 20<=len(value)<=2048

class OAuthStore:
    def __init__(self,root:pathlib.Path,clock=time.time):
        self.root=pathlib.Path(root);self.clock=clock
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'native_oauth.sqlite3'
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS codes (hash TEXT PRIMARY KEY,client TEXT,redirect TEXT,scope TEXT,expires REAL,used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY,csrf TEXT,client TEXT,redirect TEXT,scope TEXT,state TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS tokens (hash TEXT PRIMARY KEY,kind TEXT,client TEXT,owner TEXT,scope TEXT,expires REAL,revoked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS limiter (bucket TEXT PRIMARY KEY,window REAL,count INTEGER);
            ''')
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=1,isolation_level=None)
        conn.row_factory=sqlite3.Row
        try:
            conn.execute('BEGIN IMMEDIATE');yield conn;conn.commit()
        except Exception:
            conn.rollback();raise
        finally:conn.close()

    def provision(self):
        """Run locally once. Never prints material; existing provisioning is reused."""
        path=self.root/'native_registration.json'
        with self.db() as db:
            if db.execute("SELECT 1 FROM config WHERE key='client_secret_hash'").fetchone():
                return {'created':False,'registration_file_exists':path.is_file()}
            client_secret=secrets.token_urlsafe(36);link_code=secrets.token_urlsafe(24)
            db.executemany('INSERT INTO config VALUES (?,?)',[
                ('client_secret_hash',digest(client_secret)),('link_code_hash',digest(link_code)),
                ('link_code_expires',str(self.clock()+3600)),('link_code_used','0')])
            data={'client_id':CLIENT_ID,'client_secret':client_secret,'link_code':link_code,
                  'redirect_uri':REDIRECT,'scope':SCOPE,'expires_epoch':self.clock()+3600}
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(data,f)
        return {'created':True,'registration_file_exists':True}

    def _check_client(self,db,client_id,client_secret):
        row=db.execute("SELECT value FROM config WHERE key='client_secret_hash'").fetchone()
        if client_id!=CLIENT_ID or not valid_secret(client_secret) or not row or not hmac.compare_digest(row[0],digest(client_secret)):
            raise OAuthError('invalid_client')

    def begin(self,client_id,redirect_uri,response_type,state,scope=SCOPE):
        if client_id!=CLIENT_ID or redirect_uri!=REDIRECT or response_type!='code' or scope!=SCOPE:
            raise OAuthError('invalid_request')
        if not isinstance(state,str) or not 1<=len(state)<=4096:raise OAuthError('invalid_state')
        sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(self.clock(),))
            if db.execute('SELECT count(*) FROM sessions').fetchone()[0]>=100:raise OAuthError('temporarily_unavailable')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)',
                (digest(sid),digest(csrf),client_id,redirect_uri,scope,state,self.clock()+600))
        return sid,csrf

    def approve(self,sid,csrf,link_code):
        if not all(isinstance(v,str) for v in (sid,csrf,link_code)):raise OAuthError('access_denied')
        with self.db() as db:
            session=db.execute('SELECT * FROM sessions WHERE hash=?',(digest(sid),)).fetchone()
            if not session or session['expires']<self.clock() or not hmac.compare_digest(session['csrf'],digest(csrf)):
                raise OAuthError('access_denied')
            conf=dict(db.execute('SELECT key,value FROM config'))
            if conf.get('link_code_used')!='0' or self.clock()>float(conf.get('link_code_expires','0')) or not hmac.compare_digest(conf.get('link_code_hash',''),digest(link_code)):
                raise OAuthError('access_denied')
            code=secrets.token_urlsafe(32)
            db.execute('INSERT INTO codes VALUES (?,?,?,?,?,0)',(digest(code),session['client'],session['redirect'],session['scope'],self.clock()+120))
            db.execute("UPDATE config SET value='1' WHERE key='link_code_used'")
            db.execute('DELETE FROM sessions WHERE hash=?',(digest(sid),))
            return {'code':code,'state':session['state'],'client_id':session['client'],'scope':session['scope'],'redirect_uri':session['redirect']}

    def exchange(self,grant,client_id,client_secret,code=None,redirect_uri=None,refresh_token=None):
        with self.db() as db:
            self._check_client(db,client_id,client_secret)
            if grant=='authorization_code':
                if not valid_secret(code) or redirect_uri!=REDIRECT:raise OAuthError('invalid_grant')
                row=db.execute('SELECT * FROM codes WHERE hash=?',(digest(code),)).fetchone()
                if not row or row['used'] or row['expires']<self.clock() or row['client']!=client_id or row['redirect']!=redirect_uri:
                    raise OAuthError('invalid_grant')
                db.execute('UPDATE codes SET used=1 WHERE hash=?',(digest(code),))
            elif grant=='refresh_token':
                if not valid_secret(refresh_token):raise OAuthError('invalid_grant')
                row=db.execute("SELECT * FROM tokens WHERE hash=? AND kind='refresh'",(digest(refresh_token),)).fetchone()
                if not row or row['revoked'] or row['expires']<self.clock() or row['client']!=client_id:raise OAuthError('invalid_grant')
                db.execute('UPDATE tokens SET revoked=1 WHERE hash=?',(digest(refresh_token),))
            else:raise OAuthError('unsupported_grant_type')
            access=secrets.token_urlsafe(36);refresh=secrets.token_urlsafe(36)
            db.executemany('INSERT INTO tokens VALUES (?,?,?,?,?,?,0)',[
                (digest(access),'access',client_id,OWNER,SCOPE,self.clock()+3600),
                (digest(refresh),'refresh',client_id,OWNER,SCOPE,self.clock()+86400*30)])
            return {'access_token':access,'token_type':'Bearer','expires_in':3600,'refresh_token':refresh,'scope':SCOPE}

    def authenticate(self,token):
        if not valid_secret(token):raise OAuthError('invalid_token')
        with self.db() as db:
            row=db.execute("SELECT * FROM tokens WHERE hash=? AND kind='access'",(digest(token),)).fetchone()
        if not row or row['revoked'] or row['expires']<self.clock() or row['owner']!=OWNER or row['scope']!=SCOPE:
            raise OAuthError('invalid_token')
        return OWNER

    def unlink(self,token):
        self.authenticate(token)
        with self.db() as db:db.execute('UPDATE tokens SET revoked=1 WHERE owner=?',(OWNER,))

    def allow_attempt(self,bucket,limit=10,seconds=60):
        with self.db() as db:
            row=db.execute('SELECT * FROM limiter WHERE bucket=?',(bucket,)).fetchone()
            if not row or self.clock()-row['window']>=seconds:
                db.execute('INSERT OR REPLACE INTO limiter VALUES (?,?,1)',(bucket,self.clock()));return True
            if row['count']>=limit:return False
            db.execute('UPDATE limiter SET count=count+1 WHERE bucket=?',(bucket,));return True

    def public_status(self):
        with self.db() as db:
            count=db.execute("SELECT count(*) FROM tokens WHERE kind='access' AND revoked=0 AND expires>?",(self.clock(),)).fetchone()[0]
            linked=db.execute("SELECT count(*) FROM tokens WHERE revoked=0 AND expires>?",(self.clock(),)).fetchone()[0]
        return {'account_linked':bool(linked),'valid_access_tokens':count,'client_id':CLIENT_ID,
                'scope':SCOPE,'credential_values_exposed':False}
