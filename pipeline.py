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
from remotion_builder import prepare_remotion_project, download_geo_assets

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


def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"CMD GAGAL: {cmd}\n{r.stderr[-1800:]}")
    return r


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
        rot = int(float(next((sd["rotation"] for sd in v.get("side_data_list", []) if "rotation" in sd), v.get("tags", {}).get("rotate", 0))))
        w, h = int(v["width"]), int(v["height"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        info.update(w=w, h=h, rot=rot, vertical=h > w)
    if a:
        info["ch"] = int(a["channels"])
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
        if os.path.exists(out_p) or still_to_clip(c["path"], out_p):
            c.update(path=out_p, dur=STILL_DUR, is_img=False, vertical=(not IS_LONG), still=True)
            print("Gambar -> klip:", c["name"])
        else:
            c["dur"] = STILL_DUR
    return clips


def cut_kind(i: int) -> str:
    return "zoom" if i % 2 == 0 else "whip"


def motion_filter(fr: int, drift: float, kin, kout, src_scale: bool = True) -> str:
    n = CUT_FRAMES
    terms = []
    if drift:
        terms.append(f"{drift}*on/{max(fr - 1, 1)}")
    xs, blurs = [], []
    if kout:
        pr = f"max(0,(on-({fr}-{n}))/{n})"
        terms.append(f"{0.22 if kout == 'zoom' else 0.26}*pow({pr},2)")
        if kout == "whip":
            xs.append(f"+0.075*iw*pow({pr},2)")
            blurs.append(f"gblur=sigma=28:sigmaV=0.01:enable='gt(n,{fr - n - 1})'")
        else:
            blurs.append(f"gblur=sigma=10:enable='gt(n,{fr - n - 1})'")
    if kin:
        pr = f"max(0,({n}-on)/{n})"
        terms.append(f"{0.22 if kin == 'zoom' else 0.26}*pow({pr},2)")
        if kin == "whip":
            xs.append(f"-0.075*iw*pow({pr},2)")
            blurs.append(f"gblur=sigma=28:sigmaV=0.01:enable='lt(n,{n})'")
        else:
            blurs.append(f"gblur=sigma=10:enable='lt(n,{n})'")
    z = "1+" + "+".join(terms) if terms else "1"
    x = "iw/2-(iw/zoom/2)" + "".join(xs)
    zp = f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}"
    return ",".join([zp] + blurs)


def measure(path: str):
    r = sh(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    return (
        float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1]),
        float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)[-1]),
    )


def syl(w: str) -> int:
    return max(len(re.findall(r"[aiueo]", w.lower())), 1) if re.search(r"\w", w) else 0


def norm(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower())


def similar(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.82


def find_trigger(words: list, trig: str, after: int = 0):
    if not trig:
        return None
    tk = [norm(x) for x in trig.split() if norm(x)]
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

    t_cur = sp0
    tot_s = sum(w["syl"] for w in words) or 1
    for w in words:
        d_w = (sp1 - sp0) * w["syl"] / tot_s
        w["t0"], w["t1"] = t_cur, t_cur + d_w
        t_cur += d_w

    from faster_whisper import WhisperModel
    raw_16k = subprocess.run(["ffmpeg", "-v", "error", "-i", vo_path, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"], capture_output=True).stdout
    audio_np = np.frombuffer(raw_16k, np.float32)

    m = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(audio_np, language="id", word_timestamps=True, beam_size=5)
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
    for k in range(1, n):
        if t0[k] < t1[k - 1]:
            t0[k] = t1[k - 1]
        if t1[k] < t0[k] + 0.04:
            t1[k] = t0[k] + 0.04
    for k, w in enumerate(words):
        w["t0"], w["t1"] = t0[k], t1[k]

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
        return [g for g in groups if len(g) > 1] or groups

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
            cues.append({"s": g[0]["t0"] - 0.04, "e": g[-1]["t1"] + 0.12, "cap": capl})
    for i in range(1, len(cues)):
        if cues[i]["s"] < cues[i - 1]["e"] + 0.03:
            cues[i - 1]["e"] = max(cues[i]["s"] - 0.03, cues[i - 1]["s"] + 0.15)
    if cues:
        cues[-1]["e"] = min(cues[-1]["e"], dur)
    return [{"s": c["s"], "e": c["e"], "lines": c["cap"]} for c in cues]


def _normalize_overlay_type(o: dict) -> dict:
    """Alihkan tipe 'map' ke 'lower' atau 'big' tanpa koordinat/peta."""
    if o.get("type") != "map":
        return o
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
    return o


def lock_overlays(words: list, overlays: list, hook_lines: list, cta_text: str, dur: float) -> list:
    default_dur = {"lower": 2.8 if IS_LONG else 2.4, "icon": 3.2, "qmark": 2.4, "big": 2.8, "solid": 3.2, "stat": 3.6, "quote": 4.2, "list": 4.2, "produk": 4.0}
    ovs = []
    extra = [{"type": "big", "at": 0.15, "lines": hook_lines, "dur": 2.4 if IS_LONG else 2.0, "sfx": "hit", "_fixed": True}] if hook_lines else []
    if cta_text:
        extra.append({"type": "big", "at": max(0.0, dur - 2.8), "lines": [cta_text], "dur": 2.6, "sfx": "hit", "_fixed": True})

    for o in extra + overlays:
        o = _normalize_overlay_type(json.loads(json.dumps(o)))
        if o["type"] in ("list", "quote"):
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
        o["t1"] = min(o["t0"] + o.get("dur", default_dur.get(o["type"], 2.8)), nxt, dur)
        for it in o.get("items", []):
            it["_t"] = max(0.0, it["abs"] - o["t0"])
    return ovs


def _extract_claude_text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


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
  - "stat": {{"type":"stat", "trigger":"...", "value":"ANGKA/KUTIPAN SINGKAT", "label":"KETERANGAN SINGKAT"}}
  - "list": {{"type":"list", "trigger":"...", "title":"JUDUL DAFTAR", "items":[{{"text":"POIN 1"}}, {{"text":"POIN 2"}}, {{"text":"POIN 3"}}]}}
  - "icon": {{"type":"icon", "trigger":"...", "icon":"api|bahaya|fakta|berita|waktu|silang|centang", "label":"LABEL SINGKAT"}}
  - "qmark": {{"type":"qmark", "trigger":"...", "label":"PERTANYAAN SINGKAT?"}}
4. Semua teks di dalam overlay HARUS singkat (maksimal 3-6 kata per baris/item) dan menggunakan huruf KAPITAL.
5. Keluarkan HANYA JSON valid tanpa markdown ```json atau penjelasan lain dengan struktur:
{{"theme":"...","topic":"...","hook_lines":["...","..."],"keywords":["KATA1","KATA2","KATA3","KATA4","KATA5"],"overlays":[...]}}"""

    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": dir_prompt}],
    )
    txt = _extract_claude_text(resp)
    a, b = txt.find("{"), txt.rfind("}")
    assert a >= 0 and b > a, f"Balasan Auto Director tidak berisi JSON: {txt[:200]}"
    return json.loads(txt[a:b + 1])


def scan_local_clips(proj_dir: str, work_dir: str, extract_frames: bool = True) -> list:
    frames_root = os.path.join(work_dir, "frames")
    os.makedirs(frames_root, exist_ok=True)
    cands = [
        p for p in sorted(glob.glob(f"{proj_dir}/*"))
        if p.lower().endswith(VID_EXT + IMG_EXT)
        and not any(x in os.path.basename(p).lower() for x in ("output_", "short_", "long_"))
    ]
    clips = []
    for p in cands:
        name = os.path.basename(p)
        info = probe(p)
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
                sh(["ffmpeg", "-y", "-v", "error", "-i", p, "-vf", "fps=1,scale=480:-2", "-q:v", "4", f"{d}/%04d.jpg"])
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
    cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}

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
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(open(fr[i], "rb").read()).decode(),
                    },
                }
                for i in idxs
            ]
            content.append({"type": "text", "text": f"Nama file: {c['name']}. Panggil tool label_klip untuk mendeskripsikan isi visualnya secara ringkas."})
            r = client.messages.create(
                model=model,
                max_tokens=600,
                tools=[label_tool],
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": content}],
            )
            tu = next((b for b in r.content if getattr(b, "type", "") == "tool_use"), None)
            if tu is not None:
                out = tu.input
                cache[c["name"]] = {"size": sz, **out}
                json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                print("Label baru:", c["name"], "->", out.get("deskripsi", "")[:60])
        except Exception as e:
            print(f"(!) Gagal label {c['name']}: {str(e)[:180]}")

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
            tool_choice={"type": "auto"},
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


def apply_edit_command(state: dict, raw_cmd: str) -> dict:
    cmd_part = raw_cmd.split("|", 1)[1].strip() if "|" in raw_cmd else raw_cmd.strip()
    low = raw_cmd.strip().lower()

    if low.startswith("/hapus"):
        nums = set()
        for tok in re.split(r"[,\s]+", cmd_part):
            if "-" in tok:
                a, b = tok.split("-", 1)
                if a.isdigit() and b.isdigit():
                    nums.update(range(int(a), int(b) + 1))
            elif tok.isdigit():
                nums.add(int(tok))
        state["overlays"] = [o for i, o in enumerate(state["overlays"], 1) if i not in nums]

    elif low.startswith("/tema"):
        t = cmd_part.lower()
        if t in THEMES:
            state["theme"] = t

    elif low.startswith("/hook"):
        if cmd_part == "-":
            state["hook_lines"] = []
        else:
            state["hook_lines"] = [h.strip().upper() for h in cmd_part.split("|") if h.strip()]

    elif low.startswith("/cta"):
        state["cta_text"] = "" if cmd_part == "-" else cmd_part.strip().upper()

    elif low.startswith("/klip"):
        items = list(state.get("clip_hints", {}).items())
        for chunk in cmd_part.split(";"):
            if "=" not in chunk:
                continue
            k, v = [x.strip() for x in chunk.split("=", 1)]
            matched_name = next((nm for nm in state.get("usable_names", []) if v.lower() in nm.lower()), None)
            if not matched_name:
                continue
            if k.isdigit() and 1 <= int(k) <= len(items):
                items[int(k) - 1] = (items[int(k) - 1][0], matched_name)
            else:
                items.append((k, matched_name))
        state["clip_hints"] = dict(items)

    elif low.startswith("/buang_klip"):
        bad = {x.strip().lower() for x in cmd_part.split(",") if x.strip()}
        state["excluded_clips"] = list(set(state.get("excluded_clips", [])) | bad)
        state["clip_hints"] = {
            f: nm for f, nm in state.get("clip_hints", {}).items()
            if not any(b in nm.lower() for b in bad)
        }

    return state


def render_final_video(state: dict, proj_dir: str, work_dir: str, remo_dir: str = "/tmp/remotion_overlay") -> str:
    vo_path = os.path.join(work_dir, "VO_padat.wav")
    if not os.path.exists(vo_path):
        audio_cands = sorted(glob.glob(f"{proj_dir}/*.mp3") + glob.glob(f"{proj_dir}/*.m4a") + glob.glob(f"{proj_dir}/*.wav"))
        assert audio_cands, f"File audio VO tidak ditemukan di {proj_dir}"
        vo_path = tighten_vo(audio_cands[0], work_dir)

    vo_info = probe(vo_path)
    dur = vo_info["dur"]
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
    sfx_ev = [(0.15, "hit")] if hook_lines else []

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
    if not IS_LONG:
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
        for f in as_completed(futs):
            f.result()

    list_txt = os.path.join(work_dir, "list.txt")
    with open(list_txt, "w") as fh:
        for s in shots:
            shot_p = os.path.join(shots_dir, f"s{s['i']:03d}.mp4")
            fh.write(f"file '{shot_p}'\n")
    base_mp4 = os.path.join(work_dir, "base.mp4")
    sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", base_mp4])

    prepare_remotion_project(remo_dir)
    download_geo_assets(remo_dir)

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

    conc = max(2, min(4, os.cpu_count() or 2))

    def render_overlay(glass):
        props = {"overlays": ovs, "fps": FPS, "durationInFrames": n_frames, "images": [], "captions": caps, "theme": t_props, "glassSrc": glass}
        with open(f"{remo_dir}/props.json", "w") as fh:
            json.dump(props, fh, sort_keys=True)
        ov_file, codec = f"{remo_dir}/out/overlay.webm", "vp9"
        try:
            sh(f"cd {remo_dir} && npx remotion render src/index.ts Overlay {ov_file} --props=props.json --codec=vp9 --pixel-format=yuva420p --image-format=png --crf=18 --overwrite --concurrency={conc}")
        except Exception as e:
            print(f"[peringatan] VP9 gagal ({e}); beralih ke ProRes 4444...")
            codec = "prores"
            ov_file = f"{remo_dir}/out/overlay.mov"
            sh(f"cd {remo_dir} && npx remotion render src/index.ts Overlay {ov_file} --props=props.json --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le --image-format=png --overwrite --concurrency={conc}")
        return ov_file, codec

    try:
        overlay_file, used_codec = render_overlay(glass_src)
    except Exception as e:
        if glass_src is None:
            raise
        print(f"[peringatan] Render dengan latar kaca gagal ({str(e)[-300:]}); mengulang tanpa latar kaca...")
        overlay_file, used_codec = render_overlay(None)

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
    for t_ev, _ in sfx_ev:
        st = int(t_ev * SR)
        if st < len(stem):
            seg = hit[:len(stem) - st]
            stem[st:st + len(seg)] += seg
    stem = stem[:int(dur * SR)]
    stem_wav = os.path.join(work_dir, "SFX_stem.wav")
    wavfile.write(stem_wav, SR, np.stack([stem, stem], 1).astype(np.float32))

    mix_wav = os.path.join(work_dir, "mix.wav")
    pan = "pan=stereo|c0=c0|c1=c0" if vo_info.get("ch", 1) == 1 else "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"
    gain, lufs = 0.0, -16.0
    for _ in range(8):
        fc = (
            f"[0:a]aresample=48000,afade=t=in:d=0.01,acompressor=threshold=0.09:ratio=3:attack=4:release=60,"
            f"volume={gain:.2f}dB,alimiter=limit=0.63:attack=2:release=30:level=0,{pan}[v];"
            f"[v][1:a]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.7:level=0[m]"
        )
        sh(["ffmpeg", "-y", "-v", "error", "-i", vo_path, "-i", stem_wav, "-filter_complex", fc, "-map", "[m]", "-ar", "48000", "-ac", "2", "-t", f"{dur:.4f}", mix_wav])
        lufs, _ = measure(mix_wav)
        if abs(lufs - (-16.0)) < 0.1:
            break
        gain += (-16.0 - lufs)

    slug = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")[:40] or "video"
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
            "-map", "[v]", "-map", "2:a", "-frames:v", str(n_frames),
            "-c:v", "libx264", "-crf", str(FINAL_CRF), "-maxrate", FINAL_MAXRATE, "-bufsize", "20M", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", final_mp4,
        ]
    )
    return final_mp4
