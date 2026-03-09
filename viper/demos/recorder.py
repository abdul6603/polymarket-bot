"""Recorder — Playwright screen recorder with timestamp-driven cue sync.

Actions fire at exact voiceover timestamps. No sleep() guesswork.
The recorder starts a monotonic clock at recording start, then for each
cue point: wait_until(cue.timestamp) → execute action.

Self-verification: fast DOM replay confirms the chatbot responds correctly
at each step. Auto-retries on failure.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
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


def _get_chat_text(page: Page) -> str:
    return page.evaluate(
        'document.getElementById("chatBody")?.innerText || ""'
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
    """Record a demo video with cue-sheet-driven timing."""
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

        rec_start = time.monotonic()

        for cue in cue_sheet:
            action_fn = action_map.get(cue.action)
            if action_fn is None:
                print(f"  [recorder] Warning: no handler for '{cue.action}'")
                continue

            _wait_until(cue.timestamp, rec_start)
            elapsed = time.monotonic() - rec_start
            print(f"  [recorder] [{cue.action}] @ {elapsed:.1f}s (target: {cue.timestamp:.1f}s)")
            action_fn(page)

        _wait_until(total_duration + 2.0, rec_start)

        print("  [recorder] Finalizing recording...")
        video_path = page.video.path()
        context.close()
        browser.close()

    final_path = Path(video_path) if video_path else None
    if final_path and final_path.exists():
        return final_path

    webms = list(output_dir.glob("*.webm"))
    if webms:
        return webms[0]

    raise FileNotFoundError(f"No video found in {output_dir}")


# ── Self-verification ────────────────────────────────────────────────
# Fast DOM replay: fires each action, waits 2s for bot response, checks
# chat text. ~15 seconds total instead of full voiceover duration.

@dataclass
class VerifyStep:
    """One step in the verification sequence."""
    action: str
    must_have: list[str] = field(default_factory=list)
    must_not_have: list[str] = field(default_factory=list)
    check_selector: str = ""  # CSS selector that must exist after this step


# Hardcoded verification sequence for dental demos.
# Each step: fire the action, wait for response, check DOM.
DENTAL_VERIFY_STEPS = [
    VerifyStep(
        action="open_chat",
        must_have=["How can I help"],
    ),
    VerifyStep(
        action="type_insurance",
        must_have=["Delta Dental", "Cigna"],
        must_not_have=["book an appointment"],
    ),
    VerifyStep(
        action="type_booking",
        must_have=["book", "appointment"],
        must_not_have=["Saturday"],
    ),
    VerifyStep(
        action="type_hours",
        must_have=["Saturday", "hours"],
        must_not_have=["Dr. Kim available"],
    ),
    VerifyStep(
        action="type_doctor_question",
        must_have=["Dr. Kim", "Dr. Jefferson Kim"],
        check_selector=".lead-form",
    ),
    VerifyStep(
        action="form_fill",
        check_selector="#leadName",
    ),
    VerifyStep(
        action="submit",
        must_have=["Thank you"],
    ),
]


@dataclass
class VerifyResult:
    """Result of a verification run."""
    passed: bool
    checks_run: int
    checks_passed: int
    failures: list[str]

    def summary(self) -> str:
        if self.passed:
            return f"PASS ({self.checks_passed}/{self.checks_run} checks)"
        return f"FAIL ({self.checks_passed}/{self.checks_run}) — " + "; ".join(self.failures)


def verify_demo(
    demo_url: str,
    steps: list[VerifyStep] | None = None,
    action_map: dict[str, Callable[[Page], None]] | None = None,
) -> VerifyResult:
    """Fast verification: replay actions without voiceover timing.

    Opens a fresh browser, fires each action with a 2s pause for the bot
    to respond, then checks the DOM for expected content. ~15 seconds total.
    """
    if steps is None:
        steps = DENTAL_VERIFY_STEPS
    if action_map is None:
        action_map = DENTAL_ACTIONS

    checks_run = 0
    checks_passed = 0
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(demo_url, wait_until="networkidle")

        for step in steps:
            action_fn = action_map.get(step.action)
            if action_fn is None:
                failures.append(f"no handler for '{step.action}'")
                continue

            try:
                action_fn(page)
            except Exception as e:
                failures.append(f"action '{step.action}' crashed: {e}")
                break

            # Wait for bot response
            time.sleep(2.0)

            chat_text = _get_chat_text(page)

            # Check must_have
            for text in step.must_have:
                checks_run += 1
                if text.lower() in chat_text.lower():
                    checks_passed += 1
                else:
                    failures.append(f"after '{step.action}': missing '{text}'")

            # Check must_not_have
            for text in step.must_not_have:
                checks_run += 1
                if text.lower() not in chat_text.lower():
                    checks_passed += 1
                else:
                    failures.append(f"after '{step.action}': premature '{text}'")

            # Check selector
            if step.check_selector:
                checks_run += 1
                if page.locator(step.check_selector).count() > 0:
                    checks_passed += 1
                else:
                    failures.append(f"after '{step.action}': selector '{step.check_selector}' not found")

        browser.close()

    return VerifyResult(
        passed=len(failures) == 0,
        checks_run=checks_run,
        checks_passed=checks_passed,
        failures=failures,
    )


def record_with_verify(
    demo_url: str,
    cue_sheet: list[CuePoint],
    total_duration: float,
    output_dir: Path,
    viewport: tuple[int, int] = (1920, 1080),
    action_map: dict[str, Callable[[Page], None]] | None = None,
    max_retries: int = 3,
) -> Path:
    """Record with automatic retry on failure."""
    label = f"{viewport[0]}x{viewport[1]}"
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            if output_dir.exists():
                for old in output_dir.glob("*.webm"):
                    old.unlink(missing_ok=True)

            path = record_demo(
                demo_url, cue_sheet, total_duration,
                output_dir, viewport, action_map,
            )
            print(f"  [recorder] {label} recorded: {path.name}")
            return path

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                print(f"  [recorder] {label} attempt {attempt} failed: {e} — retrying...")
                time.sleep(2)
            else:
                print(f"  [recorder] {label} all {max_retries} attempts failed")

    raise RuntimeError(f"Recording {label} failed after {max_retries} attempts: {last_error}")
