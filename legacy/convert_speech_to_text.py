import numpy as np
from scipy.io.wavfile import read
from faster_whisper import WhisperModel

model = WhisperModel("large-v3", device="cuda", compute_type="float16")

fs, audio = read("test_output_new.wav")
audio = audio.astype(np.float32)
if audio.ndim > 1:
    audio = audio.mean(axis=1)

# Normalisasi ke rentang -1..1 sesuai tipe data aslinya
if np.abs(audio).max() > 1.0:
    audio = audio / 32768.0

segments, info = model.transcribe(audio, language="id", vad_filter=True, beam_size=10, initial_prompt="Percakapan bahasa Indonesia sehari-hari.")
for seg in segments:
    print(f"[{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}")