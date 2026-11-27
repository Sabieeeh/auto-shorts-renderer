import os
import re
import json
import glob
import html
import anthropic
from drive_helper import (
    get_drive_service,
    find_folder,
    download_project_assets,
    upload_or_update_file,
)
from telegram_helper import send_message, send_approval_card
from pipeline import (
    DEFAULT_STYLE_NOTE,
    tighten_vo,
    probe,
    align_whisper,
    run_auto_director,
    scan_local_clips,
    run_auto_broll,
    apply_edit_command,
    render_final_video,
)


def main():
    folder_name = os.environ.get("INPUT_FOLDER_NAME", "").strip()
    mode = os.environ.get("INPUT_MODE", "plan").strip().lower()
    theme_override = os.environ.get("INPUT_THEME", "auto").strip().lower()
    project_note = os.environ.get("INPUT_NOTE", "").strip()
    edit_cmd = os.environ.get("INPUT_EDIT_CMD", "").strip()
    chat_id = (os.environ.get("INPUT_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    model = os.environ.get("AUTO_MODEL", "claude-sonnet-4-6").strip()

    assert folder_name, "INPUT_FOLDER_NAME kosong!"
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    assert api_key, "ANTHROPIC_API_KEY belum disetel di Secrets!"

    work_dir = "/tmp/work_proj"
    proj_dir = os.path.join(work_dir, "assets")
    os.makedirs(proj_dir, exist_ok=True)

    drive = get_drive_service()
    folder = find_folder(drive, folder_name)
    folder_id = folder["id"]
    print(f"[Drive] Folder ditemukan: {folder['name']} ({folder_id})")

    state_path = os.path.join(proj_dir, "plan_state.json")

    if mode in ("plan", "auto"):
        send_message(chat_id, f"⏳ <b>Memproses folder:</b> <code>{html.escape(folder_name)}</code>\nMengunduh aset, menjalankan Whisper & menyusun rencana Claude...")
        download_project_assets(drive, folder_id, proj_dir, include_media=True)

        audio_files = sorted(glob.glob(f"{proj_dir}/*.mp3") + glob.glob(f"{proj_dir}/*.m4a") + glob.glob(f"{proj_dir}/*.wav"))
        trx_files = sorted(glob.glob(f"{proj_dir}/transkrip*"))
        assert audio_files, f"File audio VO (.mp3/.m4a/.wav) tidak ada di folder {folder_name}"
        assert trx_files, f"File transkrip* tidak ada di folder {folder_name}"

        raw_text = open(trx_files[0], "rb").read().decode("utf-8-sig")
        text = re.sub(r"\s+", " ", raw_text).strip()

        vo_path = tighten_vo(audio_files[0], work_dir)
        vo_dur = probe(vo_path)["dur"]
        words, units, unit_texts = align_whisper(vo_path, text, vo_dur, model_name="small")

        client = anthropic.Anthropic(api_key=api_key)
        plan = run_auto_director(client, text, DEFAULT_STYLE_NOTE, project_note, model=model)
        clips = scan_local_clips(proj_dir, work_dir, extract_frames=True)
        clip_hints = run_auto_broll(client, clips, unit_texts, words, proj_dir, model=model)

        labels_path = os.path.join(proj_dir, "clip_labels.json")
        if os.path.exists(labels_path):
            upload_or_update_file(drive, folder_id, labels_path, "clip_labels.json", "application/json")

        chosen_theme = theme_override if theme_override != "auto" else plan.get("theme", "hutan")
        state = {
            "folder_name": folder["name"],
            "folder_id": folder_id,
            "vo_dur": vo_dur,
            "theme": chosen_theme,
            "topic": plan.get("topic", folder["name"]),
            "hook_lines": plan.get("hook_lines", []),
            "keywords": plan.get("keywords", []),
            "cta_text": "",
            "overlays": plan.get("overlays", []),
            "clip_hints": clip_hints,
            "usable_names": [c["name"] for c in clips],
            "excluded_clips": [],
            "words": words,
            "units": units,
            "unit_texts": unit_texts,
        }
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        upload_or_update_file(drive, folder_id, state_path, "plan_state.json", "application/json")

        if mode == "plan":
            send_approval_card(chat_id, state)
            print("Rencana terkirim ke Telegram. Menunggu persetujuan.")
            return

    elif mode == "edit":
        download_project_assets(drive, folder_id, proj_dir, include_media=False)
        assert os.path.exists(state_path), f"plan_state.json belum ada di {folder_name}. Jalankan /render {folder_name} dulu."
        state = json.load(open(state_path, encoding="utf-8"))
        state = apply_edit_command(state, edit_cmd)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        upload_or_update_file(drive, folder_id, state_path, "plan_state.json", "application/json")
        send_approval_card(chat_id, state)
        print("Rencana diperbarui dan dikirim ulang ke Telegram.")
        return

    elif mode == "render":
        send_message(chat_id, f"🚀 <b>Disetujui!</b> Mulai merender B-roll + motion graphic Remotion untuk <code>{html.escape(folder_name)}</code> (±5-8 menit)...")
        download_project_assets(drive, folder_id, proj_dir, include_media=True)
        assert os.path.exists(state_path), f"plan_state.json belum ada di {folder_name}."
        state = json.load(open(state_path, encoding="utf-8"))

    final_mp4 = render_final_video(state, proj_dir, work_dir)
    uploaded = upload_or_update_file(drive, folder_id, final_mp4, os.path.basename(final_mp4), "video/mp4")
    link = uploaded.get("webViewLink", "https://drive.google.com")
    size_mb = os.path.getsize(final_mp4) / (1024 * 1024)

    send_message(
        chat_id,
        f"✅ <b>RENDER SELESAI!</b>\n\n"
        f"🎬 File: <code>{html.escape(os.path.basename(final_mp4))}</code> ({size_mb:.1f} MB)\n"
        f"📁 Folder Drive: <code>{html.escape(folder_name)}</code>\n"
        f"🔗 <a href=\"{link}\">Buka Video di Google Drive</a>",
    )
    print("=== SELESAI TOTAL ===")


if __name__ == "__main__":
    main()
