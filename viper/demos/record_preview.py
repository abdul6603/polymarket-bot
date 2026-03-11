"""Record 25-30s demo preview videos showing real chatbot interactions.

Desktop: 1280x720 horizontal
Mobile:  390x844 vertical

Flow: open chat → ask about insurance → ask about booking → get responses
No audio — visual walkthrough only.
"""
from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DEMO_URL = "https://darkcode-ai.github.io/chatbot-demos/belknapdental-com/"
OUTPUT_DIR = Path.home() / "polymarket-bot" / "data" / "demos" / "preview_recordings"


def _human_type(page, selector: str, text: str, delay: int = 60):
    """Type like a human — character by character with delay."""
    page.click(selector)
    page.type(selector, text, delay=delay)


def _record_demo(page, viewport: dict, output_path: Path, is_mobile: bool = False):
    """Record a single demo walkthrough."""
    page.set_viewport_size(viewport)
    page.goto(DEMO_URL, wait_until="networkidle")
    page.wait_for_timeout(1500)

    # Open chat widget
    page.click("#chatFab")
    page.wait_for_timeout(1800)

    # Message 1: Insurance question
    _human_type(page, "#chatInput", "Do you accept Delta Dental insurance?")
    page.wait_for_timeout(400)
    page.click('button[aria-label="Send"]')
    page.wait_for_timeout(4000)  # Wait for response to render

    # Message 2: Appointment booking
    _human_type(page, "#chatInput", "How do I book an appointment?")
    page.wait_for_timeout(400)
    page.click('button[aria-label="Send"]')
    page.wait_for_timeout(4000)

    # Message 3: Hours question
    _human_type(page, "#chatInput", "What are your Saturday hours?")
    page.wait_for_timeout(400)
    page.click('button[aria-label="Send"]')
    page.wait_for_timeout(4500)

    # Let viewer absorb the full conversation
    page.evaluate("""
        const msgs = document.querySelector('.chat-messages');
        if (msgs) msgs.scrollTop = msgs.scrollHeight;
    """)
    page.wait_for_timeout(3000)


def record_desktop(output_dir: Path) -> Path:
    """Record 1280x720 horizontal desktop preview."""
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / "desktop-preview.webm"
    final_path = output_dir / "desktop-preview.mp4"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(output_dir / "_tmp_desktop"),
            record_video_size={"width": 1280, "height": 720},
        )
        page = context.new_page()

        _record_demo(page, {"width": 1280, "height": 720}, video_path)

        # Close to flush video
        page.close()
        context.close()
        browser.close()

    # Find the recorded video
    tmp_dir = output_dir / "_tmp_desktop"
    recorded = list(tmp_dir.glob("*.webm"))
    if not recorded:
        raise RuntimeError("No video recorded for desktop")

    # Convert to MP4
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-i", str(recorded[0]),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-movflags", "+faststart",
        "-an",  # No audio
        str(final_path),
    ], check=True, capture_output=True)

    # Cleanup
    for f in tmp_dir.iterdir():
        f.unlink()
    tmp_dir.rmdir()

    return final_path


def record_mobile(output_dir: Path) -> Path:
    """Record 390x844 vertical mobile preview."""
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / "mobile-preview.mp4"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            record_video_dir=str(output_dir / "_tmp_mobile"),
            record_video_size={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()

        _record_demo(page, {"width": 390, "height": 844}, final_path, is_mobile=True)

        page.close()
        context.close()
        browser.close()

    tmp_dir = output_dir / "_tmp_mobile"
    recorded = list(tmp_dir.glob("*.webm"))
    if not recorded:
        raise RuntimeError("No video recorded for mobile")

    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-i", str(recorded[0]),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-movflags", "+faststart",
        "-an",
        str(final_path),
    ], check=True, capture_output=True)

    for f in tmp_dir.iterdir():
        f.unlink()
    tmp_dir.rmdir()

    return final_path


if __name__ == "__main__":
    print("Recording desktop preview...")
    d = record_desktop(OUTPUT_DIR)
    print(f"Desktop: {d}")

    print("Recording mobile preview...")
    m = record_mobile(OUTPUT_DIR)
    print(f"Mobile: {m}")

    print("Done!")
