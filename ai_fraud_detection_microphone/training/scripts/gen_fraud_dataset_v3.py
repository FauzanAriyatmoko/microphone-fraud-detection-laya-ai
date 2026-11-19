"""Deterministic synthetic pilot. Family split precedes expansion; no patient data."""
import argparse
import hashlib
from types import SimpleNamespace
import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = {
    'is_fraud': {'type': 'noul', 'instructions': 'Apakah percakapan memuat permintaan atau penawaran aktif yang jelas untuk fraud di layanan resepsionis rumah sakit? Larangan, edukasi, laporan kejadian lampau, dan konteks tidak cukup bukan permintaan aktif.'},
    'context_status': {'type': 'choice', 'instructions': 'Tentukan konteks percakapan loket rumah sakit. Bedakan tindakan aktif dari edukasi, laporan, dan informasi yang belum cukup.', 'criteria': {
        'risiko_aktif': 'Permintaan aktif pembayaran pribadi, pungli, rahasia autentikasi, atau pemalsuan.',
        'layanan_normal': 'Layanan resmi, pertanyaan biasa, penolakan atau edukasi anti-fraud.',
        'laporan_kejadian': 'Melaporkan atau mengutip dugaan kejadian lampau untuk ditindaklanjuti.',
        'konteks_kurang': 'Tujuan, kanal, atau maksud tidak jelas; perlu klarifikasi.'}}
}
# Each list is an independent family. Wording within a family never crosses splits.
EXTRA = {
 'train': {
  'laporan_kejadian': [
   '{sapa}, saya ingin melaporkan petugas kemarin meminta transfer ke rekening pribadinya',
   'Saya tadi diminta membacakan OTP oleh orang di telepon, saya mau mengadu {sapa}',
   'Ada orang menawarkan jalur cepat dengan uang pelicin kemarin, tolong catat laporan saya {sapa}',
   'Pasien: Kemarin uang saya diminta tanpa kuitansi. Petugas: Kami catat pengaduannya {sapa}'],
  'konteks_kurang': ['Nanti pembayarannya lewat sana ya {sapa}', 'Bisa hubungi saya untuk proses berikutnya {sapa}',
   'Nomor yang tadi tolong dimasukkan {sapa}', 'Saya perlu alamat dan nomor rekening untuk urusan obat {sapa}'],
  'risiko_aktif': ['Agar klaim BPJS cair, kita palsukan tanggal rujukan {sapa}',
    'Pakai kartu BPJS milik orang lain saja untuk klaim ini {sapa}'],
  'layanan_normal': ['Refund resmi diproses ke rekening pasien melalui formulir kasir, tanpa PIN {sapa}',
    'Pasien gawat darurat didahulukan berdasarkan triase tanpa imbalan pribadi {sapa}']},
 'calibration': {
  'laporan_kejadian': ['Saya korban pungutan di luar tagihan minggu lalu, ingin buat pengaduan {sapa}',
    'Mohon diperiksa, orang yang menelepon kemarin menyuruh saya kasih PIN {sapa}'],
  'konteks_kurang': ['Untuk administrasi nanti saya bantu urus {sapa}', 'Kode yang mana harus saya berikan {sapa}'],
  'risiko_aktif': ['Berkas klaimnya kita isi tindakan yang tidak pernah dilakukan {sapa}'],
  'layanan_normal': ['Klaim harus sesuai tindakan sebenarnya dan dokumen sah {sapa}']},
 'validation': {
  'laporan_kejadian': ['Saya mau komplain karena kemarin disuruh bayar ke dompet digital pegawai {sapa}',
    'Ini pengaduan saya tentang orang yang tadi mengaku BPJS dan menagih uang pribadi {sapa}'],
  'konteks_kurang': ['Transfernya bisa ke nomor itu {sapa}', 'Ada tambahan biaya nanti kita bicarakan {sapa}'],
  'risiko_aktif': ['Ubah diagnosis di formulir walaupun tidak sesuai supaya klaim diterima {sapa}'],
  'layanan_normal': ['Jika rujukan salah datanya, minta koreksi resmi dari fasilitas penerbit {sapa}']}
}
# Hand-authored final challenge, never used to choose seed, epoch, or thresholds.
CHALLENGE = {
 'risiko_aktif': [
 'Kirim deposit kamar ke dompet digital milik saya, kasir tidak perlu tahu.',
 'Saya minta seratus ribu di saku berkas supaya nomor antrean maju.',
 'Kirim sandi bank ke chat pribadi saya untuk mencairkan refund.',
 'Tagihan resminya sejuta, tambahan dua ratus ribu untuk saya tidak dicetak.',
 'Kita ganti identitas pasien dengan kartu peserta tetangga agar ditanggung.',
 'Walau tidak ada pemeriksaan, tulis saja pemeriksaan itu di klaim.',
 'Jangan bayar ke kasir, bayar biaya operasi ke rekening pribadi adik saya.',
 'Saya bisa jualkan obat stok pribadi, uangnya ke saya tanpa melalui apotek.',
 'PIN kartu debitnya bacakan pelan supaya saya bisa cek saldo.',
 'CVV di belakang kartu difoto lalu kirim ke nomor pribadi saya.',
 'Pasien: Apakah rekening itu milik rumah sakit? Petugas: Bukan, punya saya. Transfer ke situ.',
 'Pasien: Apakah ada struk? Petugas: Tidak, uang ini untuk saya supaya Anda diprioritaskan.',
 'Jangan kasih OTP ke orang lain, cukup kasih ke saya agar saya login bank Anda.',
 'Saya mengaku dari direksi, pindahkan pembayaran ke rekening pribadi saya tanpa verifikasi.',
 'Kalau tidak kirim uang pelicin ke saya sekarang, pendaftaran saya tahan.'],
 'layanan_normal': [
 'Bukan ke rekening saya. Tujuan pembayaran ada di tagihan resmi kasir.',
 'Tolong jangan bacakan sandi bank. Isi sendiri di aplikasi bank Anda.',
 'Pengembalian dana melalui formulir resmi hanya memerlukan rekening tujuan, bukan PIN.',
 'Pasien kritis diprioritaskan oleh tim triase dan tidak perlu uang pelicin.',
 'Dokter meminta pasien segera dibawa ke IGD untuk pertolongan.',
 'Biaya tambahan tercantum dalam tagihan resmi, silakan verifikasi ke kasir.',
 'KTP dibutuhkan untuk mencocokkan identitas pada formulir pendaftaran resmi.',
 'Obat kosong dapat dicari di apotek berizin lain dengan resep sesuai petunjuk petugas.',
 'Saya tidak bisa menerima uang supaya antrean Anda didahulukan.',
 'Mohon jangan mengubah diagnosis demi klaim, datanya harus sesuai pemeriksaan.',
 'Pasien: Boleh bayar ke Anda? Petugas: Tidak boleh, hanya ke kasir resmi.',
 'Pasien: Saya sebutkan PIN? Petugas: Jangan, PIN hanya Anda yang tahu.',
 'Tunjukkan nomor antrean yang tercetak pada tiket pendaftaran.',
 'Saya petugas rumah sakit, jadwal poli tersedia di papan informasi.',
 'Bila ada yang menyuruh transfer pribadi, tolak dan hubungi pengaduan.'],
 'laporan_kejadian': [
 'Saya datang untuk mengadu. Kemarin petugas mengambil uang tanpa memberi struk.',
 'Tadi ada penelepon meminta sandi bank. Saya ingin melaporkannya.',
 'Minggu lalu saya ditawari potong antrean dengan uang pribadi, ini laporan saya.',
 'Dalam pengaduan ini tertulis bahwa korban diminta transfer ke rekening pegawai.',
 'Petugas: Apa keluhannya? Pasien: Kemarin saya diminta membayar uang pelicin.',
 'Saya melaporkan percobaan penyalahgunaan kartu BPJS oleh orang lain.',
 'Orang kemarin bilang kirim OTP. Itu saya laporkan ke bagian keamanan.',
 'Saya mau membuat laporan tentang tagihan palsu yang diterima kemarin.',
 'Uang sudah terlanjur masuk rekening pribadi orang itu, bantu proses pengaduan.',
 'Saya mengutip pesan penipu kemarin: kirim PIN sekarang. Tolong selidiki.'],
 'konteks_kurang': [
 'Bayarnya ikut yang kemarin saja.', 'Tolong kirim nomor itu ke sini.',
 'Untuk obatnya nanti ada yang mengantarkan.', 'Saya akan bantu urus uangnya.',
 'Kode itu dimasukkan di layar sebelah.', 'Alamat dan rekeningnya boleh dicatat?',
 'Pakai jalur yang tadi lebih cepat.', 'Ada orang menunggu uang administrasi.',
 'Nanti saya kabari tujuan transfernya.', 'Berkasnya perlu diganti dulu.']}


def normalize(text):
    return re.sub(r'[^\w\s]', '', text.lower()).strip()


def build(seed=42):
    bank = json.loads((ROOT / 'training/datasets/source_template_bank.json').read_text())
    legacy = SimpleNamespace(TEMPLATES=bank['templates'], STRESS=bank['stress'])
    legacy.fill = lambda text, rng: re.sub(r'\{(\w+)\}', lambda m: rng.choice(bank['slots'][m.group(1)]), text)
    rng = random.Random(seed)
    families = {s: [] for s in ['train', 'calibration', 'validation', 'test']}
    # Ambiguous legacy positives removed: no invented hospital policy or intent.
    excluded = ['Bagi nomor rekening', 'Bisa minta alamat', 'Ada SMS masuk']
    buckets = {}
    for t in legacy.TEMPLATES:
        if any(x in t['tpl'] for x in excluded):
            continue
        # Pair/turn IDs are grouped at category level; reserve calibration only
        # from ordinary categories to avoid splitting minimal-pair families.
        status = 'risiko_aktif' if t['label'] else 'layanan_normal'
        if t['split'] == 'held':
            split = 'validation'
        else:
            key = (t['label'], t['cat'])
            n = buckets.get(key, 0)
            split = 'calibration' if n == 0 and not t['cat'].startswith(('pair_', 'dua_giliran')) else 'train'
            buckets[key] = n + 1
        family = t['cat'] if t['cat'].startswith(('pair_', 'dua_giliran')) else t['id']
        families[split].append((family, t['tpl'], status, t['cat']))
    for split, groups in EXTRA.items():
        for status, templates in groups.items():
            for i, template in enumerate(templates):
                families[split].append((f'{split}:{status}:{i}', template, status, status))
    seen = set()
    rows = {s: [] for s in families}
    def append(split, family, text, status, category, variant):
        key = normalize(text)
        if key in seen:
            return False
        seen.add(key)
        expected = {'context_status': status}
        # Ambiguous cases do not carry a misleading negative binary target.
        if status != 'konteks_kurang':
            expected['is_fraud'] = status == 'risiko_aktif'
        rows[split].append({'state': text, 'questions': QUESTIONS, 'expected': expected,
            'metadata': {'family_id': family, 'category': category, 'synthetic': True, 'variant': variant}})
        return True
    for split, total in [('train', 1200), ('calibration', 240), ('validation', 400)]:
        groups = {s: [f for f in families[split] if f[2] == s] for s in QUESTIONS['context_status']['criteria']}
        # Plenty of normal/risk phrasing, explicit minority coverage for reports/ambiguity.
        quotas = {'risiko_aktif': int(total*.35), 'layanan_normal': int(total*.35), 'laporan_kejadian': int(total*.15)}
        quotas['konteks_kurang'] = total - sum(quotas.values())
        for status, quota in quotas.items():
            got = 0
            for attempt in range(100000):
                if got == quota:
                    break
                family, template, _, category = rng.choice(groups[status])
                text = legacy.fill(template, rng)
                # Fillers only: never drop negation or invent an ASR confusion.
                if rng.random() < .5:
                    text = rng.choice(['eh ', 'jadi ', 'begini ', 'sebentar ', 'anu ', 'baik ', 'permisi ', '']) + text.lower()
                # This suffix is shared by every class; it carries no label information.
                if rng.random() < .4:
                    text += rng.choice([' ya', ' ya bu', ' ya pak', ' mohon diperhatikan', ' terima kasih'])
                got += append(split, family, text, status, category, 'synthetic_text')
            if got != quota:
                raise RuntimeError(f'Insufficient unique data for {split}/{status}: {got}/{quota}')
    for i, (label, text) in enumerate(legacy.STRESS):
        append('test', f'legacy_stress:{i}', text, 'risiko_aktif' if label else 'layanan_normal', 'legacy_stress', 'handwritten')
    for status, texts in CHALLENGE.items():
        for i, text in enumerate(texts):
            append('test', f'challenge:{status}:{i}', text, status, status, 'handwritten')
    for values in rows.values():
        rng.shuffle(values)
    for a, aa in rows.items():
        for b, bb in rows.items():
            if a < b:
                assert not ({r['metadata']['family_id'] for r in aa} & {r['metadata']['family_id'] for r in bb}), (a,b)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, default=ROOT / 'training/datasets/v3')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = build(args.seed)
    manifest = {'seed': args.seed, 'schema_version': '3.0', 'synthetic_only': True, 'splits': {}}
    for split, values in rows.items():
        data = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in values)
        (args.out / f'{split}.jsonl').write_text(data)
        manifest['splits'][split] = {'rows': len(values), 'families': len({r['metadata']['family_id'] for r in values}),
            'labels': dict(Counter(r['expected']['context_status'] for r in values)), 'sha256': hashlib.sha256(data.encode()).hexdigest()}
    (args.out / 'questions.json').write_text(json.dumps(QUESTIONS, ensure_ascii=False, indent=2) + '\n')
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))

if __name__ == '__main__':
    main()
