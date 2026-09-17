# 🔐 Koleksi API Key AI — Telegram Bot

Bot Telegram pribadi untuk **mencatat custom model AI** (format Cline) supaya kamu
tidak perlu lagi membuka tiap platform hanya untuk menyalin API key.

> Sering lupa apikey? Platform A harus dibuka dulu untuk masukin custom model lagi?
> Bot ini solusinya: catat sekali, nanti tinggal buka bot, lihat list, **salin**.

Setiap model menyimpan field standar Cline:

| Field | Contoh |
|---|---|
| **API Provider** | OpenRouter |
| **Base URL** | `https://openrouter.ai/api/v1` |
| **API Key** | `sk-or-v1-…` |
| **Model ID** | `anthropic/claude-3.5-sonnet` |

Plus **Catatan** dan **Tags** opsional untuk mempermudah pencarian.

## ✨ Fitur

- **/add `<nama>`** — tambah model secara interaktif (satu field per waktu)
- **/list** — daftar semua model (API key disembunyikan: `sk-or••••1234`)
- **/view `<nama>`** — detail lengkap + tombol salin untuk tiap field
- **Tombol salin** — ketuk "🔑 Salin API Key", masukkan PIN, key muncul untuk disalin
- **/search `<kata>`** — cari di nama / provider / model id / tags / catatan
- **/edit `<nama>` `<field>` `<nilai>`** — ubah satu field tanpa input ulang semuanya
- **/delete `<nama>`** — hapus (dengan konfirmasi)
- **/pin `<nama>`** — sematkan model favorit agar tampil paling atas
- **/export** — unduh seluruh data sebagai file JSON
- **/import** — balas file JSON dengan perintah ini untuk memulihkan data
- **/audit** — log aktivitas terakhir (siapa melakukan apa, kapan)
- **/stats** — ringkasan jumlah model

## 🛡️ Keamanan (rekomendasi tambahan dari kami)

1. **Whitelist user ID** — hanya ID Telegram kamu yang bisa memakai bot (`ALLOWED_USER_IDS`). Orang lain yang mencoba akses akan ditolak.
2. **PIN untuk membuka API key** — meski bisa membuka bot, API key hanya tampil setelah PIN benar (`REVEAL_PIN`). Mencegah mata-mata saat layar sedang dilihat orang lain.
3. **Database terenkripsi saat istirahat** — seluruh file DB dienkripsi dengan **Fernet (AES-128-CBC + HMAC)** sebelum dicommit ke repo sebagai backup. Bahkan repo-nya publik, isinya tetap aman.
4. **Tidak ada dependency selain `cryptography`** — bot memakai stdlib Python untuk komunikasi dengan Telegram, memperkecil permukaan serangan.
5. **/list selalu memmask key** — sekilas layar pun tidak membocorkan key.

> ⚠️ **Penting:** token bot dianggap rahasia. Jika pernah bocor, segera tekan
> `/revoke` di [@BotFather](https://t.me/BotFather) untuk membuat token baru.

## 🚀 Deploy Gratis 24/7

Bot ini berjalan di **GitHub Actions** — repo publik mendapat **menit Actions tak
terbatas**, jadi 100% gratis dan menyala 24/7.

Lihat **[DEPLOYMENT.md](DEPLOYMENT.md)** untuk panduan langkah demi langkah.

Singkatnya: isi 4 secret → buat repo publik → jalankan workflow `run-bot`.
Workflow otomatis menjalankan bot, auto-commit DB terenkripsi tiap 5 menit,
lalu me-rotate dirinya sendiri sebelum batas 6 jam habis (ditambah cron sebagai
jaring pengaman).

## 📁 Struktur Proyek

```
.
├── bot.py                        # Logika bot + long-polling Telegram (stdlib)
├── db.py                         # Penyimpanan SQLite terenkripsi (Fernet)
├── run.sh                        # Supervisor: restart, auto-backup, self-rotate
├── requirements.txt              # cryptography saja
├── .github/workflows/run-bot.yml # Workflow 24/7
├── data.db.enc                   # DB terenkripsi (auto-generated, dicommit)
└── DEPLOYMENT.md                 # Panduan deploy
```

## 🧪 Tes Cepat (lokal)

```bash
python3 test_offline.py   # memvalidasi esc() & upload multipart, tanpa network
```

## ❓ FAQ

**Kenapa API key-nya perlu PIN?**
Supaya jika HP/komputermu sedang dilihat orang lain, mereka tidak bisa langsung
membaca semua key. Pesan yang berisi key utuh hanya dikirim setelah PIN benar.

**Data saya disimpan mana?**
Di file `data.db.enc` yang sudah dienkripsi. File ini dicommit ke repo kamu
(aman karena terenkripsi) dan dibawa oleh setiap job runner.

**Bisa pakai untuk tim?**
Saat ini didesain untuk satu orang. Bisa ditambah user ID lain di
`ALLOWED_USER_IDS` (pisah koma), tapi semua orang itu akan memakai PIN yang sama.

**Kalau token bot bocor?**
Buka @BotFather → `/revoke` → token lama langsung mati. Perbarui secret
`BOT_TOKEN` di GitHub, lalu jalankan ulang workflow.