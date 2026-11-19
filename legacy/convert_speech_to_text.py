import ctypes
import os
import sys
from pathlib import Path

# Load shared libraries CUDA 12 (libcublasLt.so.12 & libcublas.so.12)
# Diperlukan oleh CTranslate2 / faster-whisper dengan flag RTLD_GLOBAL
def load_cuda_libraries():
    search_dirs = [
        Path(__file__).resolve().parent.parent / ".cuda12",
        Path(__file__).resolve().parent / ".cuda12",
    ]
    for p in sys.path:
        if "site-packages" in p:
            search_dirs.append(Path(p) / "nvidia")

    # libcublasLt harus dimuat sebelum libcublas
    libs_to_load = ["libcublasLt.so.12", "libcublas.so.12"]
    for lib_name in libs_to_load:
        for directory in search_dirs:
            if not directory.exists():
                continue
            matches = list(directory.rglob(f"{lib_name}*"))
            loaded = False
            for match in matches:
                if match.is_file():
                    try:
                        ctypes.CDLL(str(match), mode=ctypes.RTLD_GLOBAL)
                        loaded = True
                        break
                    except Exception:
                        pass
            if loaded:
                break

load_cuda_libraries()

import numpy as np
from scipy.io.wavfile import read
from faster_whisper import WhisperModel

model = WhisperModel("large-v3", device="cuda", compute_type="float16") # using GPU
# for using cpu model = WhisperModel("large-v3", device="cpu", compute_type="int8")

# Cari file audio baik di direktori kerja saat ini maupun di sebelah file script
audio_file = Path("test_output_pembayaran.wav")
if not audio_file.exists():
    audio_file = Path(__file__).resolve().parent / "test_output_pembayaran.wav"

fs, audio = read(str(audio_file))
audio = audio.astype(np.float32)
if audio.ndim > 1:
    audio = audio.mean(axis=1)

# Normalisasi ke rentang -1..1 sesuai tipe data aslinya
if np.abs(audio).max() > 1.0:
    audio = audio / 32768.0

segments, info = model.transcribe(
    audio,
    language="id",
    vad_filter=True,
    beam_size=10,
    initial_prompt="Percakapan bahasa Indonesia sehari-hari."
)
for seg in segments:
    print(f"[{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}")