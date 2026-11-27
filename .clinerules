
## 4. Transition, Cut Pacing & Strict Cleanup (Mandatory Fixes)
- **Kill Whip-Blur / Zoom-Blur Transitions:** Remove ALL directional motion-blur, whip-pan, and heavy zoom-blur transitions between B-roll clips. Use clean **hard cuts** aligned to Whisper sentence/pause boundaries, and only use a subtle cross-dissolve (`<= 0.25s`) on major `chapter_title` transitions.
- **B-Roll Shot Duration:** Keep B-roll shots steady (`3.5s` to `5.5s` per shot when narration continues) with only a subtle, slow push-in (`scale 1.0 -> 1.03`), instead of rapid 2-second cuts.
- **Zero Glass / Banner Boxes Anywhere:** Audit all `.tsx`/`.ts` files inside `src/` as well as `remotion_builder.py` and delete any remaining translucent dark glass cards (`rgba(..., 0.5)` + `backdropFilter`) or solid orange banner boxes.
- **No Hallucinated Stats:** Never generate `stat_callout` unless an actual number/percentage explicitly appears in the narration transcript.
- **No Orphan 1-Word Subtitles & Crisp Contrast:** Merge any isolated 1-word subtitle chunk into its neighboring phrase, and apply a multi-layer dark text shadow + subtle bottom gradient scrim so white captions remain legible over bright footage.
