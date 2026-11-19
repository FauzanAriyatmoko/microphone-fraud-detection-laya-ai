#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${LAYA_PYTHON:-$project_root/../.env_laya/bin/python}"
base_checkpoint="${1:?Usage: train_all.sh /absolute/path/to/base/multilingual}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
for seed in 0 1 2; do
  "$python_bin" -u "$project_root/training/scripts/train.py" --base "$base_checkpoint" --seed "$seed"
  "$python_bin" -u "$project_root/training/evaluation/evaluate.py" --checkpoint "$project_root/training/checkpoints/v3-s$seed" --calibrate
done
"$python_bin" -u "$project_root/training/evaluation/select_model.py"
