import sounddevice as sd
import numpy as np
from scipy.io.wavfile import write

# Konfigurasi rekaman
fs = 16000  # Sample rate (Hz)
seconds = 15  # Durasi rekaman dalam detik
device_index = 9  # Ganti dengan index mikrofon C270 Anda dari fungsi sd.query_devices()

print("Merekam...")
audio_data = sd.rec(int(seconds * fs), samplerate=fs, channels=1, device=device_index)
sd.wait()  # Tunggu sampai rekaman selesai
print("Selesai merekam!")

# Simpan ke file audio
write("test_output_new.wav", fs, audio_data)