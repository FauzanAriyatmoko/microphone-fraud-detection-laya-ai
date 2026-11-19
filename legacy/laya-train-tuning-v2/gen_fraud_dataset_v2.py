"""Generator dataset sintetis v2 - fraud resepsionis RS untuk laya-train (JSONL, pertanyaan noul).

Perubahan dari v1:
  * pasangan minimal (kalimat fraud vs aman yang hampir identik) -> memaksa model membaca ARTI, bukan kata kunci
  * template aman bernegasi / peringatan yang memuat kata "fraud-ish" (OTP, kode, transfer, rekening, bayar langsung)
  * otp_aman: 8 template di train (v1 hanya 3), tiap template punya struktur kalimat berbeda
  * hard positive: fraud yang menyamar resmi / pakai alasan keamanan
  * sebagian kasus 2 giliran bicara (Pasien -> Resepsionis)
  * split di level TEMPLATE; held-out berisi struktur kalimat yang belum pernah ada di train
  * template_id disimpan di meta_v2.csv agar kesalahan bisa dikelompokkan per template

Jalankan: python gen_fraud_dataset_v2.py
Output  : train_v2.jsonl, heldout_v2.jsonl, stress_test.jsonl, meta_v2.csv
"""
import csv, json, math, random, re

SEED = 7
T_TRAIN_PER_CLASS = 300
T_HELD_PER_CLASS = 100

QUESTIONS = {"is_fraud": {"type": "noul",
    "instructions": "Apakah ucapan resepsionis ini indikasi fraud atau pungutan tidak resmi?"}}

F = {
    "sapa": ["Bu", "Pak", "Ibu", "Bapak", "Mbak", "Mas", "Kak"],
    "biaya": ["biaya administrasi", "biaya pendaftaran", "uang muka", "biaya pemeriksaan", "biaya rawat inap",
              "biaya konsultasi", "biaya laboratorium", "biaya operasi"],
    "rek_pribadi": ["nomor rekening pribadi saya", "rekening saya", "rekening atas nama saya sendiri",
                    "nomor DANA saya", "nomor GoPay saya", "nomor e-wallet saya", "rekening BCA saya"],
    "rek_pribadi_umum": ["rekening pribadi", "rekening perorangan", "nomor DANA pribadi", "e-wallet pribadi"],
    "nomor_hp": ["nomor WhatsApp saya", "nomor HP pribadi saya", "WA pribadi saya"],
    "layanan": ["pendaftaran", "pemeriksaan", "rawat jalan", "pengambilan hasil lab", "konsultasi dokter",
                "rontgen", "pengurusan BPJS"],
    "nominal": ["lima puluh ribu", "seratus ribu", "dua ratus ribu", "tiga ratus ribu", "75 ribu", "150 ribu"],
    "obat": ["Obat ini", "Obat dari dokter ini", "Resep ini", "Vitamin dan obat ini", "Obat tetes ini", "Obat diabetes ini"],
    "kanal_resmi": ["QRIS resmi rumah sakit", "virtual account yang tertera di tagihan", "mesin pembayaran mandiri",
                    "transfer ke rekening resmi rumah sakit yang tertera di tagihan", "kartu debit di kasir"],
    "poli": ["anak", "penyakit dalam", "gigi", "mata", "kandungan", "jantung", "saraf"],
    "data": ["password m-banking", "kode CVV kartu", "PIN ATM", "kode verifikasi"],
    "kode": ["kode OTP", "kode verifikasi", "PIN", "kode rahasia", "kode SMS"],
    "alasan": ["demi keamanan", "untuk verifikasi", "untuk proses klaim", "biar cepat"],
    "jam": ["delapan pagi", "sepuluh pagi", "satu siang", "tiga sore"],
    "lantai": ["satu", "dua", "tiga", "empat"],
    "nomor": ["dua belas", "dua puluh tiga", "tiga puluh lima", "empat puluh satu"],
}

TEMPLATES = []   # dict(id,label,cat,split,tpl)
_counter = {}

def add(label, cat, split, tpls):
    for t in tpls:
        k = (label, cat)
        _counter[k] = _counter.get(k, 0) + 1
        tid = f"{'F' if label else 'S'}:{cat}:{_counter[k]}"
        TEMPLATES.append(dict(id=tid, label=label, cat=cat, split=split, tpl=t))

# ---------------- FRAUD: TRAIN ----------------
add(True, "pembayaran_pribadi", "train", [
    "Silakan transfer {biaya} ke {rek_pribadi} ya, {sapa}",
    "Untuk {biaya} bisa langsung dikirim ke {rek_pribadi}, {sapa}",
    "{sapa}, {biaya} lewat {rek_pribadi} saja biar cepat diproses",
    "Kirim {biaya} ke {rek_pribadi} dulu ya, nanti saya daftarkan",
    "Pembayaran {biaya} nggak usah ke kasir, langsung ke {rek_pribadi} saja {sapa}",
    "Transfernya ke {rek_pribadi} ya {sapa}, nanti buktinya kirim ke {nomor_hp}",
    "{sapa} tidak perlu bayar di kasir, bayar ke saya saja",
])
add(True, "jalur_cepat", "train", [
    "Kalau mau {layanan} lebih cepat, {sapa} bisa kasih uang tambahan {nominal} ke saya",
    "Supaya {sapa} tidak perlu mengantri, bayar saja langsung ke saya",
    "Untuk {layanan} lewat jalur cepat ada biaya {nominal} yang diserahkan ke saya langsung",
    "Antriannya panjang {sapa}, tapi kalau ada uang pelicin {nominal} saya masukkan duluan",
    "Biar {layanan} diprioritaskan, {sapa} cukup titip {nominal} ke saya",
])
add(True, "data_sensitif", "train", [
    "Boleh minta {kode} yang baru masuk ke HP {sapa}? untuk saya proses lebih lanjut",
    "Tolong sebutkan {data} {sapa} ya, untuk verifikasi di komputer saya",
    "Bagi nomor rekening {sapa} untuk saya simpan secara pribadi, nanti saya kirimi {nominal}",
    "{sapa}, bacakan {kode} yang barusan masuk, biar saya yang masukkan",
    "Untuk {layanan} saya butuh {data} {sapa}, kirim lewat WhatsApp pribadi saya saja",
])
add(True, "obat_langsung", "train", [
    "{obat} bisa langsung ditebus ke saya, {sapa}, tidak perlu antri di apotek",
    "Untuk {obat} bayar ke saya saja {sapa}, nanti saya antar ke rumah",
    "{obat} lebih murah kalau beli lewat saya, transfer saja ke {rek_pribadi}",
    "Bisa minta alamat dan nomor rekening {sapa}, nanti saya antar obatnya",
])
add(True, "biaya_tidak_resmi", "train", [
    "Ada biaya tambahan {nominal} untuk {layanan}, tidak masuk struk, bayar tunai ke saya saja",
    "Biar klaim BPJS-nya cepat cair, {sapa} kasih uang {nominal} ke saya",
    "Untuk {layanan} ada biaya di luar tarif, {nominal}, diserahkan ke saya jangan ke kasir ya {sapa}",
    "{sapa} mau {layanan} gratis? bisa, asal bayar {nominal} ke saya dulu",
])
add(True, "menyamar_resmi", "train", [
    "Rekening resmi rumah sakit sedang diganti, untuk sementara {biaya} dikirim ke {rek_pribadi} {sapa}",
    "{alasan}, {biaya} jangan lewat kasir, kirim saja ke {rek_pribadi}",
    "Ini prosedur terbaru {sapa}, {biaya} langsung ditransfer ke {rek_pribadi}",
])
# ---------------- FRAUD: HELD-OUT (struktur kalimat baru) ----------------
add(True, "pembayaran_pribadi", "held", [
    "Sistem kasir lagi gangguan {sapa}, jadi {biaya} sementara masuk ke {rek_pribadi} dulu",
    "Ini {rek_pribadi}, tolong {biaya} dikirim ke situ ya {sapa}",
])
add(True, "jalur_cepat", "held", [
    "Saya bisa selipkan {sapa} di urutan depan untuk {layanan}, asal ada {nominal} buat saya",
    "Dokternya penuh {sapa}, tapi untuk {nominal} saya carikan slot hari ini",
])
add(True, "data_sensitif", "held", [
    "Ada SMS masuk ke HP {sapa}? tolong foto layarnya lalu kirim ke {nomor_hp}, {alasan}",
    "Supaya akun {sapa} tidak terblokir, sebutkan {kode}-nya sekarang ya",
])
add(True, "obat_langsung", "held", [
    "Daripada nebus di apotek, {obat} saya bawakan saja {sapa}, bayarnya ke {rek_pribadi}",
])
add(True, "biaya_tidak_resmi", "held", [
    "{sapa}, {layanan} sebenarnya gratis tapi saya butuh uang rokok {nominal}, serahkan ke saya ya",
    "Untuk dapat surat rujukan lebih cepat, selipkan {nominal} di berkas ya {sapa}",
])
add(True, "menyamar_resmi", "held", [
    "Ini rekening resmi untuk {biaya}, atas nama saya pribadi, {sapa} transfer ke situ ya",
])
# ---------------- SAFE: TRAIN ----------------
add(False, "pembayaran_resmi", "train", [
    "Pembayaran {biaya} bisa di kasir lantai {lantai} atau lewat {kanal_resmi} ya, {sapa}",
    "{sapa}, {biaya} dibayarkan lewat {kanal_resmi}, rinciannya ada di tagihan",
    "Untuk {biaya} silakan ke kasir, jangan transfer ke {rek_pribadi_umum} petugas siapa pun ya {sapa}",
    "Pembayaran hanya melalui {kanal_resmi}, kami tidak menerima pembayaran ke {rek_pribadi_umum}",
    "Tagihan {biaya} sudah tercetak di lembar ini, {sapa} bayar di kasir ya",
    "Kalau ada petugas yang minta transfer ke {rek_pribadi_umum}, tolong laporkan ke kami ya {sapa}",
    "{sapa} bisa bayar langsung di kasir lantai {lantai} atau pakai {kanal_resmi}",
])
add(False, "otp_aman", "train", [
    "Kode OTP yang masuk ke HP {sapa} jangan diberikan ke siapa pun, termasuk petugas ya",
    "{sapa} masukkan sendiri {kode}-nya di aplikasi resmi rumah sakit ya",
    "Kalau ada yang meminta {data} {sapa}, jangan diberikan ya, kami tidak pernah memintanya",
    "Untuk {layanan} {sapa} cukup login sendiri di aplikasi, kami tidak perlu kode apa pun",
    "Rumah sakit tidak pernah meminta {kode} lewat telepon atau WhatsApp, {sapa}",
    "Mohon {sapa} rahasiakan {kode}, bahkan dari kami",
    "Setelah {sapa} menerima SMS verifikasi, ketik sendiri di layar ini ya, saya tidak akan melihatnya",
    "Kalau ada pesan yang mengaku dari rumah sakit dan minta {kode}, abaikan saja {sapa}",
])
add(False, "antrian_resmi", "train", [
    "Nomor antrian {sapa} {nomor}, silakan tunggu dipanggil di ruang tunggu",
    "Untuk {layanan} lebih cepat, {sapa} bisa daftar online lewat aplikasi resmi sebelum datang",
    "Antriannya memang panjang {sapa}, mohon menunggu, urutannya sesuai nomor antrian",
    "Kami tidak menerima titipan uang untuk mempercepat antrian ya {sapa}, semua sesuai nomor",
    "{sapa} bisa ambil nomor antrian di mesin dekat pintu masuk untuk {layanan}",
    "Kami tidak bisa mempercepat antrian dengan imbalan apa pun {sapa}",
])
add(False, "obat_resmi", "train", [
    "{obat} bisa ditebus di apotek rumah sakit lantai {lantai}, {sapa}",
    "{obat} harus ditebus di apotek luar ya, di dalam sini tidak ada apotek",
    "Untuk {obat} {sapa} bawa resepnya ke apotek, bayar di kasir apotek ya",
    "{obat} tersedia di apotek depan, bisa pakai BPJS atau bayar langsung di sana",
    "Kalau ada yang menawarkan {obat} di luar apotek rumah sakit, mohon jangan dibeli {sapa}",
])
add(False, "data_dan_biaya_resmi", "train", [
    "Mohon bawa kartu identitas dan kartu BPJS asli untuk {layanan}, {sapa}",
    "Kami hanya meminta data di formulir pendaftaran resmi, {sapa}",
    "{biaya} sesuai tarif resmi, rinciannya ada di struk, {sapa}",
    "Tidak ada biaya tambahan di luar struk ya {sapa}, semua tercantum di tagihan",
    "Klaim BPJS diproses langsung oleh rumah sakit, {sapa} tidak perlu membayar apa pun ke petugas",
])
add(False, "info_umum", "train", [
    "Silakan ke poli {poli} lantai {lantai}, {sapa}",
    "Jadwal dokter poli {poli} hari ini mulai jam {jam}, {sapa}",
    "{sapa}, hasil {layanan} bisa diambil besok jam {jam}",
    "Ruang tunggu poli {poli} ada di sebelah kanan, {sapa} silakan duduk dulu",
])
# ---------------- SAFE: HELD-OUT (struktur kalimat baru) ----------------
add(False, "pembayaran_resmi", "held", [
    "Mohon waspada, rumah sakit tidak pernah meminta {biaya} ditransfer ke {rek_pribadi_umum}, {sapa}",
    "Bayarnya langsung saja di kasir depan {sapa}, nanti struknya dicetak",
])
add(False, "otp_aman", "held", [
    "{sapa} tenang saja, untuk {layanan} tidak ada kode yang perlu disebutkan ke petugas",
    "Dari pihak kami tidak ada yang akan menanyakan {kode} {sapa}, kalau ada segera hubungi satpam",
    "Pesan dari bank berisi {kode} itu rahasia {sapa}, jangan dibacakan ke saya maupun orang lain",
])
add(False, "antrian_resmi", "held", [
    "Soal urutan, semua sama {sapa}, tidak ada yang bisa lompat antrian meskipun membayar lebih",
    "Mau lebih cepat? {sapa} bisa daftar lewat aplikasi, gratis dan tanpa perantara",
])
add(False, "obat_resmi", "held", [
    "Pembelian {obat} hanya di apotek resmi, bayarnya di kasir apotek, bukan ke petugas pendaftaran {sapa}",
    "{obat} bisa diambil langsung di apotek lantai {lantai} sesuai resep, {sapa}",
])
add(False, "data_dan_biaya_resmi", "held", [
    "Biaya {layanan} sudah termasuk di tagihan, jadi tidak ada pungutan tambahan ke petugas {sapa}",
    "Kalau ada yang minta uang rokok atau uang pelicin, mohon laporkan ke bagian pengaduan {sapa}",
])
add(False, "info_umum", "held", [
    "Loket {layanan} ada di lantai {lantai}, buka sampai jam {jam} {sapa}",
])

# ---------------- PASANGAN MINIMAL (fraud, aman) ----------------
def add_pairs(split, pairs):
    for i, (f, s) in enumerate(pairs, 1):
        add(True, f"pair_{split}", split, [f])
        add(False, f"pair_{split}", split, [s])

add_pairs("train", [
    ("Berikan {kode} {sapa} ke saya ya, biar saya yang proses",
     "Jangan berikan {kode} {sapa} ke siapa pun, termasuk saya"),
    ("Bayar {biaya} ke {rek_pribadi} {sapa}",
     "Bayar {biaya} ke kasir atau {kanal_resmi} {sapa}"),
    ("Bayar langsung ke saya saja {sapa}, tidak usah ke kasir",
     "Bayar langsung di kasir saja {sapa}, tidak usah ke petugas"),
    ("Kalau mau cepat, selipkan {nominal} ke saya {sapa}",
     "Kalau mau cepat, daftar online lewat aplikasi resmi {sapa}"),
    ("{obat} ditebus lewat saya saja {sapa}",
     "{obat} ditebus lewat apotek rumah sakit saja {sapa}"),
])
add_pairs("held", [
    ("Kirimkan {data} {sapa} ke {nomor_hp} sekarang",
     "Jangan kirimkan {data} {sapa} ke nomor mana pun, termasuk nomor kami"),
    ("Transfer {biaya} ke {rek_pribadi}, {sapa}, jangan lewat kasir",
     "Transfer {biaya} hanya ke rekening resmi di tagihan, {sapa}, bukan {rek_pribadi_umum}"),
    ("Saya bisa carikan jalur cepat {layanan} kalau {sapa} sedia {nominal}",
     "Tidak ada jalur cepat berbayar untuk {layanan}, {sapa}, semua sesuai antrian"),
])

# ---------------- 2 GILIRAN BICARA (pasangan fraud/aman) ----------------
def add_turns(split, items):
    for q, fa, sa in items:
        add(True, f"dua_giliran_{split}", split, [f"Pasien: {q} Resepsionis: {fa}"])
        add(False, f"dua_giliran_{split}", split, [f"Pasien: {q} Resepsionis: {sa}"])

add_turns("train", [
    ("Bayarnya ke mana ya?", "Transfer saja ke {rek_pribadi} ya {sapa}",
     "Ke kasir lantai {lantai} atau {kanal_resmi} ya {sapa}"),
    ("Kok antriannya lama sekali?", "Kalau {sapa} mau cepat, titip {nominal} ke saya",
     "Mohon bersabar {sapa}, semua sesuai nomor antrian"),
])
add_turns("held", [
    ("Ini verifikasinya bagaimana?", "Sebutkan {kode} yang masuk ke HP {sapa}, biar saya masukkan",
     "{sapa} masukkan sendiri {kode}-nya di aplikasi, kami tidak boleh mengetahuinya"),
    ("Obatnya di mana ya?", "Bayar ke saya saja nanti obatnya saya antar {sapa}",
     "Di apotek rumah sakit lantai {lantai}, bayar di kasir apotek {sapa}"),
])

STRESS = [  # ditulis tangan, gaya berbeda dari generator; label oleh penulis
 (True,  "Bu, kalau mau didahulukan, sini lewat saya aja, ada uang administrasinya sedikit, kasih ke saya langsung ya"),
 (True,  "Gini Pak, sistem pembayaran kami lagi error, jadi sementara bapak transfer ke rekening BRI saya dulu, nanti saya inputkan"),
 (True,  "Tolong bacakan enam digit angka yang barusan dikirim lewat SMS ya Bu, itu untuk konfirmasi jadwal dokter"),
 (True,  "Obatnya lagi kosong di apotek rumah sakit, tapi saya punya stok pribadi, harganya lebih murah, bayarnya ke saya aja"),
 (True,  "Untuk BPJS-nya biar nggak ditolak, ada biaya pengurusan seratus ribu, nggak usah pakai kwitansi ya"),
 (True,  "Mbak, fotoin kartu ATM-nya depan belakang terus kirim ke WA saya, biar saya bantu proses refund"),
 (True,  "Kalau Ibu mau lewat jalur khusus, nanti saya antar ke dokter langsung tanpa antri, tapi ada uang terima kasihnya ya"),
 (True,  "Pak, nomor rekening bapak berapa? Nanti saya kembalikan kelebihan bayarnya, sekalian kasih PIN-nya buat saya cek"),
 (True,  "Pembayaran lewat QRIS rumah sakit lagi gangguan, kirim aja ke DANA pribadi saya, nomornya 0812 sekian"),
 (True,  "Kalau mau rawat inap hari ini kamar penuh Bu, tapi bisa saya aturkan kalau ada pelicinnya"),
 (True,  "Ibu daftar pakai nama saya dulu aja nanti biayanya ibu transfer ke saya, lebih murah dari tarif kasir"),
 (True,  "Tolong sebutin kode verifikasi yang masuk ke HP Bapak, saya lagi bantu aktifin akun pasiennya"),
 (True,  "Hasil labnya bisa saya percepat keluar hari ini, tapi administrasinya lewat saya, jangan ke kasir"),
 (True,  "Ini rekening resmi buat deposit rawat inap ya Bu, atas nama saya, soalnya rekening rumah sakit lagi dibekukan"),
 (True,  "Saya bisa bantu hapus tagihan tambahan Ibu, asal ada fee dua ratus ribu buat saya"),
 (False, "Selamat pagi Ibu, silakan ambil nomor antrian di mesin sebelah kiri, nanti dipanggil sesuai urutan ya"),
 (False, "Untuk pembayarannya bisa di kasir lantai satu, atau pakai QRIS yang ada di meja kasir resmi rumah sakit"),
 (False, "Mohon maaf Pak, kami tidak bisa menerima pembayaran dalam bentuk apa pun di loket pendaftaran, semuanya ke kasir"),
 (False, "Ibu, kalau ada orang yang mengaku dari rumah sakit lalu minta kode OTP, jangan dikasih ya, kami tidak pernah minta"),
 (False, "Obatnya ditebus di apotek lantai dua, bayarnya di kasir apotek, BPJS juga bisa kalau ada rujukan"),
 (False, "Kartu BPJS dan KTP asli dibawa ya Bu, fotokopinya sudah kami sediakan di loket sebelah"),
 (False, "Hasil lab Bapak sudah bisa diambil besok jam sembilan di loket tiga, tidak ada biaya tambahan"),
 (False, "Sistem pembayaran online lagi gangguan, jadi silakan bayar tunai di kasir ya Bu, nanti struknya langsung dicetak"),
 (False, "Untuk transfer, pastikan tujuannya rekening atas nama rumah sakit yang tertera di tagihan, jangan ke rekening perorangan"),
 (False, "Mohon maaf antriannya cukup panjang hari ini, kami usahakan secepatnya, mohon menunggu di ruang tunggu ya Pak"),
 (False, "Kalau Ibu ingin lebih cepat besok, bisa daftar lewat aplikasi, pilih jadwal dokternya, tanpa biaya tambahan"),
 (False, "Ibu bisa menyerahkan berkas rujukan ke loket dua, nanti petugas akan mengecek dan memberi nomor antrian"),
 (False, "Kode booking Ibu sudah terkirim ke HP, tinggal tunjukkan di mesin check-in, jangan dibagikan ke orang lain ya"),
 (False, "Jam besuk rawat inap mulai pukul empat sore sampai tujuh malam, maksimal dua orang per pasien"),
 (False, "Bapak bisa mengajukan keluhan layanan ke bagian pengaduan kalau ada petugas yang meminta uang di luar tagihan"),
]

ORIGINAL = [  # 8 baris asli dari CSV (006 dan 009 yang ambigu dibuang)
 (False, "Pembayaran bisa di kasir atau QRIS resmi rumah sakit ya"),
 (True,  "Silahkan transfer ke nomor rekening pribadi saya untuk biaya administrasi ya"),
 (True,  "Bisa bagi nomor rekening ibu untuk saya simpan secara pribadi"),
 (False, "Obat ini harus ditebus di apotek luar ya, di dalam sini tidak ada apotek"),
 (True,  "Obat ini bisa langsung ditebus ke saya bu, agar ibu tidak perlu mengantri"),
 (True,  "Bisa bagi nomor OTP yang baru saja masuk bu? untuk saya proses lebih lanjut"),
 (True,  "Untuk biaya obat bisa langsung ditransfer ke nomor rekening saya ya bu"),
 (True,  "Bisa minta alamat dan nomor rekening ibu, nanti saya antar obatnya"),
]

def fill(t, rng):
    return re.sub(r"\{(\w+)\}", lambda m: rng.choice(F[m.group(1)]), t)

def asr(text, rng):
    t = re.sub(r"[,.?!:]", "", text.lower())
    if rng.random() < 0.4:
        t = rng.choice(["eh ", "jadi ", "gini ", "nah "]) + t
    return t

def sample_split(split, rng, seen, target_per_class):
    rows = []
    for label in (True, False):
        tpls = [t for t in TEMPLATES if t["split"] == split and t["label"] == label]
        k = math.ceil(target_per_class / len(tpls))
        cls = []
        for t in tpls:
            got, tries = 0, 0
            while got < k and tries < 3000:
                tries += 1
                text = fill(t["tpl"], rng)
                if rng.random() < 0.3:
                    text = asr(text, rng)
                if text in seen:
                    continue
                seen.add(text); got += 1
                cls.append((text, label, t["cat"], t["id"]))
        rng.shuffle(cls)
        rows += cls
    return rows

def balance(rows, rng):
    f = [r for r in rows if r[1]]; s = [r for r in rows if not r[1]]
    n = min(len(f), len(s))
    rng.shuffle(f); rng.shuffle(s)
    return f[:n] + s[:n]

def main():
    rng = random.Random(SEED)
    seen = {t for _, t in ORIGINAL} | {t for _, t in STRESS}
    held = balance(sample_split("held", rng, seen, T_HELD_PER_CLASS), rng)
    train = balance(sample_split("train", rng, seen, T_TRAIN_PER_CLASS), rng)
    train += [(t, l, "original_csv", "ORIG") for l, t in ORIGINAL]
    rng.shuffle(train); rng.shuffle(held)
    tr_t = {r[0] for r in train}; he_t = {r[0] for r in held}; st_t = {t for _, t in STRESS}
    assert not (tr_t & he_t) and not (tr_t & st_t) and not (he_t & st_t), "overlap!"
    tr_ids = {r[3] for r in train}; he_ids = {r[3] for r in held}
    assert not (tr_ids & he_ids - {"ORIG"}), "template bocor antar split"

    def dump(path, rows):
        with open(path, "w", encoding="utf-8") as f:
            for text, lab, *_ in rows:
                f.write(json.dumps({"state": text, "questions": QUESTIONS,
                                    "expected": {"is_fraud": lab}}, ensure_ascii=False) + "\n")
    dump("train_v2.jsonl", train); dump("heldout_v2.jsonl", held)
    dump("stress_test.jsonl", [(t, l, "stress", "STRESS") for l, t in STRESS])
    with open("meta_v2.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["split", "is_fraud", "category", "template_id", "text"])
        for sp, rows in (("train", train), ("heldout", held),
                         ("stress", [(t, l, "stress", "STRESS") for l, t in STRESS])):
            for text, lab, cat, tid in rows: w.writerow([sp, int(lab), cat, tid, text])
    for name, rows in (("train", train), ("heldout", held)):
        nf = sum(1 for r in rows if r[1])
        nt = len({r[3] for r in rows})
        print(f"{name}: {len(rows)} kasus (fraud={nf}, aman={len(rows)-nf}), {nt} template berbeda")
    print(f"stress_test: {len(STRESS)} kalimat tulisan tangan")
    n_train = len(train) - len(train) // 10
    ub = math.ceil(n_train / 8)
    for ga, ep in ((2, 6), (2, 8)):
        print(f"estimasi update (grad-accum {ga}, epochs {ep}): {ep * math.ceil(ub / ga)}")

if __name__ == "__main__":
    main()