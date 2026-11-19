# Laya RS v3 — synthetic pilot

Selected seed: 2. Status: **shadow, not production ready**.

Trained outputs: active fraud score and four-way conversation context. No trained fine-grained category or five legacy signals. Load `questions.json`, `calibration.json`, and `decision_policy.json` together with weights.

Validation context macro-F1 0.8891; final synthetic test context macro-F1 0.7973, binary recall 0.7333 at diagnostic threshold 0.5. All operational decisions currently abstain to human review because validation threshold gates failed.

See `../../training/evaluation/RESULTS.md`, `model_manifest.json`, and `../../PLAN_PROJECT.md`.
