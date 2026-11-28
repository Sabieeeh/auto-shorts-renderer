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
from roto_helper import extract_roto_layer

IS_LONG = os.environ.get("VIDEO_MODE", "").strip().upper() == "LONG"
FPS = 30
W = 1920 if IS_LONG else 1080
H = 1080 if IS_LONG else 1920

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
VID_EXT = (".mov", ".mp4")
STILL_DUR = 5.0
STILL_KB_END = 1.08
DRIFT = 0.025
CUT_FRAMES = 4
GRAIN = 3
FINAL_CRF = 22 if IS_LONG else 23
FINAL_MAXRATE = "10M" if IS_LONG else "9M"
OVERLAY_MAX = 25 if IS_LONG else 8
OVERLAY_MIN_GAP = 6.0 if IS_LONG else 5.0
SKIP_PREFIXES = ("output_", "short_", "long_")
ALLOWED_OVERLAY_TYPES = {
    "roto_typo",
    "kinetic_typo",
    "marker_quote",
    "floating_card",
    "camera_beat",
    "stat_counter",
    "draw_on",
}
TARGET_LUFS = -16.0

THEMES = {
    "hutan": dict(g=(31, 61, 43), d=(22, 46, 32), m=(45, 92, 67), c=(242, 232, 213), a=(224, 138, 46), k=(17, 17, 17), v=(8, 20, 13)),
    "laut": dict(g=(14, 42, 71), d=(9, 29, 51), m=(28, 74, 115), c=(234, 244, 250), a=(255, 183, 3), k=(8, 14, 24), v=(4, 12, 22)),
    "api": dict(g=(70, 18, 22), d=(48, 10, 14), m=(110, 34, 34), c=(250, 238, 220), a=(255, 196, 0), k=(20, 8, 8), v=(24, 4, 6)),
    "malam": dict(g=(18, 18, 24), d=(10, 10, 15), m=(40, 40, 56), c=(245, 245, 245), a=(0, 230, 118), k=(0, 0, 0), v=(0, 0, 0)),
    "bumi": dict(g=(74, 50, 32), d=(50, 32, 20), m=(110, 78, 50), c=(245, 236, 220), a=(232, 163, 23), k=(20, 12, 6), v=(24, 14, 6)),
}

DEFAULT_STYLE_NOTE = (
    "Format video panjang landscape 16:9 dokumenter budaya yang dinamis dan hidup."
    "Susun 15-25 overlay berjarak rapi menggunakan kombinasi roto_typo, kinetic_typo, marker_quote, stat_counter, dan camera_beat."
    if IS_LONG else
    "Gunakan variasi dinamis roto_typo, kinetic_typo, marker_quote, stat_counter, dan camera_beat agar layar selalu hidup dan organik."
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
    to_convert = []
    for c in clips:
        if not c.get("is_img"):
            continue
        out_p = os.path.join(still_dir, f"{c['key']}.mp4")
        cached = os.path.exists(out_p) and os.path.getsize(out_p) > 1000
        if cached:
            c.update(path=out_p, dur=STILL_DUR, is_img=False, vertical=(not IS_LONG), still=True)
        else:
            to_convert.append((c, out_p))

    if to_convert:
        workers = max(1, min(4, os.cpu_count() or 2))
        print(f"Mengonversi {len(to_convert)} gambar menjadi klip ({workers} paralel)...")

        def _conv_item(item):
            c_obj, out_path = item
            ok = still_to_clip(c_obj["path"], out_path)
            return c_obj, out_path, ok

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for c_obj, out_path, ok in ex.map(_conv_item, to_convert):
                if ok:
                    c_obj.update(path=out_path, dur=STILL_DUR, is_img=False, vertical=(not IS_LONG), still=True)
                    print("Gambar -> klip:", c_obj["name"])
                else:
                    c_obj["dur"] = STILL_DUR
    return clips


def cut_kind(i: int):
    """Hard-cut bersih pada 80% potongan; transisi halus hanya tiap 5 shot sekali agar nyaman di mata."""
    if i <= 0 or i % 5 != 0:
        return None
    return "zoom"


def motion_filter(fr: int, drift: float, kin, kout) -> str:
    n = CUT_FRAMES
    terms = []
    if drift:
        terms.append(f"{drift}*on/{max(fr - 1, 1)}")
    xs = []
    if kout:
        pr = f"max(0,(on-({fr}-{n}))/{n})"
        terms.append(f"{0.22 if kout == 'zoom' else 0.26}*pow({pr},2)")
        if kout == "whip":
            xs.append(f"+0.075*iw*pow({pr},2)")
    if kin:
        pr = f"max(0,({n}-on)/{n})"
        terms.append(f"{0.22 if kin == 'zoom' else 0.26}*pow({pr},2)")
        if kin == "whip":
            xs.append(f"-0.075*iw*pow({pr},2)")
    z = "1+" + "+".join(terms) if terms else "1"
    x = "iw/2-(iw/zoom/2)" + "".join(xs)
    return f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}"


def measure(path: str):
    """Kembalikan (LUFS terintegrasi, true peak dBFS); -inf bila audio senyap."""
    r = sh(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    li = re.findall(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", r.stderr)
    pk = re.findall(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", r.stderr)
    return (float(li[-1]) if li else float("-inf")), (float(pk[-1]) if pk else float("-inf"))


# ----------------------------------------------------------------------------
# Teks & timing
# ----------------------------------------------------------------------------
def _clean_script_text(raw: str) -> str:
    """Bersihkan penanda produksi seperti [JEDA 2 DETIK] dan bintang markdown liar."""
    cleaned = re.sub(r"\[[^\]]*\]", " ", str(raw or ""))
    cleaned = cleaned.replace("*", "")
    return re.sub(r"\s+", " ", cleaned).strip()


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
    clean_text = _clean_script_text(text)
    r_sil = sh(["ffmpeg", "-hide_banner", "-i", vo_path, "-af", "silencedetect=noise=-33dB:d=0.10", "-f", "null", "-"])
    ss = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r_sil.stderr)]
    se = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r_sil.stderr)]
    sil = [(max(s, 0.0), se[i] if i < len(se) else dur) for i, s in enumerate(ss)]
    sp0 = sil[0][1] if sil and sil[0][0] <= 0.05 else 0.0
    sp1 = sil[-1][0] if sil and sil[-1][1] >= dur - 0.05 else dur

    unit_texts = [u for u in re.split(r"(?<=[.,!?;:…])\s+", clean_text) if u.strip()]
    words, units = [], []
    for ui, u in enumerate(unit_texts):
        a = len(words)
        for w in u.split():
            words.append({"w": w, "n": norm(w), "syl": syl(w), "u": ui})
        units.append([a, len(words)])
    assert words, "Teks narasi kosong."

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
        merged = []
        for g in groups:
            if len(g) == 1 and merged:
                merged[-1].extend(g)
            else:
                merged.append(list(g))
        if len(merged) > 1 and len(merged[0]) == 1:
            merged[1] = merged[0] + merged[1]
            merged.pop(0)
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
def _normalize_overlay_type(o: dict) -> dict:
    """Konversi defensif tipe lama ke tipe animasi modern yang didukung Remotion."""
    t = str(o.get("type") or "").strip().lower()
    if t in ALLOWED_OVERLAY_TYPES:
        return o

    if t == "list":
        items = o.get("items") or []
        first_txt = str(items[0].get("text") if items and isinstance(items[0], dict) else "").strip()
        o["type"] = "floating_card"
        o["badge"] = str(o.get("title") or "POIN UTAMA").upper()
        o["value"] = (first_txt or "FAKTA PENTING").upper()
        o["label"] = ""
        return o

    if t == "stat":
        lbl = str(o.get("label") or o.get("text") or "DATA UTAMA").strip().upper()
        o["type"] = "kinetic_typo"
        o.setdefault("kicker", "FAKTA")
        o.setdefault("lines", [lbl[:24]])
        return o

    if t in ("icon", "map"):
        txt = str(o.get("label") or o.get("text") or o.get("sub") or "FOKUS").strip().upper()
        o["type"] = "camera_beat"
        o["label"] = txt[:26]
        return o

    if t in ("lower", "quote"):
        o["type"] = "marker_quote"
        return o

    if t == "qmark" and o.get("label"):
        o["lines"] = [f"{str(o['label']).upper()}?"]
    o["type"] = "kinetic_typo"
    o.setdefault("lines", [str(o.get("text") or o.get("label") or "SOROTAN").upper()[:24]])
    return o


def lock_overlays(words: list, overlays: list, hook_lines: list, cta_text: str, dur: float) -> list:
    default_dur = {
        "roto_typo": 3.0 if IS_LONG else 2.5,
        "stat_counter": 3.0 if IS_LONG else 2.5,
        "kinetic_typo": 2.8 if IS_LONG else 2.3,
        "marker_quote": 3.2 if IS_LONG else 2.6,
        "floating_card": 3.4 if IS_LONG else 2.8,
        "draw_on": 2.8 if IS_LONG else 2.3,
        "camera_beat": 2.4 if IS_LONG else 2.0,
    }
    ovs = []
    extra = [{"type": "kinetic_typo", "at": 0.15, "lines": hook_lines, "dur": 2.4 if IS_LONG else 2.0, "sfx": "hit", "_fixed": True}] if hook_lines else []
    if cta_text:
        extra.append({"type": "kinetic_typo", "at": max(0.0, dur - 2.8), "lines": [cta_text], "dur": 2.6, "sfx": "hit", "_fixed": True})

    for o in extra + overlays:
        o = _normalize_overlay_type(json.loads(json.dumps(o)))
        if "at" in o:
            o["t0"] = float(o["at"]) if float(o["at"]) >= 0 else max(dur + float(o["at"]), 0.0)
        else:
            i = find_trigger(words, o.get("trigger", ""))
            if i is None:
                continue
            o["t0"] = words[i]["t0"]
        if o["t0"] >= dur - 0.3:
            continue
        ovs.append(o)

    prio = {
        "roto_typo": 0,
        "stat_counter": 1,
        "floating_card": 2,
        "marker_quote": 3,
        "kinetic_typo": 4,
        "draw_on": 5,
        "camera_beat": 6,
    }

    def _span(o):
        return o["t0"], o["t0"] + o.get("dur", default_dur.get(o["type"], 2.6))

    def _clash(a, b):
        (a0, a1), (b0, b1) = _span(a), _span(b)
        return a0 < b1 + OVERLAY_MIN_GAP and b0 < a1 + OVERLAY_MIN_GAP

    kept = [o for o in ovs if o.get("_fixed")]
    for o in sorted((o for o in ovs if not o.get("_fixed")), key=lambda o: (prio.get(o["type"], 4), o["t0"])):
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
        o["t1"] = max(min(o["t0"] + o.get("dur", default_dur.get(o["type"], 2.6)), nxt, dur), o["t0"] + 0.3)
        for it in o.get("items", []):
            if "abs" in it:
                it["_t"] = max(0.0, it["abs"] - o["t0"])
    return ovs


# ----------------------------------------------------------------------------
# Auto Director (Smart Local Planner + Sanitizer)
# ----------------------------------------------------------------------------
def _extract_claude_text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def _parse_json_loose(txt: str) -> dict:
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
    a, b = txt.find("{"), txt.rfind("}")
    assert a >= 0 and b > a, f"Balasan tidak berisi JSON: {txt[:200]}"
    return json.loads(txt[a:b + 1])


def _clean_hook_line(s: str) -> str:
    return re.sub(r"[*]+", "", str(s or "")).strip().upper()


def _sanitize_plan(p: dict) -> dict:
    if not isinstance(p, dict):
        raise ValueError("Rencana bukan objek JSON")

    raw_scenes = p.get("scenes") or p.get("kalimat") or []
    scenes = []
    ovs = []
    kw_pool = []

    for idx, sc in enumerate(raw_scenes, 1):
        if not isinstance(sc, dict):
            continue
        narasi = str(sc.get("narasi") or sc.get("text") or "").strip()
        visual_literal = str(sc.get("visual_literal") or "").strip()
        kw = str(sc.get("keyword_cari") or "").strip()
        try:
            skor = int(sc.get("skor_penting") or 0)
        except Exception:
            skor = 0

        ov = sc.get("overlay")
        valid_ov = None
        if isinstance(ov, dict) and ov.get("type") in ALLOWED_OVERLAY_TYPES and skor >= 4:
            if ov.get("type") == "floating_card" and not ov.get("value"):
                ov["type"] = "kinetic_typo"
            if ov.get("trigger") or narasi:
                if not ov.get("trigger"):
                    words_t = re.sub(r"[^a-zA-Z0-9\s]", "", narasi.lower()).split()
                    ov["trigger"] = " ".join(words_t[:3])
                ov["alasan"] = str(ov.get("alasan") or "").strip()
                valid_ov = ov
                ovs.append(ov)

        if kw:
            kw_pool.append(kw.upper())
        if narasi:
            scenes.append({
                "no": idx,
                "narasi": narasi,
                "visual_literal": visual_literal,
                "keyword_cari": kw,
                "skor_penting": skor,
                "overlay": valid_ov,
            })

    if not ovs:
        for o in p.get("overlays") or []:
            if isinstance(o, dict) and o.get("type") in ALLOWED_OVERLAY_TYPES and o.get("trigger"):
                ovs.append(o)

    if not ovs and scenes:
        # Jaminan penyelamatan terakhir jika seluruh skor < 4
        mid_idx = min(len(scenes) - 1, max(0, len(scenes) // 3))
        sc_fb = scenes[mid_idx]
        sc_fb["skor_penting"] = 5
        trig_fb = " ".join(re.sub(r"[^a-zA-Z0-9\s]", "", sc_fb["narasi"].lower()).split()[:4])
        ov_fb = {
            "type": "kinetic_typo",
            "trigger": trig_fb or "fakta utama",
            "kicker": "SOROTAN",
            "lines": [" ".join(sc_fb["narasi"].split()[:3]).upper() or "FAKTA UTAMA"],
            "alasan": "Sorotan utama otomatis",
        }
        sc_fb["overlay"] = ov_fb
        ovs.append(ov_fb)

    if not ovs:
        print(f"[DEBUG] scenes={len(raw_scenes)} ovs={len(ovs)} sample={[(x.get('skor_penting'), (x.get('overlay') or {}).get('type')) for x in raw_scenes[:8] if isinstance(x, dict)]}")
        raise ValueError("Tidak ada overlay valid pada kalimat penting")

    raw_kws = [str(x).strip().upper() for x in (p.get("keywords") or []) if str(x).strip()]
    merged_kws = list(dict.fromkeys(kw_pool + raw_kws))[:10]

    cta_q = str(p.get("cta_pertanyaan") or "").strip()
    if not cta_q and scenes and "?" in scenes[-1]["narasi"]:
        cta_q = scenes[-1]["narasi"]

    return {
        "theme": p.get("theme") if p.get("theme") in THEMES else "bumi",
        "topic": str(p.get("topic") or "video"),
        "hook_lines": [_clean_hook_line(x) for x in (p.get("hook_lines") or []) if _clean_hook_line(x)][:3],
        "fakta_konkret": [str(x).strip() for x in (p.get("fakta_konkret") or []) if str(x).strip()][:5],
        "cta_pertanyaan": cta_q,
        "keywords": merged_kws,
        "scenes": scenes,
        "overlays": ovs,
    }


def _extract_transcript_from_prompt(prompt: str) -> str:
    """Ambil blok TRANSKRIP utuh meskipun mengandung banyak paragraf (\\n\\n)."""
    m_exact = re.search(r"TRANSKRIP:\s*\n(.*?)\n\s*CATATAN GAYA & PROYEK:", prompt, re.S)
    if m_exact and len(m_exact.group(1).strip()) > 5:
        return m_exact.group(1).strip()
    m_open = re.search(r"TRANSKRIP[^\n]*:\s*\n(.*)$", prompt, re.S)
    if m_open and len(m_open.group(1).strip()) > 5:
        return m_open.group(1).strip()
    return prompt.strip()


def _build_overlay_for_sentence(sent: str, words_s: list, nums: list, is_babak: bool, ov_count: int) -> dict:
    """Bangun objek overlay yang valid & variatif untuk kalimat penting."""
    trig = " ".join(re.sub(r"[^a-zA-Z0-9\s]", "", sent.lower()).split()[:4]) or "fakta utama"
    key_phrase = " ".join(w.upper() for w in words_s[:3]) or "FAKTA LAPANGAN"

    if is_babak:
        parts_b = [p.strip() for p in re.split(r"[:.\-–]", sent, maxsplit=1)]
        b_title = parts_b[0].upper()[:28]
        b_sub = (parts_b[1] if len(parts_b) > 1 and parts_b[1] else key_phrase).upper()[:42]
        return {
            "type": "roto_typo" if ov_count % 2 == 0 else "marker_quote",
            "trigger": trig,
            "kicker": b_title,
            "badge": b_title,
            "text": b_sub,
            "lines": [b_title, b_sub[:22]],
            "alasan": "Penanda babak dokumenter",
        }

    if nums and not re.search(r"\b(pukul|jam|november|oktober|desember|januari|202\d)\b", sent, re.I):
        raw_digits = re.sub(r"\D", "", nums[0])
        raw_n = int(raw_digits) if raw_digits else 0
        if 1 <= raw_n <= 999999999:
            lbl = " ".join(w.upper() for w in words_s[:2]) or "DATA FAKTA"
            prefix = "Rp " if re.search(r"\brp\.?\b", sent, re.I) else ""
            suffix = "%" if "%" in sent else ""
            return {
                "type": "stat_counter",
                "trigger": trig,
                "prefix": prefix,
                "value": raw_n,
                "suffix": suffix,
                "label": lbl[:24],
                "alasan": "Statistik atau angka konkret",
            }

    cycle_types = ["roto_typo", "kinetic_typo", "marker_quote", "camera_beat"]
    chosen = cycle_types[ov_count % len(cycle_types)]
    line1 = " ".join(w.upper() for w in words_s[:2]) or "SOROTAN"
    line2 = " ".join(w.upper() for w in words_s[2:4]) or key_phrase

    if chosen == "roto_typo":
        return {
            "type": "roto_typo",
            "trigger": trig,
            "lines": [line1[:18], line2[:18]],
            "alasan": "Sorotan subjek & tipografi latar belakang",
        }
    if chosen == "camera_beat":
        return {
            "type": "camera_beat",
            "trigger": trig,
            "label": key_phrase[:26],
            "alasan": "Momen observasi lapangan",
        }
    if chosen == "marker_quote":
        return {
            "type": "marker_quote",
            "trigger": trig,
            "badge": "FAKTA LAPANGAN",
            "text": " ".join(sent.split()[:7]).upper()[:46],
            "alasan": "Kutipan fakta penting",
        }
    return {
        "type": "kinetic_typo",
        "trigger": trig,
        "kicker": line1[:20],
        "lines": [line1[:18], line2[:18]],
        "alasan": "Penekanan poin utama",
    }


def _local_smart_plan(prompt: str) -> dict:
    # Mode 1: Pencocokan Kalimat -> Klip
    if "DAFTAR KLIP TERSEDIA DI GOOGLE DRIVE:" in prompt:
        m_kal = re.search(r"DAFTAR KALIMAT & VISUAL YANG DIBUTUHKAN:\n(\[.*?\])\n\nDAFTAR KLIP", prompt, re.S)
        m_klip = re.search(r"DAFTAR KLIP TERSEDIA DI GOOGLE DRIVE:\n(\[.*?\])\n\nATURAN", prompt, re.S)
        kals = json.loads(m_kal.group(1)) if m_kal else []
        klips = json.loads(m_klip.group(1)) if m_klip else []

        seq_count = sum(1 for c in klips if re.match(r"^\d{2,3}_", c.get("nama_file", "")))
        if klips and seq_count >= max(3, int(len(klips) * 0.7)):
            klips_sorted = sorted(klips, key=lambda x: x.get("nama_file", ""))
            pilihan = []
            n_k, n_c = max(len(kals), 1), len(klips_sorted)
            for idx, k in enumerate(kals):
                c_idx = min(int(idx * n_c / n_k), n_c - 1)
                c = klips_sorted[c_idx]
                trig = " ".join(re.sub(r"[^a-zA-Z0-9\s]", "", k.get("kalimat", "").lower()).split()[:4])
                pilihan.append({
                    "no": k.get("no", idx + 1),
                    "kalimat": k.get("kalimat", ""),
                    "frasa_trigger": trig,
                    "id_klip": c["id"],
                    "alasan": f"Urutan kronologis #{c_idx + 1:02d}",
                })
            return {"pilihan": pilihan}

        stop = {"yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada", "ini", "itu", "ada", "atau", "juga"}
        pilihan, used_recent = [], []
        for k in kals:
            q_words = set(re.findall(r"[a-z0-9]{3,}", (k.get("kalimat", "") + " " + k.get("keyword", "")).lower())) - stop
            best_c, best_sc = (klips[0] if klips else None), -1
            for c in klips:
                c_words = set(re.findall(r"[a-z0-9]{3,}", (c.get("nama_file", "") + " " + c.get("deskripsi_visual", "")).lower())) - stop
                sc = len(q_words & c_words) * 3 - (2 if c.get("id") in used_recent[-2:] else 0)
                if sc > best_sc:
                    best_sc, best_c = sc, c
            if best_c:
                used_recent.append(best_c["id"])
                trig = " ".join(re.sub(r"[^a-zA-Z0-9\s]", "", k.get("kalimat", "").lower()).split()[:4])
                pilihan.append({
                    "no": k.get("no", 1),
                    "kalimat": k.get("kalimat", ""),
                    "frasa_trigger": trig,
                    "id_klip": best_c["id"],
                    "alasan": f"Kecocokan kata kunci ({max(best_sc, 1)} poin)",
                })
        return {"pilihan": pilihan}

    # Mode 2: Sutradara Per Kalimat dari TRANSKRIP utuh
    raw_trx = _extract_transcript_from_prompt(prompt)
    trx = _clean_script_text(raw_trx)

    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", trx) if len(s.strip()) > 4]
    if not sents:
        sents = [trx or "Dokumentasi lapangan"]

    stop = {
        "yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada", "ini", "itu",
        "ada", "warga", "dalam", "saat", "juga", "atau", "oleh", "akan", "bisa",
        "tidak", "lebih", "sangat", "seperti", "karena", "namun", "tetapi", "sehingga",
        "antara", "secara", "berdasarkan", "hasil", "tahun", "berselang", "mereka",
        "kita", "kami", "adalah", "sebagai", "agar", "para", "sudah", "telah",
    }

    total_words = sum(len(s.split()) for s in sents) or 1
    est_dur = total_words / 2.6
    max_ov_cap = min(OVERLAY_MAX - 2, max(4, int(est_dur / 32.0))) if IS_LONG else min(OVERLAY_MAX - 2, 5)
    min_sent_gap = max(3, len(sents) // max(max_ov_cap, 1))

    # Hindari ~4 kalimat pertama & ~3 kalimat terakhir agar tidak bentrok dengan hook (0.15s) & CTA (akhir)
    safe_start = min(3, max(1, len(sents) // 12))
    safe_end = max(safe_start + 1, len(sents) - min(3, max(1, len(sents) // 12)))

    scenes, facts, kws = [], [], []
    last_ov_idx = -min_sent_gap
    ov_count = 0

    for idx, s in enumerate(sents, 1):
        words_s = [w for w in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", s) if w.lower() not in stop]
        kw = " ".join(words_s[:3]).lower() or "dokumentasi lapangan"
        kws.append(kw.upper())
        nums = re.findall(r"\b\d{1,3}(?:[.,]\d{3})*\b", s)
        is_babak = bool(re.match(r"^(?:babak|bagian)\s+(?:\d+|[ivx]+)\b", s.strip(), re.I))
        has_entity = bool(re.search(r"\b(baduy|pikukuh|kanekes|kebakaran|mitigasi|leuit|lumbung|rumbia|jaro|puun|adat|larangan|bantuan)\b", s, re.I))

        skor = 5 if (is_babak or nums or "?" in s) else (4 if (has_entity and len(words_s) >= 3) else 2)
        if nums or is_babak:
            facts.append(s[:110])

        ov = None
        in_safe_zone = safe_start < idx <= safe_end or len(sents) <= 5
        if skor >= 4 and in_safe_zone and (idx - last_ov_idx) >= min_sent_gap and ov_count < max_ov_cap:
            ov = _build_overlay_for_sentence(s, words_s, nums, is_babak, ov_count)
            last_ov_idx = idx
            ov_count += 1

        scenes.append({
            "narasi": s,
            "visual_literal": f"Rekaman nyata: {kw}",
            "keyword_cari": kw,
            "skor_penting": skor,
            "overlay": ov,
        })

    # Jaminan pasti ada overlay valid di zona aman jika filter di atas belum menghasilkan apa pun
    if not any(sc["overlay"] for sc in scenes) and scenes:
        target_i = min(len(scenes) - 1, max(0, len(scenes) // 3))
        sc_t = scenes[target_i]
        words_t = [w for w in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", sc_t["narasi"]) if w.lower() not in stop]
        sc_t["skor_penting"] = 5
        sc_t["overlay"] = _build_overlay_for_sentence(sc_t["narasi"], words_t, [], False, 0)

    first_w = [w.upper() for w in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", sents[0]) if w.lower() not in stop][:4]
    hook = [" ".join(first_w[:2]) or "DOKUMENTER BUDAYA", " ".join(first_w[2:4]) or "FAKTA LAPANGAN"]
    cta = next((s for s in reversed(sents) if "?" in s), "Bagaimana pendapat Anda tentang hal ini?")
    return {
        "theme": "bumi",
        "topic": " ".join(first_w[:3]) or "DOKUMENTER BADUY",
        "hook_lines": hook,
        "fakta_konkret": facts[:4] or sents[:3],
        "cta_pertanyaan": cta,
        "keywords": list(dict.fromkeys(kws))[:8],
        "scenes": scenes,
    }


def _call_llm_json(prompt: str, anthropic_client=None, model: str = "claude-sonnet-4-6") -> dict:
    print("[Planner] Menggunakan Smart Local Planner (0 API, 0 kuota)...")
    return _local_smart_plan(prompt)


def run_auto_director(client: anthropic.Anthropic, text: str, style_note: str, project_note: str, model: str = "claude-sonnet-4-6") -> dict:
    note_combined = style_note
    if project_note.strip():
        note_combined += f"\nCATATAN KHUSUS PROYEK INI: {project_note.strip()}"

    max_ov = "12-18 item" if IS_LONG else "4-6 item"
    fmt_label = "YouTube dokumenter layar lebar 16:9" if IS_LONG else "YouTube Shorts dokumenter/berita Indonesia"

    dir_prompt = f"""Kamu adalah editor dokumenter & sutradara visual {fmt_label}.
Tugasmu memecah transkrip narasi VO menjadi daftar scene PER KALIMAT yang sangat akurat secara visual dan faktual.

TRANSKRIP:
{text}

CATATAN GAYA & PROYEK:
{note_combined}

ATURAN PERENCANAAN WAJIB:
1. PECAH PER KALIMAT ("scenes"): Setiap kalimat/frasa utama di transkrip wajib menjadi 1 objek scene dengan field:
  - "narasi": teks persis kalimat tersebut dari transkrip.
  - "visual_literal": apa yang HARUS tampak nyata di layar untuk membuktikan kalimat itu.
  - "keyword_cari": 2-4 kata benda/aksi konkret yang bisa difilmkan/dicari.
  - "skor_penting": angka 1-5 (5 = angka/nominal konkret, nama tokoh/lokasi spesifik, klimaks konflik, atau pertanyaan penutup; 1-3 = kalimat transisi biasa).
  - "overlay": null untuk kalimat skor 1-3. HANYA isi objek overlay pada kalimat skor 4-5, dengan jarak minimal 6-7 detik antar overlay (total maksimal {max_ov} untuk seluruh video).
"""

    last_err = None
    for attempt in range(1, 4):
        try:
            return _sanitize_plan(_call_llm_json(dir_prompt, anthropic_client=client, model=model))
        except Exception as e:
            last_err = e
            print(f"(!) Auto Director percobaan {attempt}/3 gagal: {str(e)[:180]}")
    raise RuntimeError(f"Auto Director gagal setelah 3 percobaan: {last_err}")


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


def _gemini_vision_label(frames: list, clip_name: str):
    return None


def run_auto_broll(client: anthropic.Anthropic, clips: list, unit_texts: list, words: list, proj_dir: str, model: str = "claude-sonnet-4-6", scenes: list = None) -> dict:
    cache_p = os.path.join(proj_dir, "clip_labels.json")
    try:
        cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    except Exception:
        cache = {}

    dirty = False
    for c in clips:
        sz = os.path.getsize(c["path"])
        if c["name"] in cache and cache[c["name"]].get("size") == sz and cache[c["name"]].get("deskripsi"):
            print("Cache label:", c["name"], "->", cache[c["name"]].get("deskripsi", "")[:60])
            continue
        try:
            out = _gemini_vision_label(c.get("frames") or [], c["name"])
            if out and out.get("deskripsi"):
                cache[c["name"]] = {"size": sz, **out}
                dirty = True
                print("Label Gemini baru:", c["name"], "->", out.get("deskripsi", "")[:60])
            else:
                clean_nm = re.sub(r"^\d+_", "", os.path.splitext(c["name"])[0]).replace("_", " ")
                cache[c["name"]] = {"size": sz, "deskripsi": clean_nm, "tag": clean_nm.split(), "ada_orang": True, "wajah_jelas": False}
                dirty = True
        except Exception as e:
            print(f"(!) Gagal label {c['name']}: {str(e)[:140]}")
            clean_nm = re.sub(r"^\d+_", "", os.path.splitext(c["name"])[0]).replace("_", " ")
            cache[c["name"]] = {"size": sz, "deskripsi": clean_nm, "tag": clean_nm.split(), "ada_orang": True, "wajah_jelas": False}
            dirty = True

    if dirty:
        with open(cache_p, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)

    ids = {f"K{i + 1:02d}": c for i, c in enumerate(clips)}
    klip_list = [
        {
            "id": k,
            "nama_file": c["name"],
            "deskripsi_visual": cache.get(c["name"], {}).get("deskripsi", c["name"]),
            "ada_orang": cache.get(c["name"], {}).get("ada_orang", False),
        }
        for k, c in ids.items()
    ]

    if scenes:
        kal_list = [
            {
                "no": idx + 1,
                "kalimat": sc.get("narasi", ""),
                "visual_yang_dibutuhkan": sc.get("visual_literal", ""),
                "keyword": sc.get("keyword_cari", ""),
            }
            for idx, sc in enumerate(scenes)
            if sc.get("narasi")
        ]
    else:
        kal_list = [{"no": idx + 1, "kalimat": u, "visual_yang_dibutuhkan": u} for idx, u in enumerate(unit_texts)]

    match_prompt = f"""Kamu adalah asisten editor dokumenter yang memasangkan SETIAP kalimat narasi dengan klip video/foto yang PALING MEMBUKTIKAN isi kalimat tersebut.

DAFTAR KALIMAT & VISUAL YANG DIBUTUHKAN:
{json.dumps(kal_list, ensure_ascii=False, indent=2)}

DAFTAR KLIP TERSEDIA DI GOOGLE DRIVE:
{json.dumps(klip_list, ensure_ascii=False, indent=2)}

ATURAN PENCOCOKAN WAJIB:
1. Pasangkan SETIAP nomor kalimat ("no") dengan 1 "id_klip" (K01, K02, dst).
"""

    hints = {}
    scene_pairs = []
    try:
        res_match = _call_llm_json(match_prompt, anthropic_client=client, model=model)
        for p in res_match.get("pilihan", []):
            c = ids.get(p.get("id_klip"))
            if not c:
                continue
            kal_txt = str(p.get("kalimat") or "").strip()
            trig = str(p.get("frasa_trigger") or "").strip()
            if not trig and kal_txt:
                trig = " ".join(re.sub(r"[^a-zA-Z0-9\s]", "", kal_txt.lower()).split()[:4])
            if trig:
                hints[trig] = c["name"]
            scene_pairs.append({
                "no": p.get("no"),
                "kalimat": kal_txt,
                "klip": c["name"],
                "alasan": str(p.get("alasan") or "").strip(),
            })
    except Exception as e:
        print(f"(!) Pencocokan B-roll gagal: {str(e)[:180]}")

    try:
        with open(os.path.join(proj_dir, "scene_pairs.json"), "w", encoding="utf-8") as fh:
            json.dump(scene_pairs, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass

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
            matched_name = next((nm for nm in usable if nm.lower() == v.lower()), None) or next((nm for nm in usable if v.lower() in nm.lower()), None)
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
    ovs = lock_overlays(words, overlays_raw, hook_lines, cta_text, dur)
    sfx_ev = sorted({round(o["t0"], 2) for o in ovs if o.get("sfx")})

    clips = [
        c for c in scan_local_clips(proj_dir, work_dir, extract_frames=False)
        if not any(ex in c["name"].lower() for ex in excluded)
    ]
    assert clips, "Tidak ada klip B-roll tersisa untuk dirender!"
    clips = prepare_still_clips(clips, work_dir)

    hook_cut = 2.2 if IS_LONG else 1.4
    body_cut = 4.2 if IS_LONG else 2.4
    hook_window = 5.0 if IS_LONG else 3.0
    grade = "eq=contrast=1.04:saturation=1.05:gamma_r=1.03:gamma_b=0.96,colorbalance=rm=0.03:bm=-0.03"
    cut_ts = [0.0]
    while cut_ts[-1] < dur - 0.01:
        step = hook_cut if cut_ts[-1] < hook_window else body_cut
        if dur - (cut_ts[-1] + step) < 1.5:
            step = dur - cut_ts[-1]
        cut_ts.append(min(cut_ts[-1] + step, dur))
    bounds = [round(t * FPS) for t in cut_ts]
    bounds[-1] = n_frames
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
                exact = [c for c in clips if c["name"].lower() == nm.lower()]
                partial = [c for c in clips if nm.lower() in c["name"].lower() and c not in exact]
                order = (exact or partial) + order
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
        kin = cut_kind(s["i"] - 1) if s["i"] > 0 else None
        kout = cut_kind(s["i"]) if s["i"] < n_sh - 1 else None
        still = bool(c.get("still"))
        drift = 0.0 if still else DRIFT
        pre = ["-stream_loop", "-1"] if s["loop"] else []
        if c["is_img"]:
            pre = ["-loop", "1"]
        base = ["ffmpeg", "-y", "-v", "error"] + pre + ["-ss", f"{s['ss']:.3f}", "-t", f"{s['dur'] + 0.4:.3f}", "-i", c["path"], "-an"]
        out = ["-frames:v", str(fr), "-r", "30", "-c:v", "libx264", "-crf", "16", "-preset", "ultrafast", "-pix_fmt", "yuv420p", out_p]
        if IS_LONG:
            g = "" if still else f"{grade},"
            if c.get("vertical", False) and not still:
                fc = (
                    f"[0:v]fps=30,{grade},split=2[a][b];"
                    f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=35:6,eq=brightness=-0.12[bg];"
                    f"[b]scale=-2:{H}:flags=lanczos[fg];"
                    f"[bg][fg]overlay=x='(W-w)/2':y='(H-h)/2',scale={scale_up_w}:{scale_up_h},{motion_filter(fr, drift, kin, kout)},format=yuv420p[v]"
                )
                sh(base + ["-filter_complex", fc, "-map", "[v]"] + out)
            else:
                vf = (
                    f"fps=30,{g}scale={scale_up_w}:{scale_up_h}:force_original_aspect_ratio=increase,crop={scale_up_w}:{scale_up_h},"
                    f"{motion_filter(fr, drift, kin, kout)},format=yuv420p"
                )
                sh(base + ["-vf", vf] + out)
        else:
            if c.get("vertical", True):
                g = "" if still else f"{grade},"
                vf = (
                    f"fps=30,{g}scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
                    f"{motion_filter(fr, drift, kin, kout)},format=yuv420p"
                )
                sh(base + ["-vf", vf] + out)
            else:
                zp = f"zoompan=z='1+{DRIFT}*on/{max(fr - 1, 1)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=880x495:fps=30"
                fc = (
                    f"[0:v]fps=30,{grade},split=2[a][b];"
                    f"[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:6,eq=brightness=-0.12[bg];"
                    f"[b]scale=880:495:force_original_aspect_ratio=increase,crop=880:495,{zp}[fg];"
                    f"[bg][fg]overlay=100:433[t];[t][1:v]overlay=0:0:format=auto,format=yuv420p[v0];"
                    f"[v0]scale=1620:2880,{motion_filter(fr, 0.0, kin, kout)},format=yuv420p[v]"
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
    glass_src = None
    try:
        blur_p = f"{remo_dir}/public/base_blur.mp4"
        bw, bh = (480, 270) if IS_LONG else (270, 480)
        sh([
            "ffmpeg", "-y", "-v", "error", "-i", base_mp4,
            "-vf", f"scale={bw}:{bh}:flags=area,gblur=sigma=2.2,scale={W}:{H}:flags=bicubic,eq=brightness=-0.04",
            "-an", "-c:v", "libx264", "-crf", "24", "-preset", "veryfast", "-g", "15", "-pix_fmt", "yuv420p", blur_p,
        ])
        glass_src = "base_blur.mp4"
    except Exception as e:
        print(f"[peringatan] Latar kaca buram dilewati: {str(e)[:200]}")

    conc = str(max(2, min(4, os.cpu_count() or 2)))

    # --- Ekstraksi layer rotoscoping subjek untuk overlay bertipe roto_typo ---
    for idx_ov, o in enumerate(ovs):
        if o.get("type") == "roto_typo":
            seg_dur = max(0.4, o["t1"] - o["t0"])
            seg_mp4 = os.path.join(work_dir, f"roto_in_{idx_ov}.mp4")
            roto_name = f"roto_{idx_ov}.webm"
            roto_out = os.path.join(remo_dir, "public", roto_name)
            try:
                sh([
                    "ffmpeg", "-y", "-v", "error",
                    "-ss", f"{o['t0']:.3f}", "-t", f"{seg_dur:.3f}",
                    "-i", base_mp4, "-an",
                    "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    seg_mp4,
                ])
                if extract_roto_layer(seg_mp4, roto_out, fps=FPS):
                    o["rotoSrc"] = roto_name
                    print(f"[roto] Layer subjek aktif untuk overlay #{idx_ov + 1}: {roto_name}")
            except Exception as e:
                print(f"[roto] Gagal pada overlay #{idx_ov + 1}, fallback ke teks biasa: {str(e)[:120]}")

    def render_overlay(glass):
        props = {"overlays": ovs, "fps": FPS, "durationInFrames": n_frames, "images": [], "captions": caps, "theme": t_props, "glassSrc": glass}
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

    try:
        overlay_file, used_codec = render_overlay(glass_src)
    except Exception as e:
        if glass_src is None:
            raise
        print(f"[peringatan] Render dengan latar kaca gagal ({str(e)[-300:]}); mengulang tanpa latar kaca...")
        overlay_file, used_codec = render_overlay(None)

    # --- SFX + mix audio ---
    SR = 48000
    rng = np.random.default_rng(7)
    n_hit = int(0.25 * SR)
    t_arr = np.arange(n_hit) / SR
    br = np.cumsum(rng.standard_normal(n_hit))
    br = (br - np.linspace(br[0], br[-1], n_hit))
    br = br / np.max(np.abs(br))
    nz = sosfilt(butter(2, 500, "low", fs=SR, output="sos"), br) * np.exp(-t_arr / 0.05)
    hit = (np.sin(2 * np.pi * 70 * t_arr) * np.exp(-t_arr / 0.06) * 0.8 + nz * 0.5)
    hit = (hit / np.max(np.abs(hit)) * 0.9) * (10 ** (-12 / 20))

    stem = np.zeros(int(dur * SR) + SR)
    for t_ev in sfx_ev:
        st = int(t_ev * SR)
        if st < len(stem):
            seg = hit[:len(stem) - st]
            stem[st:st + len(seg)] += seg
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
