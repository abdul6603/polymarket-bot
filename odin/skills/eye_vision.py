"""Eye-Vision — real-time chart screenshot analysis.

Analyzes chart images to detect BOS, CHoCH, Order Blocks,
FVG, liquidity voids using Claude Vision API.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("odin.skills.eye_vision")

ANALYSIS_PROMPT = """You are Odin, an expert Smart Money Concepts (SMC) chart analyst.
Analyze this chart screenshot and identify ALL of the following:

1. **Market Structure**: Current trend (bullish/bearish/ranging)
2. **Break of Structure (BOS)**: Any recent BOS events with direction
3. **Change of Character (CHoCH)**: Any CHoCH signals (trend reversals)
4. **Order Blocks (OB)**: Bullish and bearish OBs with price zones
5. **Fair Value Gaps (FVG)**: Unfilled imbalances with price levels
6. **Liquidity Zones**: Equal highs/lows, stop hunt targets
7. **Liquidity Voids**: Large unfilled moves that price may return to
8. **Key Levels**: Major support/resistance from the chart

Return your analysis as JSON with this structure:
{
    "trend": "bullish|bearish|ranging",
    "confidence": 0.0-1.0,
    "patterns": [
        {"type": "BOS|CHoCH|OB|FVG|LIQUIDITY|VOID", "direction": "bullish|bearish",
         "price_level": 0.0, "zone_top": 0.0, "zone_bottom": 0.0,
         "strength": 0-100, "description": "..."}
    ],
    "bias": "long|short|neutral",
    "key_levels": {"resistance": [0.0], "support": [0.0]},
    "narrative": "2-3 sentence honest analysis of what you see"
}

Be HONEST. If the chart is unclear or you're not confident, say so.
Don't inflate confidence. Real traders need real analysis."""


class EyeVision:
    """Chart screenshot analysis using vision models."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path.home() / "odin" / "data"
        self._last_analysis: dict = {}
        self._analysis_count = 0

    def analyze_chart(self, image_source: str | bytes | Path) -> dict:
        """Analyze a chart image.

        Args:
            image_source: file path, URL, or raw bytes of chart image

        Returns:
            Analysis dict with patterns, bias, confidence, narrative.
        """
        image_data = self._load_image(image_source)
        if not image_data:
            return {"error": "Could not load image", "patterns": []}

        result = self._call_vision_api(image_data)
        self._last_analysis = result
        self._analysis_count += 1

        log.info(
            "[EYE] Chart analyzed: trend=%s bias=%s conf=%.2f patterns=%d",
            result.get("trend", "?"),
            result.get("bias", "?"),
            result.get("confidence", 0),
            len(result.get("patterns", [])),
        )
        return result

    def _load_image(self, source: str | bytes | Path) -> str | None:
        """Load image and return base64-encoded string."""
        if isinstance(source, bytes):
            return base64.b64encode(source).decode()

        path = Path(source) if isinstance(source, str) else source
        if path.exists():
            return base64.b64encode(path.read_bytes()).decode()

        if isinstance(source, str) and source.startswith(("http://", "https://")):
            return source

        log.warning("[EYE] Could not load image from: %s", str(source)[:100])
        return None

    def _call_vision_api(self, image_data: str) -> dict:
        """Call Claude or OpenAI vision API for chart analysis."""
        try:
            from shared.llm_client import llm_call
        except ImportError:
            llm_call = None

        # Try Claude Vision first (best for chart analysis)
        result = self._try_claude_vision(image_data)
        if result:
            return result

        # Fallback: OpenAI Vision
        result = self._try_openai_vision(image_data)
        if result:
            return result

        return {
            "error": "No vision API available",
            "trend": "unknown",
            "confidence": 0,
            "patterns": [],
            "bias": "neutral",
            "narrative": "Vision API unavailable. Cannot analyze chart.",
        }

    def _try_claude_vision(self, image_data: str) -> dict | None:
        """Try Claude Vision API."""
        try:
            import anthropic
            import os

            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                return None

            client = anthropic.Anthropic(api_key=api_key)

            content = []
            if image_data.startswith(("http://", "https://")):
                content.append({
                    "type": "image",
                    "source": {"type": "url", "url": image_data},
                })
            else:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_data,
                    },
                })
            content.append({"type": "text", "text": ANALYSIS_PROMPT})

            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": content}],
            )

            text = response.content[0].text
            return self._parse_response(text)

        except Exception as e:
            log.debug("[EYE] Claude Vision failed: %s", str(e)[:150])
            return None

    def _try_openai_vision(self, image_data: str) -> dict | None:
        """Try OpenAI GPT-4 Vision API."""
        try:
            import openai
            import os

            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                return None

            client = openai.OpenAI(api_key=api_key)

            if image_data.startswith(("http://", "https://")):
                image_content = {"type": "image_url", "image_url": {"url": image_data}}
            else:
                image_content = {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_data}"},
                }

            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [image_content, {"type": "text", "text": ANALYSIS_PROMPT}],
                }],
            )

            text = response.choices[0].message.content
            return self._parse_response(text)

        except Exception as e:
            log.debug("[EYE] OpenAI Vision failed: %s", str(e)[:150])
            return None

    def _parse_response(self, text: str) -> dict:
        """Parse JSON response from vision model."""
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

        return {
            "trend": "unknown",
            "confidence": 0.3,
            "patterns": [],
            "bias": "neutral",
            "narrative": text[:500] if text else "Could not parse response",
        }

    def get_status(self) -> dict:
        return {
            "analyses_done": self._analysis_count,
            "last_trend": self._last_analysis.get("trend", "none"),
            "last_confidence": self._last_analysis.get("confidence", 0),
        }
