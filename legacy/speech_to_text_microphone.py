import queue
import sys
from collections import deque
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

# --- Konfigurasi ---
fs = 16000
device_index = 9
block_duration = 0.1         # Baca audio tiap 100 ms (0.1 detik)
silence_threshold = 0.025    # Ambang batas volume suara bicara
silence_timeout = 0.6        # Jeda hening (detik) untuk dianggap 1 kalimat selesai
min_speech_duration = 0.5    # Abaikan suara singkat di bawah 0.5 detik (batuk/ketukan)
max_speech_duration = 15.0   # Batas maksimal 1 kalimat jika bicara tanpa henti
pre_buffer_duration = 0.4    # Simpan 0.4 detik sebelum suara mulai agar awal kata utuh

HALLUCINATION_PHRASES = {
    "terima kasih",
    "terima kasih telah menonton",
    "terima kasih sudah menonton",
    "jangan lupa like, share dan subscribe",
    "jangan lupa like, share, dan subscribe",
    "subscribe",
    "selamat menonton",
}

print("Memuat model Whisper large-v3 di GPU...")
model = WhisperModel("large-v3", device="cuda", compute_type="float16")

audio_queue = queue.Queue()

def audio_callback(indata, frames, time, status):
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(indata.copy())

def is_hallucination(text: str) -> bool:
    cleaned = text.strip().lower().rstrip(".!,?")
    return cleaned in HALLUCINATION_PHRASES

def transcribe_sentence(audio_data: np.ndarray):
    """Mentranskripsi satu kalimat utuh."""
    segments, _ = model.transcribe(
        audio_data,
        language="id",
        vad_filter=True,
        vad_parameters=dict(
            threshold=0.6,
            min_silence_duration_ms=400
        ),
        beam_size=2,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        condition_on_previous_text=False
    )

    valid_texts = []
    for s in segments:
        t = s.text.strip()
        if t and not is_hallucination(t) and s.no_speech_prob < 0.6:
            valid_texts.append(t)

    final_text = " ".join(valid_texts)
    if final_text:
        print(f"\r>> {final_text}")

def main():
    block_samples = int(block_duration * fs)
    pre_buffer_blocks = int(pre_buffer_duration / block_duration)
    
    # Menyimpan potongan audio sesaat sebelum pengguna mulai bicara
    pre_buffer = deque(maxlen=pre_buffer_blocks)
    
    speech_buffer = []
    is_speaking = False
    silence_time = 0.0
    speech_time = 0.0

    print("Mulai mendengarkan... Bicaralah secara alami, berhenti sejenak untuk mengakhiri kalimat (Ctrl+C untuk berhenti)\n")

    try:
        with sd.InputStream(
            samplerate=fs,
            channels=1,
            dtype="float32",
            blocksize=block_samples,
            device=device_index,
            callback=audio_callback
        ):
            while True:
                indata = audio_queue.get().flatten()
                rms = np.sqrt(np.mean(indata**2))

                if rms > silence_threshold:
                    if not is_speaking:
                        # Baru mulai bicara: masukkan pre-buffer agar awal kata tidak terpotong
                        is_speaking = True
                        speech_buffer.extend(pre_buffer)
                        pre_buffer.clear()
                        print("Mendengarkan...", end="", flush=True)

                    speech_buffer.append(indata)
                    speech_time += block_duration
                    silence_time = 0.0  # Reset hitungan hening karena masih bicara

                    # Jika bicara terlalu panjang tanpa henti, paksa proses
                    if speech_time >= max_speech_duration:
                        full_audio = np.concatenate(speech_buffer)
                        transcribe_sentence(full_audio)
                        speech_buffer = []
                        is_speaking = False
                        speech_time = 0.0
                        silence_time = 0.0

                else:
                    if is_speaking:
                        # Tetap rekam sedikit keheningan di jeda antar-kata
                        speech_buffer.append(indata)
                        silence_time += block_duration

                        # Jika jeda hening sudah melewati batas (kalimat selesai)
                        if silence_time >= silence_timeout:
                            if speech_time >= min_speech_duration:
                                full_audio = np.concatenate(speech_buffer)
                                transcribe_sentence(full_audio)
                            else:
                                # Hapus indikator "Mendengarkan..." jika hanya noise singkat
                                print("\r               \r", end="", flush=True)

                            # Reset status untuk kalimat berikutnya
                            speech_buffer = []
                            is_speaking = False
                            speech_time = 0.0
                            silence_time = 0.0
                    else:
                        # Simpan ke pre-buffer saat kondisi diam
                        pre_buffer.append(indata)

    except KeyboardInterrupt:
        print("\nSelesai.")

if __name__ == "__main__":
    main()