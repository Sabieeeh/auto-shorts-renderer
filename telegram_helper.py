"""
telegram_helper.py — Kirim pesan & kartu approval ke Telegram.
═══════════════════════════════════════════════════════════════════════════
FUNGSI PUBLIK:
  - send_message(chat_id, text, reply_markup, parse_mode)
  - send_message_long(chat_id, text, reply_markup, parse_mode)
  - send_approval_card(chat_id, state)

KONTRAK CALLBACK (WAJIB SINKRON DENGAN cloudflare_worker.js):
  approve:{folder}   → triggerWorkflow mode=render
  replan:{folder}    → triggerWorkflow mode=plan
  cancel:{folder}    → hanya kirim pesan batal
═══════════════════════════════════════════════════════════════════════════
"""

import os
import json
import html
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


def _api_call(token: str, method: str, payload: dict) -> dict:
    """Panggil Telegram Bot API. Return dict; tidak pernah raise."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[Telegram Warning] {method} HTTP {e.code}: {err_body[:300]}")
        return {"ok": False, "error": err_body}
    except Exception as e:
        print(f"[Telegram Warning] {method} gagal: {e}")
        return {"ok": False, "error": str(e)}


def _resolve_token(prefer_long: bool = False) -> str:
    """Resolve token Telegram (Bot A default, Bot B jika prefer_long)."""
    if prefer_long:
        long_token = os.environ.get("TELEGRAM_TOKEN_LONG", "").strip()
        if long_token:
            return long_token
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def _send_impl(
    chat_id: str,
    text: str,
    reply_markup: Optional[dict],
    parse_mode: str,
    token: str,
) -> None:
    """Internal — kirim pesan dengan chunking otomatis."""
    if not token or not chat_id:
        print("[Telegram] Token atau Chat ID kosong, pesan dilewati.")
        return

    chunks: List[str] = []
    if len(text) <= 3800:
        chunks = [text]
    else:
        lines = text.split("\n")
        cur = ""
        for ln in lines:
            if len(cur) + len(ln) + 1 > 3800:
                chunks.append(cur)
                cur = ln
            else:
                cur = (cur + "\n" + ln) if cur else ln
        if cur:
            chunks.append(cur)

    for idx, part in enumerate(chunks):
        payload: Dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": part,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup and idx == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        _api_call(token, "sendMessage", payload)


def send_message(
    chat_id: str,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> None:
    """Kirim pesan via Bot A (TELEGRAM_BOT_TOKEN)."""
    token = _resolve_token(prefer_long=False)
    _send_impl(chat_id, text, reply_markup, parse_mode, token)


def send_message_long(
    chat_id: str,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> None:
    """Kirim pesan via Bot B (TELEGRAM_TOKEN_LONG) — fallback ke Bot A."""
    token = _resolve_token(prefer_long=True)
    _send_impl(chat_id, text, reply_markup, parse_mode, token)


def _ringkas_ov(o: dict) -> str:
    """Ringkas 1 objek overlay menjadi teks pendek untuk kartu preview."""
    t = o.get("type")
    if t == "roto_typo":
        return " / ".join(o.get("lines", [])) + " (di belakang subjek)"
    if t == "stat_counter":
        return f"{o.get('prefix', '')}{o.get('value', '')}{o.get('suffix', '')} — {o.get('label', '')}"
    if t == "draw_on":
        return f"[{o.get('shape', 'circle')}] {o.get('label', '')}"
    if t == "kinetic_typo":
        k = o.get("kicker", "")
        ls = " / ".join(o.get("lines", []))
        return f"{k + ': ' if k else ''}{ls}"
    if t == "marker_quote":
        return f"{o.get('badge', '')} | {o.get('text', '')}"
    if t == "floating_card":
        badge = o.get("badge", "")
        value = o.get("value", "")
        sub = o.get("label", "")
        parts = [p for p in (badge, value, sub) if p]
        return " | ".join(parts)
    if t == "camera_beat":
        return o.get("label", "")
    return str(o)[:60]


def send_approval_card(chat_id: str, state: dict) -> None:
    """Kirim ringkasan rencana + tombol inline approval."""
    folder = html.escape(state.get("folder_name", "-"))
    theme = html.escape(state.get("theme", "bumi"))
    topic = html.escape(state.get("topic", "-"))
    hook = html.escape(" / ".join(state.get("hook_lines", [])) or "(tanpa hook)")
    kws = html.escape(", ".join(state.get("keywords", [])) or "-")
    cta = html.escape(state.get("cta_text") or state.get("cta_pertanyaan") or "(tanpa CTA)")
    dur = float(state.get("vo_dur", 0.0) or 0.0)

    plan_mode = state.get("plan_mode", "SHORT")
    if plan_mode not in ("SHORT", "LONG"):
        plan_mode = "SHORT"
    plan_source = state.get("plan_source", "auto")
    if plan_source not in ("markup", "auto"):
        plan_source = "auto"
    markup_tags = state.get("markup_tags_found", []) or []

    overlays = state.get("overlays", [])
    hints = state.get("clip_hints", {})
    usable = state.get("usable_names", [])
    fakta = state.get("fakta_konkret", [])
    scene_pairs = state.get("scene_pairs", [])

    fakta_block = ""
    if fakta:
        f_lines = [f"• {html.escape(str(x))}" for x in fakta[:4]]
        fakta_block = "\n<b>📌 Fakta Konkret:</b>\n" + "\n".join(f_lines) + "\n"

    source_label = "Markup (tag manual)" if plan_source == "markup" else "Auto (offline mesin)"
    source_emoji = "🏷️" if plan_source == "markup" else "🤖"
    tags_line = ""
    if markup_tags:
        tags_line = f"\n   <i>Tag ditemukan:</i> <code>{html.escape(', '.join(markup_tags))}</code>"

    pair_lines: List[str] = []
    if scene_pairs:
        for sp in scene_pairs[:12]:
            no = sp.get("no", 0)
            kal = html.escape(str(sp.get("kalimat", ""))[:85])
            klip = html.escape(str(sp.get("klip", "")))
            pair_lines.append(f"<code>[{no:02d}]</code> <i>\"{kal}\"</i>\n     ↳ 🎬 <code>{klip}</code>")
    else:
        for i, (fr, fn) in enumerate(list(hints.items())[:12], 1):
            pair_lines.append(
                f"<code>[{i:02d}]</code> <i>\"{html.escape(fr)}\"</i> → 🎬 <code>{html.escape(fn)}</code>"
            )
    if len(scene_pairs) > 12:
        pair_lines.append(f"<i>… dan {len(scene_pairs) - 12} adegan lainnya</i>")

    ov_lines: List[str] = []
    for i, o in enumerate(overlays[:15], 1):
        trig = html.escape(o.get("trigger", ""))
        desc = html.escape(_ringkas_ov(o))
        alasan = html.escape(o.get("alasan", ""))
        from_tag = " 🏷️" if o.get("_from_tag") else ""
        extra = f" <i>({alasan})</i>" if alasan else ""
        ov_lines.append(
            f"<code>[{i:02d}]</code> <b>{o.get('type')}</b>{from_tag} @ "
            f"<i>\"{trig}\"</i> → {desc}{extra}"
        )
    if len(overlays) > 15:
        ov_lines.append(f"<i>… dan {len(overlays) - 15} overlay lainnya</i>")

    msg = (
        f"🎬 <b>RENCANA VIDEO SIAP DITINJAU</b>\n"
        f"📁 <b>Folder:</b> <code>{folder}</code>\n"
        f"🎬 <b>Mode:</b> <b>{plan_mode}</b> ({dur:.1f} dtk)\n"
        f"{source_emoji} <b>Sumber Plan:</b> {source_label}{tags_line}\n"
        f"🎨 <b>Tema:</b> <code>{theme}</code> | <b>Topik:</b> {topic}\n"
        f"🪝 <b>Hook:</b> {hook}\n"
        f"💬 <b>CTA Komentar:</b> {cta}\n"
        f"🔍 <b>Keyword Visual:</b> {kws}\n"
        f"{fakta_block}\n"
        f"🎞 <b>Pasangan Kalimat → Klip ({len(scene_pairs) or len(hints)} adegan):</b>\n"
        + ("\n".join(pair_lines) if pair_lines else "• (belum ada pasangan kalimat)")
        + f"\n\n✨ <b>Overlay Momen Penting ({len(overlays)} titik):</b>\n"
        + ("\n".join(ov_lines) if ov_lines else "• (tanpa overlay)")
        + f"\n\n📦 <b>Stok Klip ({len(usable)}):</b> "
        f"<code>{html.escape(', '.join(usable[:8]))}{'…' if len(usable) > 8 else ''}</code>\n\n"
        f"✏️ <b>Koreksi cepat (kirim ke chat):</b>\n"
        f"• <code>/tema {folder} | api</code> — ganti tema\n"
        f"• <code>/hook {folder} | BARIS 1 | BARIS 2</code> — ganti hook\n"
        f"• <code>/cta {folder} | TEKS</code> — ganti CTA\n"
        f"• <code>/hapus {folder} | 2, 4-6</code> — hapus overlay nomor\n"
        f"• <code>/klip {folder} | 1=nama_file.mp4</code> — ganti klip tertentu\n\n"
        f"⚡️ <i>Atau langsung tekan tombol di bawah.</i>"
    )

    kb = {
        "inline_keyboard": [
            [
                {"text": f"✅ Setuju & Render {state.get('folder_name', '')}",
                 "callback_data": f"approve:{state.get('folder_name', '')}"},
            ],
            [
                {"text": "🔄 Susun Ulang",
                 "callback_data": f"replan:{state.get('folder_name', '')}"},
                {"text": "❌ Batal",
                 "callback_data": f"cancel:{state.get('folder_name', '')}"},
            ],
        ]
    }

    send_message(chat_id, msg, reply_markup=kb)


__all__ = [
    "send_message",
    "send_message_long",
    "send_approval_card",
]