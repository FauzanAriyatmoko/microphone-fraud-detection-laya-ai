#!/usr/bin/env python3
"""
laya_fraud_rs.py: penyaring risiko fraud (bahasa Indonesia) untuk resepsionis RS,
memakai Laya (checkpoint multilingual) secara lokal.

Perintah:
  python laya_fraud_rs.py demo                      # jalankan contoh bawaan + ukur latensi
  python laya_fraud_rs.py predict --text "..."      # nilai satu transkrip
  python laya_fraud_rs.py interactive               # ketik transkrip satu per satu
  python laya_fraud_rs.py sample                    # tulis data_contoh.csv (hanya contoh FORMAT)
  python laya_fraud_rs.py eval --data data.csv      # evaluasi + kalibrasi + saran threshold
  python laya_fraud_rs.py eval --data data.csv --save   # simpan ke fraud_config.json

Format CSV evaluasi: kolom `text,label`  (label: 1/fraud = penipuan, 0/normal = aman)

CATATAN PENTING
- Output hanya SINYAL untuk staf, bukan keputusan menolak layanan.
- Threshold bawaan hanyalah placeholder. Tentukan dari data nyata lewat `eval`.
- Transkrip TIDAK ditulis ke log. Log audit hanya berisi level & skor.
"""
import os

os.environ.setdefault("USE_TF", "0")  # hindari deadlock transformers+TensorFlow

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

MODEL = "multilingual"
MAX_LEN = 1024
CONFIG_PATH = Path("fraud_config.json")
AUDIT_LOG = Path("fraud_audit.jsonl")
EPS = 1e-4

DEFAULT_CONFIG = {
    "t_review": 0.60,      # >= ini -> PERLU_DICEK
    "t_escalate": 0.85,    # >= ini + sinyal berdampak tinggi -> ESKALASI
    "t_key_signal": 0.50,  # ambang sinyal berdampak tinggi
    "temperature": 1.0,    # kalibrasi (diisi oleh `eval --save`)
}
HIGH_IMPACT = ("minta_data_sensitif", "minta_transfer")


# --------------------------------------------------------------------------- #
# Skema pertanyaan (pola choice A/B, bukan noul, karena noul tidak stabil)
# --------------------------------------------------------------------------- #
def yn(instr, yes, no):
    return {
        "type": "choice",
        "instructions": instr,
        "criteria": {"A": f"ya, {yes}", "B": f"tidak, {no}"},
    }


QUESTIONS = {
    "minta_data_sensitif": yn(
        "Apakah penelepon meminta data pribadi atau medis pasien?",
        "ada permintaan data pasien (alamat, diagnosis, nomor kamar, NIK)",
        "tidak ada permintaan data sensitif",
    ),
    "minta_transfer": yn(
        "Apakah ada permintaan uang atau transfer ke rekening tertentu?",
        "ada permintaan transfer atau pembayaran ke rekening pribadi",
        "tidak ada permintaan uang yang mencurigakan",
    ),
    "mengaku_otoritas": yn(
        "Apakah penelepon mengaku sebagai dokter, polisi, BPJS, atau pejabat tanpa bukti?",
        "mengaku sebagai pihak berwenang tanpa bukti",
        "tidak mengaku pihak berwenang",
    ),
    "desakan_waktu": yn(
        "Apakah penelepon menekan agar segera bertindak tanpa verifikasi?",
        "ada tekanan mendesak agar bertindak sekarang juga",
        "tidak ada tekanan waktu",
    ),
    "lewati_prosedur": yn(
        "Apakah penelepon meminta melewati prosedur resmi atau merahasiakan permintaannya?",
        "diminta melewati prosedur atau dirahasiakan",
        "tidak meminta melewati prosedur",
    ),
}

LABEL_TEKS = {
    "minta_data_sensitif": "meminta data pasien",
    "minta_transfer": "meminta transfer/pembayaran",
    "mengaku_otoritas": "mengaku pihak berwenang",
    "desakan_waktu": "menekan agar buru-buru",
    "lewati_prosedur": "meminta lewati prosedur/rahasia",
}

AKSI = {
    "ESKALASI": "Jangan beri data atau terima permintaan transfer. Minta verifikasi lewat "
                "prosedur resmi dan lapor ke supervisor/keamanan.",
    "PERLU_DICEK": "Lakukan verifikasi identitas tambahan sebelum memberi informasi sensitif.",
    "AMAN": "Lanjutkan layanan normal.",
}


# --------------------------------------------------------------------------- #
# Config & kalibrasi
# --------------------------------------------------------------------------- #
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def calibrate(p, temperature):
    """Temperature scaling pada logit probabilitas opsi A (kasus 2 opsi)."""
    p = min(max(p, EPS), 1 - EPS)
    z = math.log(p / (1 - p)) / temperature
    return 1 / (1 + math.exp(-z))


def risk_of(signals, temperature=1.0):
    return max(calibrate(v, temperature) for v in signals.values())


# --------------------------------------------------------------------------- #
# Inferensi Laya
# --------------------------------------------------------------------------- #
_router = None


def get_router():
    global _router
    if _router is None:
        from laya import Router  # import lazy supaya fungsi lain bisa dites tanpa laya

        _router = Router()
        _router.preload([MODEL])  # hanya muat multilingual
    return _router


def get_signals(text):
    """Probabilitas mentah opsi A (ya) untuk tiap sinyal."""
    res = get_router().predict(text, QUESTIONS, model=MODEL, max_len=MAX_LEN)
    return {k: float(v["probabilities"]["A"]) for k, v in res["answers"].items()}


def assess(text, cfg=None):
    cfg = cfg or load_config()
    raw = get_signals(text)
    sig = {k: calibrate(v, cfg["temperature"]) for k, v in raw.items()}
    risk = max(sig.values())
    key_hit = any(sig[k] >= cfg["t_key_signal"] for k in HIGH_IMPACT)

    if risk >= cfg["t_escalate"] and key_hit:
        level = "ESKALASI"
    elif risk >= cfg["t_review"]:
        level = "PERLU_DICEK"
    else:
        level = "AMAN"

    alasan = [LABEL_TEKS[k] for k, v in sorted(sig.items(), key=lambda x: -x[1]) if v >= 0.5]
    return {"level": level, "risk": round(risk, 3),
            "signals": {k: round(v, 3) for k, v in sig.items()},
            "alasan": alasan, "aksi": AKSI[level]}


def audit(result):
    """Log tanpa teks transkrip (minimisasi data, privasi pasien)."""
    row = {"ts": datetime.now().isoformat(timespec="seconds"),
           "level": result["level"], "risk": result["risk"], "signals": result["signals"]}
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_result(r):
    print(f"  Level : {r['level']}  (risk={r['risk']:.2f})")
    if r["alasan"]:
        print(f"  Alasan: {', '.join(r['alasan'])}")
    print("  Sinyal: " + ", ".join(f"{k}={v:.2f}" for k, v in r["signals"].items()))
    print(f"  Aksi  : {r['aksi']}")


# --------------------------------------------------------------------------- #
# Contoh data (hanya untuk menunjukkan FORMAT, bukan untuk evaluasi serius)
# --------------------------------------------------------------------------- #
SAMPLES = [
    ("Halo, saya dr. Andi dari bagian bedah, tolong kirimkan nomor kamar dan diagnosis pasien atas nama Budi sekarang juga, ini darurat.", 1),
    ("Mbak, saya dari pihak BPJS, tolong transfer biaya administrasi dua juta ke rekening ini supaya klaim pasien tidak hangus hari ini.", 1),
    ("Saya keluarga pasien yang baru masuk IGD, berapa nomor kamarnya dan penyakitnya apa? Cepat ya, jangan pakai verifikasi, ini mendesak.", 1),
    ("Pak, pembayaran rawat inap bisa ditransfer ke rekening pribadi perawat supaya lebih cepat, nomornya saya kirim lewat WhatsApp.", 1),
    ("Saya petugas kepolisian, mohon berikan data rekam medis pasien kecelakaan tadi malam tanpa surat resmi, kami butuh sekarang.", 1),
    ("Ini dari direksi rumah sakit, tolong ubah nomor rekening pembayaran di loket ke nomor yang saya kirim, jangan bilang siapa-siapa.", 1),
    ("Selamat pagi, saya mau daftar berobat ke poli jantung, dokternya praktik jam berapa hari ini?", 0),
    ("Permisi, saya mau tanya biaya rawat inap kelas dua dan apakah BPJS bisa dipakai.", 0),
    ("Ibu saya kena stroke dan baru masuk IGD, tolong beri tahu di mana ruangannya, saya anaknya dan ini KTP saya.", 0),
    ("Saya mau bayar tagihan lewat transfer ke rekening resmi rumah sakit yang tertera di kasir, bisa minta nomor virtual account-nya?", 0),
    ("Dok, jadwal operasi bapak saya dimajukan ya? Saya cuma mau konfirmasi, tidak buru-buru.", 0),
    ("Tolong cek apakah hasil laboratorium saya sudah keluar, nama saya Rina, lahir 12 Mei 1990.", 0),
]


def cmd_sample(_args):
    path = Path("data_contoh.csv")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["text", "label"])
        w.writerows(SAMPLES)
    print(f"Ditulis: {path} ({len(SAMPLES)} baris). Ini hanya contoh format.")
    print("Ganti dengan 300-500 transkrip nyata/berlabel (termasuk kasus normal yang mirip-mirip).")


# --------------------------------------------------------------------------- #
# Evaluasi
# --------------------------------------------------------------------------- #
def parse_label(v):
    s = str(v).strip().lower()
    if s in {"1", "fraud", "penipuan", "true", "ya"}:
        return 1
    if s in {"0", "normal", "aman", "false", "tidak"}:
        return 0
    raise ValueError(f"Label tidak dikenali: {v!r}")


def metrics(risks, labels, t):
    tp = sum(1 for r, y in zip(risks, labels) if r >= t and y == 1)
    fp = sum(1 for r, y in zip(risks, labels) if r >= t and y == 0)
    fn = sum(1 for r, y in zip(risks, labels) if r < t and y == 1)
    tn = sum(1 for r, y in zip(risks, labels) if r < t and y == 0)
    nan = float("nan")
    return {
        "precision": tp / (tp + fp) if tp + fp else nan,
        "recall": tp / (tp + fn) if tp + fn else nan,
        "fpr": fp / (fp + tn) if fp + tn else nan,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def ece(risks, labels, bins=10):
    n, total = len(risks), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, r in enumerate(risks) if lo <= r < hi or (b == bins - 1 and r >= 1.0)]
        if not idx:
            continue
        conf = sum(risks[i] for i in idx) / len(idx)
        acc = sum(labels[i] for i in idx) / len(idx)
        total += len(idx) / n * abs(conf - acc)
    return total


def fit_temperature(raw_list, labels):
    best_t, best_nll = 1.0, float("inf")
    for i in range(5, 51):  # T = 0.5 .. 5.0
        t = i / 10
        nll = 0.0
        for sig, y in zip(raw_list, labels):
            r = min(max(risk_of(sig, t), EPS), 1 - EPS)
            nll -= math.log(r if y == 1 else 1 - r)
        if nll < best_nll:
            best_t, best_nll = t, nll
    return best_t


def fmt(x):
    return "  n/a" if (isinstance(x, float) and math.isnan(x)) else f"{x:5.2f}"


def cmd_eval(args):
    rows = []
    with open(args.data, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((r["text"], parse_label(r["label"])))
    labels = [y for _, y in rows]
    n_pos, n_neg = sum(labels), len(labels) - sum(labels)
    print(f"Data: {len(rows)} baris ({n_pos} fraud, {n_neg} normal)")
    if n_pos < 20 or n_neg < 20:
        print("PERINGATAN: data terlalu sedikit; threshold dari sini belum bisa dipercaya.")

    raw_list = []
    t0 = time.time()
    for i, (text, _) in enumerate(rows, 1):
        raw_list.append(get_signals(text))
        if i % 20 == 0:
            print(f"  ...{i}/{len(rows)}")
    print(f"Inferensi selesai ({(time.time() - t0) / len(rows) * 1000:.0f} ms/teks)\n")

    risks_raw = [risk_of(s, 1.0) for s in raw_list]
    temp = fit_temperature(raw_list, labels)
    risks = [risk_of(s, temp) for s in raw_list]
    print(f"ECE sebelum kalibrasi: {ece(risks_raw, labels):.3f}")
    print(f"ECE sesudah (T={temp:.1f}): {ece(risks, labels):.3f}\n")

    grid = [i / 20 for i in range(2, 20)]  # 0.10 .. 0.95
    print("thr   precision recall  FPR    TP  FP  FN  TN")
    for t in grid:
        m = metrics(risks, labels, t)
        print(f"{t:.2f}  {fmt(m['precision'])}     {fmt(m['recall'])}  {fmt(m['fpr'])}  "
              f"{m['tp']:3d} {m['fp']:3d} {m['fn']:3d} {m['tn']:3d}")

    ok = [t for t in grid if not math.isnan(metrics(risks, labels, t)["recall"])
          and metrics(risks, labels, t)["recall"] >= args.target_recall]
    if ok:
        t_review = max(ok)  # threshold tertinggi yang masih menjaga recall -> FPR terendah
    else:
        t_review = min(grid)
        print(f"\nPERINGATAN: tidak ada threshold yang mencapai recall {args.target_recall}. "
              "Model zero-shot belum memadai; pertimbangkan fine-tuning.")
    esc = [t for t in grid if t >= t_review
           and not math.isnan(metrics(risks, labels, t)["precision"])
           and metrics(risks, labels, t)["precision"] >= args.target_precision]
    t_escalate = min(esc) if esc else max(t_review, 0.85)

    m = metrics(risks, labels, t_review)
    print(f"\nSaran: t_review={t_review:.2f} (recall={fmt(m['recall']).strip()}, "
          f"FPR={fmt(m['fpr']).strip()}), t_escalate={t_escalate:.2f}, temperature={temp:.1f}")

    fps = [(r, t) for r, (t, y) in zip(risks, rows) if y == 0 and r >= t_review]
    if fps:
        print("\nFalse positive (periksa: kasus normal yang terlihat mencurigakan):")
        for r, t in sorted(fps, reverse=True)[:5]:
            print(f"  risk={r:.2f} | {t[:90]}")
    fns = [(r, t) for r, (t, y) in zip(risks, rows) if y == 1 and r < t_review]
    if fns:
        print("\nFalse negative (fraud yang lolos):")
        for r, t in sorted(fns)[:5]:
            print(f"  risk={r:.2f} | {t[:90]}")

    if args.save:
        cfg = load_config()
        cfg.update({"t_review": t_review, "t_escalate": t_escalate, "temperature": temp})
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"\nDisimpan ke {CONFIG_PATH}")


# --------------------------------------------------------------------------- #
# Perintah lain
# --------------------------------------------------------------------------- #
def cmd_demo(_args):
    cfg = load_config()
    print(f"Config: {cfg}\n")
    get_router()  # muat model dulu supaya latensi adil
    for text, truth in SAMPLES:
        t0 = time.time()
        r = assess(text, cfg)
        ms = (time.time() - t0) * 1000
        print(f"[label={'fraud' if truth else 'normal'}] {text[:80]}")
        print_result(r)
        print(f"  Waktu : {ms:.0f} ms\n")


def cmd_predict(args):
    r = assess(args.text)
    print_result(r)
    if args.log:
        audit(r)


def cmd_interactive(args):
    print("Ketik transkrip lalu Enter. Kosong/Ctrl+C untuk keluar.")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        r = assess(text)
        print_result(r)
        if args.log:
            audit(r)


def main():
    ap = argparse.ArgumentParser(description="Laya fraud screening (Indonesia, RS)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    sub.add_parser("sample").set_defaults(fn=cmd_sample)

    p = sub.add_parser("predict")
    p.add_argument("--text", required=True)
    p.add_argument("--log", action="store_true", help="tulis log audit (tanpa teks)")
    p.set_defaults(fn=cmd_predict)

    p = sub.add_parser("interactive")
    p.add_argument("--log", action="store_true")
    p.set_defaults(fn=cmd_interactive)

    p = sub.add_parser("eval")
    p.add_argument("--data", required=True)
    p.add_argument("--target-recall", type=float, default=0.95)
    p.add_argument("--target-precision", type=float, default=0.90)
    p.add_argument("--save", action="store_true")
    p.set_defaults(fn=cmd_eval)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())