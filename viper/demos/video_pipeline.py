"""Video Pipeline — Orchestrator + CLI for automated demo video generation.

Architecture: ONE continuous voiceover with character-level timestamps
from ElevenLabs. Cue phrases in the script map to exact timestamps.
The recorder fires actions at those timestamps. Perfect sync, permanently.

Usage:
    python -m viper.demos.video_pipeline --business dental
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from viper.demos.compositor import composite
from viper.demos.recorder import record_with_verify, verify_demo, DENTAL_ACTIONS
from viper.demos.voiceover import generate_voiceover, load_cached_voiceover


@dataclass
class DemoConfig:
    """Configuration for a demo video."""
    business_name: str
    demo_url: str
    script_text: str
    cues: list[dict]
    output_dir: Path


# ── Dental voiceover — one continuous script with cue phrases ──

DENTAL_SCRIPT_TEXT = (
    "Hey, check this out — I built a custom AI assistant for Belknap Dental. "
    "Watch what happens when a patient visits the website and clicks the chat. "
    "Let's ask about insurance — this is the number one question dental offices get. "
    "See how it instantly knows which plans they accept — Cigna, MetLife, Blue Cross — "
    "no phone call needed. The patient gets their answer in seconds. "
    "Now let's try booking an appointment. "
    "It gives patients a direct way to schedule, twenty-four seven, "
    "even when the office is closed. No more missed calls. "
    "And if they ask about hours on the weekend... "
    "It handles common questions like Saturday hours automatically. "
    "The front desk never has to answer this again. "
    "Now here's the best part — watch what happens when someone asks about a specific doctor. "
    "The bot knows your doctors. Dr. Jefferson Kim is the lead dentist here. "
    "For something this specific, it connects the patient directly — "
    "capturing their name, phone, and email as a lead. "
    "And their original question is saved right in the form. "
    "Name, phone, email — sent straight to the office. "
    "This runs twenty-four seven, never calls in sick, and pays for itself "
    "in the first week. I built this specifically for your practice — "
    "want me to set it up?"
)

# Each cue fires the action when the voice starts saying the phrase.
# Phrases must appear VERBATIM in DENTAL_SCRIPT_TEXT above.
DENTAL_CUES = [
    {"action": "open_chat", "at_phrase": "clicks the chat"},
    {"action": "type_insurance", "at_phrase": "ask about insurance"},
    {"action": "type_booking", "at_phrase": "try booking"},
    {"action": "type_hours", "at_phrase": "ask about hours"},
    {"action": "type_doctor_question", "at_phrase": "asks about a specific doctor"},
    {"action": "form_fill", "at_phrase": "capturing their name"},
    {"action": "submit", "at_phrase": "sent straight"},
]

DENTAL_CONFIG = DemoConfig(
    business_name="Belknap Dental",
    demo_url="https://darkcode-ai.github.io/chatbot-demos/belknapdental-com/",
    script_text=DENTAL_SCRIPT_TEXT,
    cues=DENTAL_CUES,
    output_dir=Path.home() / "polymarket-bot" / "data" / "demos" / "videos",
)

CONFIGS = {
    "dental": DENTAL_CONFIG,
}


def _load_api_key() -> str:
    """Load ElevenLabs API key from env or soren .env."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        env_path = Path.home() / "soren-content" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ELEVENLABS_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY not found. Set it in env or ~/soren-content/.env"
        )
    return api_key


def generate_demo(config: DemoConfig, skip_voiceover: bool = False) -> tuple[Path, Path]:
    """Generate a complete demo video — self-verifying, auto-retry.

    Pipeline:
        1. Verify chatbot works (fast DOM replay, ~15s)
        2. Generate/load voiceover with character-level timestamps
        3. Record desktop + mobile with retry (up to 3 attempts each)
        4. Composite video + audio
        5. Print PASS/FAIL

    Returns:
        tuple of (horizontal_mp4, vertical_mp4)
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    vo_dir = config.output_dir / "voiceover"

    # Step 1: Verify chatbot before recording
    print("\n=== Step 1: Verifying chatbot responds correctly ===")
    result = verify_demo(config.demo_url)
    for line in [result.summary()]:
        print(f"  [verify] {line}")
    if not result.passed:
        for f in result.failures:
            print(f"  [verify]   - {f}")
        print("\n  FAIL — chatbot verification failed. Fix the chatbot before recording.")
        raise RuntimeError(f"Chatbot verification failed: {result.summary()}")
    print(f"  [verify] All {result.checks_run} checks passed")

    # Step 2: Generate/load voiceover
    vo_result = None
    if skip_voiceover:
        vo_result = load_cached_voiceover(vo_dir)
        if vo_result:
            print("\n=== Step 2: Reusing cached voiceover + cue sheet ===")
            print(f"  Duration: {vo_result.duration_sec:.1f}s, {len(vo_result.cue_sheet)} cues")

    if vo_result is None:
        print("\n=== Step 2: Generating voiceover with timestamps ===")
        api_key = _load_api_key()
        vo_result = generate_voiceover(
            config.script_text, config.cues, api_key, vo_dir,
        )

    # Step 3: Record with retry
    print("\n=== Step 3a: Recording desktop (1920x1080) ===")
    rec_desktop = config.output_dir / "recording_desktop"
    video_path = record_with_verify(
        config.demo_url, vo_result.cue_sheet, vo_result.duration_sec,
        rec_desktop, viewport=(1920, 1080),
    )

    print("\n=== Step 3b: Recording mobile (390x844) ===")
    rec_mobile = config.output_dir / "recording_mobile"
    mobile_video_path = record_with_verify(
        config.demo_url, vo_result.cue_sheet, vo_result.duration_sec,
        rec_mobile, viewport=(390, 844),
    )

    # Step 4: Composite
    print("\n=== Step 4: Compositing final videos ===")
    h_path, v_path = composite(
        video_path, vo_result.audio_path, config.output_dir, config.business_name,
        vertical_video_path=mobile_video_path,
    )

    # Step 5: Final status
    h_ok = h_path.exists() and h_path.stat().st_size > 100_000
    v_ok = v_path.exists() and v_path.stat().st_size > 100_000

    print()
    if h_ok and v_ok:
        h_mb = h_path.stat().st_size / 1_048_576
        v_mb = v_path.stat().st_size / 1_048_576
        print(f"  PASS — horizontal.mp4 ({h_mb:.1f}MB) and vertical.mp4 ({v_mb:.1f}MB) ready")
        print(f"  Horizontal: {h_path}")
        print(f"  Vertical:   {v_path}")
    else:
        reason = []
        if not h_ok:
            reason.append("horizontal.mp4 missing or too small")
        if not v_ok:
            reason.append("vertical.mp4 missing or too small")
        msg = "; ".join(reason)
        print(f"  FAIL — {msg}")
        raise RuntimeError(f"Pipeline failed: {msg}")

    return h_path, v_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate automated demo videos")
    parser.add_argument(
        "--business",
        choices=list(CONFIGS.keys()),
        required=True,
        help="Business type to generate demo for",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output directory",
    )
    parser.add_argument(
        "--skip-voiceover",
        action="store_true",
        help="Reuse cached voiceover + cue sheet (saves ElevenLabs credits)",
    )
    args = parser.parse_args()

    config = CONFIGS[args.business]
    if args.output_dir:
        config.output_dir = args.output_dir

    generate_demo(config, skip_voiceover=args.skip_voiceover)


if __name__ == "__main__":
    main()
