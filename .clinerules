# Project Rules: auto-shorts-renderer (Editorial Motion Graphics & SFX)

## 1. Coding Discipline (Ponytail Style)
- Write minimal, surgical, production-ready diffs. Preserve existing CLI/env interfaces (`VIDEO_MODE=LONG` vs `SHORT`).
- Synthesize SFX cleanly using standard Python (`wave`/`math`/`struct`) or FFmpeg filters without external audio asset downloads.
- Verify Python syntax (`python -m py_compile pipeline.py remotion_builder.py`) and Remotion TypeScript (`npx tsc --noEmit`) before finishing.

## 2. Hard Technical Anchors
- **Whisper Alignment & Full-Phrase Subtitles:** Display the entire 2–4 word subtitle phrase clearly and brightly from frame 1 (white base text with gold/amber highlight on the spoken word; never hide upcoming words or render dark grey unreadable words). Merge isolated 1-word chunks.
- **Safe Zones:** Strictly respect `SAFE_ZONES` for both `SHORT` (`1080x1920`) and `LONG` (`1920x1080`).
- **Clean Hard Cuts & Subtle Digital Zoom:** Keep B-roll cuts clean (`3.5s–5.5s`) with slow push-in (`1.0 -> 1.03`), zero whip-blur transitions, and zero dark glass boxes.
- **No Hallucinated Stats & No Maps:** Never invent numbers for `chart_grow`/`stat_callout` unless explicitly present in the narration transcript, and never reintroduce geographic `map` overlays.

## 3. Editorial Motion Graphic System (4 Core Styles + SFX)
- **Pacing:** Trigger 1 editorial motion graphic every 4–6 seconds synced to narration meaning (`>= 0.3s` duration).
- **4 Signature Remotion Components:**
  1. `cover_story`: Magazine-style editorial title layout with thin framing rules and masked typography reveal (used for `00:00` hook and major `chapter_title` beats).
  2. `headline_wall`: Sequential staggered news/fact headline strips stacked cleanly in the upper safe zone with thin accent borders (no dark glass cards).
  3. `chart_grow`: Animated SVG line/trend chart (`strokeDashoffset` + glowing endpoint dot + subtle grid) triggered ONLY when real numbers/comparisons exist in the transcript.
  4. `frame_break`: Brief editorial inset framing of the active video clip (`scale` down to framed card with crisp hairline border) at major narrative turning points before cutting to the next shot.
- **Synthesized Documentary SFX (No BGM):** Mix subtle synthesized WAV cues (`-20 dB` below VO, `>= 1.8s` cooldown) synced to overlay entrances: `sub_pulse` (`cover_story`, `frame_break`), `marker_sweep` (`headline_wall`), and `editorial_tick` (`chart_grow`).
