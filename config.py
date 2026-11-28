"""
config.py — Konstanta global untuk pipeline auto-shorts-renderer v2.

═══════════════════════════════════════════════════════════════════════════
⚠️  KONTRAK IMPOR (WAJIB DIBACA SEBELUM EDIT)
═══════════════════════════════════════════════════════════════════════════

Modul ini membaca environment variable `VIDEO_MODE` sekali saat import.
Karena Python melakukan module caching, `IS_LONG`, `W`, `H`, `FINAL_CRF`,
dan `DEFAULT_STYLE_NOTE` TIDAK akan berubah walau env diubah setelah
modul ini ter-import.

Oleh karena itu:
  1. `main.py` WAJIB mengeset `os.environ["VIDEO_MODE"]` (nilai: "SHORT"
     atau "LONG") SEBELUM import `pipeline`, `remotion_builder`, atau
     modul lain yang meng-import `config`.
  2. Untuk komputasi runtime pasca-import, gunakan fungsi `detect_mode()`
     atau akses ulang `os.environ["VIDEO_MODE"]` — JANGAN mengandalkan
     konstanta modul.

Alur yang benar:
    # main.py
    vo_dur = probe(vo_padat)["dur"]
    plan_mode = detect_mode(vo_dur)                    # "SHORT" / "LONG"
    os.environ["VIDEO_MODE"] = plan_mode               # ← set env DULU
    from pipeline import render_final_video            # ← baru import pipeline
═══════════════════════════════════════════════════════════════════════════
"""

import os


# ═══════════════════════════════════════════════════════════════════════════
# 1. VERSI
# ═══════════════════════════════════════════════════════════════════════════

CONFIG_VERSION = "v2.0.0"


# ═══════════════════════════════════════════════════════════════════════════
# 2. DETEKSI MODE (SHORT vs LONG)
# ═══════════════════════════════════════════════════════════════════════════

SHORT_MAX_SEC = 75.0
"""
Ambang batas deteksi mode otomatis.
- VO ≤ 75 dtk  → SHORT (vertikal 1080×1920)
- VO > 75 dtk  → LONG  (horizontal 1920×1080)
"""


def detect_mode(vo_dur: float) -> str:
    """
    Tentukan mode render dari durasi VO padat (detik).

    Args:
        vo_dur: durasi audio VO setelah dipadatkan (float, detik)

    Returns:
        "LONG" jika vo_dur > SHORT_MAX_SEC, selain itu "SHORT"
    """
    try:
        return "LONG" if float(vo_dur) > SHORT_MAX_SEC else "SHORT"
    except (TypeError, ValueError):
        return "SHORT"


def _read_env_mode() -> str:
    """Baca VIDEO_MODE dari env; fallback ke SHORT jika kosong/tidak valid."""
    m = os.environ.get("VIDEO_MODE", "").strip().upper()
    return m if m in ("SHORT", "LONG") else "SHORT"


IS_LONG: bool = _read_env_mode() == "LONG"
"""True jika mode render adalah LONG (16:9 horizontal). Dihitung saat import."""


# ═══════════════════════════════════════════════════════════════════════════
# 3. DIMENSI FRAME
# ═══════════════════════════════════════════════════════════════════════════

if IS_LONG:
    W, H = 1920, 1080
else:
    W, H = 1080, 1920

# Alias untuk remotion_builder (kompatibilitas kode lama):
OUT_W, OUT_H = W, H

FPS = 30
"""Frame rate final, dipakai konsisten di Remotion, FFmpeg, dan subtitle."""

CUT_FRAMES = 4
"""Jumlah frame di tepi cut untuk motion whip (kin/kout)."""


# ═══════════════════════════════════════════════════════════════════════════
# 4. EKSTENSI FILE
# ═══════════════════════════════════════════════════════════════════════════

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
VID_EXT = (".mov", ".mp4")

SKIP_PREFIXES = ("output_", "short_", "long_")
"""Prefix nama file yang dilewati saat scan folder proyek (hindari rekursif)."""


# ═══════════════════════════════════════════════════════════════════════════
# 5. PENGATURAN GAMBAR DIAM (STILL → VIDEO)
# ═══════════════════════════════════════════════════════════════════════════

STILL_DUR = 5.0
"""Durasi default 1 gambar diam dikonversi menjadi klip video (detik)."""

STILL_KB_END = 1.08
"""Zoom akhir Ken Burns untuk gambar diam (1.08 = +8% zoom dari frame awal)."""

DRIFT = 0.025
"""Micro-drift kamera video (0.025 = 2.5% pan sepanjang klip, beri feel hidup)."""

GRAIN = 3
"""Intensitas grain film FFmpeg `noise=alls=N` (0 = tanpa grain)."""


# ═══════════════════════════════════════════════════════════════════════════
# 6. ENCODE SETTINGS (H.264 FINAL)
# ═══════════════════════════════════════════════════════════════════════════

FINAL_CRF = 22 if IS_LONG else 23
"""CRF H.264: 22 untuk LONG (sedikit lebih baik), 23 untuk SHORT (file kecil)."""

FINAL_MAXRATE = "10M" if IS_LONG else "9M"
"""Batas bitrate maksimum final (VBV)."""

FINAL_FPS = 30
FINAL_AUDIO_BITRATE = "192k"
FINAL_AUDIO_SR = 48000

AUDIO_SR = 48000
"""Sample rate standar (dipakai di tighten_vo, mix, SFX, ALIMITER)."""


# ═══════════════════════════════════════════════════════════════════════════
# 7. BATAS OVERLAY
# ═══════════════════════════════════════════════════════════════════════════

OVERLAY_MAX = 25 if IS_LONG else 8
"""Batas maksimum overlay per video (di luar hook & CTA)."""

OVERLAY_MIN_GAP = 6.0 if IS_LONG else 5.0
"""Jeda minimum antar-overlay (detik)."""

ALLOWED_OVERLAY_TYPES = {
    "roto_typo",
    "kinetic_typo",
    "marker_quote",
    "floating_card",
    "camera_beat",
    "stat_counter",
    "draw_on",
}
"""Set tipe overlay yang didukung Remotion v2 (harus sinkron dengan REG di Overlay.tsx)."""


# ═══════════════════════════════════════════════════════════════════════════
# 8. AUDIO (LOUDNESS)
# ═══════════════════════════════════════════════════════════════════════════

TARGET_LUFS = -16.0
"""Target loudness integrasi (LUFS) — standar YouTube/TikTok."""

LOUDNESS_TOLERANCE = 0.1
"""Toleransi iterasi gain (± LUFS). Jika selisih < toleransi, berhenti iterasi."""

LOUDNESS_MAX_ITER = 8
"""Maksimum iterasi penyesuaian gain (safety loop)."""


# ═══════════════════════════════════════════════════════════════════════════
# 9. TEMA WARNA
# ═══════════════════════════════════════════════════════════════════════════

THEMES = {
    "hutan": dict(
        g=(31, 61, 43), d=(22, 46, 32), m=(45, 92, 67),
        c=(242, 232, 213), a=(224, 138, 46), k=(17, 17, 17), v=(8, 20, 13),
    ),
    "laut": dict(
        g=(14, 42, 71), d=(9, 29, 51), m=(28, 74, 115),
        c=(234, 244, 250), a=(255, 183, 3), k=(8, 14, 24), v=(4, 12, 22),
    ),
    "api": dict(
        g=(70, 18, 22), d=(48, 10, 14), m=(110, 34, 34),
        c=(250, 238, 220), a=(255, 196, 0), k=(20, 8, 8), v=(24, 4, 6),
    ),
    "malam": dict(
        g=(18, 18, 24), d=(10, 10, 15), m=(40, 40, 56),
        c=(245, 245, 245), a=(0, 230, 118), k=(0, 0, 0), v=(0, 0, 0),
    ),
    "bumi": dict(
        g=(74, 50, 32), d=(50, 32, 20), m=(110, 78, 50),
        c=(245, 236, 220), a=(232, 163, 23), k=(20, 12, 6), v=(24, 14, 6),
    ),
}
"""Palet tema. Setiap key = (green, dark, mid, cream, accent, black, vignette)."""

DEFAULT_THEME = "bumi"


# ═══════════════════════════════════════════════════════════════════════════
# 10. GAYA DEFAULT UNTUK PLANNER
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_STYLE_NOTE = (
    "Format video panjang landscape 16:9 dokumenter budaya yang dinamis dan hidup. "
    "Susun 15-25 overlay berjarak rapi menggunakan kombinasi roto_typo, "
    "kinetic_typo, marker_quote, stat_counter, dan camera_beat."
    if IS_LONG else
    "Gunakan variasi dinamis roto_typo, kinetic_typo, marker_quote, "
    "stat_counter, dan camera_beat agar layar selalu hidup dan organik."
)
"""
Catatan gaya default untuk auto-director. Kalau user kirim INPUT_NOTE,
teks itu akan di-append (bukan menggantikan) supaya panduan format tetap ada.
"""


# ═══════════════════════════════════════════════════════════════════════════
# 11. TAG MARKUP INLINE (SMART PARSER)
# ═══════════════════════════════════════════════════════════════════════════

MARKUP_TAGS = (
    "ROTO",
    "KINETIC",
    "QUOTE",
    "CHART",
    "CARD",
    "DRAW",
    "MAP",
    "HOOK",
    "CTA",
)
"""Daftar tag yang didukung smart_parser. Tag lain (mis. [JEDA 2 DETIK]) akan dibuang."""

TAG_TO_OVERLAY_TYPE = {
    "ROTO": "roto_typo",
    "KINETIC": "kinetic_typo",
    "QUOTE": "marker_quote",
    "CHART": "stat_counter",
    "CARD": "floating_card",
    "DRAW": "draw_on",
    "MAP": "camera_beat",
}
"""Mapping tag → tipe overlay Remotion. HOOK & CTA ditangani khusus (feed ke state, bukan overlay)."""

TAG_ANCHOR_MIN_WORDS = 3
TAG_ANCHOR_MAX_WORDS = 5
"""Jumlah kata sesudah tag yang dipakai sebagai anchor trigger waktu."""

TAG_ANCHOR_STOPWORDS = {
    "yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada", "ini",
    "itu", "ada", "atau", "juga", "oleh", "akan", "bisa", "tidak", "lebih",
    "sangat", "seperti", "karena", "namun", "tetapi", "sehingga", "antara",
    "secara", "berdasarkan", "hasil", "tahun", "berselang", "mereka", "kita",
    "kami", "adalah", "sebagai", "agar", "para", "sudah", "telah", "saat",
    "dalam", "warga", "banyak", "sedang", "masih", "hanya", "bagi", "tapi",
    "jika", "kalau", "maka", "pun", "lah", "kah", "nya",
}
"""Stopword Indonesia yang dilewati saat memilih anchor. Anchor = kata bermakna pertama."""


# ═══════════════════════════════════════════════════════════════════════════
# 12. SOUND EFFECTS (SFX) — SINTETIS, TANPA FILE .wav
# ═══════════════════════════════════════════════════════════════════════════

SFX_VARIANTS = ("hit", "whoosh", "pop")
"""Jenis SFX yang di-generate secara sintetis di pipeline (bukan file)."""

SFX_DEFAULT_FOR_TYPE = {
    "roto_typo": "whoosh",
    "kinetic_typo": "hit",
    "marker_quote": "pop",
    "floating_card": "pop",
    "camera_beat": "whoosh",
    "stat_counter": "hit",
    "draw_on": "whoosh",
}
"""SFX default per tipe overlay. Kalau overlay punya field 'sfx', override dipakai."""

SFX_GAIN_DB = -12.0
"""Attenuasi global SFX (dB relatif VO). Negatif = SFX lebih pelan dari VO."""


# ═══════════════════════════════════════════════════════════════════════════
# 13. RENDER SAFETY (GITHUB ACTIONS)
# ═══════════════════════════════════════════════════════════════════════════

RENDER_CONCURRENCY = 2
"""Concurrency Remotion — hardcoded 2 sesuai brief (cegah OOM di 16 GB RAM)."""

WHISPER_MODEL = "small"
"""Model faster-whisper. 'small' = keseimbangan akurasi vs kecepatan di CPU."""

WHISPER_COMPUTE_TYPE = "int8"
"""Quantization faster-whisper — 'int8' cukup akurat & cepat di CPU tanpa GPU."""

WHISPER_LANGUAGE = "id"
"""Bahasa alignment Whisper (Indonesia)."""

REMOTION_DIR = "/tmp/remotion_overlay"
WORK_DIR = "/tmp/work_proj"
"""Path default di GitHub Actions runner (ephemeral, di-mount otomatis)."""


# ═══════════════════════════════════════════════════════════════════════════
# 14. CAMERA RIG (opsional — user bisa matikan)
# ═══════════════════════════════════════════════════════════════════════════

RIG_ENABLED: bool = (
    os.environ.get("RIG_OFF", "").strip().lower() not in ("1", "true", "yes", "on")
)
"""
True = CameraRig aktif (drift sinusoidal tipis di semua overlay, feel handheld).
False = matikan via env `RIG_OFF=1`.

Dibaca saat import. Nilai di-pass ke Remotion lewat props.json sebagai
`applyGlobalRig` (bool) di root props.
"""


# ═══════════════════════════════════════════════════════════════════════════
# 15. WHISPER ALIGNMENT (TIMING)
# ═══════════════════════════════════════════════════════════════════════════

WHISPER_MIN_MATCH_RATIO = 0.5
"""Ambang minimum rasio kata yang cocok dengan transkrip (peringatan jika di bawah)."""

WORD_MIN_DUR = 0.04
"""Durasi minimum 1 kata (detik)."""


# ═══════════════════════════════════════════════════════════════════════════
# 16. CAPTION (SUBTITLE) — BATAS KARAKTER PER BARIS
# ═══════════════════════════════════════════════════════════════════════════

CAPTION_MAX_LINE_CHARS_LONG = 68
CAPTION_MAX_LINE_CHARS_SHORT = 42
CAPTION_MAX_HALF_CHARS_LONG = 36
CAPTION_MAX_HALF_CHARS_SHORT = 21
"""Batas karakter untuk pemecahan baris subtitle (LONG lebih panjang karena lebar)."""


# ═══════════════════════════════════════════════════════════════════════════
# 17. THEME LOOKUP HELPER
# ═══════════════════════════════════════════════════════════════════════════

def safe_theme(name: str) -> str:
    """Kembalikan nama tema valid; fallback ke DEFAULT_THEME jika tidak dikenal."""
    return name if name in THEMES else DEFAULT_THEME


__all__ = [
    "CONFIG_VERSION",
    "SHORT_MAX_SEC", "detect_mode", "_read_env_mode", "IS_LONG",
    "W", "H", "OUT_W", "OUT_H", "FPS", "CUT_FRAMES",
    "IMG_EXT", "VID_EXT", "SKIP_PREFIXES",
    "STILL_DUR", "STILL_KB_END", "DRIFT", "GRAIN",
    "FINAL_CRF", "FINAL_MAXRATE", "FINAL_FPS",
    "FINAL_AUDIO_BITRATE", "FINAL_AUDIO_SR", "AUDIO_SR",
    "OVERLAY_MAX", "OVERLAY_MIN_GAP", "ALLOWED_OVERLAY_TYPES",
    "TARGET_LUFS", "LOUDNESS_TOLERANCE", "LOUDNESS_MAX_ITER",
    "THEMES", "DEFAULT_THEME", "DEFAULT_STYLE_NOTE",
    "MARKUP_TAGS", "TAG_TO_OVERLAY_TYPE",
    "TAG_ANCHOR_MIN_WORDS", "TAG_ANCHOR_MAX_WORDS", "TAG_ANCHOR_STOPWORDS",
    "SFX_VARIANTS", "SFX_DEFAULT_FOR_TYPE", "SFX_GAIN_DB",
    "RENDER_CONCURRENCY", "WHISPER_MODEL", "WHISPER_COMPUTE_TYPE",
    "WHISPER_LANGUAGE", "REMOTION_DIR", "WORK_DIR",
    "RIG_ENABLED",
    "WHISPER_MIN_MATCH_RATIO", "WORD_MIN_DUR",
    "CAPTION_MAX_LINE_CHARS_LONG", "CAPTION_MAX_LINE_CHARS_SHORT",
    "CAPTION_MAX_HALF_CHARS_LONG", "CAPTION_MAX_HALF_CHARS_SHORT",
    "safe_theme",
]