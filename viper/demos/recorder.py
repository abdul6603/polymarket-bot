"""Recorder — Playwright screen recorder with timestamp-driven sync.

Actions are gated by voiceover segment durations. Each segment maps to
an action; the recorder executes the action, then waits exactly the
remaining segment time before advancing. This guarantees the visual
actions are in sync with the voiceover audio.
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, sync_playwright

from viper.demos.voiceover import SegmentInfo


# ── Helpers ──────────────────────────────────────────────────────────

def _smooth_move(page: Page, x: int, y: int) -> None:
    """Move mouse smoothly to target coordinates."""
    page.mouse.move(x, y, steps=25)
    time.sleep(random.uniform(0.1, 0.2))


def _human_type(page: Page, selector: str, text: str) -> None:
    """Type text with realistic variable speed."""
    page.type(selector, text, delay=random.randint(50, 80))


def _wait_for_response(page: Page, timeout: float = 10.0) -> None:
    """Wait for bot typing indicator to appear and disappear."""
    try:
        page.wait_for_selector("#typingIndicator", timeout=timeout * 1000)
        page.wait_for_selector("#typingIndicator", state="detached", timeout=timeout * 1000)
    except Exception:
        time.sleep(1.5)


def _send_message(page: Page, text: str) -> None:
    """Type a message in chat input and send it."""
    _human_type(page, "#chatInput", text)
    time.sleep(0.3)
    try:
        page.click(".chat-input button")
    except Exception:
        page.keyboard.press("Enter")


def _get_element_center(page: Page, selector: str) -> tuple[int, int]:
    """Get center coordinates of an element."""
    box = page.locator(selector).bounding_box()
    if box is None:
        raise ValueError(f"Element {selector} not found or not visible")
    return int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)


def _wait_remaining(start: float, segment_duration: float) -> None:
    """Sleep for the remainder of a segment's duration.

    If the action took longer than the segment, skip (don't sleep negative).
    """
    elapsed = time.monotonic() - start
    remaining = segment_duration - elapsed
    if remaining > 0:
        time.sleep(remaining)


# ── Action functions ─────────────────────────────────────────────────
# Each takes (page,) and performs one visual step.
# The caller handles timing via _wait_remaining().

def _action_intro(page: Page) -> None:
    """Page is visible, viewer reads the landing page."""
    pass  # Just hold — page already loaded


def _action_open_chat(page: Page) -> None:
    """Move to chat FAB and click it open."""
    cx, cy = _get_element_center(page, "#chatFab")
    _smooth_move(page, cx, cy)
    page.click("#chatFab")
    page.wait_for_selector(".chat-window.open", timeout=5000)


def _action_ask_insurance(page: Page) -> None:
    """Type and send the insurance question."""
    _send_message(page, "Do you accept Delta Dental insurance?")


def _action_insurance_response(page: Page) -> None:
    """Wait for bot response, viewer reads it."""
    _wait_for_response(page)


def _action_book_appt(page: Page) -> None:
    """Click the Book Appointment quick button."""
    try:
        btn = page.locator('.quick-btn:has-text("Book Appointment")')
        if btn.count() > 0:
            bx, by = _get_element_center(page, '.quick-btn:has-text("Book Appointment")')
            _smooth_move(page, bx, by)
            btn.click()
        else:
            _send_message(page, "How do I book an appointment?")
    except Exception:
        _send_message(page, "How do I book an appointment?")


def _action_appt_response(page: Page) -> None:
    """Wait for bot response, viewer reads it."""
    _wait_for_response(page)


def _action_ask_hours(page: Page) -> None:
    """Type and send the hours question."""
    _send_message(page, "What are your hours on Saturday?")


def _action_hours_response(page: Page) -> None:
    """Wait for bot response, viewer reads it."""
    _wait_for_response(page)


def _action_trigger_form(page: Page) -> None:
    """Send unmatchable message to trigger lead capture form."""
    _send_message(page, "hello there")
    _wait_for_response(page)
    page.evaluate(
        'document.getElementById("chatBody").scrollTop = '
        'document.getElementById("chatBody").scrollHeight'
    )
    try:
        page.wait_for_selector(".lead-form", timeout=8000)
    except Exception:
        # Retry with a different unmatchable message
        _send_message(page, "hi")
        _wait_for_response(page)
        page.evaluate(
            'document.getElementById("chatBody").scrollTop = '
            'document.getElementById("chatBody").scrollHeight'
        )
        try:
            page.wait_for_selector(".lead-form", timeout=8000)
        except Exception:
            pass


def _action_form_fill(page: Page) -> None:
    """Fill out the lead capture form fields."""
    try:
        page.evaluate(
            'document.getElementById("chatBody").scrollTop = '
            'document.getElementById("chatBody").scrollHeight'
        )
        time.sleep(0.3)
        _human_type(page, "#leadName", "Sarah Johnson")
        time.sleep(0.3)
        _human_type(page, "#leadPhone", "603-555-0142")
        time.sleep(0.3)
        _human_type(page, "#leadEmail", "sarah.johnson@email.com")
    except Exception as e:
        print(f"  [recorder] Warning: form fill issue: {e}")


def _action_submit(page: Page) -> None:
    """Click the lead form submit button."""
    try:
        page.click(".lead-form button")
    except Exception:
        pass


def _action_closing(page: Page) -> None:
    """Hold on the confirmation message."""
    pass  # Just hold — viewer reads the thank-you


# ── Segment → action mapping ────────────────────────────────────────

DENTAL_ACTIONS: dict[str, Callable[[Page], None]] = {
    "intro": _action_intro,
    "open_chat": _action_open_chat,
    "ask_insurance": _action_ask_insurance,
    "insurance_response": _action_insurance_response,
    "book_appt": _action_book_appt,
    "appt_response": _action_appt_response,
    "ask_hours": _action_ask_hours,
    "hours_response": _action_hours_response,
    "trigger_form": _action_trigger_form,
    "form_fill": _action_form_fill,
    "submit": _action_submit,
    "closing": _action_closing,
}


# ── Main recorder ───────────────────────────────────────────────────

def record_demo(
    demo_url: str,
    segments: list[SegmentInfo],
    output_dir: Path,
    viewport: tuple[int, int] = (1920, 1080),
    action_map: dict[str, Callable[[Page], None]] | None = None,
) -> Path:
    """Record a demo video with segment-driven timing.

    Each voiceover segment maps to an action. The recorder:
    1. Starts a timer for the segment
    2. Executes the action
    3. Waits exactly (segment_duration - elapsed) before next segment

    This guarantees voice and screen are in sync.

    Args:
        demo_url: URL of the live chatbot demo
        segments: list of SegmentInfo with durations for timing
        output_dir: directory for output video
        viewport: (width, height) for the browser window
        action_map: segment_id → action function (default: DENTAL_ACTIONS)

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

        # Execute each segment action, gated by voiceover duration
        for seg in segments:
            action = action_map.get(seg.id)
            if action is None:
                print(f"  [recorder] Warning: no action for segment '{seg.id}', waiting...")
                time.sleep(seg.duration_sec)
                continue

            print(f"  [recorder] [{seg.id}] ({seg.duration_sec:.1f}s)")
            seg_start = time.monotonic()
            action(page)
            _wait_remaining(seg_start, seg.duration_sec)

        # Close browser to finalize recording
        print("  [recorder] Finalizing recording...")
        video_path = page.video.path()
        context.close()
        browser.close()

    # Playwright saves as WebM; find the file
    final_path = Path(video_path) if video_path else None
    if final_path and final_path.exists():
        print(f"  [recorder] Video saved: {final_path}")
        return final_path

    webms = list(output_dir.glob("*.webm"))
    if webms:
        print(f"  [recorder] Video saved: {webms[0]}")
        return webms[0]

    raise FileNotFoundError(f"No video found in {output_dir}")
