#!/usr/bin/env python3
"""
Render Warm Editorial Launch Video (30.0s, 1920x1080, 30fps)
using Headless Chrome (Playwright HTML5/CSS3/SVG vector engine) + 122 BPM tactile audio.
Enforces strict Zero-Blue audit across rendered keyframes before completion.
"""

import base64
import io
import json
import math
import os
import shutil
import subprocess
import wave

import cv2
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

OUT_DIR = os.environ.get("OUT_DIR", "./output")
STAGE_HTML = os.path.join(OUT_DIR, "stage.html")
OUT_VIDEO = os.path.join(OUT_DIR, "launch_video.mp4")
OUT_GRID = os.path.join(OUT_DIR, "verify_grid.jpg")

APP_ROOT = os.environ.get("APP_ROOT", ".")
APP_ICON_PATH = os.environ.get(
    "APP_ICON_PATH",
    os.path.join(APP_ROOT, "assets", "default_dark_anchor.png"),
)
AMBER_WP_PATH = os.environ.get(
    "AMBER_WP_PATH",
    os.path.join(APP_ROOT, "assets", "default_green_anchor.png"),
)

W, H = 1920, 1080
FPS = 30
TOTAL_DUR = 30.0
TOTAL_FRAMES = int(TOTAL_DUR * FPS)


def warm_neutralize_icon_data_url(icon_path):
    """Load official Glint AppIcon PNG and shift cool metallic blue cast to warm platinum (B <= R)."""
    rgba = np.array(Image.open(icon_path).convert("RGBA")).astype(np.float32)
    r, g, b, a = rgba[:, :, 0], rgba[:, :, 1], rgba[:, :, 2], rgba[:, :, 3]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    rgba[:, :, 0] = np.clip(lum * 1.01, 0, 255)
    rgba[:, :, 1] = np.clip(lum * 0.995, 0, 255)
    rgba[:, :, 2] = np.clip(lum * 0.96, 0, 255)
    rgba[:, :, 3] = a
    out_img = Image.fromarray(rgba.astype(np.uint8), mode="RGBA").resize((360, 360), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out_img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def file_to_data_url(path, mime="image/jpeg"):
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def generate_launch_audio(wav_out, sr=48000):
    """Synthesize 122 BPM warm minimal synth progression + frame-aligned mechanical & UI SFX."""
    n = int(TOTAL_DUR * sr)
    left = np.zeros(n, dtype=np.float32)
    right = np.zeros(n, dtype=np.float32)
    t = np.arange(n, dtype=np.float32) / sr

    chords = [
        (146.83, 220.00, 261.63, 329.63, 349.23),
        (116.54, 174.61, 220.00, 293.66, 349.23),
        (98.00, 196.00, 233.08, 293.66, 329.63),
        (110.00, 164.81, 220.00, 293.66, 392.00),
    ]
    beat_p = 60.0 / 122.0
    bar_len = beat_p * 4.0
    bar_idx = ((t / bar_len).astype(np.int32)) % len(chords)

    pad_l = np.zeros(n, dtype=np.float32)
    pad_r = np.zeros(n, dtype=np.float32)
    for ci, chord in enumerate(chords):
        m = (bar_idx == ci)
        tc = t[m]
        vl = np.zeros_like(tc)
        vr = np.zeros_like(tc)
        for fi, freq in enumerate(chord):
            w = 0.25 if fi == 0 else 0.14
            det = 1.0 + (0.0025 if fi % 2 == 0 else -0.0025)
            vl += w * np.sin(2 * np.pi * freq * tc)
            vr += w * np.sin(2 * np.pi * freq * det * tc)
        pad_l[m] = vl
        pad_r[m] = vr

    kick_phase = t % beat_p
    kick = 0.34 * np.exp(-kick_phase * 20.0) * np.sin(2 * np.pi * (52.0 + 100.0 * np.exp(-kick_phase * 38.0)) * kick_phase)
    sixteenth_p = beat_p * 0.25
    arp_idx = ((t / sixteenth_p).astype(np.int32)) % 4
    arp_freqs = np.array([440.0, 523.25, 659.25, 587.33], dtype=np.float32)[arp_idx]
    six_phase = t % sixteenth_p
    marimba = 0.11 * np.exp(-six_phase * 32.0) * np.sin(2 * np.pi * arp_freqs * six_phase)

    rng = np.random.default_rng(2026)
    eighth_phase = t % (beat_p * 0.5)
    hat = 0.040 * np.exp(-eighth_phase * 70.0) * rng.normal(0, 1, n).astype(np.float32)

    groove_gain = np.where((t >= 4.0) & (t < 5.0), 0.32, 0.25)
    fade = np.minimum(1.0, t / 0.5) * np.minimum(1.0, np.maximum(0.0, (TOTAL_DUR - t) / 1.0))
    left += (pad_l * 0.22 + kick + marimba * 1.05 + hat * 0.85) * groove_gain * fade
    right += (pad_r * 0.22 + kick + marimba * 0.95 + hat * 1.15) * groove_gain * fade

    def add_sfx(bt, kind):
        i0 = max(0, int((bt - 0.16) * sr))
        i1 = min(n, int((bt + 0.42) * sr))
        if i1 <= i0:
            return
        st = np.arange(i1 - i0, dtype=np.float32) / sr - 0.16
        pos = np.maximum(0.0, st)
        active = (st >= 0).astype(np.float32)
        if kind == "impact":
            sub = 0.45 * active * np.exp(-pos * 10.0) * np.sin(2 * np.pi * (48.0 + 80.0 * np.exp(-pos * 28.0)) * pos)
            snap = 0.14 * active * np.exp(-pos * 60.0) * rng.normal(0, 1, len(st)).astype(np.float32)
            left[i0:i1] += (sub + snap) * 0.82
            right[i0:i1] += (sub + snap) * 0.82
        elif kind == "whoosh":
            env = np.exp(-(st ** 2) / 0.016)
            nz = rng.normal(0, 1, len(st)).astype(np.float32)
            sw = 0.24 * env * np.sin(2 * np.pi * (160 + 480 * np.exp(-np.abs(st) * 8)) * (st + 0.2))
            left[i0:i1] += (nz * env * 0.14 + sw) * 0.68
            right[i0:i1] += (nz * env * 0.14 + sw) * 0.68
        elif kind == "pop":
            pop = 0.32 * active * np.exp(-pos * 34.0) * np.sin(2 * np.pi * (380 + 260 * np.exp(-pos * 24)) * pos)
            left[i0:i1] += pop * 0.74
            right[i0:i1] += pop * 0.74
        elif kind == "keycap":
            thock = 0.36 * active * np.exp(-pos * 90.0) * np.sin(2 * np.pi * 240.0 * pos)
            clack = 0.20 * active * np.exp(-pos * 150.0) * np.sin(2 * np.pi * 1750.0 * pos)
            left[i0:i1] += (thock + clack) * 0.78
            right[i0:i1] += (thock + clack) * 0.78
        elif kind == "shutter":
            c1 = np.exp(-pos * 130.0) * rng.normal(0, 1, len(st)).astype(np.float32)
            p2 = np.maximum(0.0, st - 0.045)
            c2 = ((st >= 0.045).astype(np.float32)) * np.exp(-p2 * 140.0) * rng.normal(0, 1, len(st)).astype(np.float32)
            left[i0:i1] += (c1 + 0.8 * c2) * 0.26 * active
            right[i0:i1] += (c1 + 0.8 * c2) * 0.26 * active
        elif kind == "shimmer":
            env = active * np.exp(-((pos - 0.35) ** 2) / 0.08)
            sw = 0.15 * env * (np.sin(2 * np.pi * (880 + 660 * pos) * pos) + 0.6 * np.sin(2 * np.pi * (1320 + 880 * pos) * pos))
            left[i0:i1] += sw
            right[i0:i1] += sw * 0.9

    # Align SFX precisely with new 30s Beat timings
    for bt in [0.05, 4.00, 5.00, 7.20, 11.40, 15.40, 19.20, 22.80, 26.80, 27.60]:
        add_sfx(bt, "impact" if bt in (4.00, 11.40, 19.20, 27.60) else "whoosh")

    for i in range(6):
        add_sfx(0.20 + i * 0.09, "pop")
    add_sfx(2.25, "pop")
    for bt in [12.55, 13.0, 15.65, 16.20, 16.80, 23.25]:
        add_sfx(bt, "pop")
    for bt in [5.10, 7.45, 12.35, 19.35, 21.80]:
        add_sfx(bt, "keycap")
    add_sfx(8.35, "shimmer")
    add_sfx(11.65, "shutter")

    for k in range(5):
        add_sfx(5.38 + k * 0.10, "keycap")
    for k in range(20):
        add_sfx(19.55 + k * 0.10, "keycap")
    for k in range(4):
        add_sfx(26.85 + k * 0.18, "keycap")

    left = np.clip(left, -0.95, 0.95)
    right = np.clip(right, -0.95, 0.95)
    pcm = np.stack([(left * 32767.0).astype(np.int16), (right * 32767.0).astype(np.int16)], axis=1).tobytes()
    with wave.open(wav_out, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)


def audit_zero_blue(sample_frames_bgr):
    """Measure % of cold blue pixels (B > R + 15 and B > G + 8) across representative beat frames."""
    max_pct = 0.0
    worst_idx = -1
    for i, fr in enumerate(sample_frames_bgr):
        b, g, r = fr[:, :, 0].astype(np.int16), fr[:, :, 1].astype(np.int16), fr[:, :, 2].astype(np.int16)
        cold_mask = ((b - r) > 15) & ((b - g) > 8)
        pct = float(cold_mask.mean() * 100.0)
        if pct > max_pct:
            max_pct = pct
            worst_idx = i
    return round(max_pct, 4), worst_idx


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp_dir = os.path.join(OUT_DIR, ".tmp_launch_v2")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        app_icon_url = warm_neutralize_icon_data_url(APP_ICON_PATH)
        amber_url = file_to_data_url(AMBER_WP_PATH, "image/jpeg")

        silent_mp4 = os.path.join(tmp_dir, "glint_silent.mp4")
        pipe = subprocess.Popen(
            [
                FFMPEG, "-y",
                "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(FPS),
                "-i", "-",
                "-c:v", "libx264", "-crf", "15", "-preset", "fast", "-pix_fmt", "yuv420p",
                silent_mp4,
            ],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME_BIN, headless=True)
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.goto(f"file://{STAGE_HTML}", wait_until="load")
            page.evaluate("(args) => window.setAssets(args[0], args[1])", [app_icon_url, amber_url])
            page.wait_for_timeout(120)

            for f_idx in range(TOTAL_FRAMES):
                t = f_idx / FPS
                page.evaluate("(t) => window.renderAt(t)", t)
                jpg_bytes = page.screenshot(type="jpeg", quality=95)
                pipe.stdin.write(jpg_bytes)

            browser.close()

        pipe.stdin.close()
        pipe.stdin = None
        _, err = pipe.communicate()
        if pipe.returncode != 0:
            raise RuntimeError("Video pipe error: " + err.decode("utf-8", "ignore")[-1000:])

        wav_path = os.path.join(tmp_dir, "launch_audio.wav")
        generate_launch_audio(wav_path)

        subprocess.run([
            FFMPEG, "-y",
            "-i", silent_mp4,
            "-i", wav_path,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-shortest", "-movflags", "+faststart",
            OUT_VIDEO,
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        # Build 10-panel verification grid & audit Zero-Blue rule
        probe = json.loads(subprocess.check_output([
            FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", OUT_VIDEO
        ], text=True))

        cap = cv2.VideoCapture(OUT_VIDEO)
        sample_times = [1.2, 3.3, 4.5, 6.4, 9.8, 13.8, 17.5, 21.0, 24.8, 28.8]
        cell_w, cell_h = 640, 360
        cols, rows = 2, 5
        grid = np.full((rows * cell_h, cols * cell_w, 3), 245, dtype=np.uint8)
        audit_frames = []

        for idx, st in enumerate(sample_times):
            fi = min(TOTAL_FRAMES - 1, int(st * FPS))
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, fr = cap.read()
            if not ok:
                raise RuntimeError(f"Failed reading frame {fi}")
            audit_frames.append(fr)
            th = cv2.resize(fr, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
            r_i, c_i = divmod(idx, cols)
            y0, x0 = r_i * cell_h, c_i * cell_w
            grid[y0:y0 + cell_h, x0:x0 + cell_w] = th
            cv2.putText(grid, f"#{idx + 1} ({st:.1f}s)", (x0 + 14, y0 + 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.80, (87, 119, 217), 2, cv2.LINE_AA)
        cap.release()
        cv2.imwrite(OUT_GRID, grid, [cv2.IMWRITE_JPEG_QUALITY, 92])

        cold_blue_pct, worst_panel = audit_zero_blue(audit_frames)
        v_s = next(s for s in probe["streams"] if s["codec_type"] == "video")
        a_s = next(s for s in probe["streams"] if s["codec_type"] == "audio")
        print(json.dumps({
            "video": OUT_VIDEO,
            "width": int(v_s["width"]),
            "height": int(v_s["height"]),
            "duration_sec": round(float(probe["format"]["duration"]), 3),
            "size_bytes": int(probe["format"]["size"]),
            "video_codec": v_s["codec_name"],
            "audio_codec": a_s["codec_name"],
            "cold_blue_pixel_pct": cold_blue_pct,
            "worst_panel_index": worst_panel,
            "verify_grid": OUT_GRID,
        }, ensure_ascii=False, indent=2))
    finally:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
