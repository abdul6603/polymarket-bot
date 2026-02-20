/**
 * Brotherhood Dashboard — Reusable UI Components
 * Toasts, loading skeletons, count-up animations, brain activity, status pulses
 */

// ═══════════════════════════════════════════
//  Toast Notification System
// ═══════════════════════════════════════════
var _toastContainer = null;
function _ensureToastContainer() {
  if (_toastContainer) return _toastContainer;
  _toastContainer = document.createElement('div');
  _toastContainer.className = 'toast-container';
  document.body.appendChild(_toastContainer);
  return _toastContainer;
}

function showToast(message, type, duration) {
  type = type || 'info';
  duration = duration || 4000;
  var container = _ensureToastContainer();
  var toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  var icons = { info: '\u2139\ufe0f', success: '\u2705', warning: '\u26a0\ufe0f', error: '\ud83d\udea8', trade: '\ud83d\udcb0' };
  toast.innerHTML = '<span class="toast-icon">' + (icons[type] || '') + '</span><span class="toast-msg">' + message + '</span>';
  container.appendChild(toast);
  // Trigger animation
  requestAnimationFrame(function() { toast.classList.add('toast-show'); });
  setTimeout(function() {
    toast.classList.remove('toast-show');
    toast.classList.add('toast-hide');
    setTimeout(function() { toast.remove(); }, 300);
  }, duration);
}

// ═══════════════════════════════════════════
//  Loading Skeleton Placeholders
// ═══════════════════════════════════════════
function showSkeleton(elementId, rows) {
  rows = rows || 3;
  var el = document.getElementById(elementId);
  if (!el) return;
  var html = '';
  for (var i = 0; i < rows; i++) {
    var width = 60 + Math.random() * 35;
    html += '<div class="skeleton-line" style="width:' + width + '%;"></div>';
  }
  el.innerHTML = '<div class="skeleton-container">' + html + '</div>';
}

function showSkeletonCard(elementId) {
  var el = document.getElementById(elementId);
  if (!el) return;
  el.innerHTML = '<div class="skeleton-card"><div class="skeleton-circle"></div><div class="skeleton-lines"><div class="skeleton-line" style="width:70%"></div><div class="skeleton-line" style="width:50%"></div></div></div>';
}

// ═══════════════════════════════════════════
//  Count-Up Animation
// ═══════════════════════════════════════════
function animateCount(elementId, targetValue, duration, prefix, suffix) {
  duration = duration || 800;
  prefix = prefix || '';
  suffix = suffix || '';
  var el = document.getElementById(elementId);
  if (!el) return;
  var startValue = parseFloat(el.textContent.replace(/[^0-9.-]/g, '')) || 0;
  var startTime = null;
  var isFloat = String(targetValue).includes('.') || Math.abs(targetValue) < 10;
  var decimals = isFloat ? 2 : 0;

  function step(timestamp) {
    if (!startTime) startTime = timestamp;
    var progress = Math.min((timestamp - startTime) / duration, 1);
    // Ease out cubic
    var eased = 1 - Math.pow(1 - progress, 3);
    var current = startValue + (targetValue - startValue) * eased;
    el.textContent = prefix + current.toFixed(decimals) + suffix;
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ═══════════════════════════════════════════
//  Brain Activity Indicator
// ═══════════════════════════════════════════
function renderBrainActivity(containerId, activityData) {
  var el = document.getElementById(containerId);
  if (!el || !activityData) return;
  var html = '<div class="brain-activity-grid">';
  var agents = Object.keys(activityData);
  for (var i = 0; i < agents.length; i++) {
    var agent = agents[i];
    var info = activityData[agent];
    var isActive = info.active;
    var calls = info.calls || 0;
    var color = (window.AGENT_COLORS && AGENT_COLORS[agent === 'sentinel' ? 'sentinel' : agent]) || '#888';
    var name = (window.AGENT_NAMES && AGENT_NAMES[agent === 'sentinel' ? 'sentinel' : agent]) || agent;
    html += '<div class="brain-agent ' + (isActive ? 'brain-active' : '') + '">';
    html += '<div class="brain-dot" style="--agent-color:' + color + '"></div>';
    html += '<span class="brain-name">' + name + '</span>';
    if (isActive) {
      html += '<span class="brain-calls">' + calls + ' calls</span>';
      html += '<div class="brain-thinking"></div>';
    }
    html += '</div>';
  }
  html += '</div>';
  el.innerHTML = html;
}

// ═══════════════════════════════════════════
//  Pattern Learnings Feed
// ═══════════════════════════════════════════
function renderPatternFeed(containerId, patterns) {
  var el = document.getElementById(containerId);
  if (!el) return;
  if (!patterns || patterns.length === 0) {
    el.innerHTML = '<div class="empty-state">No patterns learned yet</div>';
    return;
  }
  var html = '';
  for (var i = 0; i < Math.min(patterns.length, 15); i++) {
    var p = patterns[i];
    var agent = p.agent || 'unknown';
    var color = (window.AGENT_COLORS && AGENT_COLORS[agent]) || '#888';
    var conf = ((p.confidence || 0) * 100).toFixed(0);
    var ev = p.evidence_count || 0;
    html += '<div class="pattern-item">';
    html += '<span class="pattern-agent" style="color:' + color + '">' + agent.toUpperCase() + '</span>';
    html += '<span class="pattern-type">' + esc(p.pattern_type || '') + '</span>';
    html += '<span class="pattern-desc">' + esc((p.description || '').substring(0, 120)) + '</span>';
    html += '<span class="pattern-meta">' + conf + '% conf | ' + ev + ' evidence</span>';
    html += '</div>';
  }
  el.innerHTML = html;
}

// ═══════════════════════════════════════════
//  Status Pulse (for running agents)
// ═══════════════════════════════════════════
function updateStatusPulse(agentCards) {
  if (!agentCards) return;
  for (var agent in agentCards) {
    var card = document.querySelector('.agent-card[data-agent="' + agent + '"]');
    if (!card) continue;
    var dot = card.querySelector('.status-dot');
    if (!dot) continue;
    var status = agentCards[agent];
    dot.classList.remove('pulse-running', 'pulse-error', 'pulse-idle');
    if (status === 'running' || status === 'online') {
      dot.classList.add('pulse-running');
    } else if (status === 'error' || status === 'dead') {
      dot.classList.add('pulse-error');
    } else {
      dot.classList.add('pulse-idle');
    }
  }
}
