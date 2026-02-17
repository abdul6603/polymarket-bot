"""Chat routes: /api/chat/*"""
from __future__ import annotations

import os
from datetime import datetime

from flask import Blueprint, jsonify, request

from bot.shared import (
    ET,
    _chat_history,
    _CHAT_HISTORY_MAX,
    _AGENT_PROMPTS,
)

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """Group chat: send a message and get responses from all agents."""
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "No message provided"}), 400

    user_msg = data["message"]
    timestamp = datetime.now(ET).isoformat()

    _chat_history.append({"role": "user", "agent": "you", "content": user_msg, "timestamp": timestamp})
    # Prevent unbounded memory growth
    if len(_chat_history) > _CHAT_HISTORY_MAX:
        del _chat_history[:len(_chat_history) - _CHAT_HISTORY_MAX]

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        # Return placeholder responses if no API key
        responses = []
        for name in ["shelby", "soren", "garves"]:
            resp = {"agent": name, "content": f"[{name.upper()}: No OpenAI API key configured]", "timestamp": timestamp}
            responses.append(resp)
            _chat_history.append({"role": "assistant", "agent": name, "content": resp["content"], "timestamp": timestamp})
        return jsonify({"responses": responses, "history": _chat_history[-30:]})

    import requests as req

    responses = []
    for agent_name, system_prompt in _AGENT_PROMPTS.items():
        # Build conversation context for this agent
        messages = [{"role": "system", "content": system_prompt}]
        # Include recent chat history (last 10 exchanges)
        for h in _chat_history[-20:]:
            if h["role"] == "user":
                messages.append({"role": "user", "content": h["content"]})
            elif h["agent"] == agent_name:
                messages.append({"role": "assistant", "content": h["content"]})

        try:
            api_resp = req.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "messages": messages, "max_tokens": 200, "temperature": 0.8},
                timeout=15,
            )
            api_resp.raise_for_status()
            content = api_resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            content = f"[Error: {str(e)[:100]}]"

        resp = {"agent": agent_name, "content": content, "timestamp": timestamp}
        responses.append(resp)
        _chat_history.append({"role": "assistant", "agent": agent_name, "content": content, "timestamp": timestamp})

    return jsonify({"responses": responses, "history": _chat_history[-30:]})


@chat_bp.route("/api/chat/history")
def api_chat_history():
    """Get chat history."""
    return jsonify({"history": _chat_history[-50:]})


@chat_bp.route("/api/chat/agent/<agent_name>", methods=["POST"])
def api_chat_agent(agent_name: str):
    """Direct chat with a single agent. Returns one response."""
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "No message provided"}), 400

    user_msg = data["message"]
    timestamp = datetime.now(ET).isoformat()

    # Map display names to prompt keys
    name_map = {"robotox": "sentinel"}
    prompt_key = name_map.get(agent_name, agent_name)

    system_prompt = _AGENT_PROMPTS.get(prompt_key)
    if not system_prompt:
        return jsonify({"error": f"Unknown agent: {agent_name}"}), 404

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({
            "agent": agent_name,
            "content": f"[No OpenAI API key configured]",
            "timestamp": timestamp,
        })

    import requests as req

    # Build conversation from per-agent chat history
    history_key = f"_agent_chat_{agent_name}"
    if not hasattr(api_chat_agent, "_histories"):
        api_chat_agent._histories = {}
    agent_history = api_chat_agent._histories.setdefault(agent_name, [])
    agent_history.append({"role": "user", "content": user_msg, "timestamp": timestamp})

    messages = [{"role": "system", "content": system_prompt}]
    for h in agent_history[-20:]:
        messages.append({"role": h["role"], "content": h["content"]})

    try:
        api_resp = req.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": messages, "max_tokens": 300, "temperature": 0.7},
            timeout=15,
        )
        api_resp.raise_for_status()
        content = api_resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        content = f"[Error: {str(e)[:100]}]"

    agent_history.append({"role": "assistant", "content": content, "timestamp": timestamp})
    # Keep history bounded
    if len(agent_history) > 40:
        api_chat_agent._histories[agent_name] = agent_history[-30:]

    return jsonify({
        "agent": agent_name,
        "content": content,
        "timestamp": timestamp,
        "history": [{"role": h["role"], "content": h["content"], "agent": agent_name if h["role"] == "assistant" else "you"} for h in agent_history[-20:]],
    })


@chat_bp.route("/api/chat/agent/<agent_name>/history")
def api_chat_agent_history(agent_name: str):
    """Get per-agent chat history."""
    if not hasattr(api_chat_agent, "_histories"):
        return jsonify({"history": []})
    history = api_chat_agent._histories.get(agent_name, [])
    return jsonify({
        "history": [{"role": h["role"], "content": h["content"], "agent": agent_name if h["role"] == "assistant" else "you"} for h in history[-20:]],
    })
