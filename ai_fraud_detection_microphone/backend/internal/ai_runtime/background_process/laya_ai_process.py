"""Laya inference adapter shared by the daemon and standalone text CLI."""
import argparse
import json
import os
from pathlib import Path
from backend.internal.ai_runtime.ai_fraud_detection import assess,validate_policy

class LayaEngine:
    def __init__(self, checkpoint, device='cuda', policy_file=None, calibration_file=None, policy=None):
        os.environ.setdefault('USE_TF', '0')
        import laya
        self.checkpoint = Path(checkpoint).resolve()
        self.questions = json.loads((self.checkpoint/'questions.json').read_text())
        self.policy = dict(policy) if policy is not None else json.loads(Path(policy_file or self.checkpoint/'decision_policy.json').read_text())
        validate_policy(self.policy)
        self.agent = laya.load(str(self.checkpoint), device=device)
        self.agent.load_calibration(str(calibration_file or self.checkpoint/'calibration.json'))
        self.max_tokens = json.loads((self.checkpoint/'rl_agent_config.json').read_text()).get('max_len', 256)

    def predict(self, text):
        if not text.strip():
            raise ValueError('Transcript must not be empty')
        # Never silently truncate speech that may contain a negation or correction.
        if len(self.agent.tok.encode(text, add_special_tokens=True)) > self.max_tokens:
            return {'level':'TIDAK_DAPAT_DINILAI' if self.policy.get('decision_mode')=='binary' else 'PERLU_DICEK',
                    'is_fraud':None,'decision_mode':self.policy.get('decision_mode','tiered'),
                    'reason':'konteks_melebihi_batas_token','mode':'shadow','production_ready':False}
        result = self.agent.predict(text, self.questions, min_confidence=0.0)
        return assess(result['answers'], self.policy)

def main():
    p=argparse.ArgumentParser()
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--checkpoint',help='Load bundle policy without YAML overrides')
    source.add_argument('--config',type=Path,help='Use model paths and threshold overrides from YAML')
    p.add_argument('--text',required=True)
    p.add_argument('--device',default='cuda')
    a=p.parse_args()
    if a.config:
        from backend.internal.utils.config_loader import load_config
        cfg=load_config(a.config)['model']['laya']
        engine=LayaEngine(cfg['checkpoint_dir'],a.device,cfg['decision_policy_file'],cfg['calibration_file'],policy=cfg['effective_policy'])
    else:engine=LayaEngine(a.checkpoint,a.device)
    print(json.dumps(engine.predict(a.text),ensure_ascii=False,indent=2))

if __name__=='__main__': main()
