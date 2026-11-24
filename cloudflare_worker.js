/**
 * Cloudflare Worker (Gratis 24/7): Jembatan Webhook Telegram -> GitHub Actions
 *
 * Environment Variables yang wajib diisi di Cloudflare Worker Settings -> Variables:
 * - TELEGRAM_BOT_TOKEN : Token bot dari @BotFather
 * - ALLOWED_CHAT_ID    : Chat ID Telegram pribadi Anda (angka, mis. 123456789)
 * - GITHUB_TOKEN       : GitHub Personal Access Token (izin: repo & workflow)
 * - GITHUB_OWNER       : Username GitHub Anda
 * - GITHUB_REPO        : Nama repositori GitHub Anda
 * - WORKFLOW_FILE      : render.yml
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK - Telegram Webhook Bridge Active", { status: 200 });
    }

    const update = await request.json();

    // 1. Tangani klik tombol Inline Keyboard (Setuju / Acak Ulang / Batal)
    if (update.callback_query) {
      const cq = update.callback_query;
      const chatId = String(cq.message.chat.id);
      if (env.ALLOWED_CHAT_ID && chatId !== String(env.ALLOWED_CHAT_ID)) {
        return new Response("Unauthorized", { status: 200 });
      }

      const data = cq.data || "";
      const [action, folderName] = data.split(":", 2);

      if (action === "approve" && folderName) {
        await answerCallback(env, cq.id, "⏳ Memulai render final di GitHub Actions...");
        await triggerWorkflow(env, { folder_name: folderName, mode: "render", chat_id: chatId });
      } else if (action === "replan" && folderName) {
        await answerCallback(env, cq.id, "🔄 Menyusun ulang rencana Claude...");
        await triggerWorkflow(env, { folder_name: folderName, mode: "plan", chat_id: chatId });
      } else if (action === "cancel") {
        await answerCallback(env, cq.id, "❌ Dibatalkan.");
        await sendTg(env, chatId, `❌ Proses untuk folder <code>${folderName || "-"}</code> dibatalkan.`);
      }
      return new Response("OK", { status: 200 });
    }

    // 2. Tangani pesan teks perintah
    if (update.message && update.message.text) {
      const chatId = String(update.message.chat.id);
      if (env.ALLOWED_CHAT_ID && chatId !== String(env.ALLOWED_CHAT_ID)) {
        return new Response("Unauthorized", { status: 200 });
      }

      const text = update.message.text.trim();

      if (text === "/start" || text === "/help") {
        await sendTg(
          env,
          chatId,
          "🤖 <b>Bot Auto-Render YouTube Shorts</b>\n\n" +
            "• <code>/render NAMA_FOLDER</code> — Susun rencana & kirim preview persetujuan\n" +
            "• <code>/render NAMA_FOLDER | catatan khusus</code> — Susun rencana dengan catatan map/gaya\n" +
            "• <code>/auto NAMA_FOLDER</code> — Langsung render final tanpa jeda persetujuan\n" +
            "• <code>/hapus NAMA_FOLDER | 3, 7</code> — Hapus nomor overlay tertentu\n" +
            "• <code>/tema NAMA_FOLDER | hutan</code> — Ubah tema warna (hutan/api/laut/malam/bumi)\n" +
            "• <code>/hook NAMA_FOLDER | BARIS 1 | BARIS 2</code> — Ganti teks hook\n" +
            "• <code>/cta NAMA_FOLDER | TEKS CTA</code> — Tambah teks penutup\n" +
            "• <code>/klip NAMA_FOLDER | 1=namafile.mp4</code> — Ganti pasangan klip B-roll"
        );
        return new Response("OK", { status: 200 });
      }

      if (text.startsWith("/render ") || text.startsWith("/auto ")) {
        const isAuto = text.startsWith("/auto ");
        const rest = text.slice(isAuto ? 6 : 8).trim();
        const [folderRaw, noteRaw] = rest.split("|", 2);
        const folderName = (folderRaw || "").trim();
        const note = (noteRaw || "").trim();
        if (!folderName) {
          await sendTg(env, chatId, "⚠️ Format: <code>/render NAMA_FOLDER</code>");
          return new Response("OK", { status: 200 });
        }
        await sendTg(env, chatId, `📡 Mengirim perintah ke GitHub Actions untuk folder <code>${folderName}</code>...`);
        await triggerWorkflow(env, {
          folder_name: folderName,
          mode: isAuto ? "auto" : "plan",
          note,
          chat_id: chatId,
        });
        return new Response("OK", { status: 200 });
      }

      if (
        text.startsWith("/hapus ") ||
        text.startsWith("/tema ") ||
        text.startsWith("/hook ") ||
        text.startsWith("/cta ") ||
        text.startsWith("/klip ") ||
        text.startsWith("/buang_klip ")
      ) {
        const spaceIdx = text.indexOf(" ");
        const rest = text.slice(spaceIdx + 1).trim();
        const folderName = (rest.split("|")[0] || "").trim();
        if (!folderName || !rest.includes("|")) {
          await sendTg(env, chatId, "⚠️ Format edit wajib pakai pemisah <code>|</code>, contoh:\n<code>/hapus BARU | 4, 6</code>");
          return new Response("OK", { status: 200 });
        }
        await sendTg(env, chatId, `✏️ Menerapkan perubahan pada rencana <code>${folderName}</code>...`);
        await triggerWorkflow(env, {
          folder_name: folderName,
          mode: "edit",
          edit_cmd: text,
          chat_id: chatId,
        });
        return new Response("OK", { status: 200 });
      }
    }

    return new Response("OK", { status: 200 });
  },
};

async function triggerWorkflow(env, inputs) {
  const wf = env.WORKFLOW_FILE || "render.yml";
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${wf}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "Cloudflare-Telegram-Bridge",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ref: "main",
      inputs,
    }),
  });
  if (!res.ok) {
    const err = await res.text();
    await sendTg(env, inputs.chat_id, `❌ Gagal memicu GitHub Actions (${res.status}): <code>${err.slice(0, 200)}</code>`);
  }
}

async function sendTg(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML" }),
  });
}

async function answerCallback(env, callbackQueryId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ callback_query_id: callbackQueryId, text }),
  });
}
