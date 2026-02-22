"""Discord Alpha Scraper — Signal Analyzer.

Parses Discord messages into structured trading signals.
Extracts: ticker, direction, entry/exit, strategy, approach.
Uses vision LLM for image-heavy channels.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)

VISION_COUNT_FILE = Path.home() / "polymarket-bot" / "data" / "discord_vision_count.json"

SIGNAL_SYSTEM_PROMPT = """You are a trading signal parser. Extract structured data from Discord trading messages.

IMPORTANT: Messages can be either NEW TRADE SIGNALS or TRADE RESULTS/UPDATES.

Return ONLY valid JSON with these fields:
{
  "msg_type": "signal" or "result",
  "ticker": "BTC" or null,
  "direction": "LONG" or "SHORT" or null,
  "entry_price": 50000.0 or null,
  "stop_loss": 48000.0 or null,
  "take_profit": 55000.0 or null,
  "strategy": "breakout" or "support_bounce" or "trend_follow" or "reversal" or "scalp" or "swing" or null,
  "approach": "Brief description of the trader's reasoning/method" or null,
  "confidence": 0.0-1.0 based on how clear the signal is,
  "is_trade_signal": true,
  "result_outcome": "win" or "loss" or null,
  "result_r": 2.0 or -1.0 or null,
  "result_note": "tp1 hit" or "sl hit" or "closed" or null
}

Rules for NEW SIGNALS (msg_type="signal"):
- Has entry price, SL, TP, direction — it's a new trade call
- "cmp" = current market price (entry at market)
- Infer direction: "long", "buy", "bullish" = LONG; "short", "sell", "bearish" = SHORT

Rules for TRADE RESULTS (msg_type="result"):
- Messages like "sl hit -1R", "tp1 hit +2R", "take tp1 75%", "fail", "closed here", "stopped out"
- "+2R", "+1R" = win with R-multiple. "-1R", "-0.5R" = loss
- "fail", "sl hit", "stopped out" = loss
- "tp hit", "take profit", "closed in profit" = win
- Extract the R value if mentioned (e.g. "-1R" → result_r=-1.0, "+2R" → result_r=2.0)
- Set result_outcome to "win" or "loss"

General rules:
- If the message is NOT trade-related (just chat, meme, etc), set is_trade_signal=false
- Common crypto tickers: BTC, ETH, SOL, XRP, DOGE, IOTA, AVAX, LINK, etc
"""

VISION_SYSTEM_PROMPT = """You are a trading chart analyzer. A Discord trader posted this chart image with a message.

Analyze the chart and any annotations. Return ONLY valid JSON:
{
  "ticker": symbol shown on chart or null,
  "direction": "LONG" or "SHORT" based on annotations/markings,
  "entry_price": price level if marked or null,
  "stop_loss": SL level if marked or null,
  "take_profit": TP level if marked or null,
  "strategy": inferred strategy from chart pattern,
  "approach": "Description of what the chart shows — pattern, levels, indicators used",
  "confidence": 0.0-1.0,
  "is_trade_signal": true/false,
  "chart_analysis": "Brief technical analysis of what the chart shows"
}
"""


def _get_vision_count_today() -> int:
    """Get how many vision calls have been made today."""
    if not VISION_COUNT_FILE.exists():
        return 0
    try:
        data = json.loads(VISION_COUNT_FILE.read_text())
        if data.get("date") == date.today().isoformat():
            return data.get("count", 0)
        return 0
    except Exception:
        return 0


def _increment_vision_count() -> None:
    """Increment today's vision call count."""
    today = date.today().isoformat()
    count = _get_vision_count_today() + 1
    VISION_COUNT_FILE.parent.mkdir(exist_ok=True)
    VISION_COUNT_FILE.write_text(json.dumps({"date": today, "count": count}))


def analyze_text_message(content: str, author: str, channel_name: str) -> dict | None:
    """Analyze a text-only Discord message using LLM."""
    if not content or len(content.strip()) < 5:
        return None

    try:
        from shared.llm_client import llm_call
    except ImportError:
        log.warning("[DISCORD] shared.llm_client not available")
        return _fallback_parse(content)

    prompt = f"Channel: #{channel_name}\nTrader: {author}\nMessage: {content}"

    try:
        response = llm_call(
            system=SIGNAL_SYSTEM_PROMPT,
            user=prompt,
            agent="discord_scraper",
            task_type="fast",
            max_tokens=500,
        )
        return _parse_llm_response(response)
    except Exception as e:
        log.warning("[DISCORD] LLM analysis failed: %s", e)
        return _fallback_parse(content)


def analyze_image_message(
    content: str, image_urls: list[str], author: str, channel_name: str,
    vision_cap: int = 10,
) -> dict | None:
    """Analyze a message with images using vision LLM."""
    if _get_vision_count_today() >= vision_cap:
        log.info("[DISCORD] Vision daily cap reached (%d), falling back to text", vision_cap)
        return analyze_text_message(content, author, channel_name) if content else None

    try:
        from shared.llm_client import llm_call
    except ImportError:
        log.warning("[DISCORD] shared.llm_client not available for vision")
        return _fallback_parse(content) if content else None

    prompt = f"Channel: #{channel_name}\nTrader: {author}\nMessage: {content or '(image only)'}\nImage URLs: {', '.join(image_urls)}"

    try:
        response = llm_call(
            system=VISION_SYSTEM_PROMPT,
            user=prompt,
            agent="discord_scraper",
            task_type="analysis",
            max_tokens=600,
        )
        _increment_vision_count()
        return _parse_llm_response(response)
    except Exception as e:
        log.warning("[DISCORD] Vision analysis failed: %s", e)
        return _fallback_parse(content) if content else None


def _parse_llm_response(response: str) -> dict | None:
    """Extract JSON from LLM response."""
    if not response:
        return None

    # Try to find JSON in response
    text = response.strip()

    # Remove markdown code blocks if present
    if text.startswith("```"):
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = text.rstrip("`").strip()

    try:
        data = json.loads(text)
        if not data.get("is_trade_signal", False):
            return None
        return data
    except json.JSONDecodeError:
        # Try to find JSON object in text
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                if not data.get("is_trade_signal", False):
                    return None
                return data
            except json.JSONDecodeError:
                pass
    return None


def _fallback_parse(content: str) -> dict | None:
    """Regex fallback when LLM is unavailable."""
    if not content:
        return None

    text = content.upper()

    # Detect trade RESULT first (sl hit, tp hit, fail, +/-R)
    r_match = re.search(r"([+-]?\d*\.?\d+)\s*R\b", content, re.I)
    result_keywords_loss = ["SL HIT", "STOP LOSS HIT", "STOPPED OUT", "FAIL", "LOSS", "LIQUIDATED"]
    result_keywords_win = ["TP HIT", "TP1 HIT", "TP2 HIT", "TAKE TP", "TAKE PROFIT", "TARGET HIT", "CLOSED IN PROFIT"]

    is_result = False
    result_outcome = None
    result_r = None
    result_note = None

    if r_match:
        is_result = True
        result_r = float(r_match.group(1))
        result_outcome = "win" if result_r > 0 else "loss"
        result_note = f"{r_match.group(0).strip()}"
    elif any(kw in text for kw in result_keywords_loss):
        is_result = True
        result_outcome = "loss"
        result_r = -1.0
        result_note = "sl hit"
    elif any(kw in text for kw in result_keywords_win):
        is_result = True
        result_outcome = "win"
        result_r = 1.0
        result_note = "tp hit"

    # Detect direction
    direction = None
    if any(w in text for w in ["LONG", "BUY", "BULLISH", "CALLS"]):
        direction = "LONG"
    elif any(w in text for w in ["SHORT", "SELL", "BEARISH", "PUTS"]):
        direction = "SHORT"

    # Detect ticker — common crypto
    ticker = None
    tickers = [
        "BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "ADA",
        "DOT", "MATIC", "IOTA", "NEAR", "APT", "ARB", "OP", "SUI",
        "INJ", "TIA", "SEI", "JUP", "PEPE", "WIF", "BONK", "HYPE",
    ]
    for t in tickers:
        if f"${t}" in text or f" {t} " in f" {text} ":
            ticker = t
            break

    # Extract prices
    prices = re.findall(r"[\d]+\.[\d]+|[\d]{2,}", content)
    entry = float(prices[0]) if prices else None

    sl = None
    sl_match = re.search(r"(?:sl|stop\s*loss)[:\s]*\$?([\d.]+)", content, re.I)
    if sl_match:
        sl = float(sl_match.group(1))

    tp = None
    tp_match = re.search(r"(?:tp|take\s*profit|target)[:\s]*\$?([\d.]+)", content, re.I)
    if tp_match:
        tp = float(tp_match.group(1))

    if not ticker and not direction and not is_result:
        return None

    return {
        "msg_type": "result" if is_result else "signal",
        "ticker": ticker,
        "direction": direction,
        "entry_price": entry if not is_result else None,
        "stop_loss": sl,
        "take_profit": tp,
        "strategy": None,
        "approach": "Parsed from text (LLM unavailable)",
        "confidence": 0.4,
        "is_trade_signal": True,
        "result_outcome": result_outcome,
        "result_r": result_r,
        "result_note": result_note,
    }
