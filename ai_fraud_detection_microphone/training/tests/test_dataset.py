import importlib.util
import unittest
from pathlib import Path

p=Path(__file__).resolve().parents[1]/'scripts/gen_fraud_dataset_v3.py'
s=importlib.util.spec_from_file_location('dataset',p)
d=importlib.util.module_from_spec(s);s.loader.exec_module(d)

class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.rows=d.build()
    def test_reproducible(self): self.assertEqual(self.rows,d.build())
    def test_disjoint(self):
        for a,aa in self.rows.items():
            for b,bb in self.rows.items():
                if a<b:
                    self.assertFalse({d.normalize(r['state']) for r in aa}&{d.normalize(r['state']) for r in bb})
                    self.assertFalse({r['metadata']['family_id'] for r in aa}&{r['metadata']['family_id'] for r in bb})
    def test_targets(self):
        for rows in self.rows.values():
            for row in rows:
                e=row['expected']; status=e['context_status']
                self.assertIn(status,d.QUESTIONS['context_status']['criteria'])
                if status=='konteks_kurang': self.assertNotIn('is_fraud',e)
                else: self.assertEqual(e['is_fraud'],status=='risiko_aktif')
    def test_size_and_coverage(self):
        self.assertEqual({k:len(v) for k,v in self.rows.items()},dict(train=1200,calibration=240,validation=400,test=80))
        for rows in self.rows.values():
            self.assertEqual({r['expected']['context_status'] for r in rows},set(d.QUESTIONS['context_status']['criteria']))

if __name__=='__main__': unittest.main()
