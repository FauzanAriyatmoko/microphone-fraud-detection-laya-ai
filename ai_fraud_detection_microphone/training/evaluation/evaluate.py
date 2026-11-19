"""Calibrate on calibration families, select on validation only, test selected seed once."""
import argparse
import json
import os
import time
from pathlib import Path
os.environ.setdefault('USE_TF', '0')
ROOT = Path(__file__).resolve().parents[2]
LABELS = ['risiko_aktif', 'layanan_normal', 'laporan_kejadian', 'konteks_kurang']

def read(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]

def binary_metrics(scores, gold, threshold):
    tp = sum(p >= threshold and y for p,y in zip(scores,gold))
    fp = sum(p >= threshold and not y for p,y in zip(scores,gold))
    fn = sum(p < threshold and y for p,y in zip(scores,gold))
    tn = len(gold)-tp-fp-fn
    precision = tp/(tp+fp) if tp+fp else None
    recall = tp/(tp+fn) if tp+fn else None
    return dict(tp=tp,fp=fp,fn=fn,tn=tn,precision=precision,recall=recall,
        f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.0,
        fpr=fp/(fp+tn) if fp+tn else None)

def metrics(rows, predictions, threshold=.5):
    pairs = [(r,p['answers']) for r,p in zip(rows,predictions)]
    labeled = [(r,a) for r,a in pairs if 'is_fraud' in r['expected']]
    scores = [a['is_fraud']['noul'] for r,a in labeled]
    gold = [r['expected']['is_fraud'] for r,a in labeled]
    b = binary_metrics(scores,gold,threshold)
    b['brier'] = sum((p-y)**2 for p,y in zip(scores,gold))/len(gold)
    b['ece'] = sum(len(ids)/len(gold)*abs(sum(scores[i] for i in ids)/len(ids)-sum(gold[i] for i in ids)/len(ids)) for lo in range(10) if (ids := [i for i,p in enumerate(scores) if lo/10 <= p and (p < (lo+1)/10 or lo==9)]))
    confusion = {label:{pred:0 for pred in LABELS} for label in LABELS}
    errors = []
    for r,a in pairs:
        truth = r['expected']['context_status']
        pred = a['context_status']['choice']
        confusion[truth][pred] += 1
        if truth != pred or ('is_fraud' in r['expected'] and (a['is_fraud']['noul']>=threshold) != r['expected']['is_fraud']):
            errors.append({'text':r['state'],'expected':r['expected'],'answers':a,'metadata':r['metadata']})
    per = {}
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[t][label] for t in LABELS if t!=label)
        fn = sum(confusion[label][t] for t in LABELS if t!=label)
        per[label] = {'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0, 'support':sum(confusion[label].values())}
    return {'rows':len(rows),'binary':b,'context_macro_f1':sum(v['f1'] for v in per.values())/4,
        'context_accuracy':sum(confusion[t][t] for t in LABELS)/len(rows), 'context_per_class':per,'confusion':confusion,'errors':errors}

def threshold_policy(rows, predictions):
    labeled = [(r,p['answers']['is_fraud']['noul']) for r,p in zip(rows,predictions) if 'is_fraud' in r['expected']]
    scores = [p for r,p in labeled]; gold = [r['expected']['is_fraud'] for r,p in labeled]
    grid = [(i/100,binary_metrics(scores,gold,i/100)) for i in range(1,100)]
    review = [(t,m) for t,m in grid if m['recall'] is not None and m['recall']>=.95 and m['fpr'] is not None and m['fpr']<=.10]
    tr = max((t for t,m in review), default=None)
    escalate = [(t,m) for t,m in grid if tr is not None and t>=tr and m['precision'] is not None and m['precision']>=.95 and m['tp']>=20 and m['recall']>=.5]
    te = min((t for t,m in escalate), default=None)
    return {'threshold_review':tr,'threshold_escalate':te,'context_min_confidence':.7,
        'context_min_confidence_status':'provisional_not_optimized', 'source':'validation',
        'review_metrics':binary_metrics(scores,gold,tr) if tr is not None else None,
        'escalate_metrics':binary_metrics(scores,gold,te) if te is not None else None,
        'production_ready':False,'mode':'shadow','reason':'Synthetic pilot; real audio and combined decision policy not yet validated.'}

def evaluate(ckpt, split, calibrate=False):
    import laya
    from laya.calibrate import records_from_labeled
    agent = laya.load(str(ckpt), device='cuda')
    questions = json.loads((ckpt/'questions.json').read_text())
    data = ROOT/'training/datasets/v3'
    if calibrate:
        pairs=[]
        for row in read(data/'calibration.jsonl'):
            qs={k:questions[k] for k in row['expected']}
            targets={k:([float(not v),float(v)] if k=='is_fraud' else [float(v==label) for label in questions[k]['criteria']]) for k,v in row['expected'].items()}
            pairs.append((row['state'],qs,targets))
        records=records_from_labeled(agent,pairs)
        report=agent.fit_temperatures(records,compute_ece=True,seed=42)
        agent.save_calibration(str(ckpt/'calibration.json'))
        (ckpt/'external_calibration_report.json').write_text(json.dumps(report,indent=2,default=str))
    else:
        agent.load_calibration(str(ckpt/'calibration.json'))
    rows=read(data/f'{split}.jsonl')
    start=time.perf_counter()
    predictions=agent.predict_batch([r['state'] for r in rows],questions,batch_size=16,min_confidence=0.0)
    report=metrics(rows,predictions)
    report['batch_ms_per_case']=(time.perf_counter()-start)*1000/len(rows)
    report['checkpoint']=str(ckpt); report['split']=split
    (ckpt/f'{split}_metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (ckpt/f'{split}_predictions.jsonl').write_text(''.join(json.dumps({'state':r['state'],'expected':r['expected'],'result':p},ensure_ascii=False)+'\n' for r,p in zip(rows,predictions)))
    if split=='validation':
        (ckpt/'decision_policy.json').write_text(json.dumps(threshold_policy(rows,predictions),indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ['errors','confusion']},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--split',choices=['validation','test'],default='validation')
    p.add_argument('--calibrate',action='store_true')
    a=p.parse_args()
    if a.calibrate and a.split!='validation':
        p.error('Calibrate/select with validation before opening final test')
    evaluate(a.checkpoint.resolve(),a.split,a.calibrate)
