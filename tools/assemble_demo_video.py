"""Assemble the final demo MP4 from recorded scene clips.

Pipeline per scene: title card (PNG → short clip) + scene clip with a burned-in
lower-third caption, all encoded uniformly, then concatenated.

Input : tools/demo_build/scenes/scene_NN/scene_NN.webm  (from record_demo_scenes.py)
Output: tools/demo_build/demo.mp4

Usage:
    python tools/assemble_demo_video.py
"""

import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
BUILD = Path("tools/demo_build")
SCENES_DIR = BUILD / "scenes"
WORK = BUILD / "work"
W, H = 1920, 1080
FPS = 30
TITLE_SECONDS = 4.0

# (title, subtitle-on-card, lower-third caption during the scene, speed factor)
SCENES = [
    ("ESGA DataPower RCA Assistant",
     "Mid-incident, support engineers hunt through dense runbooks.\nWhat if you could just ask?",
     "AI-powered incident troubleshooting, grounded in ESGA runbooks", 1.0),
    ("How it works",
     "Runbooks → embeddings in Supabase (pgvector + full-text)\nHybrid retrieval + OpenAI = grounded, cited answers",
     "Hybrid RAG: dense + sparse retrieval over 9 runbooks", 1.0),
    ("Feature 1 · Error-code lookup",
     "Paste any DataPower error code",
     "Every claim cites its source — [Pattern · Section]", 1.0),
    ("Feature 2 · Symptom triage",
     "Describe what you're seeing",
     "Identifies the likely root cause — network issue, not one backend", 1.0),
    ("Feature 3 · Guided triage",
     "Returns the runbook's decision tree",
     "Surfaces the exact path plain keyword search misses", 1.0),
    ("Feature 4 · Step-by-step fixes",
     "Ordered, runbook-exact remediation steps",
     "JWT key rotation: capture token, match kid, update AAA policy", 1.0),
    ("Feature 5 · Escalation paths",
     "Returns the complete escalation matrix",
     "Every team, every SLA — nothing dropped", 1.0),
    ("Feature 6 · Multi-turn context",
     "Ask a follow-up — no need to restate the error",
     "“it” is resolved from the conversation automatically", 1.0),
    ("Feature 7 · Honest guardrails",
     "Out of scope? It says so.",
     "No hallucinated steps — answers stay inside the runbooks", 1.0),
    ("Measured quality",
     "Hit Rate@5 1.00 · Faithfulness 0.97 · Citations 100% · Hallucination 3%",
     "Evaluated on a 52-case golden test set with RAGAS", 1.0),
]

NAVY = (15, 23, 42)
WHITE = (241, 245, 249)
ACCENT = (96, 165, 250)


def font(size: int, bold: bool = False):
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    try:
        return ImageFont.truetype(rf"C:\Windows\Fonts\{name}", size)
    except OSError:
        return ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", size)


def make_title_card(idx: int, title: str, subtitle: str) -> Path:
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    f_title, f_sub = font(76, bold=True), font(42)
    tw = d.textlength(title, font=f_title)
    d.text(((W - tw) / 2, H / 2 - 130), title, font=f_title, fill=WHITE)
    d.rectangle([(W / 2 - 60, H / 2 - 20), (W / 2 + 60, H / 2 - 14)], fill=ACCENT)
    y = H / 2 + 30
    for line in subtitle.split("\n"):
        lw = d.textlength(line, font=f_sub)
        d.text(((W - lw) / 2, y), line, font=f_sub, fill=(148, 163, 184))
        y += 60
    p = WORK / f"title_{idx:02d}.png"
    img.save(p)
    return p


def make_caption_overlay(idx: int, caption: str) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f_cap = font(38, bold=True)
    pad_x, pad_y = 36, 20
    tw = d.textlength(caption, font=f_cap)
    bar_w = int(tw + 2 * pad_x)
    bar_h = 38 + 2 * pad_y
    x0 = (W - bar_w) // 2
    y0 = H - bar_h - 48
    d.rounded_rectangle([(x0, y0), (x0 + bar_w, y0 + bar_h)], radius=14,
                        fill=(15, 23, 42, 215))
    d.rectangle([(x0, y0), (x0 + 8, y0 + bar_h)], fill=ACCENT + (255,))
    d.text((x0 + pad_x, y0 + pad_y - 4), caption, font=f_cap, fill=WHITE)
    p = WORK / f"caption_{idx:02d}.png"
    img.save(p)
    return p


def ff(*args):
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args]
    subprocess.run(cmd, check=True)


ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "21",
       "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]


def build_title_clip(idx: int, png: Path) -> Path:
    out = WORK / f"title_{idx:02d}.mp4"
    ff("-loop", "1", "-t", str(TITLE_SECONDS), "-i", str(png),
       "-vf", f"scale={W}:{H}", *ENC, str(out))
    return out


def build_scene_clip(idx: int, caption_png: Path, speed: float) -> Path:
    src = SCENES_DIR / f"scene_{idx:02d}" / f"scene_{idx:02d}.webm"
    out = WORK / f"scene_{idx:02d}.mp4"
    vf = f"[0:v]scale={W}:{H}[v0];[v0][1:v]overlay=0:0[v1]"
    if speed != 1.0:
        vf += f";[v1]setpts=PTS/{speed}[v2]"
        last = "[v2]"
    else:
        last = "[v1]"
    ff("-i", str(src), "-i", str(caption_png),
       "-filter_complex", vf, "-map", last, *ENC, str(out))
    return out


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, (title, subtitle, caption, speed) in enumerate(SCENES, 1):
        src = SCENES_DIR / f"scene_{i:02d}" / f"scene_{i:02d}.webm"
        if not src.exists():
            print(f"[skip] scene {i:02d}: no recording found")
            continue
        print(f"[scene {i:02d}] building…", flush=True)
        t_png = make_title_card(i, title, subtitle)
        c_png = make_caption_overlay(i, caption)
        parts.append(build_title_clip(i, t_png))
        parts.append(build_scene_clip(i, c_png, speed))

    concat_list = WORK / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in parts),
        encoding="utf-8",
    )
    final = BUILD / "demo.mp4"
    ff("-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(final))

    # report duration
    probe = subprocess.run(
        [FFMPEG, "-i", str(final)], capture_output=True, text=True)
    for line in probe.stderr.splitlines():
        if "Duration" in line:
            print(line.strip())
    print(f"Final video → {final}  ({final.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
