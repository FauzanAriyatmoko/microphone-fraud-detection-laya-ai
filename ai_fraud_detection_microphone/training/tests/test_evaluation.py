import importlib.util
import unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'evaluation/evaluate.py'
s=importlib.util.spec_from_file_location('evaluation',p);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)

class EvaluationTests(unittest.TestCase):
    def test_empty_positive_precision_is_undefined(self):
        self.assertIsNone(d.binary_metrics([.1,.2],[False,True],.9)['precision'])
    def test_constant_score_cannot_meet_review_gate(self):
        rows=[{'expected':{'is_fraud':i%2==0}} for i in range(100)]
        predictions=[{'answers':{'is_fraud':{'noul':.5}}} for _ in rows]
        policy=d.threshold_policy(rows,predictions)
        self.assertIsNone(policy['threshold_review'])
        self.assertIsNone(policy['threshold_escalate'])
    def test_separable_scores(self):
        rows=[{'expected':{'is_fraud':i%2==0}} for i in range(100)]
        predictions=[{'answers':{'is_fraud':{'noul':.95 if i%2==0 else .05}}} for i in range(100)]
        policy=d.threshold_policy(rows,predictions)
        self.assertIsNotNone(policy['threshold_review'])
        self.assertEqual(policy['escalate_metrics']['precision'],1.)

if __name__=='__main__': unittest.main()
