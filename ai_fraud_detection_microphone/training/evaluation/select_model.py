"""Freeze a validation-based selection, run final test, package a shadow-only model."""
import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from evaluate import ROOT, evaluate, read, threshold_policy


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--seeds',type=int,nargs='+',default=[0,1,2])
    args=p.parse_args()
    out=ROOT/'training/evaluation'
    selection_path=out/'selection.json'
    destination=ROOT/'model/laya-fraud-rs-best'
    if selection_path.exists() or destination.exists():
        raise SystemExit('Selection already frozen; use a new version for a new experiment.')
    candidates=[]
    for seed in args.seeds:
        ckpt=ROOT/'training/checkpoints'/f'v3-s{seed}'
        report=json.loads((ckpt/'validation_metrics.json').read_text())
        rows=read(ROOT/'training/datasets/v3/validation.jsonl')
        predictions=[r['result'] for r in read(ckpt/'validation_predictions.jsonl')]
        policy=threshold_policy(rows,predictions)
        (ckpt/'decision_policy.json').write_text(json.dumps(policy,indent=2))
        candidates.append({'seed':seed,'checkpoint':str(ckpt),'context_macro_f1':report['context_macro_f1'],
            'binary_f1':report['binary']['f1'],'binary':report['binary'],'policy':policy})
    winner=max(candidates,key=lambda r:(r['context_macro_f1'],r['binary_f1'],-r['seed']))
    selection={'criterion':'validation context_macro_f1, binary_f1, lower seed','selected_seed':winner['seed'],'candidates':candidates,'test_used_for_selection':False}
    selection_path.write_text(json.dumps(selection,indent=2))
    ckpt=Path(winner['checkpoint'])
    report=evaluate(ckpt,'test')
    spec=importlib.util.spec_from_file_location('decision',ROOT/'backend/internal/ai_runtime/ai_fraud_detection.py')
    decision=importlib.util.module_from_spec(spec);spec.loader.exec_module(decision)
    from collections import Counter
    policy_counts={}
    for split in ['validation','test']:
        counts=Counter()
        for row in read(ckpt/f'{split}_predictions.jsonl'):
            result=decision.assess(row['result']['answers'],winner['policy'])
            counts[(row['expected']['context_status'],result['level'])]+=1
        policy_counts[split]=[{'gold_context':g,'level':l,'count':n} for (g,l),n in sorted(counts.items())]
    (out/'combined_policy_counts.json').write_text(json.dumps(policy_counts,indent=2))
    shutil.copytree(ckpt,destination,ignore=shutil.ignore_patterns('checkpoint_latest','*_predictions.jsonl'))
    original_hash=digest(ckpt/'model.safetensors')
    assert original_hash==digest(destination/'model.safetensors')
    # Rebind the location only after checking identical weights/config; retain provenance.
    cal=json.loads((destination/'calibration.json').read_text())
    cal['original_model_id_or_path']=cal.get('model_id_or_path')
    cal['model_id_or_path']=str(destination.resolve())
    (destination/'calibration.json').write_text(json.dumps(cal,indent=2))
    manifest={'model_version':f'laya-rs-v3-s{winner["seed"]}-synthetic-pilot','source':str(ckpt),
        'weights_sha256':original_hash,'questions_sha256':digest(destination/'questions.json'),
        'dataset_manifest':json.loads((ROOT/'training/datasets/v3/manifest.json').read_text()),
        'environment':json.loads((ckpt/'run_environment.json').read_text()),'production_ready':False,
        'limitations':['synthetic templates only','single/mild multi-turn text; no audio validation','no trained fine-grained categories or five legacy signals','domain SOP not reviewed','threshold policy may abstain on all input']}
    (destination/'model_manifest.json').write_text(json.dumps(manifest,indent=2))
    print('Packaged shadow model:',destination)

if __name__=='__main__':main()
