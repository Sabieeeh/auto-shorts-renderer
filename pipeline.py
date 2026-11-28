"""
pipeline.py — Orchestrator render pipeline auto-shorts-renderer v2.

═══════════════════════════════════════════════════════════════════════════
FUNGSI PUBLIK (dipakai main.py):
  - run_auto_director(client, text, style_note, project_note, ...)
  - scan_local_clips(proj_dir, work_dir, extract_frames)
  - run_auto_broll(client, clips, unit_texts, words, proj_dir, ...)
  - apply_edit_command(state, raw_cmd)
  - render_final_video(state, proj_dir, work_dir, remo_dir)

RE-EXPORT dari audio_utils (kompatibilitas impor lama):
  - sh, _concat_quote, probe, tighten_vo, measure, align_whisper

RE-EXPORT dari config (kompatibilitas impor lama):
  - DEFAULT_STYLE_NOTE, THEMES, ALLOWED_OVERLAY_TYPES

KONTRAK IMPOR:
  Modul ini WAJIB di-import SETELAH `os.environ["VIDEO_MODE"]` di-set
  oleh caller (main.py). Kalau tidak, `IS_LONG`, `W`, `H`, dan semua
  konstanta dependen akan salah.

═══════════════════════════════════════════════════════════════════════════
API OPSIONAL:
  Secara default, planner berjalan 100% OFFLINE (tanpa API).
  Untuk mengaktifkan API, set env `USE_API=1` dan sediakan Anthropic client.
  Kalau API gagal, otomatis fallback ke offline — pipeline TIDAK AKAN GAGAL.
═══════════════════════════════════════════════════════════════════════════
"""

import os
import re
import json
import math
import glob
import difflib
import subprocess
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

# ── Import dari modul v2 ──────────────────────────────────────────────────
import config as cfg
import smart_parser
from audio_utils import (
    sh,
    _concat_quote,
    probe,
    tighten_vo,
    measure,
    align_whisper,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. RE-EXPORT & ALIAS (kompatibilitas impor lama)
# ═══════════════════════════════════════════════════════════════════════════

IS_LONG = cfg.IS_LONG
W, H = cfg.W, cfg.H
FPS = cfg.FPS
IMG_EXT = cfg.IMG_EXT
VID_EXT = cfg.VID_EXT
STILL_DUR = cfg.STILL_DUR
STILL_KB_END = cfg.STILL_KB_END
DRIFT = cfg.DRIFT
CUT_FRAMES = cfg.CUT_FRAMES
GRAIN = cfg.GRAIN
FINAL_CRF = cfg.FINAL_CRF
FINAL_MAXRATE = cfg.FINAL_MAXRATE
OVERLAY_MAX = cfg.OVERLAY_MAX
OVERLAY_MIN_GAP = cfg.OVERLAY_MIN_GAP
SKIP_PREFIXES = cfg.SKIP_PREFIXES
ALLOWED_OVERLAY_TYPES = cfg.ALLOWED_OVERLAY_TYPES
TARGET_LUFS = cfg.TARGET_LUFS
THEMES = cfg.THEMES
DEFAULT_STYLE_NOTE = cfg.DEFAULT_STYLE_NOTE
SFX_VARIANTS = cfg.SFX_VARIANTS
SFX_DEFAULT_FOR_TYPE = cfg.SFX_DEFAULT_FOR_TYPE
RIG_ENABLED = cfg.RIG_ENABLED
RENDER_CONCURRENCY = cfg.RENDER_CONCURRENCY
REMOTION_DIR = cfg.REMOTION_DIR


# ═══════════════════════════════════════════════════════════════════════════
# 2. UTILITAS TEKS
# ═══════════════════════════════════════════════════════════════════════════

def norm(w: str) -> str:
    """Normalisasi kata untuk pencocokan: alfanumerik + lowercase."""
    return re.sub(r"[^\w]", "", w.lower())


def similar(a: str, b: str) -> bool:
    """Cek kemiripan kata (identik atau rasio ≥ 0.82)."""
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.82


def find_trigger(words: List[dict], trig: str, after: int = 0) -> Optional[int]:
    """Cari index kata pertama dalam `words` yang cocok dengan frasa `trig`."""
    if not trig:
        return None
    tk = [norm(x) for x in str(trig).split() if norm(x)]
    if not tk:
        return None
    for i in range(after, len(words) - len(tk) + 1):
        if all(similar(words[i + k]["n"], tk[k]) for k in range(len(tk))):
            return i
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 3. KONVERSI GAMBAR DIAM → KELIP
# ═══════════════════════════════════════════════════════════════════════════

def still_to_clip(src: str, out_p: str, dur: float = STILL_DUR) -> bool:
    """Ubah gambar diam menjadi klip mp4 sesuai orientasi, dengan zoom Ken Burns."""
    try:
        info = probe(src)
        iw, ih = int(info.get("w") or 0), int(info.get("h") or 0)
        if iw <= 0 or ih <= 0:
            return False
        max_fg_h = int(H * 0.88)
        sc = min(W / iw, max_fg_h / ih)
        fw = max(2, int(iw * sc) // 2 * 2)
        fh = max(2, int(ih * sc) // 2 * 2)
        k0 = 1.0 / STILL_KB_END
        grow = f"({k0:.5f}+(1-{k0:.5f})*t/{dur:.3f})"
        fc = (
            "[0:v]format=rgba,split=2[a][b];"
            f"[a]format=rgb24,scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=25:5,eq=brightness=-0.10[bg];"
            f"[b]scale={fw}:{fh}:flags=lanczos[fg0];"
            f"[fg0]scale=w='trunc({fw}*{grow}/2)*2':h='trunc({fh}*{grow}/2)*2'"
            f":eval=frame:flags=lanczos[fg];"
            "[bg][fg]overlay=x='(W-w)/2':y='(H-h)/2':format=auto,format=yuv420p[v]"
        )
        n = int(round(dur * FPS))
        sh([
            "ffmpeg", "-y", "-v", "error",
            "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", src,
            "-filter_complex", fc, "-map", "[v]",
            "-frames:v", str(n), "-r", str(FPS),
            "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-an", out_p,
        ])
        return os.path.exists(out_p) and os.path.getsize(out_p) > 1000
    except Exception as e:
        print(f"(!) Gagal konversi gambar {os.path.basename(src)}: {str(e)[:200]}")
        return False


def prepare_still_clips(clips: List[dict], work_dir: str) -> List[dict]:
    """Konversi semua klip gambar ke mp4 (cache di work_dir/stills)."""
    still_dir = os.path.join(work_dir, "stills")
    os.makedirs(still_dir, exist_ok=True)
    to_convert = []
    for c in clips:
        if not c.get("is_img"):
            continue
        out_p = os.path.join(still_dir, f"{c['key']}.mp4")
        cached = os.path.exists(out_p) and os.path.getsize(out_p) > 1000
        if cached:
            c.update(path=out_p, dur=STILL_DUR, is_img=False,
                     vertical=(not IS_LONG), still=True)
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
                    c_obj.update(path=out_path, dur=STILL_DUR, is_img=False,
                                 vertical=(not IS_LONG), still=True)
                    print("Gambar -> klip:", c_obj["name"])
                else:
                    c_obj["dur"] = STILL_DUR
    return clips


# ═══════════════════════════════════════════════════════════════════════════
# 4. MOTION / CUT
# ═══════════════════════════════════════════════════════════════════════════

def cut_kind(i: int) -> Optional[str]:
    """Hard-cut bersih pada 80% potongan; transisi halus tiap 5 shot sekali."""
    if i <= 0 or i % 5 != 0:
        return None
    return "zoom"


def motion_filter(fr: int, drift: float, kin, kout) -> str:
    """Bangun ekspresi FFmpeg zoompan untuk drift + kin/kout."""
    n = CUT_FRAMES
    terms: List[str] = []
    if drift:
        terms.append(f"{drift}*on/{max(fr - 1, 1)}")
    xs: List[str] = []
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


# ═══════════════════════════════════════════════════════════════════════════
# 5. BUILD CAPTIONS
# ═══════════════════════════════════════════════════════════════════════════

def build_captions(words: List[dict], units: List[List[int]],
                   keywords: List[str], dur: float) -> List[dict]:
    """Bangun cue subtitle word-level dari words/units/keywords."""
    kw_tok = [[norm(x) for x in k.split()] for k in keywords]
    kw_single = {w for kw in kw_tok for w in kw if len(w) >= 6}

    if IS_LONG:
        max_line_chars = cfg.CAPTION_MAX_LINE_CHARS_LONG
        max_half_chars = cfg.CAPTION_MAX_HALF_CHARS_LONG
    else:
        max_line_chars = cfg.CAPTION_MAX_LINE_CHARS_SHORT
        max_half_chars = cfg.CAPTION_MAX_HALF_CHARS_SHORT

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
        best = min(
            ((max(len(" ".join(x["w"] for x in ws[:i])),
                  len(" ".join(x["w"] for x in ws[i:]))), i)
             for i in range(2, n - 1)),
            key=lambda x: x[0],
        )
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

    cues: List[dict] = []
    for a, b in units:
        for g in split_groups(words[a:b]):
            fl = flags_for(g)
            idx = {id(w): f for w, f in zip(g, fl)}
            capl = [
                [{"w": w["w"], "kw": bool(idx[id(w)]),
                  "t0": w["t0"], "t1": w["t1"]} for w in ln]
                for ln in break2(g)
            ]
            cues.append({
                "s": max(g[0]["t0"] - 0.04, 0.0),
                "e": g[-1]["t1"] + 0.12,
                "cap": capl,
            })

    for i in range(1, len(cues)):
        if cues[i]["s"] < cues[i - 1]["e"] + 0.03:
            cues[i - 1]["e"] = max(cues[i]["s"] - 0.03, cues[i - 1]["s"] + 0.15)
    if cues:
        cues[-1]["e"] = min(cues[-1]["e"], dur)
    return [{"s": c["s"], "e": c["e"], "lines": c["cap"]} for c in cues]


# ═══════════════════════════════════════════════════════════════════════════
# 6. NORMALIZE OVERLAY
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_overlay_type(o: dict) -> dict:
    """Konversi defensif tipe lama ke tipe modern yang didukung Remotion."""
    t = str(o.get("type") or "").strip().lower()
    if t in ALLOWED_OVERLAY_TYPES:
        return o

    if t == "list":
        items = o.get("items") or []
        first_txt = str(
            items[0].get("text") if items and isinstance(items[0], dict) else ""
        ).strip()
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
    o.setdefault(
        "lines", [str(o.get("text") or o.get("label") or "SOROTAN").upper()[:24]]
    )
    return o


# ═══════════════════════════════════════════════════════════════════════════
# 7. LOCK OVERLAYS
# ═══════════════════════════════════════════════════════════════════════════

def lock_overlays(words: List[dict], overlays: List[dict],
                  hook_lines: List[str], cta_text: str, dur: float) -> List[dict]:
    """Kunci t0/t1 tiap overlay; buang bentrok; tag overlays _fixed selalu menang."""
    default_dur = {
        "roto_typo": 3.0 if IS_LONG else 2.5,
        "stat_counter": 3.0 if IS_LONG else 2.5,
        "kinetic_typo": 2.8 if IS_LONG else 2.3,
        "marker_quote": 3.2 if IS_LONG else 2.6,
        "floating_card": 3.4 if IS_LONG else 2.8,
        "draw_on": 2.8 if IS_LONG else 2.3,
        "camera_beat": 2.4 if IS_LONG else 2.0,
    }
    ovs: List[dict] = []

    extra: List[dict] = []
    if hook_lines:
        extra.append({
            "type": "kinetic_typo", "at": 0.15, "lines": hook_lines,
            "dur": 2.4 if IS_LONG else 2.0, "sfx": "hit", "_fixed": True,
        })
    if cta_text:
        extra.append({
            "type": "kinetic_typo", "at": max(0.0, dur - 2.8),
            "lines": [cta_text], "dur": 2.6, "sfx": "hit", "_fixed": True,
        })

    for o in extra + overlays:
        o = _normalize_overlay_type(json.loads(json.dumps(o)))
        if "at" in o:
            o["t0"] = (
                float(o["at"]) if float(o["at"]) >= 0
                else max(dur + float(o["at"]), 0.0)
            )
        else:
            i = find_trigger(words, o.get("trigger", ""))
            if i is None:
                continue
            o["t0"] = words[i]["t0"]
        if o["t0"] >= dur - 0.3:
            continue
        ovs.append(o)

    prio = {
        "roto_typo": 0, "stat_counter": 1, "floating_card": 2,
        "marker_quote": 3, "kinetic_typo": 4, "draw_on": 5, "camera_beat": 6,
    }

    def _span(o):
        return o["t0"], o["t0"] + o.get("dur", default_dur.get(o["type"], 2.6))

    def _clash(a, b):
        (a0, a1), (b0, b1) = _span(a), _span(b)
        return a0 < b1 + OVERLAY_MIN_GAP and b0 < a1 + OVERLAY_MIN_GAP

    kept = [o for o in ovs if o.get("_fixed")]
    for o in sorted((o for o in ovs if not o.get("_fixed")),
                    key=lambda o: (prio.get(o["type"], 4), o["t0"])):
        if len(kept) >= OVERLAY_MAX:
            print(f"[overlay] dilewati (kuota {OVERLAY_MAX}): {o['type']} @ {o['t0']:.1f}s")
            continue
        if any(_clash(o, k) for k in kept if not k.get("_fixed")):
            print(f"[overlay] dilewati (rapat < {OVERLAY_MIN_GAP:.0f}s): {o['type']} @ {o['t0']:.1f}s")
            continue
        kept.append(o)

    ovs = kept
    ovs.sort(key=lambda o: o["t0"])
    for k, o in enumerate(ovs):
        o.pop("_fixed", None)
        nxt = ovs[k + 1]["t0"] - 0.1 if k + 1 < len(ovs) else dur
        o["t1"] = max(
            min(o["t0"] + o.get("dur", default_dur.get(o["type"], 2.6)), nxt, dur),
            o["t0"] + 0.3,
        )
        for it in o.get("items", []):
            if "abs" in it:
                it["_t"] = max(0.0, it["abs"] - o["t0"])
    return ovs


# ═══════════════════════════════════════════════════════════════════════════
# 8. AUTO DIRECTOR
# ═══════════════════════════════════════════════════════════════════════════

def _extract_claude_text(resp) -> str:
    """Ambil teks balasan Claude (API mode)."""
    return "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()


def _parse_json_loose(txt: str) -> dict:
    """Parse JSON toleran terhadap pembungkus ```json ... ``` atau prefix/suffix."""
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
    a, b = txt.find("{"), txt.rfind("}")
    assert a >= 0 and b > a, f"Balasan tidak berisi JSON: {txt[:200]}"
    return json.loads(txt[a:b + 1])


def _clean_hook_line(s: str) -> str:
    return re.sub(r"[*]+", "", str(s or "")).strip().upper()


def _sanitize_plan(p: dict) -> dict:
    """Normalisasi plan (dipakai API mode opsional)."""
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
        if isinstance(ov, dict) and ov.get("type") in ALLOWED_OVERLAY_TYPES:
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
            if isinstance(o, dict) and o.get("type") in ALLOWED_OVERLAY_TYPES:
                if o.get("trigger") or o.get("at") is not None:
                    ovs.append(o)

    if not ovs and scenes:
        mid_idx = min(len(scenes) - 1, max(0, len(scenes) // 3))
        sc_fb = scenes[mid_idx]
        sc_fb["skor_penting"] = 5
        trig_fb = " ".join(
            re.sub(r"[^a-zA-Z0-9\s]", "", sc_fb["narasi"].lower()).split()[:4]
        )
        ov_fb = {
            "type": "kinetic_typo",
            "trigger": trig_fb or "fakta utama",
            "kicker": "SOROTAN",
            "lines": [
                " ".join(sc_fb["narasi"].split()[:3]).upper() or "FAKTA UTAMA"
            ],
            "alasan": "Sorotan utama otomatis",
        }
        sc_fb["overlay"] = ov_fb
        ovs.append(ov_fb)

    if not ovs:
        raise ValueError("Tidak ada overlay valid pada kalimat penting")

    raw_kws = [
        str(x).strip().upper() for x in (p.get("keywords") or []) if str(x).strip()
    ]
    merged_kws = list(dict.fromkeys(kw_pool + raw_kws))[:10]

    cta_q = str(p.get("cta_pertanyaan") or "").strip()
    if not cta_q and scenes and "?" in scenes[-1]["narasi"]:
        cta_q = scenes[-1]["narasi"]

    return {
        "theme": cfg.safe_theme(p.get("theme", "")),
        "topic": str(p.get("topic") or "video"),
        "hook_lines": [
            _clean_hook_line(x)
            for x in (p.get("hook_lines") or [])
            if _clean_hook_line(x)
        ][:3],
        "fakta_konkret": [
            str(x).strip()
            for x in (p.get("fakta_konkret") or [])
            if str(x).strip()
        ][:5],
        "cta_pertanyaan": cta_q,
        "keywords": merged_kws,
        "scenes": scenes,
        "overlays": ovs,
    }


def _call_claude_api(client, prompt: str, model: str) -> dict:
    """Panggil Claude API — hanya dipakai jika USE_API=1 (opt-in)."""
    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json_loose(_extract_claude_text(resp))


def run_auto_director(
    client,
    text: str,
    style_note: str,
    project_note: str,
    model: str = "claude-sonnet-4-6",
    parsed_tags: Optional[Dict[str, Any]] = None,
) -> dict:
    """Bangun plan dari teks narasi (offline default, API opt-in)."""
    if parsed_tags is None:
        parsed_tags = {}

    use_api = (
        client is not None
        and os.environ.get("USE_API", "").strip().lower() in ("1", "true", "yes", "on")
    )

    base_plan: Dict[str, Any]
    if use_api:
        max_ov = "12-18 item" if IS_LONG else "4-6 item"
        note_combined = style_note
        if project_note.strip():
            note_combined += f"\nCATATAN KHUSUS PROYEK INI: {project_note.strip()}"
        prompt = (
            f"Kamu adalah editor dokumenter & sutradara visual.\n"
            f"Susun scene PER KALIMAT dari transkrip berikut.\n\n"
            f"TRANSKRIP:\n{text}\n\n"
            f"CATATAN GAYA & PROYEK:\n{note_combined}\n\n"
            f"Batasan overlay total maksimal: {max_ov}.\n"
        )
        try:
            print("[Planner] Mode API (USE_API=1)...")
            raw_plan = _call_claude_api(client, prompt, model)
            base_plan = _sanitize_plan(raw_plan)
        except Exception as e:
            print(f"[Planner] API gagal, fallback offline: {str(e)[:200]}")
            base_plan = smart_parser.extract_highlights_offline(text)
    else:
        print("[Planner] Mode OFFLINE (smart_parser.extract_highlights_offline).")
        base_plan = smart_parser.extract_highlights_offline(text)

    merged = smart_parser.merge_with_tags(base_plan, parsed_tags)

    if not merged.get("scenes"):
        raise ValueError("Plan tidak memiliki scenes valid.")
    if not merged.get("overlays"):
        merged["overlays"] = _overlays_from_scenes(merged)
    return merged


def _overlays_from_scenes(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ambil semua overlay non-null dari plan['scenes']."""
    if plan.get("overlays"):
        return list(plan["overlays"])
    out: List[Dict[str, Any]] = []
    for sc in plan.get("scenes", []):
        ov = sc.get("overlay")
        if isinstance(ov, dict) and ov.get("type"):
            out.append(ov)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 9. SCAN KLIP LOKAL
# ═══════════════════════════════════════════════════════════════════════════

def scan_local_clips(proj_dir: str, work_dir: str, extract_frames: bool = True) -> List[dict]:
    """Scan gambar + video di proj_dir. Opsional ekstrak 4 frame per klip."""
    frames_root = os.path.join(work_dir, "frames")
    os.makedirs(frames_root, exist_ok=True)
    cands = [
        p for p in sorted(glob.glob(f"{proj_dir}/*"))
        if p.lower().endswith(VID_EXT + IMG_EXT)
        and not os.path.basename(p).lower().startswith(SKIP_PREFIXES)
    ]
    clips: List[dict] = []
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
                sh(["ffmpeg", "-y", "-v", "error", "-i", p,
                    "-vf", "scale=480:-2", "-q:v", "4", f"{d}/0001.jpg"])
            else:
                total = info.get("dur") or 0.0
                for n_i, frac in enumerate((0.1, 0.37, 0.63, 0.9), 1):
                    sh(["ffmpeg", "-y", "-v", "error",
                        "-ss", f"{total * frac:.2f}", "-i", p,
                        "-frames:v", "1", "-vf", "scale=480:-2",
                        "-q:v", "4", f"{d}/{n_i:04d}.jpg"], check=False)
        info["frames"] = sorted(glob.glob(f"{d}/*.jpg"))
        clips.append(info)
    return clips


# ═══════════════════════════════════════════════════════════════════════════
# 10. VISION LABEL (STUB)
# ═══════════════════════════════════════════════════════════════════════════

def _gemini_vision_label(frames: List[str], clip_name: str):
    """v2 default: vision labeling OFF. Return None → pakai fallback nama file."""
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 11. LOCAL BROLL MATCHER
# ═══════════════════════════════════════════════════════════════════════════

def _local_broll_matcher(kals: List[dict], klips: List[dict]) -> Dict[str, Any]:
    """
    Matcher klip offline — sequential ROUND-ROBIN atau keyword scoring.

    v2.1 FIX: mode sequential sekarang pakai round-robin (idx % n_c),
    bukan block assignment (int(idx * n_c / n_k)). Efek: kalimat berurutan
    mendapat klip BERBEDA, tidak ada duplikat 3× berturut-turut.
    """
    if not klips:
        return {"pilihan": []}

    seq_count = sum(
        1 for c in klips if re.match(r"^\d{2,3}_", c.get("nama_file", ""))
    )
    pilihan: List[dict] = []

    if seq_count >= max(3, int(len(klips) * 0.7)):
        klips_sorted = sorted(klips, key=lambda x: x.get("nama_file", ""))
        n_c = len(klips_sorted)
        for idx, k in enumerate(kals):
            # ── ROUND-ROBIN: idx % n_c ────────────────────────────────
            # 15 kalimat, 6 klip → klip 0,1,2,3,4,5,0,1,2,3,4,5,0,1,2
            # (bukan block: 0,0,0,1,1,1,2,2,2,3,3,3,4,4,4)
            c_idx = idx % n_c
            c = klips_sorted[c_idx]
            trig = " ".join(
                re.sub(r"[^a-zA-Z0-9\s]", "", k.get("kalimat", "").lower()).split()[:4]
            )
            pilihan.append({
                "no": k.get("no", idx + 1),
                "kalimat": k.get("kalimat", ""),
                "frasa_trigger": trig,
                "id_klip": c["id"],
                "alasan": f"Round-robin #{c_idx + 1:02d}",
            })
        return {"pilihan": pilihan}

    stop = {
        "yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada",
        "ini", "itu", "ada", "atau", "juga",
    }
    used_recent: List[str] = []
    for k in kals:
        q_words = set(
            re.findall(
                r"[a-z0-9]{3,}",
                (k.get("kalimat", "") + " " + k.get("keyword", "")).lower(),
            )
        ) - stop
        best_c, best_sc = klips[0], -1
        for c in klips:
            c_words = set(
                re.findall(
                    r"[a-z0-9]{3,}",
                    (c.get("nama_file", "") + " " + c.get("deskripsi_visual", "")).lower(),
                )
            ) - stop
            sc = (
                len(q_words & c_words) * 3
                - (2 if c.get("id") in used_recent[-2:] else 0)
            )
            if sc > best_sc:
                best_sc, best_c = sc, c
        used_recent.append(best_c["id"])
        trig = " ".join(
            re.sub(r"[^a-zA-Z0-9\s]", "", k.get("kalimat", "").lower()).split()[:4]
        )
        pilihan.append({
            "no": k.get("no", 1),
            "kalimat": k.get("kalimat", ""),
            "frasa_trigger": trig,
            "id_klip": best_c["id"],
            "alasan": f"Kecocokan kata kunci ({max(best_sc, 1)} poin)",
        })
    return {"pilihan": pilihan}


# ═══════════════════════════════════════════════════════════════════════════
# 12. AUTO BROLL
# ═══════════════════════════════════════════════════════════════════════════

def run_auto_broll(
    client,
    clips: List[dict],
    unit_texts: List[str],
    words: List[dict],
    proj_dir: str,
    model: str = "claude-sonnet-4-6",
    scenes: Optional[List[dict]] = None,
) -> Dict[str, str]:
    """Bangun peta kalimat → klip via matcher offline (default) atau API (opt-in)."""
    cache_p = os.path.join(proj_dir, "clip_labels.json")
    try:
        cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    except Exception:
        cache = {}

    dirty = False
    for c in clips:
        sz = os.path.getsize(c["path"])
        if (
            c["name"] in cache
            and cache[c["name"]].get("size") == sz
            and cache[c["name"]].get("deskripsi")
        ):
            print("Cache label:", c["name"], "->",
                  cache[c["name"]].get("deskripsi", "")[:60])
            continue
        try:
            out = _gemini_vision_label(c.get("frames") or [], c["name"])
            if out and out.get("deskripsi"):
                cache[c["name"]] = {"size": sz, **out}
                dirty = True
                print("Label baru:", c["name"], "->", out.get("deskripsi", "")[:60])
            else:
                clean_nm = re.sub(r"^\d+_", "", os.path.splitext(c["name"])[0]).replace("_", " ")
                cache[c["name"]] = {
                    "size": sz,
                    "deskripsi": clean_nm,
                    "tag": clean_nm.split(),
                    "ada_orang": True,
                    "wajah_jelas": False,
                }
                dirty = True
        except Exception as e:
            print(f"(!) Gagal label {c['name']}: {str(e)[:140]}")
            clean_nm = re.sub(r"^\d+_", "", os.path.splitext(c["name"])[0]).replace("_", " ")
            cache[c["name"]] = {
                "size": sz, "deskripsi": clean_nm, "tag": clean_nm.split(),
                "ada_orang": True, "wajah_jelas": False,
            }
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
        kal_list = [
            {"no": idx + 1, "kalimat": u, "visual_yang_dibutuhkan": u}
            for idx, u in enumerate(unit_texts)
        ]

    hints: Dict[str, str] = {}
    scene_pairs: List[dict] = []

    use_api = (
        client is not None
        and os.environ.get("USE_API", "").strip().lower() in ("1", "true", "yes", "on")
    )

    try:
        if use_api:
            match_prompt = (
                f"Pasangkan SETIAP kalimat dengan 1 klip.\n"
                f"DAFTAR KALIMAT:\n{json.dumps(kal_list, ensure_ascii=False)}\n\n"
                f"DAFTAR KLIP:\n{json.dumps(klip_list, ensure_ascii=False)}\n"
            )
            res_match = _call_claude_api(client, match_prompt, model)
        else:
            res_match = _local_broll_matcher(kal_list, klip_list)

        for p in res_match.get("pilihan", []):
            c = ids.get(p.get("id_klip"))
            if not c:
                continue
            kal_txt = str(p.get("kalimat") or "").strip()
            trig = str(p.get("frasa_trigger") or "").strip()
            if not trig and kal_txt:
                trig = " ".join(
                    re.sub(r"[^a-zA-Z0-9\s]", "", kal_txt.lower()).split()[:4]
                )
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


# ═══════════════════════════════════════════════════════════════════════════
# 13. APPLY EDIT COMMAND
# ═══════════════════════════════════════════════════════════════════════════

def apply_edit_command(state: dict, raw_cmd: str) -> dict:
    """Format: '/perintah argumen' atau '/perintah | argumen'."""
    m = re.match(r"\s*(/\w+)\s*(?:\|\s*)?(.*)$", raw_cmd or "", re.S)
    if not m:
        return state
    cmd, arg = m.group(1).lower(), m.group(2).strip()

    if cmd == "/hapus":
        nums: set = set()
        for tok in re.split(r"[,\s]+", arg):
            if "-" in tok:
                a, b = tok.split("-", 1)
                if a.isdigit() and b.isdigit():
                    nums.update(range(int(a), int(b) + 1))
            elif tok.isdigit():
                nums.add(int(tok))
        state["overlays"] = [
            o for i, o in enumerate(state.get("overlays", []), 1) if i not in nums
        ]

    elif cmd == "/tema":
        t = arg.lower()
        if t in THEMES:
            state["theme"] = t

    elif cmd == "/hook":
        state["hook_lines"] = (
            [] if arg == "-"
            else [h.strip().upper() for h in arg.split("|") if h.strip()]
        )

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
                print(f"(!) /klip: '{chunk}' dilewati, format 'frasa=klip' atau 'nomor=klip'.")
                continue
            k, v = [x.strip() for x in chunk.split("=", 1)]
            if not k or not v:
                continue
            matched_name = (
                next((nm for nm in usable if nm.lower() == v.lower()), None)
                or next((nm for nm in usable if v.lower() in nm.lower()), None)
            )
            if not matched_name:
                print(f"(!) /klip: nama klip '{v}' tidak cocok.")
                continue
            if k.isdigit():
                idx = int(k)
                if not items:
                    print(f"(!) /klip: indeks {idx} tidak bisa dipakai, pakai 'frasa=klip'.")
                    continue
                if not 1 <= idx <= len(items):
                    print(f"(!) /klip: indeks {idx} di luar rentang 1-{len(items)}.")
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


# ═══════════════════════════════════════════════════════════════════════════
# 14. SFX SINTETIS
# ═══════════════════════════════════════════════════════════════════════════

def _gen_sfx_hit(SR: int) -> np.ndarray:
    """Kick-drum + noise transient — untuk kinetic_typo & stat_counter."""
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    br = np.cumsum(rng.standard_normal(n))
    br = br - np.linspace(br[0], br[-1], n)
    br = br / (np.max(np.abs(br)) + 1e-9)
    nz = sosfilt(butter(2, 500, "low", fs=SR, output="sos"), br) * np.exp(-t / 0.05)
    hit = np.sin(2 * np.pi * 70 * t) * np.exp(-t / 0.06) * 0.8 + nz * 0.5
    peak = np.max(np.abs(hit)) + 1e-9
    return (hit / peak * 0.9 * (10 ** (cfg.SFX_GAIN_DB / 20))).astype(np.float32)


def _gen_sfx_whoosh(SR: int) -> np.ndarray:
    """High-pass noise sweep — untuk roto_typo, camera_beat, draw_on."""
    n = int(0.55 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(11)
    noise = rng.standard_normal(n).astype(np.float32)
    hp = sosfilt(butter(2, 1500, "high", fs=SR, output="sos"), noise).astype(np.float32)
    env = (np.sin(np.pi * t / t[-1]) ** 1.4).astype(np.float32)
    whoosh = hp * env
    peak = np.max(np.abs(whoosh)) + 1e-9
    return (whoosh / peak * 0.85 * (10 ** (cfg.SFX_GAIN_DB / 20))).astype(np.float32)


def _gen_sfx_pop(SR: int) -> np.ndarray:
    """Blip sine pendek — untuk marker_quote & floating_card."""
    n = int(0.15 * SR)
    t = np.arange(n) / SR
    pop = np.sin(2 * np.pi * 850 * t) * np.exp(-t / 0.018)
    click_n = int(0.003 * SR)
    if click_n > 0:
        pop[:click_n] += np.linspace(0.5, 0, click_n, dtype=np.float32)
    peak = np.max(np.abs(pop)) + 1e-9
    return (pop / peak * 0.9 * (10 ** (cfg.SFX_GAIN_DB / 20))).astype(np.float32)


def _build_sfx_stem(sfx_events: List[tuple], dur: float, SR: int) -> np.ndarray:
    """Bangun stem mono dari list (t_sec, variant)."""
    stem = np.zeros(int(dur * SR) + SR, dtype=np.float32)
    cache: Dict[str, np.ndarray] = {}
    for t_ev, variant in sfx_events:
        if variant not in cache:
            if variant == "whoosh":
                cache[variant] = _gen_sfx_whoosh(SR)
            elif variant == "pop":
                cache[variant] = _gen_sfx_pop(SR)
            else:
                cache[variant] = _gen_sfx_hit(SR)
        seg = cache[variant]
        st = int(t_ev * SR)
        if st < len(stem):
            end = min(st + len(seg), len(stem))
            stem[st:end] += seg[:end - st]
    return stem[:int(dur * SR)]


# ═══════════════════════════════════════════════════════════════════════════
# 15. CLEANUP HELPER
# ═══════════════════════════════════════════════════════════════════════════

def _safe_rm(path: str) -> None:
    """Hapus file/folder, abaikan error (untuk cleanup di GH Actions)."""
    try:
        if os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        print(f"[cleanup] gagal hapus {path}: {str(e)[:120]}")


# ═══════════════════════════════════════════════════════════════════════════
# 16. RENDER FINAL VIDEO
# ═══════════════════════════════════════════════════════════════════════════

def render_final_video(
    state: dict,
    proj_dir: str,
    work_dir: str,
    remo_dir: str = REMOTION_DIR,
) -> str:
    """Rakit video final: B-roll + overlay Remotion + subtitle + SFX + master audio."""
    os.makedirs(work_dir, exist_ok=True)

    vo_path = os.path.join(work_dir, "VO_padat.wav")
    if not os.path.exists(vo_path):
        audio_cands = sorted(
            glob.glob(f"{proj_dir}/*.mp3")
            + glob.glob(f"{proj_dir}/*.m4a")
            + glob.glob(f"{proj_dir}/*.wav")
        )
        assert audio_cands, f"File audio VO tidak ditemukan di {proj_dir}"
        vo_path = tighten_vo(audio_cands[0], work_dir)

    vo_info = probe(vo_path)
    dur = vo_info["dur"]
    assert dur > 1.0, "Durasi VO terlalu pendek."
    n_frames = round(dur * FPS)

    words = state["words"]
    units = state["units"]
    keywords = state.get("keywords", [])
    theme_name = cfg.safe_theme(state.get("theme", ""))
    topic = state.get("topic", "video")
    hook_lines = state.get("hook_lines", [])
    cta_text = state.get("cta_text", "")
    overlays_raw = state.get("overlays", [])
    clip_hints = state.get("clip_hints", {})
    excluded = {x.lower() for x in state.get("excluded_clips", [])}

    caps = build_captions(words, units, keywords, dur)
    ovs = lock_overlays(words, overlays_raw, hook_lines, cta_text, dur)

    sfx_events: List[tuple] = []
    for o in ovs:
        t0 = round(o["t0"], 2)
        variant = o.get("sfx") or SFX_DEFAULT_FOR_TYPE.get(o["type"], "hit")
        if variant not in SFX_VARIANTS:
            variant = "hit"
        sfx_events.append((t0, variant))
    sfx_events.sort()

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
    hint_t: List[tuple] = []
    for kw, clipname in clip_hints.items():
        i = find_trigger(words, kw)
        if i is not None:
            hint_t.append((words[i]["t0"], clipname))

    def overlap_ok(c, ss, d_seg, relax):
        return all(
            min(ss + d_seg, b) - max(ss, a) <= relax * d_seg
            for a, b in used[c["key"]]
        )

    def pick_shot(d_seg, t_start, t_end, rr):
        order = clips[rr % len(clips):] + clips[:rr % len(clips)]
        for t, nm in hint_t:
            if t_start <= t <= t_end:
                exact = [c for c in clips if c["name"].lower() == nm.lower()]
                partial = [
                    c for c in clips
                    if nm.lower() in c["name"].lower() and c not in exact
                ]
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

    shots: List[dict] = []
    for i in range(n_sh):
        fr = bounds[i + 1] - bounds[i]
        d_seg = fr / FPS
        c, ss, loop = pick_shot(d_seg, bounds[i] / FPS, bounds[i + 1] / FPS, i)
        used[c["key"]].append((ss, ss + d_seg))
        ptr[c["key"]] = ss + d_seg
        shots.append({
            "i": i, "clip": c, "ss": ss, "dur": d_seg,
            "frames": fr, "t": bounds[i] / FPS, "loop": loop,
        })

    shots_dir = os.path.join(work_dir, "shots")
    os.makedirs(shots_dir, exist_ok=True)

    phone_png = os.path.join(work_dir, "phone.png")
    if not IS_LONG and not os.path.exists(phone_png):
        sd = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sd).rounded_rectangle(
            [80, 427, 1000, 962], radius=56, fill=(0, 0, 0, 130)
        )
        l_ph = sd.filter(ImageFilter.GaussianBlur(18))
        d_ph = ImageDraw.Draw(l_ph)
        d_ph.rounded_rectangle(
            [80, 413, 1000, 948], radius=56, fill=(17, 17, 17, 255),
            outline=(64, 64, 64, 255), width=3,
        )
        hole = Image.new("L", (W, H), 255)
        ImageDraw.Draw(hole).rounded_rectangle(
            [100, 433, 980, 928], radius=26, fill=0
        )
        l_ph.putalpha(
            Image.fromarray(
                np.minimum(
                    np.array(l_ph.getchannel("A")),
                    np.array(hole),
                ).astype(np.uint8)
            )
        )
        l_ph.save(phone_png)

    scale_up_w = int(W * 1.5)
    scale_up_h = int(H * 1.5)

    def render_shot(s: dict) -> str:
        c, fr = s["clip"], s["frames"]
        out_p = os.path.join(shots_dir, f"s{s['i']:03d}.mp4")
        kin = cut_kind(s["i"] - 1) if s["i"] > 0 else None
        kout = cut_kind(s["i"]) if s["i"] < n_sh - 1 else None
        still = bool(c.get("still"))
        drift = 0.0 if still else DRIFT
        pre = ["-stream_loop", "-1"] if s["loop"] else []
        if c["is_img"]:
            pre = ["-loop", "1"]
        base = (
            ["ffmpeg", "-y", "-v", "error"]
            + pre
            + ["-ss", f"{s['ss']:.3f}", "-t", f"{s['dur'] + 0.4:.3f}",
               "-i", c["path"], "-an"]
        )
        out = ["-frames:v", str(fr), "-r", "30", "-c:v", "libx264",
               "-crf", "16", "-preset", "ultrafast", "-pix_fmt", "yuv420p", out_p]

        if IS_LONG:
            g = "" if still else f"{grade},"
            if c.get("vertical", False) and not still:
                fc = (
                    f"[0:v]fps=30,{grade},split=2[a][b];"
                    f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},boxblur=35:6,eq=brightness=-0.12[bg];"
                    f"[b]scale=-2:{H}:flags=lanczos[fg];"
                    f"[bg][fg]overlay=x='(W-w)/2':y='(H-h)/2',"
                    f"scale={scale_up_w}:{scale_up_h},"
                    f"{motion_filter(fr, drift, kin, kout)},format=yuv420p[v]"
                )
                sh(base + ["-filter_complex", fc, "-map", "[v]"] + out)
            else:
                vf = (
                    f"fps=30,{g}scale={scale_up_w}:{scale_up_h}"
                    f":force_original_aspect_ratio=increase,"
                    f"crop={scale_up_w}:{scale_up_h},"
                    f"{motion_filter(fr, drift, kin, kout)},format=yuv420p"
                )
                sh(base + ["-vf", vf] + out)
        else:
            if c.get("vertical", True):
                g = "" if still else f"{grade},"
                vf = (
                    f"fps=30,{g}scale=1620:2880:force_original_aspect_ratio=increase,"
                    f"crop=1620:2880,"
                    f"{motion_filter(fr, drift, kin, kout)},format=yuv420p"
                )
                sh(base + ["-vf", vf] + out)
            else:
                zp = (
                    f"zoompan=z='1+{DRIFT}*on/{max(fr - 1, 1)}'"
                    f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                    f":d=1:s=880x495:fps=30"
                )
                fc = (
                    f"[0:v]fps=30,{grade},split=2[a][b];"
                    f"[a]scale=1080:1920:force_original_aspect_ratio=increase,"
                    f"crop=1080:1920,boxblur=40:6,eq=brightness=-0.12[bg];"
                    f"[b]scale=880:495:force_original_aspect_ratio=increase,"
                    f"crop=880:495,{zp}[fg];"
                    f"[bg][fg]overlay=100:433[t];[t][1:v]overlay=0:0:format=auto,"
                    f"format=yuv420p[v0];"
                    f"[v0]scale=1620:2880,{motion_filter(fr, 0.0, kin, kout)},"
                    f"format=yuv420p[v]"
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
    sh(["ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", list_txt,
        "-c", "copy", base_mp4])

    # CLEANUP #1
    _safe_rm(shots_dir)
    _safe_rm(list_txt)

    try:
        from remotion_builder import prepare_remotion_project
    except ImportError as e:
        raise RuntimeError(f"Gagal import remotion_builder: {e}")

    prepare_remotion_project(remo_dir, is_long=IS_LONG)
    os.makedirs(f"{remo_dir}/out", exist_ok=True)
    os.makedirs(f"{remo_dir}/public", exist_ok=True)

    _hex = lambda t: "#%02X%02X%02X" % tuple(t)
    _t = THEMES[theme_name]
    t_props = {
        "hex": {
            "green": _hex(_t["g"]), "dark": _hex(_t["d"]),
            "mid": _hex(_t["m"]), "cream": _hex(_t["c"]),
            "orange": _hex(_t["a"]), "black": _hex(_t["k"]),
        },
        "vars": {
            "--g": "%d,%d,%d" % _t["g"], "--d": "%d,%d,%d" % _t["d"],
            "--a": "%d,%d,%d" % _t["a"], "--c": "%d,%d,%d" % _t["c"],
            "--v": "%d,%d,%d" % _t["v"],
        },
    }

    glass_src = None
    blur_p = f"{remo_dir}/public/base_blur.mp4"
    try:
        bw, bh = (480, 270) if IS_LONG else (270, 480)
        sh([
            "ffmpeg", "-y", "-v", "error", "-i", base_mp4,
            "-vf", (
                f"scale={bw}:{bh}:flags=area,gblur=sigma=2.2,"
                f"scale={W}:{H}:flags=bicubic,eq=brightness=-0.04"
            ),
            "-an", "-c:v", "libx264", "-crf", "24", "-preset", "veryfast",
            "-g", "15", "-pix_fmt", "yuv420p", blur_p,
        ])
        glass_src = "base_blur.mp4"
    except Exception as e:
        print(f"[peringatan] Latar kaca dilewati: {str(e)[:200]}")

    conc = str(RENDER_CONCURRENCY)

    roto_tmp_files: List[str] = []
    try:
        from roto_helper import extract_roto_layer
    except ImportError:
        extract_roto_layer = None

    if extract_roto_layer:
        for idx_ov, o in enumerate(ovs):
            if o.get("type") == "roto_typo":
                seg_dur = max(0.4, o["t1"] - o["t0"])
                seg_mp4 = os.path.join(work_dir, f"roto_in_{idx_ov}.mp4")
                roto_name = f"roto_{idx_ov}.webm"
                roto_out = os.path.join(remo_dir, "public", roto_name)
                roto_tmp_files.append(seg_mp4)
                try:
                    sh([
                        "ffmpeg", "-y", "-v", "error",
                        "-ss", f"{o['t0']:.3f}", "-t", f"{seg_dur:.3f}",
                        "-i", base_mp4, "-an",
                        "-c:v", "libx264", "-crf", "18",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        seg_mp4,
                    ])
                    if extract_roto_layer(seg_mp4, roto_out, fps=FPS):
                        o["rotoSrc"] = roto_name
                        print(f"[roto] Layer subjek aktif: {roto_name}")
                except Exception as e:
                    print(f"[roto] Gagal overlay #{idx_ov + 1}: {str(e)[:120]}")

    def render_overlay(glass):
        props = {
            "overlays": ovs,
            "fps": FPS,
            "durationInFrames": n_frames,
            "images": [],
            "captions": caps,
            "theme": t_props,
            "glassSrc": glass,
            "applyGlobalRig": RIG_ENABLED,
        }
        with open(f"{remo_dir}/props.json", "w") as fh:
            json.dump(props, fh, sort_keys=True)

        ov_file, codec = f"{remo_dir}/out/overlay.webm", "vp9"
        try:
            sh([
                "npx", "remotion", "render", "src/index.ts", "Overlay",
                ov_file, "--props=props.json", "--codec=vp9",
                "--pixel-format=yuva420p", "--image-format=png",
                "--crf=18", "--overwrite", f"--concurrency={conc}",
            ], cwd=remo_dir)
        except Exception as e:
            print(f"[peringatan] VP9 gagal ({str(e)[-300:]}); beralih ke ProRes 4444...")
            codec = "prores"
            ov_file = f"{remo_dir}/out/overlay.mov"
            sh([
                "npx", "remotion", "render", "src/index.ts", "Overlay",
                ov_file, "--props=props.json", "--codec=prores",
                "--prores-profile=4444", "--pixel-format=yuva444p10le",
                "--image-format=png", "--overwrite",
                f"--concurrency={conc}",
            ], cwd=remo_dir)
        return ov_file, codec

    try:
        overlay_file, used_codec = render_overlay(glass_src)
    except Exception as e:
        if glass_src is None:
            raise
        print(
            f"[peringatan] Render dgn latar kaca gagal ({str(e)[-300:]}); "
            f"mengulang tanpa latar kaca..."
        )
        overlay_file, used_codec = render_overlay(None)

    for p in roto_tmp_files:
        _safe_rm(p)

    SR = 48000
    stem = _build_sfx_stem(sfx_events, dur, SR)
    stem_wav = os.path.join(work_dir, "SFX_stem.wav")
    wavfile.write(stem_wav, SR, np.stack([stem, stem], 1).astype(np.float32))

    mix_wav = os.path.join(work_dir, "mix.wav")
    pan = (
        "pan=stereo|c0=c0|c1=c0"
        if vo_info.get("ch", 1) == 1
        else "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"
    )
    gain = 0.0
    for _ in range(cfg.LOUDNESS_MAX_ITER):
        fc = (
            f"[0:a]aresample=48000,afade=t=in:d=0.01,"
            f"acompressor=threshold=0.09:ratio=3:attack=4:release=60,"
            f"volume={gain:.2f}dB,"
            f"alimiter=limit=0.63:attack=2:release=30:level=0,{pan}[v];"
            f"[v][1:a]amix=inputs=2:normalize=0:duration=first,"
            f"alimiter=limit=0.7:level=0[m]"
        )
        sh([
            "ffmpeg", "-y", "-v", "error",
            "-i", vo_path, "-i", stem_wav,
            "-filter_complex", fc, "-map", "[m]",
            "-ar", "48000", "-ac", "2", "-t", f"{dur:.4f}", mix_wav,
        ])
        lufs, _ = measure(mix_wav)
        if not math.isfinite(lufs) or abs(lufs - TARGET_LUFS) < cfg.LOUDNESS_TOLERANCE:
            break
        gain = max(-30.0, min(30.0, gain + (TARGET_LUFS - lufs)))

    slug = re.sub(r"[^a-z0-9]+", "_", str(topic).lower()).strip("_")[:40] or "video"
    prefix = "Long" if IS_LONG else "Short"
    out_name = f"{prefix}_{slug}.mp4"
    final_mp4 = os.path.join(work_dir, out_name)

    fc_final = (
        "[1:v]format=rgba[ov];"
        "[0:v][ov]overlay=0:0:format=auto,format=yuv420p,"
        "vignette=angle=PI/4.5"
        + (f",noise=alls={GRAIN}:allf=t" if GRAIN else "")
        + "[v]"
    )
    ov_in = (
        ["-c:v", "libvpx-vp9", "-i", overlay_file]
        if used_codec == "vp9"
        else ["-i", overlay_file]
    )
    sh(
        ["ffmpeg", "-y", "-v", "error", "-i", base_mp4]
        + ov_in
        + [
            "-i", mix_wav, "-filter_complex", fc_final,
            "-map", "[v]", "-map", "2:a",
            "-frames:v", str(n_frames), "-shortest",
            "-c:v", "libx264", "-crf", str(FINAL_CRF),
            "-maxrate", FINAL_MAXRATE, "-bufsize", "20M",
            "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", final_mp4,
        ]
    )

    try:
        _safe_rm(base_mp4)
        _safe_rm(blur_p)
        _safe_rm(mix_wav)
        _safe_rm(stem_wav)
        _safe_rm(overlay_file)
        _safe_rm(os.path.join(work_dir, "stills"))
        _safe_rm(os.path.join(work_dir, "frames"))
        _safe_rm(os.path.join(work_dir, "phone.png"))
    except Exception as e:
        print(f"[cleanup] ringan gagal: {str(e)[:120]}")

    print(f"[render] selesai: {final_mp4} ({os.path.getsize(final_mp4) / (1024 * 1024):.1f} MB)")
    return final_mp4


__all__ = [
    "sh", "_concat_quote", "probe", "tighten_vo", "measure", "align_whisper",
    "DEFAULT_STYLE_NOTE", "THEMES", "ALLOWED_OVERLAY_TYPES",
    "run_auto_director",
    "scan_local_clips",
    "run_auto_broll",
    "apply_edit_command",
    "render_final_video",
    "norm", "similar", "find_trigger",
    "still_to_clip", "prepare_still_clips",
    "cut_kind", "motion_filter",
    "build_captions",
    "lock_overlays", "_normalize_overlay_type",
    "_safe_rm",
]