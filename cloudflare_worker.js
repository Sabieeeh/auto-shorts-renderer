/**
 * Cloudflare Worker — Telegram Webhook → GitHub Actions Bridge
 * auto-shorts-renderer v2
 *
 * ═══════════════════════════════════════════════════════════════════════
 * ENV VARS WAJIB (Cloudflare Dashboard → Settings → Variables):
 *   TELEGRAM_BOT_TOKEN : token Bot A dari @BotFather
 *   ALLOWED_CHAT_ID    : chat ID Telegram Anda (numerik)
 *   GITHUB_TOKEN       : PAT dengan scope `workflow`
 *   GITHUB_OWNER       : username GitHub Anda
 *   GITHUB_REPO        : nama repositori
 *   WORKFLOW_FILE      : (opsional) default "render.yml"
 *
 * ═══════════════════════════════════════════════════════════════════════
 * KONTRAK CALLBACK (WAJIB SINKRON DENGAN telegram_helper.py):
 *
 *   Tombol Telegram mengirim `callback_data` dalam format: action:folder
 *
 *     approve:SABTU   → triggerWorkflow(mode="render")   [✅ Setuju & Render]
 *     replan:SABTU    → triggerWorkflow(mode="plan")     [🔄 Susun Ulang]
 *     cancel:SABTU    → kirim pesan batal (tidak trigger) [❌ Batal]
 *
 *   Format lama "/setuju SABTU" SUDAH TIDAK DIPAKAI — itulah akar bug
 *   tombol ✅ tidak pernah memicu render di versi sebelumnya.
 *
 * ═══════════════════════════════════════════════════════════════════════
 * WORKFLOW TARGET:
 *   Semua request (SHORT & LONG) memicu `render.yml` (satu workflow).
 *   Mode SHORT/LONG dideteksi otomatis oleh main.py dari durasi VO.
 *   `render_long.yml` SUDAH DIHAPUS (konsolidasi).
 * ═══════════════════════════════════════════════════════════════════════
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK - Telegram Webhook Bridge Active (v2)", { status: 200 });
    }

    let update;
    try {
      update = await request.json();
    } catch (e) {
      console.error("[worker] JSON parse error:", String(e));
      return new Response("Bad Request", { status: 400 });
    }

    // ═══════════════════════════════════════════════════════════════════
    // 1. CALLBACK QUERY (klik tombol inline keyboard)
    // ═══════════════════════════════════════════════════════════════════
    if (update.callback_query) {
      const cq = update.callback_query;
      const chatId = String(cq.message.chat.id);

      if (env.ALLOWED_CHAT_ID && chatId !== String(env.ALLOWED_CHAT_ID)) {
        console.warn(`[worker] callback dari chat tidak diizinkan: ${chatId}`);
        return new Response("Unauthorized", { status: 200 });
      }

      const data = String(cq.data || "");
      const [action, folderName] = data.split(":", 2);
      console.log(`[worker] callback action="${action}" folder="${folderName || "-"}"`);

      if (action === "approve" && folderName) {
        await answerCallback(env, cq.id, "⏳ Memulai render final di GitHub Actions...");
        await sendTg(
          env, chatId,
          `🚀 <b>Memulai render</b> untuk folder <code>${esc(folderName)}</code>.\n` +
          `<i>Mode SHORT/LONG akan dideteksi otomatis dari durasi VO.</i>`
        );
        await triggerWorkflow(env, {
          folder_name: folderName,
          mode: "render",
          chat_id: chatId,
        });
      } else if (action === "replan" && folderName) {
        await answerCallback(env, cq.id, "🔄 Menyusun ulang rencana...");
        await sendTg(
          env, chatId,
          `🔄 <b>Menyusun ulang rencana</b> untuk folder <code>${esc(folderName)}</code>...`
        );
        await triggerWorkflow(env, {
          folder_name: folderName,
          mode: "plan",
          chat_id: chatId,
        });
      } else if (action === "cancel") {
        await answerCallback(env, cq.id, "❌ Dibatalkan.");
        await sendTg(
          env, chatId,
          `❌ Proses untuk folder <code>${esc(folderName || "-")}</code> dibatalkan. ` +
          `<i>Tidak ada render yang dijalankan.</i>`
        );
      } else {
        console.warn(`[worker] callback tidak dikenal: action="${action}" data="${data}"`);
        await answerCallback(env, cq.id, "⚠️ Aksi tidak dikenal.");
      }
      return new Response("OK", { status: 200 });
    }

    // ═══════════════════════════════════════════════════════════════════
    // 2. PESAN TEKS
    // ═══════════════════════════════════════════════════════════════════
    if (update.message && update.message.text) {
      const chatId = String(update.message.chat.id);

      if (env.ALLOWED_CHAT_ID && chatId !== String(env.ALLOWED_CHAT_ID)) {
        console.warn(`[worker] message dari chat tidak diizinkan: ${chatId}`);
        return new Response("Unauthorized", { status: 200 });
      }

      const text = update.message.text.trim();

      // ── /start / /help ──────────────────────────────────────────────
      if (text === "/start" || text === "/help") {
        await sendTg(
          env, chatId,
          "🤖 <b>Bot Auto-Render Video (v2)</b>\n\n" +
          "<b>📥 Perintah utama:</b>\n" +
          "• <code>/render FOLDER</code> — Susun rencana & kirim preview persetujuan\n" +
          "• <code>/render FOLDER | catatan</code> — Tambah catatan (koordinat map, gaya, dll)\n" +
          "• <code>/auto FOLDER</code> — Langsung render final tanpa approval\n\n" +
          "<b>🎬 Mode otomatis:</b>\n" +
          "• VO ≤ 75 dtk → <b>SHORT</b> (1080×1920 vertikal)\n" +
          "• VO &gt; 75 dtk → <b>LONG</b> (1920×1080 horizontal)\n" +
          "• Override: <code>/render FOLDER | force:long</code> atau <code>force:short</code>\n\n" +
          "<b>✏️ Edit rencana (setelah preview):</b>\n" +
          "• <code>/tema FOLDER | hutan</code> — ganti tema (hutan/laut/api/malam/bumi)\n" +
          "• <code>/hook FOLDER | BARIS 1 | BARIS 2</code> — ganti hook\n" +
          "• <code>/cta FOLDER | TEKS</code> — ganti CTA\n" +
          "• <code>/hapus FOLDER | 3, 5-7</code> — hapus overlay\n" +
          "• <code>/klip FOLDER | 1=namafile.mp4</code> — ganti klip B-roll\n" +
          "• <code>/buang_klip FOLDER | klip1.mp4, klip2.mp4</code> — buang klip dari pool\n\n" +
          "<b>❌ Batal:</b>\n" +
          "• <code>/batal FOLDER</code> — batalkan (tidak trigger apa pun)\n\n" +
          "<b>🏷️ Tag markup opsional di transkrip.txt:</b>\n" +
          "<code>[ROTO: L1 | L2]</code>, <code>[KINETIC: KICKER | L1 | L2]</code>,\n" +
          "<code>[QUOTE: BADGE | TEKS]</code>, <code>[CHART: ANGKA | LABEL]</code>,\n" +
          "<code>[CARD: BADGE | VALUE | SUB]</code>, <code>[DRAW: circle|LABEL]</code>,\n" +
          "<code>[MAP: LOKASI]</code>, <code>[HOOK: L1 | L2]</code>, <code>[CTA: TEKS]</code>"
        );
        return new Response("OK", { status: 200 });
      }

      // ── /render FOLDER [| note]  &  /auto FOLDER [| note] ───────────
      if (text.startsWith("/render ") || text.startsWith("/auto ")) {
        const isAuto = text.startsWith("/auto ");
        const rest = text.slice(isAuto ? 6 : 8).trim();
        const [folderRaw, noteRaw] = rest.split("|", 2);
        const folderName = (folderRaw || "").trim();
        const note = (noteRaw || "").trim();

        if (!folderName) {
          await sendTg(
            env, chatId,
            "⚠️ Format: <code>/render NAMA_FOLDER</code>\n" +
            "Contoh: <code>/render SABTU</code>"
          );
          return new Response("OK", { status: 200 });
        }

        await sendTg(
          env, chatId,
          `📡 <b>Mengirim perintah ke GitHub Actions</b>\n` +
          `Folder: <code>${esc(folderName)}</code>\n` +
          `Mode: <b>${isAuto ? "auto (langsung render)" : "plan (susun & tunggu approval)"}</b>\n` +
          (note ? `Catatan: <i>${esc(note)}</i>\n` : "") +
          `<i>Menunggu respon dari runner...</i>`
        );
        await triggerWorkflow(env, {
          folder_name: folderName,
          mode: isAuto ? "auto" : "plan",
          note,
          chat_id: chatId,
        });
        return new Response("OK", { status: 200 });
      }

      // ── /batal FOLDER (cancel via chat, alternatif tombol ❌) ───────
      if (text.startsWith("/batal ") || text === "/batal") {
        const folderName = text.slice(6).trim().split("|")[0].trim() || "-";
        await sendTg(
          env, chatId,
          `❌ Proses untuk folder <code>${esc(folderName)}</code> dibatalkan. ` +
          `<i>Tidak ada render yang dijalankan.</i>`
        );
        return new Response("OK", { status: 200 });
      }

      // ── Edit commands (/hapus /tema /hook /cta /klip /buang_klip) ───
      const editPrefixes = ["/hapus ", "/tema ", "/hook ", "/cta ", "/klip ", "/buang_klip "];
      if (editPrefixes.some((p) => text.startsWith(p))) {
        const spaceIdx = text.indexOf(" ");
        const rest = text.slice(spaceIdx + 1).trim();
        const folderName = (rest.split("|")[0] || "").trim();

        if (!folderName || !rest.includes("|")) {
          await sendTg(
            env, chatId,
            "⚠️ Format edit wajib pakai pemisah <code>|</code>.\n\n" +
            "Contoh:\n" +
            "• <code>/hapus SABTU | 4, 6</code>\n" +
            "• <code>/tema SABTU | api</code>\n" +
            "• <code>/hook SABTU | BARIS 1 | BARIS 2</code>\n" +
            "• <code>/klip SABTU | 1=nama_file.mp4</code>"
          );
          return new Response("OK", { status: 200 });
        }

        await sendTg(
          env, chatId,
          `✏️ <b>Menerapkan perubahan</b> pada rencana folder <code>${esc(folderName)}</code>...\n` +
          `<i>Preview baru akan dikirim setelah selesai.</i>`
        );
        await triggerWorkflow(env, {
          folder_name: folderName,
          mode: "edit",
          edit_cmd: text,
          chat_id: chatId,
        });
        return new Response("OK", { status: 200 });
      }

      // ── Pesan tidak dikenal ────────────────────────────────────────
      await sendTg(
        env, chatId,
        "❓ Perintah tidak dikenal. Ketik <code>/help</code> untuk melihat daftar perintah."
      );
    }

    return new Response("OK", { status: 200 });
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// HELPER: Trigger GitHub Actions workflow_dispatch
// ═══════════════════════════════════════════════════════════════════════════

async function triggerWorkflow(env, inputs) {
  const wf = env.WORKFLOW_FILE || "render.yml";
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${wf}/dispatches`;

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "Cloudflare-Telegram-Bridge-v2",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "main",
        inputs,
      }),
    });

    if (!res.ok) {
      const err = await res.text();
      console.error(`[worker] triggerWorkflow gagal ${res.status}: ${err.slice(0, 400)}`);
      if (inputs.chat_id) {
        await sendTg(
          env, inputs.chat_id,
          `❌ <b>Gagal memicu GitHub Actions</b> (${res.status})\n` +
          `<code>${esc(err.slice(0, 200))}</code>\n\n` +
          `<i>Periksa: GITHUB_TOKEN scope, nama repo, dan file workflow.</i>`
        );
      }
    } else {
      console.log(`[worker] workflow triggered OK (mode=${inputs.mode}, folder=${inputs.folder_name})`);
    }
  } catch (e) {
    console.error(`[worker] triggerWorkflow exception: ${String(e)}`);
    if (inputs.chat_id) {
      await sendTg(
        env, inputs.chat_id,
        `❌ <b>Network error</b> saat memicu workflow: <code>${esc(String(e).slice(0, 200))}</code>`
      );
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// HELPER: Telegram API
// ═══════════════════════════════════════════════════════════════════════════

async function sendTg(env, chatId, text) {
  if (!chatId) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: "HTML",
        disable_web_page_preview: true,
      }),
    });
  } catch (e) {
    console.error(`[worker] sendTg error: ${String(e)}`);
  }
}

async function answerCallback(env, callbackQueryId, text) {
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ callback_query_id: callbackQueryId, text }),
    });
  } catch (e) {
    console.error(`[worker] answerCallback error: ${String(e)}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// HELPER: HTML escape untuk nama folder (cegah parse_mode HTML break)
// ═══════════════════════════════════════════════════════════════════════════

function esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}