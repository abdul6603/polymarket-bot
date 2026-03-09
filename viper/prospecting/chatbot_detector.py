"""Detect existing chat widgets on a business website."""
from __future__ import annotations

from dataclasses import dataclass

_SIGNATURES: list[tuple[str, list[str]]] = [
    ("Intercom", ["intercom", "widget.intercom.io", "intercomSettings"]),
    ("Drift", ["drift.com", "driftt.com", "drift-widget"]),
    ("Tawk.to", ["tawk.to", "embed.tawk.to"]),
    ("Tidio", ["tidio", "tidioChatCode", "code.tidio.co"]),
    ("LiveChat", ["livechatinc.com", "cdn.livechatinc.com", "__lc_inited"]),
    ("Zendesk Chat", ["zopim", "zendesk.com/embeddable", "zdassets.com"]),
    ("Freshchat", ["freshchat", "wchat.freshchat.com"]),
    ("Crisp", ["crisp.chat", "client.crisp.chat"]),
    ("HubSpot Chat", ["hubspot.com/conversations", "js.hs-scripts.com", "hbspt"]),
    ("Olark", ["olark", "static.olark.com"]),
    ("Chatwoot", ["chatwoot", "app.chatwoot.com"]),
    ("Botpress", ["botpress", "cdn.botpress.cloud"]),
    ("ManyChat", ["manychat", "mcwidget"]),
    ("Landbot", ["landbot", "cdn.landbot.io"]),
]


@dataclass
class ChatbotDetectionResult:
    """Result of chatbot presence detection."""
    has_chatbot: bool = False
    chatbot_name: str = ""
    confidence: str = "none"  # "high", "medium", "none", "unknown"


def detect_chatbot(html: str) -> ChatbotDetectionResult:
    """Detect chatbot widgets via string matching on raw HTML.

    Short-circuits on first match. Typically ~1-5ms per page.
    """
    if not html:
        return ChatbotDetectionResult(confidence="unknown")

    html_lower = html.lower()
    for name, markers in _SIGNATURES:
        for marker in markers:
            if marker.lower() in html_lower:
                return ChatbotDetectionResult(
                    has_chatbot=True,
                    chatbot_name=name,
                    confidence="high",
                )

    return ChatbotDetectionResult(has_chatbot=False, confidence="medium")
