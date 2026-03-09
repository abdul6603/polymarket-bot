"""Video Pipeline — Orchestrator + CLI for automated demo video generation.

Usage:
    python -m viper.demos.video_pipeline --business dental
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from viper.demos.compositor import composite
from viper.demos.recorder import record_demo
from viper.demos.voiceover import SegmentInfo, concat_voiceover, generate_segments


@dataclass
class DemoConfig:
    """Configuration for a demo video."""
    business_name: str
    demo_url: str
    voiceover_script: list[dict]
    output_dir: Path


# ── Pre-built dental config ──
DENTAL_SCRIPT = [
    {
        "id": "intro",
        "text": "Hey, check this out — I built a custom AI assistant for Belknap Dental.",
    },
    {
        "id": "open_chat",
        "text": "Watch what happens when a patient visits the website and clicks the chat.",
    },
    {
        "id": "ask_insurance",
        "text": "Let's ask about insurance — this is the number one question dental offices get.",
    },
    {
        "id": "insurance_response",
        "text": "Boom — instant answer. No staff time wasted.",
    },
    {
        "id": "book_appt",
        "text": "Now let's try booking an appointment.",
    },
    {
        "id": "appt_response",
        "text": "It walks them right through the process.",
    },
    {
        "id": "ask_hours",
        "text": "And if they ask about hours...",
    },
    {
        "id": "hours_response",
        "text": "Right there. Twenty-four seven. Even when the office is closed.",
    },
    {
        "id": "trigger_form",
        "text": "Now here's the best part — when a patient needs something the bot can't handle...",
    },
    {
        "id": "form_fill",
        "text": "It automatically captures their info as a lead.",
    },
    {
        "id": "submit",
        "text": "Name, phone, email — sent straight to the office.",
    },
    {
        "id": "closing",
        "text": (
            "This runs twenty-four seven, never calls in sick, and pays for itself "
            "in the first week. I built this specifically for your practice — "
            "want me to set it up?"
        ),
    },
]

DENTAL_CONFIG = DemoConfig(
    business_name="Belknap Dental",
    demo_url="https://darkcode-ai.github.io/chatbot-demos/belknapdental-com/",
    voiceover_script=DENTAL_SCRIPT,
    output_dir=Path.home() / "polymarket-bot" / "data" / "demos" / "videos",
)

CONFIGS = {
    "dental": DENTAL_CONFIG,
}


def generate_demo(config: DemoConfig, skip_voiceover: bool = False) -> tuple[Path, Path]:
    """Generate a complete demo video.

    Pipeline:
        1. Generate voiceover segments (ElevenLabs)
        2. Record browser demo (Playwright)
        3. Composite video + audio (moviepy/ffmpeg)

    Returns:
        tuple of (horizontal_mp4, vertical_mp4)
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)

    # Load ElevenLabs API key
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        # Try loading from soren .env
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

    # Step 1: Generate voiceover
    vo_dir = config.output_dir / "voiceover"
    voiceover_path = vo_dir / "voiceover_full.wav"

    if skip_voiceover and voiceover_path.exists():
        print("\n=== Step 1: Reusing existing voiceover ===")
        # Rebuild segment info from existing WAV files
        import wave
        segments = []
        for item in config.voiceover_script:
            seg_path = vo_dir / f"{item['id']}.wav"
            if seg_path.exists():
                with wave.open(str(seg_path), "rb") as wf:
                    dur = wf.getnframes() / wf.getframerate()
                segments.append(SegmentInfo(id=item["id"], path=seg_path, duration_sec=dur))
            else:
                raise FileNotFoundError(f"Missing cached segment: {seg_path}")
        total_duration = sum(s.duration_sec for s in segments) + 0.3 * (len(segments) - 1)
        print(f"  Cached voiceover: {total_duration:.1f}s ({len(segments)} segments)")
    else:
        print("\n=== Step 1: Generating voiceover segments ===")
        segments = generate_segments(config.voiceover_script, api_key, vo_dir)
        total_duration = sum(s.duration_sec for s in segments) + 0.3 * (len(segments) - 1)
        print(f"  Total voiceover duration: {total_duration:.1f}s")
        voiceover_path = concat_voiceover(segments, voiceover_path)

    # Step 2a: Record desktop (1920x1080) for horizontal
    print("\n=== Step 2a: Recording desktop demo (1920x1080) ===")
    rec_desktop = config.output_dir / "recording_desktop"
    video_path = record_demo(config.demo_url, segments, rec_desktop, viewport=(1920, 1080))

    # Step 2b: Record mobile (390x844) for vertical
    print("\n=== Step 2b: Recording mobile demo (390x844) ===")
    rec_mobile = config.output_dir / "recording_mobile"
    mobile_video_path = record_demo(config.demo_url, segments, rec_mobile, viewport=(390, 844))

    # Step 3: Composite final videos
    print("\n=== Step 3: Compositing final videos ===")
    h_path, v_path = composite(
        video_path, voiceover_path, config.output_dir, config.business_name,
        vertical_video_path=mobile_video_path,
    )

    print(f"\n=== Done! ===")
    print(f"  Horizontal: {h_path}")
    print(f"  Vertical:   {v_path}")
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
        help="Reuse existing voiceover WAVs (saves ElevenLabs credits)",
    )
    args = parser.parse_args()

    config = CONFIGS[args.business]
    if args.output_dir:
        config.output_dir = args.output_dir

    generate_demo(config, skip_voiceover=args.skip_voiceover)


if __name__ == "__main__":
    main()
