"""Train one seed; calibration uses a separate family-held-out file afterwards."""
import argparse
import hashlib
import importlib.metadata
import platform
import time
import json
import os
from pathlib import Path
os.environ.setdefault('USE_TF', '0')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
from laya.train import TrainConfig, finetune

ROOT = Path(__file__).resolve().parents[2]

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base', required=True, help='Local base checkpoint directory')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--epochs', type=int, default=8)
    args = p.parse_args()
    out = ROOT / 'training/checkpoints' / f'v3-s{args.seed}'
    if out.exists():
        raise SystemExit(f'Refusing to overwrite {out}')
    out.mkdir(parents=True)
    h = hashlib.sha256()
    with (Path(args.base) / 'model.safetensors').open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    environment = {'python': platform.python_version(), 'base_checkpoint': str(Path(args.base).resolve()),
        'base_weights_sha256': h.hexdigest(), 'seed': args.seed,
        'packages': {name: importlib.metadata.version(name) for name in ['laya', 'torch', 'transformers', 'numpy']}}
    (out / 'run_environment.json').write_text(json.dumps(environment, indent=2))
    start = time.monotonic()
    cfg = TrainConfig(epochs=args.epochs, micro_batch=8, grad_accum=2,
        loss='rlcd', seed=args.seed, max_len=256, head_max_len=256,
        calib_frac=0.0, calib_max=0, label_smoothing=0.05,
        eval_data=str(ROOT / 'training/datasets/v3/validation.jsonl'), log_every=100)
    result = finetune(str(ROOT / 'training/datasets/v3/train.jsonl'), args.base, str(out), cfg, device='cuda')
    result['wall_seconds'] = time.monotonic() - start
    (out / 'run_summary.json').write_text(json.dumps(result, indent=2, default=str))

if __name__ == '__main__':
    main()
