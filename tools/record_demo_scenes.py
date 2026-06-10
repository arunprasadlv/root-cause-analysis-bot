"""Record the demo video scenes with Playwright (per docs/demo_script.md run sheet).

Each scene is captured as a separate .webm clip in tools/demo_build/scenes/.
Run assemble_demo_video.py afterwards to produce the final MP4.

Requires the app server running on localhost:8000 (warmed up).

Usage:
    python tools/record_demo_scenes.py [--scene N]   # default: all scenes
"""

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT_DIR = Path("tools/demo_build/scenes")
VIEWPORT = {"width": 1920, "height": 1080}

CHIP_ERROR_CODE = "What does error code 0x00d30003 mean"
CHIP_MULTI_TIMEOUT = "Multiple backend services are timing out"
CHIP_JWT = "HTTP 401 after a JWT signing key rotation"
CHIP_ESCALATION = "persisted for over 30 minutes"

TYPED_TRIAGE = "A backend connection timeout has occurred. How do I determine the root cause?"
TYPED_FOLLOWUP = "How do I fix it?"
TYPED_NEGATIVE = "How do I configure a new Multi-Protocol Gateway service from scratch?"


def smooth_scroll(page, to_y: int, steps: int = 25, pause: float = 0.05) -> None:
    cur = page.evaluate("window.scrollY")
    delta = (to_y - cur) / steps
    for i in range(steps):
        page.evaluate(f"window.scrollTo(0, {cur + delta * (i + 1)})")
        time.sleep(pause)


def wait_for_answer(page, prev_count: int, timeout_s: int = 90) -> None:
    """Wait until a new assistant message appears and streaming settles."""
    deadline = time.time() + timeout_s
    sel = ".msg.assistant, .message.assistant, .assistant"
    while time.time() < deadline:
        if page.locator(sel).count() > prev_count:
            break
        time.sleep(0.5)
    else:
        raise TimeoutError("assistant answer did not appear")
    # let the answer render fully, then hold so viewers can read
    last = page.locator(sel).last
    stable, prev_len = 0, -1
    while time.time() < deadline and stable < 2:
        cur_len = len(last.inner_text())
        stable = stable + 1 if cur_len == prev_len else 0
        prev_len = cur_len
        time.sleep(0.7)
    time.sleep(0.5)


def type_query(page, text: str, per_char: float = 0.045) -> None:
    box = page.locator("#query")
    box.click()
    box.type(text, delay=per_char * 1000)
    time.sleep(0.4)
    page.keyboard.press("Enter")


def assistant_count(page) -> int:
    return page.locator(".msg.assistant, .message.assistant, .assistant").count()


def run_chip(page, chip_text: str) -> None:
    prev = assistant_count(page)
    page.locator(".example-chip", has_text=chip_text).first.click()
    wait_for_answer(page, prev)


def run_typed(page, text: str) -> None:
    prev = assistant_count(page)
    type_query(page, text)
    wait_for_answer(page, prev)


def scroll_answer_into_view(page, hold: float = 8.0) -> None:
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    chat = page.locator("#chat")
    if chat.count():
        page.evaluate("const c=document.getElementById('chat'); c.scrollTop=c.scrollHeight;")
    time.sleep(hold)


# ── Scenes ────────────────────────────────────────────────────────────────────

def scene_01_hook(page):
    page.goto(BASE + "/")
    time.sleep(5)
    smooth_scroll(page, 500, steps=40, pause=0.06)
    time.sleep(4)


def scene_02_architecture(page):
    page.goto(BASE + "/")
    arch = page.locator("text=Architecture").first
    if arch.count():
        arch.scroll_into_view_if_needed()
    else:
        smooth_scroll(page, 900)
    time.sleep(5)
    smooth_scroll(page, page.evaluate("window.scrollY") + 500, steps=40, pause=0.06)
    time.sleep(5)


def scene_03_error_code(page):
    page.goto(BASE + "/chat")
    time.sleep(2)
    run_chip(page, CHIP_ERROR_CODE)
    scroll_answer_into_view(page, hold=14)


def scene_04_symptom(page):
    page.goto(BASE + "/chat")
    time.sleep(1.5)
    run_chip(page, CHIP_MULTI_TIMEOUT)
    scroll_answer_into_view(page, hold=12)


def scene_05_triage(page):
    page.goto(BASE + "/chat")
    time.sleep(1.5)
    run_typed(page, TYPED_TRIAGE)
    scroll_answer_into_view(page, hold=14)


def scene_06_procedure(page):
    page.goto(BASE + "/chat")
    time.sleep(1.5)
    run_chip(page, CHIP_JWT)
    scroll_answer_into_view(page, hold=14)


def scene_07_escalation(page):
    page.goto(BASE + "/chat")
    time.sleep(1.5)
    run_chip(page, CHIP_ESCALATION)
    scroll_answer_into_view(page, hold=14)


def scene_08_multiturn(page):
    page.goto(BASE + "/chat")
    time.sleep(1.5)
    run_chip(page, CHIP_ERROR_CODE)
    scroll_answer_into_view(page, hold=5)
    run_typed(page, TYPED_FOLLOWUP)
    scroll_answer_into_view(page, hold=14)


def scene_09_guardrails(page):
    page.goto(BASE + "/chat")
    time.sleep(1.5)
    run_typed(page, TYPED_NEGATIVE)
    scroll_answer_into_view(page, hold=12)


def scene_10_eval_report(page):
    page.goto(BASE + "/eval-report")
    time.sleep(5)
    smooth_scroll(page, 600, steps=40, pause=0.06)
    time.sleep(4)
    smooth_scroll(page, 1300, steps=40, pause=0.06)
    time.sleep(6)


SCENES = [
    scene_01_hook, scene_02_architecture, scene_03_error_code, scene_04_symptom,
    scene_05_triage, scene_06_procedure, scene_07_escalation, scene_08_multiturn,
    scene_09_guardrails, scene_10_eval_report,
]


def record_scene(pw, idx: int, fn) -> Path:
    scene_dir = OUT_DIR / f"scene_{idx:02d}"
    scene_dir.mkdir(parents=True, exist_ok=True)
    for old in scene_dir.glob("*.webm"):
        old.unlink()
    browser = pw.chromium.launch()
    context = browser.new_context(
        viewport=VIEWPORT,
        record_video_dir=str(scene_dir),
        record_video_size=VIEWPORT,
        device_scale_factor=1,
    )
    page = context.new_page()
    try:
        fn(page)
    finally:
        context.close()
        browser.close()
    clip = next(scene_dir.glob("*.webm"))
    final = scene_dir / f"scene_{idx:02d}.webm"
    if final.exists():
        final.unlink()
    clip.rename(final)
    return final


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", type=int, help="record a single scene (1-10)")
    args = parser.parse_args()

    import urllib.request
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=5)
    except Exception:
        print("ERROR: server not reachable on :8000 — start it first")
        sys.exit(1)

    targets = [(args.scene, SCENES[args.scene - 1])] if args.scene else list(
        enumerate(SCENES, 1))

    with sync_playwright() as pw:
        for idx, fn in targets:
            t0 = time.time()
            print(f"[scene {idx:02d}] recording…", flush=True)
            clip = record_scene(pw, idx, fn)
            print(f"[scene {idx:02d}] done in {time.time()-t0:.0f}s → {clip}", flush=True)

    print("All scenes recorded.")


if __name__ == "__main__":
    main()
