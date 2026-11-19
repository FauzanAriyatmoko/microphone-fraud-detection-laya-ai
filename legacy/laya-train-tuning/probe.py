import sys, laya

Q = {"is_fraud": {"type": "noul",
     "instructions": "Apakah ucapan resepsionis ini indikasi fraud atau pungutan tidak resmi?"}}
PROBES = [  # (label_yang_benar, kalimat)
 (True,  "Supaya akun Bapak tidak terblokir, sebutkan kode OTP-nya sekarang ya"),
 (True,  "Supaya akun Bapak tidak terblokir, sebutkan kode OTP-nya ke saya sekarang ya"),
 (True,  "Untuk dapat surat rujukan lebih cepat, selipkan dua ratus ribu di berkas ya Pak"),
 (True,  "Untuk dapat surat rujukan lebih cepat, selipkan dua ratus ribu di berkas ke saya ya Pak"),
 (True,  "Uang administrasinya diserahkan ke Pak Budi di ruang belakang ya Bu"),
 (True,  "Transfer ke rekening 1234567890 a.n. Dedi ya Bu, nanti saya catat"),
 (False, "Pesan dari bank berisi kode SMS itu rahasia Bu, jangan dibacakan ke saya maupun orang lain"),
 (False, "Pesan dari bank berisi kode SMS itu rahasia Bu, jangan dibacakan ke siapa pun"),
 (False, "Silakan bayar ke kasir ya Bu, jangan bayar ke saya"),
 (False, "Saya hanya membantu pendaftaran Bu, pembayaran di kasir lantai satu"),
]
for ck in sys.argv[1:]:
    agent = laya.load(ck)
    res = agent.predict_batch([t for _, t in PROBES], Q, batch_size=16)
    print("\n==", ck)
    for (gold, t), r in zip(PROBES, res):
        p = r["answers"]["is_fraud"]["noul"]
        print(f"  {p:.3f} {'✓' if (p>=.5)==gold else '✗'} gold_fraud={gold!s:5} {t}")