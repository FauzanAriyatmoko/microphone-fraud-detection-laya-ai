"""Generator dataset sintetis fraud resepsionis RS untuk laya-train (JSONL, pertanyaan noul).

Split dilakukan di level TEMPLATE: template di held-out tidak pernah muncul di train,
jadi evaluasi menguji generalisasi ke frasa baru, bukan hafalan kalimat.
Jalankan:  python gen_fraud_dataset.py
Output  :  train_synth.jsonl, heldout_synth.jsonl, meta_synth.csv
"""
import csv, json, random, re

SEED = 42
N_TRAIN_PER_CLASS = 200     # total train = 2x ini (+ baris asli dari CSV-mu)
N_HELD_PER_CLASS = 60       # total held-out = 2x ini

QUESTIONS = {"is_fraud": {"type": "noul",
    "instructions": "Apakah ucapan resepsionis ini indikasi fraud atau pungutan tidak resmi?"}}

F = {
    "sapa": ["Bu", "Pak", "Ibu", "Bapak", "Mbak", "Mas", "Kak"],
    "biaya": ["biaya administrasi", "biaya pendaftaran", "uang muka", "biaya pemeriksaan",
              "biaya rawat inap", "biaya konsultasi", "biaya laboratorium", "biaya operasi"],
    "rek_pribadi": ["nomor rekening pribadi saya", "rekening saya", "rekening atas nama saya sendiri",
                    "nomor DANA saya", "nomor GoPay saya", "nomor e-wallet saya", "rekening BCA saya"],
    "layanan": ["pendaftaran", "pemeriksaan", "rawat jalan", "pengambilan hasil lab", "konsultasi dokter",
                "rontgen", "pengurusan BPJS"],
    "nominal": ["lima puluh ribu", "seratus ribu", "dua ratus ribu", "tiga ratus ribu", "Rp75 ribu", "Rp150 ribu"],
    "obat": ["Obat ini", "Obat dari dokter ini", "Resep ini", "Vitamin dan obat ini", "Obat tetes ini", "Obat diabetes ini"],
    "kanal_resmi": ["QRIS resmi rumah sakit", "virtual account yang tertera di tagihan", "mesin pembayaran mandiri",
                    "transfer ke rekening resmi rumah sakit yang tertera di tagihan", "kartu debit di kasir"],
    "poli": ["anak", "penyakit dalam", "gigi", "mata", "kandungan", "jantung", "saraf"],
    "data": ["password m-banking", "kode CVV kartu", "PIN ATM", "kode verifikasi"],
    "jam": ["delapan pagi", "sepuluh pagi", "satu siang", "tiga sore"],
    "lantai": ["satu", "dua", "tiga", "empat"],
    "nomor": ["dua belas", "dua puluh tiga", "tiga puluh lima", "empat puluh satu"],
}

# (label, kategori, [template...])  -- fraud=True
FRAUD = {
 "pembayaran_pribadi": [
   "Silakan transfer {biaya} ke {rek_pribadi} ya, {sapa}",
   "Untuk {biaya} bisa langsung dikirim ke {rek_pribadi}, {sapa}",
   "{sapa}, {biaya} lewat {rek_pribadi} saja biar cepat diproses",
   "Kirim {biaya} ke {rek_pribadi} dulu ya, nanti saya daftarkan",
   "Pembayaran {biaya} nggak usah ke kasir, langsung ke {rek_pribadi} saja {sapa}",
   "Nanti {biaya} ditransfer ke {rek_pribadi} supaya {sapa} tidak perlu antre di kasir",
 ],
 "jalur_cepat": [
   "Kalau mau {layanan} lebih cepat, {sapa} bisa kasih uang tambahan {nominal} ke saya",
   "Supaya {sapa} tidak perlu mengantri, bayar saja langsung ke saya",
   "Untuk {layanan} lewat jalur cepat ada biaya {nominal} yang diserahkan ke saya langsung",
   "Antriannya panjang {sapa}, tapi kalau ada uang pelicin {nominal} saya masukkan duluan",
   "Biar {layanan} diprioritaskan, {sapa} cukup titip {nominal} ke saya",
 ],
 "data_sensitif": [
   "Boleh minta kode OTP yang baru masuk ke HP {sapa}? untuk saya proses lebih lanjut",
   "Tolong sebutkan {data} {sapa} ya, untuk verifikasi di komputer saya",
   "Bagi nomor rekening {sapa} untuk saya simpan secara pribadi, nanti saya kirimi {nominal}",
   "{sapa}, bacakan kode OTP yang barusan masuk, biar saya yang masukkan",
   "Untuk {layanan} saya butuh {data} {sapa}, kirim lewat WhatsApp pribadi saya saja",
 ],
 "obat_langsung": [
   "{obat} bisa langsung ditebus ke saya, {sapa}, tidak perlu antri di apotek",
   "Untuk {obat} bayar ke saya saja {sapa}, nanti saya antar ke rumah",
   "{obat} lebih murah kalau beli lewat saya, transfer saja ke {rek_pribadi}",
   "Bisa minta alamat dan nomor rekening {sapa}, nanti saya antar obatnya",
 ],
 "biaya_tidak_resmi": [
   "Ada biaya tambahan {nominal} untuk {layanan}, tidak masuk struk, bayar tunai ke saya saja",
   "Biar klaim BPJS-nya cepat cair, {sapa} kasih uang {nominal} ke saya",
   "Untuk {layanan} ada biaya di luar tarif, {nominal}, diserahkan ke saya jangan ke kasir ya {sapa}",
   "{sapa} mau {layanan} gratis? bisa, asal bayar {nominal} ke saya dulu",
 ],
}
SAFE = {
 "pembayaran_resmi": [
   "Pembayaran {biaya} bisa di kasir lantai {lantai} atau lewat {kanal_resmi} ya, {sapa}",
   "{sapa}, {biaya} dibayarkan lewat {kanal_resmi}, rinciannya ada di tagihan",
   "Untuk {biaya} silakan ke kasir, jangan transfer ke rekening pribadi petugas siapa pun ya {sapa}",
   "Pembayaran hanya melalui {kanal_resmi}, kami tidak menerima pembayaran ke rekening pribadi",
   "Tagihan {biaya} sudah tercetak di lembar ini, {sapa} bayar di kasir ya",
 ],
 "otp_aman": [
   "Kode OTP yang masuk ke HP {sapa} jangan diberikan ke siapa pun, termasuk petugas ya",
   "{sapa} masukkan sendiri kode OTP-nya di aplikasi resmi rumah sakit ya",
   "Kalau ada yang meminta {data} {sapa}, jangan diberikan ya, kami tidak pernah memintanya",
   "Untuk {layanan} {sapa} cukup login sendiri di aplikasi, kami tidak perlu kode apa pun",
 ],
 "antrian_resmi": [
   "Nomor antrian {sapa} {nomor}, silakan tunggu dipanggil di ruang tunggu",
   "Untuk {layanan} lebih cepat, {sapa} bisa daftar online lewat aplikasi resmi sebelum datang",
   "Antriannya memang panjang {sapa}, mohon menunggu, urutannya sesuai nomor antrian",
   "Kami tidak menerima titipan uang untuk mempercepat antrian ya {sapa}, semua sesuai nomor",
   "{sapa} bisa ambil nomor antrian di mesin dekat pintu masuk untuk {layanan}",
 ],
 "obat_resmi": [
   "{obat} bisa ditebus di apotek rumah sakit lantai {lantai}, {sapa}",
   "{obat} harus ditebus di apotek luar ya, di dalam sini tidak ada apotek",
   "Untuk {obat} {sapa} bawa resepnya ke apotek, bayar di kasir apotek ya",
   "{obat} tersedia di apotek depan, bisa pakai BPJS atau bayar langsung di sana",
 ],
 "data_dan_biaya_resmi": [
   "Mohon bawa kartu identitas dan kartu BPJS asli untuk {layanan}, {sapa}",
   "Kami hanya meminta data di formulir pendaftaran resmi, {sapa}",
   "{biaya} sesuai tarif resmi, rinciannya ada di struk, {sapa}",
   "Tidak ada biaya tambahan di luar struk ya {sapa}, semua tercantum di tagihan",
   "Klaim BPJS diproses langsung oleh rumah sakit, {sapa} tidak perlu membayar apa pun ke petugas",
 ],
 "info_umum": [
   "Silakan ke poli {poli} lantai {lantai}, {sapa}",
   "Jadwal dokter poli {poli} hari ini mulai jam {jam}, {sapa}",
   "{sapa}, hasil {layanan} bisa diambil besok jam {jam}",
   "Ruang tunggu poli {poli} ada di sebelah kanan, {sapa} silakan duduk dulu",
 ],
}

def fill(t, rng):
    return re.sub(r"\{(\w+)\}", lambda m: rng.choice(F[m.group(1)]), t)

def asr(text, rng):
    t = re.sub(r"[,.?!]", "", text.lower())
    if rng.random() < 0.4:
        t = rng.choice(["eh ", "jadi ", "gini ", "nah "]) + t
    return t

def split_templates(cats, n_held_frac=0.25):
    train, held = {}, {}
    for cat, tpls in cats.items():
        k = max(1, round(len(tpls) * n_held_frac))
        train[cat], held[cat] = tpls[:-k], tpls[-k:]
    return train, held

def sample(cats, total, rng, seen, label):
    rows, cat_names = [], list(cats)
    per = total // len(cat_names)
    extra = total - per * len(cat_names)
    for i, cat in enumerate(cat_names):
        need, tries = per + (1 if i < extra else 0), 0
        got = 0
        while got < need and tries < 5000:
            tries += 1
            tpl = rng.choice(cats[cat])
            text = fill(tpl, rng)
            if rng.random() < 0.3:
                text = asr(text, rng)
            if text in seen:
                continue
            seen.add(text); got += 1
            rows.append((text, label, cat))
    return rows

def main():
    rng = random.Random(SEED)
    f_tr, f_he = split_templates(FRAUD)
    s_tr, s_he = split_templates(SAFE)
    seen = set()
    # baris asli dari CSV-mu (tanpa 006 dan 009 yang ambigu) masuk ke train
    original = [
      ("Pembayaran bisa di kasir atau QRIS resmi rumah sakit ya", False, "pembayaran_resmi"),
      ("Silahkan transfer ke nomor rekening pribadi saya untuk biaya administrasi ya", True, "pembayaran_pribadi"),
      ("Bisa bagi nomor rekening ibu untuk saya simpan secara pribadi", True, "data_sensitif"),
      ("Obat ini harus ditebus di apotek luar ya, di dalam sini tidak ada apotek", False, "obat_resmi"),
      ("Obat ini bisa langsung ditebus ke saya bu, agar ibu tidak perlu mengantri", True, "obat_langsung"),
      ("Bisa bagi nomor OTP yang baru saja masuk bu? untuk saya proses lebih lanjut", True, "data_sensitif"),
      ("Untuk biaya obat bisa langsung ditransfer ke nomor rekening saya ya bu", True, "pembayaran_pribadi"),
      ("Bisa minta alamat dan nomor rekening ibu, nanti saya antar obatnya", True, "obat_langsung"),
    ]
    for t, _, _ in original: seen.add(t)
    # held-out dibangun dulu agar tidak ada kalimat held-out yang kebetulan sama dengan train
    held = sample(f_he, N_HELD_PER_CLASS, rng, seen, True) + sample(s_he, N_HELD_PER_CLASS, rng, seen, False)
    train = list(original) + sample(f_tr, N_TRAIN_PER_CLASS, rng, seen, True) \
                           + sample(s_tr, N_TRAIN_PER_CLASS, rng, seen, False)
    rng.shuffle(train); rng.shuffle(held)

    assert not ({t for t,_,_ in train} & {t for t,_,_ in held}), "overlap train/held-out!"

    def dump(path, rows, split):
        with open(path, "w", encoding="utf-8") as f:
            for text, lab, _ in rows:
                f.write(json.dumps({"state": text, "questions": QUESTIONS,
                                    "expected": {"is_fraud": lab}}, ensure_ascii=False) + "\n")
    dump("train_synth.jsonl", train, "train")
    dump("heldout_synth.jsonl", held, "held")
    with open("meta_synth.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["split", "is_fraud", "category", "text"])
        for split, rows in (("train", train), ("heldout", held)):
            for text, lab, cat in rows: w.writerow([split, int(lab), cat, text])
    for name, rows in (("train", train), ("heldout", held)):
        n_f = sum(1 for _, l, _ in rows if l)
        print(f"{name}: {len(rows)} kasus  (fraud={n_f}, aman={len(rows)-n_f})")

if __name__ == "__main__":
    main()
