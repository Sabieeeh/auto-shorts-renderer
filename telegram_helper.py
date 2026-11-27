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
    if t == "camera_beat":
        return o.get("label", "")
    return str(o)[:60]


def send_approval_card(chat_id: str, state: dict):
    """Kirim ringkasan rencana berbasis bukti visual (Kalimat -> Klip) & overlay momen penting."""
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
    fakta = state.get("fakta_konkret", [])
    scene_pairs = state.get("scene_pairs", [])

    fakta_block = ""
    if fakta:
        f_lines = [f"• {html.escape(x)}" for x in fakta[:4]]
        fakta_block = "\n<b>📌 Fakta Konkret:</b>\n" + "\n".join(f_lines) + "\n"

    pair_lines = []
    if scene_pairs:
        for sp in scene_pairs[:18]:
            no = sp.get("no", 0)
            kal = html.escape(str(sp.get("kalimat", ""))[:85])
            klip = html.escape(str(sp.get("klip", "")))
            pair_lines.append(f"<code>[{no:02d}]</code> <i>\"{kal}\"</i>\n     ↳ 🎬 <code>{klip}</code>")
    else:
        for i, (fr, fn) in enumerate(list(hints.items())[:18], 1):
            pair_lines.append(f"<code>[{i:02d}]</code> <i>\"{html.escape(fr)}\"</i> → 🎬 <code>{html.escape(fn)}</code>")

    ov_lines = []
    for i, o in enumerate(overlays, 1):
        trig = html.escape(o.get("trigger", ""))
        desc = html.escape(_ringkas_ov(o))
        alasan = html.escape(o.get("alasan", ""))
        extra = f" <i>({alasan})</i>" if alasan else ""
        ov_lines.append(f"<code>[{i:02d}]</code> <b>{o.get('type')}</b> @ <i>\"{trig}\"</i> → {desc}{extra}")

    msg = (
        f"🎬 <b>RENCANA VIDEO SIAP DITINJAU</b>\n"
        f"📁 <b>Folder:</b> <code>{folder}</code> ({dur:.1f} dtk)\n"
        f"🎨 <b>Tema:</b> <code>{theme}</code> | <b>Topik:</b> {topic}\n"
        f"🪝 <b>Hook:</b> {hook}\n"
        f"💬 <b>CTA Komentar:</b> {cta}\n"
        f"🔍 <b>Keyword Visual:</b> {kws}\n"
        f"{fakta_block}\n"
        f"🎞 <b>Pasangan Kalimat → Klip ({len(pair_lines)} adegan):</b>\n"
        + ("\n".join(pair_lines) if pair_lines else "• (belum ada pasangan kalimat)")
        + f"\n\n✨ <b>Overlay Momen Penting ({len(overlays)} titik):</b>\n"
        + ("\n".join(ov_lines) if ov_lines else "• (tanpa overlay)")
        + f"\n\n📦 <b>Stok Klip ({len(usable)}):</b> <code>{html.escape(', '.join(usable[:12]))}</code>\n\n"
        f"✏️ <b>Koreksi cepat:</b>\n"
        f"• Ganti klip kalimat: <code>/klip 2 | nama_file.mp4</code>\n"
        f"• Ganti hook: <code>/hook BARIS 1 | BARIS 2</code>\n"
        f"• Hapus overlay: <code>/hapus 2</code>\n"
        f"• Lanjut render: <code>/setuju {folder}</code>"
    )

    kb = {
        "inline_keyboard": [
            [{"text": f"✅ Setuju & Render {state['folder_name']}", "callback_data": f"/setuju {state['folder_name']}"}]
        ]
    }
    send_message(chat_id, msg, reply_markup=kb)
