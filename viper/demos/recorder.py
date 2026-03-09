"""Recorder — Playwright screen recorder with timestamp-driven cue sync.

Actions fire at exact voiceover timestamps. No sleep() guesswork.
The recorder starts a monotonic clock at recording start, then for each
cue point: wait_until(cue.timestamp) → execute action.
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, sync_playwright

from viper.demos.voiceover import CuePoint


# ── Helpers ──────────────────────────────────────────────────────────

def _smooth_move(page: Page, x: int, y: int) -> None:
    page.mouse.move(x, y, steps=25)
    time.sleep(random.uniform(0.1, 0.2))


def _human_type(page: Page, selector: str, text: str) -> None:
    page.type(selector, text, delay=random.randint(50, 80))



def _send_message(page: Page, text: str) -> None:
    _human_type(page, "#chatInput", text)
    time.sleep(0.3)
    try:
        page.click(".chat-input button")
    except Exception:
        page.keyboard.press("Enter")


def _get_element_center(page: Page, selector: str) -> tuple[int, int]:
    box = page.locator(selector).bounding_box()
    if box is None:
        raise ValueError(f"Element {selector} not found or not visible")
    return int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)


def _scroll_chat(page: Page) -> None:
    page.evaluate(
        'document.getElementById("chatBody").scrollTop = '
        'document.getElementById("chatBody").scrollHeight'
    )


# ── Action functions ─────────────────────────────────────────────────

def _action_open_chat(page: Page) -> None:
    cx, cy = _get_element_center(page, "#chatFab")
    _smooth_move(page, cx, cy)
    page.click("#chatFab")
    page.wait_for_selector(".chat-window.open", timeout=5000)


def _action_type_insurance(page: Page) -> None:
    _send_message(page, "Do you accept Delta Dental insurance?")


def _action_type_booking(page: Page) -> None:
    _send_message(page, "How do I book an appointment?")


def _action_type_hours(page: Page) -> None:
    _send_message(page, "What are your hours on Saturday?")


def _action_type_doctor_question(page: Page) -> None:
    _send_message(page, "Is Dr. Kim available for a consultation this week?")


def _action_form_fill(page: Page) -> None:
    try:
        _scroll_chat(page)
        time.sleep(0.3)
        _human_type(page, "#leadName", "Sarah Johnson")
        time.sleep(0.3)
        _human_type(page, "#leadPhone", "603-555-0142")
        time.sleep(0.3)
        _human_type(page, "#leadEmail", "sarah.johnson@email.com")
        time.sleep(0.3)
        # Textarea is pre-filled with the original question; just verify it exists
        if page.locator("#leadNote").count() > 0:
            _scroll_chat(page)
    except Exception as e:
        print(f"  [recorder] Warning: form fill issue: {e}")


def _action_submit(page: Page) -> None:
    try:
        page.click(".lead-form button")
    except Exception:
        pass


# ── Action registry ──────────────────────────────────────────────────

DENTAL_ACTIONS: dict[str, Callable[[Page], None]] = {
    "open_chat": _action_open_chat,
    "type_insurance": _action_type_insurance,
    "type_booking": _action_type_booking,
    "type_hours": _action_type_hours,
    "type_doctor_question": _action_type_doctor_question,
    "form_fill": _action_form_fill,
    "submit": _action_submit,
}


# ── Timestamp-driven recorder ───────────────────────────────────────

def _wait_until(target_ts: float, rec_start: float) -> None:
    """Wait until target_ts seconds have elapsed since rec_start."""
    elapsed = time.monotonic() - rec_start
    remaining = target_ts - elapsed
    if remaining > 0:
        time.sleep(remaining)


def record_demo(
    demo_url: str,
    cue_sheet: list[CuePoint],
    total_duration: float,
    output_dir: Path,
    viewport: tuple[int, int] = (1920, 1080),
    action_map: dict[str, Callable[[Page], None]] | None = None,
) -> Path:
    """Record a demo video with cue-sheet-driven timing.

    Each cue point fires at its exact timestamp from recording start.
    The voiceover audio is laid over the recording in the compositor —
    they're in sync because the cues came from the voiceover alignment.

    Args:
        demo_url: URL of the live chatbot demo
        cue_sheet: list of CuePoint(action, timestamp) from voiceover alignment
        total_duration: total voiceover duration (recording stops after this + buffer)
        output_dir: directory for output video
        viewport: (width, height) for browser window
        action_map: action_name → callable (default: DENTAL_ACTIONS)

    Returns:
        Path to recorded WebM video file
    """
    if action_map is None:
        action_map = DENTAL_ACTIONS

    output_dir.mkdir(parents=True, exist_ok=True)
    vw, vh = viewport

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": vw, "height": vh},
            record_video_dir=str(output_dir),
            record_video_size={"width": vw, "height": vh},
        )
        page = context.new_page()
        page.set_viewport_size({"width": vw, "height": vh})

        print(f"  [recorder] Loading demo page ({vw}x{vh})...")
        page.goto(demo_url, wait_until="networkidle")

        # Start the clock — this is t=0, same as voiceover start
        rec_start = time.monotonic()

        # Fire each cue at its exact timestamp
        for cue in cue_sheet:
            action_fn = action_map.get(cue.action)
            if action_fn is None:
                print(f"  [recorder] Warning: no handler for '{cue.action}'")
                continue

            _wait_until(cue.timestamp, rec_start)
            elapsed = time.monotonic() - rec_start
            print(f"  [recorder] [{cue.action}] @ {elapsed:.1f}s (target: {cue.timestamp:.1f}s)")
            action_fn(page)

        # Hold until voiceover ends + 2s buffer
        _wait_until(total_duration + 2.0, rec_start)

        print("  [recorder] Finalizing recording...")
        video_path = page.video.path()
        context.close()
        browser.close()

    final_path = Path(video_path) if video_path else None
    if final_path and final_path.exists():
        print(f"  [recorder] Video saved: {final_path}")
        return final_path

    webms = list(output_dir.glob("*.webm"))
    if webms:
        print(f"  [recorder] Video saved: {webms[0]}")
        return webms[0]

    raise FileNotFoundError(f"No video found in {output_dir}")
