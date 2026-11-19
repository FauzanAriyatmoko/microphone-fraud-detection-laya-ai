#!/usr/bin/env bash
# Install Python runtime dependencies and optional local native libraries without sudo.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${LAYA_PYTHON:-$project_root/../.env_laya/bin/python}"
"$python_bin" -m pip install -r "$project_root/backend/requirements.txt"
for option in "$@"; do
  case "$option" in
    --cuda12)
      "$python_bin" -m pip install --target "$project_root/../.cuda12" --upgrade 'nvidia-cublas-cu12==12.9.3.13'
      ;;
    --local-portaudio)
      temp_dir="$(mktemp -d)"
      trap 'rm -rf -- "$temp_dir"' EXIT
      (cd -- "$temp_dir" && apt-get download libportaudio2)
      for package in "$temp_dir"/*.deb; do dpkg-deb -x "$package" "$project_root/.native"; done
      ln -sf libportaudio.so.2 "$project_root/.native/usr/lib/x86_64-linux-gnu/libportaudio.so"
      ;;
    *) echo 'Usage: setup_runtime.sh [--cuda12] [--local-portaudio]' >&2; exit 2 ;;
  esac
done
"$python_bin" -m pip check
"$project_root/scripts/run_standalone.sh" --check-config --verify-weights
