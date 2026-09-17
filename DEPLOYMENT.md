# 🚀 Panduan Deploy — Gratis 24/7 dengan GitHub Actions

Bot ini berjalan di **GitHub Actions**. Repo **publik** mendapat menit Actions
**tak terbatas**, sehingga bot bisa menyala 24/7 tanpa biaya.

## Cara kerja

```
workflow run-bot
  └─ checkout repo (membawa data.db.enc terenkripsi)
  └─ run.sh
       ├─ background: commit+push data.db.enc tiap 5 menit jika berubah
       └─ foreground: python3 bot.py (restart otomatis kalau crash)
            └─ setelah 5 jam (BOT_MAX_RUNTIME_SEC) exit code 20 = "rotate"
  └─ gh workflow run run-bot  → job baru menggantikan job lama
```

Ada juga **jaring pengaman cron** (`0 0,6,12,18 * * *`) yang menjalankan ulang
workflow 4x sehari jika mekanisme self-rotate gagal.

---

## Langkah 1 — Dapatkan Telegram User ID kamu

Bot hanya merespon user ID yang terdaftar. Cara mengetahui ID-mu:

1. Buka [@userinfobot](https://t.me/userinfobot) di Telegram.
2. Kirim pesan apa saja (mis. `/start`).
3. Bot akan membalas dengan **Id: `123456789`** — catat angka itu.

> Atau jalankan `curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | jq`
> setelah kamu mengirim pesan ke bot-mu, lalu lihat `message.from.id`.

## Langkah 2 — Buat kunci enkripsi DB

Jalankan perintah ini di terminal (butuh `pip install cryptography`):

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Akan muncul string seperti:

```
ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=
```

**Simpan baik-baik.** Tanpa kunci ini, data tidak bisa dibaca kembali.

## Langkah 3 — Tentukan PIN

Buat PIN numerik 6 digit, mis. `482917`. PIN ini wajib diketik untuk melihat
API key secara utuh.

## Langkah 4 — Buat repo publik & push kode

```bash
git init -b main
git add .
git commit -m "feat: telegram bot vault for AI api keys"
git remote add origin https://github.com/<username>/koleksiapikeyai.git
git push -u origin main
```

> ⚠️ Pastikan repo **publik**. Repo privat menghabiskan menit Actions berbayar
> (hanya 2000 menit/bulan di paket gratis) dan bot akan berhenti berjalan.

## Langkah 5 — Isi GitHub Secrets

Buka **Settings → Secrets and variables → Actions → New repository secret**:

| Nama Secret | Nilai | Wajib |
|---|---|---|
| `BOT_TOKEN` | token dari @BotFather | ✅ |
| `ALLOWED_USER_IDS` | ID Telegram kamu (beberapa: `111,222`) | ✅ |
| `REVEAL_PIN` | PIN 6 digit untuk membuka API key | ✅ |
| `DB_ENCRYPTION_KEY` | kunci Fernet dari Langkah 2 | ✅ |
| `GH_TOKEN` | (opsional) Personal Access Token scope `repo`+`workflow` | ⬜ |

> **Tentang `GH_TOKEN`:** Secara default bot memakai token bawaan GitHub
> (`github.token`) yang sudah cukup untuk auto-commit & self-redispatch.
> Tambahkan PAT hanya jika auto-commit atau self-rotate sering gagal.

**Isi secret via CLI (opsional):**

```bash
gh secret set BOT_TOKEN        --body "8843...tGs"
gh secret set ALLOWED_USER_IDS --body "123456789"
gh secret set REVEAL_PIN       --body "482917"
gh secret set DB_ENCRYPTION_KEY --body "ZmDfcTF7_60GrrY167zs...="
```

## Langkah 6 — Jalankan bot

```bash
gh workflow run run-bot.yml
```

Atau buka tab **Actions → run-bot → Run workflow**.

Cek log: **Actions → run-bot → job yang berjalan**. Jika muncul
`Bot online: @namabotkamu`, bot sudah hidup. 🎉

---

## Langkah 7 — Uji coba di Telegram

Kirim ke bot kamu:

```
/start
/add deepseek-v3
```

Lalu jawab berturut-turut: `DeepSeek`, `https://api.deepseek.com/v1`,
`sk-xxxx`, `deepseek-chat`, /skip, /skip.

Kemudian:

```
/list                          # lihat semua model (key tersembunyi)
/view deepseek-v3              # detail + tombol
  └ ketuk "🔑 Salin API Key"   # ketik PIN, key muncul
/search deepseek               # cari
/pin deepseek-v3               # sematkan
/edit deepseek-v3 api_key sk-baru
/export                        # unduh JSON backup
/audit                         # lihat log aktivitas
```

---

## 🔧 Operasional

**Melihat status:** `gh run list --workflow run-bot.yml --limit 5`

**Hentikan bot:** `gh run cancel <run-id>`, lalu disable workflow:
`gh workflow disable run-bot.yml`

**Backup manual:** jalankan `/export` di Telegram, simpan file JSON-nya.

**Restore:** balas file JSON backup dengan `/import`.

**Ganti PIN/encryption key?**
- PIN: cukup update secret `REVEAL_PIN` & restart workflow. Langsung efektif.
- Encryption key: **harus export dulu** (`/export`), update secret, hapus
  `data.db.enc` lama, baru `/import` kembali. Data lama tidak bisa dibaca
  dengan kunci baru.

## ❗ Troubleshooting

| Gejala | Solusi |
|---|---|
| `Bot token invalid` | Cek secret `BOT_TOKEN` sudah benar |
| `DB_ENCRYPTION_KEY does not match` | Kunci berubah tapi DB lama masih ada. Export dulu, atau hapus `data.db.enc` & mulai dari nol |
| Bot balas "Tidak diizinkan" | `ALLOWED_USER_IDS` belum berisi ID-mu |
| Bot offline setelah beberapa jam | Cek tab Actions; kalau workflow gagal, jalankan `gh workflow run run-bot.yml` manual. Cron akan otomatis menyalakan ulang 4x sehari |
| `getUpdates` 409 Conflict | Ada instance bot lain sedang berjalan (mis. di komputer lokal). Matikan yang lain |
| Auto-commit DB gagal | Tambahkan secret `GH_TOKEN` berisi PAT scope `repo` |
