import importlib.util
import unittest
from pathlib import Path
p=Path(__file__).resolve().parents[2]/'backend/internal/ai_runtime/ai_fraud_detection.py'
s=importlib.util.spec_from_file_location('policy',p);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)

class PolicyTests(unittest.TestCase):
    def check(self, context, score, expected, confidence=.95, review=.4, escalate=.85):
        result=d.assess({'is_fraud':{'noul':score},'context_status':{'choice':context,'answer_confidence':confidence}},
                        {'threshold_review':review,'threshold_escalate':escalate})
        self.assertEqual(result['level'],expected)
    def test_report_never_accuses_speaker(self): self.check('laporan_kejadian',.99,'PERLU_DICEK')
    def test_unknown_never_safe(self): self.check('konteks_kurang',.01,'PERLU_DICEK')
    def test_disagreement(self): self.check('layanan_normal',.99,'PERLU_DICEK')
    def test_uncertain(self): self.check('layanan_normal',.01,'PERLU_DICEK',confidence=.3)
    def test_active(self): self.check('risiko_aktif',.99,'ESKALASI')
    def test_no_escalation_threshold(self): self.check('risiko_aktif',.99,'PERLU_DICEK',escalate=None)
    def test_normal(self): self.check('layanan_normal',.01,'TIDAK_ADA_INDIKASI')
    def test_nan(self):
        with self.assertRaises(ValueError): self.check('layanan_normal',float('nan'),'TIDAK_ADA_INDIKASI')

if __name__=='__main__': unittest.main()
