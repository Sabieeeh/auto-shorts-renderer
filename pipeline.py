import os
import re
import json
import math
import glob
import base64
import difflib
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.io import wavfile
from scipy.signal import butter, sosfilt
import anthropic
from remotion_builder import prepare_remotion_project

IS_LONG = os.environ.get("VIDEO_MODE", "").strip().upper() == "LONG"
FPS = 30
W = 1920 if IS_LONG else 1080
H = 1080 if IS_LONG else 1920

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
VID_EXT = (".mov", ".mp4")
STILL_DUR = 5.0
STILL_KB_END = 1.08
DRIFT = 0.06
CUT_FRAMES = 6
GRAIN = 3
FINAL_CRF = 22 if IS_LONG else 23
FINAL_MAXRATE = "10M" if IS_LONG else "9M"
OVERLAY_MAX = 25 if IS_LONG else 8
OVERLAY_MIN_GAP = 6.0 if IS_LONG else 5.0
SKIP_PREFIXES = ("output_", "short_", "long_")
ALLOWED_OVERLAY_TYPES = {"big", "lower", "stat", "stat_callout", "list", "icon", "qmark", "quote", "solid",
                        "cover_story", "headline_wall", "chart_grow", "frame_break"}
TARGET_LUFS = -16.0

THEMES = {
    "hutan": dict(g=(31, 61, 43), d=(22, 46, 32), m=(45, 92, 67), c=(242, 232, 213), a=(224, 138, 46), k=(17, 17, 17), v=(8, 20, 13)),
    "laut": dict(g=(14, 42, 71), d=(9, 29, 51), m=(28, 74, 115), c=(234, 244, 250), a=(255, 183, 3), k=(8, 14, 24), v=(4, 12, 22)),
    "api": dict(g=(70, 18, 22), d=(48, 10, 14), m=(110, 34, 34), c=(250, 238, 220), a=(255, 196, 0), k=(20, 8, 8), v=(24, 4, 6)),
    "malam": dict(g=(18, 18, 24), d=(10, 10, 15), m=(40, 40, 56), c=(245, 245, 245), a=(0, 230, 118), k=(0, 0, 0), v=(0, 0, 0)),
    "bumi": dict(g=(74, 50, 32), d=(50, 32, 20), m=(110, 78, 50), c=(245, 236, 220), a=(232, 163, 23), k=(20, 12, 6), v=(24, 14, 6)),
}

DEFAULT_STYLE_NOTE = (
    "Format video panjang landscape 16:9 dokumenter budaya Suku Baduy yang tenang dan informatif. "
    "Susun 15-25 overlay berjarak rapi, kombinasikan big, lower, stat, dan list. Batasi icon maksimal 3 kali."
    if IS_LONG else
    "Gunakan kombinasi dinamis big, lower, stat, dan list untuk konten dokumenter Suku Baduy. "
    "Batasi overlay tipe icon maksimal 2-3 kali saja agar tidak monoton."
)

_WHISPER_CACHE = {}


# ----------------------------------------------------------------------------
# Utilitas dasar
# ----------------------------------------------------------------------------
def sh(cmd, check=True, cwd=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        shown = cmd if isinstance(cmd, str) else " ".join(map(str, cmd))
        raise RuntimeError(f"CMD GAGAL: {shown[:600]}\n{r.stderr[-1800:]}")
    return r


def _concat_quote(path: str) -> str:
    """Escape path untuk file list ffmpeg concat."""
    return path.replace("'", "'\\''")


def probe(path: str) -> dict:
    r = sh(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    j = json.loads(r.stdout)
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    raw_dur = j.get("format", {}).get("duration") or (v.get("duration") if v else None) or 0.0
    try:
        dur_val = float(raw_dur)
    except (TypeError, ValueError):
        dur_val = 0.0
    info = {"dur": dur_val}
    if v:
        try:
            rot = int(float(next((sd["rotation"] for sd in v.get("side_data_list", []) if "rotation" in sd), v.get("tags", {}).get("rotate", 0))))
        except (TypeError, ValueError):
            rot = 0
        w, h = int(v["width"]), int(v["height"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        info.update(w=w, h=h, rot=rot, vertical=h > w)
    if a:
        info["ch"] = int(a.get("channels", 1))
    return info


def still_to_clip(src: str, out_p: str, dur: float = STILL_DUR) -> bool:
    """Ubah gambar diam menjadi klip mp4 sesuai orientasi (1920x1080 atau 1080x1920) 30 fps."""
    try:
        info = probe(src)
        iw, ih = int(info.get("w") or 0), int(info.get("h") or 0)
        if iw <= 0 or ih <= 0:
            return False
        max_fg_h = int(H * 0.88)
        sc = min(W / iw, max_fg_h / ih)
        fw, fh = max(2, int(iw * sc) // 2 * 2), max(2, int(ih * sc) // 2 * 2)
        k0 = 1.0 / STILL_KB_END
        grow = f"({k0:.5f}+(1-{k0:.5f})*t/{dur:.3f})"
        fc = (
            "[0:v]format=rgba,split=2[a][b];"
            f"[a]format=rgb24,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:5,eq=brightness=-0.10[bg];"
            f"[b]scale={fw}:{fh}:flags=lanczos[fg0];"
            f"[fg0]scale=w='trunc({fw}*{grow}/2)*2':h='trunc({fh}*{grow}/2)*2':eval=frame:flags=lanczos[fg];"
            "[bg][fg]overlay=x='(W-w)/2':y='(H-h)/2':format=auto,format=yuv420p[v]"
        )
        n = int(round(dur * FPS))
        sh([
            "ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", src,
            "-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-r", str(FPS),
            "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", out_p,
        ])
        return os.path.exists(out_p) and os.path.getsize(out_p) > 1000
    except Exception as e:
        print(f"(!) Gagal konversi gambar {os.path.basename(src)}: {str(e)[:200]}")
        return False


def prepare_still_clips(clips: list, work_dir: str) -> list:
    still_dir = os.path.join(work_dir, "stills")
    os.makedirs(still_dir, exist_ok=True)
    for c in clips:
        if not c.get("is_img"):
            continue
        out_p = os.path.join(still_dir, f"{c['key']}.mp4")
        cached = os.path.exists(out_p) and os.path.getsize(out_p) > 1000
        if cached or still_to_clip(c["path"], out_p):
            c.update(path=out_p, dur=STILL_DUR, is_img=False, vertical=(not IS_LONG), still=True)
            print("Gambar -> klip:", c["name"])
        else:
            c["dur"] = STILL_DUR
    return clips


def shot_zoom_filter(fr: int) -> str:
    """Zoom-in halus 1.00 -> 1.03 tanpa hentakan, tanpa blur transisi."""
    z = f"1+0.03*on/{max(fr - 1, 1)}"
    return f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}"


def measure(path: str):
    """Kembalikan (LUFS terintegrasi, true peak dBFS); -inf bila audio senyap."""
    r = sh(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    li = re.findall(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", r.stderr)
    pk = re.findall(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", r.stderr)
    return (float(li[-1]) if li else float("-inf")), (float(pk[-1]) if pk else float("-inf"))


# ----------------------------------------------------------------------------
# Teks & timing
# ----------------------------------------------------------------------------
def syl(w: str) -> int:
    return max(len(re.findall(r"[aiueo]", w.lower())), 1) if re.search(r"\w", w) else 0


def norm(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower())


def similar(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.82


def find_trigger(words: list, trig: str, after: int = 0):
    if not trig:
        return None
    tk = [norm(x) for x in str(trig).split() if norm(x)]
    if not tk:
        return None
    for i in range(after, len(words) - len(tk) + 1):
        if all(similar(words[i + k]["n"], tk[k]) for k in range(len(tk))):
            return i
    return None


def tighten_vo(src: str, work_dir: str, tight_db: int = -33, max_pause: float = 0.50, keep_inner: float = 0.07, keep_long: float = 0.45) -> str:
    SR = 48000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32).copy()
    assert len(x) > 0, f"Audio VO kosong/tidak terbaca: {src}"
    dur = len(x) / SR
    r = sh(["ffmpeg", "-hide_banner", "-i", src, "-af", f"silencedetect=noise={tight_db}dB:d=0.10", "-f", "null", "-"])
    ss = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    se = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    sil = [(max(s, 0.0), min(se[i] if i < len(se) else dur, dur)) for i, s in enumerate(ss)]
    pieces, cur = [], 0.0
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
        head, tail = (0.0, keep) if s <= 0.02 else ((keep, 0.0) if e >= dur - 0.02 else (keep / 2, keep / 2))
        pieces.append(x[int(cur * SR):int((s + head) * SR)])
        cur = e - tail
    pieces.append(x[int(cur * SR):])
    fd = int(0.003 * SR)
    for i, p in enumerate(pieces):
        if len(p) > 2 * fd:
            if i > 0:
                p[:fd] *= np.linspace(0, 1, fd)
            if i < len(pieces) - 1:
                p[-fd:] *= np.linspace(1, 0, fd)
    y = np.concatenate(pieces)
    out = os.path.join(work_dir, "VO_padat.wav")
    wavfile.write(out, SR, y.astype(np.float32))
    print(f"VO dipadatkan: {dur:.2f} -> {len(y) / SR:.2f} dtk")
    return out


def align_whisper(vo_path: str, text: str, dur: float, model_name: str = "small"):
    r_sil = sh(["ffmpeg", "-hide_banner", "-i", vo_path, "-af", "silencedetect=noise=-33dB:d=0.10", "-f", "null", "-"])
    ss = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r_sil.stderr)]
    se = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r_sil.stderr)]
    sil = [(max(s, 0.0), se[i] if i < len(se) else dur) for i, s in enumerate(ss)]
    sp0 = sil[0][1] if sil and sil[0][0] <= 0.05 else 0.0
    sp1 = sil[-1][0] if sil and sil[-1][1] >= dur - 0.05 else dur

    unit_texts = [u for u in re.split(r"(?<=[.,!?;:…])\s+", text) if u.strip()]
    words, units = [], []
    for ui, u in enumerate(unit_texts):
        a = len(words)
        for w in u.split():
            words.append({"w": w, "n": norm(w), "syl": syl(w), "u": ui})
        units.append([a, len(words)])
    assert words, "Teks narasi kosong."

    # Perkiraan awal berbasis suku kata (cadangan bila Whisper tidak cocok)
    t_cur = sp0
    tot_s = sum(w["syl"] for w in words) or 1
    for w in words:
        d_w = (sp1 - sp0) * w["syl"] / tot_s
        w["t0"], w["t1"] = t_cur, t_cur + d_w
        t_cur += d_w

    from faster_whisper import WhisperModel
    raw_16k = subprocess.run(["ffmpeg", "-v", "error", "-i", vo_path, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"], capture_output=True).stdout
    audio_np = np.frombuffer(raw_16k, np.float32)

    if model_name not in _WHISPER_CACHE:
        _WHISPER_CACHE[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
    m = _WHISPER_CACHE[model_name]
    segs, _ = m.transcribe(audio_np, language="id", word_timestamps=True, beam_size=5, condition_on_previous_text=False)
    ws = [(norm(w.word), float(w.start), float(w.end)) for sg in segs for w in (sg.words or []) if norm(w.word)]

    tgt, src = [w["n"] for w in words], [x[0] for x in ws]
    sm = difflib.SequenceMatcher(None, tgt, src, autojunk=False)
    n = len(words)
    t0, t1 = [None] * n, [None] * n
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                t0[i1 + k], t1[i1 + k] = ws[j1 + k][1], ws[j1 + k][2]
    known = sum(x is not None for x in t0)
    print(f"Whisper: {known}/{n} kata cocok dengan transkrip!")
    if known < n * 0.5:
        print("(!) Kecocokan Whisper rendah; timing sebagian besar memakai perkiraan suku kata. Periksa teks narasi vs audio.")

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
        w["t0"], w["t1"] = min(t0[k], dur), min(t1[k], dur)

    return words, units, unit_texts


def build_captions(words: list, units: list, keywords: list, dur: float) -> list:
    kw_tok = [[norm(x) for x in k.split()] for k in keywords]
    kw_single = {w for kw in kw_tok for w in kw if len(w) >= 6}
    max_line_chars = 68 if IS_LONG else 42
    max_half_chars = 36 if IS_LONG else 21

    def split_groups(ws):
        txt = " ".join(w["w"] for w in ws)
        if len(txt) <= max_line_chars:
            return [ws]
        k = math.ceil(len(txt) / max_line_chars)
        target = len(txt) / k
        groups, cur = [], []
        for w in ws:
            cur.append(w)
            if len(groups) < k - 1 and len(" ".join(x["w"] for x in cur)) >= target:
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)
        # Gabungkan grup pendek/1 kata ke tetangga agar tidak ada subtitel yatim
        merged = []
        for g in groups:
            if merged and (len(g) <= 1 or len(" ".join(x["w"] for x in g)) <= 4):
                merged[-1].extend(g)
            elif len(g) <= 1 and not merged:
                merged.append(list(g))
            else:
                merged.append(list(g))
        if len(merged) > 1 and (len(merged[0]) <= 1 or len(" ".join(x["w"] for x in merged[0])) <= 4):
            merged[1] = merged[0] + merged[1]
            merged.pop(0)
        i = 1
        while i < len(merged):
            if len(merged[i]) <= 1 or len(" ".join(x["w"] for x in merged[i])) <= 4:
                merged[i - 1].extend(merged[i])
                merged.pop(i)
            else:
                i += 1
        return merged

    def break2(ws):
        n = len(ws)
        if len(" ".join(w["w"] for w in ws)) <= max_half_chars or n < 4:
            return [ws]
        best = min(((max(len(" ".join(x["w"] for x in ws[:i])), len(" ".join(x["w"] for x in ws[i:]))), i) for i in range(2, n - 1)), key=lambda x: x[0])
        return [ws[:best[1]], ws[best[1]:]]

    def flags_for(ws):
        norms = [w["n"] for w in ws]
        fl, cnt = [False] * len(ws), 0
        for kw in kw_tok:
            L = len(kw)
            if L == 0:
                continue
            for i in range(len(ws) - L + 1):
                if norms[i:i + L] == kw and not any(fl[i:i + L]) and cnt < 2:
                    for j in range(L):
                        fl[i + j] = True
                    cnt += 1
                    break
        if cnt == 0:
            for i, n in enumerate(norms):
                if n in kw_single:
                    fl[i] = True
                    break
        return fl

    cues = []
    for a, b in units:
        for g in split_groups(words[a:b]):
            fl = flags_for(g)
            idx = {id(w): f for w, f in zip(g, fl)}
            capl = [[{"w": w["w"], "kw": bool(idx[id(w)]), "t0": w["t0"], "t1": w["t1"]} for w in ln] for ln in break2(g)]
            cues.append({"s": max(g[0]["t0"] - 0.04, 0.0), "e": g[-1]["t1"] + 0.12, "cap": capl})
    for i in range(1, len(cues)):
        if cues[i]["s"] < cues[i - 1]["e"] + 0.03:
            cues[i - 1]["e"] = max(cues[i]["s"] - 0.03, cues[i - 1]["s"] + 0.15)
    if cues:
        cues[-1]["e"] = min(cues[-1]["e"], dur)
    return [{"s": c["s"], "e": c["e"], "lines": c["cap"]} for c in cues]


# ----------------------------------------------------------------------------
# Overlay
# ----------------------------------------------------------------------------
def _transcript_number_tokens(text: str) -> set:
    nums = set()
    for m in re.findall(r"\d+(?:[\.,]\d+)?%?", str(text or "")):
        nums.add(m.replace(" ", ""))
        nums.add(m.replace(",", "."))
        nums.add(m.replace(".", ","))
    return {n for n in nums if n}


def _overlay_has_hallucinated_stat(o: dict, transcript_nums: set) -> bool:
    t = o.get("type")
    if t not in ("stat", "stat_callout"):
        return False
    value = str(o.get("value") or o.get("text") or (o.get("lines") or [""])[0] or "")
    value_nums = re.findall(r"\d+(?:[\.,]\d+)?%?", value)
    if not value_nums:
        return False
    for n in value_nums:
        variants = {n, n.replace(",", "."), n.replace(".", ",")}
        if not (variants & transcript_nums):
            return True
    return False


def _clean_overlay_text(s: str, max_words: int = 8, max_chars: int = 56) -> str:
    txt = re.sub(r"\s+", " ", str(s or "")).strip(" -:;,.")
    if not txt:
        return ""
    words = txt.split()
    if len(words) > max_words:
        txt = " ".join(words[:max_words]).strip(" -:;,.")
    if len(txt) > max_chars:
        cut = txt[:max_chars].rstrip()
        i = cut.rfind(" ")
        txt = (cut[:i] if i > 8 else cut).strip(" -:;,.")
    return txt


def _normalize_overlay_type(o: dict) -> dict:
    """Normalisasi tipe lama/alias dan bersihkan teks overlay agar tidak terpotong."""
    if o.get("type") == "stat_callout":
        o["type"] = "stat"
    if o.get("type") == "map":
        txt = str(o.get("label") or o.get("sub") or o.get("text") or "").strip()
        o.pop("pin", None)
        o.pop("zoom", None)
        o.pop("sub", None)
        o.pop("label", None)
        if txt and len(txt.split()) <= 3 and len(txt) <= 24:
            o["type"] = "big"
            o["lines"] = [txt.upper()]
        else:
            o["type"] = "lower"
            o["badge"] = o.get("badge") or "CATATAN BUDAYA"
            o["text"] = (txt or "WILAYAH ADAT").upper()

    t = o.get("type")
    if t == "big":
        lines = []
        for l in (o.get("lines") or [])[:3]:
            cl = _clean_overlay_text(l, max_words=5, max_chars=28).upper()
            if cl:
                lines.append(cl)
        if not lines and o.get("text"):
            cl = _clean_overlay_text(o["text"], max_words=5, max_chars=28).upper()
            if cl:
                lines = [cl]
        o["lines"] = lines
    elif t == "lower":
        o["badge"] = _clean_overlay_text(o.get("badge") or "CATATAN BUDAYA", max_words=3, max_chars=24).upper()
        o["text"] = _clean_overlay_text(o.get("text") or "", max_words=6, max_chars=42).upper()
    elif t == "stat":
        o["value"] = _clean_overlay_text(o.get("value") or o.get("text") or "", max_words=4, max_chars=24).upper()
        o["label"] = _clean_overlay_text(o.get("label") or "", max_words=6, max_chars=42).upper()
    elif t in ("list", "quote"):
        o["title"] = _clean_overlay_text(o.get("title") or "RINGKASAN FAKTA", max_words=4, max_chars=28).upper()
        items = []
        for it in (o.get("items") or [])[:4]:
            if isinstance(it, dict):
                txt = _clean_overlay_text(it.get("text") or "", max_words=6, max_chars=38).upper()
                if txt:
                    nit = dict(it)
                    nit["text"] = txt
                    items.append(nit)
            else:
                txt = _clean_overlay_text(it, max_words=6, max_chars=38).upper()
                if txt:
                    items.append({"text": txt})
        o["items"] = items
    elif t == "icon":
        o["label"] = _clean_overlay_text(o.get("label") or "", max_words=4, max_chars=30).upper()
    elif t == "qmark":
        o["label"] = _clean_overlay_text(o.get("label") or "", max_words=6, max_chars=42).upper()
        if o["label"] and not o["label"].endswith("?"):
            o["label"] += "?"
    elif t == "solid":
        lines = []
        for l in (o.get("lines") or [])[:3]:
            cl = _clean_overlay_text(l, max_words=5, max_chars=30).upper()
            if cl:
                lines.append(cl)
        o["lines"] = lines
    elif t == "cover_story":
        o["kicker"] = _clean_overlay_text(o.get("kicker") or o.get("badge") or "LAPORAN KHUSUS", max_words=4, max_chars=28).upper()
        lines = []
        for l in (o.get("lines") or [])[:3]:
            cl = _clean_overlay_text(l, max_words=6, max_chars=36).upper()
            if cl:
                lines.append(cl)
        if not lines and o.get("title"):
            cl = _clean_overlay_text(o["title"], max_words=6, max_chars=36).upper()
            if cl:
                lines = [cl]
        o["lines"] = lines
        if o.get("sub"):
            o["sub"] = _clean_overlay_text(o["sub"], max_words=5, max_chars=32).upper()
    elif t == "headline_wall":
        headlines = []
        for hl in (o.get("headlines") or o.get("lines") or [])[:3]:
            cl = _clean_overlay_text(hl, max_words=8, max_chars=56).upper()
            if cl:
                headlines.append(cl)
        if not headlines and o.get("text"):
            cl = _clean_overlay_text(o["text"], max_words=8, max_chars=56).upper()
            if cl:
                headlines = [cl]
        o["headlines"] = headlines
        o.pop("lines", None)
    elif t == "chart_grow":
        o["title"] = _clean_overlay_text(o.get("title") or o.get("label") or "TREN DATA", max_words=5, max_chars=32).upper()
        o["value"] = _clean_overlay_text(o.get("value") or "", max_words=3, max_chars=20).upper()
        # data points: list of numbers, keep as-is
        if not isinstance(o.get("data"), list):
            o["data"] = []
    elif t == "frame_break":
        o["label"] = _clean_overlay_text(o.get("label") or o.get("text") or "", max_words=5, max_chars=32).upper()
    return o


def lock_overlays(words: list, overlays: list, hook_lines: list, cta_text: str, dur: float, transcript_text: str = "") -> list:
    default_dur = {"lower": 2.8 if IS_LONG else 2.4, "icon": 3.2, "qmark": 2.4, "big": 2.8, "solid": 3.2, "stat": 3.6, "quote": 4.2, "list": 4.2, "produk": 4.0,
                   "cover_story": 2.8, "headline_wall": 2.6, "chart_grow": 3.2, "frame_break": 1.5}
    transcript_nums = _transcript_number_tokens(transcript_text or " ".join(w.get("w", "") for w in words))
    ovs = []
    extra = [{"type": "big", "at": 0.15, "lines": hook_lines, "dur": 2.4 if IS_LONG else 2.0, "sfx": "hit", "_fixed": True}] if hook_lines else []
    if cta_text:
        extra.append({"type": "big", "at": max(0.0, dur - 2.8), "lines": [cta_text], "dur": 2.6, "sfx": "hit", "_fixed": True})

    for o in extra + overlays:
        o = _normalize_overlay_type(json.loads(json.dumps(o)))
        if _overlay_has_hallucinated_stat(o, transcript_nums):
            print(f"[overlay] dilewati (stat/angka tidak ada di transkrip): {o.get('value','')!r}")
            continue
        if o.get("type") in ("list", "quote"):
            items = o.get("items", [])
            if not items:
                continue
            ok = True
            parent_idx = find_trigger(words, o.get("trigger", ""))
            for idx_it, it in enumerate(items):
                trig = it.get("trigger") or o.get("trigger", "")
                i = find_trigger(words, trig)
                if i is not None:
                    it["abs"] = words[i]["t0"] + (idx_it * 0.65 if "trigger" not in it else 0.0)
                elif parent_idx is not None:
                    it["abs"] = words[parent_idx]["t0"] + (idx_it * 0.65)
                else:
                    ok = False
                    break
            if not ok:
                continue
            o["t0"] = max(0.0, items[0]["abs"] - 0.15)
        elif "at" in o:
            o["t0"] = o["at"] if o["at"] >= 0 else max(dur + o["at"], 0)
        else:
            i = find_trigger(words, o.get("trigger", ""))
            if i is None:
                continue
            o["t0"] = words[i]["t0"]
        if o["t0"] >= dur - 0.3:
            continue
        ovs.append(o)

    prio = {"stat": 0, "list": 0, "quote": 0, "lower": 1, "big": 2, "qmark": 3, "icon": 3, "solid": 3}

    def _span(o):
        return o["t0"], o["t0"] + o.get("dur", default_dur.get(o["type"], 2.8))

    def _clash(a, b):
        (a0, a1), (b0, b1) = _span(a), _span(b)
        return a0 < b1 + OVERLAY_MIN_GAP and b0 < a1 + OVERLAY_MIN_GAP

    kept = [o for o in ovs if o.get("_fixed")]
    for o in sorted((o for o in ovs if not o.get("_fixed")), key=lambda o: (prio.get(o["type"], 2), o["t0"])):
        if len(kept) >= OVERLAY_MAX:
            print(f"[overlay] dilewati (melebihi batas {OVERLAY_MAX}): {o['type']} @ {o['t0']:.1f}s")
            continue
        if any(_clash(o, k) for k in kept):
            print(f"[overlay] dilewati (terlalu rapat, jeda < {OVERLAY_MIN_GAP:.0f}s): {o['type']} @ {o['t0']:.1f}s")
            continue
        kept.append(o)
    ovs = kept

    ovs.sort(key=lambda o: o["t0"])
    for k, o in enumerate(ovs):
        o.pop("_fixed", None)
        nxt = ovs[k + 1]["t0"] - 0.1 if k + 1 < len(ovs) else dur
        o["t1"] = max(min(o["t0"] + o.get("dur", default_dur.get(o["type"], 2.8)), nxt, dur), o["t0"] + 0.3)
        for it in o.get("items", []):
            it["_t"] = max(0.0, it["abs"] - o["t0"])
    return ovs


# ----------------------------------------------------------------------------
# Auto Director (Claude)
# ----------------------------------------------------------------------------
def _extract_claude_text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def _parse_json_loose(txt: str) -> dict:
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
    a, b = txt.find("{"), txt.rfind("}")
    assert a >= 0 and b > a, f"Balasan tidak berisi JSON: {txt[:200]}"
    return json.loads(txt[a:b + 1])


def _sanitize_plan(p: dict, transcript_text: str = "") -> dict:
    if not isinstance(p, dict):
        raise ValueError("Rencana bukan objek JSON")
    transcript_nums = _transcript_number_tokens(transcript_text)
    ovs = []
    for o in p.get("overlays") or []:
        if not isinstance(o, dict) or o.get("type") not in ALLOWED_OVERLAY_TYPES:
            continue
        o = _normalize_overlay_type(dict(o))
        if _overlay_has_hallucinated_stat(o, transcript_nums):
            print(f"[plan] stat dibuang (angka tidak ada di transkrip): {o.get('value','')!r}")
            continue
        if o.get("type") in ("list", "quote"):
            if o.get("items"):
                ovs.append(o)
            continue
        if o.get("trigger"):
            if o.get("type") == "big" and not o.get("lines"):
                continue
            if o.get("type") == "lower" and not o.get("text"):
                continue
            if o.get("type") == "stat" and not o.get("value"):
                continue
            if o.get("type") in ("icon", "qmark") and not o.get("label"):
                continue
            if o.get("type") == "cover_story" and not o.get("lines"):
                continue
            if o.get("type") == "headline_wall" and not o.get("headlines"):
                continue
            ovs.append(o)
        elif o.get("type") in ("cover_story", "headline_wall", "chart_grow", "frame_break") and "at" in o:
            # editorial types may use absolute 'at' positioning
            ovs.append(o)
    if not ovs:
        raise ValueError("Tidak ada overlay valid dalam rencana")
    return {
        "theme": p.get("theme") if p.get("theme") in THEMES else "hutan",
        "topic": str(p.get("topic") or "video"),
        "hook_lines": [str(x).strip().upper() for x in (p.get("hook_lines") or []) if str(x).strip()][:3],
        "keywords": [str(x).strip().upper() for x in (p.get("keywords") or []) if str(x).strip()][:8],
        "overlays": ovs,
    }


def run_auto_director(client: anthropic.Anthropic, text: str, style_note: str, project_note: str, model: str = "claude-sonnet-4-6") -> dict:
    note_combined = style_note
    if project_note.strip():
        note_combined += f"\nCATATAN KHUSUS PROYEK INI: {project_note.strip()}"

    target_ov = "15 sampai 25 item" if IS_LONG else "6 sampai 8 item"
    fmt_label = "YouTube dokumenter layar lebar 16:9" if IS_LONG else "YouTube Shorts dokumenter Indonesia"

    dir_prompt = f"""Kamu adalah sutradara motion graphic {fmt_label}.
Baca transkrip narasi VO berikut dan susun rencana visual overlay yang tajam, padat, bervariasi, dan sinkron dengan kalimat narasi.

TRANSKRIP:
{text}

CATATAN GAYA & PROYEK:
{note_combined}

ATURAN WAJIB:
1. Pilih "theme" paling cocok dari: "api", "hutan", "bumi", "laut", "malam".
2. Buat {target_ov} di array "overlays" (tiap overlay berjarak minimal ~10 detik dari yang lain). Pilih momen penekanan paling strategis. Setiap item HARUS punya "trigger" berupa 2-4 kata PERSIS yang muncul berurutan di transkrip (huruf kecil, tanpa tanda baca) agar terkunci ke timing Whisper.
3. Variasikan "type" overlay (utamakan "big", "lower", "stat", dan "list"; JANGAN gunakan tipe "map"):
  - "big": {{"type":"big", "trigger":"...", "lines":["BARIS 1", "BARIS 2"]}} (maks 2-3 baris pendek, kapitals)
  - "lower": {{"type":"lower", "trigger":"...", "badge":"CATATAN BUDAYA", "text":"TEKS SINGKAT"}}
  - "stat": {{"type":"stat", "trigger":"...", "value":"ANGKA/KUTIPAN SINGKAT", "label":"KETERANGAN SINGKAT"}} (HANYA jika angka/persentase tertulis eksplisit di transkrip; jika tidak ada angka, jangan pakai stat)
  - "list": {{"type":"list", "trigger":"...", "title":"JUDUL DAFTAR", "items":[{{"text":"POIN 1"}}, {{"text":"POIN 2"}}, {{"text":"POIN 3"}}]}}
  - "icon": {{"type":"icon", "trigger":"...", "icon":"api|bahaya|fakta|berita|waktu|silang|centang", "label":"LABEL SINGKAT"}}
  - "qmark": {{"type":"qmark", "trigger":"...", "label":"PERTANYAAN SINGKAT?"}}
4. Semua teks di dalam overlay HARUS singkat (maksimal 3-6 kata per baris/item) dan menggunakan huruf KAPITAL. Jangan pernah memotong kalimat di tengah; gunakan frasa utuh yang ringkas.
5. Keluarkan HANYA JSON valid tanpa markdown ```json atau penjelasan lain dengan struktur:
{{"theme":"...","topic":"...","hook_lines":["...","..."],"keywords":["KATA1","KATA2","KATA3","KATA4","KATA5"],"overlays":[...]}}"""

    last_err = None
    for attempt in range(1, 4):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": dir_prompt}],
            )
            return _sanitize_plan(_parse_json_loose(_extract_claude_text(resp)), transcript_text=text)
        except Exception as e:
            last_err = e
            print(f"(!) Auto Director percobaan {attempt}/3 gagal: {str(e)[:180]}")
    raise RuntimeError(f"Auto Director gagal setelah 3 percobaan: {last_err}")


# ----------------------------------------------------------------------------
# Editorial Motion Graphic Planner
# ----------------------------------------------------------------------------
_EDITORIAL_CYCLE = ["cover_story", "headline_wall", "frame_break", "headline_wall", "chart_grow", "frame_break", "headline_wall"]

def plan_editorial_moments(words: list, units: list, unit_texts: list, dur: float,
                           transcript_text: str = "", target_count: int = 6) -> list:
    """Pilih 5-7 momen editorial (cover_story / headline_wall / chart_grow / frame_break)
    setiap 4-6 detik, sinkron dengan batas kalimat narasi."""
    transcript_nums = _transcript_number_tokens(transcript_text)
    # Rekonstruksi unit_texts jika kosong
    if not unit_texts and units and words:
        unit_texts = [" ".join(words[a:b][k]["w"] for k in range(b - a)) for a, b in units]

    # Kumpulkan batas awal tiap unit (kalimat) sebagai kandidat timing
    unit_starts = []
    for a, b in units:
        if b > a:
            t = words[a]["t0"]
            if t < dur - 1.5:
                unit_starts.append((t, a, b))

    if not unit_starts:
        return []

    MIN_GAP = 4.0
    selected: list = []
    last_t = -MIN_GAP

    for t, a, b in unit_starts:
        if t - last_t < MIN_GAP:
            continue
        if len(selected) >= target_count:
            break
        selected.append((t, a, b))
        last_t = t

    # Pastikan minimal ada cover_story di awal (t ~ 0)
    if selected and selected[0][0] > 2.0 and unit_starts:
        # coba sisipkan unit pertama
        t0, a0, b0 = unit_starts[0]
        if not selected or selected[0][0] != t0:
            selected.insert(0, (t0, a0, b0))
        selected = selected[:target_count]

    moments = []
    cycle_idx = 0
    for idx, (t, a, b) in enumerate(selected):
        # Tentukan tipe berdasarkan siklus
        etype = _EDITORIAL_CYCLE[cycle_idx % len(_EDITORIAL_CYCLE)]
        cycle_idx += 1

        # chart_grow hanya boleh jika ada angka di transkrip
        if etype == "chart_grow" and not transcript_nums:
            etype = "headline_wall"

        unit_words = words[a:b]
        snippet = " ".join(w["w"] for w in unit_words[:8])
        snippet_words = snippet.split()

        if etype == "cover_story":
            # Pakai 2-5 kata pertama unit sebagai judul, sisanya sebagai sub
            title_words = snippet_words[:5]
            sub_words = snippet_words[5:9] if len(snippet_words) > 5 else []
            lines = [" ".join(title_words).upper()]
            if sub_words:
                lines.append(" ".join(sub_words).upper())
            m = {
                "type": "cover_story",
                "at": t,
                "kicker": "LAPORAN KHUSUS",
                "lines": lines,
                "dur": 2.8,
            }
        elif etype == "headline_wall":
            # Ambil sampai 3 unit teks terdekat sebagai headline strips
            u_idx = next((i for i, (u_a, u_b) in enumerate(units) if u_a == a), 0)
            hls = []
            for ui in range(u_idx, min(u_idx + 3, len(units))):
                ua, ub = units[ui]
                txt = " ".join(words[ua:ub][k]["w"] for k in range(min(8, ub - ua)))
                cl = _clean_overlay_text(txt, max_words=7, max_chars=52).upper()
                if cl:
                    hls.append(cl)
            if not hls:
                hls = [snippet[:52].upper()]
            m = {
                "type": "headline_wall",
                "at": t,
                "headlines": hls[:3],
                "dur": 2.6,
            }
        elif etype == "chart_grow":
            # Ambil angka pertama dari transkrip untuk value label
            nums_list = sorted(transcript_nums)
            val = nums_list[0] if nums_list else ""
            m = {
                "type": "chart_grow",
                "at": t,
                "title": _clean_overlay_text(snippet, max_words=4, max_chars=28).upper(),
                "value": val,
                "data": [],  # default upward trend
                "dur": 3.2,
            }
        else:  # frame_break
            lbl = _clean_overlay_text(snippet, max_words=4, max_chars=28).upper()
            m = {
                "type": "frame_break",
                "at": t,
                "label": lbl,
                "dur": 1.5,
            }

        moments.append(m)

    return moments


# ----------------------------------------------------------------------------
# Synthesized Documentary SFX (pure Python wave/math/struct)
# ----------------------------------------------------------------------------
import wave
import struct
import math as _math
import random as _random


def _write_wav(path: str, samples: list, sr: int = 44100, channels: int = 1):
    """Tulis list float [-1,1] ke file WAV 16-bit."""
    n = len(samples)
    with wave.open(path, "w") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for s in samples:
            v = max(-32767, min(32767, int(s * 32767)))
            wf.writeframes(struct.pack("<h", v))


def _synth_sub_pulse(path: str, sr: int = 44100):
    """sub_pulse.wav: ~0.35s sapuan bass hangat 70Hz->42Hz (untuk cover_story & frame_break)."""
    dur = 0.35
    n = int(dur * sr)
    samples = []
    for i in range(n):
        t = i / sr
        env = _math.sin(_math.pi * t / dur) ** 0.5  # half-sine envelope
        freq = 70.0 - (70.0 - 42.0) * (t / dur)
        # accumulate phase manually for smooth sweep
        # use simple sin with instantaneous freq
        phase = 2 * _math.pi * (70.0 * t - (70.0 - 42.0) * t * t / (2 * dur))
        sig = _math.sin(phase) * env
        # add slight 2nd harmonic warmth
        sig += 0.18 * _math.sin(2 * phase) * env
        # low-pass feel: gentle clipping
        sig = _math.tanh(sig * 1.2) / 1.2
        samples.append(sig * 0.72)
    # stereo
    stereo = []
    for s in samples:
        stereo.append(s)
        stereo.append(s)
    _write_wav(path, stereo, sr, channels=2)


def _synth_marker_sweep(path: str, sr: int = 44100):
    """marker_sweep.wav: ~0.25s sapuan tekstur halus (untuk headline_wall)."""
    dur = 0.25
    n = int(dur * sr)
    rng = _random.Random(42)
    samples = []
    for i in range(n):
        t = i / sr
        env = t / dur * _math.exp(-t / (dur * 0.4))
        env = max(0.0, env)
        # noise sweep: white noise filtered by rising freq window
        noise = rng.uniform(-1, 1)
        freq_center = 800 + 2400 * (t / dur)
        # simple resonant feel: sin at center freq modulated by noise
        tone = _math.sin(2 * _math.pi * freq_center * t) * 0.4
        sig = (noise * 0.6 + tone) * env
        samples.append(sig * 0.55)
    stereo = []
    for s in samples:
        stereo.append(s)
        stereo.append(s)
    _write_wav(path, stereo, sr, channels=2)


def _synth_editorial_tick(path: str, sr: int = 44100):
    """editorial_tick.wav: ~0.08s ketukan taktil redam (untuk chart_grow)."""
    dur = 0.08
    n = int(dur * sr)
    samples = []
    for i in range(n):
        t = i / sr
        env = _math.exp(-t / 0.018)
        # click: mix of a sharp transient + short pitched tone
        click = (_random.Random(i).uniform(-1, 1) if i < int(0.004 * sr) else 0.0) * 0.6
        tone = _math.sin(2 * _math.pi * 1200 * t) * 0.5
        sig = (click + tone) * env
        samples.append(sig * 0.65)
    stereo = []
    for s in samples:
        stereo.append(s)
        stereo.append(s)
    _write_wav(path, stereo, sr, channels=2)


def generate_editorial_sfx(work_dir: str) -> dict:
    """Generate semua SFX ke /tmp dan return dict path per tipe."""
    sfx_map = {}
    for name, fn in [
        ("sub_pulse", _synth_sub_pulse),
        ("marker_sweep", _synth_marker_sweep),
        ("editorial_tick", _synth_editorial_tick),
    ]:
        p = os.path.join(work_dir, f"{name}.wav")
        fn(p)
        sfx_map[name] = p
        print(f"SFX synthesized: {name}.wav -> {p}")
    return sfx_map


# Mapping tipe overlay ke SFX
EDITORIAL_SFX_MAP = {
    "cover_story": "sub_pulse",
    "frame_break": "sub_pulse",
    "headline_wall": "marker_sweep",
    "chart_grow": "editorial_tick",
}


# ----------------------------------------------------------------------------
# B-roll
# ----------------------------------------------------------------------------
def scan_local_clips(proj_dir: str, work_dir: str, extract_frames: bool = True) -> list:
    frames_root = os.path.join(work_dir, "frames")
    os.makedirs(frames_root, exist_ok=True)
    cands = [
        p for p in sorted(glob.glob(f"{proj_dir}/*"))
        if p.lower().endswith(VID_EXT + IMG_EXT)
        and not os.path.basename(p).lower().startswith(SKIP_PREFIXES)
    ]
    clips = []
    for p in cands:
        name = os.path.basename(p)
        try:
            info = probe(p)
        except Exception as e:
            print(f"(!) Lewati {name} (tidak terbaca): {str(e)[:120]}")
            continue
        if not info.get("w"):
            print(f"(!) Lewati {name} (tanpa stream video)")
            continue
        is_img = p.lower().endswith(IMG_EXT)
        if is_img:
            info["dur"] = STILL_DUR
        key = re.sub(r"\W", "_", name)
        info.update(path=p, name=name, key=key, is_img=is_img)
        d = os.path.join(frames_root, key)
        os.makedirs(d, exist_ok=True)
        if extract_frames and not glob.glob(f"{d}/*.jpg"):
            if is_img:
                sh(["ffmpeg", "-y", "-v", "error", "-i", p, "-vf", "scale=480:-2", "-q:v", "4", f"{d}/0001.jpg"])
            else:
                total = info.get("dur") or 0.0
                for n_i, frac in enumerate((0.1, 0.37, 0.63, 0.9), 1):
                    sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{total * frac:.2f}", "-i", p, "-frames:v", "1",
                        "-vf", "scale=480:-2", "-q:v", "4", f"{d}/{n_i:04d}.jpg"], check=False)
        info["frames"] = sorted(glob.glob(f"{d}/*.jpg"))
        clips.append(info)
    return clips


def run_auto_broll(client: anthropic.Anthropic, clips: list, unit_texts: list, words: list, proj_dir: str, model: str = "claude-sonnet-4-6") -> dict:
    label_tool = {
        "name": "label_klip",
        "description": "Deskripsi visual klip/gambar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "deskripsi": {"type": "string"},
                "tag": {"type": "array", "items": {"type": "string"}},
                "ada_orang": {"type": "boolean"},
                "wajah_jelas": {"type": "boolean"},
            },
            "required": ["deskripsi", "tag", "ada_orang", "wajah_jelas"],
        },
    }
    match_tool = {
        "name": "cocokkan_klip",
        "description": "Pasangkan frasa narasi dengan klip B-roll.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pilihan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "frasa": {"type": "string"},
                            "id_klip": {"type": "string"},
                        },
                        "required": ["frasa", "id_klip"],
                    },
                }
            },
            "required": ["pilihan"],
        },
    }

    cache_p = os.path.join(proj_dir, "clip_labels.json")
    try:
        cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    except Exception:
        cache = {}

    dirty = False
    for c in clips:
        sz = os.path.getsize(c["path"])
        if c["name"] in cache and cache[c["name"]].get("size") == sz:
            print("Cache label:", c["name"], "->", cache[c["name"]].get("deskripsi", "")[:60])
            continue
        try:
            fr = c["frames"]
            if not fr:
                continue
            idxs = sorted({int(i * (len(fr) - 1) / 3) for i in range(4)}) if len(fr) > 1 else [0]
            content = []
            for i in idxs:
                with open(fr[i], "rb") as fh:
                    content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(fh.read()).decode()},
                    })
            content.append({"type": "text", "text": f"Nama file: {c['name']}. Panggil tool label_klip untuk mendeskripsikan isi visualnya secara ringkas."})
            r = client.messages.create(
                model=model,
                max_tokens=600,
                tools=[label_tool],
                tool_choice={"type": "tool", "name": "label_klip"},
                messages=[{"role": "user", "content": content}],
            )
            tu = next((b for b in r.content if getattr(b, "type", "") == "tool_use"), None)
            if tu is not None:
                out = tu.input
                cache[c["name"]] = {"size": sz, **out}
                dirty = True
                print("Label baru:", c["name"], "->", out.get("deskripsi", "")[:60])
        except Exception as e:
            print(f"(!) Gagal label {c['name']}: {str(e)[:180]}")
    if dirty:
        with open(cache_p, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)

    ids = {f"K{i + 1:02d}": c for i, c in enumerate(clips)}
    klip_list = [
        {"id": k, "deskripsi": cache.get(c["name"], {}).get("deskripsi", c["name"])}
        for k, c in ids.items()
    ]
    kal_list = [{"teks": u} for u in unit_texts]

    hints = {}
    try:
        r2 = client.messages.create(
            model=model,
            max_tokens=3000,
            tools=[match_tool],
            tool_choice={"type": "tool", "name": "cocokkan_klip"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Panggil tool cocokkan_klip untuk memasangkan frasa persis (2-4 kata) dari kalimat narasi ke id_klip yang paling relevan:\n"
                        + json.dumps({"kalimat": kal_list, "klip": klip_list}, ensure_ascii=False)
                    ),
                }
            ],
        )
        tu2 = next((b for b in r2.content if getattr(b, "type", "") == "tool_use"), None)
        if tu2 is not None:
            for p in tu2.input.get("pilihan", []):
                c = ids.get(p.get("id_klip"))
                if c and find_trigger(words, p.get("frasa", "")) is not None:
                    hints[p["frasa"]] = c["name"]
    except Exception as e:
        print(f"(!) Pencocokan B-roll dilewati: {str(e)[:180]}")

    return hints


# ----------------------------------------------------------------------------
# Perintah edit
# ----------------------------------------------------------------------------
def apply_edit_command(state: dict, raw_cmd: str) -> dict:
    """Format: '/perintah argumen' atau '/perintah | argumen'."""
    m = re.match(r"\s*(/\w+)\s*(?:\|\s*)?(.*)$", raw_cmd or "", re.S)
    if not m:
        return state
    cmd, arg = m.group(1).lower(), m.group(2).strip()

    if cmd == "/hapus":
        nums = set()
        for tok in re.split(r"[,\s]+", arg):
            if "-" in tok:
                a, b = tok.split("-", 1)
                if a.isdigit() and b.isdigit():
                    nums.update(range(int(a), int(b) + 1))
            elif tok.isdigit():
                nums.add(int(tok))
        state["overlays"] = [o for i, o in enumerate(state.get("overlays", []), 1) if i not in nums]

    elif cmd == "/tema":
        t = arg.lower()
        if t in THEMES:
            state["theme"] = t

    elif cmd == "/hook":
        state["hook_lines"] = [] if arg == "-" else [h.strip().upper() for h in arg.split("|") if h.strip()]

    elif cmd == "/cta":
        state["cta_text"] = "" if arg == "-" else arg.upper()

    elif cmd == "/klip":
        items = list(state.get("clip_hints", {}).items())
        usable = state.get("usable_names", [])
        for chunk in arg.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                print(f"(!) /klip: '{chunk}' dilewati, format harus 'frasa=klip' atau 'nomor=klip'.")
                continue
            k, v = [x.strip() for x in chunk.split("=", 1)]
            if not k or not v:
                print(f"(!) /klip: '{chunk}' dilewati, bagian kiri atau kanan '=' kosong.")
                continue
            matched_name = next((nm for nm in usable if v.lower() in nm.lower()), None)
            if not matched_name:
                print(f"(!) /klip: nama klip '{v}' tidak cocok dengan klip yang tersedia ({len(usable)} klip).")
                continue
            if k.isdigit():
                idx = int(k)
                if not items:
                    print(f"(!) /klip: indeks {idx} tidak bisa dipakai karena clip_hints masih kosong; gunakan format 'frasa=klip'.")
                    continue
                if not 1 <= idx <= len(items):
                    print(f"(!) /klip: indeks {idx} di luar rentang 1-{len(items)}; perintah dilewati.")
                    continue
                items[idx - 1] = (items[idx - 1][0], matched_name)
            else:
                items.append((k, matched_name))
        state["clip_hints"] = dict(items)

    elif cmd == "/buang_klip":
        bad = {x.strip().lower() for x in arg.split(",") if x.strip()}
        state["excluded_clips"] = list(set(state.get("excluded_clips", [])) | bad)
        state["clip_hints"] = {
            f: nm for f, nm in state.get("clip_hints", {}).items()
            if not any(b in nm.lower() for b in bad)
        }

    return state


# ----------------------------------------------------------------------------
# Render final
# ----------------------------------------------------------------------------
def render_final_video(state: dict, proj_dir: str, work_dir: str, remo_dir: str = "/tmp/remotion_overlay") -> str:
    os.makedirs(work_dir, exist_ok=True)
    vo_path = os.path.join(work_dir, "VO_padat.wav")
    if not os.path.exists(vo_path):
        audio_cands = sorted(glob.glob(f"{proj_dir}/*.mp3") + glob.glob(f"{proj_dir}/*.m4a") + glob.glob(f"{proj_dir}/*.wav"))
        assert audio_cands, f"File audio VO tidak ditemukan di {proj_dir}"
        vo_path = tighten_vo(audio_cands[0], work_dir)

    vo_info = probe(vo_path)
    dur = vo_info["dur"]
    assert dur > 1.0, "Durasi VO terlalu pendek."
    n_frames = round(dur * FPS)

    words = state["words"]
    units = state["units"]
    keywords = state.get("keywords", [])
    theme_name = state.get("theme", "hutan") if state.get("theme") in THEMES else "hutan"
    topic = state.get("topic", "video")
    hook_lines = state.get("hook_lines", [])
    cta_text = state.get("cta_text", "")
    overlays_raw = state.get("overlays", [])
    clip_hints = state.get("clip_hints", {})
    excluded = {x.lower() for x in state.get("excluded_clips", [])}

    caps = build_captions(words, units, keywords, dur)
    transcript_text = state.get("text") or " ".join(w.get("w", "") for w in words)

    # Tambahkan editorial moments otomatis (5-7 momen per ~40s)
    target_editorial = max(5, min(7, int(dur / 6)))
    editorial_moments = plan_editorial_moments(words, units, state.get("unit_texts", []), dur,
                                               transcript_text=transcript_text, target_count=target_editorial)
    combined_overlays = list(overlays_raw) + editorial_moments
    print(f"[editorial] {len(editorial_moments)} momen editorial ditambahkan. Total overlay: {len(combined_overlays)}")

    ovs = lock_overlays(words, combined_overlays, hook_lines, cta_text, dur, transcript_text=transcript_text)
    sfx_ev = sorted({round(o["t0"], 2) for o in ovs if o.get("sfx")})

    clips = [
        c for c in scan_local_clips(proj_dir, work_dir, extract_frames=False)
        if not any(ex in c["name"].lower() for ex in excluded)
    ]
    assert clips, "Tidak ada klip B-roll tersisa untuk dirender!"
    clips = prepare_still_clips(clips, work_dir)

    grade = "eq=contrast=1.04:saturation=1.05:gamma_r=1.03:gamma_b=0.96,colorbalance=rm=0.03:bm=-0.03"
    # Hard cut di batas kalimat/jeda Whisper. Tiap klip 3.5-5.5s saat narasi berlanjut.
    unit_bounds = sorted({
        max(0.0, min(dur, float(words[a]["t0"])))
        for a, b in units if b > a
    } | {0.0, float(dur)})
    cut_ts = [0.0]
    while cut_ts[-1] < dur - 0.01:
        cur = cut_ts[-1]
        rem = dur - cur
        if rem <= 5.8:
            cut_ts.append(dur)
            break
        window_end = cur + 5.5
        cands = [t for t in unit_bounds if cur + 3.5 <= t <= window_end]
        if cands:
            nxt = max(cands)
        else:
            after = [t for t in unit_bounds if t > window_end]
            nxt = min(after[0], cur + 5.5) if after else min(cur + 4.5, dur)
        if nxt - cur < 1.5:
            nxt = min(cur + 3.5, dur)
        if dur - nxt < 3.5 and nxt < dur:
            nxt = dur
        cut_ts.append(min(nxt, dur))
    bounds = [round(t * FPS) for t in cut_ts]
    bounds[-1] = n_frames
    # dedup bounds
    dedup = [bounds[0]]
    for bnd in bounds[1:]:
        if bnd > dedup[-1]:
            dedup.append(bnd)
    if dedup[-1] != n_frames:
        dedup.append(n_frames)
    bounds = dedup
    n_sh = len(bounds) - 1

    used = {c["key"]: [] for c in clips}
    ptr = {c["key"]: 0.0 for c in clips}
    hint_t = []
    for kw, clipname in clip_hints.items():
        i = find_trigger(words, kw)
        if i is not None:
            hint_t.append((words[i]["t0"], clipname))

    def overlap_ok(c, ss, d_seg, relax):
        return all(min(ss + d_seg, b) - max(ss, a) <= relax * d_seg for a, b in used[c["key"]])

    def pick_shot(d_seg, t_start, t_end, rr):
        order = clips[rr % len(clips):] + clips[:rr % len(clips)]
        for t, nm in hint_t:
            if t_start <= t <= t_end:
                order = [c for c in clips if nm.lower() in c["name"].lower()] + order
        for relax in (0.0, 0.5, 0.8, 1.0):
            for c in order:
                if c["dur"] < d_seg + 0.4:
                    continue
                grid = list(np.arange(0, c["dur"] - d_seg - 0.3, 0.5))
                if not grid:
                    continue
                k0 = next((k for k, s in enumerate(grid) if s >= ptr[c["key"]]), 0)
                for ss in grid[k0:] + grid[:k0]:
                    if overlap_ok(c, ss, d_seg, relax):
                        return c, float(ss), False
        longest = max(clips, key=lambda c: c["dur"])
        return longest, 0.0, True

    shots = []
    for i in range(n_sh):
        fr = bounds[i + 1] - bounds[i]
        d_seg = fr / FPS
        c, ss, loop = pick_shot(d_seg, bounds[i] / FPS, bounds[i + 1] / FPS, i)
        used[c["key"]].append((ss, ss + d_seg))
        ptr[c["key"]] = ss + d_seg
        shots.append({"i": i, "clip": c, "ss": ss, "dur": d_seg, "frames": fr, "t": bounds[i] / FPS, "loop": loop})

    shots_dir = os.path.join(work_dir, "shots")
    os.makedirs(shots_dir, exist_ok=True)

    phone_png = os.path.join(work_dir, "phone.png")
    if not IS_LONG and not os.path.exists(phone_png):
        sd = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sd).rounded_rectangle([80, 427, 1000, 962], radius=56, fill=(0, 0, 0, 130))
        l_ph = sd.filter(ImageFilter.GaussianBlur(18))
        d_ph = ImageDraw.Draw(l_ph)
        d_ph.rounded_rectangle([80, 413, 1000, 948], radius=56, fill=(17, 17, 17, 255), outline=(64, 64, 64, 255), width=3)
        hole = Image.new("L", (W, H), 255)
        ImageDraw.Draw(hole).rounded_rectangle([100, 433, 980, 928], radius=26, fill=0)
        l_ph.putalpha(Image.fromarray(np.minimum(np.array(l_ph.getchannel("A")), np.array(hole)).astype(np.uint8)))
        l_ph.save(phone_png)

    scale_up_w = int(W * 1.5)
    scale_up_h = int(H * 1.5)

    def render_shot(s):
        c, fr = s["clip"], s["frames"]
        out_p = os.path.join(shots_dir, f"s{s['i']:03d}.mp4")
        pre = ["-stream_loop", "-1"] if s["loop"] else []
        if c["is_img"]:
            pre = ["-loop", "1"]
        base = ["ffmpeg", "-y", "-v", "error"] + pre + ["-ss", f"{s['ss']:.3f}", "-t", f"{s['dur'] + 0.4:.3f}", "-i", c["path"], "-an"]
        out = ["-frames:v", str(fr), "-r", "30", "-c:v", "libx264", "-crf", "16", "-preset", "ultrafast", "-pix_fmt", "yuv420p", out_p]
        zp = shot_zoom_filter(fr)
        if IS_LONG:
            if c.get("vertical", False):
                fc = (
                    f"[0:v]fps=30,{grade},split=2[a][b];"
                    f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=35:6,eq=brightness=-0.12[bg];"
                    f"[b]scale=-2:{H}:flags=lanczos[fg];"
                    f"[bg][fg]overlay=x='(W-w)/2':y='(H-h)/2',scale={scale_up_w}:{scale_up_h},{zp},format=yuv420p[v]"
                )
                sh(base + ["-filter_complex", fc, "-map", "[v]"] + out)
            else:
                vf = (
                    f"fps=30,{grade},scale={scale_up_w}:{scale_up_h}:force_original_aspect_ratio=increase,crop={scale_up_w}:{scale_up_h},"
                    f"{zp},format=yuv420p"
                )
                sh(base + ["-vf", vf] + out)
        else:
            if c.get("vertical", True):
                vf = (
                    f"fps=30,{grade},scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
                    f"{zp},format=yuv420p"
                )
                sh(base + ["-vf", vf] + out)
            else:
                inner_zp = f"zoompan=z='1+0.03*on/{max(fr - 1, 1)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=880x495:fps=30"
                fc = (
                    f"[0:v]fps=30,{grade},split=2[a][b];"
                    f"[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:6,eq=brightness=-0.12[bg];"
                    f"[b]scale=880:495:force_original_aspect_ratio=increase,crop=880:495,{inner_zp}[fg];"
                    f"[bg][fg]overlay=100:433[t];[t][1:v]overlay=0:0:format=auto,format=yuv420p[v0];"
                    f"[v0]scale=1620:2880,{zp},format=yuv420p[v]"
                )
                inp = base[:-1] + ["-loop", "1", "-i", phone_png, "-an"]
                sh(inp + ["-filter_complex", fc, "-map", "[v]"] + out)
        return out_p

    workers = max(1, min(4, os.cpu_count() or 2))
    print(f"Memotong {n_sh} shot B-roll ({workers} paralel)...")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(render_shot, s) for s in shots]
        try:
            for f in as_completed(futs):
                f.result()
        except Exception:
            for f in futs:
                f.cancel()
            raise

    list_txt = os.path.join(work_dir, "list.txt")
    with open(list_txt, "w") as fh:
        for s in shots:
            shot_p = os.path.join(shots_dir, f"s{s['i']:03d}.mp4")
            fh.write(f"file '{_concat_quote(shot_p)}'\n")
    base_mp4 = os.path.join(work_dir, "base.mp4")
    sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", base_mp4])

    prepare_remotion_project(remo_dir)
    os.makedirs(f"{remo_dir}/out", exist_ok=True)
    os.makedirs(f"{remo_dir}/public", exist_ok=True)

    _hex = lambda t: "#%02X%02X%02X" % tuple(t)
    _t = THEMES[theme_name]
    t_props = {
        "hex": {"green": _hex(_t["g"]), "dark": _hex(_t["d"]), "mid": _hex(_t["m"]), "cream": _hex(_t["c"]), "orange": _hex(_t["a"]), "black": _hex(_t["k"])},
        "vars": {"--g": "%d,%d,%d" % _t["g"], "--d": "%d,%d,%d" % _t["d"], "--a": "%d,%d,%d" % _t["a"], "--c": "%d,%d,%d" % _t["c"], "--v": "%d,%d,%d" % _t["v"]},
    }

    conc = str(max(2, min(4, os.cpu_count() or 2)))

    def render_overlay():
        props = {"overlays": ovs, "fps": FPS, "durationInFrames": n_frames, "images": [], "captions": caps, "theme": t_props}
        with open(f"{remo_dir}/props.json", "w") as fh:
            json.dump(props, fh, sort_keys=True)
        ov_file, codec = f"{remo_dir}/out/overlay.webm", "vp9"
        try:
            sh(["npx", "remotion", "render", "src/index.ts", "Overlay", ov_file, "--props=props.json", "--codec=vp9",
                "--pixel-format=yuva420p", "--image-format=png", "--crf=18", "--overwrite", f"--concurrency={conc}"], cwd=remo_dir)
        except Exception as e:
            print(f"[peringatan] VP9 gagal ({str(e)[-300:]}); beralih ke ProRes 4444...")
            codec = "prores"
            ov_file = f"{remo_dir}/out/overlay.mov"
            sh(["npx", "remotion", "render", "src/index.ts", "Overlay", ov_file, "--props=props.json", "--codec=prores",
                "--prores-profile=4444", "--pixel-format=yuva444p10le", "--image-format=png", "--overwrite", f"--concurrency={conc}"], cwd=remo_dir)
        return ov_file, codec

    overlay_file, used_codec = render_overlay()

    # --- Synthesized Documentary SFX + mix audio ---
    SR = 48000
    # Generate per-tipe SFX files (pure Python, no external downloads)
    sfx_files = generate_editorial_sfx(work_dir)

    # Build timeline events: list of (timestamp, sfx_name) with 1.8s cooldown
    SFX_COOLDOWN = 1.8
    sfx_events: list = []
    last_sfx_t = -SFX_COOLDOWN

    # Collect from overlay types (editorial types) + legacy sfx="hit"
    ov_sorted = sorted(ovs, key=lambda o: o["t0"])
    for o in ov_sorted:
        t_ev = round(o["t0"], 3)
        otype = o.get("type", "")
        sfx_name = EDITORIAL_SFX_MAP.get(otype)
        if sfx_name is None and o.get("sfx"):
            # legacy hit -> sub_pulse fallback
            sfx_name = "sub_pulse"
        if sfx_name and (t_ev - last_sfx_t) >= SFX_COOLDOWN:
            sfx_events.append((t_ev, sfx_name))
            last_sfx_t = t_ev

    print(f"[sfx] {len(sfx_events)} cue SFX (cooldown={SFX_COOLDOWN}s): {[(round(t,2), s) for t, s in sfx_events]}")

    # Load SFX files via scipy for precise mixing
    stem_total = int(dur * SR) + SR
    stem = np.zeros(stem_total, dtype=np.float32)
    SFX_DB = -20.0  # -20 dB di bawah VO
    sfx_gain = 10 ** (SFX_DB / 20.0)

    for t_ev, sfx_name in sfx_events:
        sfx_path = sfx_files.get(sfx_name)
        if not sfx_path or not os.path.exists(sfx_path):
            continue
        try:
            sfx_sr, sfx_data = wavfile.read(sfx_path)
            # sfx_data is int16, convert to float32
            if sfx_data.dtype == np.int16:
                sfx_arr = sfx_data.astype(np.float32) / 32768.0
            else:
                sfx_arr = sfx_data.astype(np.float32)
            # resample if needed
            if sfx_sr != SR:
                ratio = SR / sfx_sr
                n_out = int(len(sfx_arr) * ratio)
                sfx_arr = np.interp(np.linspace(0, len(sfx_arr) - 1, n_out), np.arange(len(sfx_arr)), sfx_arr[:, 0] if sfx_arr.ndim > 1 else sfx_arr)
                sfx_arr = np.stack([sfx_arr, sfx_arr], axis=1) if sfx_data.ndim == 1 else sfx_arr
            # Mix left channel only into mono stem
            mono = sfx_arr[:, 0] if sfx_arr.ndim > 1 else sfx_arr
            mono = mono * sfx_gain
            st = int(t_ev * SR)
            end = min(st + len(mono), stem_total)
            if st < stem_total:
                seg = mono[:end - st]
                stem[st:st + len(seg)] += seg
        except Exception as e:
            print(f"[sfx] gagal mix {sfx_name} @ {t_ev:.2f}s: {e}")

    stem = stem[:int(dur * SR)]
    stem_wav = os.path.join(work_dir, "SFX_stem.wav")
    wavfile.write(stem_wav, SR, np.stack([stem, stem], 1).astype(np.float32))

    mix_wav = os.path.join(work_dir, "mix.wav")
    pan = "pan=stereo|c0=c0|c1=c0" if vo_info.get("ch", 1) == 1 else "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"
    gain = 0.0
    for _ in range(8):
        fc = (
            f"[0:a]aresample=48000,afade=t=in:d=0.01,acompressor=threshold=0.09:ratio=3:attack=4:release=60,"
            f"volume={gain:.2f}dB,alimiter=limit=0.63:attack=2:release=30:level=0,{pan}[v];"
            f"[v][1:a]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.7:level=0[m]"
        )
        sh(["ffmpeg", "-y", "-v", "error", "-i", vo_path, "-i", stem_wav, "-filter_complex", fc, "-map", "[m]", "-ar", "48000", "-ac", "2", "-t", f"{dur:.4f}", mix_wav])
        lufs, _ = measure(mix_wav)
        if not math.isfinite(lufs) or abs(lufs - TARGET_LUFS) < 0.1:
            break
        gain = max(-30.0, min(30.0, gain + (TARGET_LUFS - lufs)))

    # --- Gabung final ---
    slug = re.sub(r"[^a-z0-9]+", "_", str(topic).lower()).strip("_")[:40] or "video"
    prefix = "Long" if IS_LONG else "Short"
    out_name = f"{prefix}_{slug}.mp4"
    final_mp4 = os.path.join(work_dir, out_name)
    fc_final = (
        "[1:v]format=rgba[ov];[0:v][ov]overlay=0:0:format=auto,format=yuv420p,"
        "vignette=angle=PI/4.5" + (f",noise=alls={GRAIN}:allf=t" if GRAIN else "") + "[v]"
    )
    ov_in = ["-c:v", "libvpx-vp9", "-i", overlay_file] if used_codec == "vp9" else ["-i", overlay_file]
    sh(
        ["ffmpeg", "-y", "-v", "error", "-i", base_mp4]
        + ov_in
        + [
            "-i", mix_wav, "-filter_complex", fc_final,
            "-map", "[v]", "-map", "2:a", "-frames:v", str(n_frames), "-shortest",
            "-c:v", "libx264", "-crf", str(FINAL_CRF), "-maxrate", FINAL_MAXRATE, "-bufsize", "20M", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", final_mp4,
        ]
    )
    return final_mp4
