# Pipeline YouTube Shorts Otomatis (GitHub Actions + Telegram + Google Drive)

Sistem produksi YouTube Shorts otomatis dengan **Claude Auto Director**, **Whisper Alignment**, **Remotion Motion Graphics**, dan **FFmpeg Audio Mastering (-16 LUFS)** tanpa perlu membuka Google Colab.

## 1. Persiapan Repositori GitHub
1. Buat repositori baru di GitHub (disarankan **Public** agar kuota GitHub Actions gratis tanpa batas; seluruh kunci API tetap aman karena tersimpan terenkripsi di *Secrets*).
2. Unggah semua isi folder ini ke repositori tersebut (pastikan struktur `.github/workflows/render.yml` ikut terunggah).

## 2. Isi GitHub Repository Secrets
Buka **Settings -> Secrets and variables -> Actions -> New repository secret**, lalu tambahkan:
- `ANTHROPIC_API_KEY` : API key Claude (`sk-ant-...`)
- `TELEGRAM_BOT_TOKEN` : Token bot dari `@BotFather`
- `TELEGRAM_CHAT_ID` : ID akun Telegram Anda (cek lewat `@userinfobot`)
- `GDRIVE_CLIENT_ID` : Client ID dari `token.json` Google Drive Anda
- `GDRIVE_CLIENT_SECRET` : Client Secret dari `token.json` Google Drive Anda
- `GDRIVE_REFRESH_TOKEN` : Refresh Token dari `token.json` Google Drive Anda

*(Catatan: Jika memakai Google Service Account, cukup isi `GDRIVE_SERVICE_ACCOUNT_JSON` dan bagikan folder Drive ke email service account tersebut.)*

## 3. Pasang Jembatan Telegram (Cloudflare Worker — Gratis)
1. Buka dashboard [Cloudflare Workers](https://dash.cloudflare.com/) -> **Create Worker**.
2. Tempel seluruh isi file `cloudflare_worker.js`, klik **Deploy**.
3. Masuk ke **Settings -> Variables and Secrets**, tambahkan:
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_CHAT_ID`
   - `GITHUB_TOKEN` (buat Personal Access Token klasik dengan centang `repo` & `workflow`)
   - `GITHUB_OWNER` (username GitHub Anda)
   - `GITHUB_REPO` (nama repositori Anda)
   - `WORKFLOW_FILE` = `render.yml`
4. Daftarkan webhook Telegram dengan membuka URL berikut sekali di browser:
   `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=<URL_CLOUDFLARE_WORKER_ANDA>`

## 4. Cara Pakai Sehari-hari
1. Unggah `vo.mp3`, `transkrip.txt`, dan klip `.mp4`/`.jpg` ke folder Google Drive (misalnya `BARU`).
2. Kirim pesan di Telegram: `/render BARU`
3. Tunggu ±1–2 menit sampai bot mengirim daftar overlay & B-roll beserta tombol **✅ Setuju & Render Final**.
4. Jika ada yang ingin diubah, balas misalnya `/hapus BARU | 4, 8` atau `/tema BARU | hutan`.
5. Tekan **✅ Setuju & Render Final** — video final otomatis diunggah ke folder Google Drive yang sama!
