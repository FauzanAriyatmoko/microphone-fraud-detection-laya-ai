import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.internal.ai_runtime.ai_fraud_detection import assess,resolve_policy
from backend.internal.utils.config_loader import load_config

class BinaryPolicyTests(unittest.TestCase):
    def result(self,score,**context):
        return assess({'is_fraud':{'noul':score},'context_status':context},
            {'decision_mode':'binary','threshold_fraud':.5})
    def test_boundary(self):
        for score,expected in [(0,False),(.4999,False),(.5,True),(1,True)]:
            with self.subTest(score=score):
                result=self.result(score)
                self.assertEqual(result['is_fraud'],expected)
                self.assertEqual(result['level'],'FRAUD' if expected else 'TIDAK_FRAUD')
    def test_context_and_confidence_do_not_override_binary_score(self):
        for choice in ['risiko_aktif','layanan_normal','laporan_kejadian','konteks_kurang']:
            self.assertEqual(self.result(.1,choice=choice,answer_confidence=.1)['level'],'TIDAK_FRAUD')
            self.assertEqual(self.result(.9,choice=choice,answer_confidence=.1)['level'],'FRAUD')
    def test_context_is_optional_for_binary(self):
        self.assertFalse(assess({'is_fraud':{'noul':.2}},{'decision_mode':'binary'})['is_fraud'])
    def test_invalid_score_is_not_classified_safe(self):
        for score in [float('nan'),float('inf'),-1,2]:
            with self.assertRaises(ValueError):self.result(score)
    def test_invalid_config(self):
        for changes in [{'decision_mode':'unknown'},{'threshold_fraud':None},{'threshold_fraud':True},{'threshold_fraud':1.1}]:
            with self.assertRaises(ValueError):resolve_policy({},changes)
    def test_adjusting_binary_threshold(self):
        answers={'is_fraud':{'noul':.6}}
        self.assertTrue(assess(answers,{'decision_mode':'binary','threshold_fraud':.5})['is_fraud'])
        self.assertFalse(assess(answers,{'decision_mode':'binary','threshold_fraud':.7})['is_fraud'])
    def test_default_config_activates_binary_mode(self):
        policy=load_config()['model']['laya']['effective_policy']
        self.assertEqual(policy['decision_mode'],'binary');self.assertEqual(policy['threshold_fraud'],.5)

if __name__=='__main__':unittest.main()
