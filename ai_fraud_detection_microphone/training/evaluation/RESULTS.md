# Hasil fine-tuning Laya AI — pilot resepsionis RS

Tanggal: 18 November 2026 (Asia/Jakarta). **Training selesai; model belum memenuhi gate operasional.**

Paket terpilih: `../../model/laya-fraud-rs-best/` (seed 2). Ketiga seed dilatih dari base multilingual yang sama selama 8 epoch menggunakan GPU lokal. Model dipilih hanya dengan validation; final test tidak dipakai untuk memilih seed atau threshold.

## Data dan reproducibility

- Train 1.200 contoh / 63 keluarga; calibration 240 / 18; validation 400 / 29; test 80 / 80.
- Semua data sintetis. Dua output: `is_fraud` aktif dan `context_status` empat kelas. Metadata tidak diberikan ke model.
- File `selection.json`, `environment.json`, `legacy_inventory.json`, manifest dataset, log training dan checkpoint per seed tersedia untuk audit.
- RLCD, 8 epoch, micro-batch 8, grad-accum 2, label smoothing 0,05, token budget 256/256. Kalibrasi memakai split keluarga terpisah; 2.220 item training per seed.
- Warnings upstream tentang API gradient checkpointing yang deprecated dan urutan scheduler/optimizer tercatat pada log; run menyelesaikan delapan epoch tanpa loss non-finite yang tercatat. Tidak ada patch diam-diam terhadap library.

## Perbandingan validation setelah kalibrasi

Angka biner di tabel memakai ambang diagnostik 0,5, bukan ambang operasional yang telah diterima.

| Seed | Macro-F1 konteks | Precision fraud | Recall fraud | F1 fraud | ECE biner |
|---|---:|---:|---:|---:|---:|
| 0 | 0.7774 | 1.0000 | 0.7357 | 0.8477 | 0.0708 |
| 1 | 0.8548 | 1.0000 | 0.7071 | 0.8285 | 0.0660 |
| 2 | 0.8891 | 1.0000 | 0.8000 | 0.8889 | 0.0532 |

## Final test seed 2

- Context accuracy: **83.75%** (67/80); context macro-F1: **0.7973**.
- Biner @0,5: TP=22, FP=0, FN=8, TN=40; precision=100.00%, recall=73.33%, F1=0.8462.
- Metrik biner hanya memakai 70 contoh yang memiliki label biner. Sepuluh kasus ambigu tidak diberi label aman palsu.
- Brier biner: 0.1041; ECE: 0.0969.
- Throughput inference batch test: sekitar 5.40 ms/kasus, batch size 16. Ini bukan latency p95 satu ucapan dan bukan latency audio→MQTT.

| Gold context | Pred risiko | Pred normal | Pred laporan | Pred kurang konteks |
|---|---:|---:|---:|---:|
| risiko_aktif | 24 | 6 | 0 | 0 |
| layanan_normal | 0 | 30 | 0 | 0 |
| laporan_kejadian | 0 | 1 | 9 | 0 |
| konteks_kurang | 1 | 4 | 1 | 4 |

## Keputusan kesiapan

**Tidak lulus.** Macro-F1 test 0,7973 di bawah target pilot 0,85 dan recall biner 0,7333 di bawah target 0,95. Nol false positive pada 40 negatif berlabel tidak membuktikan nol false positive di lapangan. Test kecil dan sintetis tidak cukup untuk klaim produksi.

Pada ketiga seed, tidak ada threshold review di grid 0,01–0,99 yang memenuhi recall ≥0,95 sekaligus FPR ≤0,10 pada validation. Policy menyimpan `threshold_review=null` dan `threshold_escalate=null`, bukan memasang angka yang gagal memenuhi target. Batas ini hanya pencarian grid yang diuji; jangan mengklaim tidak ada nilai threshold secara matematis.

Akibatnya, **kebijakan gabungan saat ini mengirim seluruh 400 validation dan 80 test ke PERLU_DICEK**. Skor dan prediksi konteks tetap tersedia untuk riset; tidak ada otomatisasi “aman” atau eskalasi yang telah lolos validasi. `production_ready=false` dan `mode=shadow`. Status tersebut juga diterapkan di `config/config.yaml`.

## Diagnosis dan langkah berikutnya

- Kegagalan konteks: 6/30 risiko diprediksi normal; 6/10 konteks kurang dipetakan ke kelas lain. Laporan: 9/10 dikenali, 1 diprediksi normal.
- Skenario sulit mencakup imbalan tersirat, pengalihan rekening ke kerabat, enam digit SMS tanpa menyebut OTP, pemalsuan klaim, serta referensi percakapan yang tidak lengkap.
- Data latih laporan dan konteks kurang masih sedikit keluarga meskipun banyak variasi filler. Menambah filler bukan solusi; perlu menambah keluarga, paraphrase independen, multi-turn, role unknown, dan anotasi SOP RS.
- Untuk iterasi v4, perluas dari analisis validation dan review domain. Final test v3 sekarang telah dilihat untuk diagnosis, sehingga v4 harus memiliki test independen baru sebelum klaim peningkatan.
- Belum ada pelatihan kategori rinci/lima sinyal, pengujian mikrofon/ASR, pengukuran false alerts per jam, atau validasi pasien nyata. Tahapan P2–P9 tercantum di `../../PLAN_PROJECT.md`.

## Validasi engineering

- 15 unit tests lulus: pemisahan data, reproduksibilitas, konsistensi label, coverage, ketidakpastian, konflik output, dan pencarian threshold.
- Bank template mandiri menghasilkan keempat split identik byte-for-byte dengan data training.
- Python compilation, shell syntax dan parsing YAML diperiksa.
- Paket model memiliki hash bobot yang sama dengan checkpoint seed terpilih; schema, kalibrasi eksternal dan policy disertakan.
- Smoke inference model bundle dicatat di `smoke_inference.json`; input kosong ditolak dan input terlalu panjang dialihkan untuk review.
- Tidak mengaktifkan mikrofon, mengirim pesan MQTT, atau menginstal service systemd.

Lihat `../checkpoints/v3-s2/test_metrics.json` untuk semua kesalahan beserta prediksi dan label. Angka pada laporan ini merupakan hasil run lokal, bukan target atau hasil yang diasumsikan.
