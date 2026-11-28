"""
smart_parser.py — Parser tag markup & heuristik penentuan highlight offline.
═══════════════════════════════════════════════════════════════════════════
PERUBAHAN v2.2:
  - Skip nama bulan, hari, tahun dari proper noun (NOVEMBER, KAMIS, 2026)
  - Prioritaskan kata impactful dari konteks cerita (kebakaran, api, dst)
  - Hindari kicker repetisi di kinetic_typo (kicker == line1 → kosong)
═══════════════════════════════════════════════════════════════════════════
"""

import re
from typing import Any, Dict, List, Optional

import config as cfg


_TAG_RE = re.compile(r"\[\s*([A-Z_]+)\s*(?::\s*([^\]]*))?\s*\]")
_TAG_STRIP_RE = re.compile(r"\[[^\]]*\]")
_WS_RE = re.compile(r"\s+")

_GENERIC_WORDS = {
    "zaman", "ketika", "orang", "banyak", "sangat", "bisa", "juga",
    "akan", "masih", "sudah", "telah", "satu", "para", "namun",
    "tetapi", "seperti", "jika", "kalau", "maka", "kini", "saat",
    "ini", "itu", "begitu", "demikian", "memang", "hanya",
    "mereka", "warga", "dalam", "juga", "atau", "oleh", "adalah",
    "malah", "justru", "tetap", "kembali", "lagi", "sekali",
    "sebagian", "besar", "kecil", "hari", "malam", "pagi", "siang",
    "sebelum", "sesudah", "setelah", "kemudian", "lalu", "akhirnya",
    "sekitar", "hampir", "lebih", "kurang", "antara", "melalui",
}

_DATE_WORDS = {
    "januari", "februari", "maret", "april", "mei", "juni", "juli",
    "agustus", "september", "oktober", "november", "desember",
    "senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu",
    "tahun", "bulan", "minggu", "hari", "tanggal",
}

_HOOK_VERBS = {
    "menolak", "melawan", "memilih", "bertahan", "berjuang",
    "menentang", "berhenti", "meminta", "menyerahkan", "menjaga",
    "mengusir", "menutup", "berubah", "hilang", "kehilangan",
    "mempertahankan", "meninggalkan", "mendapatkan", "menuntut",
    "memutuskan", "menetapkan", "membangun", "terbakar", "melalap",
    "memusnahkan", "menghancurkan", "memadamkan", "menyelamatkan",
    "melanda", "menimpa", "meluluhlantakkan", "meratakan",
}

_HOOK_NOUNS = {
    "internet", "sinyal", "ponsel", "modernisasi", "tradisi",
    "adat", "dilema", "konflik", "krisis", "wabah", "kebakaran",
    "banjir", "hutan", "tanah", "budaya", "perubahan",
    "wisatawan", "turis", "pandemi", "virus", "bencana",
    "api", "rumbia", "kampung", "bambu", "asap", "bara",
    "dapur", "rumah", "keluarga", "warga", "petaka", "musibah",
    "tragedi", "korban", "pengungsi", "kehilangan",
}

_HOOK_ADJ = {
    "bebas", "terisolasi", "terpencil", "sunyi", "sepi",
    "modern", "kuno", "ketinggalan", "maju", "dilematis",
    "hilang", "lestari", "rusak", "terancam", "terbakar",
    "hangus", "musnah", "porak", "poranda", "luluh", "luluhlantak",
}


def _truncate_words(text: str, max_chars: int) -> str:
    words = str(text or "").split()
    if not words:
        return ""
    result = words[0]
    for w in words[1:]:
        if len(result) + 1 + len(w) > max_chars:
            break
        result += " " + w
    return result


def _pick_anchor_phrase(text_after: str) -> str:
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
    cleaned = _TAG_STRIP_RE.sub(" ", str(raw_text or ""))
    cleaned = cleaned.replace("*", "")
    return _WS_RE.sub(" ", cleaned).strip()


def auto_sentence_boundaries(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[.!?;…])\s+|\n+", raw)
    return [p.strip() for p in parts if len(p.strip()) > 4]


def _is_meaningful_word(w: str) -> bool:
    lw = w.lower()
    return (
        lw not in cfg.TAG_ANCHOR_STOPWORDS
        and lw not in _GENERIC_WORDS
        and lw not in _DATE_WORDS
    )


def _generate_smart_hook(sents: List[str], clean_text: str) -> List[str]:
    first5 = " ".join(sents[:5]) if sents else clean_text[:400]

    proper = re.findall(
        r"(?<!^)(?<![.!?;]\s)(?<!\n)\b([A-Z][a-z]{3,})\b", first5
    )
    proper = list(dict.fromkeys(
        w.upper() for w in proper
        if _is_meaningful_word(w)
    ))

    tokens = [
        w.upper() for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", first5)
        if _is_meaningful_word(w)
    ]
    uniq = list(dict.fromkeys(tokens))

    noun_choices = [w for w in uniq if w.lower() in _HOOK_NOUNS]
    verb_choices = [w for w in uniq if w.lower() in _HOOK_VERBS]
    adj_choices = [w for w in uniq if w.lower() in _HOOK_ADJ]
    other_uniq = [w for w in uniq if w not in noun_choices
                  and w not in verb_choices and w not in adj_choices]

    if proper:
        line1 = proper[0]
    elif noun_choices:
        line1 = noun_choices[0]
    elif verb_choices:
        line1 = verb_choices[0]
    elif other_uniq:
        line1 = " ".join(other_uniq[:2])
    else:
        line1 = "DOKUMENTER"

    pool = [w for w in uniq if w != line1]
    pool_noun = [w for w in pool if w.lower() in _HOOK_NOUNS]
    pool_verb = [w for w in pool if w.lower() in _HOOK_VERBS]
    pool_adj = [w for w in pool if w.lower() in _HOOK_ADJ]

    line2_words: List[str] = []
    if pool_adj and pool_noun:
        line2_words = [pool_adj[0], pool_noun[0]]
    elif pool_noun and len(pool_noun) >= 2:
        line2_words = pool_noun[:2]
    elif pool_noun and pool_verb:
        line2_words = [pool_verb[0], pool_noun[0]]
    elif pool_verb and pool_adj:
        line2_words = [pool_adj[0], pool_verb[0]]
    elif pool_noun:
        line2_words = [pool_noun[0]]
    elif pool_verb:
        line2_words = [pool_verb[0]]

    if not line2_words:
        all_tokens = re.findall(r"[A-Za-zÀ-ÿ]{4,}", clean_text)
        for w in all_tokens:
            lw, uw = w.lower(), w.upper()
            if uw == line1 or not _is_meaningful_word(w):
                continue
            if lw in _HOOK_NOUNS or lw in _HOOK_VERBS or lw in _HOOK_ADJ:
                line2_words.append(uw)
                if len(line2_words) >= 2:
                    break

    if not line2_words:
        line2_words = other_uniq[:2] if other_uniq else ["FAKTA LAPANGAN"]

    line2 = " ".join(line2_words[:2]) if line2_words else "FAKTA LAPANGAN"
    return [line1, line2]


def _generate_smart_topic(sents: List[str], hook_lines: List[str]) -> str:
    first5 = " ".join(sents[:5]) if sents else ""

    tokens = [
        w.upper() for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", first5)
        if _is_meaningful_word(w)
    ]
    uniq = list(dict.fromkeys(tokens))

    proper = re.findall(
        r"(?<!^)(?<![.!?;]\s)(?<!\n)\b([A-Z][a-z]{3,})\b", first5
    )
    proper = list(dict.fromkeys(
        w.upper() for w in proper if _is_meaningful_word(w)
    ))
    noun_choices = [w for w in uniq if w.lower() in _HOOK_NOUNS]

    if proper and noun_choices:
        return f"{noun_choices[0]} {proper[0]}"
    if proper:
        return proper[0]
    if noun_choices:
        return " ".join(noun_choices[:2])
    if hook_lines and hook_lines[0]:
        return hook_lines[0]
    return " ".join(uniq[:3]) if uniq else "DOKUMENTER"


def build_overlay_from_tag(tag_type: str, args_raw: str, anchor: str) -> Optional[Dict[str, Any]]:
    tag_type = (tag_type or "").strip().upper()
    if tag_type not in cfg.TAG_TO_OVERLAY_TYPE or not anchor:
        return None

    parts = [p.strip() for p in str(args_raw or "").split("|")]
    def _get(i: int, default: str = "") -> str:
        return parts[i] if i < len(parts) and parts[i] else default

    ov_type = cfg.TAG_TO_OVERLAY_TYPE[tag_type]
    ov: Dict[str, Any] = {
        "type": ov_type, "trigger": anchor,
        "_from_tag": True, "_tag_type": tag_type,
        "alasan": f"[TAG] {tag_type}",
    }

    if tag_type == "ROTO":
        l1 = _truncate_words(_get(0).upper(), 18)
        l2 = _truncate_words(_get(1).upper(), 18)
        if not l1: return None
        ov["lines"] = [l1] + ([l2] if l2 else [])
        ov["sfx"] = "whoosh"

    elif tag_type == "KINETIC":
        kicker = _truncate_words(_get(0).upper(), 20)
        l1 = _truncate_words(_get(1).upper(), 24)
        l2 = _truncate_words(_get(2).upper(), 24)
        if not (l1 or l2): return None
        if kicker and kicker != l1:
            ov["kicker"] = kicker
        ov["lines"] = [x for x in (l1, l2) if x]
        ov["sfx"] = "hit"

    elif tag_type == "QUOTE":
        badge = _truncate_words(_get(0, "KUTIPAN").upper(), 20)
        text = _truncate_words(_get(1).upper(), 60)
        if not text: return None
        ov["badge"] = badge
        ov["text"] = text
        ov["sfx"] = "pop"

    elif tag_type == "CHART":
        raw_num = _get(0, "0")
        digits = re.sub(r"\D", "", raw_num)
        num = int(digits) if digits else 0
        if num <= 0: return None
        label = _truncate_words(_get(1, "DATA").upper(), 24)
        ov["prefix"] = "Rp " if re.search(r"\brp\.?\b", args_raw, re.I) else ""
        ov["suffix"] = "%" if "%" in args_raw else ""
        ov["value"] = num
        ov["label"] = label
        ov["sfx"] = "hit"

    elif tag_type == "CARD":
        badge = _truncate_words(_get(0, "DATA").upper(), 20)
        value = _truncate_words(_get(1).upper(), 30)
        sub = _truncate_words(_get(2).upper(), 30)
        if not value: return None
        ov["badge"] = badge
        ov["value"] = value
        ov["label"] = sub
        ov["sfx"] = "pop"

    elif tag_type == "DRAW":
        shape = _get(0, "circle").lower()
        if shape not in ("circle", "underline", "arrow"): shape = "circle"
        label = _truncate_words(_get(1).upper(), 24)
        ov["shape"] = shape
        ov["label"] = label
        ov["sfx"] = "whoosh"

    elif tag_type == "MAP":
        loc = _truncate_words(_get(0).upper(), 26)
        if not loc: return None
        ov["label"] = loc
        ov["kicker"] = "LOKASI"
        ov["sfx"] = "whoosh"

    return ov


def parse_markup_tags(raw_text: str) -> Dict[str, Any]:
    raw = str(raw_text or "")
    overlays: List[Dict[str, Any]] = []
    hook_lines: List[str] = []
    cta_text: str = ""
    tags_found_set: set = set()
    tag_count = 0

    for m in _TAG_RE.finditer(raw):
        tag_type = (m.group(1) or "").strip().upper()
        if tag_type not in cfg.MARKUP_TAGS: continue

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
        "hook_lines": hook_lines, "cta_text": cta_text,
        "overlays": overlays, "tags_found": sorted(tags_found_set),
        "tag_count": tag_count,
    }


def _build_auto_overlay_for_sentence(
    sent: str, words_s: List[str], nums: List[str],
    is_babak: bool, ov_count: int,
) -> Dict[str, Any]:
    trig = " ".join(
        re.sub(r"[^a-zA-Z0-9\s]", "", sent.lower()).split()[:4]
    ) or "fakta utama"
    key_phrase = " ".join(w.upper() for w in words_s[:3]) or "FAKTA LAPANGAN"

    if is_babak:
        parts_b = [p.strip() for p in re.split(r"[:.\-–]", sent, maxsplit=1)]
        b_title = _truncate_words(parts_b[0].upper(), 28)
        raw_sub = parts_b[1] if len(parts_b) > 1 and parts_b[1] else key_phrase
        b_sub = _truncate_words(raw_sub.upper(), 42)
        return {
            "type": "roto_typo" if ov_count % 2 == 0 else "marker_quote",
            "trigger": trig, "kicker": b_title, "badge": b_title,
            "text": b_sub, "lines": [b_title, _truncate_words(b_sub, 22)],
            "alasan": "Penanda babak dokumenter",
        }

    if nums and not re.search(
        r"\b(pukul|jam|november|oktober|desember|januari|februari|maret|april"
        r"|mei|juni|juli|agustus|september|202\d|201\d)\b",
        sent, re.I,
    ):
        raw_digits = re.sub(r"\D", "", nums[0])
        raw_n = int(raw_digits) if raw_digits else 0
        if 1 <= raw_n <= 999_999_999:
            lbl = _truncate_words(
                " ".join(w.upper() for w in words_s[:2]) or "DATA FAKTA", 24
            )
            prefix = "Rp " if re.search(r"\brp\.?\b", sent, re.I) else ""
            suffix = "%" if "%" in sent else ""
            return {
                "type": "stat_counter", "trigger": trig,
                "prefix": prefix, "value": raw_n, "suffix": suffix,
                "label": lbl, "alasan": "Statistik atau angka konkret",
            }

    cycle = ["roto_typo", "kinetic_typo", "marker_quote", "camera_beat"]
    chosen = cycle[ov_count % len(cycle)]
    line1 = _truncate_words(
        " ".join(w.upper() for w in words_s[:2]) or "SOROTAN", 18
    )
    line2 = _truncate_words(
        " ".join(w.upper() for w in words_s[2:4]) or key_phrase, 18
    )

    if chosen == "roto_typo":
        return {
            "type": "roto_typo", "trigger": trig,
            "lines": [line1, line2],
            "alasan": "Sorotan subjek & tipografi",
        }
    if chosen == "camera_beat":
        return {
            "type": "camera_beat", "trigger": trig,
            "label": _truncate_words(key_phrase, 26),
            "alasan": "Momen observasi lapangan",
        }
    if chosen == "marker_quote":
        quote_text = _truncate_words(
            " ".join(sent.split()[:7]), 46
        ).upper()
        return {
            "type": "marker_quote", "trigger": trig,
            "badge": "FAKTA LAPANGAN", "text": quote_text,
            "alasan": "Kutipan fakta penting",
        }
    kicker_val = line1 if line1 != line2 else "SOROTAN"
    return {
        "type": "kinetic_typo", "trigger": trig,
        "kicker": _truncate_words(kicker_val, 20),
        "lines": [line1, line2],
        "alasan": "Penekanan poin utama",
    }


def extract_highlights_offline(text: str) -> Dict[str, Any]:
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
                r"|pemerintah|tanah|ekonomi|budaya|api)\b",
                s, re.I,
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
            skor >= 4 and in_safe_zone
            and (idx - last_ov_idx) >= min_sent_gap
            and ov_count < max_ov_cap
        ):
            ov = _build_auto_overlay_for_sentence(s, words_s, nums, is_babak, ov_count)
            last_ov_idx = idx
            ov_count += 1

        scenes.append({
            "narasi": s, "visual_literal": f"Rekaman nyata: {kw}",
            "keyword_cari": kw, "skor_penting": skor, "overlay": ov,
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

    hook = _generate_smart_hook(sents, clean)
    topic = _generate_smart_topic(sents, hook)

    cta = next(
        (s for s in reversed(sents) if "?" in s),
        "Bagaimana pendapat Anda tentang hal ini?",
    )

    return {
        "theme": cfg.DEFAULT_THEME, "topic": topic, "hook_lines": hook,
        "fakta_konkret": facts[:4] or sents[:3], "cta_pertanyaan": cta,
        "keywords": list(dict.fromkeys(kws))[:8], "scenes": scenes,
    }


def merge_with_tags(auto_plan: Dict[str, Any], parsed_tags: Dict[str, Any]) -> Dict[str, Any]:
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
        if not trig or trig in seen_triggers: continue
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
    if plan.get("overlays"):
        return list(plan["overlays"])
    ovs: List[Dict[str, Any]] = []
    for sc in plan.get("scenes", []):
        ov = sc.get("overlay")
        if isinstance(ov, dict) and ov.get("type"):
            ovs.append(ov)
    return ovs