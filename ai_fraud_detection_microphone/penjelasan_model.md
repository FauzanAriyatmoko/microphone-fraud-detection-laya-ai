# Penjelasan model Laya dan kualitas evaluasinya

Dokumentasi berdasarkan artefak lokal yang diperiksa pada **20 November 2026**. Model terpilih: **`laya-rs-v3-s2-synthetic-pilot`**. Status: **shadow, `production_ready=false`**. Semua data evaluasi masih sintetis. Dokumen ini menjelaskan hasil yang tersedia; tidak menjalankan training atau evaluasi GPU baru.

## 1. Peran model dalam project

Alurnya adalah audio mikrofon atau rekaman → segmentasi/VAD → Whisper speech-to-text → Laya → aturan keputusan → dashboard, CSV/JSONL, dan MQTT bila diaktifkan.

**Whisper mengenali ucapan; Laya menilai teks percakapan.** Laya tidak menerima gelombang suara secara langsung. Kesalahan transkripsi, pemotongan ucapan, atau hilangnya konteks bisa memengaruhi keputusan akhir meskipun model teks bekerja baik.

Paket lokal berada di [model/laya-fraud-rs-best](model/laya-fraud-rs-best/). Model merupakan fine-tuning checkpoint multilingual `convaiinnovations/laya`, dengan encoder `jhu-clsp/mmBERT-base`. Konfigurasi encoder menyebut arsitektur ModernBERT, 22 layer, hidden size 768, dan 12 attention head. Head Laya memiliki konfigurasi 2 layer. Pipeline ini menghasilkan keputusan terstruktur, bukan jawaban percakapan bebas.

Encoder menyimpan kapasitas posisi 8.192 token, tetapi **batas training dan penggunaan project adalah 256 token** untuk `max_len` dan `head_max_len`. Kapasitas encoder tidak berarti runtime project otomatis menerima konteks sepanjang itu; instruksi pertanyaan juga menggunakan anggaran token.

### Dua keluaran yang dilatih

| Keluaran | Tipe | Makna |
|---|---|---|
| `is_fraud` | `noul`, skor 0–1 | Indikasi permintaan/penawaran fraud aktif pada layanan resepsionis RS |
| `context_status` | `choice`, empat kelas | Jenis konteks percakapan |

| Kelas konteks | Makna dan contoh |
|---|---|
| `risiko_aktif` | Permintaan aktif pembayaran pribadi, pungli, rahasia autentikasi, atau pemalsuan; misalnya meminta transfer ke DANA pribadi petugas |
| `layanan_normal` | Layanan resmi, pertanyaan biasa, penolakan, atau edukasi anti-fraud; misalnya pembayaran ke kasir resmi dengan kuitansi |
| `laporan_kejadian` | Melaporkan atau mengutip dugaan kejadian lampau untuk ditindaklanjuti |
| `konteks_kurang` | Maksud, tujuan, atau kanal belum jelas; misalnya “Bayarnya ikut yang kemarin saja” |

Label fraud aktif tidak sama dengan sekadar adanya kata “transfer”, “BPJS”, atau “OTP”. Laporan kejadian dapat memuat tindakan fraud tetapi diberi `is_fraud=false` karena bukan permintaan aktif dalam definisi dataset. Kasus `konteks_kurang` tidak diberi label biner; ketidakjelasan tidak dijadikan label aman. Model belum dilatih untuk kategori rinci atau lima sinyal legacy. Definisi persis tersedia di [questions.json](model/laya-fraud-rs-best/questions.json).

## 2. Data dan pemisahan evaluasi

Sumber: [manifest dataset](training/datasets/v3/manifest.json) dan [bank template](training/datasets/source_template_bank.json).

| Split | Percakapan | Keluarga template | Risiko aktif | Normal | Laporan | Konteks kurang | Fungsi |
|---|---:|---:|---:|---:|---:|---:|---|
| Train | 1.200 | 63 | 420 | 420 | 180 | 180 | Mengubah bobot model |
| Calibration | 240 | 18 | 84 | 84 | 36 | 36 | Menyesuaikan temperature skor |
| Validation | 400 | 29 | 140 | 140 | 60 | 60 | Memilih seed dan mencari threshold |
| Test | 80 | 80 | 30 | 30 | 10 | 10 | Evaluasi akhir seed terpilih |

Pemisahan dilakukan berdasarkan keluarga template agar variasi dari keluarga yang sama tidak tersebar antarsplit. Ini mengurangi kebocoran template, tetapi tidak membuktikan generalisasi pada percakapan rumah sakit nyata.

Satu percakapan menghasilkan satu pertanyaan konteks dan, jika labelnya tersedia, satu pertanyaan biner. Akibatnya, 1.200 percakapan train menjadi **2.220 item pertanyaan training**; 400 validation menjadi **740 item** dalam laporan internal training. Evaluasi biner eksternal hanya memakai **340 validation** dan **70 test** berlabel biner. Evaluasi konteks memakai seluruh 400 dan 80 percakapan.

## 3. Proses dan parameter training

[train_all.sh](training/scripts/train_all.sh) melatih seed 0, 1, dan 2 dari base yang sama, kemudian melakukan kalibrasi dan evaluasi validation. [select_model.py](training/evaluation/select_model.py) memilih context macro-F1 tertinggi, lalu binary F1, lalu seed lebih kecil jika seri. Test tidak dipakai dalam pemilihan seed.

| Parameter run seed 2 | Nilai | Penjelasan |
|---|---:|---|
| `epochs` | 8 | Delapan putaran data training |
| `seed` | 2 | Mengatur sumber acak; seed berbeda dapat menghasilkan model berbeda |
| `micro_batch` | 8 | Item per langkah forward/backward |
| `grad_accum` | 2 | Akumulasi gradien; batch efektif nominal 16 item pada satu perangkat |
| `encoder_lr` | 0,000025 | Laju pembaruan bobot encoder |
| `head_lr` | 0,0001 | Laju pembaruan head keputusan |
| `min_lr` | 0,000001 | Batas bawah learning rate |
| `weight_decay` | 0,01 | Regularisasi bobot |
| `grad_clip` | 1,0 | Membatasi norma gradien untuk stabilitas |
| `loss` | `rlcd` | Gabungan objective policy-gradient pada logit ber-noise dan soft cross-entropy |
| `rl_samples` | 4 | Jumlah sampel noise untuk objective RLCD |
| `sigma_start` → `sigma_end` | 0,4 → 0,1 | Noise eksplorasi diturunkan sepanjang epoch |
| `w_sph`, `w_rps` | 0,75; 1,0 | Bobot komponen reward pada objective |
| `label_smoothing` | 0,05 | Melunakkan distribusi target agar tidak sepenuhnya one-hot |
| `freeze_encoder` | false | Encoder ikut diperbarui |
| `max_len`, `head_max_len` | 256; 256 | Batas token pada training |
| `calib_frac`, `calib_max` | 0; 0 | Kalibrasi internal training dinonaktifkan; menggunakan split eksternal |

Run seed 2 mencatat waktu training sekitar **194,18 detik**. Ini durasi run tersebut, bukan jaminan waktu pada perangkat lain. Provenance paket mencatat Python 3.12.3, Laya 0.4.35, PyTorch 2.15.0, dan Transformers 5.23.0.

Gunakan `run_summary.json → train_report → training` atau `calibration.json → config → training → laya_train` untuk parameter fine-tuning project. Metadata bawaan seperti `epochs_completed=4`, `updates=15987`, dan `hours=4.97` pada konfigurasi model tidak boleh dianggap sebagai durasi atau jumlah epoch fine-tuning lokal ini.

## 4. Arti metrik penilaian

Positif berarti **fraud aktif**, negatif berarti **tidak ada fraud aktif menurut label dataset**.

| Istilah | Kondisi | Dampak praktis |
|---|---|---|
| True Positive / TP | Label fraud, diprediksi fraud | Risiko terdeteksi dengan benar |
| True Negative / TN | Label non-fraud, diprediksi non-fraud | Percakapan negatif dikenali dengan benar |
| False Positive / FP | Label non-fraud, diprediksi fraud | Alarm keliru yang menambah beban pemeriksaan |
| False Negative / FN | Label fraud, diprediksi non-fraud | Risiko terlewat |

| Metrik | Rumus | Cara membaca |
|---|---|---|
| Accuracy | `(TP + TN) / N` | Proporsi seluruh prediksi benar; bisa menyesatkan jika kelas tidak seimbang |
| Precision / PPV | `TP / (TP + FP)` | Dari semua alarm fraud, berapa yang benar |
| Recall / sensitivity / TPR | `TP / (TP + FN)` | Dari semua fraud aktual, berapa yang terdeteksi |
| Specificity / TNR | `TN / (TN + FP)` | Dari semua negatif aktual, berapa yang dikenali benar |
| FPR | `FP / (FP + TN)` | Proporsi negatif yang memicu alarm keliru |
| FNR | `FN / (FN + TP)` | Proporsi fraud yang terlewat; `1 − recall` |
| NPV | `TN / (TN + FN)` | Dari prediksi non-fraud, berapa yang benar negatif |
| F1 | `2TP / (2TP + FP + FN)` | Rata-rata harmonik precision dan recall; tidak memperhitungkan TN secara langsung |
| Support | Jumlah label aktual per kelas | Menjelaskan berapa contoh yang mendasari angka |
| Macro-F1 konteks | Rata-rata F1 dari empat kelas | Setiap kelas memiliki bobot sama meskipun support berbeda |
| Balanced accuracy biner | `(recall + specificity) / 2` | Menyeimbangkan penilaian kelas positif dan negatif |

Jika penyebut nol, metrik seperti precision tidak terdefinisi; script dapat menyimpan `null`. Angka nol tidak selalu pengganti yang tepat.

### Loss, confidence, dan kalibrasi

- **Training loss RLCD** adalah objective optimasi, bukan persentase kesalahan. Nilainya bisa negatif karena memuat komponen policy-gradient. Loss negatif bukan accuracy negatif atau bukti model rusak.
- **Evaluation loss** dalam `train_report` adalah soft cross-entropy `−mean(Σ target × log(probabilitas))`. Lebih kecil menunjukkan distribusi lebih dekat ke target pada data evaluasi tersebut. Ini berbeda dari objective RLCD, sehingga keduanya tidak boleh dibandingkan langsung.
- **Mean confidence** pada laporan training adalah rata-rata probabilitas top-1. Confidence tinggi tidak menjamin jawaban benar; model bisa salah dengan sangat yakin.
- **Brier biner** pada `evaluate.py` adalah `mean((skor_fraud − label_biner)²)`. Lebih kecil lebih baik, rentang 0–1. Mengukur kesalahan probabilitas, bukan hanya benar/salah pada threshold.
- **Brier pada train_report** menjumlahkan kesalahan kuadrat seluruh komponen distribusi kelas sebelum dirata-ratakan. `brier_top1` mengukur kuadrat selisih confidence top-1 dengan indikator prediksi benar. Keduanya berbeda dari Brier biner eksternal.
- **ECE** mengukur selisih skor dan frekuensi kejadian dalam kelompok skor, dibobot jumlah contoh. Evaluator eksternal memakai 10 bin skor fraud. ECE kecil lebih baik, tetapi dipengaruhi jumlah data dan pembagian bin. ECE training memakai confidence top-1 versus ketepatan prediksi, sehingga definisinya berbeda.
- **ROC-AUC** merangkum pemisahan kelas pada berbagai threshold; **PR-AUC** merangkum trade-off precision–recall. Keduanya belum disimpan dalam laporan project, sehingga tidak ada angka aktual yang diklaim di sini. Latency p50/p95 dan false alerts per jam juga belum tersedia dari evaluasi teks ini.

## 5. Hasil training sebelum dan sesudah fine-tuning

Sumber: [run_summary.json](model/laya-fraud-rs-best/run_summary.json), bagian `train_report`. Evaluasi ini memakai 740 item pertanyaan validation, sebelum kalibrasi eksternal.

| Metrik internal | Sebelum | Sesudah |
|---|---:|---:|
| Accuracy gabungan pertanyaan | 46,89% | 89,73% |
| Evaluation loss | 1,7782 | 0,3714 |
| Mean confidence top-1 | 0,7467 | 0,9451 |
| ECE top-1 | 0,2805 | 0,0536 |
| Brier seluruh distribusi | 0,8066 | 0,1749 |
| Brier top-1 | 0,3604 | 0,0848 |
| Accuracy pertanyaan konteks (`choice`) | 48,75% | 88,00% |
| Accuracy pertanyaan biner (`noul`) | 44,71% | 91,76% |

Accuracy gabungan meningkat **42,84 poin persentase**. Angka 89,73% bukan accuracy fraud pada final test, dan tidak berarti 89,73% audio lapangan pasti dinilai benar.

Training loss per epoch seed 2: `−0,0131; −0,5006; −0,4621; −0,5106; −0,5013; −0,4998; −0,4949; −0,4052`. Kurva ini tidak terus turun; nilai epoch terakhir bukan bukti otomatis bahwa model terbaik atau sudah konvergen untuk penggunaan nyata.

## 6. Perbandingan seed pada validation

Sumber: [selection.json](training/evaluation/selection.json). Skor biner menggunakan threshold diagnostik **0,50**, sesudah kalibrasi eksternal.

| Seed | Macro-F1 konteks | Precision fraud | Recall fraud | F1 fraud | ECE biner |
|---|---:|---:|---:|---:|---:|
| 0 | 0,7774 | 100% | 73,57% | 0,8477 | 0,0708 |
| 1 | 0,8548 | 100% | 70,71% | 0,8285 | 0,0660 |
| **2** | **0,8891** | **100%** | **80,00%** | **0,8889** | **0,0532** |

Seed 2 dipilih berdasarkan validation, lalu paketnya disalin ke `model/laya-fraud-rs-best/`. Hash bobot dan provenance tersedia di [model_manifest.json](model/laya-fraud-rs-best/model_manifest.json).

## 7. Hasil final test biner dan pengaruh threshold sekarang

Laporan `test_metrics.json` memakai **0,50**. Konfigurasi runtime [config.yaml](config/config.yaml) saat dokumentasi diperiksa memakai **`decision_mode: binary`, `threshold_fraud: 0.65`**. Tabel berikut menghitung ulang threshold 0,65 dari prediksi seed 2 yang sudah tersimpan, tanpa inference atau training ulang.

| Metrik | Validation @0,50 | Validation @0,65 | Test @0,50 | Test @0,65 |
|---|---:|---:|---:|---:|
| Contoh berlabel biner | 340 | 340 | 70 | 70 |
| TP | 112 | 110 | 22 | 21 |
| FP | 0 | 0 | 0 | 0 |
| FN | 28 | 30 | 8 | 9 |
| TN | 200 | 200 | 40 | 40 |
| Accuracy | 91,76% | 91,18% | 88,57% | 87,14% |
| Precision | 100% | 100% | 100% | 100% |
| Recall | 80,00% | 78,57% | 73,33% | 70,00% |
| F1 | 0,8889 | 0,8800 | 0,8462 | 0,8235 |
| FPR | 0% | 0% | 0% | 0% |

Pada test @0,50: specificity 100%, FNR 26,67%, NPV 83,33%, dan balanced accuracy 86,67%. Pada @0,65: FNR menjadi 30%, NPV 81,63%, dan balanced accuracy 85%.

Confusion matrix test @0,50:

| Label aktual \ Prediksi | FRAUD | TIDAK_FRAUD |
|---|---:|---:|
| Fraud aktif | TP = 22 | FN = 8 |
| Non-fraud aktif | FP = 0 | TN = 40 |

Artinya, 8 dari 30 fraud aktif terlewat pada 0,50; dengan threshold sekarang, 9 terlewat. **Precision 100% pada sampel ini tidak berarti semua fraud terdeteksi atau sistem bebas alarm keliru di lapangan.** Hanya ada 40 negatif berlabel pada test.

Brier biner test **0,1041**, ECE **0,0969**; validation masing-masing **0,0712** dan **0,0532**. Brier/ECE evaluator ini dihitung dari skor tanpa threshold, sehingga tidak berubah hanya karena batas keputusan diganti.

Contoh FN yang tercatat: “Untuk BPJS-nya biar nggak ditolak, ada biaya pengurusan seratus ribu, nggak usah pakai kwitansi ya” mendapat skor 0,0791 meskipun labelnya fraud aktif. Ini menunjukkan perlunya memperluas kemampuan semantik, bukan sekadar menaikkan threshold.

## 8. Kualitas pemahaman konteks

Accuracy konteks test **83,75% (67/80)**; macro-F1 **0,7973**. Ini terpisah dari confusion matrix fraud biner.

| Label aktual \ Prediksi | Risiko aktif | Normal | Laporan | Konteks kurang |
|---|---:|---:|---:|---:|
| Risiko aktif | 24 | 6 | 0 | 0 |
| Normal | 0 | 30 | 0 | 0 |
| Laporan | 0 | 1 | 9 | 0 |
| Konteks kurang | 1 | 4 | 1 | 4 |

| Kelas | Support test | F1 test |
|---|---:|---:|
| `risiko_aktif` | 30 | 0,8727 |
| `layanan_normal` | 30 | 0,8451 |
| `laporan_kejadian` | 10 | 0,9000 |
| `konteks_kurang` | 10 | 0,5714 |

Kelemahan terbesar ada pada konteks kurang: hanya 4/10 dikenali benar. Normal dikenali 30/30, tetapi F1-nya bukan 1 karena ada contoh kelas lain yang keliru masuk normal. Dua head juga bisa bertentangan: skor fraud tinggi belum tentu menghasilkan konteks `risiko_aktif`.

## 9. Kalibrasi dan arti persentase risiko

Kalibrasi temperature menyesuaikan ketajaman distribusi dengan membagi logit menggunakan temperature sebelum softmax. Temperature di atas 1 biasanya melunakkan skor. Ini tidak melatih ulang pemahaman bahasa atau menjamin perbaikan seluruh keputusan.

[calibration.json](model/laya-fraud-rs-best/calibration.json) menyimpan temperature `[1,4211698; 1,0; 1,5261976]` untuk urutan tipe `choice`, `score`, `noul`. Tipe `score` tidak dipakai oleh dua pertanyaan project. Split calibration menghasilkan 444 item: 240 konteks dan 204 biner.

[external_calibration_report.json](model/laya-fraud-rs-best/external_calibration_report.json) menyimpan `ece_before=NaN`, `ece_after=NaN`, dan `n_eval=0`; bucket calibration dikecualikan dari evaluasi internal tersebut. **NaN berarti metrik tidak tersedia, bukan nol.** Laporan itu tidak membuktikan besar peningkatan ECE sebelum/sesudah kalibrasi eksternal. ECE validation/test yang tersedia berasal dari evaluator terpisah.

Skor `noul=0,80` ditampilkan sebagai risiko 80%. Maknanya adalah skor keluaran model untuk fraud aktif menurut definisi tugas. **Ini bukan accuracy 80%, bukan tingkat keparahan kerugian, dan belum dapat dianggap peluang kejadian nyata 80% yang tervalidasi.**

`confidence` dan `answer_confidence` merupakan field terpisah dari skor fraud. Misalnya skor fraud sekitar 0,079 dapat disertai confidence sekitar 0,921 karena model yakin pada arah negatif. Untuk konteks, gunakan `choice` dan distribusi `probabilities`; jangan menganggap seluruh field confidence identik dengan probabilitas kelas yang dipilih. Mean confidence pada `train_report` secara khusus dihitung dari probabilitas top-1.

## 10. Hubungan kualitas model dengan aturan runtime

Pada mode **binary**, skor valid `>= threshold_fraud` menjadi `FRAUD`; skor lebih rendah menjadi `TIDAK_FRAUD`. Dengan konfigurasi saat ini, batasnya **65%**. Konteks tetap informasi diagnostik, bukan gerbang keputusan biner. Input tidak layak dinilai, misalnya kegagalan kualitas audio/teks, dapat menghasilkan `TIDAK_DAPAT_DINILAI` dan `is_fraud=null`; itu tidak dihitung sebagai non-fraud.

Menaikkan threshold umumnya mengurangi atau mempertahankan jumlah alarm, dengan risiko menambah FN. Menurunkannya umumnya meningkatkan atau mempertahankan recall, dengan risiko menambah FP. Perubahan perlu diukur pada data yang mewakili penggunaan, bukan diasumsikan selalu membaik.

Policy asli model adalah kebijakan **tiered** dengan `threshold_review=null`, `threshold_escalate=null`, dan confidence konteks 0,70 yang masih provisional. Pencarian grid validation 0,01–0,99 tidak menemukan threshold review dengan recall ≥95% sekaligus FPR ≤10%. Gate eskalasi juga mensyaratkan precision ≥95%, TP ≥20, recall ≥50%, dan threshold tidak di bawah review.

Laporan historis [RESULTS.md](training/evaluation/RESULTS.md) menyatakan semua input masuk `PERLU_DICEK` pada **policy tiered asli** tersebut. Itu bukan perilaku mode binary dengan override YAML sekarang. Mengaktifkan binary memudahkan pengujian, tetapi tidak membuat model lulus gate kualitas atau mengubah `production_ready=false`.

## 11. Penilaian kesiapan dan evaluasi berikutnya

Model menunjukkan peningkatan terhadap base pada data sintetis, tetapi **belum memenuhi target pilot**: macro-F1 konteks test 0,7973 berada di bawah 0,85 dan recall fraud @0,50 sebesar 73,33% berada di bawah 95%. Pada threshold sekarang recall test menjadi 70%.

Batas bukti yang tersedia:

- Data berasal dari template sintetis; SOP, variasi bahasa, peran pembicara, dan distribusi percakapan RS nyata belum tervalidasi.
- Test kecil; angka 100% precision atau 0% FPR belum stabil untuk klaim operasional.
- Evaluasi teks tidak mengukur kesalahan Whisper, noise, overlapping speech, atau akurasi pipeline audio secara keseluruhan. Pengujian engineering upload/mikrofon bukan pengganti dataset audio berlabel.
- Inference batch test sekitar 5,40 ms/kasus, batch size 16; ini bukan latency p95 satu ucapan atau waktu audio sampai keputusan.
- Test v3 sudah dibuka dan dipakai untuk diagnosis. Iterasi berikutnya perlu test independen baru; jangan memilih threshold dari test ini lalu mengklaim hasil final tetap independen.

Langkah evaluasi berikutnya: perluas keluarga skenario dan paraphrase independen, tambah konteks multi-turn dan peran pembicara, review label bersama domain RS, kumpulkan audio berizin yang mewakili lingkungan penggunaan, ukur WER/CER ASR serta kualitas keputusan end-to-end, lalu ukur recall/FPR, precision, kalibrasi, false alerts per jam, coverage penolakan, dan latency p50/p95. Sertakan interval ketidakpastian agar perubahan pada sampel kecil tidak ditafsirkan berlebihan.

## 12. Peta artefak dan cara audit

| Lokasi | Fungsi |
|---|---|
| `model/laya-fraud-rs-best/model.safetensors` | Bobot paket yang digunakan runtime |
| `encoder/`, `tokenizer/`, `rl_agent_config.json` dalam paket | Konfigurasi arsitektur, tokenizer, dan head Laya |
| `questions.json` | Definisi dua tugas dan kelas konteks |
| `calibration.json` | Temperature dan metadata model |
| `decision_policy.json` | Policy tiered asli; dapat ditimpa YAML runtime |
| `model_manifest.json`, `run_environment.json` | Versi, hash, asal checkpoint, dan lingkungan run |
| `run_summary.json`, `train_report.json` | Parameter, loss epoch, dan hasil sebelum/sesudah training |
| `validation_metrics.json`, `test_metrics.json` | Ringkasan metrik dan daftar kasus salah |
| `training/checkpoints/v3-s*/` | Checkpoint dan artefak masing-masing seed |
| `training/checkpoints/v3-s2/test_predictions.jsonl` | Prediksi setiap kasus test, termasuk kasus benar |
| `training/evaluation/evaluate.py` | Rumus metrik, kalibrasi, dan pencarian threshold |
| `training/evaluation/selection.json` | Perbandingan seed dan alasan pemilihan |
| `training/evaluation/combined_policy_counts.json` | Hasil policy gabungan historis, bukan binary YAML sekarang |
| `backend/internal/ai_runtime/ai_fraud_detection.py` | Validasi skor dan penerapan aturan runtime |

Pada `test_predictions.jsonl`, baca `state` sebagai input, `expected` sebagai label, lalu `result.answers.is_fraud.noul` sebagai skor dan `result.answers.context_status.choice` sebagai prediksi konteks. `usage` menunjukkan pemakaian token/truncation. Label `expected` adalah referensi evaluasi, bukan jawaban model. CSV/JSONL runtime tanpa label manual tidak cukup untuk menghitung accuracy, TP, atau FN.

Untuk memeriksa konfigurasi efektif tanpa training:

```bash
python3 ai_fraud_detection_microphone/backend/app/main.py --check-config
```

Untuk menghitung ulang metrik dari prediksi tersimpan, jalankan dari root repository; tidak membutuhkan GPU dan tidak mengubah artefak:

```python
import importlib.util
import json
from pathlib import Path

root = Path("ai_fraud_detection_microphone")
spec = importlib.util.spec_from_file_location(
    "evaluation", root / "training/evaluation/evaluate.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
rows = evaluation.read(root / "training/datasets/v3/test.jsonl")
predictions = [item["result"] for item in evaluation.read(
    root / "training/checkpoints/v3-s2/test_predictions.jsonl"
)]
for threshold in (0.50, 0.65):
    report = evaluation.metrics(rows, predictions, threshold=threshold)
    binary = report["binary"]
    binary["accuracy"] = (binary["tp"] + binary["tn"]) / sum(
        binary[key] for key in ("tp", "tn", "fp", "fn")
    )
    print(threshold, json.dumps(binary, indent=2))
```

Mengubah `threshold_fraud` di YAML tidak menulis ulang laporan evaluasi lama. Setiap laporan kualitas baru perlu menyebut dataset, versi model, kalibrasi, mode keputusan, dan threshold yang benar-benar digunakan.
