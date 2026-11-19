"""Tampilkan kesalahan model per template, dan ringkasan seberapa yakin model saat salah.

Pemakaian:
  python show_errors_v2.py --ckpt ./laya-fraud-rs-v2 --data heldout_v2.jsonl --split heldout
  python show_errors_v2.py --ckpt ./laya-fraud-rs-v2 --data stress_test.jsonl --split stress
"""
import argparse, csv, collections, json
import laya

ap = argparse.ArgumentParser()
ap.add_argument("--ckpt", required=True)
ap.add_argument("--data", required=True)
ap.add_argument("--meta", default="meta_v2.csv")
ap.add_argument("--split", required=True, choices=["train", "heldout", "stress"])
ap.add_argument("--threshold", type=float, default=0.5)
ap.add_argument("--batch-size", type=int, default=32)
args = ap.parse_args()

cases = [json.loads(l) for l in open(args.data, encoding="utf-8")]
meta = {r["text"]: r for r in csv.DictReader(open(args.meta, encoding="utf-8")) if r["split"] == args.split}

agent = laya.load(args.ckpt)
res = agent.predict_batch([c["state"] for c in cases], cases[0]["questions"], batch_size=args.batch_size)

rows = []
for c, r in zip(cases, res):
    p = r["answers"]["is_fraud"]["noul"]
    gold = bool(c["expected"]["is_fraud"])
    m = meta.get(c["state"], {})
    rows.append(dict(p=p, gold=gold, wrong=(p >= args.threshold) != gold,
                     tid=m.get("template_id", "?"), cat=m.get("category", "?"), text=c["state"]))

n = len(rows); wrong = [r for r in rows if r["wrong"]]
fp = [r for r in wrong if not r["gold"]]; fn = [r for r in wrong if r["gold"]]
print(f"\n=== {args.data} @ threshold {args.threshold} ===")
print(f"akurasi {1 - len(wrong)/n:.3f}  ({n - len(wrong)}/{n})   FP={len(fp)}  FN={len(fn)}")
conf = [r for r in wrong if (r["p"] >= 0.9 if not r["gold"] else r["p"] <= 0.1)]
print(f"kesalahan yang SANGAT yakin (p>=0.9 atau <=0.1): {len(conf)} dari {len(wrong)}")

by_t = collections.defaultdict(lambda: [0, 0])
for r in rows:
    by_t[r["tid"]][0] += 1; by_t[r["tid"]][1] += r["wrong"]
bad = sorted(((t, v) for t, v in by_t.items() if v[1]), key=lambda x: -x[1][1])
if bad:
    print("\nTemplate yang salah (template_id: salah/total):")
    for t, (tot, w) in bad:
        print(f"  {t:32s} {w}/{tot}")
by_c = collections.defaultdict(lambda: [0, 0])
for r in rows:
    by_c[r["cat"]][0] += 1; by_c[r["cat"]][1] += r["wrong"]
print("\nPer kategori (salah/total):")
for c, (tot, w) in sorted(by_c.items(), key=lambda x: -x[1][1]):
    print(f"  {c:26s} {w}/{tot}")
if wrong:
    print("\nContoh kesalahan (diurutkan dari paling yakin):")
    for r in sorted(wrong, key=lambda r: -abs(r["p"] - 0.5))[:15]:
        print(f"  p={r['p']:.3f} gold_fraud={r['gold']!s:5} [{r['tid']}] {r['text']}")