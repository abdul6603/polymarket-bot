"""Recorder — Playwright screen recorder with realistic human interactions."""
from __future__ import annotations

import random
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from viper.demos.voiceover import SegmentInfo

# Timing offsets: cumulative start time for each voiceover segment's action.
# Built from segment durations at runtime.


def _human_delay(low: float = 1.0, high: float = 2.0) -> None:
    """Pause like a human reading/thinking."""
    time.sleep(random.uniform(low, high))


def _smooth_move(page: Page, x: int, y: int) -> None:
    """Move mouse smoothly to target coordinates."""
    page.mouse.move(x, y, steps=25)
    time.sleep(random.uniform(0.1, 0.3))


def _human_type(page: Page, selector: str, text: str) -> None:
    """Type text with realistic variable speed."""
    page.type(selector, text, delay=random.randint(50, 80))


def _wait_for_response(page: Page, timeout: float = 10.0) -> None:
    """Wait for bot typing indicator to appear and disappear."""
    try:
        page.wait_for_selector("#typingIndicator", timeout=timeout * 1000)
        page.wait_for_selector("#typingIndicator", state="detached", timeout=timeout * 1000)
    except Exception:
        # Fallback: just wait a bit if typing indicator doesn't show
        time.sleep(2.0)
    time.sleep(0.5)  # Brief pause after response appears


def _send_message(page: Page, text: str) -> None:
    """Type a message in chat input and send it."""
    _human_type(page, "#chatInput", text)
    time.sleep(random.uniform(0.3, 0.5))
    # Click the send button explicitly (more reliable than Enter in headless)
    try:
        page.click(".chat-input button")
    except Exception:
        page.keyboard.press("Enter")


def _build_timing(segments: list[SegmentInfo]) -> dict[str, float]:
    """Build cumulative timing map from segment durations.

    Returns dict mapping segment_id -> duration in seconds.
    """
    return {seg.id: seg.duration_sec for seg in segments}


def _get_element_center(page: Page, selector: str) -> tuple[int, int]:
    """Get center coordinates of an element."""
    box = page.locator(selector).bounding_box()
    if box is None:
        raise ValueError(f"Element {selector} not found or not visible")
    return int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)


def record_demo(
    demo_url: str,
    segments: list[SegmentInfo],
    output_dir: Path,
    viewport: tuple[int, int] = (1920, 1080),
) -> Path:
    """Record a demo video with realistic browser interactions.

    Actions are timed to match voiceover segment durations.

    Args:
        demo_url: URL of the live chatbot demo
        segments: list of SegmentInfo with durations for timing
        output_dir: directory for output video
        viewport: (width, height) for the browser window

    Returns:
        Path to recorded WebM video file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    durations = _build_timing(segments)
    vw, vh = viewport

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": vw, "height": vh},
            record_video_dir=str(output_dir),
            record_video_size={"width": vw, "height": vh},
        )
        page = context.new_page()
        # Ensure viewport is set before navigation so CSS media queries fire
        page.set_viewport_size({"width": vw, "height": vh})

        print(f"  [recorder] Loading demo page ({vw}x{vh})...")
        page.goto(demo_url, wait_until="networkidle")

        # === Interaction sequence ===
        # Max 1-1.5s pause between actions — keep it continuous

        # 1. intro — Page loads, brief pause
        time.sleep(min(durations.get("intro", 3.0), 2.0))

        # 2. open_chat — Click chat FAB
        print("  [recorder] Opening chat...")
        cx, cy = _get_element_center(page, "#chatFab")
        _smooth_move(page, cx, cy)
        page.click("#chatFab")
        page.wait_for_selector(".chat-window.open", timeout=5000)
        time.sleep(1.0)

        # 3. ask_insurance — Type insurance question
        print("  [recorder] Asking about insurance...")
        _human_delay(0.5, 0.8)
        _send_message(page, "Do you accept Delta Dental insurance?")

        # 4. insurance_response — Wait for bot response
        _wait_for_response(page)
        time.sleep(1.0)

        # 5. book_appt — Click "Book Appointment" quick button
        print("  [recorder] Clicking Book Appointment...")
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

        # 6. appt_response — Wait for response
        _wait_for_response(page)
        time.sleep(1.0)

        # 7. ask_hours — Type hours question
        print("  [recorder] Asking about hours...")
        _human_delay(0.3, 0.6)
        _send_message(page, "What are your hours on Saturday?")

        # 8. hours_response — Wait for response
        _wait_for_response(page)
        time.sleep(1.2)

        # 9. trigger_form — Ask something to trigger lead capture
        print("  [recorder] Triggering lead form...")
        _human_delay(0.3, 0.6)
        _send_message(page, "hello there")

        # Wait for lead form to appear
        _wait_for_response(page)
        page.evaluate('document.getElementById("chatBody").scrollTop = document.getElementById("chatBody").scrollHeight')
        try:
            page.wait_for_selector(".lead-form", timeout=8000)
        except Exception:
            print("  [recorder] Retrying with fallback question...")
            _send_message(page, "hi")
            _wait_for_response(page)
            page.evaluate('document.getElementById("chatBody").scrollTop = document.getElementById("chatBody").scrollHeight')
            try:
                page.wait_for_selector(".lead-form", timeout=8000)
            except Exception:
                print("  [recorder] Warning: lead form not detected, continuing...")

        # 10. form_fill — Fill out the lead form
        print("  [recorder] Filling lead form...")
        time.sleep(0.5)

        try:
            page.evaluate('document.getElementById("chatBody").scrollTop = document.getElementById("chatBody").scrollHeight')
            time.sleep(0.3)

            _human_type(page, "#leadName", "Sarah Johnson")
            time.sleep(0.3)
            _human_type(page, "#leadPhone", "603-555-0142")
            time.sleep(0.3)
            _human_type(page, "#leadEmail", "sarah.johnson@email.com")
            time.sleep(0.3)
        except Exception as e:
            print(f"  [recorder] Warning: form fill issue: {e}")

        # 11. submit — Submit the form
        print("  [recorder] Submitting form...")
        time.sleep(0.5)
        try:
            page.click(".lead-form button")
        except Exception:
            pass

        # 12. closing — Pause on confirmation
        time.sleep(min(durations.get("closing", 6.0), 3.0))

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

    # Fallback: find any WebM in output dir
    webms = list(output_dir.glob("*.webm"))
    if webms:
        print(f"  [recorder] Video saved: {webms[0]}")
        return webms[0]

    raise FileNotFoundError(f"No video found in {output_dir}")
