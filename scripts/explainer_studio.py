#!/usr/bin/env python3
"""
Explainer Studio CLI — Long-form Audio-Driven Article Explainer Engine.

Supports arbitrary-length multi-chapter videos (1-15+ min) with:
  - Automatic speech synthesis (edge-tts neural Chinese voice with macOS `say` fallback)
    or user-provided audio/video files per chapter
  - Audio-driven timeline locking (exact frame count + per-frame RMS voice waveform + clause subtitles)
  - Headless Chrome + Playwright HTML5/CSS3/SVG rendering (`web_stage/explainer_stage.html`)
    enforcing the 4 Anti-AI-Slop rules (Zero-Blue Rule, Double-Bezel UI, Spring Physics)

Subcommands:
  init           Create a 6-chapter starter explainer_project.json covering all 6 layouts.
  from-markdown  Convert any Markdown (.md) article into a multi-chapter explainer_project.json.
  render         Synthesize chapter audio, align subtitles/RMS, and render final MP4 via Chrome + FFmpeg.
  verify         Probe rendered MP4, compute cold_blue_pixel_pct, and build contact sheet.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import wave

import cv2
import numpy as np
from playwright.sync_api import sync_playwright

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STAGE_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "web_stage", "explainer_stage.html"),
    os.path.join(SCRIPT_DIR, "explainer_stage.html"),
    os.path.join(os.path.dirname(SCRIPT_DIR), "web_stage", "explainer_stage.html"),
]


def find_stage_html(custom_path=""):
    if custom_path and os.path.exists(custom_path):
        return os.path.abspath(custom_path)
    for cand in DEFAULT_STAGE_CANDIDATES:
        if os.path.exists(cand):
            return os.path.abspath(cand)
    raise FileNotFoundError("Cannot locate explainer_stage.html in web_stage/ or scripts/.")


def get_duration(path):
    res = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return float(res.stdout.strip())


def split_subtitle_clauses(text, dur_sec, max_chars=30):
    """Split spoken narration into balanced readable clauses mapped to [0, dur_sec]."""
    raw = [p.strip() for p in re.split(r"[。！？；!?;，,]+", text or "") if p.strip()]
    if not raw:
        return [{"start": 0.0, "end": dur_sec, "text": ""}]

    merged = []
    for piece in raw:
        if merged and len(merged[-1]) + 1 + len(piece) <= max_chars:
            merged[-1] = f"{merged[-1]}，{piece}"
        else:
            merged.append(piece)

    weights = [max(4, len(c)) for c in merged]
    total_w = float(sum(weights))
    usable = max(0.5, dur_sec - 0.30)
    out = []
    cur = 0.0
    for c, w in zip(merged, weights):
        span = usable * (w / total_w)
        out.append({"start": cur, "end": cur + span, "text": c})
        cur += span
    out[-1]["end"] = dur_sec + 1.0
    return out


async def _run_edge_tts(text, voice, out_mp3):
    import edge_tts

    comm = edge_tts.Communicate(text, voice)
    await comm.save(out_mp3)


def synthesize_chapter_wav(spoken_text, out_wav, voice="zh-CN-XiaoxiaoNeural", tts_mode="auto", tmp_dir="."):
    """Synthesize spoken narration via edge-tts (with offline macOS `say` fallback) and normalize to 48kHz WAV."""
    raw_audio = None
    if tts_mode in ("auto", "edge"):
        mp3_tmp = os.path.join(tmp_dir, f"tts_{os.path.basename(out_wav)}.mp3")
        try:
            asyncio.run(_run_edge_tts(spoken_text, voice, mp3_tmp))
            if os.path.exists(mp3_tmp) and os.path.getsize(mp3_tmp) > 512:
                raw_audio = mp3_tmp
        except Exception as exc:
            if tts_mode == "edge":
                raise RuntimeError(f"edge-tts failed: {exc}") from exc
            print(f"[tts fallback] edge-tts unavailable ({exc}), using macOS say.", file=sys.stderr)

    if raw_audio is None:
        aiff_tmp = os.path.join(tmp_dir, f"tts_{os.path.basename(out_wav)}.aiff")
        say_voice = "Tingting"
        subprocess.run(
            ["say", "-v", say_voice, "-r", "195", "-o", aiff_tmp, spoken_text or "。"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        raw_audio = aiff_tmp

    normalize_audio_file(raw_audio, out_wav, pad_sec=0.35)


def normalize_audio_file(in_path, out_wav, pad_sec=0.35):
    subprocess.run(
        [
            FFMPEG, "-y", "-i", in_path, "-vn",
            "-af", f"loudnorm=I=-16:TP=-1.5:LRA=9,aresample=48000,apad=pad_dur={pad_sec}",
            "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
            out_wav,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def extract_rms_envelope(wav_path, n_frames, fps):
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        samples = samples.reshape(-1, n_ch).mean(axis=1)
    hop = max(1, int(sr / fps))
    env = np.zeros(n_frames, dtype=np.float32)
    for i in range(n_frames):
        s0 = i * hop
        s1 = min(len(samples), s0 + hop)
        if s1 > s0:
            env[i] = float(np.sqrt(np.mean(samples[s0:s1] ** 2)))
    peak = max(1e-4, float(np.percentile(env, 95)))
    return np.clip(env / peak, 0.08, 1.0)


def generate_editorial_groove(total_dur, chapter_starts, wav_out, sr=48000):
    """Synthesize warm 112 BPM ambient editorial pad + subtle chapter chimes (for ducked background)."""
    n = int(round(total_dur * sr))
    t = np.arange(n, dtype=np.float32) / sr
    buf = np.zeros(n, dtype=np.float32)

    chords = [
        (220.0, 277.18, 329.63),
        (196.0, 246.94, 293.66),
        (174.61, 220.0, 261.63),
        (164.81, 196.0, 246.94),
    ]
    bar_len = 2.1428  # 112 BPM
    bar_idx = ((t / bar_len).astype(np.int32)) % len(chords)
    pad = np.zeros(n, dtype=np.float32)
    for ci, chord in enumerate(chords):
        m = (bar_idx == ci)
        tc = t[m]
        v = np.zeros_like(tc)
        for fi, freq in enumerate(chord):
            v += (0.24 if fi == 0 else 0.14) * np.sin(2 * np.pi * freq * tc)
        pad[m] = v

    beat_p = bar_len / 4.0
    bp = t % beat_p
    pulse = 0.12 * np.exp(-bp * 20.0) * np.sin(2 * np.pi * 65.0 * bp)
    tick_p = bar_len / 8.0
    tp = t % tick_p
    tick = 0.05 * np.exp(-tp * 32.0) * np.sin(2 * np.pi * 587.33 * tp)

    fade = np.minimum(1.0, t / 0.8) * np.minimum(1.0, np.maximum(0.0, (total_dur - t) / 1.2))
    buf += (pad * 0.20 + pulse + tick) * 0.24 * fade

    for st in chapter_starts[1:]:
        i0 = max(0, int((st - 0.05) * sr))
        i1 = min(n, int((st + 0.55) * sr))
        if i1 <= i0:
            continue
        seg = np.arange(i1 - i0, dtype=np.float32) / sr - 0.05
        mask = seg >= 0.0
        chime = np.zeros_like(seg)
        chime[mask] = 0.22 * np.exp(-seg[mask] * 8.0) * (
            np.sin(2 * np.pi * 523.25 * seg[mask]) + 0.5 * np.sin(2 * np.pi * 783.99 * seg[mask])
        )
        buf[i0:i1] += chime

    buf = np.clip(buf, -0.95, 0.95)
    pcm = (buf * 32767.0).astype(np.int16)
    stereo = np.stack([pcm, pcm], axis=1).tobytes()
    with wave.open(wav_out, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo)


def default_explainer_project():
    return {
        "series_title": "EDITORIAL DEEP DIVE · LONG-FORM EXPLAINER",
        "cta_text": "关注频道 · 获取完整原文精读模板",
        "tts": {"voice": "zh-CN-XiaoxiaoNeural", "mode": "auto"},
        "format": {"width": 1920, "height": 1080, "fps": 24},
        "chapters": [
            {
                "id": "ch_01",
                "layout": "title_hook",
                "short_title": "导读破题",
                "badge": "DEEP READING · ARTICLE EXPLAINER",
                "title": "为什么大多数 AI 视频一眼假？核心工程解法拆解",
                "subtitle": "从「冷蓝霓虹抽卡」走向 Anthropic 原教旨暖纸矢量叙事与语音驱动时间轴",
                "meta_chips": ["6 CHAPTERS", "AUDIO-DRIVEN TIMELINE", "ZERO-BLUE PALETTE"],
                "spoken_text": "为什么大多数 AI 生成的讲解视频总有一股廉价塑料味？今天我们用五分钟，把文章里的核心工程答案一次讲透。",
            },
            {
                "id": "ch_02",
                "layout": "quote_highlight",
                "short_title": "原文洞察",
                "eyebrow": "ORIGINAL EXCERPT · CORE INSIGHT",
                "source_label": "《去 AI 味视频工程指南》· 第一节",
                "quote_parts": [
                    {"text": "视觉廉价感往往源于 ", "highlight": False},
                    {"text": "暗夜深蓝叠加霓虹描边，", "highlight": True},
                    {"text": "而真正耐看的技术讲解依靠 ", "highlight": False},
                    {"text": "暖棉纸底板、精确字体层级与随声音推进的节奏留白。", "highlight": True},
                ],
                "side_title": "三大关键结论",
                "bullets": [
                    "75% 篇幅采用 #FAF9F5 暖棉纸，克制使用 #141413 暖碳黑反差",
                    "彻底清零冷青紫光晕，只留陶土橘 #D97757 作为焦点指示色",
                    "先出旁白音频，再按音频帧数反向锁定每一幕的弹簧时长",
                ],
                "spoken_text": "原文直接指出问题根源：深蓝夜景配合霓虹线条最容易显得廉价；耐看的深度解读靠暖棉纸底板、精密排版以及随语音同步高亮的留白节奏。",
            },
            {
                "id": "ch_03",
                "layout": "architecture_flow",
                "short_title": "架构拆解",
                "title": "语音驱动长视频的四步流水线",
                "subtitle": "不限章节数量，讲一分钟还是二十分钟都零音画漂移",
                "steps": [
                    {"tag": "PARSE", "title": "长文拆章", "desc": "读取 Markdown 段落与代码块，自动匹配 6 套暖调排版模板"},
                    {"tag": "VOICE", "title": "神经语音合成", "desc": "逐章生成高自然度旁白并抽出每帧 RMS 声浪包络与字幕分句"},
                    {"tag": "VECTOR", "title": "Chrome 矢量渲染", "desc": "无头浏览器按音频精确帧数驱动弹簧动效，杜绝边缘锯齿"},
                    {"tag": "MIX", "title": "侧链闪避混音", "desc": "FFmpeg 将人声、112 BPM 环境律动与转场提示音精准合并"},
                ],
                "spoken_text": "这套长视频流水线只有四步：第一步按 Markdown 自动拆章；第二步合成语音并锁定帧数；第三步由浏览器矢量引擎逐帧渲染；第四步完成侧链混音。",
            },
            {
                "id": "ch_04",
                "layout": "code_walkthrough",
                "short_title": "代码精讲",
                "title": "按音频精确锁定视频帧数",
                "code_filename": "explainer_studio.py",
                "code_lang": "PYTHON 3.10",
                "code_lines": [
                    "for idx, ch in enumerate(chapters):",
                    "    synthesize_chapter_wav(ch['spoken_text'], wav_path)",
                    "    dur_sec = get_duration(wav_path)",
                    "    n_frames = max(1, int(round(dur_sec * FPS)))",
                    "    rms_env = extract_rms_envelope(wav_path, n_frames, FPS)",
                    "    clauses = split_subtitle_clauses(ch['spoken_text'], dur_sec)",
                    "    for f in range(n_frames):",
                    "        page.evaluate('renderExplainerFrame', state)",
                    "        pipe.stdin.write(page.screenshot(type='jpeg'))",
                ],
                "annotations": [
                    {"lines": [2, 4], "line_label": "LINE 2-4 · 音画同步锁", "heading": "先算语音时长，再定渲染帧数", "body": "不再写死 9 秒时长，读完音频真实秒数后直接决定渲染多少帧。"},
                    {"lines": [5, 6], "line_label": "LINE 5-6 · 声浪与分句", "heading": "实测声波 + 标点分句字幕", "body": "抽出每帧 RMS 音量振幅驱动底部 28 根声浪柱，同步高亮字幕。"},
                    {"lines": [7, 9], "line_label": "LINE 7-9 · 零落盘直出", "heading": "浏览器内存管道直推 FFmpeg", "body": "无头 Chrome 截帧通过标准输入管道直写 H.264，支持任意长视频。"},
                ],
                "spoken_text": "看核心代码：第 2 到 4 行先合成每章语音再反推帧数；第 5 行提取声波和字幕分句；第 7 到 9 行直接用管道把浏览器矢量帧推给 FFmpeg 编码。",
            },
            {
                "id": "ch_05",
                "layout": "comparison_bento",
                "short_title": "方案对比",
                "title": "短平快口播 vs 长文精读工作流",
                "subtitle": "双引擎各自负责最擅长的场景",
                "items": [
                    {
                        "tag": "LEGACY SHORT",
                        "title": "旧版口播模式",
                        "metric_value": "45s",
                        "unit": "MAX OPTIMAL",
                        "metric_pct": 35,
                        "winner": False,
                        "points": ["依赖 Google Flow 网页逐段生成底片", "适合 3~5 段今日热点速报", "高密真人口播适合前 30 秒吸睛"],
                    },
                    {
                        "tag": "EXPLAINER MODE",
                        "title": "新版长文讲解引擎",
                        "metric_value": "∞",
                        "unit": "1–20+ MIN UNLIMITED",
                        "metric_pct": 94,
                        "winner": True,
                        "points": ["单篇 Markdown 直接拆解为 N 章长视频", "支持引用划重点、架构流程图、代码逐行聚焦", "零等待本地语音合成 + 0% 冷蓝矢量排版"],
                    },
                ],
                "spoken_text": "对比旧版口播与新版长文引擎：口播模式适合三十秒资讯开场，而新版讲解引擎支持不限章节长度、代码聚焦与图解卡片，轻松做出高质量深度长视频。",
            },
            {
                "id": "ch_06",
                "layout": "summary_takeaway",
                "short_title": "总结清单",
                "title": "一句指令，把任意文章变成深度视频",
                "takeaways": [
                    {"heading": "直接丢 Markdown 或文章", "body": "运行 from-markdown 或直接发一段文章给我，自动拆成 6 套排版章节。"},
                    {"heading": "零冷蓝 AI 味彻底清零", "body": "全程遵循暖棉纸、暖碳黑、陶土橘与阻尼弹簧运动物理规律。"},
                    {"heading": "音画永远严丝合缝", "body": "旁白多长，单章就精确铺满多少帧，再也不怕长视频音画不同步。"},
                ],
                "cta": "尝试对 AI 说：把这篇文章做成讲解视频",
                "spoken_text": "总结三条：你可以直接丢一篇文章运行转换命令，全片保持暖棉纸零冷蓝质感，并且每一章都严格锁定语音时长。快去试试看吧。",
            },
        ],
    }


def _extract_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[。！？!?])\s*", text or "") if s.strip()]


def markdown_to_explainer_project(md_text, title_fallback="文章精读讲解"):
    """Parse any Markdown article into an unlimited-chapter explainer project dict."""
    lines = md_text.splitlines()
    doc_title = title_fallback
    sections = []
    cur_sec = {"heading": "", "lines": []}

    for ln in lines:
        m1 = re.match(r"^\s*#\s+(.+)$", ln)
        m2 = re.match(r"^\s*##+\s+(.+)$", ln)
        if m1 and doc_title == title_fallback and not sections and not cur_sec["lines"]:
            doc_title = m1.group(1).strip()
        elif m2:
            if cur_sec["heading"] or cur_sec["lines"]:
                sections.append(cur_sec)
            cur_sec = {"heading": m2.group(1).strip(), "lines": []}
        else:
            cur_sec["lines"].append(ln)
    if cur_sec["heading"] or cur_sec["lines"]:
        sections.append(cur_sec)

    chapters = []
    # Chapter 1: cover hook from intro text (if present) or doc title
    intro_lines = []
    body_sections = sections
    if sections and not sections[0]["heading"]:
        intro_lines = [l.strip() for l in sections[0]["lines"] if l.strip()]
        body_sections = sections[1:]

    intro_text = " ".join(intro_lines[:2]) if intro_lines else f"今天带你深度拆解《{doc_title}》的核心逻辑与关键细节。"
    chapters.append({
        "id": "ch_01",
        "layout": "title_hook",
        "short_title": "文章导读",
        "badge": "MARKDOWN TO VIDEO · EXPLAINER",
        "title": doc_title,
        "subtitle": intro_text[:95],
        "meta_chips": [f"{len(body_sections) + 1} CHAPTERS", "AUDIO-DRIVEN", "ZERO-BLUE"],
        "spoken_text": f"今天我们来精读这篇内容：{doc_title}。{intro_text}",
    })

    for idx, sec in enumerate(body_sections, start=2):
        sec_text = "\n".join(sec["lines"]).strip()
        heading = sec["heading"] or f"核心章节 {idx}"
        short_t = re.sub(r"^[\d一二三四五六七八九十\.、\s]+", "", heading)[:10] or heading[:10]

        code_m = re.search(r"```([^\n]*)\n(.*?)```", sec_text, flags=re.S)
        quotes = [re.sub(r"^\s*>\s*", "", l).strip() for l in sec["lines"] if re.match(r"^\s*>\s*", l)]
        bullets = [re.sub(r"^\s*[-*•]\s+", "", l).strip() for l in sec["lines"] if re.match(r"^\s*[-*•]\s+", l)]
        plain_paras = [
            l.strip() for l in sec["lines"]
            if l.strip() and not l.strip().startswith((">", "-", "*", "```", "|"))
        ]
        prose = " ".join(plain_paras)
        spoken = f"接下来看{heading}。" + (" ".join(bullets[:4]) if bullets and not prose else prose[:180])
        if len(spoken) < 20 and quotes:
            spoken += " ".join(quotes)

        is_last = idx == len(body_sections) + 1
        if is_last and any(k in heading for k in ("总结", "结语", "结论", "Summary", "Takeaway", "总结与")):
            pts = bullets[:3] if bullets else _extract_sentences(prose)[:3]
            chapters.append({
                "id": f"ch_{idx:02d}",
                "layout": "summary_takeaway",
                "short_title": short_t,
                "title": heading,
                "takeaways": [{"heading": f"要点 {ti + 1}", "body": pt} for ti, pt in enumerate(pts)],
                "spoken_text": spoken,
            })
        elif code_m:
            lang = (code_m.group(1) or "CODE").strip().upper()
            clines = [c.rstrip() for c in code_m.group(2).splitlines() if c.strip()][:12]
            annos_src = bullets[:3] if bullets else _extract_sentences(prose)[:3]
            if not annos_src:
                annos_src = ["关注核心逻辑实现与关键参数说明"]
            step_n = max(1, len(clines) // len(annos_src))
            annos = []
            for ai, txt in enumerate(annos_src):
                s_ln = ai * step_n + 1
                e_ln = len(clines) if ai == len(annos_src) - 1 else min(len(clines), (ai + 1) * step_n)
                annos.append({"lines": [s_ln, e_ln], "line_label": f"LINES {s_ln}-{e_ln}", "heading": heading, "body": txt})
            chapters.append({
                "id": f"ch_{idx:02d}",
                "layout": "code_walkthrough",
                "short_title": short_t,
                "title": heading,
                "code_filename": "snippet",
                "code_lang": lang or "TEXT",
                "code_lines": clines,
                "annotations": annos,
                "spoken_text": spoken or f"这一节我们看代码实现：{heading}。",
            })
        elif quotes or idx % 3 == 2:
            q_full = " ".join(quotes) if quotes else (prose[:110] if prose else heading)
            mid = len(q_full) // 2
            chapters.append({
                "id": f"ch_{idx:02d}",
                "layout": "quote_highlight",
                "short_title": short_t,
                "source_label": f"{doc_title} · {heading}",
                "quote_parts": [
                    {"text": q_full[:mid], "highlight": False},
                    {"text": q_full[mid:], "highlight": True},
                ],
                "side_title": heading,
                "bullets": (bullets or _extract_sentences(prose))[:3] or [heading],
                "spoken_text": spoken,
            })
        else:
            step_items = (bullets or _extract_sentences(prose) or [heading])[:4]
            if len(step_items) < 2:
                step_items = [heading, step_items[0]]
            chapters.append({
                "id": f"ch_{idx:02d}",
                "layout": "architecture_flow",
                "short_title": short_t,
                "title": heading,
                "subtitle": plain_paras[0][:70] if plain_paras else "",
                "steps": [
                    {"tag": f"STEP {si + 1:02d}", "title": it[:16], "desc": it}
                    for si, it in enumerate(step_items)
                ],
                "spoken_text": spoken,
            })

    # Ensure closing summary dark card if author didn't include explicit summary section
    if chapters[-1]["layout"] != "summary_takeaway":
        tail_idx = len(chapters) + 1
        chapters.append({
            "id": f"ch_{tail_idx:02d}",
            "layout": "summary_takeaway",
            "short_title": "全文回顾",
            "title": f"《{doc_title}》核心回顾",
            "takeaways": [
                {"heading": c["short_title"], "body": c.get("title") or c.get("subtitle", "")}
                for c in chapters[1:4]
            ] or [{"heading": "核心洞察", "body": intro_text[:60]}],
            "cta": "订阅收藏 · 欢迎评论区交流",
            "spoken_text": f"以上就是《{doc_title}》的全部核心拆解，感谢观看，下期继续。",
        })

    return {
        "series_title": doc_title[:36].upper(),
        "cta_text": "订阅收藏 · 获取完整文章讲解",
        "tts": {"voice": "zh-CN-XiaoxiaoNeural", "mode": "auto"},
        "format": {"width": 1920, "height": 1080, "fps": 24},
        "chapters": chapters,
    }


def cmd_init(args):
    proj = default_explainer_project()
    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=2)
    print(f"Initialized 6-chapter explainer project -> {out_path}")


def cmd_from_markdown(args):
    md_path = os.path.abspath(args.input)
    if not os.path.exists(md_path):
        print(f"Error: Markdown file not found: {md_path}", file=sys.stderr)
        sys.exit(1)
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    stem = os.path.splitext(os.path.basename(md_path))[0]
    proj = markdown_to_explainer_project(md_text, title_fallback=stem)
    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=2)
    print(f"Converted Markdown ({len(proj['chapters'])} chapters) -> {out_path}")


def cmd_render(args):
    proj_path = os.path.abspath(args.project)
    if not os.path.exists(proj_path):
        print(f"Error: project file not found: {proj_path}", file=sys.stderr)
        sys.exit(1)
    with open(proj_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    proj_dir = os.path.dirname(proj_path)
    chapters = cfg.get("chapters", [])
    if not chapters:
        print("Error: explainer project has zero chapters.", file=sys.stderr)
        sys.exit(1)

    fmt = cfg.get("format", {})
    W = int(fmt.get("width", 1920))
    H = int(fmt.get("height", 1080))
    FPS = int(args.fps or fmt.get("fps", 24))
    tts_cfg = cfg.get("tts", {})
    voice = args.voice or tts_cfg.get("voice", "zh-CN-XiaoxiaoNeural")
    tts_mode = args.tts or tts_cfg.get("mode", "auto")

    stage_html = find_stage_html(args.stage)
    out_path = os.path.abspath(args.output) if args.output else os.path.join(proj_dir, "output", "《文章精读讲解》成片.mp4")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_dir = os.path.join(os.path.dirname(out_path), f".tmp_explainer_{os.getpid()}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        # 1. Build per-chapter normalized WAV audio + derive exact frame counts, RMS envelopes, and clauses
        ch_wavs, ch_durs, ch_frames, ch_envs, ch_clauses = [], [], [], [], []
        for idx, ch in enumerate(chapters):
            wav_p = os.path.join(tmp_dir, f"ch_{idx:03d}.wav")
            custom_audio = ch.get("audio_path", "").strip()
            if custom_audio:
                abs_aud = custom_audio if os.path.isabs(custom_audio) else os.path.normpath(os.path.join(proj_dir, custom_audio))
                if not os.path.exists(abs_aud):
                    raise FileNotFoundError(f"Missing chapter audio_path: {abs_aud}")
                normalize_audio_file(abs_aud, wav_p, pad_sec=0.35)
            else:
                synthesize_chapter_wav(ch.get("spoken_text", ""), wav_p, voice=voice, tts_mode=tts_mode, tmp_dir=tmp_dir)

            dur = get_duration(wav_p)
            nf = max(1, int(round(dur * FPS)))
            env = extract_rms_envelope(wav_p, nf, FPS)
            clauses = split_subtitle_clauses(ch.get("spoken_text", ""), dur)

            ch_wavs.append(wav_p)
            ch_durs.append(nf / float(FPS))
            ch_frames.append(nf)
            ch_envs.append(env)
            ch_clauses.append(clauses)

        total_frames = sum(ch_frames)
        total_dur = total_frames / float(FPS)
        chapter_starts = []
        acc_t = 0.0
        for d in ch_durs:
            chapter_starts.append(acc_t)
            acc_t += d

        print(f"Rendering {len(chapters)} chapters ({total_dur:.2f}s, {total_frames} frames @ {FPS}fps, {W}x{H})...")

        # 2. Concatenate speech WAVs & generate ducked ambient music + chapter chimes
        concat_wav = os.path.join(tmp_dir, "narration_concat.wav")
        list_txt = os.path.join(tmp_dir, "wav_list.txt")
        with open(list_txt, "w", encoding="utf-8") as lf:
            for wv in ch_wavs:
                lf.write(f"file '{wv}'\n")
        subprocess.run(
            [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-t", f"{total_dur:.3f}", "-c:a", "pcm_s16le", concat_wav],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        bgm_wav = os.path.join(tmp_dir, "editorial_bgm.wav")
        generate_editorial_groove(total_dur, chapter_starts, bgm_wav)

        # 3. Render every frame through Headless Chrome (Playwright) piped straight to FFmpeg
        silent_mp4 = os.path.join(tmp_dir, "explainer_silent.mp4")
        pipe = subprocess.Popen(
            [
                FFMPEG, "-y",
                "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(FPS), "-i", "-",
                "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
                silent_mp4,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=CHROME_BIN,
                headless=True,
                args=["--use-gl=angle", "--use-angle=metal", "--disable-gpu-sandbox"],
            )
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.goto(f"file://{stage_html}")
            page.evaluate(
                "meta => window.initExplainer(meta)",
                {
                    "series_title": cfg.get("series_title", "LONG-FORM EXPLAINER"),
                    "cta_text": cfg.get("cta_text", ""),
                    "chapters": chapters,
                },
            )

            global_frame = 0
            for ch_idx, nf in enumerate(ch_frames):
                dur = ch_durs[ch_idx]
                env = ch_envs[ch_idx]
                clauses = ch_clauses[ch_idx]
                for lf in range(nf):
                    lt = lf / float(FPS)
                    gt = global_frame / float(FPS)
                    sub_txt = clauses[-1]["text"]
                    for cl in clauses:
                        if lt < cl["end"]:
                            sub_txt = cl["text"]
                            break
                    page.evaluate(
                        "st => window.renderExplainerFrame(st)",
                        {
                            "chapterIndex": ch_idx,
                            "chapterTime": lt,
                            "chapterProgress": lt / max(0.01, dur),
                            "globalTime": gt,
                            "globalProgress": gt / max(0.01, total_dur),
                            "totalDuration": total_dur,
                            "rms": float(env[lf]),
                            "subtitle": sub_txt,
                        },
                    )
                    pipe.stdin.write(page.screenshot(type="jpeg", quality=92))
                    global_frame += 1

            browser.close()

        pipe.stdin.close()
        pipe.stdin = None
        _, err = pipe.communicate()
        if pipe.returncode != 0:
            raise RuntimeError("FFmpeg frame pipe error: " + err.decode("utf-8", "ignore")[-1000:])

        # 4. Sidechain-ducked narration + ambient editorial mix
        subprocess.run(
            [
                FFMPEG, "-y",
                "-i", silent_mp4,
                "-i", concat_wav,
                "-i", bgm_wav,
                "-filter_complex",
                "[1:a]asplit=2[v1][v2];"
                "[2:a][v1]sidechaincompress=threshold=0.04:ratio=4.0:attack=15:release=220:makeup=1.0[bgm_duck];"
                "[bgm_duck]volume=0.72[bgm];"
                "[v2][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart",
                out_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        print(f"Rendered long-form explainer video ({total_dur:.2f}s, {W}x{H}): {out_path}")
    finally:
        if not args.keep_temp and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def cmd_verify(args):
    video_path = os.path.abspath(args.video)
    if not os.path.exists(video_path):
        print(f"Error: video file not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    grid_path = os.path.abspath(args.output) if args.output else os.path.join(os.path.dirname(video_path), "verify_explainer_grid.jpg")
    os.makedirs(os.path.dirname(grid_path), exist_ok=True)

    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", video_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    v_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v_stream or not a_stream:
        print("Error: output video missing video or audio stream.", file=sys.stderr)
        sys.exit(1)

    dur = float(info["format"]["duration"])
    W = int(v_stream["width"])
    H = int(v_stream["height"])
    size_bytes = int(info["format"]["size"])

    cap = cv2.VideoCapture(video_path)
    total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)

    sample_fracs = [0.06, 0.22, 0.36, 0.50, 0.64, 0.78, 0.86, 0.92, 0.97]
    cell_w, cell_h = 640, int(640 * H / W)
    cols, rows = 3, 3
    grid = np.full((rows * cell_h, cols * cell_w, 3), (19, 20, 20), dtype=np.uint8)
    blue_pcts = []

    for idx, frac in enumerate(sample_fracs):
        f_idx = min(max(0, total_f - 1), int(frac * total_f))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, fr = cap.read()
        if not ret:
            print(f"Error: failed to decode frame {f_idx}", file=sys.stderr)
            sys.exit(1)
        b_ch, g_ch, r_ch = fr[:, :, 0].astype(np.int16), fr[:, :, 1].astype(np.int16), fr[:, :, 2].astype(np.int16)
        blue_pcts.append(float(np.mean((b_ch - r_ch > 15) & (b_ch - g_ch > 8)) * 100.0))

        thumb = cv2.resize(fr, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        r_idx, c_idx = divmod(idx, cols)
        y0, x0 = r_idx * cell_h, c_idx * cell_w
        grid[y0:y0 + cell_h, x0:x0 + cell_w] = thumb
        cv2.putText(grid, f"#{idx + 1} ({f_idx / fps:.1f}s)", (x0 + 16, y0 + 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, (87, 119, 217), 2, cv2.LINE_AA)
    cap.release()

    cv2.imwrite(grid_path, grid, [cv2.IMWRITE_JPEG_QUALITY, 92])
    report = {
        "video_path": video_path,
        "width": W,
        "height": H,
        "fps": round(fps, 2),
        "duration_sec": round(dur, 3),
        "size_bytes": size_bytes,
        "video_codec": v_stream.get("codec_name"),
        "audio_codec": a_stream.get("codec_name"),
        "cold_blue_pixel_pct": round(float(max(blue_pcts)), 4) if blue_pcts else 0.0,
        "verify_grid": grid_path,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Long-Form Article Explainer Studio CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create starter 6-chapter explainer_project.json")
    p_init.add_argument("--output", "-o", required=True)
    p_init.set_defaults(func=cmd_init)

    p_md = sub.add_parser("from-markdown", help="Convert Markdown (.md) article to explainer_project.json")
    p_md.add_argument("--input", "-i", required=True)
    p_md.add_argument("--output", "-o", required=True)
    p_md.set_defaults(func=cmd_from_markdown)

    p_ren = sub.add_parser("render", help="Render long-form explainer MP4")
    p_ren.add_argument("--project", "-p", required=True)
    p_ren.add_argument("--output", "-o", default="")
    p_ren.add_argument("--voice", default="")
    p_ren.add_argument("--tts", choices=["auto", "edge", "macos"], default="")
    p_ren.add_argument("--fps", type=int, default=0)
    p_ren.add_argument("--stage", default="")
    p_ren.add_argument("--keep-temp", action="store_true")
    p_ren.set_defaults(func=cmd_render)

    p_ver = sub.add_parser("verify", help="Verify MP4 streams & cold_blue_pixel_pct")
    p_ver.add_argument("--video", "-v", required=True)
    p_ver.add_argument("--output", "-o", default="")
    p_ver.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
