import re,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TEXT_EXT={'.py','.md','.json','.yaml','.yml','.txt','.example'}
class NoPrivateMaterial(unittest.TestCase):
 def test_no_known_private_material(self):
  text='\n'.join(p.read_text(errors='ignore') for p in ROOT.rglob('*')
                   if p.is_file() and p.name!='test_no_private_material.py'
                   and (p.suffix in TEXT_EXT or p.name.startswith('.env')))
  forbidden=[
   r'192\.168\.10\.', r'yasmina\.aima\.surf', r'light\.wiz_tunable_white_eb',
   r'ae64d59d-b2c5-4d38-9d1b-6d27e53c5d73', r'b852e05fcb63',
   r'/home/aimaops/', r'bf26ee19-c8fd-4c61-8d64-b7b7147f6468'
  ]
  for pattern in forbidden:
   self.assertIsNone(re.search(pattern,text,re.I),pattern)
if __name__=='__main__': unittest.main()
