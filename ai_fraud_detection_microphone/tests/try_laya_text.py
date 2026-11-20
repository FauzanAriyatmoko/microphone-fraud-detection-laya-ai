#!/usr/bin/env python3
"""Manual text-only Laya runner using the project's effective configuration."""
import argparse
import json
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='Uji model Laya dengan teks tanpa STT, audio, atau MQTT.')
    parser.add_argument('--text', help='Kalimat satu kali; tanpa opsi ini masuk mode interaktif.')
    parser.add_argument('--config', type=Path, default=PROJECT / 'config/config.yaml')
    parser.add_argument('--device', choices=['cuda', 'cpu'], help='Default mengikuti konfigurasi project.')
    parser.add_argument('--json', action='store_true', help='Cetak hasil lengkap sebagai JSON.')
    args = parser.parse_args()
    if args.text is not None and not args.text.strip():
        parser.error('--text tidak boleh kosong')

    # Match the local project's Python environment, including when invoked with python3.
    python = Path(os.environ.get('LAYA_PYTHON', PROJECT.parent / '.env_laya/bin/python')).absolute()
    if python.is_file() and str(python) != str(Path(sys.executable).absolute()):
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.path.insert(0, str(PROJECT))
    os.environ.setdefault('USE_TF', '0')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    os.environ.setdefault('OMP_NUM_THREADS', '4')

    from backend.internal.utils.config_loader import load_config
    from backend.internal.ai_runtime.background_process.laya_ai_process import LayaEngine

    config = load_config(args.config)
    model = config['model']['laya']
    policy = model['effective_policy']
    print(f"Model: {config['model_version']} | mode: {policy.get('decision_mode', 'tiered')} | "
          f"threshold_fraud: {policy.get('threshold_fraud', 0.5)}", file=sys.stderr)
    print('Memuat model Laya...', file=sys.stderr)
    engine = LayaEngine(model['checkpoint_dir'], args.device or model['device'],
                        model['decision_policy_file'], model['calibration_file'], policy=policy)

    def predict(text):
        result = engine.predict(text)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Hasil: {result['level']}")
            if 'fraud_score' in result:
                print(f"Skor risiko: {result['fraud_score']:.2%}")
            print(f"Konteks: {result.get('context_status') or '-'}")
            print(f"Alasan: {result['reason']}")
        return result

    if args.text is not None:
        predict(args.text)
        return
    print('Masukkan kalimat, lalu Enter. Ketik /exit untuk keluar. Setiap input dinilai mandiri.', file=sys.stderr)
    while True:
        try:
            text = input('Kalimat> ')
        except EOFError:
            break
        if text.strip() == '/exit':
            break
        if text.strip():
            predict(text)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nPengujian dihentikan.', file=sys.stderr)
    except Exception as exc:
        print(f'Pengujian gagal: {exc}', file=sys.stderr)
        sys.exit(1)
