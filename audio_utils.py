"""
audio_utils.py — Utilitas audio & timing untuk pipeline auto-shorts-renderer v2.

═══════════════════════════════════════════════════════════════════════════
FUNGSI PUBLIK:
  - sh(cmd, check=True, cwd=None)              : wrapper subprocess
  - _concat_quote(path)                        : escape path untuk FFmpeg concat
  - probe(path)                                : ffprobe → dict info media
  - tighten_vo(src, work_dir, ...)             : padatkan VO (buang jeda) → WAV
  - measure(path)                              : ukur LUFS & true peak
  - _clean_script_text(raw)                    : bersihkan markdown/tag kasar
  - align_whisper(vo_path, clean_text, dur)    : alignment kata via faster-whisper

KONTRAK PENTING:
  `align_whisper` TIDAK melakukan cleaning teks. Caller WAJIB mengirim
  `clean_text` yang sudah dibersihkan (mis. via `smart_parser.strip_tags`).
  Fungsi ini juga TIDAK mengunduh model Whisper — model di-cache in-memory
  setelah pemanggilan pertama (kunci: nama model).

  Modul ini TIDAK meng-import `config.py` (kecuali konstanta FPS yang
  tidak diperlukan). Semua fungsi murni stdlib + numpy + faster_whisper.
═══════════════════════════════════════════════════════════════════════════
"""

import os
import re
import json
import difflib
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.io import wavfile


# ═══════════════════════════════════════════════════════════════════════════
# CACHE MODEL WHISPER (in-memory, per-proses)
# ═══════════════════════════════════════════════════════════════════════════

_WHISPER_CACHE: Dict[str, Any] = {}
"""Cache model Whisper per nama model. Kunci: nama model ('small', 'base', dll)."""


# ═══════════════════════════════════════════════════════════════════════════
# 1. SUBPROCESS HELPER
# ═══════════════════════════════════════════════════════════════════════════

def sh(cmd, check: bool = True, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """
    Jalankan perintah shell / list-argumen dengan capture output.

    Args:
        cmd     : str (dijalankan via shell) atau list[str] (tanpa shell)
        check   : jika True (default) dan returncode != 0 → raise RuntimeError
        cwd     : working directory opsional

    Returns:
        subprocess.CompletedProcess (r.stdout, r.stderr, r.returncode)

    Raises:
        RuntimeError jika check=True dan returncode != 0
    """
    r = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if check and r.returncode != 0:
        shown = cmd if isinstance(cmd, str) else " ".join(map(str, cmd))
        raise RuntimeError(f"CMD GAGAL: {shown[:600]}\n{r.stderr[-1800:]}")
    return r


def _concat_quote(path: str) -> str:
    """
    Escape path agar aman untuk file list FFmpeg concat (delimiter single quote).
    Contoh: "it's a file.mp4" → "it'\\''s a file.mp4"
    """
    return path.replace("'", "'\\''")


# ═══════════════════════════════════════════════════════════════════════════
# 2. PROBE MEDIA
# ═══════════════════════════════════════════════════════════════════════════

def probe(path: str) -> Dict[str, Any]:
    """
    Ambil metadata media via ffprobe.

    Returns dict dengan kunci yang ADA hanya jika stream-nya ada:
      - dur      : float, durasi (detik). 0.0 jika tidak terbaca.
      - w, h     : int, dimensi video (sudah dikoreksi rotasi)
      - rot      : int, derajat rotasi (0 / 90 / -90 / 180)
      - vertical : bool, True jika h > w
      - ch       : int, jumlah channel audio
    """
    r = sh([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ])
    j = json.loads(r.stdout)
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)

    raw_dur = (
        j.get("format", {}).get("duration")
        or (v.get("duration") if v else None)
        or 0.0
    )
    try:
        dur_val = float(raw_dur)
    except (TypeError, ValueError):
        dur_val = 0.0

    info: Dict[str, Any] = {"dur": dur_val}

    if v:
        try:
            rot = int(float(next(
                (
                    sd["rotation"]
                    for sd in v.get("side_data_list", [])
                    if "rotation" in sd
                ),
                v.get("tags", {}).get("rotate", 0),
            )))
        except (TypeError, ValueError):
            rot = 0
        w, h = int(v["width"]), int(v["height"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        info.update(w=w, h=h, rot=rot, vertical=h > w)

    if a:
        info["ch"] = int(a.get("channels", 1))

    return info


# ═══════════════════════════════════════════════════════════════════════════
# 3. PEMADATAN VO (HAPUS JEDA PANJANG)
# ═══════════════════════════════════════════════════════════════════════════

def tighten_vo(
    src: str,
    work_dir: str,
    tight_db: int = -33,
    max_pause: float = 0.50,
    keep_inner: float = 0.07,
    keep_long: float = 0.45,
) -> str:
    """
    Padatkan audio VO dengan membuang jeda hening berlebih.

    Logika:
      - Deteksi hening dengan silencedetect FFmpeg (noise -33 dB, durasi ≥ 0.10 dtk)
      - Jeda pendek (< max_pause) → sisakan keep_inner (default 0.07 dtk)
      - Jeda panjang (≥ max_pause) → sisakan keep_long (default 0.45 dtk)
      - Jeda awal file → sisakan ≤ 0.12 dtk
      - Jeda akhir file → sisakan ≤ 0.25 dtk
      - Fade in/out 3 ms antar-piece untuk hindari klik

    Args:
        src        : path audio input (mp3/m4a/wav)
        work_dir   : folder output (menulis VO_padat.wav)
        tight_db   : ambang noise silencedetect (dB)
        max_pause  : batas jeda "panjang" (detik)
        keep_inner : sisa jeda pendek (detik)
        keep_long  : sisa jeda panjang (detik)

    Returns:
        path file WAV hasil pemadatan
    """
    SR = 48000
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src,
         "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True,
    ).stdout
    x = np.frombuffer(raw, np.float32).copy()
    assert len(x) > 0, f"Audio VO kosong/tidak terbaca: {src}"

    dur = len(x) / SR
    r = sh([
        "ffmpeg", "-hide_banner", "-i", src,
        "-af", f"silencedetect=noise={tight_db}dB:d=0.10",
        "-f", "null", "-",
    ])
    ss = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    se = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    sil = [(max(s, 0.0), min(se[i] if i < len(se) else dur, dur)) for i, s in enumerate(ss)]

    pieces: List[np.ndarray] = []
    cur = 0.0
    for s, e in sil:
        L = e - s
        if s <= 0.02:
            keep = min(L, 0.12)
        elif e >= dur - 0.02:
            keep = min(L, 0.25)
        elif L < max_pause:
            keep = min(L, keep_inner)
        else:
            keep = min(L, keep_long)

        if keep >= L - 0.01:
            continue

        if s <= 0.02:
            head, tail = 0.0, keep
        elif e >= dur - 0.02:
            head, tail = keep, 0.0
        else:
            head, tail = keep / 2, keep / 2

        pieces.append(x[int(cur * SR):int((s + head) * SR)])
        cur = e - tail

    pieces.append(x[int(cur * SR):])

    # Fade 3 ms di tepi tiap piece agar tidak klik
    fd = int(0.003 * SR)
    for i, p in enumerate(pieces):
        if len(p) > 2 * fd:
            if i > 0:
                p[:fd] *= np.linspace(0, 1, fd)
            if i < len(pieces) - 1:
                p[-fd:] *= np.linspace(1, 0, fd)

    y = np.concatenate(pieces)
    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(work_dir, "VO_padat.wav")
    wavfile.write(out, SR, y.astype(np.float32))
    print(f"VO dipadatkan: {dur:.2f} -> {len(y) / SR:.2f} dtk")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 4. MEASURE LUFS (LOUDNESS)
# ═══════════════════════════════════════════════════════════════════════════

def measure(path: str) -> Tuple[float, float]:
    """
    Ukur LUFS terintegrasi + true peak maksimum via ebur128 FFmpeg.

    Returns:
        (integrated_lufs, true_peak_dbfs)
        Nilai -inf jika audio senyap / tidak terbaca.
    """
    r = sh([
        "ffmpeg", "-nostats", "-i", path,
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ])
    li = re.findall(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", r.stderr)
    pk = re.findall(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", r.stderr)
    return (
        float(li[-1]) if li else float("-inf"),
        float(pk[-1]) if pk else float("-inf"),
    )


# ═══════════════════════════════════════════════════════════════════════════
# 5. CLEANING TEKS NARASI (KOMPATIBILITAS BALIK)
# ═══════════════════════════════════════════════════════════════════════════

def _clean_script_text(raw: str) -> str:
    """
    Bersihkan teks narasi dari:
      - Tag bracket apa pun [ ... ] (termasuk markup production & [JEDA X DETIK])
      - Karakter markdown bold/italic '*'
      - Normalisasi whitespace

    ⚠️  Fungsi ini akan MEMBUANG tag markup `[ROTO:...]` dll.
    Kalau Anda ingin memanfaatkan tag, panggil `smart_parser.parse_markup_tags(raw)`
    TERLEBIH DAHULU sebelum memanggil fungsi ini / `strip_tags`.
    """
    cleaned = re.sub(r"\[[^\]]*\]", " ", str(raw or ""))
    cleaned = cleaned.replace("*", "")
    return re.sub(r"\s+", " ", cleaned).strip()


# ═══════════════════════════════════════════════════════════════════════════
# 6. ALIGNMENT WHISPER (WORD-LEVEL TIMING)
# ═══════════════════════════════════════════════════════════════════════════

def _norm(w: str) -> str:
    """Normalisasi kata: hanya alfanumerik + lowercase (untuk pencocokan)."""
    return re.sub(r"[^\w]", "", w.lower())


def _syl(w: str) -> int:
    """Perkiraan jumlah suku kata (untuk alokasi durasi fallback)."""
    return max(len(re.findall(r"[aiueo]", w.lower())), 1) if re.search(r"\w", w) else 0


def align_whisper(
    vo_path: str,
    clean_text: str,
    dur: float,
    model_name: str = "small",
    language: str = "id",
    compute_type: str = "int8",
) -> Tuple[List[dict], List[List[int]], List[str]]:
    """
    Selaraskan kata-kata narasi dengan timestamp audio via faster-whisper.

    ⚠️  Caller WAJIB mengirim `clean_text` yang SUDAH dibersihkan.
    Fungsi ini TIDAK memanggil `_clean_script_text` internal — jadi tag
    `[ROTO:...]` dll harus di-strip dulu di `main.py` via `smart_parser.strip_tags`.

    Args:
        vo_path     : path audio VO padat
        clean_text  : teks narasi bersih (tanpa tag)
        dur         : durasi VO (detik) — dipakai untuk clamp akhir
        model_name  : nama model faster-whisper ('small', 'base', dll)
        language    : kode bahasa (default 'id' = Indonesia)
        compute_type: quantisasi ('int8' default, cepat di CPU tanpa GPU)

    Returns:
        (words, units, unit_texts)
          - words     : list dict {w, n, syl, u, t0, t1}
          - units     : list [start_idx, end_idx] per kalimat
          - unit_texts: list[str] kalimat asli (untuk dipakai planner)
    """
    unit_texts = [u for u in re.split(r"(?<=[.,!?;:…])\s+", clean_text) if u.strip()]

    words: List[dict] = []
    units: List[List[int]] = []
    for ui, u in enumerate(unit_texts):
        a = len(words)
        for w in u.split():
            words.append({"w": w, "n": _norm(w), "syl": _syl(w), "u": ui})
        units.append([a, len(words)])
    assert words, "Teks narasi kosong."

    r_sil = sh([
        "ffmpeg", "-hide_banner", "-i", vo_path,
        "-af", "silencedetect=noise=-33dB:d=0.10",
        "-f", "null", "-",
    ])
    ss = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r_sil.stderr)]
    se = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r_sil.stderr)]
    sil = [(max(s, 0.0), se[i] if i < len(se) else dur) for i, s in enumerate(ss)]
    sp0 = sil[0][1] if sil and sil[0][0] <= 0.05 else 0.0
    sp1 = sil[-1][0] if sil and sil[-1][1] >= dur - 0.05 else dur

    t_cur = sp0
    tot_s = sum(w["syl"] for w in words) or 1
    for w in words:
        d_w = (sp1 - sp0) * w["syl"] / tot_s
        w["t0"], w["t1"] = t_cur, t_cur + d_w
        t_cur += d_w

    from faster_whisper import WhisperModel

    raw_16k = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", vo_path,
         "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True,
    ).stdout
    audio_np = np.frombuffer(raw_16k, np.float32)

    if model_name not in _WHISPER_CACHE:
        _WHISPER_CACHE[model_name] = WhisperModel(
            model_name, device="cpu", compute_type=compute_type,
        )
    m = _WHISPER_CACHE[model_name]

    segs, _ = m.transcribe(
        audio_np,
        language=language,
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    ws = [
        (_norm(w.word), float(w.start), float(w.end))
        for sg in segs
        for w in (sg.words or [])
        if _norm(w.word)
    ]

    tgt = [w["n"] for w in words]
    src = [x[0] for x in ws]
    sm = difflib.SequenceMatcher(None, tgt, src, autojunk=False)
    n = len(words)
    t0: List[Optional[float]] = [None] * n
    t1: List[Optional[float]] = [None] * n

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                t0[i1 + k] = ws[j1 + k][1]
                t1[i1 + k] = ws[j1 + k][2]

    known = sum(x is not None for x in t0)
    print(f"Whisper: {known}/{n} kata cocok dengan transkrip!")
    if known < n * 0.5:
        print(
            "(!) Kecocokan Whisper rendah; timing sebagian besar memakai perkiraan "
            "suku kata. Periksa teks narasi vs audio."
        )

    i = 0
    while i < n:
        if t0[i] is not None:
            i += 1
            continue
        j = i
        while j < n and t0[j] is None:
            j += 1
        a = t1[i - 1] if i > 0 else sp0
        b = t0[j] if j < n else sp1
        if b < a:
            b = a + 0.05 * (j - i)
        tot = sum(words[k]["syl"] for k in range(i, j)) or 1
        t = a
        for k in range(i, j):
            d = (b - a) * words[k]["syl"] / tot
            t0[k], t1[k] = t, t + d
            t += d
        i = j

    for k in range(n):
        if k > 0 and t0[k] < t1[k - 1]:
            t0[k] = t1[k - 1]
        if t1[k] < t0[k] + 0.04:
            t1[k] = t0[k] + 0.04

    for k, w in enumerate(words):
        w["t0"] = min(t0[k], dur)
        w["t1"] = min(t1[k], dur)

    return words, units, unit_texts


__all__ = [
    "sh",
    "_concat_quote",
    "probe",
    "tighten_vo",
    "measure",
    "_clean_script_text",
    "align_whisper",
]