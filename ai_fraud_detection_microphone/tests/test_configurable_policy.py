import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
import yaml
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.internal.utils.config_loader import load_config
from backend.internal.ai_runtime.ai_fraud_detection import resolve_policy,assess
from backend.internal.ai_runtime.background_process.laya_ai_process import LayaEngine

class ConfigurablePolicyTests(unittest.TestCase):
    def config(self,**overrides):
        raw=yaml.safe_load((ROOT/'config/config.yaml').read_text())
        raw['model']['laya']['decision_mode']='tiered'
        raw['model']['laya'].update(overrides)
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'config.yaml';file.write_text(yaml.safe_dump(raw))
            return load_config(file)
    def test_config_overrides_bundle_without_modifying_it(self):
        path=ROOT/'model/laya-fraud-rs-best/decision_policy.json';before=path.read_bytes()
        cfg=self.config(threshold_review=.35,threshold_escalate=.75,context_min_confidence=.6)
        policy=cfg['model']['laya']['effective_policy']
        self.assertEqual(policy['threshold_review'],.35);self.assertEqual(policy['threshold_escalate'],.75)
        self.assertEqual(policy['context_min_confidence'],.6);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(policy['source'],'config_override');self.assertFalse(policy['production_ready'])
    def test_fallback_and_explicit_null(self):
        base={'threshold_review':.4,'threshold_escalate':.8,'context_min_confidence':.7}
        self.assertEqual(resolve_policy(base,{})['threshold_review'],.4)
        self.assertIsNone(resolve_policy(base,{'threshold_review':None,'threshold_escalate':None})['threshold_review'])
        self.assertIsNone(resolve_policy(base,{'threshold_escalate':None})['threshold_escalate'])
    def test_invalid_thresholds_rejected(self):
        for values in [dict(threshold_review=-.1),dict(threshold_escalate=1.1),dict(threshold_review=.9,threshold_escalate=.5),
                       dict(threshold_review=None,threshold_escalate=.8),dict(context_min_confidence=None),
                       dict(context_min_confidence=True),dict(threshold_review='0.5'),dict(threshold_review=float('nan'))]:
            with self.subTest(values=values),self.assertRaises(ValueError):self.config(**values)
    def test_policy_version_tracks_effective_values(self):
        first=self.config(threshold_review=.5)
        self.assertEqual(first['policy_version'],self.config(threshold_review=.5)['policy_version'])
        self.assertNotEqual(first['policy_version'],self.config(threshold_review=.6)['policy_version'])
    def test_engine_uses_configured_policy(self):
        cfg=self.config(threshold_review=.8,threshold_escalate=.9)['model']['laya']
        agent=Mock();agent.tok.encode.return_value=[1,2]
        agent.predict.return_value={'answers':{'is_fraud':{'noul':.6},'context_status':{'choice':'layanan_normal','answer_confidence':.9}}}
        with patch.dict(sys.modules,{'laya':types.SimpleNamespace(load=Mock(return_value=agent))}):
            engine=LayaEngine(cfg['checkpoint_dir'],policy=cfg['effective_policy'])
            self.assertEqual(engine.predict('Bayar di kasir')['level'],'TIDAK_ADA_INDIKASI')
            engine.policy=resolve_policy(engine.policy,{'threshold_review':.5})
            self.assertEqual(engine.predict('Bayar di kasir')['reason'],'keluaran_model_bertentangan')
    def test_exact_boundaries_and_disable_escalation(self):
        policy={'threshold_review':.5,'threshold_escalate':.85,'context_min_confidence':.7}
        answers={'is_fraud':{'noul':.85},'context_status':{'choice':'risiko_aktif','answer_confidence':.7}}
        self.assertEqual(assess(answers,policy)['level'],'ESKALASI')
        self.assertEqual(assess(answers,resolve_policy(policy,{'threshold_escalate':None}))['level'],'PERLU_DICEK')

if __name__=='__main__':unittest.main()
