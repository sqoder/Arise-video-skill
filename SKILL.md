---
name: fable-video-workflow
description: End-to-end editorial AI video workflow supporting short Veo talking-head digests, pure Playwright/Chrome vector product launches, and long-form audio-driven article explainer videos ("讲解一篇文章", Markdown-to-video, unlimited chapters). Enforces Zero-Blue warm editorial design, Double-Bezel cards, spring physics, and automated narration-to-frame sync. Use whenever the user asks to create an "AI大事件", "今日AI", "讲解视频", "把文章做成视频", long-form explainer, or mentions `fable-video-workflow` / `AI视频工作流`.
---

# Editorial AI Video Studio Workflow (Short Digest + Launch + Long-Form Explainer)

## Overview

This workflow enforces warm editorial aesthetics (Zero-Blue Rule) across **three rendering tracks**:
1. **Engine 1 — Talking-Head + Editorial Compositing (`scripts/fable_studio.py`)**:
   - **Live-action Presenter Clips (Google Flow / Veo)**: Generate `2..N` realistic talking-head clips (warm tungsten studio light — zero neon blue/purple — plus solid `#97A396` sage-green studio wall) with strict `NO ON-SCREEN TEXT` and policy-safe phonetic/descriptive spoken Chinese.
   - **Programmatic Motion Graphics**: Composite kinetic typography, behind-head GrabCut layering, Terracotta (`#D97757`) flash cards, sage-green LAB matte separation, live RMS voice waveforms, Warm Parchment (`#FAF9F5`) sticker cutouts + tilted polaroids, warm-carbon aperture iris -> Architectural 3-Track NLE Timeline + Double-Bezel Studio Viewport, Warm Carbon (`#1C1B19`) Double-Bezel typing cards, damped spring physics (`spring_ease`), and 118 BPM sidechain-ducked audio mix.
2. **Engine 2 — Pure Vector Product/Editorial Launch (`scripts/render_web_stage.py` + `scripts/stage_template.html`, mirrored at `web_stage/`)**:
   - Deterministic **Headless Chrome + Playwright** frame capture rendering HTML5/CSS3/SVG UI cards (`0.0%` cold blue, Double-Bezel containers, native `<kbd>` keycaps, CSS `letter-spacing`, smooth auto-follow zooms, damped springs).
3. **Engine 3 — Long-Form Article Explainer (`scripts/explainer_studio.py` + `scripts/explainer_stage.html`)**:
   - Unlimited-chapter (1–20+ min) Markdown/article explainer video with audio-driven chapter frame locking (`edge-tts` neural Chinese voice + offline macOS `say` fallback), synchronized live RMS waveform + clause subtitles, and 6 editorial layouts (`title_hook`, `quote_highlight`, `architecture_flow`, `code_walkthrough`, `comparison_bento`, `summary_takeaway`).

## Four Anti-AI-Slop Rules (Enforced by Default)

1. **Zero-Blue Rule**: Default canvas & card tokens are Warm Parchment (`#FAF9F5` / `#F3F1EA`), Warm Carbon (`#141413` / `#1C1B19`), Terracotta (`#D97757`), Botanical Sage (`#788C5D`), and `1px` rules (`#DFDCD2`). Google Flow studio lighting uses warm tungsten key + soft amber rim light (`zero neon blue or purple lighting`).
2. **Architectural NLE Timeline B-Roll**: Beat transitions use a ruled `#FAF9F5` drafting grid, Double-Bezel live viewport, and 3-track timeline (`01 / VIDEO TRIM`, `02 / ZOOM · Z` Bezier curve, `03 / VOICE 48K`), banning dark-blue fake skylines and neon grids.
3. **Dual Rendering Engines**: Use `fable_studio.py` for Veo presenter stories, or adapt `web_stage/render_web_stage.py` + `web_stage/stage_template.html` when pure UI/browser-grade vector motion graphics are needed.
4. **Damped Spring Physics & Quantitative Verification**: UI transitions use harmonic `spring_ease(t, stiffness, damping)` easing, and `fable_studio.py verify` reports `ui_beat_cold_blue_pct` across synthetic graphical beats (`#3`, `#6`, `#7`) alongside `ffprobe` streams and `verify_grid.jpg`.

Default standalone user workspace: `$HOME/Downloads/ai-video-workflow/` (or the repository root `.`).

## Dependencies

- **Browser automation**: Refer to the existing **`ego-browser`** skill before controlling Google Flow in the browser. Never duplicate browser automation setup outside `ego-browser`.
- **Python runtime**: `python3` (Python 3.10+ with `playwright`, `cv2`, `numpy`, and `PIL` installed).
- **Web-Stage Browser**: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` (used headless by `render_web_stage.py`).
- **Media binaries**: `ffmpeg`, `ffprobe` (e.g., via Homebrew).

## Quick Start

```bash
PY=${PYTHON_BIN:-python3}
STUDIO="${STUDIO:-.}"

# 1. Create or reset project.json (supports --aspect 16:9|9:16 and --segments 3|4|5)
$PY "$STUDIO/fable_studio.py" init --output "$STUDIO/project.json" --aspect 16:9 --segments 3

# 2. Export safety-derisked Google Flow prompts + ingredient anchor instructions
$PY "$STUDIO/fable_studio.py" flow-plan --project "$STUDIO/project.json" --output "$STUDIO/output/flow_tasks.json"

# 3. Render final video once clips/seg_01.mp4..seg_0N.mp4 exist
$PY "$STUDIO/fable_studio.py" render --project "$STUDIO/project.json" --output "$STUDIO/output/《今日AI大事件》成片.mp4"

# 4. Verify A/V streams and produce 9-beat contact sheet
$PY "$STUDIO/fable_studio.py" verify --video "$STUDIO/output/《今日AI大事件》成片.mp4" --output "$STUDIO/output/verify_grid.jpg"
```

## Utility Scripts

- **`scripts/fable_studio.py`** (mirrored at `fable_studio.py`):
  - `init -o <project.json> [--aspect 16:9|9:16] [--segments N] [--hud-prefix "AI DAILY"] [--cta "每天一分钟 · 点个关注"]` (`N >= 2`, unlimited segments)
  - `flow-plan -p <project.json> [-o <flow_tasks.json>]`: Applies `safety.auto_derisk` to spoken dialogue while preserving original brand spelling in overlay fields.
  - `render -p <project.json> [-o <output.mp4>] [--keep-temp]`: Validates all segment clips exist, renders full Fable beats, cleans temporary intermediate files automatically.
  - `verify -v <video.mp4> [-o <verify_grid.jpg>]`: Runs `ffprobe`, reports `ui_beat_cold_blue_pct` across graphical beats (`#3`, `#6`, `#7`), and saves a 3x3 timestamped contact sheet.

- **`scripts/explainer_studio.py` + `scripts/explainer_stage.html`** (mirrored at `explainer_studio.py` & `web_stage/explainer_stage.html`):
  - Long-form audio-driven article explainer mode supporting unlimited chapters (1–15+ minutes).
  - `init -o <explainer_project.json>`: Creates a 6-chapter starter JSON covering all 6 warm editorial layouts (`title_hook`, `quote_highlight`, `architecture_flow`, `code_walkthrough`, `comparison_bento`, `summary_takeaway`).
  - `from-markdown -i <article.md> -o <explainer_project.json>`: Automatically converts any Markdown article into multi-chapter explainer JSON.
  - `render -p <explainer_project.json> -o <output.mp4> [--voice zh-CN-XiaoxiaoNeural] [--tts auto|edge|macos]`: Synthesizes chapter narration via `edge-tts` (with offline macOS `say -v Tingting` fallback), locks exact frame counts & RMS waveform + clause subtitles to audio duration, renders via Headless Chrome Playwright, and mixes 112 BPM ducked groove.
  - `verify -v <output.mp4> [-o <verify_explainer_grid.jpg>]`: Runs `ffprobe`, measures `cold_blue_pixel_pct` across sampled frames, and builds a 3x3 contact sheet.

## Supported Input Modes

1. **One-Sentence Topic ("一句话出片")**:
   - Research current factual news/events via `search_web` when the user asks about today's/recent news.
   - Draft concise ~9s spoken scripts per segment, map real brand typography into visual overlay fields, and populate `project.json`.
   - Run `flow-plan`, generate each segment in Google Flow via `ego-browser`, download clips to `clips/seg_01.mp4 ..`, then run `render` and `verify`.
2. **Long-Form Article / Markdown Explainer ("讲解一篇文章 / 长视频模式")**:
   - Accept any pasted article, Markdown file, or topic; convert/structure into N logical chapters (`explainer_studio.py from-markdown` or custom JSON across all 6 layouts: `title_hook`, `quote_highlight`, `architecture_flow`, `code_walkthrough`, `comparison_bento`, `summary_takeaway`).
   - Run `explainer_studio.py render` + `verify` (audio duration drives video frame count with zero A/V drift).
3. **Custom Script / Storyboard ("自带口播/分镜")**:
   - Preserve the user's exact spoken lines in `segments[*].spoken_text` and extract punchy visual callouts into overlay fields (`behind_head_left`/`right`, `flash_card_title`, `behind_body_lines`, `cutout_title`/`cutout_serif_lines`, `broll_caption`, `user_prompt_text`).
   - Run `flow-plan` -> Google Flow -> `render` -> `verify`.
4. **Custom Persona / Photo + Flexible Format ("自带照片 / 自定义形象 / 竖屏 9:16")**:
   - Copy or reference the user's portrait image in `persona.dark_anchor_path` (and/or `green_anchor_path`), set `persona.mode = "custom"`, or set `format.aspect_ratio = "9:16"` (`1080x1920`).
   - Optionally customize `brand.hud_prefix` and `brand.cta_text`.

## Google Flow Browser Playbook (via `ego-browser`)

1. **Workspace Check**: Call `listTaskSpaces()` first; reuse your configured browser task space and Chrome profile. Never leave dangling tabs; finish cleanly via `task.finish({ keep: [...] })` after downloads settle.
2. **Model & Ratio Selection**: Use `"Veo 3.1 - Lite"` (default) and `"x1"` batch count; match `"16:9"` or `"9:16"` to `project.json`.
3. **Attaching Consistency Ingredients**:
   - Upload or select `assets/default_dark_anchor.png` / `assets/default_green_anchor.png` (or the user's portrait photo).
   - **Verify ingredient chip attachment**: Confirm the ingredient button (`button "素材"`) sits strictly between the last `svg_root` and `textfield "您希望创作什么？"` in the DOM/snapshot before submitting.
4. **Snackbar Obstacle Handling**: Dismiss any `"已将 X 项内容移至回收站"` snackbars by clicking `text "关闭"` before clicking nearby media cards.
5. **Silent Safety Retry**: Always read prompts from `flow_tasks.json` (`flow_prompt`, already de-risked). If Google Flow rejects a prompt for policy/safety despite `auto_derisk`, immediately soften remaining proper nouns, financial words, or geopolitical phrasing in `spoken_text_safe` and retry silently without stopping to bother the user.

## Compositing & Typography Guardrails

- **No Tofu Glyphs**: Never use emojis (`🔥`, `💳`) or unsupported symbols (`▸`) in Pillow text layers; stick to clean Chinese/ASCII characters and `·`, `—`, `&`, `●`.
- **OpenCV Array Contiguity**: When converting PIL RGBA/RGB slices to BGR arrays for OpenCV calls (`rectangle`, `warpAffine`, `GaussianBlur`), always wrap with `np.ascontiguousarray(...)` and allocate float buffers explicitly with `np.zeros((H, W, 3), dtype=...)`.
- **Clean Cleanup**: Temporary normalized WAV/MP4 directories inside `output/` must be cleaned up after `render` completes (`--keep-temp` only when debugging).

## Common Mistakes

- **Running system Python without dependencies**: Results in `ModuleNotFoundError: No module named 'cv2'`. Execute with the Python 3.10+ environment where `cv2`/`playwright`/`Pillow` are installed (e.g., `PYTHON_BIN=/path/to/python3`).
- **Passing `"新闻记者"` or unmasked `"OpenAI / IPO"` into Flow dialogue**: Triggers safety blocks or sterile news-anchor clichés. Keep `"视频创作者"` framing and run `flow-plan` first.
- **Letting Flow burn subtitles into raw video**: Ruin behind-head typography and sage-green cutout aesthetics. Always keep `STRICT NO ON-SCREEN TEXT` in Flow prompts.
- **Skipping `verify` / visual check**: Always inspect `verify_grid.jpg` via `read_file` after rendering to confirm typography margins, matte edges, polaroid placement, and audio duration.
