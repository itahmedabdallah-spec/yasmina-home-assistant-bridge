"""Human-readable statuses for new typed command journals; legacy untouched."""
import json
from native_provider import NativeLedger
class BundleLedger(NativeLedger):
 def recent(self,limit=10):
  with self.db() as db:
   rows=db.execute('SELECT id,phase,response,created,write_attempts,diagnostics FROM requests ORDER BY created DESC LIMIT ?',(max(1,min(int(limit),30)),)).fetchall()
  result=[]
  for row in rows:
   response=json.loads(row['response']) if row['response'] else {}
   result.append({'request_id':row['id'],'phase':row['phase'],'created_epoch':row['created'],
    'physical_write_attempts':row['write_attempts'],'result':{'status':response.get('status','PENDING'),'reason':response.get('reason'),'error_code':response.get('error_code')},
    'diagnostics':json.loads(row['diagnostics']) if row['diagnostics'] else None,
    'source':'authenticated_native_command','voice_origin_not_distinguishable_from_app':True})
  return result
