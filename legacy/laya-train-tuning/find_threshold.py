"""Evaluasi checkpoint Laya pada held-out dan cari threshold untuk is_fraud (noul).

Pemakaian:
  python find_threshold.py --ckpt ./laya-fraud-rs --data heldout_synth.jsonl
"""
import argparse, json
import laya

ap = argparse.ArgumentParser()
ap.add_argument("--ckpt", required=True)
ap.add_argument("--data", required=True)
ap.add_argument("--batch-size", type=int, default=32)
ap.add_argument("--target-precision", type=float, default=0.95)
args = ap.parse_args()

cases = [json.loads(l) for l in open(args.data, encoding="utf-8")]
questions = cases[0]["questions"]          # harus SAMA dengan yang dipakai saat training
states = [c["state"] for c in cases]
gold = [bool(c["expected"]["is_fraud"]) for c in cases]

agent = laya.load(args.ckpt)
results = agent.predict_batch(states, questions, batch_size=args.batch_size)
p = [r["answers"]["is_fraud"]["noul"] for r in results]

def metrics(th):
    pred = [x >= th for x in p]
    tp = sum(a and g for a, g in zip(pred, gold))
    fp = sum(a and not g for a, g in zip(pred, gold))
    fn = sum((not a) and g for a, g in zip(pred, gold))
    tn = sum((not a) and (not g) for a, g in zip(pred, gold))
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return dict(th=th, prec=prec, rec=rec, f1=f1, fp=fp, fn=fn, tp=tp, tn=tn)

print(f"{'thr':>5} {'prec':>6} {'recall':>6} {'F1':>6} {'FP':>4} {'FN':>4}")
rows = [metrics(t / 20) for t in range(1, 20)]
for m in rows:
    print(f"{m['th']:5.2f} {m['prec']:6.3f} {m['rec']:6.3f} {m['f1']:6.3f} {m['fp']:4d} {m['fn']:4d}")

best_f1 = max(rows, key=lambda m: m["f1"])
ok = [m for m in rows if m["prec"] >= args.target_precision]
hi = min(ok, key=lambda m: m["th"]) if ok else None
print(f"\nThreshold F1 terbaik          : {best_f1['th']:.2f} (F1={best_f1['f1']:.3f})")
if hi:
    print(f"Threshold ESKALASI otomatis   : {hi['th']:.2f} (precision>={args.target_precision}, recall={hi['rec']:.3f})")
    low = max([m["th"] for m in rows if m["rec"] >= 0.95] or [0.05])
    print(f"Threshold REVIEW MANUAL (>=)  : {low:.2f} (recall>=0.95)  -> antara {low:.2f} dan {hi['th']:.2f} kirim ke manusia")
else:
    print(f"Tidak ada threshold dengan precision >= {args.target_precision} pada held-out ini.")
print("\nCatatan: ini held-out sintetis. Ukur ulang pada percakapan nyata (hasil speech-to-text) sebelum dipakai.")
