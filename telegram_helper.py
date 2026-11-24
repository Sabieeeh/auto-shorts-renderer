import os
import json
import html
import urllib.request
import urllib.error


def _api_call(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
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


def send_message(chat_id: str, text: str, reply_markup: dict = None, parse_mode: str = "HTML"):
    """Kirim pesan Telegram (otomatis dipecah jika lebih dari 3800 karakter)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not chat_id:
        print("[Telegram] Token atau Chat ID kosong, pesan dilewati.")
        return

    chunks = []
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
        payload = {
            "chat_id": str(chat_id),
            "text": part,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup and idx == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        _api_call(token, "sendMessage", payload)


def _ringkas_ov(o: dict) -> str:
    t = o.get("type")
    if t == "big":
        return " / ".join(o.get("lines", []))
    if t == "lower":
        return f"{o.get('badge', '')} | {o.get('text', '')}"
    if t == "list":
        items = ", ".join(i.get("text", "") for i in o.get("items", []))
        return f"{o.get('title', '')}: {items}"
    if t == "icon":
        return f"[{o.get('icon', '')}] {o.get('label', '')}"
    if t == "qmark":
        return o.get("label", "")
    if t == "map":
        return f"{o.get('label', '')} ({o.get('sub', '')}) pin={o.get('pin')}"
    return str(o)[:60]


def send_approval_card(chat_id: str, state: dict):
    """Kirim ringkasan rencana overlay & B-roll beserta tombol Setuju di Telegram."""
    folder = html.escape(state["folder_name"])
    theme = html.escape(state["theme"])
    topic = html.escape(state["topic"])
    hook = html.escape(" / ".join(state.get("hook_lines", [])) or "(tanpa hook)")
    kws = html.escape(", ".join(state.get("keywords", [])))
    cta = html.escape(state.get("cta_text") or "(tanpa CTA)")
    dur = state.get("vo_dur", 0.0)
    overlays = state.get("overlays", [])
    hints = state.get("clip_hints", {})
    usable = state.get("usable_names", [])

    ov_lines = []
    for i, o in enumerate(overlays, 1):
        trig = html.escape(o.get("trigger", ""))
        desc = html.escape(_ringkas_ov(o))
        ov_lines.append(f"<code>[{i:02d}]</code> <b>{o.get('type')}</b> @ <i>\"{trig}\"</i> → {desc}")

    clip_lines = []
    for i, (fr, fn) in enumerate(list(hints.items())[:15], 1):
        clip_lines.append(f"<code>[{i:02d}]</code> <i>\"{html.escape(fr)}\"</i> → <code>{html.escape(fn)}</code>")

    msg = (
        f"🎬 <b>RENCANA VIDEO SIAP DISETUJUI</b>\n"
        f"📁 Folder: <code>{folder}</code> | ⏱ VO: <b>{dur:.1f} dtk</b>\n"
        f"🎨 Tema: <b>{theme}</b> | Aset: <b>{len(usable)} klip</b>\n"
        f"📌 Topik: {topic}\n"
        f"🪝 Hook: <b>{hook}</b>\n"
        f"🔑 Keyword: <code>{kws}</code>\n"
        f"📣 CTA: {cta}\n\n"
        f"<b>✨ DAFTAR OVERLAY ({len(overlays)} item):</b>\n"
        + ("\n".join(ov_lines) if ov_lines else "(kosong)")
        + "\n\n<b>🎞 PASANGAN B-ROLL UTAMA:</b>\n"
        + ("\n".join(clip_lines) if clip_lines else "(otomatis bergantian)")
        + "\n\n<b>✏️ Edit Cepat (opsional sebelum Setuju):</b>\n"
        f"• <code>/hapus {folder} | 3, 7</code>\n"
        f"• <code>/tema {folder} | hutan</code>\n"
        f"• <code>/hook {folder} | BARIS 1 | BARIS 2</code>\n"
        f"• <code>/cta {folder} | FOLLOW UNTUK FAKTA LAINNYA</code>\n"
        f"• <code>/klip {folder} | 1=namafile.mp4</code>"
    )

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Setuju & Render Final", "callback_data": f"approve:{state['folder_name']}"},
            ],
            [
                {"text": "🔄 Acak Ulang Rencana", "callback_data": f"replan:{state['folder_name']}"},
                {"text": "❌ Batal", "callback_data": f"cancel:{state['folder_name']}"},
            ],
        ]
    }
    send_message(chat_id, msg, reply_markup=keyboard)
