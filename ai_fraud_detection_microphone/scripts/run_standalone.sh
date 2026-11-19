#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${LAYA_PYTHON:-$project_root/../.env_laya/bin/python}"
# Optional local CUDA 12 cuBLAS and PortAudio installation; keep environment overrides.
for library_dir in "$project_root/../.cuda12/nvidia/cublas/lib" "$project_root/.native/usr/lib/x86_64-linux-gnu"; do
  if [[ -d "$library_dir" ]]; then
    export LD_LIBRARY_PATH="$library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
done
export USE_TF=0
export TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
export PYTHONPATH="$project_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" -m backend.app.main "$@"
