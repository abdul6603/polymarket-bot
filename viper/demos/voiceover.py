"""Voiceover — ElevenLabs TTS segment generator for demo videos."""
from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from elevenlabs import ElevenLabs


@dataclass
class SegmentInfo:
    """Info about a generated voiceover segment."""
    id: str
    path: Path
    duration_sec: float


def _wav_duration(path: Path) -> float:
    """Get duration of a WAV file in seconds."""
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / rate


def _mp3_to_wav(mp3_bytes: bytes, wav_path: Path) -> None:
    """Convert MP3 bytes to WAV using moviepy/ffmpeg."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(mp3_bytes)
        tmp_mp3 = tmp.name

    subprocess.run(
        ["ffmpeg", "-y", "-i", tmp_mp3, "-ar", "44100", "-ac", "1", wav_path],
        capture_output=True,
        check=True,
    )
    Path(tmp_mp3).unlink(missing_ok=True)


def generate_segments(
    script: list[dict],
    api_key: str,
    output_dir: Path | None = None,
) -> list[SegmentInfo]:
    """Generate TTS audio for each script segment.

    Args:
        script: list of {"id": "intro", "text": "Hey, check this out..."}
        api_key: ElevenLabs API key
        output_dir: directory for WAV files (default: temp dir)

    Returns:
        list of SegmentInfo with id, path, and measured duration
    """
    if output_dir is None:
        output_dir = Path(mkdtemp(prefix="demo_vo_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    client = ElevenLabs(api_key=api_key)
    segments: list[SegmentInfo] = []

    for item in script:
        seg_id = item["id"]
        text = item["text"]
        wav_path = output_dir / f"{seg_id}.wav"

        print(f"  [ElevenLabs] Generating: {seg_id}")

        # Generate with conversational settings (NOT Soren's dramatic style)
        audio_gen = client.text_to_speech.convert(
            text=text,
            voice_id="nPczCjzI2devNBz1zQrb",  # Brian — deep, resonant, conversational
            model_id="eleven_multilingual_v2",
            voice_settings={
                "stability": 0.55,
                "similarity_boost": 0.75,
                "style": 0.25,
                "use_speaker_boost": True,
            },
        )

        # Collect MP3 bytes from generator
        mp3_data = b""
        for chunk in audio_gen:
            mp3_data += chunk

        # Convert MP3 to WAV for timing/concat
        _mp3_to_wav(mp3_data, wav_path)

        duration = _wav_duration(wav_path)
        print(f"  [done] {seg_id}: {duration:.2f}s")
        segments.append(SegmentInfo(id=seg_id, path=wav_path, duration_sec=duration))

    return segments


def concat_voiceover(segments: list[SegmentInfo], output_path: Path | None = None) -> Path:
    """Join all WAV segments with 0.3s silence gaps.

    Args:
        segments: list of SegmentInfo from generate_segments()
        output_path: output WAV path (default: alongside first segment)

    Returns:
        Path to concatenated WAV file
    """
    if not segments:
        raise ValueError("No segments to concatenate")

    if output_path is None:
        output_path = segments[0].path.parent / "voiceover_full.wav"

    # Read first segment to get params
    with wave.open(str(segments[0].path), "rb") as wf:
        params = wf.getparams()
        sample_rate = params.framerate
        sample_width = params.sampwidth
        n_channels = params.nchannels

    # 0.3s silence gap
    gap_frames = int(sample_rate * 0.3)
    silence = b"\x00" * (gap_frames * sample_width * n_channels)

    with wave.open(str(output_path), "wb") as out:
        out.setnchannels(n_channels)
        out.setsampwidth(sample_width)
        out.setframerate(sample_rate)

        for i, seg in enumerate(segments):
            with wave.open(str(seg.path), "rb") as wf:
                out.writeframes(wf.readframes(wf.getnframes()))
            # Add gap between segments (not after last)
            if i < len(segments) - 1:
                out.writeframes(silence)

    print(f"  [concat] Full voiceover: {output_path}")
    return output_path
