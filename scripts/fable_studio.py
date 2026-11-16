#!/Library/Frameworks/Python.framework/Versions/3.10/bin/python3
"""
Fable Studio CLI — End-to-end editorial motion-graphics video studio for Google Flow (Veo) + Python/FFmpeg.

Subcommands:
  init       Initialize a starter project.json (3/4/5 segments, 16:9 or 9:16).
  flow-plan  Auto de-risk spoken scripts and export Google Flow Web UI prompts + anchor instructions.
  render     Composite raw Veo clips with Fable 5.2 motion graphics, mattes, 3D lens/timeline, and mixed audio.
  verify     Inspect rendered MP4 via ffprobe and build a multi-beat contact sheet verify_grid.jpg.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

FONT_MONO = "/System/Library/Fonts/SFNSMono.ttf"
FONT_SANS = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_HEITI = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_SONGTI = "/System/Library/Fonts/Supplemental/Songti.ttc"
FONT_SERIF_IT = "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf"
FONT_SERIF_BD = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"

# Anthropic Warm Editorial Palette (Zero-Blue Rule)
RGB_TERRA = (217, 119, 87)     # #D97757 Terracotta
BGR_TERRA = (87, 119, 217)
RGB_CARBON = (20, 20, 19)      # #141413 Warm Carbon
BGR_CARBON = (19, 20, 20)
RGB_PARC = (250, 249, 245)     # #FAF9F5 Warm Parchment
BGR_PARC = (245, 249, 250)
RGB_BONE = (243, 241, 234)     # #F3F1EA Bone Paper
BGR_BONE = (234, 241, 243)
RGB_SAGE = (120, 140, 93)      # #788C5D Botanical Sage
BGR_SAGE = (93, 140, 120)
RGB_RULE = (223, 220, 210)     # #DFDCD2 1px Rule Hairline
BGR_RULE = (210, 220, 223)
RGB_STONE = (218, 214, 204)    # Warm Stone text on dark carbon
BGR_SAND = (222, 232, 238)     # #EEE8DE Sand viewport backdrop

DEFAULT_SAFETY_REPLACEMENTS = {
    "OpenAI": "智能对话平台",
    "ChatGPT": "智能对话",
    "Anthropic": "克劳德团队",
    "Claude": "克劳德",
    "Gemini": "双子星",
    "DeepSeek": "深度求索",
    "NASDAQ": "商业市场",
    "IPO": "商业扩展",
    "上市": "商业化拓展",
    "新闻记者": "视频创作者",
    "记者": "创作者",
    "报道": "分享",
    "股票": "市场",
    "时事": "行业动态",
}


def get_duration(path):
    res = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return float(res.stdout.strip())


def ease_out_cubic(x):
    x = max(0.0, min(1.0, x))
    return 1.0 - pow(1.0 - x, 3)


def ease_in_out_cubic(x):
    x = max(0.0, min(1.0, x))
    return 4 * x * x * x if x < 0.5 else 1 - pow(-2 * x + 2, 3) / 2


def ease_out_back(x):
    x = max(0.0, min(1.0, x))
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * pow(x - 1, 3) + c1 * pow(x - 1, 2)


def spring_ease(t, stiffness=180.0, damping=22.0):
    """Damped harmonic oscillator easing curve matching CSS physics springs."""
    if t <= 0.0:
        return 0.0
    w0 = math.sqrt(stiffness)
    zeta = damping / (2.0 * w0)
    if zeta < 1.0:
        wd = w0 * math.sqrt(1.0 - zeta * zeta)
        env = math.exp(-zeta * w0 * t)
        return 1.0 - env * (math.cos(wd * t) + (zeta * w0 / wd) * math.sin(wd * t))
    return 1.0 - (1.0 + w0 * t) * math.exp(-w0 * t)


def fit_font(draw, text, font_path, index, max_size, min_size, max_width):
    """Return the largest TrueType font size in [min_size, max_size] whose rendered width <= max_width."""
    for sz in range(int(max_size), int(min_size) - 1, -4):
        kw = {"index": index} if font_path.endswith(".ttc") and index is not None else {}
        fnt = ImageFont.truetype(font_path, sz, **kw)
        bb = draw.textbbox((0, 0), text, font=fnt)
        if (bb[2] - bb[0]) <= max_width:
            return fnt
    kw = {"index": index} if font_path.endswith(".ttc") and index is not None else {}
    return ImageFont.truetype(font_path, int(min_size), **kw)


def draw_hud(draw, label, W, H, dot_color=RGB_TERRA, text_color=RGB_PARC):
    is_vert = H > W
    x = 46 if is_vert else 54
    y = 66 if is_vert else 42
    sz = 18 if is_vert else 20
    f_mono = ImageFont.truetype(FONT_MONO, sz)
    draw.ellipse([x, y + 5, x + 10, y + 15], fill=dot_color)
    draw.text((x + 22, y), label, font=f_mono, fill=text_color)


def paste_rgba_on_bgr(bg_bgr, fg_rgba):
    """Alpha-composite uint8 RGBA PIL Image onto uint8 BGR OpenCV array."""
    fg_arr = np.array(fg_rgba)
    alpha = fg_arr[:, :, 3:4].astype(np.float32) / 255.0
    fg_bgr = np.ascontiguousarray(fg_arr[:, :, [2, 1, 0]]).astype(np.float32)
    out = bg_bgr.astype(np.float32) * (1.0 - alpha) + fg_bgr * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def composite_alpha_bgr(bg_bgr, fg_bgr, alpha_01, W, H, offset_x=0):
    """Composite fg_bgr onto bg_bgr using float32 alpha mask [H,W] in [0,1] shifted horizontally."""
    if offset_x != 0:
        M = np.float32([[1, 0, offset_x], [0, 1, 0]])
        fg_s = cv2.warpAffine(fg_bgr, M, (W, H), borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        a_s = cv2.warpAffine(alpha_01, M, (W, H), borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    else:
        fg_s = fg_bgr
        a_s = alpha_01
    a3 = a_s[:, :, None]
    out = bg_bgr.astype(np.float32) * (1.0 - a3) + fg_s.astype(np.float32) * a3
    return np.clip(out, 0, 255).astype(np.uint8)


def segment_sage_green(frame_bgr, W, H, bg_lab_ema=None):
    """Extract clean foreground presenter matte from a solid sage-green wall studio clip."""
    small_w, small_h = (960, 540) if W >= H else (540, 960)
    small = cv2.resize(frame_bgr, (small_w, small_h), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)

    s_w_box = max(60, int(small_w * 0.18))
    s_h_box = max(80, int(small_h * 0.25))
    samples = np.concatenate([
        lab[20:20 + s_h_box, 20:20 + s_w_box].reshape(-1, 3),
        lab[20:20 + s_h_box, small_w - 20 - s_w_box:small_w - 20].reshape(-1, 3),
    ], axis=0)
    cur_bg = np.median(samples, axis=0)
    bg_lab = cur_bg if bg_lab_ema is None else (0.85 * bg_lab_ema + 0.15 * cur_bg)

    dL = (lab[:, :, 0] - bg_lab[0]) * 0.45
    da = (lab[:, :, 1] - bg_lab[1]) * 2.1
    db = (lab[:, :, 2] - bg_lab[2]) * 2.1
    dist = np.sqrt(dL * dL + da * da + db * db)
    fg_raw = (dist > 14.5).astype(np.uint8) * 255

    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k9 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    fg_clean = cv2.morphologyEx(fg_raw, cv2.MORPH_OPEN, k5)
    fg_clean = cv2.morphologyEx(fg_clean, cv2.MORPH_CLOSE, k9)

    num_lbl, labels, stats, _ = cv2.connectedComponentsWithStats(fg_clean)
    core_roi = labels[int(small_h * 0.22):int(small_h * 0.88), int(small_w * 0.30):int(small_w * 0.70)]
    keep = np.zeros_like(fg_clean)
    for lbl in np.unique(core_roi):
        if lbl > 0 and stats[lbl, cv2.CC_STAT_AREA] > 3000:
            keep[labels == lbl] = 255

    alpha = cv2.resize(keep, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    alpha = cv2.GaussianBlur(alpha, (7, 7), 1.6)
    alpha = np.clip((alpha - 0.12) / 0.78, 0.0, 1.0)

    out_bgr = frame_bgr.astype(np.float32)
    b, g, r = out_bgr[:, :, 0], out_bgr[:, :, 1], out_bgr[:, :, 2]
    edge_band = ((alpha > 0.02) & (alpha < 0.98)).astype(np.float32)
    avg_rb = 0.5 * (r + b)
    excess_g = np.maximum(0.0, g - avg_rb)
    out_bgr[:, :, 1] = g - excess_g * 0.55 * edge_band
    return np.clip(out_bgr, 0, 255).astype(np.uint8), alpha, bg_lab


def compute_dark_subject_mask(frame_bgr, face_cascade, W, H):
    """GrabCut foreground segmentation on a Dark Editing Suite frame, face-anchored."""
    sw, sh = (480, 270) if W >= H else (270, 480)
    small = cv2.resize(frame_bgr, (sw, sh), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(32, 32))
    if len(faces) > 0:
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        cx = fx + fw // 2
    else:
        fx, fy, fw, fh = int(sw * 0.43), int(sh * 0.22), int(sw * 0.14), int(sh * 0.18)
        cx = sw // 2

    mask = np.full((sh, sw), cv2.GC_PR_BGD, dtype=np.uint8)
    mask[:, :max(10, cx - int(fw * 1.9))] = cv2.GC_BGD
    mask[:, min(sw - 10, cx + int(fw * 1.9)):] = cv2.GC_BGD
    mask[:max(4, fy - int(fh * 0.45)), :] = cv2.GC_BGD

    x0 = max(0, cx - int(fw * 1.45))
    x1 = min(sw, cx + int(fw * 1.45))
    y0 = max(0, fy - int(fh * 0.30))
    mask[y0:, x0:x1] = cv2.GC_PR_FGD
    core_x0 = max(0, cx - int(fw * 0.35))
    core_x1 = min(sw, cx + int(fw * 0.35))
    mask[fy + int(fh * 0.15):, core_x0:core_x1] = cv2.GC_FGD

    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(small, mask, None, bgd, fgd, 4, cv2.GC_INIT_WITH_MASK)
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)

    num_lbl, labels, stats, _ = cv2.connectedComponentsWithStats(fg)
    roi = labels[int(sh * 0.40):int(sh * 0.88), max(0, cx - 20):min(sw, cx + 20)]
    keep = np.zeros_like(fg)
    for lbl in np.unique(roi):
        if lbl > 0 and stats[lbl, cv2.CC_STAT_AREA] > 1000:
            keep[labels == lbl] = 255
    keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    alpha = cv2.resize(keep, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    alpha = cv2.GaussianBlur(alpha, (9, 9), 2.2)
    return np.clip((alpha - 0.15) / 0.72, 0.0, 1.0)


def extract_rms_envelope(wav_path, n_frames, fps=24):
    """Extract normalized per-frame RMS energy envelope [0..1] for voice waveform."""
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


def normalize_and_extract_wav(in_mp4, out_mp4, out_wav, W, H, FPS):
    """Normalize speech clip to WxH FPS and -16 LUFS stereo audio + WAV envelope file."""
    cmd = [
        FFMPEG, "-y", "-i", in_mp4,
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=9,aresample=48000",
        "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        out_mp4,
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    subprocess.run(
        [FFMPEG, "-y", "-i", out_mp4, "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2", out_wav],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )


def render_3d_lens_portal_frame(t_in_broll, t_dur, timeline_bgr, W, H):
    """Render warm carbon metallic cinema lens with opening 8-blade brass/terracotta iris revealing timeline_bgr."""
    prog = min(1.0, t_in_broll / max(0.01, t_dur))
    ease_p = ease_in_out_cubic(prog)
    cx, cy = W // 2, H // 2
    min_dim = min(W, H)
    max_dim = max(W, H)
    zoom = 1.0 + 1.85 * (ease_p ** 1.4)
    outer_r = int(0.454 * min_dim * zoom)

    lens_pil = Image.new("RGBA", (W, H), RGB_CARBON + (255,))
    ld = ImageDraw.Draw(lens_pil)

    ld.ellipse([cx - outer_r - 14, cy - outer_r - 14, cx + outer_r + 14, cy + outer_r + 14],
               fill=(28, 27, 25, 255), outline=(217, 119, 87, 210), width=max(2, int(6 * zoom)))
    ld.ellipse([cx - int(outer_r * 0.88), cy - int(outer_r * 0.88),
                cx + int(outer_r * 0.88), cy + int(outer_r * 0.88)],
               fill=(36, 34, 31, 255), outline=(140, 134, 122, 180), width=max(2, int(4 * zoom)))

    rot_deg = -25.0 + 38.0 * ease_p
    for deg in range(0, 360, 6):
        rad = math.radians(deg + rot_deg)
        r0 = outer_r * 0.90
        r1 = outer_r * (0.95 if deg % 30 == 0 else 0.925)
        x0, y0 = cx + r0 * math.cos(rad), cy + r0 * math.sin(rad)
        x1, y1 = cx + r1 * math.cos(rad), cy + r1 * math.sin(rad)
        col = (240, 225, 205, 200) if deg % 30 == 0 else (160, 154, 142, 120)
        ld.line([(x0, y0), (x1, y1)], fill=col, width=max(1, int(2 * zoom)))

    glass_r = int(outer_r * 0.72)
    ld.ellipse([cx - glass_r, cy - glass_r, cx + glass_r, cy + glass_r],
               fill=RGB_CARBON + (255,), outline=(217, 119, 87, 180), width=3)

    if outer_r < 2200:
        ld.arc([cx - int(outer_r * 0.96), cy - int(outer_r * 0.96),
                cx + int(outer_r * 0.96), cy + int(outer_r * 0.96)],
               start=-35 + rot_deg, end=25 + rot_deg, fill=(217, 119, 87, 180), width=max(2, int(8 * zoom)))
        ld.arc([cx - int(outer_r * 0.82), cy - int(outer_r * 0.82),
                cx + int(outer_r * 0.82), cy + int(outer_r * 0.82)],
               start=145 + rot_deg, end=205 + rot_deg, fill=(120, 140, 93, 150), width=max(2, int(5 * zoom)))

    iris_r = int(55 + max_dim * 0.68 * (ease_p ** 2.1))
    n_blades = 8
    iris_pts = []
    for bi in range(n_blades):
        a = 2 * math.pi * bi / n_blades + math.radians(rot_deg * 0.7)
        iris_pts.append((cx + iris_r * math.cos(a), cy + iris_r * math.sin(a)))

    blade_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(blade_mask).polygon(iris_pts, fill=255)
    mask_arr = cv2.GaussianBlur(np.array(blade_mask).astype(np.float32) / 255.0, (7, 7), 1.5)

    lens_bgr = np.ascontiguousarray(np.array(lens_pil)[:, :, [2, 1, 0]]).astype(np.float32)
    m3 = mask_arr[:, :, None]
    out = timeline_bgr.astype(np.float32) * m3 + lens_bgr * (1.0 - m3)
    out_u8 = np.ascontiguousarray(np.clip(out, 0, 255).astype(np.uint8))
    cv2.polylines(out_u8, [np.array(iris_pts, dtype=np.int32)], True, (145, 165, 190), 2, cv2.LINE_AA)
    return out_u8


def render_3d_timeline_world(t_scroll, thumb_strips, live_monitor_bgr, W, H, zoom_in=0.0):
    """Build Anthropic architectural 3-track NLE timeline + Double-Bezel studio viewport on Warm Parchment."""
    is_vert = H > W
    canvas = Image.new("RGBA", (W, H), RGB_PARC + (255,))
    cd = ImageDraw.Draw(canvas)

    # Subtle 1px drafting grid + outer architectural frame
    grid_col = (238, 235, 227, 255)
    for gx in range(120, W, 120):
        cd.line([(gx, 26), (gx, H - 26)], fill=grid_col, width=1)
    for gy in range(120, H, 120):
        cd.line([(26, gy), (W - 26, gy)], fill=grid_col, width=1)
    cd.rectangle([26, 26, W - 26, H - 26], outline=RGB_RULE + (255,), width=1)

    if is_vert:
        bx0, by0, bx1, by1 = 64, 148, W - 64, 868
        tx0, ty0, tx1, ty1 = 64, 902, W - 64, 1590
        mw, mh = 350, 622
        label_w = 180
    else:
        bx0, by0, bx1, by1 = 96, 88, W - 96, 566
        tx0, ty0, tx1, ty1 = 96, 590, W - 96, 912
        mw, mh = 720, 405
        label_w = 220

    # Upper Studio Viewport Double-Bezel card
    cd.rounded_rectangle([bx0, by0, bx1, by1], radius=22, fill=RGB_BONE + (255,), outline=RGB_RULE + (255,), width=1)
    cd.rounded_rectangle([bx0 + 10, by0 + 10, bx1 - 10, by1 - 10], radius=16, fill=(238, 232, 222, 255), outline=RGB_RULE + (255,), width=1)

    mx0 = (W - mw) // 2
    my0 = by0 + (by1 - by0 - mh) // 2

    # Top-right auto-follow badge inside studio viewport
    f_mono_sm = ImageFont.truetype(FONT_MONO, 15 if not is_vert else 14)
    z_disp = 1.0 + 0.45 * ease_in_out_cubic(min(1.0, t_scroll / 2.2))
    badge_txt = f"AUTO-FOLLOW · {z_disp:.2f}x"
    bw_px = cd.textbbox((0, 0), badge_txt, font=f_mono_sm)[2] + 28
    cd.rounded_rectangle([bx1 - 24 - bw_px, by0 + 22, bx1 - 24, by0 + 54], radius=8, fill=RGB_CARBON + (255,))
    cd.text((bx1 - 24 - bw_px + 14, by0 + 29), badge_txt, font=f_mono_sm, fill=RGB_TERRA + (255,))

    # Telemetry metadata on left of viewport (landscape)
    if not is_vert:
        cd.text((bx0 + 28, by0 + 28), "STUDIO VIEWPORT · 60 FPS", font=f_mono_sm, fill=(104, 102, 94, 255))
        cd.text((bx0 + 28, by1 - 46), "DOUBLE-BEZEL · ZERO-BLUE", font=f_mono_sm, fill=(104, 102, 94, 255))

    # Lower Architectural 3-Track NLE Timeline Board
    cd.rounded_rectangle([tx0, ty0, tx1, ty1], radius=16, fill=(255, 255, 255, 255), outline=RGB_RULE + (255,), width=1)
    ruler_h = 34
    cd.rectangle([tx0 + 1, ty0 + 1, tx1 - 1, ty0 + ruler_h], fill=RGB_BONE + (255,))
    cd.line([(tx0, ty0 + ruler_h), (tx1, ty0 + ruler_h)], fill=RGB_RULE + (255,), width=1)

    f_mono_xs = ImageFont.truetype(FONT_MONO, 13 if not is_vert else 12)
    lane_x0 = tx0 + label_w
    lane_x1 = tx1 - 16
    lane_w = lane_x1 - lane_x0 - 16

    for ti, ts_lbl in enumerate(["00:00", "00:02", "00:04", "00:06", "00:08", "00:10"]):
        rx = lane_x0 + 16 + int((lane_w - 42) * ti / 5)
        cd.text((rx, ty0 + 9), ts_lbl, font=f_mono_xs, fill=(116, 112, 104, 255))

    track_area_h = ty1 - (ty0 + ruler_h)
    tr_h = track_area_h // 3
    cd.line([(lane_x0, ty0 + ruler_h), (lane_x0, ty1)], fill=RGB_RULE + (255,), width=1)

    tracks = [
        ("01 / VIDEO TRIM", (92, 90, 82, 255)),
        ("02 / ZOOM · Z", RGB_TERRA + (255,)),
        ("03 / VOICE 48K", RGB_SAGE + (255,)),
    ]
    for idx, (lbl, lcol) in enumerate(tracks):
        ty_top = ty0 + ruler_h + idx * tr_h
        ty_bot = ty1 if idx == 2 else (ty_top + tr_h)
        if idx > 0:
            cd.line([(tx0, ty_top), (tx1, ty_top)], fill=RGB_RULE + (255,), width=1)
        cd.text((tx0 + 20, ty_top + tr_h // 2 - 8), lbl, font=f_mono_xs, fill=lcol)

    # Track 1 clip bars (scrolling warm sand blocks)
    t1_y0 = ty0 + ruler_h + 14
    t1_y1 = ty0 + ruler_h + tr_h - 14
    c1_x0 = lane_x0 + 18
    c1_x1 = lane_x0 + 18 + int(lane_w * 0.62)
    c2_x0 = c1_x1 + 12
    c2_x1 = lane_x1
    cd.rounded_rectangle([c1_x0, t1_y0, c1_x1, t1_y1], radius=8, fill=(238, 232, 222, 255), outline=RGB_RULE + (255,), width=1)
    cd.rectangle([c1_x0, t1_y0 + 4, c1_x0 + 6, t1_y1 - 4], fill=RGB_TERRA + (255,))
    cd.rectangle([c1_x1 - 6, t1_y0 + 4, c1_x1, t1_y1 - 4], fill=RGB_TERRA + (255,))
    cd.rounded_rectangle([c2_x0, t1_y0, c2_x1, t1_y1], radius=8, fill=RGB_BONE + (255,), outline=RGB_RULE + (255,), width=1)
    cd.text((c2_x0 + 16, (t1_y0 + t1_y1) // 2 - 7), "SPEED · 1.25x", font=f_mono_xs, fill=(104, 102, 94, 255))

    # Track 3 botanical sage 48kHz audio lane
    t3_y0 = ty0 + ruler_h + 2 * tr_h + 14
    t3_y1 = ty1 - 14
    cd.rounded_rectangle([c1_x0, t3_y0, lane_x1, t3_y1], radius=8, fill=RGB_SAGE + (255,))
    n_wf = max(24, lane_w // 12)
    ymid3 = (t3_y0 + t3_y1) // 2
    for wi in range(n_wf):
        wx = c1_x0 + 14 + wi * 12
        if wx > lane_x1 - 14:
            break
        wh = int(6 + 18 * abs(math.sin(wi * 0.55 + t_scroll * 1.2) * math.cos(wi * 0.21)))
        cd.rectangle([wx, ymid3 - wh, wx + 4, ymid3 + wh], fill=RGB_PARC + (210,))

    world_bgr = np.ascontiguousarray(np.array(canvas)[:, :, [2, 1, 0]])

    # Paste real presenter monitor inside Studio Viewport with crisp white double border
    mon_img = cv2.resize(live_monitor_bgr, (mw, mh), interpolation=cv2.INTER_AREA)
    cv2.rectangle(world_bgr, (mx0 - 8, my0 - 8), (mx0 + mw + 8, my0 + mh + 8), (255, 255, 255), -1)
    cv2.rectangle(world_bgr, (mx0 - 8, my0 - 8), (mx0 + mw + 8, my0 + mh + 8), BGR_RULE, 2)
    world_bgr[my0:my0 + mh, mx0:mx0 + mw] = mon_img

    # Filmstrip thumbnails inside Track 1 clip block (warm-neutralized cleanly)
    n_thumbs = max(2, (c1_x1 - c1_x0 - 24) // 84)
    th_h = max(24, t1_y1 - t1_y0 - 12)
    th_w = 76
    for ti in range(n_thumbs):
        src_t = thumb_strips[(ti + int(t_scroll * 2)) % len(thumb_strips)]
        trs = cv2.resize(src_t, (th_w, th_h), interpolation=cv2.INTER_AREA)
        tx_p = c1_x0 + 14 + ti * 84
        if tx_p + th_w <= c1_x1 - 12:
            world_bgr[t1_y0 + 6:t1_y0 + 6 + th_h, tx_p:tx_p + th_w] = trs

    # Track 2 anti-aliased Bezier S-curve zoom envelope + keyframe dots
    t2_y0 = ty0 + ruler_h + tr_h + 14
    t2_y1 = ty0 + ruler_h + 2 * tr_h - 14
    cv2.line(world_bgr, (c1_x0, (t2_y0 + t2_y1) // 2), (lane_x1, (t2_y0 + t2_y1) // 2), BGR_RULE, 1)
    curve_pts = []
    for k in range(121):
        u = k / 120.0
        px = int(c1_x0 + u * (lane_x1 - c1_x0))
        if u < 0.22:
            yv = 0.85
        elif u < 0.42:
            yv = 0.85 - 0.70 * ease_in_out_cubic((u - 0.22) / 0.20)
        elif u < 0.74:
            yv = 0.15
        elif u < 0.90:
            yv = 0.15 + 0.70 * ease_in_out_cubic((u - 0.74) / 0.16)
        else:
            yv = 0.85
        py = int(t2_y0 + yv * (t2_y1 - t2_y0))
        curve_pts.append((px, py))
    cv2.polylines(world_bgr, [np.array(curve_pts, dtype=np.int32)], False, BGR_TERRA, 2, cv2.LINE_AA)
    for ku in (0.22, 0.42, 0.74, 0.90):
        kx = int(c1_x0 + ku * (lane_x1 - c1_x0))
        ky = int(t2_y0 + (0.85 if ku in (0.22, 0.90) else 0.15) * (t2_y1 - t2_y0))
        cv2.circle(world_bgr, (kx, ky), 5, BGR_TERRA, -1, cv2.LINE_AA)

    # Sweeping vertical Terracotta Playhead across ruler + all 3 tracks
    ph_x = int(c1_x0 + ((t_scroll * 260) % max(100, (lane_x1 - c1_x0))))
    cv2.line(world_bgr, (ph_x, ty0 + 4), (ph_x, ty1), BGR_TERRA, 2, cv2.LINE_AA)
    tri = np.array([[ph_x - 6, ty0 + 2], [ph_x + 6, ty0 + 2], [ph_x, ty0 + 12]], dtype=np.int32)
    cv2.fillPoly(world_bgr, [tri], BGR_TERRA, cv2.LINE_AA)

    if zoom_in > 0.001:
        zp = ease_in_out_cubic(zoom_in)
        z_scale = 1.0 + (W / mw - 1.0) * (zp ** 1.45)
        target_cx, target_cy = mx0 + mw * 0.5, my0 + mh * 0.5
        view_w = W / z_scale
        view_h = H / z_scale
        cx_cur = W * 0.5 + (target_cx - W * 0.5) * zp
        cy_cur = H * 0.5 + (target_cy - H * 0.5) * zp
        x0 = max(0, min(W - view_w, cx_cur - view_w * 0.5))
        y0 = max(0, min(H - view_h, cy_cur - view_h * 0.5))
        src_crop = world_bgr[int(y0):int(y0 + view_h), int(x0):int(x0 + view_w)]
        world_bgr = cv2.resize(src_crop, (W, H), interpolation=cv2.INTER_CUBIC)

    return world_bgr


def generate_groove_and_sfx(total_dur, beat_times, keystroke_times, wav_out, sr=48000):
    """Synthesize upbeat 118 BPM lo-fi groove + crisp transition whooshes, pops, and key clicks."""
    n = int(total_dur * sr)
    buf = np.zeros(n, dtype=np.float32)
    t = np.arange(n, dtype=np.float32) / sr

    chords = [
        (220.0, 277.18, 329.63, 415.30),
        (196.0, 246.94, 293.66, 369.99),
        (174.61, 220.0, 261.63, 329.63),
        (164.81, 196.0, 246.94, 293.66),
    ]
    bar_len = 2.033
    bar_idx = ((t / bar_len).astype(np.int32)) % len(chords)
    pad = np.zeros(n, dtype=np.float32)
    for ci, chord in enumerate(chords):
        mask = (bar_idx == ci)
        tc = t[mask]
        val = np.zeros_like(tc)
        for fi, freq in enumerate(chord):
            val += (0.28 if fi == 0 else 0.16) * np.sin(2 * np.pi * freq * tc)
        pad[mask] = val

    beat_period = 0.50847
    beat_phase = t % beat_period
    kick_env = np.exp(-beat_phase * 22.0)
    kick = 0.25 * kick_env * np.sin(2 * np.pi * (55.0 + 95.0 * np.exp(-beat_phase * 35.0)) * beat_phase)
    eighth_phase = t % (beat_period * 0.5)
    pluck = 0.10 * np.exp(-eighth_phase * 28.0) * np.sin(2 * np.pi * 659.25 * eighth_phase)

    fade = np.minimum(1.0, t / 0.8) * np.minimum(1.0, np.maximum(0.0, (total_dur - t) / 1.2))
    buf += (pad * 0.18 + kick + pluck) * 0.22 * fade

    rng = np.random.default_rng(7)
    for bt, kind in beat_times:
        i0 = max(0, int((bt - 0.18) * sr))
        i1 = min(n, int((bt + 0.35) * sr))
        if i1 <= i0:
            continue
        seg_t = np.arange(i1 - i0, dtype=np.float32) / sr - 0.18
        if kind == "whoosh":
            noise = rng.normal(0, 1, len(seg_t)).astype(np.float32)
            env = np.exp(-(seg_t ** 2) / 0.018)
            sw = 0.25 * env * np.sin(2 * np.pi * (140 + 420 * np.exp(-np.abs(seg_t) * 8)) * (seg_t + 0.2))
            buf[i0:i1] += (noise * env * 0.14 + sw) * 0.65
        elif kind == "pop":
            env = np.exp(-np.maximum(0.0, seg_t) * 35.0) * (seg_t >= 0)
            pop = 0.35 * env * np.sin(2 * np.pi * (320 + 220 * np.exp(-seg_t * 25)) * np.maximum(0.0, seg_t))
            buf[i0:i1] += pop * 0.7

    for kt in keystroke_times:
        i0 = max(0, int(kt * sr))
        i1 = min(n, i0 + int(0.03 * sr))
        if i1 <= i0:
            continue
        st = np.arange(i1 - i0, dtype=np.float32) / sr
        click = 0.25 * np.exp(-st * 160.0) * np.sin(2 * np.pi * 1650.0 * st)
        buf[i0:i1] += click

    buf = np.clip(buf, -0.95, 0.95)
    pcm = (buf * 32767.0).astype(np.int16)
    stereo = np.stack([pcm, pcm], axis=1).tobytes()
    with wave.open(wav_out, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo)


def default_project_dict(aspect_ratio="16:9", segment_count=3, hud_prefix="AI DAILY", cta_text="每天一分钟 · 点个关注"):
    W, H = (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080)
    seg_intro = {
        "id": "seg_01",
        "scene_type": "dark_intro",
        "clip_path": "clips/seg_01.mp4",
        "spoken_text": "今天AI圈又有三个大消息，智能对话平台钱包上线、克劳德办公三件套发布，还有双子星和深度求索全部开放新接口。",
        "kinetic_lines": ["这支视频，", "可不是人工", "剪出来的。"],
        "transition_tag": "第一件大事",
        "behind_head_left": "OpenAI",
        "behind_head_right": "Wallet",
        "behind_head_sub": "智能对话钱包上线",
        "flash_card_title": "ChatGPT Wallet",
        "flash_card_sub": "AI 直接刷卡买单",
        "outro_headline": "智能钱包上线",
        "outro_sub": "AI 直接刷卡买单",
    }
    seg_green = {
        "id": "seg_02",
        "scene_type": "green_matte",
        "clip_path": "clips/seg_02.mp4",
        "spoken_text": "首先是智能钱包功能上线，以后授权后可以直接自动比价下单；同时克劳德企业办公三件套也同步发布了。",
        "behind_body_lines": ["刷卡", "买单"],
        "green_sub_text": "人脸或指纹授权，AI 自动比价下单",
        "waveform_title": "授权声浪",
        "waveform_sub_text": "克劳德企业三件套同步发布",
        "cutout_wait_text": "智能会议 · 表格",
        "cutout_title": "企业办公三件套",
        "cutout_serif_lines": ["Meetings · Sheets", "& Whiteboards"],
    }
    extra_presets = [
        {
            "scene_type": "dark_broll_extra",
            "spoken_text": "同时各大模型开发接口也在加速降本提效，本地工具链和自动化剪辑工作流全面跑通。",
            "card_title": "AI 开发接口升级",
            "card_serif": "API & Automation",
            "card_bullets": ["调用延迟显著降低", "多模态推理一体化", "开源生态全面协同"],
        },
        {
            "scene_type": "dark_broll_extra",
            "spoken_text": "更关键的是，智能终端已经能够自主完成检索、整理与生成，让普通创作者也能一键成片。",
            "card_title": "创作者效率跃迁",
            "card_serif": "Creator Stack",
            "card_bullets": ["一句话生成分镜脚本", "实拍抠像自动排版", "动效音轨一键混音"],
        },
    ]

    segments = [seg_intro]
    if segment_count >= 3:
        segments.append(seg_green)
    for idx in range(3, segment_count):
        seg_num = idx
        preset = dict(extra_presets[(idx - 3) % len(extra_presets)])
        preset["id"] = f"seg_{seg_num:02d}"
        preset["clip_path"] = f"clips/seg_{seg_num:02d}.mp4"
        preset["card_index"] = f"{seg_num:02d}"
        segments.append(preset)

    if segment_count >= 2:
        seg_finale_id = f"seg_{segment_count:02d}"
        seg_finale = {
            "id": seg_finale_id,
            "scene_type": "dark_finale",
            "clip_path": f"clips/{seg_finale_id}.mp4",
            "spoken_text": "同时双子星四代扩展全面开放，深度求索最新接口也支持实时联网与语音，记得点赞关注，明天继续聊。",
            "broll_hud_sub": "GEMINI 4 & DEEPSEEK API",
            "broll_caption": "双子星四代扩展 · DeepSeek 原生联网与语音接口全开放",
            "user_prompt_header": "你只需要说一句话",
            "user_prompt_text": "把今日AI大事件做成视频",
            "finale_pre_text": "剩下的全交给",
            "final_pop_word": "AI",
        }
        segments.append(seg_finale)

    return {
        "brand": {
            "hud_prefix": hud_prefix,
            "cta_text": cta_text,
            "polaroid_caption": f"{hud_prefix} · WORKSPACE",
        },
        "format": {
            "aspect_ratio": aspect_ratio,
            "width": W,
            "height": H,
            "fps": 24,
            "segment_count": segment_count,
        },
        "persona": {
            "mode": "default",
            "dark_anchor_name": "Woman sitting in editing room",
            "green_anchor_name": "Woman smiling in studio backdrop",
            "dark_anchor_path": "assets/default_dark_anchor.png",
            "green_anchor_path": "assets/default_green_anchor.png",
            "custom_persona_prompt": "",
        },
        "safety": {
            "auto_derisk": True,
            "replacements": dict(DEFAULT_SAFETY_REPLACEMENTS),
        },
        "segments": segments,
    }


def derisk_spoken_text(text, safety_cfg):
    """Replace company names / news / financial keywords in Flow speech prompts cleanly."""
    if not safety_cfg.get("auto_derisk", True):
        return text, []
    replacements = safety_cfg.get("replacements", DEFAULT_SAFETY_REPLACEMENTS)
    result = text
    applied = []
    for k in sorted(replacements.keys(), key=len, reverse=True):
        v = replacements[k]
        pattern = re.compile(re.escape(k), flags=re.IGNORECASE) if k.isascii() else re.compile(re.escape(k))
        if pattern.search(result):
            result = pattern.sub(v, result)
            applied.append({"from": k, "to": v})
    return result, applied


def build_flow_prompt(seg, persona_cfg, fmt_cfg, safety_cfg):
    """Build a policy-safe, no-on-screen-text Google Flow Veo prompt for a segment."""
    scene_type = seg["scene_type"]
    safe_speech, applied = derisk_spoken_text(seg.get("spoken_text", ""), safety_cfg)
    mode = persona_cfg.get("mode", "default")
    custom_desc = persona_cfg.get("custom_persona_prompt", "").strip()

    if scene_type in ("dark_intro", "dark_broll_extra", "dark_finale"):
        anchor_hint = persona_cfg.get("dark_anchor_name", "Woman sitting in editing room")
        anchor_file = persona_cfg.get("dark_anchor_path", "assets/default_dark_anchor.png")
        subject_desc = (
            custom_desc
            if mode == "custom" and custom_desc
            else "Same exact young East Asian female video creator from the attached reference ingredient image, keeping identical face, hairstyle, glasses/no-glasses styling, and relaxed dark editing room attire"
        )
        scene_env = (
            "sitting centered in a dimly lit dark video editing studio, dual glowing blurred editing monitors behind her with warm tungsten key light and soft amber rim light, zero neon blue or purple lighting, shallow depth of field, medium close-up chest-up framing, clean negative space on left and right."
            if scene_type != "dark_finale"
            else "sitting centered in a warm dark video editing suite, leaning in slightly at the start then giving a confident natural smile and slight hand gesture at the end, blurred warm amber editing monitors in background, zero blue light, shallow depth of field."
        )
    else:
        anchor_hint = persona_cfg.get("green_anchor_name", "Woman smiling in studio backdrop")
        anchor_file = persona_cfg.get("green_anchor_path", "assets/default_green_anchor.png")
        subject_desc = (
            custom_desc
            if mode == "custom" and custom_desc
            else "Same exact young East Asian female creator from the attached reference ingredient image, wearing a warm cream knit top"
        )
        scene_env = (
            "standing/sitting centered in front of a completely clean, flat, even muted sage-green studio wall (#97A396) with soft overhead studio lighting and zero background props or shadows behind her shoulders."
        )

    prompt = (
        f"{subject_desc}, {scene_env} "
        f"Static locked-off tripod camera, eye-level 35mm cinema lens, natural expressive facial delivery and gentle natural hand gestures. "
        f'说这段中文，不要字幕不要文字，声音清晰自然，语气有节奏感：“{safe_speech}” '
        f"STRICT NO ON-SCREEN TEXT: zero subtitles, zero floating graphics, zero UI overlays, pure clean photorealistic footage."
    )
    return {
        "segment_id": seg["id"],
        "scene_type": scene_type,
        "output_clip_path": seg["clip_path"],
        "aspect_ratio": fmt_cfg.get("aspect_ratio", "16:9"),
        "recommended_model": "Veo 3.1 - Lite",
        "ingredient_name": anchor_hint,
        "ingredient_file": anchor_file,
        "spoken_text_original": seg.get("spoken_text", ""),
        "spoken_text_safe": safe_speech,
        "replacements_applied": applied,
        "flow_prompt": prompt,
    }


def resolve_path(base_dir, p):
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base_dir, p))


def cmd_init(args):
    cfg = default_project_dict(
        aspect_ratio=args.aspect,
        segment_count=args.segments,
        hud_prefix=args.hud_prefix,
        cta_text=args.cta,
    )
    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"Initialized project config ({args.aspect}, {args.segments} segments): {out_path}")


def cmd_flow_plan(args):
    proj_path = os.path.abspath(args.project)
    if not os.path.exists(proj_path):
        print(f"Error: project file not found: {proj_path}", file=sys.stderr)
        sys.exit(1)
    with open(proj_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    proj_dir = os.path.dirname(proj_path)
    out_path = os.path.abspath(args.output) if args.output else os.path.join(proj_dir, "output", "flow_tasks.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    tasks = [
        build_flow_prompt(seg, cfg.get("persona", {}), cfg.get("format", {}), cfg.get("safety", {}))
        for seg in cfg.get("segments", [])
    ]
    payload = {
        "project": proj_path,
        "aspect_ratio": cfg.get("format", {}).get("aspect_ratio", "16:9"),
        "tasks": tasks,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Generated {len(tasks)} Google Flow generation tasks -> {out_path}")
    for t in tasks:
        print(f"  [{t['segment_id']} | {t['scene_type']}] Ingredient: {t['ingredient_name']} ({t['ingredient_file']})")
        if t["replacements_applied"]:
            subs = ", ".join(f"{r['from']}->{r['to']}" for r in t["replacements_applied"])
            print(f"    Safety auto-derisk: {subs}")


def cmd_render(args):
    proj_path = os.path.abspath(args.project)
    if not os.path.exists(proj_path):
        print(f"Error: project file not found: {proj_path}", file=sys.stderr)
        sys.exit(1)
    with open(proj_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    proj_dir = os.path.dirname(proj_path)
    brand = cfg.get("brand", {})
    hud_prefix = brand.get("hud_prefix", "AI DAILY")
    cta_text = brand.get("cta_text", "每天一分钟 · 点个关注")
    polaroid_caption = brand.get("polaroid_caption", f"{hud_prefix} · WORKSPACE")

    fmt = cfg.get("format", {})
    W = int(fmt.get("width", 1920))
    H = int(fmt.get("height", 1080))
    FPS = int(fmt.get("fps", 24))
    is_vert = H > W

    segments = cfg.get("segments", [])
    if not segments:
        print("Error: project.json has no segments.", file=sys.stderr)
        sys.exit(1)

    clip_abs_paths = []
    for seg in segments:
        cp = resolve_path(proj_dir, seg["clip_path"])
        if not os.path.exists(cp):
            print(
                f"Error: missing clip for segment '{seg['id']}': {cp}\n"
                f"Run flow-plan and save the generated Google Flow clip to {cp} first.",
                file=sys.stderr,
            )
            sys.exit(1)
        clip_abs_paths.append(cp)

    out_path = os.path.abspath(args.output) if args.output else os.path.join(proj_dir, "output", "《今日AI大事件》成片.mp4")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    proc_dir = os.path.join(proj_dir, "output", f".tmp_proc_{os.getpid()}")
    os.makedirs(proc_dir, exist_ok=True)

    try:
        norm_mp4s, norm_wavs, durs, clips_frames, envs = [], [], [], [], []
        for idx, (seg, cp) in enumerate(zip(segments, clip_abs_paths)):
            omp = os.path.join(proc_dir, f"norm_{idx}.mp4")
            owv = os.path.join(proc_dir, f"norm_{idx}.wav")
            normalize_and_extract_wav(cp, omp, owv, W, H, FPS)
            norm_mp4s.append(omp)
            norm_wavs.append(owv)
            durs.append(get_duration(omp))

            cap = cv2.VideoCapture(omp)
            arr = []
            while True:
                ret, fr = cap.read()
                if not ret:
                    break
                arr.append(fr)
            cap.release()
            clips_frames.append(arr)
            envs.append(extract_rms_envelope(owv, len(arr), FPS))

        total_dur = sum(durs)
        print(f"Rendering {len(segments)} segments ({W}x{H} @ {FPS}fps, {total_dur:.2f}s)...")

        thumb_strips = [
            arr[min(len(arr) - 1, max(0, len(arr) // 3))] for arr in clips_frames
        ]

        # Precompute GrabCut masks for behind-head windows in dark_intro / dark_broll_extra
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        dark_masks_by_seg = {}
        for s_idx, (seg, d, arr) in enumerate(zip(segments, durs, clips_frames)):
            if seg["scene_type"] == "dark_intro":
                t_b1_start = 3.4 if d >= 8.2 else 0.377 * d
                t_b1_end = 5.2 if d >= 8.2 else 0.577 * d
                i_start = max(0, int((t_b1_start - 0.2) * FPS))
                i_end = min(len(arr), int((t_b1_end + 0.1) * FPS))
                seg_masks = {}
                last_m = None
                for fi in range(i_start, i_end):
                    if (fi - i_start) % 2 == 0 or last_m is None:
                        m = compute_dark_subject_mask(arr[fi], face_cascade, W, H)
                        last_m = m if last_m is None else (0.55 * last_m + 0.45 * m)
                    seg_masks[fi] = last_m
                dark_masks_by_seg[s_idx] = seg_masks

        # Fonts
        sc = 0.78 if is_vert else 1.0
        f_songti_big = ImageFont.truetype(FONT_SONGTI, int(210 * (0.72 if is_vert else 1.0)), index=0)
        f_songti_med = ImageFont.truetype(FONT_SONGTI, int(96 * sc), index=1)
        f_songti_sub = ImageFont.truetype(FONT_SONGTI, int(64 * sc), index=1)
        f_serif_giant = ImageFont.truetype(FONT_SERIF_IT, int(154 * (0.68 if is_vert else 1.0)))
        f_serif_flash = ImageFont.truetype(FONT_SERIF_IT, int(148 * (0.62 if is_vert else 1.0)))
        f_sans_kin = ImageFont.truetype(FONT_SANS, int(54 * (0.85 if is_vert else 1.0)), index=2)
        f_sans_sm = ImageFont.truetype(FONT_SANS, int(34 * (0.90 if is_vert else 1.0)), index=2)
        f_user_txt = ImageFont.truetype(FONT_SANS, int(38 * (0.92 if is_vert else 1.0)), index=0)
        f_ai_pop = ImageFont.truetype(FONT_SERIF_IT, int(175 * (0.78 if is_vert else 1.0)))

        # Pre-render tilted polaroid from first clip
        pol_w, pol_h = (300, 175) if is_vert else (360, 210)
        polaroid_src = cv2.resize(clips_frames[0][min(len(clips_frames[0]) - 1, 30)], (pol_w, pol_h), interpolation=cv2.INTER_AREA)
        pol_rgb = cv2.cvtColor(polaroid_src, cv2.COLOR_BGR2RGB)
        cw_card, ch_card = pol_w + 32, pol_h + 50
        pol_card = Image.new("RGBA", (cw_card, ch_card), (255, 255, 255, 255))
        pol_card.paste(Image.fromarray(pol_rgb), (16, 16))
        ImageDraw.Draw(pol_card).text(
            (20, pol_h + 22), polaroid_caption,
            font=ImageFont.truetype(FONT_MONO, 14 if is_vert else 15), fill=(70, 70, 75),
        )
        pad_w, pad_h = cw_card + 58, ch_card + 60
        sh_box = Image.new("RGBA", (pad_w, pad_h), (0, 0, 0, 0))
        ImageDraw.Draw(sh_box).rectangle([29, 34, pad_w - 29, pad_h - 26], fill=(20, 20, 25, 95))
        pol_pad = Image.fromarray(cv2.GaussianBlur(np.array(sh_box), (25, 25), 9))
        pol_pad.paste(pol_card, (25, 22), pol_card)
        pol_tilted = pol_pad.rotate(5.5, resample=Image.BICUBIC, expand=True)

        silent_mp4 = os.path.join(proc_dir, "fable_video_only.mp4")
        pipe = subprocess.Popen(
            [
                FFMPEG, "-y",
                "-f", "rawvideo", "-vcodec", "rawvideo",
                "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
                "-i", "-",
                "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p",
                silent_mp4,
            ],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Build beat events & keystroke times dynamically across all segments
        beat_events = []
        keystroke_times = []
        cum_t = 0.0
        for s_idx, (seg, d) in enumerate(zip(segments, durs)):
            st = seg["scene_type"]
            if s_idx > 0:
                beat_events.append((cum_t, "whoosh"))
            if st == "dark_intro":
                t_b1_s = 3.4 if d >= 8.2 else 0.377 * d
                t_b1_e = 5.2 if d >= 8.2 else 0.577 * d
                t_b1b_e = 6.2 if d >= 8.2 else 0.688 * d
                beat_events.extend([
                    (cum_t + t_b1_s, "pop"),
                    (cum_t + t_b1_e, "whoosh"),
                    (cum_t + t_b1b_e, "pop"),
                ])
            elif st == "green_matte":
                t_b2_c = 2.4 if d >= 8.0 else 0.266 * d
                t_b3_c = 4.6 if d >= 8.0 else 0.511 * d
                beat_events.extend([
                    (cum_t + t_b2_c, "pop"),
                    (cum_t + t_b3_c, "whoosh"),
                    (cum_t + t_b3_c + 0.85, "pop"),
                ])
            elif st == "dark_broll_extra":
                beat_events.extend([
                    (cum_t + 0.5, "pop"),
                    (cum_t + d * 0.55, "pop"),
                ])
            elif st == "dark_finale":
                t_b4_ir = 1.45 if d >= 8.0 else 0.161 * d
                t_b4_e = 3.60 if d >= 8.0 else 0.400 * d
                typing_txt = seg.get("user_prompt_text", "把今日AI大事件做成视频")
                beat_events.extend([
                    (cum_t + t_b4_ir, "pop"),
                    (cum_t + t_b4_e, "whoosh"),
                    (cum_t + t_b4_e + 2.0, "pop"),
                ])
                for k in range(len(typing_txt)):
                    keystroke_times.append(cum_t + t_b4_e + 0.15 + k / 6.5)
            cum_t += d

        bg_lab_ema = None
        for s_idx, (seg, d, arr, env) in enumerate(zip(segments, durs, clips_frames, envs)):
            st = seg["scene_type"]
            n_f = len(arr)

            if st == "dark_intro":
                t_b1_start = 3.4 if d >= 8.2 else 0.377 * d
                t_b1_end = 5.2 if d >= 8.2 else 0.577 * d
                t_b1b_end = 6.2 if d >= 8.2 else 0.688 * d
                t_tag = 2.85 if d >= 8.2 else 0.316 * d
                k_lines = seg.get("kinetic_lines", ["这支视频，", "可不是人工", "剪出来的。"])
                tag_txt = seg.get("transition_tag", "第一件大事")
                bh_l = seg.get("behind_head_left", "OpenAI")
                bh_r = seg.get("behind_head_right", "Wallet")
                bh_sub = seg.get("behind_head_sub", "智能对话钱包上线")
                fc_title = seg.get("flash_card_title", "ChatGPT Wallet")
                fc_sub = seg.get("flash_card_sub", "AI 直接刷卡买单")
                outro_hl = seg.get("outro_headline", "智能钱包上线")
                outro_sub = seg.get("outro_sub", "AI 直接刷卡买单")
                seg_masks = dark_masks_by_seg.get(s_idx, {})

                for l_idx in range(n_f):
                    t_clip = l_idx / FPS
                    raw_bgr = arr[l_idx].copy()

                    if t_b1_end <= t_clip < t_b1b_end:
                        p = spring_ease((t_clip - t_b1_end) * 2.2, 185, 22)
                        card = Image.new("RGBA", (W, H), RGB_TERRA + (255,))
                        cd = ImageDraw.Draw(card)
                        cd.rectangle([26, 26, W - 26, H - 26], outline=(20, 20, 19, 45), width=1)
                        draw_hud(cd, "EDITED BY", W, H, dot_color=RGB_CARBON, text_color=RGB_CARBON)
                        f_fc = fit_font(cd, fc_title, FONT_SERIF_IT, None, int(148 * (0.65 if is_vert else 1.0)), 44, W - 110)
                        bb = cd.textbbox((0, 0), fc_title, font=f_fc)
                        tw = bb[2] - bb[0]
                        yc = int(H * 0.38 if is_vert else 390) + int((1.0 - p) * 35)
                        cd.text(((W - tw) // 2, yc), fc_title, font=f_fc, fill=RGB_CARBON)
                        f_fsub = fit_font(cd, fc_sub, FONT_SONGTI, 1, int(64 * sc), 32, W - 120)
                        sbb = cd.textbbox((0, 0), fc_sub, font=f_fsub)
                        cd.text(((W - (sbb[2] - sbb[0])) // 2, yc + (bb[3] - bb[1]) + 34), fc_sub, font=f_fsub, fill=RGB_PARC)
                        frame_bgr = np.ascontiguousarray(np.array(card)[:, :, [2, 1, 0]])
                    else:
                        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        od = ImageDraw.Draw(overlay)
                        draw_hud(od, f"EDITED BY  /  {hud_prefix}", W, H, dot_color=RGB_TERRA, text_color=RGB_PARC)

                        if t_clip < t_b1_start:
                            lx = 76 if is_vert else 120
                            ly_base = 1280 if is_vert else 395
                            if t_clip >= t_tag:
                                p = ease_out_cubic((t_clip - t_tag) / 0.3)
                                od.text((lx, (1320 if is_vert else 465) - int((1 - p) * 20)), tag_txt, font=f_songti_sub, fill=RGB_TERRA + (255,))
                            else:
                                timing_starts = [0.20, 1.05, 1.90]
                                for li, ltxt in enumerate(k_lines[:3]):
                                    st_t = timing_starts[li]
                                    if t_clip >= st_t:
                                        p = ease_out_cubic((t_clip - st_t) / 0.22)
                                        y_pos = ly_base + li * (64 if is_vert else 74) + int((1 - p) * 18)
                                        if li == 1 and "不是" in ltxt:
                                            idx_hl = ltxt.find("不是")
                                            pre, hl, post = ltxt[:idx_hl], "不是", ltxt[idx_hl + 2:]
                                            cx_k = lx
                                            for part, col in [(pre, RGB_PARC), (hl, RGB_TERRA), (post, RGB_PARC)]:
                                                if part:
                                                    od.text((cx_k, y_pos), part, font=f_sans_kin, fill=col, stroke_width=2, stroke_fill=RGB_CARBON + (220,))
                                                    cx_k += od.textbbox((0, 0), part, font=f_sans_kin)[2]
                                        else:
                                            od.text((lx, y_pos), ltxt, font=f_sans_kin, fill=RGB_PARC, stroke_width=2, stroke_fill=RGB_CARBON + (220,))
                            frame_bgr = paste_rgba_on_bgr(raw_bgr, overlay)

                        elif t_clip < t_b1_end:
                            bg_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                            bd = ImageDraw.Draw(bg_layer)
                            p = ease_out_cubic((t_clip - t_b1_start) / 0.35)
                            if is_vert:
                                fl = fit_font(bd, bh_l, FONT_SERIF_IT, None, 132, 54, W - 120)
                                wl = bd.textbbox((0, 0), bh_l, font=fl)[2]
                                bd.text(((W - wl) // 2, 260 + int((1 - p) * 40)), bh_l, font=fl, fill=RGB_PARC + (255,))
                                if t_clip >= t_b1_start + 0.65:
                                    p2 = ease_out_cubic((t_clip - (t_b1_start + 0.65)) / 0.30)
                                    fr_f = fit_font(bd, bh_r, FONT_SERIF_IT, None, 132, 54, W - 120)
                                    wr = bd.textbbox((0, 0), bh_r, font=fr_f)[2]
                                    bd.text(((W - wr) // 2, 860 + int((1 - p2) * 40)), bh_r, font=fr_f, fill=RGB_PARC + (255,))
                            else:
                                fl = fit_font(bd, bh_l, FONT_SERIF_IT, None, 154, 72, 620)
                                bd.text((110, 340 + int((1 - p) * 45)), bh_l, font=fl, fill=RGB_PARC + (255,))
                                if t_clip >= t_b1_start + 0.65:
                                    p2 = ease_out_cubic((t_clip - (t_b1_start + 0.65)) / 0.30)
                                    fr_f = fit_font(bd, bh_r, FONT_SERIF_IT, None, 154, 72, 660)
                                    bd.text((1185, 340 + int((1 - p2) * 45)), bh_r, font=fr_f, fill=RGB_PARC + (255,))
                            composed_bg = paste_rgba_on_bgr(raw_bgr, bg_layer)
                            alpha_fg = seg_masks.get(l_idx, np.zeros((H, W), dtype=np.float32))
                            composed = composite_alpha_bgr(composed_bg, raw_bgr, alpha_fg, W, H)
                            od.text((76 if is_vert else 120, 1400 if is_vert else 560), bh_sub, font=f_sans_sm, fill=RGB_BONE)
                            frame_bgr = paste_rgba_on_bgr(composed, overlay)

                        else:
                            p = ease_out_cubic((t_clip - t_b1b_end) / 0.3)
                            lx = 76 if is_vert else 115
                            ly_hl = 1320 if is_vert else 420
                            f_hl = fit_font(od, outro_hl, FONT_SONGTI, 1, int(96 * sc), 44, W - lx * 2)
                            od.text((lx, ly_hl), outro_hl, font=f_hl, fill=RGB_PARC + (int(255 * p),))
                            od.text((lx + 3, ly_hl + (92 if is_vert else 115)), outro_sub, font=f_sans_sm, fill=RGB_TERRA + (int(255 * p),))
                            frame_bgr = paste_rgba_on_bgr(raw_bgr, overlay)

                    pipe.stdin.write(frame_bgr.tobytes())

            elif st == "green_matte":
                t_b2_cut = 2.4 if d >= 8.0 else 0.266 * d
                t_b3_cut = 4.6 if d >= 8.0 else 0.511 * d
                bb_lines = seg.get("behind_body_lines", ["刷卡", "买单"])
                green_sub = seg.get("green_sub_text", "人脸或指纹授权，AI 自动比价下单")
                wf_title = seg.get("waveform_title", "授权声浪")
                wf_sub = seg.get("waveform_sub_text", "克劳德企业三件套同步发布")
                cut_wait = seg.get("cutout_wait_text", "智能会议 · 表格")
                cut_title = seg.get("cutout_title", "企业办公三件套")
                cut_serif = seg.get("cutout_serif_lines", ["Meetings · Sheets", "& Whiteboards"])

                yy, xx = np.mgrid[0:H, 0:W]
                r2 = ((xx - W * 0.5) / (W * 0.65)) ** 2 + ((yy - H * 0.42) / (H * 0.7)) ** 2
                vig = np.clip(1.06 - 0.16 * r2, 0.85, 1.08)[:, :, None]
                bg_sage_base = np.clip(np.full((H, W, 3), (150, 163, 151), dtype=np.float32) * vig, 0, 255).astype(np.uint8)
                bg_cream = np.full((H, W, 3), BGR_PARC, dtype=np.uint8)
                cv2.rectangle(bg_cream, (26, 26), (W - 26, H - 26), BGR_RULE, 1)
                white_bgr = np.full((H, W, 3), (255, 255, 255), dtype=np.uint8)
                zeros_bgr = np.zeros((H, W, 3), dtype=np.uint8)

                for l_idx in range(n_f):
                    t_clip = l_idx / FPS
                    raw_bgr = arr[l_idx]
                    despilled_bgr, fg_alpha, bg_lab_ema = segment_sage_green(raw_bgr, W, H, bg_lab_ema)

                    if t_clip < t_b3_cut:
                        behind = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        bd = ImageDraw.Draw(behind)

                        if t_clip < t_b2_cut:
                            p = ease_out_cubic(t_clip / 0.35)
                            txt1 = bb_lines[0] if len(bb_lines) > 0 else "刷卡"
                            txt2 = bb_lines[1] if len(bb_lines) > 1 else "买单"
                            f1 = fit_font(bd, txt1, FONT_SONGTI, 0, int(210 * (0.72 if is_vert else 1.0)), 64, int(W * 0.7))
                            f2 = fit_font(bd, txt2, FONT_SONGTI, 0, int(210 * (0.72 if is_vert else 1.0)), 64, int(W * 0.7))
                            w1 = bd.textbbox((0, 0), txt1, font=f1)[2]
                            w2 = bd.textbbox((0, 0), txt2, font=f2)[2]
                            if is_vert:
                                bd.text(((W - w1) // 2 - 120, 310 + int((1 - p) * 35)), txt1, font=f1, fill=(240, 234, 222, 245))
                                bd.text(((W - w2) // 2 + 120, 740 + int((1 - p) * 35)), txt2, font=f2, fill=(240, 234, 222, 245))
                            else:
                                bd.text(((W - w1) // 2 - 220, 195 + int((1 - p) * 35)), txt1, font=f1, fill=(240, 234, 222, 245))
                                bd.text(((W - w2) // 2 + 230, 395 + int((1 - p) * 35)), txt2, font=f2, fill=(240, 234, 222, 245))
                            bg_drawn = paste_rgba_on_bgr(bg_sage_base, behind)
                            comp = composite_alpha_bgr(bg_drawn, despilled_bgr, fg_alpha, W, H)

                            front = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                            fd = ImageDraw.Draw(front)
                            draw_hud(fd, "TYPE  /  BEHIND SUBJECT", W, H, dot_color=(242, 92, 84))
                            fd.text((76 if is_vert else 115, 1540 if is_vert else 845), green_sub, font=f_sans_sm, fill=(250, 248, 242))
                            frame_bgr = paste_rgba_on_bgr(comp, front)
                        else:
                            p = ease_out_cubic((t_clip - t_b2_cut) / 0.30)
                            fwf = fit_font(bd, wf_title, FONT_SONGTI, 1, 96 if is_vert else 128, 48, W - 160)
                            bd.text((80 if is_vert else 140, 240 if is_vert else 120), wf_title, font=fwf, fill=(242, 236, 224, 245))
                            amp = float(env[l_idx])
                            n_bars = 34 if is_vert else 56
                            bar_w = 14
                            gap = 12
                            start_x = (W - n_bars * (bar_w + gap)) // 2
                            cy_bar = 820 if is_vert else 470
                            for bi in range(n_bars):
                                wave_shape = 0.45 + 0.55 * abs(math.sin(bi * 0.42 + t_clip * 9.5) * math.cos(bi * 0.18 - t_clip * 4.2))
                                bar_h = int((26 + (230 if is_vert else 210) * amp * wave_shape) * p)
                                bx = start_x + bi * (bar_w + gap)
                                bd.rounded_rectangle([bx, cy_bar - bar_h, bx + bar_w, cy_bar + bar_h], radius=7, fill=(242, 236, 224, 240))
                            bg_drawn = paste_rgba_on_bgr(bg_sage_base, behind)
                            comp = composite_alpha_bgr(bg_drawn, despilled_bgr, fg_alpha, W, H)

                            front = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                            fd = ImageDraw.Draw(front)
                            draw_hud(fd, "SOUND  /  VOICE WAVEFORM", W, H, dot_color=(242, 92, 84))
                            fd.text((76 if is_vert else 115, 1540 if is_vert else 845), wf_sub, font=f_sans_sm, fill=(250, 248, 242))
                            frame_bgr = paste_rgba_on_bgr(comp, front)
                    else:
                        lt = t_clip - t_b3_cut
                        behind = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        bd = ImageDraw.Draw(behind)
                        slide_t = spring_ease((lt - 0.85) * 1.4, 180, 22) if lt >= 0.85 else 0.0
                        dx = int((-155 if is_vert else -370) * slide_t)

                        if slide_t > 0.01:
                            r_disc = int((195 if is_vert else 245) * min(1.0, slide_t * 1.15))
                            dcx = W // 2 + dx + (30 if is_vert else 60)
                            dcy = 720 if is_vert else 445
                            bd.ellipse([dcx - r_disc, dcy - r_disc, dcx + r_disc, dcy + r_disc], fill=RGB_TERRA + (255,))

                        bg_with_disc = paste_rgba_on_bgr(bg_cream, behind)
                        mask_u8 = (fg_alpha * 255).astype(np.uint8)
                        k_border = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (29, 29))
                        dilated = cv2.dilate(mask_u8, k_border).astype(np.float32) / 255.0
                        dilated = cv2.GaussianBlur(dilated, (7, 7), 1.5)
                        M_sh = np.float32([[1, 0, dx + 6], [0, 1, 12]])
                        sh_alpha = cv2.warpAffine(dilated, M_sh, (W, H), borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                        sh_alpha = cv2.GaussianBlur(sh_alpha, (25, 25), 8) * 0.24
                        bg_sh = composite_alpha_bgr(bg_with_disc, zeros_bgr, sh_alpha, W, H)
                        bg_sticker = composite_alpha_bgr(bg_sh, white_bgr, dilated, W, H, offset_x=dx)
                        comp = composite_alpha_bgr(bg_sticker, despilled_bgr, fg_alpha, W, H, offset_x=dx)

                        front = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        fd = ImageDraw.Draw(front)
                        hud_txt = "CUTOUT  /  LIVE MATTE" if lt < 0.95 else "PLACE  /  ANYWHERE"
                        draw_hud(fd, hud_txt, W, H, dot_color=RGB_TERRA, text_color=RGB_CARBON)

                        if lt < 0.95:
                            p_txt = ease_out_cubic(lt / 0.3)
                            if is_vert:
                                fd.text((80, 1440), cut_wait, font=f_sans_kin, fill=RGB_CARBON + (int(255 * p_txt),))
                            else:
                                fd.text((1350, 320), cut_wait, font=f_sans_kin, fill=RGB_CARBON + (int(255 * p_txt),))
                        else:
                            p_r = ease_out_cubic((lt - 0.95) / 0.45)
                            if is_vert:
                                f_ct = fit_font(fd, cut_title, FONT_SONGTI, 1, 74, 40, W - 140)
                                fd.text((72, 1140 + int((1 - p_r) * 24)), cut_title, font=f_ct, fill=RGB_CARBON + (int(255 * p_r),))
                                for si, s_line in enumerate(cut_serif[:2]):
                                    f_rf = fit_font(fd, s_line, FONT_SERIF_IT, None, 48, 28, W - 160)
                                    fd.text((76, 1235 + si * 58 + int((1 - p_r) * 20)), s_line, font=f_rf, fill=RGB_TERRA + (int(255 * p_r),))
                                p_pol = spring_ease((lt - 1.35) * 1.4, 180, 22) if lt >= 1.35 else 0.0
                                if p_pol > 0.01:
                                    px = W - pol_tilted.width - 70
                                    py = int(1410 + (1.0 - p_pol) * 90)
                                    front.alpha_composite(pol_tilted, (px, py))
                            else:
                                f_ct = fit_font(fd, cut_title, FONT_SONGTI, 1, 96, 48, W - 1090)
                                fd.text((1030, 175 + int((1 - p_r) * 24)), cut_title, font=f_ct, fill=RGB_CARBON + (int(255 * p_r),))
                                for si, s_line in enumerate(cut_serif[:2]):
                                    f_rf = fit_font(fd, s_line, FONT_SERIF_IT, None, 56, 30, W - 1090)
                                    fd.text((1035, 300 + si * 68 + int((1 - p_r) * 20)), s_line, font=f_rf, fill=RGB_TERRA + (int(255 * p_r),))
                                p_pol = spring_ease((lt - 1.35) * 1.4, 180, 22) if lt >= 1.35 else 0.0
                                if p_pol > 0.01:
                                    px = 1130
                                    py = int(515 + (1.0 - p_pol) * 90)
                                    front.alpha_composite(pol_tilted, (px, py))

                        frame_bgr = paste_rgba_on_bgr(comp, front)

                    pipe.stdin.write(frame_bgr.tobytes())

            elif st == "dark_broll_extra":
                c_idx = seg.get("card_index", f"0{s_idx + 1}")
                c_title = seg.get("card_title", "重点动态")
                c_serif = seg.get("card_serif", "Key Highlight")
                c_bullets = seg.get("card_bullets", ["要点一", "要点二", "要点三"])

                for l_idx in range(n_f):
                    t_clip = l_idx / FPS
                    raw_bgr = arr[l_idx].copy()
                    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    od = ImageDraw.Draw(ov)
                    draw_hud(od, f"HIGHLIGHT {c_idx}  /  {hud_prefix}", W, H, dot_color=RGB_TERRA)

                    p_card = ease_out_cubic(t_clip / 0.40)
                    if is_vert:
                        gx0, gy0, gx1, gy1 = 70, 1260, W - 70, 1740
                    else:
                        gx0, gy0, gx1, gy1 = 96, 260, 660, 820
                    blur_roi = cv2.GaussianBlur(raw_bgr[gy0:gy1, gx0:gx1], (45, 45), 16)
                    dark_tint = np.full_like(blur_roi, (25, 27, 28))
                    glass_patch = cv2.addWeighted(blur_roi, 0.48, dark_tint, 0.52, 0)
                    pill_im = Image.new("L", (gx1 - gx0, gy1 - gy0), 0)
                    ImageDraw.Draw(pill_im).rounded_rectangle([0, 0, gx1 - gx0 - 1, gy1 - gy0 - 1], radius=24, fill=255)
                    gm = (np.array(pill_im).astype(np.float32) / 255.0 * p_card)[:, :, None]
                    roi_cur = raw_bgr[gy0:gy1, gx0:gx1].astype(np.float32)
                    raw_bgr[gy0:gy1, gx0:gx1] = np.clip(roi_cur * (1 - gm) + glass_patch.astype(np.float32) * gm, 0, 255).astype(np.uint8)

                    od.rounded_rectangle([gx0, gy0, gx1, gy1], radius=24, outline=RGB_PARC + (int(95 * p_card),), width=1)
                    od.rounded_rectangle([gx0 + 6, gy0 + 6, gx1 - 6, gy1 - 6], radius=19, outline=RGB_TERRA + (int(55 * p_card),), width=1)
                    od.text((gx0 + 32, gy0 + 26), c_idx, font=ImageFont.truetype(FONT_MONO, 28), fill=RGB_TERRA + (int(255 * p_card),))
                    f_cs = fit_font(od, c_serif, FONT_SERIF_IT, None, 42, 24, gx1 - gx0 - 120)
                    od.text((gx0 + 96, gy0 + 20), c_serif, font=f_cs, fill=RGB_STONE + (int(240 * p_card),))
                    f_ct_ex = fit_font(od, c_title, FONT_SONGTI, 1, 64, 34, gx1 - gx0 - 64)
                    od.text((gx0 + 32, gy0 + 82), c_title, font=f_ct_ex, fill=RGB_PARC + (int(255 * p_card),))
                    for bi, b_txt in enumerate(c_bullets[:3]):
                        bt_start = 0.55 + bi * 0.65
                        if t_clip >= bt_start:
                            pb = ease_out_cubic((t_clip - bt_start) / 0.30)
                            by = gy0 + 185 + bi * 78 + int((1 - pb) * 16)
                            od.ellipse([gx0 + 36, by + 12, gx0 + 48, by + 24], fill=RGB_TERRA + (255,))
                            od.text((gx0 + 68, by), b_txt, font=f_sans_sm, fill=RGB_PARC + (int(255 * pb),))

                    frame_bgr = paste_rgba_on_bgr(raw_bgr, ov)
                    pipe.stdin.write(frame_bgr.tobytes())

            else:
                # dark_finale: Beat 4 (3D Lens + NLE Timeline Highway) -> Beat 5 (Frosted Glass Typing + Pop Finale)
                t_b4_iris = 1.45 if d >= 8.0 else 0.161 * d
                t_b4_end = 3.60 if d >= 8.0 else 0.400 * d
                broll_sub = seg.get("broll_hud_sub", "GEMINI 4 & DEEPSEEK API")
                broll_cap = seg.get("broll_caption", "双子星四代扩展 · DeepSeek 原生联网与语音接口全开放")
                up_header = seg.get("user_prompt_header", "你只需要说一句话")
                typing_str = seg.get("user_prompt_text", "把今日AI大事件做成视频")
                pre_txt = seg.get("finale_pre_text", "剩下的全交给")
                pop_word = seg.get("final_pop_word", "AI")

                for l_idx in range(n_f):
                    t_clip = l_idx / FPS
                    raw_bgr = arr[l_idx].copy()

                    if t_clip < t_b4_end:
                        zoom_t = max(0.0, (t_clip - (t_b4_end - 0.72)) / 0.72) if t_clip >= (t_b4_end - 0.72) else 0.0
                        world_bgr = render_3d_timeline_world(t_clip, thumb_strips, raw_bgr, W, H, zoom_in=zoom_t)
                        frame_bgr = render_3d_lens_portal_frame(t_clip, t_b4_iris, world_bgr, W, H) if t_clip < t_b4_iris else world_bgr

                        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        od = ImageDraw.Draw(ov)
                        hud_col = RGB_PARC if (t_clip < 0.90 or zoom_t > 0.72) else RGB_CARBON
                        draw_hud(od, f"STUDIO  /  NLE TIMELINE · {broll_sub}", W, H, dot_color=RGB_TERRA, text_color=hud_col)
                        if t_clip >= 0.8:
                            f_cap = fit_font(od, broll_cap, FONT_SANS, 2, int(34 * (0.85 if is_vert else 1.0)), 22, W - 160)
                            cbb = od.textbbox((0, 0), broll_cap, font=f_cap)
                            cw = cbb[2] - cbb[0]
                            px_cap = (W - cw - 48) // 2 if is_vert else 96
                            py_cap = 1660 if is_vert else 932
                            od.rounded_rectangle([px_cap, py_cap, px_cap + cw + 48, py_cap + 62], radius=14,
                                                 fill=RGB_CARBON + (230,), outline=RGB_TERRA + (90,), width=1)
                            od.text((px_cap + 24, py_cap + 13), broll_cap, font=f_cap, fill=RGB_PARC)
                        frame_bgr = paste_rgba_on_bgr(frame_bgr, ov)
                    else:
                        lt = t_clip - t_b4_end
                        if is_vert:
                            gx0, gy0, gx1, gy1 = 90, 1290, W - 90, 1495
                        else:
                            gx0, gy0, gx1, gy1 = 105, 455, 635, 645
                        blur_roi = cv2.GaussianBlur(raw_bgr[gy0:gy1, gx0:gx1], (45, 45), 16)
                        dark_tint = np.full_like(blur_roi, (25, 27, 28))
                        glass_patch = cv2.addWeighted(blur_roi, 0.52, dark_tint, 0.48, 0)
                        pill_im = Image.new("L", (gx1 - gx0, gy1 - gy0), 0)
                        ImageDraw.Draw(pill_im).rounded_rectangle([0, 0, gx1 - gx0 - 1, gy1 - gy0 - 1], radius=22, fill=255)
                        gm = (np.array(pill_im).astype(np.float32) / 255.0)[:, :, None]
                        roi_cur = raw_bgr[gy0:gy1, gx0:gx1].astype(np.float32)
                        raw_bgr[gy0:gy1, gx0:gx1] = np.clip(roi_cur * (1 - gm) + glass_patch.astype(np.float32) * gm, 0, 255).astype(np.uint8)

                        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        od = ImageDraw.Draw(ov)
                        draw_hud(od, "PROMPT  /  ONE SENTENCE", W, H, dot_color=RGB_TERRA, text_color=RGB_PARC)

                        od.text((gx0 + 6, gy0 - 58), up_header, font=f_sans_sm, fill=RGB_PARC)
                        od.rounded_rectangle([gx0, gy0, gx1, gy1], radius=22, outline=RGB_PARC + (95,), width=1)
                        od.rounded_rectangle([gx0 + 6, gy0 + 6, gx1 - 6, gy1 - 6], radius=17, outline=RGB_TERRA + (55,), width=1)
                        od.ellipse([gx0 + 26, gy0 + 30, gx0 + 35, gy0 + 39], fill=RGB_TERRA + (255,))
                        od.text((gx0 + 44, gy0 + 24), "USER", font=ImageFont.truetype(FONT_MONO, 17), fill=RGB_STONE + (220,))

                        n_chars = min(len(typing_str), max(0, int((lt - 0.15) * 6.5)))
                        cur_str = typing_str[:n_chars]
                        f_ut = fit_font(od, typing_str, FONT_SANS, 0, int(38 * (0.92 if is_vert else 1.0)), 24, gx1 - gx0 - 64)
                        tx, ty = gx0 + 28, gy0 + 78
                        od.text((tx, ty), cur_str, font=f_ut, fill=RGB_PARC + (255,))
                        if int(t_clip * 3.0) % 2 == 0:
                            c_bb = od.textbbox((tx, ty), cur_str, font=f_ut)
                            cx_cur = c_bb[2] + 4 if cur_str else tx
                            od.rectangle([cx_cur, ty + 4, cx_cur + 3, ty + 40], fill=RGB_TERRA + (255,))

                        if lt >= 2.0:
                            p_ai = spring_ease((lt - 2.0) * 1.6, 190, 20)
                            if is_vert:
                                wp_pre = od.textbbox((0, 0), pre_txt, font=f_sans_kin)[2]
                                od.text(((W - wp_pre) // 2, 235), pre_txt, font=f_sans_kin, fill=RGB_PARC + (255,))
                                f_pop_fit = fit_font(od, pop_word, FONT_SERIF_IT, None, 145, 64, W - 160)
                                wp_pop = od.textbbox((0, 0), pop_word, font=f_pop_fit)[2]
                                od.text(((W - wp_pop) // 2, 305 + int((1.0 - p_ai) * 40)), pop_word, font=f_pop_fit, fill=RGB_PARC + (255,))
                            else:
                                od.text((1350, 395), pre_txt, font=f_sans_kin, fill=RGB_PARC + (255,))
                                f_pop_fit = fit_font(od, pop_word, FONT_SERIF_IT, None, 175, 72, W - 1440)
                                od.text((1410, 465 + int((1.0 - p_ai) * 45)), pop_word, font=f_pop_fit, fill=RGB_PARC + (255,))
                        if lt >= 3.2:
                            if is_vert:
                                wp_cta = od.textbbox((0, 0), cta_text, font=f_sans_sm)[2]
                                od.text(((W - wp_cta) // 2, 1565), cta_text, font=f_sans_sm, fill=RGB_TERRA + (255,))
                            else:
                                od.text((1385, 690), cta_text, font=f_sans_sm, fill=RGB_TERRA + (255,))

                        frame_bgr = paste_rgba_on_bgr(raw_bgr, ov)

                    pipe.stdin.write(frame_bgr.tobytes())

        pipe.stdin.close()
        pipe.stdin = None
        _, err = pipe.communicate()
        if pipe.returncode != 0:
            raise RuntimeError("Video pipe error: " + err.decode("utf-8", "ignore")[-1000:])

        concat_wav = os.path.join(proc_dir, "speech_concat.wav")
        list_txt = os.path.join(proc_dir, "wav_list.txt")
        with open(list_txt, "w", encoding="utf-8") as lf:
            for wv in norm_wavs:
                lf.write(f"file '{wv}'\n")
        subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c:a", "pcm_s16le", concat_wav],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        bgm_sfx_wav = os.path.join(proc_dir, "bgm_sfx.wav")
        generate_groove_and_sfx(total_dur, beat_events, keystroke_times, bgm_sfx_wav)

        mix_cmd = [
            FFMPEG, "-y",
            "-i", silent_mp4,
            "-i", concat_wav,
            "-i", bgm_sfx_wav,
            "-filter_complex",
            "[1:a]asplit=2[v1][v2];"
            "[2:a][v1]sidechaincompress=threshold=0.05:ratio=3.5:attack=15:release=180:makeup=1.0[bgm_duck];"
            "[bgm_duck]volume=0.82[bgm];"
            "[v2][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            out_path,
        ]
        subprocess.run(mix_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        print(f"Rendered final video ({total_dur:.2f}s, {W}x{H}): {out_path}")
    finally:
        if not args.keep_temp and os.path.exists(proc_dir):
            shutil.rmtree(proc_dir, ignore_errors=True)


def cmd_verify(args):
    video_path = os.path.abspath(args.video)
    if not os.path.exists(video_path):
        print(f"Error: video file not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    grid_path = os.path.abspath(args.output) if args.output else os.path.join(os.path.dirname(video_path), "verify_grid.jpg")
    os.makedirs(os.path.dirname(grid_path), exist_ok=True)

    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", video_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    v_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v_stream or not a_stream:
        print("Error: rendered file must contain both video and audio streams.", file=sys.stderr)
        sys.exit(1)

    dur = float(info["format"]["duration"])
    W = int(v_stream["width"])
    H = int(v_stream["height"])
    size_bytes = int(info["format"]["size"])

    cap = cv2.VideoCapture(video_path)
    total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)

    # Sample 9 evenly spaced visual beats
    sample_fracs = [0.06, 0.14, 0.21, 0.30, 0.45, 0.58, 0.72, 0.80, 0.94]
    cell_w = 640 if W >= H else 360
    cell_h = int(cell_w * H / W)
    cols, rows = 3, 3
    grid = np.full((rows * cell_h, cols * cell_w, 3), BGR_CARBON, dtype=np.uint8)
    ui_blue_pcts = []

    for idx, frac in enumerate(sample_fracs):
        f_idx = min(max(0, total_f - 1), int(frac * total_f))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, fr = cap.read()
        if not ret:
            print(f"Error: failed to decode frame {f_idx}", file=sys.stderr)
            sys.exit(1)
        if idx in (2, 5, 6):
            b_ch, g_ch, r_ch = fr[:, :, 0].astype(np.int16), fr[:, :, 1].astype(np.int16), fr[:, :, 2].astype(np.int16)
            ui_blue_pcts.append(float(np.mean((b_ch - r_ch > 15) & (b_ch - g_ch > 8)) * 100.0))
        thumb = cv2.resize(fr, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        r_idx, c_idx = divmod(idx, cols)
        y0, x0 = r_idx * cell_h, c_idx * cell_w
        grid[y0:y0 + cell_h, x0:x0 + cell_w] = thumb
        cv2.putText(grid, f"#{idx + 1} ({f_idx / fps:.1f}s)", (x0 + 16, y0 + 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, BGR_TERRA, 2, cv2.LINE_AA)
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
        "ui_beat_cold_blue_pct": round(float(np.mean(ui_blue_pcts)), 4) if ui_blue_pcts else 0.0,
        "verify_grid": grid_path,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Fable 5.2 Editorial Motion-Graphics Studio CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create starter project.json")
    p_init.add_argument("--output", "-o", required=True, help="Output project.json path")
    p_init.add_argument("--aspect", choices=["16:9", "9:16"], default="16:9")
    p_init.add_argument("--segments", type=int, default=3, help="Number of segments (>= 2)")
    p_init.add_argument("--hud-prefix", default="AI DAILY")
    p_init.add_argument("--cta", default="每天一分钟 · 点个关注")
    p_init.set_defaults(func=cmd_init)

    p_flow = sub.add_parser("flow-plan", help="Generate safety-filtered Google Flow prompts from project.json")
    p_flow.add_argument("--project", "-p", required=True, help="Input project.json path")
    p_flow.add_argument("--output", "-o", default="", help="Output flow_tasks.json path")
    p_flow.set_defaults(func=cmd_flow_plan)

    p_ren = sub.add_parser("render", help="Render final Fable-style MP4 from project.json")
    p_ren.add_argument("--project", "-p", required=True, help="Input project.json path")
    p_ren.add_argument("--output", "-o", default="", help="Output MP4 path")
    p_ren.add_argument("--keep-temp", action="store_true", help="Keep intermediate normalized WAV/MP4 temp dir")
    p_ren.set_defaults(func=cmd_render)

    p_ver = sub.add_parser("verify", help="Verify final MP4 streams and generate 9-beat contact sheet")
    p_ver.add_argument("--video", "-v", required=True, help="Input rendered MP4 path")
    p_ver.add_argument("--output", "-o", default="", help="Output verify_grid.jpg path")
    p_ver.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
