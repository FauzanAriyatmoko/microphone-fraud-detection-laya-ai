import csv, json, laya

agent = laya.load("./laya-fraud-rs")
cases = [json.loads(l) for l in open("heldout_synth.jsonl", encoding="utf-8")]
meta = {r["text"]: r["category"] for r in csv.DictReader(open("meta_synth.csv", encoding="utf-8"))
        if r["split"] == "heldout"}

res = agent.predict_batch([c["state"] for c in cases], cases[0]["questions"], batch_size=32)
rows = []
for c, r in zip(cases, res):
    p = r["answers"]["is_fraud"]["noul"]
    if (p >= 0.5) != c["expected"]["is_fraud"]:
        rows.append((p, c["expected"]["is_fraud"], meta.get(c["state"], "?"), c["state"]))
for p, gold, cat, text in sorted(rows, reverse=True):
    print(f"p={p:.3f} gold_fraud={gold} [{cat}] {text}")