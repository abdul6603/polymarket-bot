"""HTML Builder — self-contained chat widget landing page."""
from __future__ import annotations

import html
import json
import logging
from viper.demos.scraper import ScrapedBusiness
from viper.demos.qa_generator import QAPair

log = logging.getLogger(__name__)


def build_demo_html(biz: ScrapedBusiness, qa_pairs: list[QAPair]) -> str:
    """Build a self-contained HTML page with branded chat widget."""
    name = html.escape(biz.name or "Business")
    tagline = html.escape(biz.tagline or f"Welcome to {biz.name or 'our website'}")
    color = biz.brand_color or "#2563eb"
    phone = html.escape(biz.phone or "")
    niche = biz.niche

    # Prepare Q&A data for JS
    qa_data = []
    for pair in qa_pairs:
        qa_data.append({
            "q": pair.question,
            "a": pair.answer,
            "kw": pair.keywords,
            "cat": pair.category,
        })

    # Quick-action buttons based on niche
    if niche == "dental":
        quick_actions = [
            {"label": "Book Appointment", "q": "How do I book an appointment?"},
            {"label": "Insurance", "q": "Do you accept my insurance?"},
            {"label": "Services", "q": "What services do you offer?"},
            {"label": "Emergency", "q": "Do you handle dental emergencies?"},
        ]
    elif niche == "real_estate":
        quick_actions = [
            {"label": "Buy a Home", "q": "How do I start the home buying process?"},
            {"label": "Sell My Home", "q": "How do I sell my home?"},
            {"label": "Schedule Showing", "q": "Can I schedule a showing?"},
            {"label": "Home Value", "q": "What's my home worth?"},
        ]
    else:
        quick_actions = [
            {"label": "Services", "q": "What services do you offer?"},
            {"label": "Contact", "q": "How do I contact you?"},
            {"label": "Hours", "q": "What are your hours?"},
            {"label": "Location", "q": "Where are you located?"},
        ]

    qa_json = json.dumps(qa_data, ensure_ascii=False)
    actions_json = json.dumps(quick_actions, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} - AI Assistant Demo</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --brand: {color};
  --brand-dark: color-mix(in srgb, {color} 80%, black);
  --brand-light: color-mix(in srgb, {color} 15%, white);
  --text: #1a1a2e;
  --text-light: #6b7280;
  --bg: #f8fafc;
  --white: #ffffff;
  --shadow: 0 4px 24px rgba(0,0,0,0.12);
  --radius: 12px;
}}

body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
}}

/* Hero Section */
.hero {{
  background: linear-gradient(135deg, var(--brand) 0%, var(--brand-dark) 100%);
  color: white;
  padding: 80px 20px 100px;
  text-align: center;
  position: relative;
  overflow: hidden;
}}
.hero::after {{
  content: '';
  position: absolute;
  bottom: -2px;
  left: 0;
  right: 0;
  height: 60px;
  background: var(--bg);
  border-radius: 50% 50% 0 0;
}}
.hero h1 {{
  font-size: clamp(1.8rem, 4vw, 2.8rem);
  font-weight: 700;
  margin-bottom: 16px;
  letter-spacing: -0.02em;
}}
.hero p {{
  font-size: 1.15rem;
  opacity: 0.9;
  max-width: 600px;
  margin: 0 auto 32px;
  line-height: 1.6;
}}
.hero-cta {{
  display: inline-block;
  background: white;
  color: var(--brand);
  padding: 14px 32px;
  border-radius: 50px;
  font-weight: 600;
  font-size: 1.05rem;
  cursor: pointer;
  border: none;
  transition: transform 0.2s, box-shadow 0.2s;
}}
.hero-cta:hover {{
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.15);
}}

/* Features */
.features {{
  max-width: 900px;
  margin: -20px auto 60px;
  padding: 0 20px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 24px;
  position: relative;
  z-index: 1;
}}
.feature-card {{
  background: var(--white);
  padding: 28px;
  border-radius: var(--radius);
  box-shadow: 0 2px 12px rgba(0,0,0,0.06);
  text-align: center;
}}
.feature-card .icon {{
  font-size: 2rem;
  margin-bottom: 12px;
}}
.feature-card h3 {{
  font-size: 1.05rem;
  margin-bottom: 8px;
  color: var(--text);
}}
.feature-card p {{
  font-size: 0.9rem;
  color: var(--text-light);
  line-height: 1.5;
}}

/* Footer */
.footer {{
  text-align: center;
  padding: 40px 20px;
  color: var(--text-light);
  font-size: 0.85rem;
}}
.footer a {{ color: var(--brand); text-decoration: none; }}
.footer a:hover {{ text-decoration: underline; }}

/* Chat Widget */
.chat-fab {{
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--brand);
  color: white;
  border: none;
  cursor: pointer;
  box-shadow: var(--shadow);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.3s, background 0.3s;
  animation: pulse 2s infinite;
}}
.chat-fab:hover {{ transform: scale(1.08); }}
.chat-fab svg {{ width: 28px; height: 28px; }}
@keyframes pulse {{
  0%, 100% {{ box-shadow: 0 4px 24px rgba(0,0,0,0.12); }}
  50% {{ box-shadow: 0 4px 32px rgba(0,0,0,0.25); }}
}}

.chat-window {{
  position: fixed;
  bottom: 100px;
  right: 24px;
  width: 380px;
  max-width: calc(100vw - 32px);
  height: 560px;
  max-height: calc(100vh - 140px);
  background: var(--white);
  border-radius: 16px;
  box-shadow: 0 8px 40px rgba(0,0,0,0.18);
  z-index: 1001;
  display: none;
  flex-direction: column;
  overflow: hidden;
}}
.chat-window.open {{
  display: flex;
  animation: slideUp 0.3s ease-out;
}}
@keyframes slideUp {{
  from {{ opacity: 0; transform: translateY(20px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}

.chat-header {{
  background: var(--brand);
  color: white;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}}
.chat-header .avatar {{
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: rgba(255,255,255,0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.2rem;
}}
.chat-header .info h4 {{ font-size: 0.95rem; font-weight: 600; }}
.chat-header .info span {{ font-size: 0.8rem; opacity: 0.85; }}
.chat-close {{
  margin-left: auto;
  background: none;
  border: none;
  color: white;
  cursor: pointer;
  font-size: 1.4rem;
  padding: 4px;
  opacity: 0.8;
}}
.chat-close:hover {{ opacity: 1; }}

.chat-body {{
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}}

.msg {{
  max-width: 85%;
  padding: 10px 14px;
  border-radius: 16px;
  font-size: 0.9rem;
  line-height: 1.5;
  animation: fadeIn 0.3s;
}}
@keyframes fadeIn {{
  from {{ opacity: 0; transform: translateY(6px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
.msg.bot {{
  background: var(--brand-light);
  color: var(--text);
  align-self: flex-start;
  border-bottom-left-radius: 4px;
}}
.msg.user {{
  background: var(--brand);
  color: white;
  align-self: flex-end;
  border-bottom-right-radius: 4px;
}}

.quick-actions {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 4px 0;
}}
.quick-btn {{
  background: var(--white);
  border: 1.5px solid var(--brand);
  color: var(--brand);
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 0.82rem;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}}
.quick-btn:hover {{
  background: var(--brand);
  color: white;
}}

.typing {{
  display: flex;
  gap: 4px;
  padding: 10px 14px;
  background: var(--brand-light);
  border-radius: 16px;
  border-bottom-left-radius: 4px;
  align-self: flex-start;
  max-width: 60px;
}}
.typing span {{
  width: 8px;
  height: 8px;
  background: var(--brand);
  border-radius: 50%;
  opacity: 0.4;
  animation: typingDot 1.4s infinite;
}}
.typing span:nth-child(2) {{ animation-delay: 0.2s; }}
.typing span:nth-child(3) {{ animation-delay: 0.4s; }}
@keyframes typingDot {{
  0%, 60%, 100% {{ opacity: 0.4; transform: translateY(0); }}
  30% {{ opacity: 1; transform: translateY(-4px); }}
}}

.chat-input {{
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #e5e7eb;
  flex-shrink: 0;
}}
.chat-input input {{
  flex: 1;
  border: 1.5px solid #e5e7eb;
  border-radius: 24px;
  padding: 10px 16px;
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.2s;
}}
.chat-input input:focus {{ border-color: var(--brand); }}
.chat-input button {{
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--brand);
  color: white;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: background 0.2s;
}}
.chat-input button:hover {{ background: var(--brand-dark); }}

/* Lead capture form */
.lead-form {{
  background: #f0f4ff;
  border-radius: 12px;
  padding: 14px;
  align-self: flex-start;
  max-width: 90%;
}}
.lead-form p {{
  font-size: 0.85rem;
  margin-bottom: 10px;
  color: var(--text);
}}
.lead-form input {{
  display: block;
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 0.85rem;
  margin-bottom: 8px;
}}
.lead-form button {{
  background: var(--brand);
  color: white;
  border: none;
  padding: 8px 20px;
  border-radius: 8px;
  font-size: 0.85rem;
  cursor: pointer;
  width: 100%;
}}

@media (max-width: 480px) {{
  .chat-window {{
    bottom: 0;
    right: 0;
    width: 100vw;
    height: 100vh;
    max-height: 100vh;
    border-radius: 0;
  }}
  .chat-fab {{ bottom: 16px; right: 16px; width: 56px; height: 56px; }}
}}
</style>
</head>
<body>

<section class="hero">
  <h1>{name}</h1>
  <p>{tagline}</p>
  <button class="hero-cta" onclick="toggleChat()">Try Our AI Assistant</button>
</section>

<section class="features">
  {_feature_cards(niche)}
</section>

<footer class="footer">
  <p>Powered by <a href="https://darkcodeai.carrd.co" target="_blank">DarkCode AI</a></p>
  <p style="margin-top:8px;font-size:0.78rem;opacity:0.7">AI-powered chatbot demo &mdash; responses are generated from publicly available business information</p>
</footer>

<!-- Chat Widget -->
<button class="chat-fab" id="chatFab" onclick="toggleChat()" aria-label="Open chat">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
  </svg>
</button>

<div class="chat-window" id="chatWindow">
  <div class="chat-header">
    <div class="avatar">{_niche_emoji(niche)}</div>
    <div class="info">
      <h4>{name}</h4>
      <span>AI Assistant &bull; Online</span>
    </div>
    <button class="chat-close" onclick="toggleChat()">&times;</button>
  </div>
  <div class="chat-body" id="chatBody"></div>
  <div class="chat-input">
    <input type="text" id="chatInput" placeholder="Type your question..." autocomplete="off">
    <button onclick="sendMessage()" aria-label="Send">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
    </button>
  </div>
</div>

<script>
(function() {{
  var QA_DATA = {qa_json};
  var QUICK_ACTIONS = {actions_json};
  var BUSINESS_NAME = {json.dumps(biz.name or "our team")};
  var PHONE = {json.dumps(biz.phone or "")};

  var chatOpen = false;
  var initialized = false;

  window.toggleChat = function() {{
    chatOpen = !chatOpen;
    var win = document.getElementById("chatWindow");
    var fab = document.getElementById("chatFab");
    if (chatOpen) {{
      win.classList.add("open");
      fab.style.display = "none";
      if (!initialized) {{
        initChat();
        initialized = true;
      }}
      setTimeout(function() {{ document.getElementById("chatInput").focus(); }}, 300);
    }} else {{
      win.classList.remove("open");
      fab.style.display = "flex";
    }}
  }};

  function initChat() {{
    addBotMessage("Hi there! I am the AI assistant for " + BUSINESS_NAME + ". How can I help you today?");
    setTimeout(function() {{
      addQuickActions();
    }}, 400);
  }}

  function addBotMessage(text) {{
    var body = document.getElementById("chatBody");
    var div = document.createElement("div");
    div.className = "msg bot";
    div.textContent = text;
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
  }}

  function addUserMessage(text) {{
    var body = document.getElementById("chatBody");
    var div = document.createElement("div");
    div.className = "msg user";
    div.textContent = text;
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
  }}

  function addQuickActions() {{
    var body = document.getElementById("chatBody");
    var container = document.createElement("div");
    container.className = "quick-actions";
    for (var i = 0; i < QUICK_ACTIONS.length; i++) {{
      var btn = document.createElement("button");
      btn.className = "quick-btn";
      btn.textContent = QUICK_ACTIONS[i].label;
      btn.setAttribute("data-q", QUICK_ACTIONS[i].q);
      btn.onclick = function() {{
        var q = this.getAttribute("data-q");
        handleUserInput(q);
        this.parentElement.remove();
      }};
      container.appendChild(btn);
    }}
    body.appendChild(container);
    body.scrollTop = body.scrollHeight;
  }}

  function showTyping() {{
    var body = document.getElementById("chatBody");
    var div = document.createElement("div");
    div.className = "typing";
    div.id = "typingIndicator";
    div.innerHTML = "<span></span><span></span><span></span>";
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
  }}

  function hideTyping() {{
    var el = document.getElementById("typingIndicator");
    if (el) el.remove();
  }}

  function findBestMatch(input) {{
    var inputLower = input.toLowerCase();
    var inputWords = inputLower.split(/\\s+/);
    var bestScore = 0;
    var bestAnswer = null;

    for (var i = 0; i < QA_DATA.length; i++) {{
      var qa = QA_DATA[i];
      var score = 0;

      // Keyword matching
      for (var k = 0; k < qa.kw.length; k++) {{
        var kw = qa.kw[k].toLowerCase();
        if (inputLower.indexOf(kw) !== -1) {{
          score += 20;
        }}
      }}

      // Word overlap with question
      var qWords = qa.q.toLowerCase().split(/\\s+/);
      for (var w = 0; w < inputWords.length; w++) {{
        if (inputWords[w].length <= 3) continue;
        for (var qw = 0; qw < qWords.length; qw++) {{
          if (qWords[qw].indexOf(inputWords[w]) !== -1 || inputWords[w].indexOf(qWords[qw]) !== -1) {{
            score += 8;
          }}
        }}
      }}

      // Exact phrase bonus
      if (inputLower.indexOf(qa.q.toLowerCase()) !== -1 || qa.q.toLowerCase().indexOf(inputLower) !== -1) {{
        score += 50;
      }}

      if (score > bestScore) {{
        bestScore = score;
        bestAnswer = qa.a;
      }}
    }}

    if (bestScore >= 15) {{
      return bestAnswer;
    }}
    return null;
  }}

  function handleUserInput(text) {{
    addUserMessage(text);
    showTyping();

    var delay = 600 + Math.random() * 600;
    setTimeout(function() {{
      hideTyping();
      var answer = findBestMatch(text);
      if (answer) {{
        addBotMessage(answer);
      }} else {{
        showLeadCapture(text);
      }}
    }}, delay);
  }}

  function showLeadCapture(originalQuestion) {{
    var body = document.getElementById("chatBody");
    addBotMessage("Great question! Let me connect you with " + BUSINESS_NAME + " directly so they can give you the best answer.");

    var form = document.createElement("div");
    form.className = "lead-form";
    form.innerHTML =
      "<p>Leave your info and we will get back to you:</p>" +
      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your name\\">" +
      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +
      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +
      "<button onclick=\\"submitLead()\\" type=\\"button\\">Send</button>";
    body.appendChild(form);
    body.scrollTop = body.scrollHeight;
  }}

  window.submitLead = function() {{
    var name = document.getElementById("leadName").value;
    var phone = document.getElementById("leadPhone").value;
    var email = document.getElementById("leadEmail").value;

    if (!name && !phone && !email) {{
      return;
    }}

    var forms = document.querySelectorAll(".lead-form");
    for (var i = 0; i < forms.length; i++) {{ forms[i].remove(); }}

    addBotMessage("Thank you, " + (name || "friend") + "! " + BUSINESS_NAME + " will reach out to you shortly." +
      (PHONE ? " You can also call us directly at " + PHONE + "." : ""));
  }};

  window.sendMessage = function() {{
    var input = document.getElementById("chatInput");
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    handleUserInput(text);
  }};

  document.getElementById("chatInput").addEventListener("keypress", function(e) {{
    if (e.key === "Enter") window.sendMessage();
  }});
}})();
</script>
</body>
</html>'''


def _niche_emoji(niche: str) -> str:
    """Return emoji for niche."""
    if niche == "dental":
        return "&#129463;"  # tooth emoji
    if niche == "real_estate":
        return "&#127968;"  # house emoji
    return "&#128172;"  # speech bubble


def _feature_cards(niche: str) -> str:
    """Generate feature cards HTML based on niche."""
    if niche == "dental":
        cards = [
            ("&#128197;", "Easy Scheduling", "Book appointments instantly, 24/7 — even outside office hours."),
            ("&#128737;", "Insurance Check", "Instantly verify your dental insurance coverage and benefits."),
            ("&#9889;", "Emergency Support", "Get immediate guidance for dental emergencies anytime."),
        ]
    elif niche == "real_estate":
        cards = [
            ("&#127968;", "Property Search", "Get instant answers about listings, areas, and pricing."),
            ("&#128200;", "Market Insights", "Access up-to-date market data for informed decisions."),
            ("&#128197;", "Schedule Showings", "Book property tours and consultations instantly."),
        ]
    else:
        cards = [
            ("&#128172;", "Instant Answers", "Get responses to common questions 24/7."),
            ("&#128197;", "Easy Booking", "Schedule appointments without picking up the phone."),
            ("&#9889;", "Always Available", "Your AI assistant never sleeps."),
        ]

    html_parts = []
    for icon, title, desc in cards:
        html_parts.append(
            f'<div class="feature-card">'
            f'<div class="icon">{icon}</div>'
            f'<h3>{title}</h3>'
            f'<p>{desc}</p>'
            f'</div>'
        )
    return "\n  ".join(html_parts)
