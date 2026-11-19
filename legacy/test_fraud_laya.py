import json
import os
import warnings

# Matikan warning kalibrasi dan progress bar Hugging Face
warnings.filterwarnings("ignore", category=RuntimeWarning)
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

from laya import Router

router = Router(preload=True)

state = {
    "speaker": "receptionist",
    "utterance": "Halo ibu untuk pendaftaran pengobatan selanjutnya bisa langsung "
                 "transfer ke saya saja, agar tidak perlu menunggu antrian terlalu lama ya",
}

questions = {
    "is_fraud": {
        "type": "noul",
        "instructions": "Apakah ucapan resepsionis ini indikasi fraud atau pungutan tidak resmi?",
    },
    "fraud_type": {
        "type": "choice",
        "instructions": "Jenis indikasi fraud apa yang paling sesuai?",
        "criteria": {
            "pembayaran_pribadi": "meminta pembayaran ditransfer ke rekening atau nomor pribadi",
            "jalur_cepat_berbayar": "menawarkan percepatan antrian atau layanan dengan imbalan uang",
            "data_sensitif": "meminta OTP, PIN, atau data rahasia pasien",
            "biaya_tidak_resmi": "menyebut biaya di luar tarif resmi rumah sakit",
            "aman": "percakapan layanan normal tanpa indikasi fraud",
        },
    },
    "severity": {
        "type": "score",
        "instructions": "Seberapa serius indikasi fraud ini?",
        "criteria": ["tidak ada", "mencurigakan", "sangat jelas fraud"],
    },
}

result = router.predict(state, questions)

# Cetak hasil prediksi dalam format JSON rapi
print("\n=== HASIL DETEKSI FRAUD ===")
print(json.dumps(result["answers"], indent=2, ensure_ascii=False))