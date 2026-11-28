"""
smart_parser.py — Parser tag markup & heuristik penentuan highlight offline.

═══════════════════════════════════════════════════════════════════════════
FUNGSI PUBLIK:
  - parse_markup_tags(raw_text) → dict
  - strip_tags(raw_text) → str
  - build_overlay_from_tag(tag_type, args, anchor) → dict | None
  - auto_sentence_boundaries(text) → List[str]
  - extract_highlights_offline(text) → dict
  - merge_with_tags(auto_plan, parsed_tags) → dict

TAG YANG DIDUKUNG:
  [ROTO: L1 | L2]              → roto_typo
  [KINETIC: KICKER | L1 | L2]  → kinetic_typo
  [QUOTE: BADGE | TEXT]        → marker_quote
  [CHART: ANGKA | LABEL]       → stat_counter
  [CARD: BADGE | VALUE | SUB]  → floating_card
  [DRAW: circle|underline|arrow | LABEL] → draw_on
  [MAP: LOKASI]                → camera_beat
  [HOOK: L1 | L2]              → state.hook_lines
  [CTA: TEXT]                  → state.cta_text

KONTRAK IMPOR:
  Modul ini meng-import `config` di top-level. Konstanta config
  (IS_LONG, OVERLAY_MAX, THEMES, dll) akan BENAR hanya jika caller
  sudah mengeset os.environ["VIDEO_MODE"] SEBELUM import modul ini.
  Di pipeline produksi, `main.py` selalu melakukannya lebih dulu.

  Jika untuk unit test, set env dulu:
      os.environ["VIDEO_MODE"] = "SHORT"  # atau "LONG"
      import smart_parser
═══════════════════════════════════════════════════════════════════════════
"""

import re
from typing import Any, Dict, List, Optional

import config as cfg


# ═══════════════════════════════════════════════════════════════════════════
# 1. REGEX & KONSTANTA INTERNAL
# ═══════════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"\[\s*([A-Z_]+)\s*(?::\s*([^\]]*))?\s*\]")
"""
Cocokkan `[TAG]` atau `[TAG: args]`.
  - group(1) = nama tag (uppercase)
  - group(2) = args (boleh kosong / None)
"""

_TAG_STRIP_RE = re.compile(r"\[[^\]]*\]")
"""Match semua bracket apa pun, untuk strip_tags (termasuk yang tidak dikenal)."""

_WS_RE = re.compile(r"\s+")


# ═══════════════════════════════════════════════════════════════════════════
# 2. UTILITAS TEKS DASAR
# ═══════════════════════════════════════════════════════════════════════════

def _pick_anchor_phrase(text_after: str) -> str:
    """
    Ambil 3–5 kata bermakna SETELAH tag sebagai anchor trigger waktu.

    Aturan:
      - Skip stopword di awal (agar anchor dimulai kata bermakna)
      - Ambil hingga TAG_ANCHOR_MAX_WORDS kata (default 5)
      - Kalau hasil kosong → return "" (tag akan dibuang)
    """
    clean = _TAG_STRIP_RE.sub(" ", str(text_after or ""))
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9]+", clean)
    if not tokens:
        return ""

    i = 0
    while i < len(tokens) and tokens[i].lower() in cfg.TAG_ANCHOR_STOPWORDS:
        i += 1

    picked = tokens[i:i + cfg.TAG_ANCHOR_MAX_WORDS]
    return " ".join(picked)


def strip_tags(raw_text: str) -> str:
    """
    Bersihkan SEMUA tag `[...]` (termasuk yang tidak dikenal) + karakter `*`.
    """
    cleaned = _TAG_STRIP_RE.sub(" ", str(raw_text or ""))
    cleaned = cleaned.replace("*", "")
    return _WS_RE.sub(" ", cleaned).strip()


def auto_sentence_boundaries(text: str) -> List[str]:
    """
    Pecah teks menjadi kalimat UTUH (tidak pernah potong di tengah kata).
    """
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[.!?;…])\s+|\n+", raw)
    return [p.strip() for p in parts if len(p.strip()) > 4]


# ═══════════════════════════════════════════════════════════════════════════
# 3. KONVERSI TAG → OVERLAY OBJEK
# ═══════════════════════════════════════════════════════════════════════════

def build_overlay_from_tag(
    tag_type: str,
    args_raw: str,
    anchor: str,
) -> Optional[Dict[str, Any]]:
    """
    Ubah 1 tag (mis. ROTO) menjadi objek overlay yang Remotion pahami.
    """
    tag_type = (tag_type or "").strip().upper()
    if tag_type not in cfg.TAG_TO_OVERLAY_TYPE:
        return None
    if not anchor or not anchor.strip():
        return None

    parts = [p.strip() for p in str(args_raw or "").split("|")]

    def _get(i: int, default: str = "") -> str:
        return parts[i] if i < len(parts) and parts[i] else default

    ov_type = cfg.TAG_TO_OVERLAY_TYPE[tag_type]
    ov: Dict[str, Any] = {
        "type": ov_type,
        "trigger": anchor,
        "_from_tag": True,
        "_tag_type": tag_type,
        "alasan": f"[TAG] {tag_type}",
    }

    if tag_type == "ROTO":
        l1 = _get(0).upper()[:18]
        l2 = _get(1).upper()[:18]
        if not l1:
            return None
        ov["lines"] = [l1] + ([l2] if l2 else [])
        ov["sfx"] = "whoosh"

    elif tag_type == "KINETIC":
        kicker = _get(0).upper()[:20]
        l1 = _get(1).upper()[:24]
        l2 = _get(2).upper()[:24]
        if not (l1 or l2):
            return None
        if kicker:
            ov["kicker"] = kicker
        ov["lines"] = [x for x in (l1, l2) if x]
        ov["sfx"] = "hit"

    elif tag_type == "QUOTE":
        badge = _get(0, "KUTIPAN").upper()[:20]
        text = _get(1).upper()[:60]
        if not text:
            return None
        ov["badge"] = badge
        ov["text"] = text
        ov["sfx"] = "pop"

    elif tag_type == "CHART":
        raw_num = _get(0, "0")
        digits = re.sub(r"\D", "", raw_num)
        num = int(digits) if digits else 0
        if num <= 0:
            return None
        label = _get(1, "DATA").upper()[:24]
        ov["prefix"] = "Rp " if re.search(r"\brp\.?\b", args_raw, re.I) else ""
        ov["suffix"] = "%" if "%" in args_raw else ""
        ov["value"] = num
        ov["label"] = label
        ov["sfx"] = "hit"

    elif tag_type == "CARD":
        badge = _get(0, "DATA").upper()[:20]
        value = _get(1).upper()[:30]
        sub = _get(2).upper()[:30]
        if not value:
            return None
        ov["badge"] = badge
        ov["value"] = value
        ov["label"] = sub
        ov["sfx"] = "pop"

    elif tag_type == "DRAW":
        shape = _get(0, "circle").lower()
        if shape not in ("circle", "underline", "arrow"):
            shape = "circle"
        label = _get(1).upper()[:24]
        ov["shape"] = shape
        ov["label"] = label
        ov["sfx"] = "whoosh"

    elif tag_type == "MAP":
        loc = _get(0).upper()[:26]
        if not loc:
            return None
        ov["label"] = loc
        ov["kicker"] = "LOKASI"
        ov["sfx"] = "whoosh"

    return ov


# ═══════════════════════════════════════════════════════════════════════════
# 4. PARSE SEMUA TAG DI TRANSKRIP
# ═══════════════════════════════════════════════════════════════════════════

def parse_markup_tags(raw_text: str) -> Dict[str, Any]:
    """
    Ekstrak semua tag markup dari transkrip mentah.
    """
    raw = str(raw_text or "")
    overlays: List[Dict[str, Any]] = []
    hook_lines: List[str] = []
    cta_text: str = ""
    tags_found_set: set = set()
    tag_count = 0

    for m in _TAG_RE.finditer(raw):
        tag_type = (m.group(1) or "").strip().upper()
        if tag_type not in cfg.MARKUP_TAGS:
            continue

        args_raw = (m.group(2) or "").strip()
        after_text = raw[m.end():m.end() + 240]
        anchor = _pick_anchor_phrase(after_text)

        if tag_type == "HOOK":
            parts = [p.strip() for p in args_raw.split("|") if p.strip()]
            if parts:
                hook_lines = [p.upper() for p in parts[:3]]
                tags_found_set.add("HOOK")
                tag_count += 1
            continue

        if tag_type == "CTA":
            if args_raw.strip():
                cta_text = args_raw.strip().upper()
                tags_found_set.add("CTA")
                tag_count += 1
            continue

        ov = build_overlay_from_tag(tag_type, args_raw, anchor)
        if ov:
            overlays.append(ov)
            tags_found_set.add(tag_type)
            tag_count += 1

    return {
        "has_markup": len(overlays) > 0 or bool(hook_lines) or bool(cta_text),
        "hook_lines": hook_lines,
        "cta_text": cta_text,
        "overlays": overlays,
        "tags_found": sorted(tags_found_set),
        "tag_count": tag_count,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. HEURISTIK AUTO-MODE (TANPA TAG)
# ═══════════════════════════════════════════════════════════════════════════

def _build_auto_overlay_for_sentence(
    sent: str,
    words_s: List[str],
    nums: List[str],
    is_babak: bool,
    ov_count: int,
) -> Dict[str, Any]:
    """
    Bangun objek overlay otomatis untuk 1 kalimat penting.
    """
    trig = " ".join(
        re.sub(r"[^a-zA-Z0-9\s]", "", sent.lower()).split()[:4]
    ) or "fakta utama"
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

    if nums and not re.search(
        r"\b(pukul|jam|november|oktober|desember|januari|februari|maret|april"
        r"|mei|juni|juli|agustus|september|202\d|201\d)\b",
        sent,
        re.I,
    ):
        raw_digits = re.sub(r"\D", "", nums[0])
        raw_n = int(raw_digits) if raw_digits else 0
        if 1 <= raw_n <= 999_999_999:
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

    cycle = ["roto_typo", "kinetic_typo", "marker_quote", "camera_beat"]
    chosen = cycle[ov_count % len(cycle)]
    line1 = " ".join(w.upper() for w in words_s[:2]) or "SOROTAN"
    line2 = " ".join(w.upper() for w in words_s[2:4]) or key_phrase

    if chosen == "roto_typo":
        return {
            "type": "roto_typo",
            "trigger": trig,
            "lines": [line1[:18], line2[:18]],
            "alasan": "Sorotan subjek & tipografi",
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


def extract_highlights_offline(text: str) -> Dict[str, Any]:
    """
    Mode Cerdas OFFLINE (tanpa API) — pengganti `_local_smart_plan` Mode 2 lama.
    """
    clean = strip_tags(text)
    sents = auto_sentence_boundaries(clean)
    if not sents:
        sents = [clean or "Dokumentasi lapangan"]

    total_words = sum(len(s.split()) for s in sents) or 1
    est_dur = total_words / 2.6

    if cfg.IS_LONG:
        max_ov_cap = min(cfg.OVERLAY_MAX - 2, max(4, int(est_dur / 32.0)))
    else:
        max_ov_cap = min(cfg.OVERLAY_MAX - 2, 5)
    min_sent_gap = max(3, len(sents) // max(max_ov_cap, 1))

    safe_start = min(3, max(1, len(sents) // 12))
    safe_end = max(safe_start + 1, len(sents) - min(3, max(1, len(sents) // 12)))

    scenes: List[Dict[str, Any]] = []
    facts: List[str] = []
    kws: List[str] = []
    last_ov_idx = -min_sent_gap
    ov_count = 0

    for idx, s in enumerate(sents, 1):
        words_s = [
            w for w in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", s)
            if w.lower() not in cfg.TAG_ANCHOR_STOPWORDS
        ]
        kw = " ".join(words_s[:3]).lower() or "dokumentasi lapangan"
        kws.append(kw.upper())

        nums = re.findall(r"\b\d{1,3}(?:[.,]\d{3})*\b", s)
        is_babak = bool(
            re.match(r"^(?:babak|bagian)\s+(?:\d+|[ivx]+)\b", s.strip(), re.I)
        )
        has_entity = bool(
            re.search(
                r"\b(baduy|pikukuh|kanekes|kebakaran|mitigasi|leuit|lumbung"
                r"|rumbia|jaro|puun|adat|larangan|bantuan|hutan|desa|warga"
                r"|pemerintah|tanah|ekonomi|budaya)\b",
                s,
                re.I,
            )
        )

        skor = 5 if (is_babak or nums or "?" in s) else (
            4 if (has_entity and len(words_s) >= 3) else 2
        )
        if nums or is_babak:
            facts.append(s[:110])

        ov = None
        in_safe_zone = safe_start < idx <= safe_end or len(sents) <= 5
        if (
            skor >= 4
            and in_safe_zone
            and (idx - last_ov_idx) >= min_sent_gap
            and ov_count < max_ov_cap
        ):
            ov = _build_auto_overlay_for_sentence(s, words_s, nums, is_babak, ov_count)
            last_ov_idx = idx
            ov_count += 1

        scenes.append({
            "narasi": s,
            "visual_literal": f"Rekaman nyata: {kw}",
            "keyword_cari": kw,
            "skor_penting": skor,
            "overlay": ov,
        })

    if not any(sc["overlay"] for sc in scenes) and scenes:
        target_i = min(len(scenes) - 1, max(0, len(scenes) // 3))
        sc_t = scenes[target_i]
        words_t = [
            w for w in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", sc_t["narasi"])
            if w.lower() not in cfg.TAG_ANCHOR_STOPWORDS
        ]
        sc_t["skor_penting"] = 5
        sc_t["overlay"] = _build_auto_overlay_for_sentence(
            sc_t["narasi"], words_t, [], False, 0
        )

    first_w = [
        w.upper() for w in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", sents[0])
        if w.lower() not in cfg.TAG_ANCHOR_STOPWORDS
    ][:4]
    hook = [
        " ".join(first_w[:2]) or "DOKUMENTER BUDAYA",
        " ".join(first_w[2:4]) or "FAKTA LAPANGAN",
    ]
    cta = next(
        (s for s in reversed(sents) if "?" in s),
        "Bagaimana pendapat Anda tentang hal ini?",
    )

    return {
        "theme": cfg.DEFAULT_THEME,
        "topic": " ".join(first_w[:3]) or "DOKUMENTER",
        "hook_lines": hook,
        "fakta_konkret": facts[:4] or sents[:3],
        "cta_pertanyaan": cta,
        "keywords": list(dict.fromkeys(kws))[:8],
        "scenes": scenes,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 6. MERGE: TAG PRIORITAS > AUTO
# ═══════════════════════════════════════════════════════════════════════════

def merge_with_tags(
    auto_plan: Dict[str, Any],
    parsed_tags: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Gabungkan plan otomatis (dari extract_highlights_offline) dengan hasil tag.
    """
    if not parsed_tags.get("has_markup"):
        result = dict(auto_plan)
        result.setdefault("overlays", _overlays_from_scenes(auto_plan))
        result["plan_source"] = "auto"
        result["markup_tags_found"] = []
        return result

    merged = dict(auto_plan)

    if parsed_tags.get("hook_lines"):
        merged["hook_lines"] = list(parsed_tags["hook_lines"])

    if parsed_tags.get("cta_text"):
        merged["cta_pertanyaan"] = parsed_tags["cta_text"]

    seen_triggers: set = set()
    tag_ovs: List[Dict[str, Any]] = []
    for ov in parsed_tags.get("overlays", []):
        trig = (ov.get("trigger") or "").strip().lower()
        if not trig or trig in seen_triggers:
            continue
        seen_triggers.add(trig)
        ov_copy = dict(ov)
        ov_copy["_fixed"] = True
        tag_ovs.append(ov_copy)

    auto_ovs = _overlays_from_scenes(merged)
    kept_auto = [
        ov for ov in auto_ovs
        if (ov.get("trigger") or "").strip().lower() not in seen_triggers
    ]

    merged["overlays"] = tag_ovs + kept_auto
    merged["plan_source"] = "markup" if tag_ovs else "auto"
    merged["markup_tags_found"] = list(parsed_tags.get("tags_found", []))
    return merged


def _overlays_from_scenes(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ambil semua overlay non-null dari plan['scenes'] (kompatibel dengan pipeline)."""
    if plan.get("overlays"):
        return list(plan["overlays"])
    ovs: List[Dict[str, Any]] = []
    for sc in plan.get("scenes", []):
        ov = sc.get("overlay")
        if isinstance(ov, dict) and ov.get("type"):
            ovs.append(ov)
    return ovs


__all__ = [
    "parse_markup_tags",
    "strip_tags",
    "build_overlay_from_tag",
    "auto_sentence_boundaries",
    "extract_highlights_offline",
    "merge_with_tags",
]