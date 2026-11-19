# Pemeriksaan implementasi dan perbandingan rencana

Tanggal pemeriksaan: 19 November 2026, Asia/Jakarta. Cakupan: melanjutkan pipeline sesuai permintaan pengguna; peningkatan kualitas model ditunda. Referensi dibandingkan: `PLAN_PROJECT.md` dan `../laya_ai_fraud_detection_planning.md`.

## Kesimpulan

Implementasi sebelumnya sudah membentuk worker capture, segmenter, STT, Laya, audit, outbox MQTT, CLI, dan template systemd, tetapi belum selesai diverifikasi sebagai satu sistem. Dokumen `PLAN_PROJECT.md` masih menyebut banyak file sebagai placeholder meskipun file tersebut sudah diimplementasikan. Runtime CUDA/PortAudio, dependency lock, panduan menjalankan, dan installer systemd memerlukan penyelesaian.

Pipeline sekarang berhasil diuji menggunakan WAV legacy, **Faster-Whisper Large-v3 dan Laya pada GPU asli**, audit harian, serta **broker MQTT loopback**. Subscriber menerima 2 event transkripsi dan 2 event keputusan; outbox habis setelah ACK, lalu status offline terkirim saat shutdown. Angka ini adalah bukti integrasi, bukan ukuran akurasi model atau benchmark realtime loket.

## Perbandingan kedua dokumen

| Aspek | PLAN_PROJECT.md | laya_ai_fraud_detection_planning.md | Pilihan implementasi |
|---|---|---|---|
| Arah produk | Daemon Ubuntu, loket RS, MQTT | Sama | Pertahankan fokus headless dan entrypoint tunggal |
| Struktur worker | Capture, segmenter, STT, Laya, bounded queue | Diagram lebih ringkas | Gunakan struktur rinci agar Whisper tidak menghentikan capture |
| Konteks | Empat kelas termasuk laporan dan ambigu | Noul + kategori/sinyal | Pakai kemampuan model yang tersedia; dukungan kategori/sinyal menjadi perubahan schema/model berikutnya |
| Payload keputusan | Context, evidence, policy version | Menambahkan action recommendation | Tambahkan rekomendasi deterministik berdasarkan level/konteks |
| Health | Queue, latency, drops, outbox | Contoh device/RMS/GPU temperature | Gabungkan: identitas perangkat, RMS, GPU temperature/memory/utilization, queue age dan ACK |
| Logging | Retensi, permission, redaksi | Transkrip lengkap untuk analisis | Sediakan mode omit/masked/raw; default omit, mode raw bisa dipilih saat pengumpulan data |
| Confidence STT | Metadata kualitas dengan definisi | Contoh confidence 0,96 | Gunakan no_speech/logprob/VAD, tidak mengisi angka confidence yang tidak diukur |
| MQTT offline | Persistensi, ACK, TTL, kuota, dedup | Auto reconnect/buffer umum | Pertahankan outbox persisten dan exponential backoff + jitter |
| Threshold | Dibaca dari bundle terversi | Nilai tetap 0,50/0,85 | Bundle policy dipakai tanpa mengubah bobot atau training pada tahap runtime |
| Systemd | Lifecycle dan timeout lebih rinci | Contoh service sederhana | Gunakan installer idempotent, launcher bersama, timeout dan restart |
| Uji akhir | Replay/integrasi/soak/pilot | systemctl dan broker target | Replay/model/broker lokal selesai; uji mikrofon target, systemd aktif, soak dan broker target masih perlu lingkungan deployment |

Dokumen pembanding merupakan baseline yang lebih ringkas, bukan pengganti seluruh rencana yang telah disempurnakan. Bagian operasional yang relevan sudah digabung; angka payload dan kemampuan model tidak dianggap sebagai hasil pengukuran.

## Kesesuaian implementasi terhadap arsitektur

| Komponen | File | Status dan bukti |
|---|---|---|
| Entrypoint | `backend/app/main.py` | CLI config/device/replay, dry-run, duration, SIGINT/SIGTERM dan SIGUSR1 |
| Orkestrasi | `backend/app/ai_manager.py` | Empat worker, bounded queues, drain, error propagation, inference watchdog |
| Audio | `backend/internal/utils/audio.py` | PCM normalization, mono/resample, prebuffer, endpoint, continuation, gap detection |
| STT | `backend/internal/ai_runtime/background_process/speech_to_text.py` | Model sekali load, VAD, segment quality, konsumsi generator, latency |
| Laya | `backend/internal/ai_runtime/background_process/laya_ai_process.py` | Model/schema/calibration/policy dipakai bersama, input panjang masuk review |
| Policy | `backend/internal/ai_runtime/ai_fraud_detection.py` | Keputusan konteks dan ketidakpastian; tidak menghalangi daemon karena metrik model |
| Config | `backend/internal/utils/config_loader.py` | Path project-root, model schema/weights verification, env override, validasi batas |
| Audit | `backend/internal/utils/logger.py` | JSON stdout, JSONL harian, timezone, mode transkrip, retensi dan permission |
| MQTT | `backend/internal/client/mqtt_client.py` | SQLite, dedup, TTL, batas payload/row, ACK, reconnect, LWT dan retained status |
| Telemetry | `backend/internal/utils/health.py` | Host GPU opsional, queue depth/age; device dan runtime stats dikirim manager |
| Packaging | `backend/requirements.txt`, `requirements.lock.txt` | Dependency langsung dan closure versi environment lokal |
| Setup | `scripts/setup_runtime.sh`, `run_standalone.sh` | Setup Python, opsi native PortAudio/cuBLAS, library discovery pada launcher |
| Service | `systemd/`, `scripts/install_service.sh` | Template dan hasil rendering diverifikasi systemd; pemasangan ulang identik tidak menulis ulang |
| Subscriber | `scripts/test_mqtt_sub.py` | Memeriksa envelope event pada topic konfigurasi |
| REST | `backend/internal/delivery/` | Sengaja tidak diaktifkan; tidak ada kebutuhan REST pada pipeline MQTT |

## Perbaikan yang dilakukan pada kelanjutan ini

1. Menyelesaikan dependency CUDA 12 cuBLAS pada direktori lokal, sehingga runtime CTranslate2 berjalan bersama PyTorch tanpa mengganti library PyTorch.
2. Menyelesaikan PortAudio lokal dan memastikan device discovery dapat dijalankan.
3. Membuat systemd memakai launcher yang sama seperti manual run; memperbaiki rendering WorkingDirectory/EnvironmentFile dan peletakan Environment.
4. Menambah GPU/device health, queue age, pemisahan latency endpointing/ASR/Laya/queue, dan action recommendation.
5. Memindahkan RMS calculation keluar callback mikrofon dan menambahkan rejection untuk metadata STT non-finite.
6. Menambah watchdog inference, pengujian drain shutdown dan pengujian integrasi GPU opt-in yang dapat dijalankan ulang.
7. Mengunci dependensi, menyiapkan setup script, README operasional dan bukti hasil pengujian.

## Pengujian dan batas hasil

Total **36 pengujian lulus**: 20 runtime/broker, 1 GPU end-to-end yang dijalankan eksplisit, dan 15 invariant training. Test GPU dilewati secara default pada discovery biasa agar tidak memuat model besar tanpa permintaan.

- Unit/integration runtime: audio silence/noise, PCM/resampling, prebuffer, max duration, audio gaps, validasi konfigurasi, audit/redaksi/retensi, outbox persistence/order/quota/expiry, replay drain, ASR rejection, worker failure, watchdog dan stop request.
- Broker fixture loopback: ACK, offline queue, restart publisher, reconnect setelah broker restart, dan offline status saat shutdown.
- GPU end-to-end: WAV 15 detik legacy → segmentasi → Whisper Large-v3 → Laya → audit → MQTT subscriber/ACK.
- Training invariant tests tetap dijalankan untuk memastikan perubahan runtime tidak merusak dataset/policy sebelumnya.
- `pip check`, compile Python, shell syntax, bundle hash verification, serta `systemd-analyze verify` dijalankan. Host memiliki warning terpisah tentang service `camera_tools.service` yang world-writable; file tersebut bukan bagian proyek ini dan tidak diubah.

Bukti terstruktur lokal: `tests/artifacts/e2e_mqtt_events.json`, `tests/artifacts/e2e.log`, dan hasil test tersimpan dalam `tests/artifacts/`. Event menggunakan mode omit sehingga tidak menyimpan transkrip asli. Broker fixture terpisah pada `.env_laya_broker` agar pin dependensinya tidak bertabrakan dengan environment model.

Batas deployment yang belum diverifikasi:

- Mikrofon konfigurasi index 9/C270 tidak tersedia di host saat audit. `--list-devices` hanya menemukan pulse/default; `--check-device` untuk index 9 gagal secara eksplisit. Nama perangkat target harus dipilih ketika hardware tersedia.
- Template service sudah diverifikasi, tetapi belum dipasang/dinyalakan sebagai service OS. Instalasi membutuhkan hak admin host; tidak ada perubahan `/etc/systemd/system` yang dilakukan.
- MQTT target `192.168.100.2` belum diuji; seluruh pengujian memakai broker lokal yang diisolasi.
- Soak test 8 jam, cabut/pasang hardware, ground truth transkripsi dan pengukuran latency p95 realtime belum dilaksanakan. Replay offline tidak menjadi bukti angka latency tersebut.
- Masking mode masked hanya best effort angka/kontak. Mode omit adalah default yang digunakan dalam pengujian.

Langkah deployment dan seluruh perintah reproduksi tersedia di [README.md](README.md). Tidak ada training tambahan atau perubahan bobot model pada kelanjutan runtime ini.
