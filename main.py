"""
main.py — Entry point pipeline auto-shorts-renderer v2.
═══════════════════════════════════════════════════════════════════════════
ALUR EKSEKUSI:
  mode="plan"   : download → vo_dur → detect SHORT/LONG → parse markup
                  → align Whisper → planner → save plan_state.json
                  → kirim approval card (SELESAI, tunggu tombol ✅)
  mode="auto"   : sama dengan plan, lalu LANGSUNG render final
  mode="edit"   : download → apply_edit_command → save & kirim ulang card
  mode="render" : download → baca plan_mode → render → upload → notif selesai
═══════════════════════════════════════════════════════════════════════════
"""

import os
import json
import glob
import html

import config as cfg
import smart_parser
from audio_utils import (
    probe,
    tighten_vo,
    align_whisper,
)
from drive_helper import (
    get_drive_service,
    find_folder,
    download_project_assets,
    upload_or_update_file,
)


def _read_inputs() -> dict:
    """Baca semua INPUT_* env + resolve chat_id + validasi mode."""
    folder_name = os.environ.get("INPUT_FOLDER_NAME", "").strip()
    mode = os.environ.get("INPUT_MODE", "plan").strip().lower()
    theme_override = os.environ.get("INPUT_THEME", "auto").strip().lower()
    project_note = os.environ.get("INPUT_NOTE", "").strip()
    edit_cmd = os.environ.get("INPUT_EDIT_CMD", "").strip()
    chat_id = (
        os.environ.get("INPUT_CHAT_ID")
        or os.environ.get("TELEGRAM_CHAT_ID")
        or ""
    ).strip()
    model = os.environ.get("AUTO_MODEL", "claude-sonnet-4-6").strip()

    assert folder_name, "INPUT_FOLDER_NAME kosong!"
    if mode not in ("plan", "render", "auto", "edit"):
        raise ValueError(
            f"INPUT_MODE tidak valid: '{mode}' "
            f"(harus salah satu: plan | render | auto | edit)"
        )

    return {
        "folder_name": folder_name,
        "mode": mode,
        "theme_override": theme_override,
        "project_note": project_note,
        "edit_cmd": edit_cmd,
        "chat_id": chat_id,
        "model": model,
    }


def _resolve_force_mode(note: str) -> str:
    """Cek apakah project_note memaksa mode tertentu (LONG/SHORT/auto)."""
    low = (note or "").lower()
    if "force:long" in low or "force long" in low:
        return "LONG"
    if "force:short" in low or "force short" in low:
        return "SHORT"
    return ""


def _ensure_anthropic_client():
    """Opsional — kembalikan Anthropic client jika USE_API=1 dan paket tersedia."""
    if os.environ.get("USE_API", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[main] USE_API=1 tapi ANTHROPIC_API_KEY kosong — fallback offline.")
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        print("[main] Paket `anthropic` tidak terpasang — fallback offline.")
        return None
    except Exception as e:
        print(f"[main] Anthropic client gagal dibuat ({str(e)[:120]}) — fallback offline.")
        return None


def _build_state(
    folder: dict,
    folder_id: str,
    plan: dict,
    scene_pairs: list,
    clip_hints: dict,
    clips: list,
    words: list,
    units: list,
    vo_dur: float,
    plan_mode: str,
    theme_override: str,
) -> dict:
    """Rakit plan_state.json dari hasil planner + media metadata."""
    chosen_theme = (
        theme_override
        if theme_override != "auto" and theme_override in cfg.THEMES
        else cfg.safe_theme(plan.get("theme", ""))
    )

    return {
        "folder_name": folder["name"],
        "folder_id": folder_id,
        "plan_mode": plan_mode,
        "plan_source": plan.get("plan_source", "auto"),
        "markup_tags_found": plan.get("markup_tags_found", []),
        "vo_dur": vo_dur,
        "theme": chosen_theme,
        "topic": plan.get("topic", folder["name"]),
        "hook_lines": plan.get("hook_lines", []),
        "keywords": plan.get("keywords", []),
        "cta_text": plan.get("cta_pertanyaan", ""),
        "fakta_konkret": plan.get("fakta_konkret", []),
        "scenes": plan.get("scenes", []),
        "scene_pairs": scene_pairs,
        "overlays": plan.get("overlays", []),
        "clip_hints": clip_hints,
        "usable_names": [c["name"] for c in clips],
        "excluded_clips": [],
        "words": words,
        "units": units,
    }


def main():
    inputs = _read_inputs()
    folder_name = inputs["folder_name"]
    mode = inputs["mode"]
    theme_override = inputs["theme_override"]
    project_note = inputs["project_note"]
    edit_cmd = inputs["edit_cmd"]
    chat_id = inputs["chat_id"]
    model = inputs["model"]

    work_dir = "/tmp/work_proj"
    proj_dir = os.path.join(work_dir, "assets")
    os.makedirs(proj_dir, exist_ok=True)
    state_path = os.path.join(proj_dir, "plan_state.json")

    drive = get_drive_service()
    folder = find_folder(drive, folder_name)
    folder_id = folder["id"]
    print(f"[Drive] Folder ditemukan: {folder['name']} ({folder_id})")

    from telegram_helper import send_message, send_message_long, send_approval_card

    state: dict = {}
    plan_mode: str = "SHORT"

    if mode in ("plan", "auto"):
        send_message(
            chat_id,
            f"⏳ <b>Memproses folder:</b> <code>{html.escape(folder_name)}</code>\n"
            f"Mengunduh aset, memadatkan VO, alignment Whisper, menyusun rencana...",
        )
        download_project_assets(drive, folder_id, proj_dir, include_media=True)

        audio_files = sorted(
            glob.glob(f"{proj_dir}/*.mp3")
            + glob.glob(f"{proj_dir}/*.m4a")
            + glob.glob(f"{proj_dir}/*.wav")
        )
        trx_files = sorted(glob.glob(f"{proj_dir}/transkrip*"))
        assert audio_files, f"File audio VO (.mp3/.m4a/.wav) tidak ada di folder {folder_name}"
        assert trx_files, f"File transkrip* tidak ada di folder {folder_name}"

        raw_text = open(trx_files[0], "rb").read().decode("utf-8-sig")

        parsed_tags = smart_parser.parse_markup_tags(raw_text)
        if parsed_tags.get("has_markup"):
            print(
                f"[main] Tag ditemukan: {parsed_tags.get('tags_found', [])} "
                f"(total {parsed_tags.get('tag_count', 0)})"
            )
        else:
            print("[main] Tidak ada tag markup — memakai auto-mode.")

        clean_text = smart_parser.strip_tags(raw_text)
        assert clean_text, f"Transkrip bersih kosong setelah strip_tags di {folder_name}"

        vo_padat = tighten_vo(audio_files[0], work_dir)
        vo_dur = probe(vo_padat)["dur"]

        force = _resolve_force_mode(project_note)
        plan_mode = force if force else cfg.detect_mode(vo_dur)
        print(f"[main] VO {vo_dur:.1f} dtk → mode {plan_mode}" + (f" (force via note)" if force else ""))

        os.environ["VIDEO_MODE"] = plan_mode

        from pipeline import (
            run_auto_director,
            scan_local_clips,
            run_auto_broll,
        )

        words, units, unit_texts = align_whisper(
            vo_padat, clean_text, vo_dur, model_name=cfg.WHISPER_MODEL,
        )

        client = _ensure_anthropic_client()
        plan = run_auto_director(
            client,
            clean_text,
            cfg.DEFAULT_STYLE_NOTE,
            project_note,
            model=model,
            parsed_tags=parsed_tags,
        )

        clips = scan_local_clips(proj_dir, work_dir, extract_frames=True)
        clip_hints = run_auto_broll(
            client, clips, unit_texts, words, proj_dir,
            model=model, scenes=plan.get("scenes"),
        )

        scene_pairs_path = os.path.join(proj_dir, "scene_pairs.json")
        scene_pairs = (
            json.load(open(scene_pairs_path, encoding="utf-8"))
            if os.path.exists(scene_pairs_path) else []
        )

        labels_path = os.path.join(proj_dir, "clip_labels.json")
        if os.path.exists(labels_path):
            upload_or_update_file(
                drive, folder_id, labels_path,
                "clip_labels.json", "application/json",
            )

        state = _build_state(
            folder, folder_id, plan, scene_pairs, clip_hints, clips,
            words, units, vo_dur, plan_mode, theme_override,
        )
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        upload_or_update_file(
            drive, folder_id, state_path, "plan_state.json", "application/json",
        )

        if mode == "plan":
            send_approval_card(chat_id, state)
            print("Rencana terkirim ke Telegram. Menunggu persetujuan.")
            return

        send_message(
            chat_id,
            f"🚀 <b>Auto-render:</b> <code>{html.escape(folder_name)}</code> "
            f"({plan_mode}, {vo_dur:.1f} dtk) — langsung render tanpa approval.",
        )

    elif mode == "edit":
        download_project_assets(drive, folder_id, proj_dir, include_media=False)
        assert os.path.exists(state_path), (
            f"plan_state.json belum ada di {folder_name}. Jalankan /render {folder_name} dulu."
        )
        state = json.load(open(state_path, encoding="utf-8"))

        plan_mode = state.get("plan_mode", "SHORT")
        if plan_mode not in ("SHORT", "LONG"):
            plan_mode = "SHORT"
        os.environ["VIDEO_MODE"] = plan_mode

        from pipeline import apply_edit_command

        state = apply_edit_command(state, edit_cmd)
        state.setdefault("plan_mode", plan_mode)

        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        upload_or_update_file(
            drive, folder_id, state_path, "plan_state.json", "application/json",
        )
        send_approval_card(chat_id, state)
        print("Rencana diperbarui dan dikirim ulang ke Telegram.")
        return

    elif mode == "render":
        download_project_assets(drive, folder_id, proj_dir, include_media=True)
        assert os.path.exists(state_path), f"plan_state.json belum ada di {folder_name}."
        state = json.load(open(state_path, encoding="utf-8"))

        plan_mode = state.get("plan_mode", "SHORT")
        if plan_mode not in ("SHORT", "LONG"):
            plan_mode = "SHORT"
        os.environ["VIDEO_MODE"] = plan_mode

        dur = state.get("vo_dur", 0.0)
        mode_emoji = "🎬" if plan_mode == "SHORT" else "🎥"
        send_message(
            chat_id,
            f"{mode_emoji} <b>Disetujui!</b> Mulai render <b>{plan_mode}</b> "
            f"({dur:.1f} dtk) untuk <code>{html.escape(folder_name)}</code>...",
        )

    from pipeline import render_final_video

    final_mp4 = render_final_video(state, proj_dir, work_dir)
    uploaded = upload_or_update_file(
        drive, folder_id, final_mp4,
        remote_name=None,
        mime_type="video/mp4",
    )
    link = uploaded.get("webViewLink", "https://drive.google.com")
    size_mb = os.path.getsize(final_mp4) / (1024 * 1024)
    out_name = os.path.basename(final_mp4)

    done_msg = (
        f"✅ <b>RENDER SELESAI!</b>\n\n"
        f"🎬 Mode: <b>{plan_mode}</b>\n"
        f"📁 File: <code>{html.escape(out_name)}</code> ({size_mb:.1f} MB)\n"
        f"🗂 Folder Drive: <code>{html.escape(folder_name)}</code>\n"
        f"🔗 <a href=\"{link}\">Buka Video di Google Drive</a>"
    )

    if plan_mode == "LONG":
        send_message_long(chat_id, done_msg)
    else:
        send_message(chat_id, done_msg)

    print("=== SELESAI TOTAL ===")


if __name__ == "__main__":
    main()