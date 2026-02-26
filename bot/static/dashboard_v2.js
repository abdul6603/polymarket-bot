var currentTab = 'overview';
var _atlasBgCache = null;
var _overviewCache = null;
var AGENT_COLORS = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',lisa:'#ff8800',sentinel:'#00ff44',thor:'#ff6600',hawk:'#FFD700',viper:'#00ff88',quant:'#00BFFF',odin:'#8B5CF6',oracle:'#F59E0B'};
var AGENT_INITIALS = {garves:'GA',soren:'SO',shelby:'SH',atlas:'AT',lisa:'LI',sentinel:'RO',thor:'TH',hawk:'HK',viper:'VP',quant:'QT',odin:'OD',oracle:'OR'};
var AGENT_ROLES = {garves:'Trading Bot',soren:'Content Creator',shelby:'Team Leader',atlas:'Data Scientist',lisa:'Social Media',sentinel:'Health Monitor',thor:'Coding Lieutenant',hawk:'Market Predator',viper:'Opportunity Hunter',quant:'Strategy Lab',odin:'Futures Trader',oracle:'Weekly Crypto Oracle'};
var AGENT_NAMES = {garves:'Garves',soren:'Soren',shelby:'Shelby',atlas:'Atlas',lisa:'Lisa',sentinel:'Robotox',thor:'Thor',hawk:'Hawk',viper:'Viper',quant:'Quant',odin:'Odin',oracle:'Oracle'};
var AGENT_AVATARS = {soren:'/static/soren_profile.png'};

function renderLossCapBar(containerId, dailyPnl, cap, period) {
  var el = document.getElementById(containerId);
  if (!el) return;
  var loss = Math.max(0, -dailyPnl);
  var pct = cap > 0 ? Math.min(100, (loss / cap) * 100) : 0;
  var color = pct >= 100 ? 'var(--error)' : pct >= 70 ? 'var(--warning)' : 'var(--success)';
  var label = pct >= 100 ? 'CAP HIT' : '$' + loss.toFixed(0) + ' / $' + cap.toFixed(0);
  var per = period || 'Daily';
  el.innerHTML = '<div style="font-size:0.58rem;color:var(--text-muted);margin-bottom:2px;">' + per + ' Loss Cap</div>'
    + '<div style="background:rgba(255,255,255,0.06);border-radius:3px;height:6px;overflow:hidden;">'
    + '<div style="width:' + pct.toFixed(1) + '%;height:100%;background:' + color + ';border-radius:3px;transition:width 0.5s;"></div></div>'
    + '<div style="font-size:0.56rem;margin-top:1px;color:' + color + ';font-weight:600;">' + label + '</div>';
}

var TAB_ALIASES = {'performance': 'traders'};

function switchTab(tab) {
  var contentTab = TAB_ALIASES[tab] || tab;
  currentTab = contentTab;
  var tabs = document.querySelectorAll('.tab-content');
  for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove('active');
  var btns = document.querySelectorAll('.sidebar-btn');
  for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
  var el = document.getElementById('tab-' + contentTab);
  if (el) el.classList.add('active');
  var btn = document.querySelector('.sidebar-btn[data-tab="' + tab + '"]');
  if (btn) btn.classList.add('active');
  refresh();
  if (tab === 'performance' && typeof tradersSwitchSub === 'function') {
    setTimeout(function() { tradersSwitchSub('performance'); }, 250);
  }
}

function sidebarSearch(q) {
  q = (q || '').toLowerCase().trim();
  var btns = document.querySelectorAll('.sidebar-scroll .sidebar-btn');
  var labels = document.querySelectorAll('.sidebar-scroll .sidebar-section-label');
  var dividers = document.querySelectorAll('.sidebar-scroll .sidebar-divider');
  if (!q) {
    for (var i = 0; i < btns.length; i++) btns[i].style.display = '';
    for (var i = 0; i < labels.length; i++) labels[i].style.display = '';
    for (var i = 0; i < dividers.length; i++) dividers[i].style.display = '';
    return;
  }
  for (var i = 0; i < btns.length; i++) {
    var text = btns[i].textContent.toLowerCase();
    btns[i].style.display = text.indexOf(q) !== -1 ? '' : 'none';
  }
  for (var i = 0; i < labels.length; i++) labels[i].style.display = 'none';
  for (var i = 0; i < dividers.length; i++) dividers[i].style.display = 'none';
}

function wrColor(wr) { return wr >= 50 ? 'var(--success)' : wr >= 40 ? 'var(--warning)' : 'var(--error)'; }
function esc(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function pct(w, l) { var t = w + l; return t > 0 ? (w / t * 100).toFixed(1) + '%' : '--'; }
async function safeFetch(url, opts) {
  var resp = await fetch(url, opts);
  if (!resp.ok) { console.warn('API error', resp.status, url); return null; }
  return resp.json();
}

async function restartAgent(agentId) {
  if (!confirm('Restart ' + (AGENT_NAMES[agentId] || agentId) + '?')) return;
  try {
    var resp = await fetch('/api/system/action/restart-' + agentId, {method:'POST'});
    var data = await resp.json();
    if (data.success) {
      showToast((AGENT_NAMES[agentId]||agentId) + ' restarting...', 'success');
      setTimeout(refresh, 3000);
    } else {
      showToast('Restart failed: ' + (data.error || 'unknown'), 'error');
    }
  } catch(e) {
    showToast('Restart error: ' + e.message, 'error');
  }
}

var _brainCountsCache = {};
function fetchBrainCounts() {
  fetch('/api/brain/all').then(function(r){return r.json();}).then(function(d){
    _brainCountsCache = d.agents || {};
  }).catch(function(){});
}
function renderAgentGrid(overview) {
  fetchBrainCounts();
  var g = overview.garves || {};
  var s = overview.soren || {};
  var sh = overview.shelby || {};
  var brainAgentMap = {garves:'garves',soren:'soren',shelby:'shelby',atlas:'atlas',lisa:'lisa',sentinel:'robotox',thor:'thor',hawk:'hawk',viper:'viper',quant:'quant',odin:'odin',oracle:'oracle'};
  var cards = [
    {id:'garves', stats:[['WR',(g.win_rate||0)+'%'],['Trades',g.total_trades||0],['Pending',g.pending||0]], online:g.running},
    {id:'hawk', stats:[['WR',((overview.hawk||{}).win_rate||0)+'%'],['Open',(overview.hawk||{}).open_bets||0]], online:(overview.hawk||{}).running},
    {id:'odin', stats:[['Mode',((overview.odin||{}).mode||'paper').toUpperCase()],['Open',(overview.odin||{}).open_positions||0],['P&L','$'+((overview.odin||{}).total_pnl||0).toFixed(2)]], online:(overview.odin||{}).running},
    {id:'oracle', stats:[['Regime',((overview.oracle||{}).regime||'--').replace(/_/g,' ')],['WR',((overview.oracle||{}).win_rate||0)+'%']], online:(overview.oracle||{}).running},
    {id:'soren', stats:[['Queue',s.queue_pending||0],['Posted',s.total_posted||0]], online:true},
    {id:'lisa', stats:[['Posts',(overview.lisa||{}).total_posts||0]], online:true},
    {id:'shelby', stats:[['Status',sh.running?'Online':'Offline']], online:sh.running},
    {id:'atlas', stats:[['Status','Active']], online:true},
    {id:'thor', stats:[['Done',(overview.thor||{}).completed||0],['Queue',(overview.thor||{}).pending||0]], online:(overview.thor||{}).state !== 'offline'},
    {id:'sentinel', stats:[['Role','Monitor']], online:true},
    {id:'viper', stats:[['Found',(overview.viper||{}).opportunities||0]], online:(overview.viper||{}).running},
    {id:'quant', stats:[['Best WR',((overview.quant||{}).best_win_rate||0)+'%']], online:(overview.quant||{}).running}
  ];
  var html = '';
  for (var i = 0; i < cards.length; i++) {
    var c = cards[i];
    var agentColor = AGENT_COLORS[c.id] || '#888';
    var brainKey = brainAgentMap[c.id] || c.id;
    var brainCount = _brainCountsCache[brainKey] || 0;
    html += '<div class="agent-card" data-agent="' + c.id + '" onclick="switchTab(\'' + c.id + '\')" style="border-top:2px solid ' + agentColor + ';padding:var(--space-3) var(--space-4);">';
    html += '<div class="agent-card-header" style="margin-bottom:2px;">';
    html += '<div style="display:flex;align-items:center;gap:6px;flex:1;min-width:0;">';
    html += '<span class="status-dot ' + (c.online !== false ? 'online' : 'offline') + '" style="width:6px;height:6px;"></span>';
    html += '<span class="agent-card-name" style="font-size:0.78rem;">' + (AGENT_NAMES[c.id] || c.id) + '</span>';
    if (brainCount > 0) {
      html += '<span class="brain-badge" title="' + brainCount + ' brain note' + (brainCount > 1 ? 's' : '') + '" style="font-size:0.58rem;min-width:14px;height:14px;padding:0 3px;">' + brainCount + '</span>';
    }
    html += '</div>';
    var restartId = c.id === 'sentinel' ? 'robotox' : c.id;
    html += '<button class="ov-action-btn" onclick="event.stopPropagation();restartAgent(\'' + restartId + '\')" title="Restart ' + (AGENT_NAMES[c.id]||c.id) + '" style="padding:1px 6px;font-size:0.56rem;">&#x27F3;</button>';
    html += '</div>';
    html += '<div class="agent-card-role" style="font-size:0.62rem;margin-bottom:4px;">' + (AGENT_ROLES[c.id] || '') + '</div>';
    html += '<div class="agent-card-stats" style="gap:1px;">';
    for (var j = 0; j < c.stats.length; j++) {
      html += '<div class="agent-card-stat" style="font-size:0.68rem;"><span class="label" style="font-size:0.62rem;">' + c.stats[j][0] + '</span><span>' + c.stats[j][1] + '</span></div>';
    }
    html += '</div></div>';
  }
  document.getElementById('agent-grid').innerHTML = html;
}

function renderIndicatorAccuracy(data) {
  var acc = (data.garves || {}).indicator_accuracy || {};
  var tbody = document.getElementById('indicator-tbody');
  var keys = Object.keys(acc);
  if (keys.length === 0) return;
  var html = '';
  for (var i = 0; i < keys.length; i++) {
    var name = keys[i];
    var ind = acc[name];
    var a = (ind.accuracy || 0) * 100;
    html += '<tr><td>' + esc(name) + '</td>';
    html += '<td style="color:' + wrColor(a) + '">' + a.toFixed(1) + '%</td>';
    html += '<td>' + (ind.total_votes || 0) + '</td>';
    html += '<td>' + (ind.correct_votes || 0) + '</td>';
    html += '<td>1.00</td></tr>';
  }
  tbody.innerHTML = html;
}

async function loadMemoryFeed() {
  var el = document.getElementById('memory-feed-panel');
  if (!el) return;
  try {
    var resp = await fetch('/api/brain/recent-updates');
    var d = await resp.json();
    var items = d.updates || [];
    if (items.length === 0) { el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);font-size:0.76rem;">No recent memory updates.</div>'; return; }
    var html = '';
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var color = AGENT_COLORS[it.agent] || 'var(--text-secondary)';
      html += '<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.025);font-size:0.74rem;">';
      html += '<span style="color:' + color + ';font-weight:600;min-width:60px;font-family:var(--font-mono);">' + esc(AGENT_NAMES[it.agent] || it.agent) + '</span>';
      html += '<span style="color:var(--text-muted);min-width:48px;">' + esc(it.time || '') + '</span>';
      html += '<span style="color:var(--text-secondary);flex:1;">' + esc((it.topic || '') + ': ' + (it.content || '').substring(0, 120)) + '</span>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">Memory feed unavailable.</div>'; }
}

function renderGarvesStats(data) {
  var s = data.summary || {};
  document.getElementById('garves-winrate').textContent = (s.win_rate || 0) + '%';
  document.getElementById('garves-winrate').style.color = wrColor(s.win_rate || 0);
  document.getElementById('garves-pnl').textContent = '$' + (s.pnl || 0).toFixed(2);
  document.getElementById('garves-pnl').style.color = (s.pnl || 0) >= 0 ? 'var(--success)' : 'var(--error)';
  document.getElementById('garves-wins-losses').textContent = (s.wins || 0) + ' / ' + (s.losses || 0);
  document.getElementById('garves-total').textContent = s.total_trades || 0;
  document.getElementById('garves-resolved').textContent = s.resolved || 0;
  document.getElementById('garves-pending').textContent = s.pending || 0;
}

function renderBreakdown(tbodyId, data) {
  var el = document.getElementById(tbodyId);
  var keys = Object.keys(data || {});
  if (keys.length === 0) { el.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center;">--</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var d = data[k];
    var w = d.wins || 0; var l = d.losses || 0;
    html += '<tr><td>' + esc(k.toUpperCase()) + '</td><td>' + (w + l) + '</td><td style="color:' + wrColor(w/(w+l)*100) + '">' + pct(w,l) + '</td></tr>';
  }
  el.innerHTML = html;
}

function renderPendingTrades(trades) {
  var el = document.getElementById('pending-tbody');
  if (!trades || trades.length === 0) { el.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:24px;">No pending trades</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    html += '<tr><td>' + esc(t.time) + '</td><td>' + esc(t.asset) + ' ' + esc(t.timeframe) + '</td>';
    html += '<td style="color:' + (t.direction === 'UP' ? 'var(--success)' : 'var(--error)') + '">' + esc(t.direction) + '</td>';
    html += '<td>' + ((t.edge||0)*100).toFixed(1) + '%</td>';
    html += '<td>' + ((t.confidence||0)*100).toFixed(0) + '%</td>';
    html += '<td><span class="badge badge-warning">Pending</span></td></tr>';
  }
  el.innerHTML = html;
}

function renderResolvedTrades(trades) {
  var el = document.getElementById('resolved-tbody');
  if (!trades || trades.length === 0) { el.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:24px;">No resolved trades</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    var won = t.won;
    html += '<tr><td>' + esc(t.time) + '</td><td>' + esc(t.asset) + ' ' + esc(t.timeframe) + '</td>';
    html += '<td style="color:' + (t.direction === 'UP' ? 'var(--success)' : 'var(--error)') + '">' + esc(t.direction) + '</td>';
    html += '<td>' + ((t.edge||0)*100).toFixed(1) + '%</td>';
    html += '<td><span class="badge ' + (won ? 'badge-success' : 'badge-error') + '">' + (won ? 'WIN' : 'LOSS') + '</span></td>';
    html += '<td>' + esc(t.outcome) + '</td></tr>';
  }
  el.innerHTML = html;
}

// === GARVES LIVE TAB RENDERERS ===
function renderLiveStats(data) {
  var s = data.summary || {};
  animateCount('live-winrate', s.win_rate || 0, 800, '', '%');
  document.getElementById('live-winrate').style.color = wrColor(s.win_rate || 0);
  var pnl = s.pnl || 0;
  animateCount('live-pnl', Math.abs(pnl), 800, pnl >= 0 ? '+$' : '-$');
  document.getElementById('live-pnl').style.color = pnl >= 0 ? 'var(--success)' : 'var(--error)';
  document.getElementById('live-wins-losses').textContent = (s.wins || 0) + ' / ' + (s.losses || 0);
  document.getElementById('live-total').textContent = s.total_trades || 0;
  document.getElementById('live-resolved').textContent = s.resolved || 0;
  document.getElementById('live-pending').textContent = s.pending || 0;
}

function renderLiveBalance(data) {
  var portEl = document.getElementById('live-portfolio');
  var cashEl = document.getElementById('live-cash');
  var pnlEl = document.getElementById('live-real-pnl');
  if (!portEl) return;
  // Animate hero stat values
  animateCount('live-portfolio', data.portfolio || 0, 800, '$');
  portEl.style.color = (data.portfolio || 0) > 0 ? 'var(--success)' : 'var(--error)';
  animateCount('live-cash', data.cash || 0, 800, '$');
  cashEl.style.color = (data.cash || 0) > 0 ? 'var(--success)' : 'var(--text-muted)';
  var pnl = data.pnl || 0;
  animateCount('live-real-pnl', Math.abs(pnl), 800, pnl >= 0 ? '+$' : '-$');
  pnlEl.style.color = pnl >= 0 ? 'var(--success)' : 'var(--error)';
  // Show live/stale indicator
  var label = 'Portfolio';
  if (data.live && !data.stale) {
    label += ' <span style="color:var(--success);font-size:0.6rem;">LIVE</span>';
  } else if (data.stale) {
    label += ' <span style="color:var(--warning);font-size:0.6rem;">STALE</span>';
  }
  portEl.parentElement.querySelector('.stat-label').innerHTML = label;
  // Populate hero cards
  var heroToday = document.getElementById('garves-hero-today-pnl');
  var heroTotal = document.getElementById('garves-hero-total-pnl');
  if (heroTotal) heroTotal.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
  if (heroToday && data.today_pnl != null) {
    var tp = data.today_pnl;
    heroToday.textContent = (tp >= 0 ? '+$' : '-$') + Math.abs(tp).toFixed(2);
    heroToday.style.color = tp >= 0 ? 'var(--success)' : 'var(--error)';
  }
}

// === LIVE COUNTDOWN TIMER ENGINE ===
var _pendingTradesData = [];
var _countdownInterval = null;

function formatCountdown(seconds) {
  if (seconds <= 0) return 'RESOLVING...';
  var h = Math.floor(seconds / 3600);
  var m = Math.floor((seconds % 3600) / 60);
  var s = Math.floor(seconds % 60);
  if (h > 0) return h + 'h ' + String(m).padStart(2, '0') + 'm ' + String(s).padStart(2, '0') + 's';
  return String(m).padStart(2, '0') + 'm ' + String(s).padStart(2, '0') + 's';
}

function getCountdownColor(seconds) {
  if (seconds <= 0) return '#ff6b35';
  if (seconds < 120) return 'var(--error)';
  if (seconds < 600) return 'var(--warning)';
  return 'var(--success)';
}

function getCountdownPct(seconds, timeframe) {
  var total = 900;
  if (timeframe === '1h') total = 3600;
  else if (timeframe === '4h') total = 14400;
  else if (timeframe === '15m') total = 900;
  else if (timeframe === '5m') total = 300;
  var pct = Math.max(0, Math.min(100, (seconds / total) * 100));
  return pct;
}

function updateCountdowns() {
  var now = Date.now() / 1000;
  for (var i = 0; i < _pendingTradesData.length; i++) {
    var t = _pendingTradesData[i];
    var remaining = (t.market_end_ts || 0) - now;
    var cdEl = document.getElementById('countdown-' + i);
    var barEl = document.getElementById('cd-bar-' + i);
    if (cdEl) {
      cdEl.textContent = formatCountdown(remaining);
      cdEl.style.color = getCountdownColor(remaining);
    }
    if (barEl) {
      var pct = getCountdownPct(remaining, t.timeframe);
      barEl.style.width = pct + '%';
      barEl.style.background = getCountdownColor(remaining);
    }
  }
}

// === POSITION COUNTDOWN TICKER (Hawk + Garves on-chain) ===
var _positionCountdownInterval = null;

function _ensurePositionCountdownTicker() {
  if (_positionCountdownInterval) return;
  _positionCountdownInterval = setInterval(_tickPositionCountdowns, 1000);
}

function _tickPositionCountdowns() {
  // Hawk open positions
  for (var i = 0; i < _hawkPositionsData.length; i++) {
    var el = document.getElementById('hawk-pos-timer-' + i);
    if (el && _hawkPositionsData[i].end_date) {
      var tl = hawkCalcTimeLeft(_hawkPositionsData[i].end_date, _hawkPositionsData[i].cur_price || 0, _hawkPositionsData[i].category);
      el.innerHTML = tl.text + '<div style="font-size:0.58rem;font-weight:400;opacity:0.7;color:' + tl.color + ';">' + (tl.label || '') + '</div>';
      el.style.color = tl.color;
    }
  }
  // Garves on-chain holdings
  for (var j = 0; j < _onChainHoldingsData.length; j++) {
    var el2 = document.getElementById('oc-timer-' + j);
    if (el2 && _onChainHoldingsData[j].end_date) {
      var tl2 = hawkCalcTimeLeft(_onChainHoldingsData[j].end_date, _onChainHoldingsData[j].cur_price || 0, _onChainHoldingsData[j].category);
      el2.innerHTML = tl2.text + '<div style="font-size:0.58rem;font-weight:400;opacity:0.7;color:' + tl2.color + ';">' + (tl2.label || '') + '</div>';
      el2.style.color = tl2.color;
    }
  }
}

function renderLivePendingTrades(trades) {
  var el = document.getElementById('live-pending-tbody');
  var countBadge = document.getElementById('pending-count-badge');
  var exposureEl = document.getElementById('pending-exposure');
  _pendingTradesData = trades || [];

  if (countBadge) countBadge.textContent = _pendingTradesData.length;

  if (!trades || trades.length === 0) {
    el.innerHTML = '<tr><td colspan="8" class="text-muted" style="text-align:center;padding:24px;">No pending live trades</td></tr>';
    if (exposureEl) exposureEl.textContent = 'Exposure: $0.00';
    return;
  }

  var totalExposure = 0;
  var html = '';
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    var stake = t.stake || 0;
    var entryPrice = t.entry_price || 0.5;
    var potProfit = stake * (1 - entryPrice) - stake * 0.02;
    var potLoss = stake * entryPrice;
    totalExposure += stake;
    var now = Date.now() / 1000;
    var remaining = (t.market_end_ts || 0) - now;
    var cdPct = getCountdownPct(remaining, t.timeframe);

    html += '<tr>';
    html += '<td style="font-size:0.72rem;">' + esc(t.time) + '</td>';
    html += '<td><span style="font-weight:600;">' + esc(t.asset) + '</span> <span class="text-muted">' + esc(t.timeframe) + '</span></td>';
    html += '<td style="color:' + (t.direction === 'UP' ? 'var(--success)' : 'var(--error)') + ';font-weight:700;">' + esc(t.direction) + '</td>';
    html += '<td style="font-weight:600;">$' + stake.toFixed(2) + '</td>';
    html += '<td>' + ((t.edge||0)*100).toFixed(1) + '%</td>';
    html += '<td style="min-width:120px;">';
    html += '<div style="font-weight:700;font-family:var(--font-mono);font-size:0.82rem;" id="countdown-' + i + '">' + formatCountdown(remaining) + '</div>';
    html += '<div style="background:rgba(255,255,255,0.05);border-radius:3px;height:4px;margin-top:3px;overflow:hidden;">';
    html += '<div id="cd-bar-' + i + '" style="height:100%;border-radius:3px;background:' + getCountdownColor(remaining) + ';width:' + cdPct + '%;transition:width 1s linear;"></div></div>';
    html += '<div class="text-muted" style="font-size:0.64rem;margin-top:2px;">Expires ' + esc(t.expires) + '</div>';
    html += '</td>';
    html += '<td style="color:var(--success);font-weight:600;">+$' + potProfit.toFixed(2) + '</td>';
    html += '<td style="color:var(--error);font-weight:600;">-$' + potLoss.toFixed(2) + '</td>';
    html += '</tr>';
  }
  el.innerHTML = html;

  if (exposureEl) exposureEl.textContent = 'Exposure: $' + totalExposure.toFixed(2);

  // Start countdown timer if not running
  if (!_countdownInterval) {
    _countdownInterval = setInterval(updateCountdowns, 1000);
  }
}

function renderLiveResolvedTrades(trades) {
  var el = document.getElementById('live-resolved-tbody');
  var summaryEl = document.getElementById('resolved-summary');
  if (!trades || trades.length === 0) {
    el.innerHTML = '<tr><td colspan="7" class="text-muted" style="text-align:center;padding:24px;">No resolved live trades yet</td></tr>';
    if (summaryEl) summaryEl.textContent = '--';
    return;
  }

  var totalWon = 0, totalLost = 0, winCount = 0, lossCount = 0;
  var html = '';
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    var won = t.won;
    var pnl = t.est_pnl || 0;
    var stake = t.stake || 0;
    var pnlStr = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
    var pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--error)';
    if (won) { totalWon += pnl; winCount++; } else { totalLost += Math.abs(pnl); lossCount++; }

    html += '<tr>';
    html += '<td style="font-size:0.72rem;">' + esc(t.time) + '</td>';
    html += '<td><span style="font-weight:600;">' + esc(t.asset) + '</span> <span class="text-muted">' + esc(t.timeframe) + '</span></td>';
    html += '<td style="color:' + (t.direction === 'UP' ? 'var(--success)' : 'var(--error)') + ';font-weight:700;">' + esc(t.direction) + '</td>';
    html += '<td>$' + stake.toFixed(2) + '</td>';
    html += '<td>' + ((t.edge||0)*100).toFixed(1) + '%</td>';
    html += '<td><span class="badge ' + (won ? 'badge-success' : 'badge-error') + '" style="font-weight:700;">' + (won ? 'WIN' : 'LOSS') + '</span></td>';
    html += '<td style="color:' + pnlColor + ';font-weight:700;font-size:0.88rem;">' + pnlStr + '</td>';
    html += '</tr>';
  }
  el.innerHTML = html;

  if (summaryEl) {
    var net = totalWon - totalLost;
    var netStr = (net >= 0 ? '+$' : '-$') + Math.abs(net).toFixed(2);
    var netColor = net >= 0 ? 'var(--success)' : 'var(--error)';
    summaryEl.innerHTML = '<span style="color:var(--success);">' + winCount + 'W</span> / <span style="color:var(--error);">' + lossCount + 'L</span> | Won: <span style="color:var(--success);">+$' + totalWon.toFixed(2) + '</span> Lost: <span style="color:var(--error);">-$' + totalLost.toFixed(2) + '</span> | Net: <span style="color:' + netColor + ';font-weight:700;">' + netStr + '</span>';
  }
}

// === PORTFOLIO RENDERER ===
var _onChainHoldingsData = [];

function renderOnChainPositions(data) {
  var dot = document.getElementById('positions-live-dot');
  if (!dot) return;

  dot.style.background = data.live ? 'var(--success)' : '#555';

  var t = data.totals || {};

  // Summary cards — open positions only
  var countEl = document.getElementById('oc-open-count');
  var marginEl = document.getElementById('oc-margin');
  var valEl = document.getElementById('oc-value');
  var pnlEl = document.getElementById('oc-open-pnl');

  if (countEl) countEl.textContent = t.open_count || 0;
  if (marginEl) marginEl.textContent = '$' + (t.open_margin || 0).toFixed(2);
  if (valEl) {
    valEl.textContent = '$' + (t.open_value || 0).toFixed(2);
    valEl.style.color = (t.open_value || 0) > 0 ? 'var(--success)' : '';
  }
  if (pnlEl) {
    var op = t.open_pnl || 0;
    pnlEl.textContent = (op >= 0 ? '+$' : '-$') + Math.abs(op).toFixed(2);
    pnlEl.style.color = op >= 0 ? 'var(--success)' : 'var(--error)';
  }

  // Current Holdings table
  var holdEl = document.getElementById('oc-holdings-tbody');
  var holdings = data.holdings || [];
  _onChainHoldingsData = holdings;
  if (holdEl) {
    if (holdings.length === 0) {
      holdEl.innerHTML = '<tr><td colspan="13" class="text-muted" style="text-align:center;padding:16px;">No open positions — scanning for opportunities</td></tr>';
    } else {
      var html = '';
      for (var i = 0; i < holdings.length; i++) {
        var p = holdings[i];
        var pc = p.pnl >= 0 ? 'var(--success)' : 'var(--error)';
        var ps = (p.pnl >= 0 ? '+$' : '-$') + Math.abs(p.pnl).toFixed(2);
        var tl = hawkCalcTimeLeft(p.end_date, p.cur_price || 0, p.category || 'crypto_event');
        var gPayout = (p.size || 0) * 1.0;
        var gReturn = gPayout - (p.cost || 0);
        var gReturnPct = (p.cost || 0) > 0 ? (gReturn / p.cost * 100) : 0;
        var engName = (p.engine || 'snipe').toLowerCase().replace(/[^a-z_]/g, '');
        var engLabel = engName === 'res_scalp' ? 'RES' : engName === 'maker' ? 'MKR' : engName === 'whale' ? 'WHALE' : engName === 'taker' ? 'TAKER' : 'SNIPE';
        var engClass = engName === 'res_scalp' ? 'res_scalp' : engName;
        html += '<tr>';
        html += '<td><span class="gv-engine-pill ' + esc(engClass) + '">' + engLabel + '</span></td>';
        html += '<td style="font-weight:600;">' + esc(p.asset) + '</td>';
        html += '<td style="font-size:0.72rem;">' + esc(p.market) + '</td>';
        html += '<td style="color:' + (p.outcome === 'Up' ? 'var(--success)' : 'var(--error)') + ';font-weight:600;">' + esc(p.outcome) + '</td>';
        html += '<td>' + p.size.toFixed(0) + '</td>';
        html += '<td>$' + p.avg_price.toFixed(3) + '</td>';
        html += '<td style="font-weight:600;">$' + p.cur_price.toFixed(3) + '</td>';
        html += '<td>$' + p.cost.toFixed(2) + '</td>';
        html += '<td style="font-weight:600;">$' + p.value.toFixed(2) + '</td>';
        html += '<td id="oc-timer-' + i + '" style="color:' + tl.color + ';font-weight:600;font-size:0.76rem;white-space:nowrap;">' + tl.text + '<div style="font-size:0.58rem;font-weight:400;opacity:0.7;">' + (tl.label || '') + '</div></td>';
        if (p.status === 'won') {
          html += '<td><span class="badge badge-success" style="font-weight:700;">WON ' + ps + '</span></td>';
        } else {
          html += '<td style="color:' + pc + ';font-weight:700;">' + ps + ' (' + p.pnl_pct.toFixed(1) + '%)</td>';
        }
        html += '<td style="color:#00d4ff;font-weight:600;">$' + gPayout.toFixed(2) + '</td>';
        html += '<td style="color:' + (gReturn >= 0 ? '#00ff44' : '#ff4444') + ';font-weight:600;">+$' + gReturn.toFixed(2) + ' <span style="font-size:0.68rem;opacity:0.7;">(+' + gReturnPct.toFixed(0) + '%)</span></td>';
        html += '</tr>';
      }
      holdEl.innerHTML = html;
      _ensurePositionCountdownTicker();
    }
  }

  // Trade History
  var histEl = document.getElementById('oc-history-tbody');
  var recEl = document.getElementById('oc-record');
  var realPnlEl = document.getElementById('oc-realized-pnl');
  var history = data.history || [];

  if (recEl) {
    var w = t.record_wins || 0;
    var l = t.record_losses || 0;
    recEl.innerHTML = '<span style="color:var(--success);font-weight:600;">' + w + 'W</span> / <span style="color:var(--error);font-weight:600;">' + l + 'L</span>';
  }
  if (realPnlEl) {
    var rp = t.realized_pnl || 0;
    realPnlEl.innerHTML = 'Realized: <span style="color:' + (rp >= 0 ? 'var(--success)' : 'var(--error)') + ';font-weight:600;">' + (rp >= 0 ? '+$' : '-$') + Math.abs(rp).toFixed(2) + '</span>';
  }
  if (histEl) {
    if (history.length === 0) {
      histEl.innerHTML = '<tr><td colspan="7" class="text-muted" style="text-align:center;padding:12px;">No trade history</td></tr>';
    } else {
      var html = '';
      for (var i = 0; i < history.length; i++) {
        var p = history[i];
        var won = p.won;
        var rp = p.result_pnl || 0;
        var rc = rp >= 0 ? 'var(--success)' : 'var(--error)';
        var rs = (rp >= 0 ? '+$' : '-$') + Math.abs(rp).toFixed(2);
        var hEngName = (p.engine || 'snipe').toLowerCase().replace(/[^a-z_]/g, '');
        var hEngLabel = hEngName === 'res_scalp' ? 'RES' : hEngName === 'maker' ? 'MKR' : hEngName === 'whale' ? 'WHALE' : hEngName === 'taker' ? 'TAKER' : 'SNIPE';
        var hEngClass = hEngName === 'res_scalp' ? 'res_scalp' : hEngName;
        html += '<tr>';
        html += '<td><span class="gv-engine-pill ' + esc(hEngClass) + '">' + hEngLabel + '</span></td>';
        html += '<td style="font-weight:600;">' + esc(p.asset) + '</td>';
        html += '<td style="font-size:0.72rem;">' + esc(p.market) + '</td>';
        html += '<td>' + esc(p.outcome) + '</td>';
        html += '<td>$' + (p.cost || 0).toFixed(2) + '</td>';
        html += '<td><span class="badge ' + (won ? 'badge-success' : 'badge-error') + '" style="font-weight:700;">' + (won ? 'WON' : 'LOST') + '</span></td>';
        html += '<td style="color:' + rc + ';font-weight:700;font-size:0.88rem;">' + rs + '</td>';
        html += '</tr>';
      }
      histEl.innerHTML = html;
    }
  }
}

function renderLiveLogs(lines) {
  var el = document.getElementById('live-log-feed');
  if (!lines || lines.length === 0) return;
  var html = '';
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i] || '';
    var cls = '';
    if (line.indexOf('ERROR') !== -1) cls = 'error';
    else if (line.indexOf('WARNING') !== -1) cls = 'warning';
    else if (line.indexOf('TRADE') !== -1 || line.indexOf('WIN') !== -1 || line.indexOf('LIVE') !== -1) cls = 'success';
    var parts = line.split(' ');
    var time = parts.length > 1 ? parts[0] + ' ' + parts[1] : '';
    var msg = parts.slice(2).join(' ');
    html += '<div class="log-line"><span class="log-time">' + esc(time) + '</span><span class="log-msg ' + cls + '">' + esc(msg) + '</span></div>';
  }
  el.innerHTML = html;
}

function renderLogs(lines) {
  var el = document.getElementById('log-feed');
  if (!lines || lines.length === 0) return;
  var html = '';
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i] || '';
    var cls = '';
    if (line.indexOf('ERROR') !== -1) cls = 'error';
    else if (line.indexOf('WARNING') !== -1) cls = 'warning';
    else if (line.indexOf('TRADE') !== -1 || line.indexOf('WIN') !== -1) cls = 'success';
    var parts = line.split(' ');
    var time = parts.length > 1 ? parts[0] + ' ' + parts[1] : '';
    var msg = parts.slice(2).join(' ');
    html += '<div class="log-line"><span class="log-time">' + esc(time) + '</span><span class="log-msg ' + cls + '">' + esc(msg) + '</span></div>';
  }
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
}

async function loadDailyReports() {
  // Load daily history table
  try {
    var resp = await fetch('/api/garves/daily-reports');
    var data = await resp.json();
    var reports = data.reports || [];
    var tbody = document.getElementById('daily-history-tbody');
    if (!tbody) return;
    if (!reports.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-muted" style="text-align:center;padding:24px;">No daily reports yet — first report generates at midnight ET</td></tr>';
      return;
    }
    var html = '';
    for (var i = reports.length - 1; i >= 0; i--) {
      var r = reports[i];
      var s = r.summary || {};
      var wr = s.win_rate || 0;
      var wrColor = wr >= 55 ? 'var(--success)' : wr >= 45 ? 'var(--warning)' : 'var(--error)';
      var pnlColor = (s.pnl || 0) >= 0 ? 'var(--success)' : 'var(--error)';
      var mistakeCount = (r.mistakes || []).filter(function(m) { return m.severity === 'high'; }).length;
      var regime = (r.strategy || {}).regime || '?';
      html += '<tr>';
      html += '<td style="font-weight:600;">' + esc(r.date || '?') + '</td>';
      html += '<td>' + (s.total_trades || 0) + '</td>';
      html += '<td>' + (s.wins || 0) + '-' + (s.losses || 0) + '</td>';
      html += '<td style="color:' + wrColor + ';font-weight:600;">' + wr.toFixed(1) + '%</td>';
      html += '<td style="color:' + pnlColor + ';font-weight:600;">$' + (s.pnl || 0).toFixed(2) + '</td>';
      html += '<td>' + (s.avg_edge || 0).toFixed(1) + '%</td>';
      html += '<td><span class="badge badge-info">' + esc(regime) + '</span></td>';
      html += '<td>' + (mistakeCount > 0 ? '<span style="color:var(--error);">' + mistakeCount + ' high</span>' : '<span style="color:var(--success);">clean</span>') + '</td>';
      html += '</tr>';
    }
    tbody.innerHTML = html;
  } catch(e) {
    console.error('daily reports error:', e);
  }

  // Load today's mistake analysis
  try {
    var resp2 = await fetch('/api/garves/daily-report/today');
    var today = await resp2.json();
    var mistakes = today.mistakes || [];
    var el = document.getElementById('daily-mistakes');
    if (!el) return;
    if (!mistakes.length) {
      el.innerHTML = '<div class="text-muted" style="padding:var(--space-4);">No issues detected today.</div>';
      return;
    }
    var mhtml = '';
    for (var j = 0; j < mistakes.length; j++) {
      var m = mistakes[j];
      var sevColor = m.severity === 'high' ? 'var(--error)' : m.severity === 'medium' ? 'var(--warning)' : 'var(--success)';
      var typeIcon = m.type === 'technical' ? 'TECH' : m.type === 'approach' ? 'STRAT' : m.type === 'functional' ? 'FUNC' : 'INFO';
      mhtml += '<div class="glass-card" style="border-left:3px solid ' + sevColor + ';">';
      mhtml += '<div style="display:flex;justify-content:space-between;margin-bottom:4px;">';
      mhtml += '<span style="font-weight:600;font-size:0.8rem;">' + esc(m.title || '') + '</span>';
      mhtml += '<span><span class="badge" style="background:' + sevColor + ';color:#000;font-size:0.62rem;">' + typeIcon + '</span>';
      mhtml += ' <span class="badge" style="background:rgba(255,255,255,0.08);font-size:0.62rem;">' + esc(m.severity || '') + '</span></span>';
      mhtml += '</div>';
      mhtml += '<div style="font-size:0.76rem;color:var(--text-secondary);font-family:var(--font-mono);">' + esc(m.detail || '') + '</div>';
      mhtml += '</div>';
    }
    el.innerHTML = mhtml;
  } catch(e) {
    console.error('daily mistakes error:', e);
  }
}

async function loadDerivatives() {
  try {
    var resp = await fetch('/api/garves/derivatives');
    var data = await resp.json();

    // Funding rates card
    var frEl = document.getElementById('deriv-funding');
    if (frEl) {
      var fr = data.funding_rates || {};
      var assets = ['bitcoin', 'ethereum', 'solana'];
      var frHtml = '<div class="stat-label">Funding Rates' +
        (data.connected ? ' <span style="color:var(--success);">LIVE</span>' : ' <span style="color:var(--error);">OFFLINE</span>') +
        '</div>';
      for (var i = 0; i < assets.length; i++) {
        var a = assets[i];
        var r = fr[a];
        if (r) {
          var rate = (r.rate * 100).toFixed(4);
          var rateColor = r.rate > 0 ? 'var(--error)' : r.rate < 0 ? 'var(--success)' : 'var(--text-secondary)';
          var label = r.rate > 0 ? 'Longs pay' : r.rate < 0 ? 'Shorts pay' : 'Neutral';
          frHtml += '<div style="display:flex;justify-content:space-between;padding:4px 8px;font-size:0.78rem;">';
          frHtml += '<span style="text-transform:uppercase;font-weight:600;">' + a.slice(0,3) + '</span>';
          frHtml += '<span style="color:' + rateColor + ';">' + rate + '% <span style="font-size:0.65rem;opacity:0.7;">(' + label + ')</span></span>';
          frHtml += '</div>';
        } else {
          frHtml += '<div style="padding:4px 8px;font-size:0.78rem;color:var(--text-secondary);">' + a.slice(0,3).toUpperCase() + ': --</div>';
        }
      }
      frEl.innerHTML = frHtml;
    }

    // Liquidations card
    var liqEl = document.getElementById('deriv-liquidations');
    if (liqEl) {
      var liq = data.liquidations || {};
      var liqHtml = '<div class="stat-label">Liquidations (5m)</div>';
      for (var i = 0; i < assets.length; i++) {
        var a = assets[i];
        var l = liq[a];
        if (l) {
          var longUsd = (l.long_liq_usd_5m || 0);
          var shortUsd = (l.short_liq_usd_5m || 0);
          var cascade = l.cascade_detected;
          var cascDir = l.cascade_direction || '';
          liqHtml += '<div style="padding:4px 8px;font-size:0.78rem;">';
          liqHtml += '<span style="text-transform:uppercase;font-weight:600;">' + a.slice(0,3) + '</span> ';
          liqHtml += '<span style="color:var(--error);">L:$' + (longUsd/1000).toFixed(1) + 'K</span> ';
          liqHtml += '<span style="color:var(--success);">S:$' + (shortUsd/1000).toFixed(1) + 'K</span>';
          if (cascade) {
            liqHtml += ' <span class="badge" style="background:var(--warning);color:#000;font-size:0.6rem;animation:pulse 1s infinite;">CASCADE ' + cascDir.toUpperCase() + '</span>';
          }
          liqHtml += ' <span style="font-size:0.65rem;opacity:0.6;">' + (l.event_count || 0) + ' events</span>';
          liqHtml += '</div>';
        }
      }
      liqEl.innerHTML = liqHtml;
    }

    // Spot depth card
    var depthEl = document.getElementById('deriv-depth');
    if (depthEl) {
      var depth = data.spot_depth || {};
      var dHtml = '<div class="stat-label">Spot Depth (Top 5)</div>';
      for (var i = 0; i < assets.length; i++) {
        var a = assets[i];
        var d = depth[a];
        if (d) {
          var imb = d.imbalance || 0;
          var imbColor = imb > 0.05 ? 'var(--success)' : imb < -0.05 ? 'var(--error)' : 'var(--text-secondary)';
          var imbLabel = imb > 0.05 ? 'BID heavy' : imb < -0.05 ? 'ASK heavy' : 'Balanced';
          dHtml += '<div style="display:flex;justify-content:space-between;padding:4px 8px;font-size:0.78rem;">';
          dHtml += '<span style="text-transform:uppercase;font-weight:600;">' + a.slice(0,3) + '</span>';
          dHtml += '<span style="color:' + imbColor + ';">' + (imb * 100).toFixed(1) + '% <span style="font-size:0.65rem;opacity:0.7;">(' + imbLabel + ')</span></span>';
          dHtml += '</div>';
          dHtml += '<div style="padding:0 8px 4px;font-size:0.65rem;color:var(--text-secondary);">';
          dHtml += 'Bid: $' + (d.bid_depth_usd/1000).toFixed(1) + 'K | Ask: $' + (d.ask_depth_usd/1000).toFixed(1) + 'K | Spread: ' + d.spread_pct + '%';
          dHtml += '</div>';
        }
      }
      depthEl.innerHTML = dHtml;
    }
  } catch(e) {
    console.error('derivatives error:', e);
  }
}

async function loadExternalData() {
  try {
    var resp = await fetch('/api/garves/external-data');
    var data = await resp.json();

    // Macro card
    var macroEl = document.getElementById('ext-macro');
    if (macroEl && data.macro) {
      var m = data.macro;
      var eventBadge = m.is_event_day
        ? '<span class="badge" style="background:var(--warning);color:#000;animation:pulse 1s infinite;">' + (m.event_type||'').toUpperCase() + ' DAY</span> '
        : '<span class="badge badge-success">Normal</span> ';
      var dxyColor = m.dxy_trend === 'rising' ? 'var(--error)' : m.dxy_trend === 'falling' ? 'var(--success)' : 'var(--text-secondary)';
      macroEl.innerHTML = '<div class="stat-label">Macro (FRED) ' + eventBadge + '</div>'
        + '<div style="padding:4px 8px;font-size:0.78rem;">DXY: <span style="color:' + dxyColor + ';">' + (m.dxy_value||0).toFixed(1) + ' (' + (m.dxy_trend||'--') + ')</span></div>'
        + '<div style="padding:4px 8px;font-size:0.78rem;">VIX: ' + (m.vix_value||0).toFixed(1) + '</div>'
        + (m.is_event_day ? '<div style="padding:4px 8px;font-size:0.78rem;">Edge mult: <b>' + (m.edge_multiplier||1).toFixed(1) + 'x</b></div>' : '');
    } else if (macroEl) {
      macroEl.innerHTML = '<div class="stat-label">Macro (FRED)</div><div class="text-muted" style="padding:8px;font-size:0.78rem;">No API key set</div>';
    }

    // DeFi card
    var defiEl = document.getElementById('ext-defi');
    if (defiEl && data.defi) {
      var d = data.defi;
      var scColor = (d.stablecoin_change_7d_pct||0) > 0 ? 'var(--success)' : 'var(--error)';
      var tvlColor = (d.tvl_change_24h_pct||0) > 0 ? 'var(--success)' : 'var(--error)';
      defiEl.innerHTML = '<div class="stat-label">DeFi (DeFiLlama)</div>'
        + '<div style="padding:4px 8px;font-size:0.78rem;">Stablecoin MCap: $' + ((d.stablecoin_mcap_usd||0)/1e9).toFixed(1) + 'B <span style="color:' + scColor + ';">(' + (d.stablecoin_change_7d_pct||0).toFixed(1) + '% 7d)</span></div>'
        + '<div style="padding:4px 8px;font-size:0.78rem;">TVL: $' + ((d.tvl_usd||0)/1e9).toFixed(1) + 'B <span style="color:' + tvlColor + ';">(' + (d.tvl_change_24h_pct||0).toFixed(1) + '% 24h)</span></div>';
    } else if (defiEl) {
      defiEl.innerHTML = '<div class="stat-label">DeFi (DeFiLlama)</div><div class="text-muted" style="padding:8px;font-size:0.78rem;">Waiting for data...</div>';
    }

    // Mempool card
    var mpEl = document.getElementById('ext-mempool');
    if (mpEl && data.mempool) {
      var mp = data.mempool;
      var congColor = mp.congestion_level === 'extreme' ? 'var(--error)' : mp.congestion_level === 'high' ? 'var(--warning)' : mp.congestion_level === 'elevated' ? '#ffaa00' : 'var(--success)';
      mpEl.innerHTML = '<div class="stat-label">Mempool (BTC) <span class="badge" style="background:' + congColor + ';color:#000;font-size:0.6rem;">' + (mp.congestion_level||'--').toUpperCase() + '</span></div>'
        + '<div style="padding:4px 8px;font-size:0.78rem;">Fee: ' + (mp.fastest_fee||0) + ' sat/vB (ratio: ' + (mp.fee_ratio||1).toFixed(1) + 'x)</div>'
        + '<div style="padding:4px 8px;font-size:0.78rem;">Pending TX: ' + (mp.tx_count||0).toLocaleString() + '</div>';
    } else if (mpEl) {
      mpEl.innerHTML = '<div class="stat-label">Mempool (BTC)</div><div class="text-muted" style="padding:8px;font-size:0.78rem;">Waiting for data...</div>';
    }

    // Per-asset Coinglass + Whale cards
    var assetMap = {bitcoin:'btc', ethereum:'eth', solana:'sol', xrp:'xrp'};
    var assets = data.assets || {};
    for (var aName in assetMap) {
      var elId = 'ext-' + assetMap[aName];
      var el = document.getElementById(elId);
      if (!el) continue;
      var ad = assets[aName] || {};
      var cg = ad.coinglass;
      var wh = ad.whale;
      var html = '<div class="stat-label">' + assetMap[aName].toUpperCase() + ' External</div>';
      if (cg) {
        var lsColor = cg.long_short_ratio > 1.5 ? 'var(--error)' : cg.long_short_ratio < 0.67 ? 'var(--success)' : 'var(--text-secondary)';
        var oiColor = cg.oi_change_1h_pct > 1 ? 'var(--success)' : cg.oi_change_1h_pct < -1 ? 'var(--error)' : 'var(--text-secondary)';
        html += '<div style="padding:2px 8px;font-size:0.72rem;">OI: $' + ((cg.oi_usd||0)/1e6).toFixed(0) + 'M <span style="color:' + oiColor + ';">(' + (cg.oi_change_1h_pct||0).toFixed(1) + '% 1h)</span></div>';
        html += '<div style="padding:2px 8px;font-size:0.72rem;">L/S: <span style="color:' + lsColor + ';">' + (cg.long_short_ratio||0).toFixed(2) + '</span></div>';
        if (cg.etf_available) {
          var etfColor = cg.etf_net_flow_usd > 0 ? 'var(--success)' : 'var(--error)';
          html += '<div style="padding:2px 8px;font-size:0.72rem;">ETF: <span style="color:' + etfColor + ';">$' + ((cg.etf_net_flow_usd||0)/1e6).toFixed(0) + 'M</span></div>';
        }
      } else {
        html += '<div style="padding:4px 8px;font-size:0.72rem;color:var(--text-secondary);">No Coinglass data</div>';
      }
      if (wh && wh.tx_count > 0) {
        var netColor = wh.net_flow_usd > 0 ? 'var(--error)' : 'var(--success)';
        html += '<div style="padding:2px 8px;font-size:0.72rem;">Whale: <span style="color:' + netColor + ';">$' + ((wh.net_flow_usd||0)/1e6).toFixed(1) + 'M net</span> (' + wh.tx_count + ' txs)</div>';
      }
      el.innerHTML = html;
    }
  } catch(e) {
    console.error('external data error:', e);
  }
}

async function loadMLWinPredictor() {
  try {
    var resp = await fetch('/api/garves/ml-status');
    var d = await resp.json();
    var statusEl = document.getElementById('ml-status');
    var accEl = document.getElementById('ml-accuracy');
    var samplesEl = document.getElementById('ml-samples');
    var f1El = document.getElementById('ml-f1');
    if (statusEl) {
      statusEl.textContent = d.model_loaded ? 'ACTIVE' : 'NOT TRAINED';
      statusEl.style.color = d.model_loaded ? 'var(--success)' : 'var(--warning)';
    }
    var m = d.metrics || {};
    if (accEl) accEl.textContent = m.cv_accuracy ? (m.cv_accuracy * 100).toFixed(1) + '%' : '--';
    if (samplesEl) samplesEl.textContent = m.num_samples || '--';
    if (f1El) f1El.textContent = m.f1 ? m.f1.toFixed(3) : '--';
    var barsEl = document.getElementById('ml-feature-bars');
    if (barsEl && m.top_features && m.top_features.length > 0) {
      var maxImp = m.top_features[0][1];
      var html = '';
      for (var i = 0; i < Math.min(m.top_features.length, 15); i++) {
        var f = m.top_features[i];
        var pct = (f[1] / maxImp * 100).toFixed(0);
        html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">';
        html += '<span style="min-width:180px;text-align:right;color:var(--text-muted);">' + f[0] + '</span>';
        html += '<div style="flex:1;background:rgba(255,255,255,0.05);border-radius:3px;height:14px;overflow:hidden;">';
        html += '<div style="width:' + pct + '%;height:100%;background:var(--agent-garves);border-radius:3px;"></div></div>';
        html += '<span style="min-width:50px;color:var(--text-muted);">' + (f[1] * 100).toFixed(1) + '%</span>';
        html += '</div>';
      }
      barsEl.innerHTML = html;
    }
  } catch(e) { console.error('ML status:', e); }
}

async function loadConvictionData() {
  try {
    var resp = await fetch('/api/garves/conviction');
    var data = await resp.json();
    if (data.error) return;
    // Indicator weights table only (asset signals removed — were always "no signal")
    var weights = data.indicator_weights || {};
    var wKeys = Object.keys(weights);
    if (wKeys.length > 0) {
      var wHtml = '';
      wKeys.sort(function(a,b) { return (weights[b].dynamic_weight||0) - (weights[a].dynamic_weight||0); });
      for (var j = 0; j < wKeys.length; j++) {
        var wk = wKeys[j];
        var w = weights[wk];
        var statusBadge = '';
        if (w.disabled) statusBadge = '<span class="badge badge-error" style="font-size:0.6rem;">DISABLED</span>';
        else if (w.dynamic_weight > w.base_weight) statusBadge = '<span class="badge badge-success" style="font-size:0.6rem;">BOOSTED</span>';
        else if (w.dynamic_weight < w.base_weight) statusBadge = '<span class="badge badge-warning" style="font-size:0.6rem;">REDUCED</span>';
        else statusBadge = '<span class="badge badge-info" style="font-size:0.6rem;">BASE</span>';
        var accText = w.accuracy !== null ? w.accuracy + '%' : '--';
        var accColor = w.accuracy !== null ? (w.accuracy >= 55 ? 'var(--success)' : w.accuracy < 45 ? 'var(--error)' : 'var(--text-primary)') : 'var(--text-secondary)';
        wHtml += '<tr><td style="font-weight:600;">' + esc(wk) + '</td><td>' + w.base_weight + '</td><td>' + w.dynamic_weight + '</td>';
        wHtml += '<td style="color:' + accColor + ';">' + accText + '</td><td>' + (w.total_votes || 0) + '</td><td>' + statusBadge + '</td></tr>';
      }
      document.getElementById('conv-weights-tbody').innerHTML = wHtml;
    }
  } catch (e) {}
}

async function loadRegimeBadge() {
  try {
    var resp = await fetch('/api/garves/regime');
    var data = await resp.json();
    var label = data.label || 'unknown';
    var fng = data.fng_value || 0;
    var cls = 'badge-info';
    if (label === 'extreme_fear' || label === 'fear') cls = 'badge-error';
    else if (label === 'greed' || label === 'extreme_greed') cls = 'badge-success';
    else if (label === 'neutral') cls = 'badge-warning';
    document.getElementById('garves-regime-badge').innerHTML = '<span class="badge ' + cls + '">Regime: ' + label.replace(/_/g, ' ').toUpperCase() + ' (FnG: ' + fng + ')</span>';
  } catch (e) {}
}

async function loadMLStatus() {
  try {
    var r = await fetch('/api/ml/status');
    var d = await r.json();
    // LSTM
    var lstmEl = document.getElementById('ml-lstm-status');
    if (lstmEl && d.lstm) {
      var assets = Object.keys(d.lstm);
      if (assets.length > 0) {
        var html = assets.map(function(a) {
          var m = d.lstm[a];
          var accPct = (m.val_acc * 100).toFixed(1);
          var color = m.val_acc >= 0.55 ? 'var(--success)' : 'var(--text-muted)';
          return '<div style="display:flex;justify-content:space-between;"><span>' + a.charAt(0).toUpperCase() + a.slice(1) + '</span><span style="color:' + color + '">' + accPct + '% acc</span><span style="color:var(--text-muted)">' + m.candles + ' candles</span></div>';
        }).join('');
        lstmEl.innerHTML = html;
      } else {
        lstmEl.innerHTML = '<span style="color:var(--text-muted)">No models trained yet</span>';
      }
    }
    // XGBoost
    var xgbEl = document.getElementById('ml-xgb-status');
    if (xgbEl && d.xgboost) {
      if (d.xgboost.status === 'trained') {
        xgbEl.innerHTML = '<div style="color:var(--success)">Active</div><div>Accuracy: ' + (d.xgboost.accuracy * 100).toFixed(1) + '%</div><div>F1: ' + (d.xgboost.f1 * 100).toFixed(1) + '%</div><div>' + d.xgboost.num_samples + ' trades</div>';
      } else {
        var resolved = d.xgboost.resolved_trades || d.xgboost.num_samples || 0;
        xgbEl.innerHTML = '<div style="color:var(--warning)">Waiting for data</div><div>' + resolved + '/30 resolved trades</div>';
      }
    }
    // FinBERT
    var fbEl = document.getElementById('ml-finbert-status');
    if (fbEl && d.finbert) {
      if (d.finbert.status === 'loaded') {
        fbEl.innerHTML = '<div style="color:var(--success)">Active</div><div>ProsusAI/finbert</div><div>MPS accelerated</div>';
      } else if (d.finbert.status === 'not_loaded') {
        fbEl.innerHTML = '<div style="color:var(--text-muted)">Idle (loads on first use)</div>';
      } else {
        fbEl.innerHTML = '<div style="color:var(--text-muted)">' + d.finbert.status + '</div>';
      }
    }
  } catch (e) {}
}

async function loadJournal() {
  try {
    var r = await fetch('/api/garves/journal');
    var d = await r.json();
    if (d.error) return;

    // Streak card
    var streakEl = document.getElementById('journal-streak');
    if (streakEl && d.streak_status) {
      var s = d.streak_status;
      var color = s.current > 0 ? 'var(--success)' : s.current < 0 ? 'var(--danger)' : 'var(--text-muted)';
      var icon = s.current > 0 ? '+' : '';
      streakEl.innerHTML = '<span style="color:' + color + '">' + icon + s.current + '</span>';
    }

    // Best combo card
    var bestEl = document.getElementById('journal-best-combo');
    if (bestEl && d.best_combos && d.best_combos.length > 0) {
      var b = d.best_combos[0];
      bestEl.innerHTML = '<span style="color:var(--success);font-size:0.72rem;">' + b.combo + '</span><br><span style="font-size:0.64rem;">' + b.win_rate + '% (' + b.total + ')</span>';
    }

    // Worst combo card
    var worstEl = document.getElementById('journal-worst-combo');
    if (worstEl && d.worst_combos && d.worst_combos.length > 0) {
      var w = d.worst_combos[0];
      worstEl.innerHTML = '<span style="color:var(--danger);font-size:0.72rem;">' + w.combo + '</span><br><span style="font-size:0.64rem;">' + w.win_rate + '% (' + w.total + ')</span>';
    }

    // Hour heatmap
    var hmEl = document.getElementById('journal-hour-heatmap');
    if (hmEl && d.hour_heatmap) {
      var cells = '';
      for (var h = 0; h < 24; h++) {
        var hd = d.hour_heatmap[String(h)] || {wins:0,losses:0,total:0,win_rate:0};
        var bg = 'rgba(128,128,128,0.2)';
        if (hd.total >= 3) {
          if (hd.win_rate >= 65) bg = 'rgba(0,255,136,0.35)';
          else if (hd.win_rate >= 50) bg = 'rgba(0,255,136,0.15)';
          else bg = 'rgba(255,68,68,0.25)';
        }
        cells += '<div style="background:' + bg + ';padding:3px 2px;border-radius:3px;text-align:center;" title="' + h + ':00 ET — ' + hd.wins + 'W/' + hd.losses + 'L (' + hd.win_rate + '%)">' + h + '<br><span style="font-size:0.56rem;">' + (hd.total > 0 ? hd.win_rate + '%' : '-') + '</span></div>';
      }
      hmEl.innerHTML = cells;
    }

    // Best combos table
    var bestTbody = document.getElementById('journal-best-tbody');
    if (bestTbody && d.best_combos) {
      if (d.best_combos.length === 0) {
        bestTbody.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center;">No data</td></tr>';
      } else {
        bestTbody.innerHTML = d.best_combos.map(function(c) {
          return '<tr><td>' + c.combo + '</td><td>' + c.wins + '-' + c.losses + '</td><td style="color:var(--success)">' + c.win_rate + '%</td></tr>';
        }).join('');
      }
    }

    // Worst combos table
    var worstTbody = document.getElementById('journal-worst-tbody');
    if (worstTbody && d.worst_combos) {
      if (d.worst_combos.length === 0) {
        worstTbody.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center;">No data</td></tr>';
      } else {
        worstTbody.innerHTML = d.worst_combos.map(function(c) {
          return '<tr><td>' + c.combo + '</td><td>' + c.wins + '-' + c.losses + '</td><td style="color:var(--danger)">' + c.win_rate + '%</td></tr>';
        }).join('');
      }
    }

    // Mistake patterns
    var mistakesEl = document.getElementById('journal-mistakes');
    if (mistakesEl && d.mistake_patterns) {
      if (d.mistake_patterns.length === 0) {
        mistakesEl.innerHTML = '<span style="color:var(--success)">No mistake patterns detected</span>';
      } else {
        mistakesEl.innerHTML = d.mistake_patterns.map(function(m) {
          return '<div style="margin-bottom:4px;"><span style="color:var(--warning);">' + m.pattern + '</span> <span class="text-muted">(' + m.count + 'x)</span> — ' + m.description + '</div>';
        }).join('');
      }
    }

    // Recommendations
    var recsEl = document.getElementById('journal-recommendations');
    if (recsEl && d.recommendations) {
      if (d.recommendations.length === 0) {
        recsEl.innerHTML = '<span class="text-muted">No recommendations at this time</span>';
      } else {
        recsEl.innerHTML = d.recommendations.map(function(r) {
          return '<div style="margin-bottom:3px;">&#8226; ' + r + '</div>';
        }).join('');
      }
    }
  } catch (e) {}
}

function freshnessBadge(f) {
  if (!f) return '';
  var colors = {green:'badge-success',yellow:'badge-warning',orange:'badge-warning',red:'badge-error'};
  var cls = colors[f.color] || 'badge-neutral';
  return '<span class="badge ' + cls + '" title="Freshness: ' + f.score + '/100">' + f.label + ' ' + f.score + '</span>';
}
function renderSoren(data) {
  document.getElementById('soren-queue-total').textContent = data.queue_total || 0;
  document.getElementById('soren-pending').textContent = data.pending || 0;
  document.getElementById('soren-posted').textContent = data.posted || 0;
  document.getElementById('soren-failed').textContent = data.failed || 0;
  // Freshness summary
  var fs = data.freshness || {};
  var fEl = document.getElementById('soren-freshness');
  if (fEl) {
    fEl.innerHTML = '<span style="font-size:0.75rem;color:var(--text-secondary);">Freshness: <strong>' + (fs.avg_score||0) + '</strong>/100 &nbsp; ' +
      '<span style="color:var(--success);">' + (fs.fresh||0) + ' fresh</span> · ' +
      '<span style="color:var(--warning);">' + (fs.ok||0) + ' ok</span> · ' +
      '<span style="color:#ff8800;">' + (fs.stale||0) + ' stale</span> · ' +
      '<span style="color:var(--error);">' + (fs.expired||0) + ' expired</span></span>';
  }
  // Use new filterable queue renderer
  renderSorenQueue(data.items || []);
}

async function sorenGenerate(itemId, mode) {
  try {
    await fetch('/api/soren/generate/' + itemId, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:mode||'full'})});
    refresh();
  } catch (e) { alert('Error: ' + e.message); }
}

async function sorenApprove(itemId) {
  await fetch('/api/soren/approve/' + itemId, {method:'POST'});
  refresh();
}

async function sorenReject(itemId) {
  await fetch('/api/soren/reject/' + itemId, {method:'POST'});
  refresh();
}

async function sorenBrandCheck(itemId, btn) {
  var origText = btn.textContent;
  btn.textContent = 'Reviewing...';
  btn.disabled = true;
  try {
    var resp = await fetch('/api/lisa/review/' + encodeURIComponent(itemId), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({platform: 'instagram'})
    });
    var data = await resp.json();
    if (data.error) { btn.textContent = 'Error'; btn.disabled = false; return; }
    var color = data.score >= 7 ? '#22aa44' : data.score >= 4 ? '#ffaa00' : '#ff4444';
    var label = data.score >= 7 ? 'PASS' : data.score >= 4 ? 'WARN' : 'FAIL';
    btn.innerHTML = '<span style="color:' + color + ';font-weight:600;">' + data.score + '/10 ' + label + '</span>';
    btn.disabled = false;
    if (data.issues && data.issues.length > 0) {
      var parent = btn.closest('.queue-item');
      if (parent) {
        var existing = parent.querySelector('.brand-review-detail');
        if (existing) existing.remove();
        var detail = document.createElement('div');
        detail.className = 'brand-review-detail';
        detail.style.cssText = 'font-size:0.72rem;color:var(--text-muted);padding:var(--space-2) var(--space-4);border-left:2px solid ' + color + ';margin-top:var(--space-2);';
        var dtxt = data.issues.join(' | ');
        if (data.suggested_fix) dtxt += '<br><span style="color:var(--agent-lisa);">Fix:</span> ' + esc(data.suggested_fix);
        detail.innerHTML = dtxt;
        parent.appendChild(detail);
      }
    }
  } catch (e) { btn.textContent = origText; btn.disabled = false; }
}

function sorenPreview(itemId) {
  var modal = document.getElementById('video-modal');
  var video = document.getElementById('modal-video');
  video.src = '/api/soren/preview/' + itemId + '?t=' + Date.now();
  document.getElementById('modal-download-btn').onclick = function() { sorenDownload(itemId); };
  modal.classList.add('active');
  video.load();
  video.play().catch(function(){});
}

function sorenDownload(itemId) {
  var a = document.createElement('a');
  a.href = '/api/soren/download/' + itemId;
  a.download = 'soren_reel_' + itemId + '.mp4';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function closeModal() {
  var modal = document.getElementById('video-modal');
  var video = document.getElementById('modal-video');
  video.pause(); video.src = '';
  modal.classList.remove('active');
}

// ── Shelby Task V2 ──
var _shelbyAllTasks = [];

function renderShelby(data) {
  var tasks = data.tasks || [];
  _shelbyAllTasks = tasks;
  var todayStr = new Date().toISOString().substring(0, 10);

  var highPri = 0, pending = 0, doneToday = 0, nextDue = null;
  for (var i = 0; i < tasks.length; i++) {
    var t = tasks[i], st = t.status || '';
    if (st !== 'done' && st !== 'archived' && (t.priority || 3) <= 2) highPri++;
    if (st === 'pending') pending++;
    if (st === 'done' && (t.completed || '').substring(0, 10) === todayStr) doneToday++;
    var tdue = t.due_at || t.due || '';
    if (st !== 'done' && st !== 'archived' && tdue && tdue.length >= 10 && (!nextDue || tdue < nextDue)) nextDue = tdue;
  }
  var e1 = document.getElementById('shelby-high-priority');
  var e2 = document.getElementById('shelby-tasks-pending');
  var e3 = document.getElementById('shelby-tasks-done-today');
  var e4 = document.getElementById('shelby-next-due');
  if (e1) e1.textContent = highPri;
  if (e2) e2.textContent = pending;
  if (e3) e3.textContent = doneToday;
  if (e4) e4.textContent = nextDue ? nextDue.substring(5, 10) : 'None';
  filterShelbyTasks();
}

function filterShelbyTasks() {
  var tasks = _shelbyAllTasks.slice();
  var fAgent = document.getElementById('shelby-filter-agent');
  var fStatus = document.getElementById('shelby-filter-status');
  var fSort = document.getElementById('shelby-filter-sort');
  var agentVal = fAgent ? fAgent.value : '';
  var statusVal = fStatus ? fStatus.value : '';
  var sortVal = fSort ? fSort.value : 'priority';

  if (agentVal) tasks = tasks.filter(function(t) { return (t.agent || '') === agentVal; });
  if (statusVal) tasks = tasks.filter(function(t) { return (t.status || '') === statusVal; });

  if (sortVal === 'priority') tasks.sort(function(a, b) { return (a.priority || 3) - (b.priority || 3); });
  else if (sortVal === 'due') tasks.sort(function(a, b) { return (a.due_at || a.due || 'zzzz').localeCompare(b.due_at || b.due || 'zzzz'); });
  else if (sortVal === 'agent') tasks.sort(function(a, b) { return (a.agent || '').localeCompare(b.agent || ''); });
  else if (sortVal === 'created') tasks.sort(function(a, b) { return (b.created_at || b.created || '').localeCompare(a.created_at || a.created || ''); });

  renderShelbyTaskCards(tasks);
  var cEl = document.getElementById('shelby-task-count');
  if (cEl) cEl.textContent = tasks.length + ' task' + (tasks.length !== 1 ? 's' : '');
}

function renderShelbyTaskCards(tasks) {
  var el = document.getElementById('shelby-task-list');
  if (!el) return;
  if (tasks.length === 0) { el.innerHTML = '<div class="text-muted" style="padding:20px;text-align:center;">No tasks match your filters.</div>'; return; }

  var catColors = {infrastructure:'#00d4ff',integration:'#a78bfa',content:'#f59e0b',trading:'#10b981',research:'#6366f1',ops:'#94a3b8'};
  var diffLabels = {1:'Easy',2:'Medium',3:'Hard'};
  var diffColors = {1:'#10b981',2:'#f59e0b',3:'#ef4444'};
  var statusNext = {pending:'in_progress',in_progress:'done',done:'pending'};
  var statusLabels = {pending:'Pending',in_progress:'In Progress',done:'Done'};
  var statusColors = {pending:'#94a3b8',in_progress:'#f59e0b',done:'#10b981'};
  var html = '';

  for (var i = 0; i < tasks.length; i++) {
    var t = tasks[i];
    var pri = t.priority || 3;
    var priLabels = {1:'P1',2:'P2',3:'P3',4:'P4'};
    var priColor = pri <= 1 ? '#ef4444' : pri <= 2 ? '#f59e0b' : pri <= 3 ? '#3b82f6' : '#64748b';
    var st = t.status || 'pending';
    var diff = t.difficulty || 2;
    var cat = t.category || 'ops';
    var agent = t.agent || '';
    var agentName = agent ? (AGENT_NAMES[agent] || agent.charAt(0).toUpperCase() + agent.slice(1)) : 'Unassigned';
    var agentColor = agent ? (AGENT_COLORS[agent] || 'var(--text)') : 'var(--text-muted)';
    var doneOpacity = st === 'done' ? 'opacity:0.45;' : '';
    var doneStrike = st === 'done' ? 'text-decoration:line-through;' : '';
    var dueStr = '';
    var tDue = t.due_at || t.due || '';
    if (tDue && tDue.length >= 10) {
      var today = new Date(); today.setHours(0,0,0,0);
      var dueDate = new Date(tDue.substring(0,10) + 'T00:00:00');
      var daysDiff = Math.round((dueDate - today) / 86400000);
      if (daysDiff < 0) dueStr = '<span style="color:#ef4444;font-weight:600;">Overdue ' + Math.abs(daysDiff) + 'd</span>';
      else if (daysDiff === 0) dueStr = '<span style="color:#f59e0b;font-weight:600;">Due today</span>';
      else if (daysDiff === 1) dueStr = '<span style="color:#f59e0b;">Tomorrow</span>';
      else if (daysDiff <= 7) dueStr = '<span style="color:var(--text-muted);">' + tDue.substring(5,10) + ' (' + daysDiff + 'd)</span>';
      else dueStr = '<span style="color:var(--text-muted);">' + tDue.substring(5,10) + '</span>';
    }

    // Left border = priority color
    html += '<div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-left:3px solid ' + priColor + ';border-bottom:1px solid rgba(255,255,255,0.04);' + doneOpacity + '">';

    // Priority badge
    html += '<div style="min-width:36px;text-align:center;"><span style="background:' + priColor + '22;color:' + priColor + ';padding:3px 8px;border-radius:4px;font-weight:700;font-size:0.76rem;">' + (priLabels[pri] || 'P3') + '</span></div>';

    // Main content
    html += '<div style="flex:1;min-width:0;">';
    html += '<div style="font-size:0.82rem;font-weight:500;' + doneStrike + 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(t.title || '') + '">' + esc(t.title || '') + '</div>';
    // Meta row: agent, category, difficulty, due
    html += '<div style="display:flex;gap:8px;align-items:center;margin-top:3px;flex-wrap:wrap;">';
    html += '<span style="color:' + agentColor + ';font-size:0.72rem;font-weight:600;">' + esc(agentName) + '</span>';
    html += '<span style="background:' + (catColors[cat] || '#94a3b8') + '18;color:' + (catColors[cat] || '#94a3b8') + ';padding:1px 6px;border-radius:3px;font-size:0.68rem;">' + esc(cat) + '</span>';
    if (t.source && t.source !== 'manual') html += '<span style="color:#6366f1;font-size:0.68rem;">' + esc(t.source) + '</span>';
    if (dueStr) html += dueStr;
    html += '</div>';
    // Notes preview (dispatch results)
    var desc = t.description || t.notes || '';
    if (desc) {
      var descPreview = desc.length > 120 ? desc.substring(0, 117) + '...' : desc;
      var descBg = st === 'done' ? 'rgba(16,185,129,0.08)' : 'rgba(59,130,246,0.08)';
      var descColor = st === 'done' ? '#10b981' : '#3b82f6';
      html += '<div style="margin-top:4px;padding:3px 8px;background:' + descBg + ';border-radius:4px;font-size:0.68rem;color:' + descColor + ';line-height:1.4;cursor:pointer;white-space:pre-line;" onclick="this.textContent=this.dataset.full||this.textContent" data-full="' + esc(desc) + '">' + esc(descPreview) + '</div>';
    }
    html += '</div>';

    // Status toggle
    var stLabel = statusLabels[st];
    if (st === 'in_progress' && (t.notes || '').indexOf('Dispatching') === 0) stLabel = 'Running...';
    html += '<span style="cursor:pointer;background:' + statusColors[st] + '18;color:' + statusColors[st] + ';padding:4px 10px;border-radius:4px;font-size:0.72rem;font-weight:600;white-space:nowrap;user-select:none;" onclick="updateShelbyTaskStatus(' + t.id + ',\'' + (statusNext[st] || 'pending') + '\')" title="Click to change status">' + stLabel + '</span>';

    // Run (dispatch) button — show for pending/in_progress tasks assigned to dispatchable agents
    var dispatchAgents = ['robotox','atlas','thor'];
    if (st !== 'done' && dispatchAgents.indexOf(agent) !== -1) {
      html += '<button id="dispatch-btn-' + t.id + '" style="background:none;border:1px solid rgba(255,255,255,0.15);color:#3b82f6;cursor:pointer;font-size:0.72rem;padding:3px 7px;border-radius:4px;line-height:1;" onclick="manualDispatchTask(' + t.id + ')" title="Run / Dispatch task">&#9654;</button>';
    }

    // Delete
    html += '<button style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:0.8rem;padding:4px 6px;opacity:0.5;" onclick="deleteShelbyTask(' + t.id + ')" title="Delete task">&times;</button>';

    html += '</div>';
  }
  el.innerHTML = html;
}

// ── Shelby Task V2: CRUD ──

function toggleShelbyAdvanced() {
  var el = document.getElementById('shelby-add-advanced');
  var btn = document.getElementById('shelby-advanced-toggle');
  if (!el) return;
  if (el.style.display === 'none') {
    el.style.display = 'flex';
    if (btn) btn.textContent = '- Hide options';
  } else {
    el.style.display = 'none';
    if (btn) btn.textContent = '+ More options (difficulty, impact, category)';
  }
}

function setShelbyDiff(val, btn) {
  document.getElementById('shelby-add-diff').value = val;
  document.querySelectorAll('.shelby-diff-btn').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
}

function setShelbyBen(val, btn) {
  document.getElementById('shelby-add-ben').value = val;
  document.querySelectorAll('.shelby-ben-btn').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
}

async function addShelbyTask() {
  var titleEl = document.getElementById('shelby-add-title');
  var title = titleEl ? titleEl.value.trim() : '';
  if (!title) { if (titleEl) titleEl.focus(); return; }
  var agentEl = document.getElementById('shelby-add-agent');
  var catEl = document.getElementById('shelby-add-category');
  var dueEl = document.getElementById('shelby-add-due');
  var body = {
    title: title,
    agent: agentEl ? agentEl.value || undefined : undefined,
    category: catEl ? catEl.value || undefined : undefined,
    difficulty: parseInt(document.getElementById('shelby-add-diff').value) || 2,
    benefit: parseInt(document.getElementById('shelby-add-ben').value) || 3,
    due: dueEl ? dueEl.value || undefined : undefined
  };
  try {
    var resp = await fetch('/api/shelby/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    var data = await resp.json();
    if (data.success) {
      if (titleEl) titleEl.value = '';
      if (dueEl) dueEl.value = '';
      var sr = await fetch('/api/shelby');
      renderShelby(await sr.json());
    } else {
      alert('Error: ' + (data.error || 'Unknown'));
    }
  } catch (e) { alert('Error: ' + e.message); }
}

async function updateShelbyTaskStatus(id, newStatus) {
  try {
    var resp = await fetch('/api/shelby/tasks/' + id, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status: newStatus})});
    var data = await resp.json();
    if (data.success) {
      var sr = await fetch('/api/shelby');
      renderShelby(await sr.json());
    }
  } catch (e) { console.error(e); }
}

async function deleteShelbyTask(id) {
  if (!confirm('Delete task #' + id + '?')) return;
  try {
    var resp = await fetch('/api/shelby/tasks/' + id, {method:'DELETE'});
    var data = await resp.json();
    if (data.success) {
      var sr = await fetch('/api/shelby');
      renderShelby(await sr.json());
    }
  } catch (e) { console.error(e); }
}

async function triggerScheduledDispatch(routine) {
  var resultEl = document.getElementById('dispatch-schedule-result');
  if (resultEl) { resultEl.style.display = 'block'; resultEl.textContent = 'Dispatching...'; }
  try {
    var resp = await fetch('/api/shelby/schedule/dispatch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({routine: routine})});
    var data = await resp.json();
    if (data.success) {
      if (resultEl) resultEl.textContent = routine.replace('dispatch_','').replace(/^\w/,function(c){return c.toUpperCase();}) + ': ' + data.result;
      setTimeout(async function() { var sr = await fetch('/api/shelby'); renderShelby(await sr.json()); }, 2000);
    } else {
      if (resultEl) resultEl.textContent = 'Error: ' + (data.error || 'Unknown');
    }
  } catch (e) { if (resultEl) resultEl.textContent = 'Error: ' + e.message; }
}

async function manualDispatchTask(id) {
  try {
    var btn = document.getElementById('dispatch-btn-' + id);
    if (btn) { btn.disabled = true; btn.textContent = '...'; }
    var resp = await fetch('/api/shelby/tasks/' + id + '/dispatch', {method:'POST'});
    var data = await resp.json();
    if (data.success) {
      var sr = await fetch('/api/shelby');
      renderShelby(await sr.json());
    } else {
      alert(data.error || 'Cannot dispatch this task');
      if (btn) { btn.disabled = false; btn.textContent = '\u25B6'; }
    }
  } catch (e) { console.error(e); if (btn) { btn.disabled = false; btn.textContent = '\u25B6'; } }
}

async function reprioritizeTasks() {
  try {
    await fetch('/api/shelby/tasks/prioritize', {method:'POST'});
    var sr = await fetch('/api/shelby');
    renderShelby(await sr.json());
  } catch (e) { console.error(e); }
}

async function loadSchedule() {
  try {
    var resp = await fetch('/api/shelby/schedule');
    var data = await resp.json();
    var schedule = data.schedule || {};
    var el = document.getElementById('shelby-schedule');
    var keys = Object.keys(schedule).sort();
    if (keys.length === 0) { el.innerHTML = '<div class="text-muted" style="padding:var(--space-4);">No schedule data.</div>'; return; }
    var html = '';
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      var s = schedule[k];
      var done = s.completed;
      var safeK = k.replace(/:/g, '-');
      html += '<div>';
      html += '<div class="schedule-slot" style="' + (done ? 'border-left:3px solid var(--success);' : '') + '">';
      html += '<span class="schedule-time">' + esc(k) + '</span>';
      html += '<span class="schedule-desc" style="flex:1;">' + esc(s.name || k);
      if (done) html += ' <span class="badge badge-success" style="margin-left:8px;">Done</span>';
      html += '</span>';
      if (done && s.result) {
        html += '<button class="btn" style="font-size:0.68rem;" onclick="toggleScheduleDetail(&apos;sched-' + safeK + '&apos;)">View Details</button>';
      }
      html += '</div>';
      if (done && s.result) {
        html += '<div class="schedule-detail" id="sched-' + safeK + '">' + esc(s.result) + '</div>';
      }
      html += '</div>';
    }
    el.innerHTML = html;
    // Also update next routine (avoids duplicate /api/shelby/schedule call)
    var currentTime = data.current_time || '';
    var nextEl = document.getElementById('shelby-next-routine');
    if (nextEl) {
      var nextRoutine = null;
      for (var j = 0; j < keys.length; j++) {
        if (!schedule[keys[j]].completed && keys[j] >= currentTime) {
          nextRoutine = keys[j] + ' ' + (schedule[keys[j]].name || '');
          break;
        }
      }
      if (nextRoutine) { nextEl.textContent = nextRoutine.split(' ')[0]; nextEl.title = nextRoutine; }
      else { nextEl.textContent = 'All done'; }
    }
  } catch (e) {}
}

var _atlasStartTime = null;
var _atlasUptimeInterval = null;
function formatUptime(ms) {
  var s = Math.floor(ms / 1000);
  var d = Math.floor(s / 86400); s %= 86400;
  var h = Math.floor(s / 3600); s %= 3600;
  var m = Math.floor(s / 60); s %= 60;
  if (d > 0) return d + 'd ' + h + 'h ' + m + 'm ' + s + 's';
  if (h > 0) return h + 'h ' + m + 'm ' + s + 's';
  return m + 'm ' + s + 's';
}
function tickAtlasUptime() {
  if (!_atlasStartTime) return;
  var el = document.getElementById('atlas-uptime');
  if (el) {
    el.textContent = formatUptime(Date.now() - _atlasStartTime);
    el.style.color = 'var(--success)';
  }
}
function renderAtlas(data) {
  if (data.error || data.status === 'offline') {
    document.getElementById('atlas-uptime').textContent = 'Offline';
    document.getElementById('atlas-uptime').style.color = 'var(--error)';
    _atlasStartTime = null;
    return;
  }
  var brain = data.brain || {};
  var bg = data.background || {};
  // Set start time from server data or use page load as fallback
  if (!_atlasStartTime && bg.started_at) {
    _atlasStartTime = new Date(bg.started_at).getTime();
  } else if (!_atlasStartTime) {
    _atlasStartTime = Date.now();
  }
  // Start the 1-second tick if not already running
  if (!_atlasUptimeInterval) {
    _atlasUptimeInterval = setInterval(tickAtlasUptime, 1000);
    tickAtlasUptime();
  }
  // Live metrics cards
  var lm = data.live_metrics || {};
  var agentsFedEl = document.getElementById('atlas-agents-fed');
  if (agentsFedEl) {
    var fed = lm.agents_fed || 0;
    var total = lm.agents_total || 9;
    agentsFedEl.textContent = fed + '/' + total;
    agentsFedEl.style.color = fed >= 7 ? 'var(--success)' : fed >= 4 ? 'var(--warning)' : 'var(--error)';
  }
  var kgEl = document.getElementById('atlas-knowledge-growth');
  if (kgEl) {
    var growth = lm.knowledge_growth || 0;
    kgEl.textContent = '+' + growth + ' today';
    kgEl.style.color = growth > 0 ? 'var(--success)' : 'var(--text-secondary)';
  }
  var anomEl = document.getElementById('atlas-anomalies');
  if (anomEl) {
    var anoms = lm.anomalies || 0;
    anomEl.textContent = anoms;
    anomEl.style.color = anoms > 0 ? 'var(--warning)' : 'var(--success)';
  }
  var ebEl = document.getElementById('atlas-event-bus');
  if (ebEl) {
    var evts = lm.event_bus_events || 0;
    ebEl.textContent = evts + ' events';
    ebEl.style.color = evts > 0 ? 'var(--success)' : 'var(--text-secondary)';
  }
  // Latest insight bar
  var insightBar = document.getElementById('atlas-latest-insight');
  var insightText = document.getElementById('atlas-insight-text');
  if (insightBar && insightText && lm.latest_insight && lm.latest_insight.text) {
    var li = lm.latest_insight;
    var agentName = (AGENT_NAMES[li.agent] || li.agent || 'Atlas');
    var confPct = Math.round((li.confidence || 0) * 100);
    insightText.innerHTML = '<span style="color:' + (AGENT_COLORS[li.agent] || 'var(--agent-atlas)') + ';font-weight:600;">' + esc(agentName) + '</span> <span style="color:var(--text-muted);font-size:0.66rem;">(' + confPct + '% confidence)</span><br>' + esc(li.text);
    insightBar.style.display = 'block';
  } else if (insightBar) {
    insightBar.style.display = 'none';
  }
  // Learning badge — show per-agent breakdown
  var lBadge = document.getElementById('atlas-learning');
  if (lBadge && data.recent_learnings) {
    var agentCounts = {};
    (data.recent_learnings || []).forEach(function(l) {
      var a = l.agent || 'atlas';
      agentCounts[a] = (agentCounts[a] || 0) + 1;
    });
    var parts = [];
    for (var a in agentCounts) {
      var name = {garves:'Garves',soren:'Soren',shelby:'Shelby',lisa:'Lisa',atlas:'Atlas'}[a] || a;
      parts.push(name + ': ' + agentCounts[a]);
    }
    var level = (brain.learnings||0) >= 50 ? 'Expert' : (brain.learnings||0) >= 20 ? 'Advanced' : (brain.learnings||0) >= 5 ? 'Learning' : 'Novice';
    lBadge.innerHTML = '<span class="wb-label">Learning:</span> <span style="color:var(--success);">' + level + '</span> ' + (brain.learnings||0) + ' lessons' + (parts.length ? ' (' + parts.join(', ') + ')' : '') + ' | ' + (brain.observations||0) + ' obs';
  }
  // Research quality score
  var research = data.research || {};
  var qEl = document.getElementById('atlas-quality-score');
  if (qEl) {
    var qs = research.avg_quality_score || 0;
    qEl.textContent = qs > 0 ? qs + '/10' : '--';
    qEl.style.color = qs >= 8 ? 'var(--success)' : qs >= 6 ? 'var(--warning)' : qs > 0 ? 'var(--error)' : 'var(--text-secondary)';
  }
  // API Budget stat card
  var apiBudgetEl = document.getElementById('atlas-api-budget');
  if (apiBudgetEl && data.costs) {
    var todayCalls = (data.costs.today_tavily || 0) + (data.costs.today_openai || 0);
    apiBudgetEl.textContent = todayCalls + ' calls';
    apiBudgetEl.style.color = todayCalls > 100 ? 'var(--warning)' : 'var(--success)';
  }
}

async function loadAtlasBgStatus() {
  var el = document.getElementById('atlas-bg-status');
  if (!el) return;
  try {
    var resp = await fetch('/api/atlas/background/status');
    var d = await resp.json();
    var running = d.running;
    var stateColor = running ? (d.state === 'running' ? 'var(--success)' : 'var(--agent-atlas)') : 'var(--error)';
    var dotCls = running ? (d.state === 'running' ? 'online' : 'idle') : 'offline';

    var html = '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">';
    html += '<span class="status-dot ' + dotCls + '"></span>';
    html += '<span style="font-family:var(--font-heading);font-size:0.82rem;font-weight:600;color:' + stateColor + ';">' + esc(d.state_label || d.state || 'Unknown') + '</span>';
    if (running && d.state !== 'running') {
      html += '<span class="badge badge-info" style="animation:pulse-glow 1.5s ease-in-out infinite;">Working</span>';
    }
    html += '</div>';

    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;">';

    html += '<div style="padding:6px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;">';
    html += '<div style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;">Cycles</div>';
    html += '<div style="font-family:var(--font-mono);font-size:1rem;font-weight:600;color:var(--agent-atlas);">' + (d.cycles || 0) + '</div></div>';

    html += '<div style="padding:6px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;">';
    html += '<div style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;">Researches</div>';
    html += '<div style="font-family:var(--font-mono);font-size:1rem;font-weight:600;color:var(--agent-atlas);">' + (d.total_researches || 0) + '</div></div>';

    html += '<div style="padding:6px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;">';
    html += '<div style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;">URLs Seen</div>';
    html += '<div style="font-family:var(--font-mono);font-size:1rem;font-weight:600;color:var(--agent-atlas);">' + (d.unique_urls || 0) + '</div></div>';

    html += '<div style="padding:6px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;">';
    html += '<div style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;">Last Findings</div>';
    html += '<div style="font-family:var(--font-mono);font-size:1rem;font-weight:600;color:var(--agent-atlas);">' + (d.last_findings || 0) + '</div></div>';

    html += '</div>';

    if (d.last_cycle) {
      var ago = Math.round((Date.now() - new Date(d.last_cycle).getTime()) / 60000);
      html += '<div style="margin-top:8px;font-size:0.72rem;color:var(--text-muted);">Last cycle: ' + ago + ' min ago';
      if (d.started_at) {
        var startedAgo = Math.round((Date.now() - new Date(d.started_at).getTime()) / 3600000);
        html += ' &middot; Running for ' + (startedAgo >= 1 ? startedAgo + 'h' : '<1h');
      }
      html += '</div>';
    }
    // Cycle countdown — works even before first cycle completes
    if (running && d.cycle_minutes) {
      var cyclMs = d.cycle_minutes * 60000;
      var isWorking = d.state !== 'running' && d.state !== 'idle' && d.state !== 'stopped';
      if (isWorking) {
        html += '<div style="margin-top:6px;font-size:0.78rem;color:var(--agent-atlas);font-family:var(--font-mono);display:flex;align-items:center;gap:6px;">';
        html += '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--agent-atlas);box-shadow:0 0 8px var(--agent-atlas);animation:pulse-glow 1.5s ease-in-out infinite;"></span>';
        html += 'Working: ' + esc(d.state_label || d.state) + '</div>';
      } else if (d.last_cycle) {
        var nextAt = new Date(d.last_cycle).getTime() + cyclMs;
        var remain = nextAt - Date.now();
        if (remain > 0) {
          var rm = Math.floor(remain / 60000);
          var rs = Math.floor((remain % 60000) / 1000);
          html += '<div id="atlas-countdown-timer" data-next="' + nextAt + '" style="margin-top:6px;font-size:0.78rem;color:var(--agent-atlas);font-family:var(--font-mono);display:flex;align-items:center;gap:6px;">';
          html += '<svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="6" fill="none" stroke="var(--agent-atlas)" stroke-width="1.5" opacity="0.3"/><circle cx="7" cy="7" r="6" fill="none" stroke="var(--agent-atlas)" stroke-width="1.5" stroke-dasharray="37.7" stroke-dashoffset="' + (37.7 * remain / cyclMs) + '" transform="rotate(-90 7 7)" stroke-linecap="round"/></svg>';
          html += 'Next cycle in: <strong>' + rm + 'm ' + rs + 's</strong></div>';
        } else {
          html += '<div style="margin-top:6px;font-size:0.78rem;color:var(--success);font-family:var(--font-mono);">Cycle starting soon...</div>';
        }
      }
    }

    if (d.last_error) {
      html += '<div style="margin-top:6px;font-size:0.72rem;color:var(--error);padding:4px 8px;background:rgba(255,68,68,0.06);border-radius:4px;">Last error: ' + esc(d.last_error) + '</div>';
    }

    html += '<div style="margin-top:8px;display:flex;gap:6px;">';
    if (!running) {
      html += '<button class="btn btn-success" onclick="atlasStartBg()" style="font-size:0.7rem;">Start Background</button>';
    } else {
      html += '<button class="btn btn-error" onclick="atlasStopBg()" style="font-size:0.7rem;">Stop Background</button>';
    }
    html += '</div>';

    el.innerHTML = html;
    _atlasBgCache = d;
    renderAtlasHierarchy(d);
    updateOverviewCountdown();
  } catch (e) {
    el.innerHTML = '<div class="text-muted" style="padding:8px;">Background status unavailable</div>';
  }
}

function renderAtlasHierarchy(bgStatus) {
  var el = document.getElementById('atlas-hierarchy');
  if (!el) return;
  var running = bgStatus.running;
  var state = bgStatus.state || 'idle';
  var target = bgStatus.current_target;
  var learnCount = bgStatus.recent_learn_count || 0;
  var stateLabel = bgStatus.state_label || state;
  var feedLog = bgStatus.agent_feed_log || {};

  var agents = [
    {key:'garves', name:'Garves', color:'#00d4ff', x:60,  y:30},
    {key:'soren',  name:'Soren',  color:'#cc66ff', x:175, y:30},
    {key:'shelby', name:'Shelby', color:'#ffaa00', x:290, y:30},
    {key:'lisa',   name:'Lisa',   color:'#ff8800', x:405, y:30},
    {key:'robotox',name:'Robotox',color:'#00ff44', x:520, y:30},
    {key:'thor',   name:'Thor',   color:'#ff6600', x:635, y:30}
  ];
  var atlasX = 350, atlasY = 150;
  var w = 700, h = 210;

  var html = '<div class="hierarchy-header">';
  html += '<span class="hierarchy-title">Live Feed Hierarchy</span>';
  if (running && (state === 'learning' || learnCount > 0)) {
    html += '<span class="learn-pulse"><span class="learn-pulse-dot"></span>Learning (' + learnCount + ' new)</span>';
  }
  if (running && state !== 'running' && state !== 'idle') {
    html += '<span style="font-family:var(--font-mono);font-size:0.7rem;color:var(--agent-atlas);margin-left:auto;">' + esc(stateLabel) + '</span>';
  }
  html += '</div>';

  var svg = '<svg viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;max-height:210px;">';

  // Draw lines from Atlas to each agent
  for (var i = 0; i < agents.length; i++) {
    var ag = agents[i];
    var isActive = target === ag.key || target === 'all';
    var hasFed = feedLog[ag.key] && feedLog[ag.key].feed_count > 0;
    var lineColor = isActive ? '#22cc55' : hasFed ? ag.color + '33' : 'rgba(255,255,255,0.06)';
    var lineWidth = isActive ? 2.5 : hasFed ? 1.5 : 1;
    var dashAttr = isActive ? ' stroke-dasharray="8 4" style="animation:hierarchy-flow 0.8s linear infinite;"' : '';
    svg += '<line x1="' + atlasX + '" y1="' + atlasY + '" x2="' + ag.x + '" y2="' + (ag.y + 16) + '" stroke="' + lineColor + '" stroke-width="' + lineWidth + '"' + dashAttr + '/>';
    if (isActive) {
      svg += '<circle cx="' + ag.x + '" cy="' + (ag.y + 16) + '" r="4" fill="#22cc55" opacity="0.6"><animate attributeName="r" values="3;6;3" dur="1.5s" repeatCount="indefinite"/><animate attributeName="opacity" values="0.8;0.3;0.8" dur="1.5s" repeatCount="indefinite"/></circle>';
    }
  }

  // Draw agent nodes
  for (var i = 0; i < agents.length; i++) {
    var ag = agents[i];
    var isActive = target === ag.key || target === 'all';
    var hasFed = feedLog[ag.key] && feedLog[ag.key].feed_count > 0;
    var opacity = isActive ? '1' : hasFed ? '0.8' : '0.35';
    var glow = isActive ? ' filter="url(#glow-' + ag.key + ')"' : '';

    if (isActive) {
      svg += '<defs><filter id="glow-' + ag.key + '"><feGaussianBlur stdDeviation="4" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';
    }

    svg += '<g opacity="' + opacity + '"' + glow + '>';
    svg += '<circle cx="' + ag.x + '" cy="' + ag.y + '" r="16" fill="rgba(8,8,16,0.9)" stroke="' + ag.color + '" stroke-width="' + (isActive ? 2.5 : 1) + '"/>';
    svg += '<text x="' + ag.x + '" y="' + (ag.y + 4) + '" text-anchor="middle" fill="' + ag.color + '" font-size="9" font-family="\'JetBrains Mono\',monospace" font-weight="600">' + ag.name.substring(0,2).toUpperCase() + '</text>';
    svg += '<text x="' + ag.x + '" y="' + (ag.y - 22) + '" text-anchor="middle" fill="' + ag.color + '" font-size="8" font-family="Inter,sans-serif" opacity="0.8">' + ag.name + '</text>';

    // Feed status label under node
    var agFeed = feedLog[ag.key];
    if (isActive) {
      svg += '<text x="' + ag.x + '" y="' + (ag.y + 32) + '" text-anchor="middle" fill="#22cc55" font-size="6.5" font-family="\'JetBrains Mono\',monospace" font-weight="600">FEEDING</text>';
    } else if (agFeed && agFeed.last_fed) {
      var feedTime = agFeed.last_fed.substring(11, 16);
      svg += '<text x="' + ag.x + '" y="' + (ag.y + 30) + '" text-anchor="middle" fill="' + ag.color + '" font-size="6" font-family="\'JetBrains Mono\',monospace" opacity="0.6">' + feedTime + '</text>';
      svg += '<text x="' + ag.x + '" y="' + (ag.y + 39) + '" text-anchor="middle" fill="rgba(255,255,255,0.3)" font-size="5.5" font-family="Inter,sans-serif">' + agFeed.feed_count + 'x fed</text>';
    } else {
      svg += '<text x="' + ag.x + '" y="' + (ag.y + 32) + '" text-anchor="middle" fill="rgba(255,255,255,0.2)" font-size="6" font-family="Inter,sans-serif">unfed</text>';
    }

    svg += '</g>';
  }

  // Draw Atlas node (center, larger)
  var atlasGlow = running && state !== 'running' && state !== 'idle' ? ' filter="url(#glow-atlas)"' : '';
  if (running && state !== 'running' && state !== 'idle') {
    svg += '<defs><filter id="glow-atlas"><feGaussianBlur stdDeviation="6" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';
  }
  svg += '<g' + atlasGlow + '>';
  svg += '<circle cx="' + atlasX + '" cy="' + atlasY + '" r="26" fill="rgba(8,8,16,0.95)" stroke="#22cc55" stroke-width="2"/>';
  if (running && state !== 'running' && state !== 'idle') {
    svg += '<circle cx="' + atlasX + '" cy="' + atlasY + '" r="26" fill="none" stroke="#22cc55" stroke-width="1" opacity="0.3"><animate attributeName="r" values="26;34;26" dur="2s" repeatCount="indefinite"/><animate attributeName="opacity" values="0.3;0;0.3" dur="2s" repeatCount="indefinite"/></circle>';
  }
  svg += '<text x="' + atlasX + '" y="' + (atlasY + 1) + '" text-anchor="middle" fill="#22cc55" font-size="10" font-family="\'Exo 2\',sans-serif" font-weight="700" letter-spacing="1">ATLAS</text>';
  var atlasSubLabel = running ? (target ? 'FEEDING → ' + (target === 'all' ? 'ALL' : target.toUpperCase()) : 'ONLINE') : 'OFFLINE';
  svg += '<text x="' + atlasX + '" y="' + (atlasY + 13) + '" text-anchor="middle" fill="rgba(255,255,255,0.4)" font-size="7" font-family="\'JetBrains Mono\',monospace">' + atlasSubLabel + '</text>';
  svg += '</g>';

  svg += '</svg>';
  html += svg;
  el.innerHTML = html;
}

async function atlasStartBg() {
  try { await fetch('/api/atlas/background/start', {method:'POST'}); loadAtlasBgStatus(); } catch(e) {}
}

async function atlasStopBg() {
  try { await fetch('/api/atlas/background/stop', {method:'POST'}); loadAtlasBgStatus(); } catch(e) {}
}

function tickAtlasCountdown() {
  // Atlas tab countdown
  var timer = document.getElementById('atlas-countdown-timer');
  if (timer) {
    var nextAt = parseInt(timer.getAttribute('data-next'));
    var remain = nextAt - Date.now();
    if (remain > 0) {
      var rm = Math.floor(remain / 60000);
      var rs = Math.floor((remain % 60000) / 1000);
      var cyclMs = (_atlasBgCache && _atlasBgCache.cycle_minutes ? _atlasBgCache.cycle_minutes * 60000 : 2700000);
      var svgOffset = (37.7 * remain / cyclMs);
      timer.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="6" fill="none" stroke="var(--agent-atlas)" stroke-width="1.5" opacity="0.3"/><circle cx="7" cy="7" r="6" fill="none" stroke="var(--agent-atlas)" stroke-width="1.5" stroke-dasharray="37.7" stroke-dashoffset="' + svgOffset + '" transform="rotate(-90 7 7)" stroke-linecap="round"/></svg>Next cycle in: <strong>' + rm + 'm ' + rs + 's</strong>';
    } else {
      timer.innerHTML = '<span style="color:var(--success);">Cycle starting soon...</span>';
    }
  }
  // Overview tab countdown
  updateOverviewCountdown();
}

function updateOverviewCountdown() {
  var el = document.getElementById('overview-atlas-countdown');
  if (!el) return;
  var d = _atlasBgCache;
  if (!d || !d.running || !d.cycle_minutes) {
    el.innerHTML = '';
    return;
  }
  var cyclMs = d.cycle_minutes * 60000;
  var isWorking = d.state !== 'running' && d.state !== 'idle' && d.state !== 'stopped';
  if (isWorking) {
    el.innerHTML = '<div class="atlas-cycle-badge working"><span class="atlas-cycle-dot"></span>Atlas working: ' + esc(d.state_label || d.state) + '</div>';
  } else if (d.last_cycle) {
    var nextAt = new Date(d.last_cycle).getTime() + cyclMs;
    var remain = nextAt - Date.now();
    if (remain > 0) {
      var rm = Math.floor(remain / 60000);
      var rs = Math.floor((remain % 60000) / 1000);
      el.innerHTML = '<div class="atlas-cycle-badge idle"><svg width="14" height="14" viewBox="0 0 14 14" style="flex-shrink:0;"><circle cx="7" cy="7" r="6" fill="none" stroke="var(--agent-atlas)" stroke-width="1.5" opacity="0.3"/><circle cx="7" cy="7" r="6" fill="none" stroke="var(--agent-atlas)" stroke-width="1.5" stroke-dasharray="37.7" stroke-dashoffset="' + (37.7 * remain / cyclMs) + '" transform="rotate(-90 7 7)" stroke-linecap="round"/></svg>Next Atlas cycle: <strong>' + rm + 'm ' + rs + 's</strong></div>';
    } else {
      el.innerHTML = '<div class="atlas-cycle-badge starting">Atlas cycle starting soon...</div>';
    }
  } else {
    el.innerHTML = '';
  }
}

setInterval(tickAtlasCountdown, 1000);

async function atlasLiveResearch() {
  var el = document.getElementById('atlas-live-research');
  if (el.style.display !== 'none') { el.style.display = 'none'; return; }
  el.style.display = 'block';
  el.innerHTML = '<div style="color:var(--text-secondary);">Loading live research...</div>';
  try {
    var resp = await fetch('/api/atlas/live-research');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div style="color:var(--error);">' + data.error + '</div>'; return; }
    var articles = data.articles || [];
    var html = '<div style="margin-bottom:8px;font-weight:600;color:var(--agent-atlas);">Atlas Live Research (' + data.total_researched + ' total | ' + data.seen_urls + ' unique URLs)</div>';
    if (articles.length === 0) {
      html += '<div style="color:var(--text-secondary);">No research yet this session. Atlas researches every 45 min.</div>';
    } else {
      var agentColors = {garves:'var(--agent-garves)',soren:'var(--agent-soren)',shelby:'var(--agent-shelby)',lisa:'var(--agent-lisa)',atlas:'var(--agent-atlas)'};
      articles.forEach(function(a) {
        var agentName = {garves:'Garves',soren:'Soren',shelby:'Shelby',lisa:'Lisa',atlas:'Atlas'}[a.agent] || a.agent;
        var color = agentColors[a.agent] || 'var(--text-primary)';
        html += '<div style="padding:8px 0;border-bottom:1px solid var(--border);">';
        html += '<div style="display:flex;gap:8px;align-items:center;">';
        html += '<span style="color:' + color + ';font-weight:600;font-size:0.8rem;">[' + agentName + ']</span>';
        html += '<span style="font-size:0.75rem;color:var(--text-secondary);">' + (a.query||'') + '</span>';
        if (a.quality) html += '<span style="font-size:0.7rem;color:' + (a.quality >= 8 ? 'var(--success)' : a.quality >= 6 ? 'var(--warning)' : 'var(--error)') + ';">' + a.quality + '/10</span>';
        html += '</div>';
        if (a.url) html += '<a href="' + a.url + '" target="_blank" style="font-size:0.8rem;color:var(--agent-atlas);word-break:break-all;">' + (a.source || a.url) + '</a>';
        if (a.insight) html += '<div style="font-size:0.8rem;color:var(--text-secondary);margin-top:2px;">' + a.insight + '</div>';
        html += '</div>';
      });
    }
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<div style="color:var(--error);">Failed to load: ' + e.message + '</div>';
  }
}

async function atlasDeepResearch() {
  var agentEl = document.getElementById('atlas-deep-agent');
  var queryEl = document.getElementById('atlas-deep-query');
  var resultEl = document.getElementById('atlas-deep-result');
  if (!queryEl || !resultEl) return;
  var query = queryEl.value.trim();
  if (!query) { alert('Enter a research topic'); return; }
  var agent = agentEl ? agentEl.value : '';
  resultEl.style.display = 'block';
  resultEl.innerHTML = '<div style="color:var(--agent-atlas);">Researching: ' + esc(query) + '...</div>';
  try {
    var resp = await fetch('/api/atlas/deep-research', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:agent,query:query})});
    var data = await resp.json();
    if (data.error) { resultEl.innerHTML = '<div style="color:var(--error);">' + esc(data.error) + '</div>'; return; }
    var results = data.results || [];
    var html = '<div style="font-weight:600;color:var(--agent-atlas);margin-bottom:8px;">Research Results (' + results.length + ' findings)</div>';
    if (!results.length) {
      html += '<div class="text-muted">No results found. Try a different query.</div>';
    } else {
      results.forEach(function(r) {
        html += '<div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">';
        if (r.error) { html += '<div style="color:var(--error);">' + esc(r.error) + '</div>'; }
        else {
          if (r.title || r.query) html += '<div style="font-weight:600;font-size:0.78rem;color:var(--text-primary);">' + esc(r.title || r.query || '') + '</div>';
          if (r.insight || r.summary || r.content) html += '<div style="font-size:0.76rem;color:var(--text-secondary);margin-top:3px;line-height:1.5;">' + esc((r.insight || r.summary || r.content || '').substring(0, 400)) + '</div>';
          if (r.url || r.source) html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">' + esc(r.source || r.url || '') + '</div>';
        }
        html += '</div>';
      });
    }
    resultEl.innerHTML = html;
    // Also show in report area
    var reportEl = document.getElementById('atlas-report');
    if (reportEl) reportEl.innerHTML = html;
  } catch(e) { resultEl.innerHTML = '<div style="color:var(--error);">Research failed: ' + esc(e.message) + '</div>'; }
}

async function atlasKBSearch() {
  var queryEl = document.getElementById('atlas-kb-search-query');
  var resultEl = document.getElementById('atlas-kb-search-results');
  if (!queryEl || !resultEl) return;
  var q = queryEl.value.trim();
  if (!q) { alert('Enter a search query'); return; }
  resultEl.style.display = 'block';
  resultEl.innerHTML = '<div style="color:var(--agent-atlas);">Searching KB...</div>';
  try {
    var resp = await fetch('/api/atlas/kb-search?q=' + encodeURIComponent(q) + '&top_k=15');
    var data = await resp.json();
    if (data.error) { resultEl.innerHTML = '<div style="color:var(--error);">' + esc(data.error) + '</div>'; return; }
    var results = data.results || [];
    var method = data.method || 'keyword';
    var html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">';
    html += '<span style="font-weight:600;color:var(--agent-atlas);">' + results.length + ' results</span>';
    html += '<span style="font-size:0.66rem;padding:2px 8px;border-radius:10px;background:' + (method === 'semantic' ? 'rgba(34,170,68,0.15);color:#22aa44' : 'rgba(255,170,0,0.15);color:#ffaa00') + ';">' + method + '</span>';
    html += '</div>';
    if (!results.length) {
      html += '<div class="text-muted">No matching entries found.</div>';
    } else {
      results.forEach(function(r) {
        var typeBadge = r._type === 'learning' ? '<span style="font-size:0.62rem;padding:1px 6px;border-radius:8px;background:rgba(0,212,255,0.12);color:#00d4ff;">Learning</span>' : '<span style="font-size:0.62rem;padding:1px 6px;border-radius:8px;background:rgba(255,255,255,0.06);color:var(--text-muted);">Observation</span>';
        var scoreColor = (r._score || 0) > 0.7 ? '#00ff44' : (r._score || 0) > 0.4 ? '#ffaa00' : 'var(--text-muted)';
        html += '<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">';
        html += '<div style="display:flex;align-items:center;gap:6px;">' + typeBadge;
        if (r.agent) html += '<span style="font-size:0.66rem;color:var(--text-muted);">' + esc(r.agent) + '</span>';
        html += '</div>';
        html += '<span style="font-size:0.68rem;color:' + scoreColor + ';font-weight:600;">' + ((r._score || 0) * 100).toFixed(0) + '%</span>';
        html += '</div>';
        var text = r.insight || r.observation || '';
        html += '<div style="font-size:0.74rem;color:var(--text-secondary);margin-top:3px;line-height:1.4;">' + esc(text.substring(0, 300)) + '</div>';
        if (r.confidence !== undefined) html += '<div style="font-size:0.64rem;color:var(--text-muted);margin-top:2px;">Confidence: ' + (r.confidence * 100).toFixed(0) + '%</div>';
        html += '</div>';
      });
    }
    resultEl.innerHTML = html;
  } catch(e) { resultEl.innerHTML = '<div style="color:var(--error);">Search failed: ' + esc(e.message) + '</div>'; }
}

async function loadAtlasCompetitorSummary() {
  var el = document.getElementById('atlas-competitor-summary');
  var bullets = document.getElementById('atlas-competitor-bullets');
  if (!el || !bullets) return;
  try {
    var resp = await fetch('/api/atlas/competitor-summary');
    var data = await resp.json();
    if (!data.has_data) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    var html = '';
    (data.bullets || []).forEach(function(b) {
      html += '<div style="margin-bottom:4px;padding-left:12px;border-left:2px solid var(--agent-atlas);">' + esc(b.text) + '</div>';
    });
    bullets.innerHTML = html;
  } catch(e) { el.style.display = 'none'; }
}

function atlasCompetitorSendTo(agent) {
  var bullets = document.getElementById('atlas-competitor-bullets');
  if (!bullets) return;
  var text = bullets.innerText;
  if (!text) { alert('No competitor intel to send'); return; }
  fetch('/api/brain/' + agent, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:'Competitor Intel from Atlas',content:text.substring(0,2000),type:'note',tags:['competitor','atlas']})})
  .then(function(r){return r.json();}).then(function(d){
    alert('Sent to ' + agent + (d.success ? ' successfully' : ': ' + (d.error || 'failed')));
  }).catch(function(e){ alert('Failed: ' + e.message); });
}

function atlasPriorityBadge(p) {
  var lvl = (p||'medium').toLowerCase();
  var cls = 'atlas-priority atlas-priority--' + ({'critical':'critical','high':'high','medium':'medium','low':'low'}[lvl]||'medium');
  return '<span class="' + cls + '">' + esc(lvl) + '</span>';
}
function atlasAgentColor(agent) {
  return {garves:'var(--agent-garves)',soren:'var(--agent-soren)',shelby:'var(--agent-shelby)',lisa:'var(--agent-lisa)',atlas:'var(--agent-atlas)',thor:'var(--agent-thor)',robotox:'var(--agent-sentinel)',sentinel:'var(--agent-sentinel)',hawk:'var(--agent-hawk, #FFD700)',quant:'var(--agent-quant, #00BFFF)',viper:'var(--agent-viper, #00ff88)',odin:'var(--agent-odin, #E8DCC8)'}[agent] || 'var(--text-secondary)';
}
function atlasAgentLabel(agent) {
  return {garves:'Garves',soren:'Soren',shelby:'Shelby',lisa:'Lisa',atlas:'Atlas',thor:'Thor',robotox:'Robotox',sentinel:'Robotox',hawk:'Hawk',quant:'Quant',viper:'Viper',odin:'Odin'}[agent] || agent;
}
function atlasKvRows(obj) {
  var html = '';
  var keys = Object.keys(obj);
  for (var i = 0; i < keys.length; i++) {
    var v = obj[keys[i]];
    var display = (typeof v === 'object' && v !== null) ? JSON.stringify(v) : String(v);
    html += '<div class="atlas-kv-row"><span class="kv-key">' + esc(keys[i].replace(/_/g, ' ')) + '</span><span class="kv-val">' + esc(display) + '</span></div>';
  }
  return html;
}
function atlasSection(title, color, bodyHtml) {
  return '<div class="atlas-report-section"><div class="section-header"><span class="sh-dot" style="background:' + color + ';box-shadow:0 0 6px ' + color + ';"></span>' + esc(title) + '</div><div class="section-body">' + bodyHtml + '</div></div>';
}

async function atlasFullReport() {
  var el = document.getElementById('atlas-report');
  el.style.display = 'block';
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Generating full intelligence report...</div>';
  try {
    var resp = await fetch('/api/atlas/report', {method:'POST'});
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var html = '<div class="atlas-report-header">Full Intelligence Report <span style="float:right;font-size:0.7rem;font-weight:400;color:var(--text-muted);">' + esc(data.generated_at || 'now') + '</span></div>';
    if (data.cross_agent_insights && data.cross_agent_insights.length > 0) {
      var body = '';
      for (var i = 0; i < data.cross_agent_insights.length; i++) {
        body += '<div class="atlas-rec-item"><span class="rec-num" style="color:var(--agent-atlas);">&#x25CF;</span><span class="rec-text">' + esc(data.cross_agent_insights[i]) + '</span></div>';
      }
      html += atlasSection('Cross-Agent Insights', 'var(--agent-atlas)', body);
    }
    var agents = ['garves','soren','shelby','lisa'];
    for (var a = 0; a < agents.length; a++) {
      var name = agents[a];
      var section = data[name];
      if (!section) continue;
      var color = atlasAgentColor(name);
      var body = '';
      if (section.overview) { body += atlasKvRows(section.overview); }
      if (section.recommendations && section.recommendations.length > 0) {
        body += '<div style="margin-top:var(--space-3);font-family:var(--font-mono);font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:var(--space-2);">Recommendations</div>';
        for (var r = 0; r < section.recommendations.length; r++) {
          var rec = section.recommendations[r];
          var recText = (typeof rec === 'string') ? rec : (rec.recommendation || rec.description || JSON.stringify(rec));
          var recPri = (typeof rec === 'object' && rec.priority) ? rec.priority : null;
          body += '<div class="atlas-rec-item">' + (recPri ? atlasPriorityBadge(recPri) + ' ' : '') + '<span class="rec-text">' + esc(recText) + '</span></div>';
        }
      }
      html += atlasSection(atlasAgentLabel(name), color, body);
    }
    if (data.action_items && data.action_items.length > 0) {
      var body = '';
      for (var k = 0; k < data.action_items.length; k++) {
        var item = data.action_items[k];
        var agentTag = item.agent ? '<span class="atlas-agent-tag" style="color:' + atlasAgentColor(item.agent) + ';border-color:' + atlasAgentColor(item.agent) + ';">' + esc(atlasAgentLabel(item.agent)) + '</span>' : '';
        body += '<div class="atlas-rec-item"><span class="rec-num">' + (k+1) + '</span>' + atlasPriorityBadge(item.priority) + '<span class="rec-text">' + esc(item.recommendation || item.description || '') + agentTag + '</span></div>';
      }
      html += atlasSection('Action Items (' + data.action_items.length + ')', 'var(--warning)', body);
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}

async function atlasAnalyze(agent) {
  var el = document.getElementById('atlas-report');
  el.style.display = 'block';
  var label = atlasAgentLabel(agent);
  var color = atlasAgentColor(agent);
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Analyzing ' + esc(label) + '...</div>';
  try {
    var resp = await fetch('/api/atlas/' + agent);
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var html = '<div class="atlas-report-header" style="background:rgba(255,255,255,0.03);border-left-color:' + color + ';">' + esc(label) + ' Deep Analysis</div>';
    if (data.overview) {
      html += atlasSection('Overview', color, atlasKvRows(data.overview));
    }
    if (data.edge_analysis) {
      html += atlasSection('Edge Analysis', color, atlasKvRows(data.edge_analysis));
    }
    if (data.breakdowns) {
      var bKeys = Object.keys(data.breakdowns);
      for (var b = 0; b < bKeys.length; b++) {
        var bd = data.breakdowns[bKeys[b]];
        var body = (typeof bd === 'object' && bd !== null) ? atlasKvRows(bd) : '<div class="atlas-kv-row"><span class="kv-val">' + esc(String(bd)) + '</span></div>';
        html += atlasSection(bKeys[b].replace(/_/g, ' '), color, body);
      }
    }
    var sections = ['regime_analysis','straddle_analysis','time_analysis','indicator_analysis',
                    'queue_audit','pillar_balance','caption_quality','hashtag_audit','ab_testing',
                    'posting_overview','scheduling_analysis','platform_health','outbox_status',
                    'tasks','scheduler','economics','profile'];
    for (var s = 0; s < sections.length; s++) {
      var sec = data[sections[s]];
      if (!sec) continue;
      var secTitle = sections[s].replace(/_/g, ' ');
      if (typeof sec === 'object' && !Array.isArray(sec)) {
        html += atlasSection(secTitle, color, atlasKvRows(sec));
      } else if (Array.isArray(sec)) {
        var body = '';
        for (var si = 0; si < sec.length; si++) {
          var item = sec[si];
          if (typeof item === 'string') {
            body += '<div class="atlas-rec-item"><span class="rec-num">' + (si+1) + '</span><span class="rec-text">' + esc(item) + '</span></div>';
          } else {
            var pri = item.priority ? atlasPriorityBadge(item.priority) + ' ' : '';
            body += '<div class="atlas-rec-item"><span class="rec-num">' + (si+1) + '</span>' + pri + '<span class="rec-text">' + esc(item.description || item.recommendation || JSON.stringify(item)) + '</span></div>';
          }
        }
        html += atlasSection(secTitle, color, body);
      }
    }
    if (data.recommendations && data.recommendations.length > 0) {
      var body = '';
      for (var ri = 0; ri < data.recommendations.length; ri++) {
        var rec = data.recommendations[ri];
        if (typeof rec === 'string') {
          body += '<div class="atlas-rec-item"><span class="rec-num">' + (ri+1) + '</span><span class="rec-text">' + esc(rec) + '</span></div>';
        } else {
          body += '<div class="atlas-rec-item"><span class="rec-num">' + (ri+1) + '</span>' + atlasPriorityBadge(rec.priority) + '<span class="rec-text">' + esc(rec.recommendation || rec.description || '') + '</span></div>';
        }
      }
      html += atlasSection('Recommendations', color, body);
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}

async function atlasSuggestImprovements() {
  var el = document.getElementById('atlas-report');
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Scanning for improvements...</div>';
  try {
    var resp = await fetch('/api/atlas/improvements', {method:'POST'});
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var html = '<div class="atlas-report-header" style="border-left-color:var(--warning);">Improvement Scan</div>';
    var count = 0;
    var agents = ['garves','soren','shelby','lisa'];
    for (var a = 0; a < agents.length; a++) {
      var name = agents[a];
      var items = data[name];
      if (!items || items.length === 0) continue;
      var color = atlasAgentColor(name);
      var body = '';
      for (var i = 0; i < items.length; i++) {
        var imp = items[i];
        count++;
        body += '<div class="atlas-rec-item">' + atlasPriorityBadge(imp.priority) + '<span class="rec-text">' + esc(imp.description || imp.title || '');
        var chips = '';
        if (imp.impact) chips += '<span class="atlas-impact-chip">Impact: ' + esc(imp.impact) + '</span> ';
        if (imp.effort) chips += '<span class="atlas-impact-chip">Effort: ' + esc(imp.effort) + '</span> ';
        if (imp.skill) chips += '<span class="atlas-impact-chip">Skill: ' + esc(imp.skill) + '</span>';
        if (chips) body += '<div style="margin-top:3px;">' + chips + '</div>';
        body += '</span></div>';
      }
      html += atlasSection(atlasAgentLabel(name) + ' (' + items.length + ')', color, body);
    }
    if (data.system_wide && data.system_wide.length > 0) {
      var body = '';
      for (var sw = 0; sw < data.system_wide.length; sw++) {
        var s = data.system_wide[sw];
        count++;
        body += '<div class="atlas-rec-item">' + atlasPriorityBadge(s.priority) + '<span class="rec-text">' + esc(s.suggestion || s.description || '');
        if (s.area) body += '<span class="atlas-impact-chip" style="margin-left:6px;">Area: ' + esc(s.area) + '</span>';
        body += '</span></div>';
      }
      html += atlasSection('System-Wide (' + data.system_wide.length + ')', 'var(--text-secondary)', body);
    }
    if (data.new_agents && data.new_agents.length > 0) {
      var body = '';
      for (var na = 0; na < data.new_agents.length; na++) {
        var ag = data.new_agents[na];
        body += '<div class="atlas-rec-item"><span class="rec-text"><strong style="color:var(--text);">' + esc(ag.name||'?') + '</strong> <span class="atlas-agent-tag">' + esc(ag.role||'?') + '</span><br><span style="color:var(--text-muted);font-size:0.72rem;">' + esc(ag.description||'') + '</span></span></div>';
      }
      html += atlasSection('New Agents Suggested (' + data.new_agents.length + ')', 'var(--agent-soren)', body);
    }
    if (data.new_skills && data.new_skills.length > 0) {
      var body = '';
      for (var ns = 0; ns < data.new_skills.length; ns++) {
        var sk = data.new_skills[ns];
        body += '<div class="atlas-rec-item"><span class="atlas-agent-tag" style="color:' + atlasAgentColor(sk.agent) + ';border-color:' + atlasAgentColor(sk.agent) + ';">' + esc(atlasAgentLabel(sk.agent)) + '</span><span class="rec-text"><strong style="color:var(--text);">' + esc(sk.skill||'') + '</strong> &#x2014; ' + esc(sk.description||'') + '</span></div>';
      }
      html += atlasSection('New Skills Suggested (' + data.new_skills.length + ')', 'var(--agent-thor)', body);
    }
    if (count === 0) {
      html += '<div class="atlas-report-section"><div class="section-body" style="text-align:center;color:var(--text-muted);padding:var(--space-8);">No improvements found at this time. System is running optimally.</div></div>';
    } else {
      html += '<div style="text-align:right;font-family:var(--font-mono);font-size:0.72rem;color:var(--text-muted);padding:var(--space-2) 0;">Total: ' + count + ' improvements</div>';
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}


async function atlasAcknowledgeImprovements() {
  var el = document.getElementById('atlas-report');
  try {
    var resp = await fetch('/api/atlas/improvements/acknowledge', {method:'POST'});
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    el.innerHTML = atlasSection('Suggestions Dismissed', 'var(--text-muted)', '<div style="text-align:center;padding:var(--space-4);color:var(--text-secondary);font-family:var(--font-mono);font-size:0.78rem;">Acknowledged <strong>' + data.acknowledged + '</strong> suggestions. Atlas will generate fresh insights next cycle.<br><span style="color:var(--text-muted);font-size:0.72rem;">Total dismissed: ' + data.total_dismissed + '</span></div>');
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}

async function atlasCompressKB() {
  var el = document.getElementById('atlas-report');
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Compressing knowledge base...</div>';
  try {
    var resp = await fetch('/api/atlas/summarize', {method:'POST'});
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var body = '';
    if (data.before_count !== undefined) body += '<div class="atlas-kv-row"><span class="kv-key">Before</span><span class="kv-val">' + data.before_count + ' observations</span></div>';
    if (data.after_count !== undefined) body += '<div class="atlas-kv-row"><span class="kv-key">After</span><span class="kv-val">' + data.after_count + ' observations</span></div>';
    if (data.new_learnings !== undefined) body += '<div class="atlas-kv-row"><span class="kv-key">New Learnings</span><span class="kv-val" style="color:var(--success);">' + data.new_learnings + '</span></div>';
    if (data.message) body += '<div style="margin-top:var(--space-3);color:var(--text-secondary);font-size:0.76rem;">' + esc(data.message) + '</div>';
    if (!body) body = '<div style="color:var(--text-secondary);">' + esc(JSON.stringify(data)) + '</div>';
    el.innerHTML = atlasSection('Knowledge Base Compressed', 'var(--agent-atlas)', body);
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}

// ═══ V8: Atlas Priority Queue + Dashboard Summary ═══

async function loadAtlasPriorityQueue() {
  var el = document.getElementById('atlas-priority-queue');
  if (!el) return;
  try {
    var resp = await fetch('/api/atlas/priority-queue');
    var d = await resp.json();
    var actions = d.actions || [];
    if (actions.length === 0) {
      el.innerHTML = '<div class="glass-card" style="text-align:center;padding:20px;"><span style="color:var(--success);font-weight:600;">All clear — nothing urgent</span></div>';
      return;
    }
    var html = '';
    var catColors = {kb: '#22aa44', feeding: '#00bfff', research: '#a855f7', system: '#f59e0b'};
    var catIcons = {kb: '&#x1F9E0;', feeding: '&#x1F4E1;', research: '&#x1F50D;', system: '&#x2699;'};
    for (var i = 0; i < actions.length; i++) {
      var a = actions[i];
      var catColor = catColors[a.category] || '#888';
      var catIcon = catIcons[a.category] || '&#x25CF;';
      var prioBg = a.priority >= 80 ? 'rgba(255,68,68,0.15)' : a.priority >= 60 ? 'rgba(255,159,11,0.12)' : 'rgba(255,255,255,0.04)';
      var prioBorder = a.priority >= 80 ? 'rgba(255,68,68,0.3)' : a.priority >= 60 ? 'rgba(255,159,11,0.25)' : 'rgba(255,255,255,0.08)';
      html += '<div class="glass-card" style="padding:10px 14px;border-left:3px solid ' + catColor + ';background:' + prioBg + ';border:1px solid ' + prioBorder + ';">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">';
      html += '<div style="flex:1;min-width:0;">';
      html += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">';
      html += '<span style="font-size:0.82rem;">' + catIcon + '</span>';
      html += '<span style="font-weight:600;font-size:0.78rem;color:var(--text-primary);">' + esc(a.title) + '</span>';
      html += '<span style="font-size:0.6rem;padding:1px 6px;border-radius:3px;background:' + catColor + '22;color:' + catColor + ';font-weight:600;text-transform:uppercase;">' + esc(a.category) + '</span>';
      html += '</div>';
      html += '<div style="font-size:0.68rem;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(a.description) + '</div>';
      html += '</div>';
      html += '<div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">';
      html += '<span style="font-size:0.64rem;color:var(--text-muted);">' + esc(a.impact || '') + '</span>';
      html += '<button class="btn" onclick="atlasExecuteAction(' + i + ')" style="font-size:0.66rem;padding:3px 10px;white-space:nowrap;" id="atlas-pq-btn-' + i + '">Execute</button>';
      html += '</div></div>';
      html += '<div id="atlas-pq-result-' + i + '" style="display:none;margin-top:8px;padding:8px 10px;border-radius:4px;background:rgba(34,170,68,0.08);font-size:0.72rem;font-family:var(--font-mono);"></div>';
      html += '</div>';
    }
    el.innerHTML = html;
    window._atlasPQActions = actions;
  } catch(e) {
    el.innerHTML = '<div class="glass-card" style="text-align:center;padding:16px;"><span class="text-muted">Failed to load priority queue</span></div>';
  }
}

async function atlasExecuteAction(idx) {
  var actions = window._atlasPQActions || [];
  if (!actions[idx]) return;
  var a = actions[idx];
  var btn = document.getElementById('atlas-pq-btn-' + idx);
  var result = document.getElementById('atlas-pq-result-' + idx);
  if (btn) { btn.disabled = true; btn.textContent = 'Running...'; btn.style.opacity = '0.5'; }
  // Show immediate feedback — research takes 10-20s
  if (result) {
    result.style.display = 'block';
    result.style.background = 'rgba(34,170,68,0.06)';
    result.innerHTML = '<span style="color:var(--agent-atlas);">Researching ' + esc(a.action_body && a.action_body.agent ? a.action_body.agent : a.title || '') + '...</span> <span class="text-muted" style="font-size:0.66rem;">(takes 10-20s)</span>';
  }
  try {
    var opts = {};
    if (a.action_method === 'POST') {
      opts.method = 'POST';
      opts.headers = {'Content-Type': 'application/json'};
      if (a.action_body) opts.body = JSON.stringify(a.action_body);
    }
    var resp = await fetch(a.action_endpoint, opts);
    var d = await resp.json();
    if (result) {
      result.style.display = 'block';
      if (d.error) {
        result.style.background = 'rgba(255,68,68,0.1)';
        result.textContent = 'Error: ' + (d.error || 'Unknown');
      } else {
        result.style.background = 'rgba(34,170,68,0.08)';
        var summary = d.message || d.summary || (d.count !== undefined ? d.count + ' results found' : '') || JSON.stringify(d).substring(0, 150);
        result.innerHTML = '<span style="color:var(--success);font-weight:600;">Done</span> — ' + esc(String(summary));
      }
    }
    if (btn) { btn.textContent = 'Done'; btn.style.color = 'var(--success)'; }
  } catch(e) {
    if (result) { result.style.display = 'block'; result.style.background = 'rgba(255,68,68,0.1)'; result.textContent = 'Failed: ' + e.message; }
    if (btn) { btn.textContent = 'Failed'; btn.disabled = false; btn.style.opacity = '1'; }
  }
}

async function atlasExecuteTop3() {
  var btn = document.getElementById('atlas-exec-top3-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Executing...'; btn.style.opacity = '0.6'; }
  var max = Math.min(3, (window._atlasPQActions || []).length);
  for (var i = 0; i < max; i++) {
    await atlasExecuteAction(i);
  }
  if (btn) { btn.textContent = 'Done'; btn.style.opacity = '1'; }
  setTimeout(function() {
    if (btn) { btn.textContent = 'Execute Top 3'; btn.disabled = false; }
    loadAtlasPriorityQueue();
    loadAtlasDashboardSummary();
  }, 3000);
}

async function loadAtlasDashboardSummary() {
  try {
    var resp = await fetch('/api/atlas/dashboard-summary');
    var d = await resp.json();

    // KB Health
    var kb = d.kb || {};
    var scoreEl = document.getElementById('atlas-kb-score');
    if (scoreEl) {
      scoreEl.textContent = (kb.score || 0) + '/100';
      scoreEl.style.color = kb.score >= 70 ? 'var(--success)' : kb.score >= 40 ? '#FFD700' : 'var(--error)';
    }
    setText('atlas-kb-obs', kb.observations || 0);
    setText('atlas-kb-learn', kb.learnings || 0);
    var staleEl = document.getElementById('atlas-kb-stale');
    if (staleEl) { staleEl.textContent = (kb.stale || 0) + ' stale'; staleEl.style.color = kb.stale > 10 ? 'var(--error)' : 'var(--warning)'; }

    // Agent Feeds
    var feeds = d.feeds || {};
    setText('atlas-feed-count', feeds.fed || 0);
    setText('atlas-feed-total', feeds.total || 11);
    var starvEl = document.getElementById('atlas-feed-starving');
    if (starvEl) { starvEl.textContent = (feeds.starving || 0) + ' starving'; starvEl.style.color = feeds.starving > 0 ? 'var(--error)' : 'var(--success)'; }
    var feedCountEl = document.getElementById('atlas-feed-count');
    if (feedCountEl) feedCountEl.style.color = feeds.fed >= 8 ? 'var(--success)' : feeds.fed >= 4 ? '#FFD700' : 'var(--error)';

    // Research ROI
    var r = d.research || {};
    var qEl = document.getElementById('atlas-research-quality');
    if (qEl) {
      qEl.textContent = (r.avg_quality || 0) + '/10';
      qEl.style.color = r.avg_quality >= 7 ? 'var(--success)' : r.avg_quality >= 5 ? '#FFD700' : 'var(--error)';
    }
    setText('atlas-research-today', (r.today || 0) + ' entries');
    setText('atlas-research-hit', (r.hit_rate || 0) + '%');
    setText('atlas-research-tavily', (r.month_tavily || 0) + '/' + (r.tavily_budget || 12000));
  } catch(e) {}
}

async function atlasInfraEval() {
  var el = document.getElementById('atlas-report');
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Evaluating system infrastructure...</div>';
  try {
    var resp = await fetch('/api/atlas/infra-eval');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var html = '<div class="atlas-report-header" style="border-left-color:var(--agent-atlas);">System / Infrastructure Evaluation</div>';
    // KB stats
    if (data.knowledge_base) {
      html += atlasSection('Knowledge Base', 'var(--agent-atlas)', atlasKvRows(data.knowledge_base));
    }
    // Per-agent health
    if (data.agents_health) {
      var agents = Object.keys(data.agents_health);
      for (var i = 0; i < agents.length; i++) {
        var name = agents[i];
        var h = data.agents_health[name];
        html += atlasSection(atlasAgentLabel(name) + ' Health', atlasAgentColor(name), atlasKvRows(h));
      }
    }
    // Recommendations
    if (data.recommendations && data.recommendations.length > 0) {
      var body = '';
      for (var r = 0; r < data.recommendations.length; r++) {
        var rec = data.recommendations[r];
        body += '<div class="atlas-rec-item">' + atlasPriorityBadge(rec.priority) + '<span class="rec-text">' + esc(rec.recommendation || '') + '</span></div>';
      }
      html += atlasSection('Recommendations', 'var(--warning)', body);
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}

async function atlasThoughts() {
  var el = document.getElementById('atlas-report');
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Loading Atlas thoughts...</div>';
  try {
    var resp = await fetch('/api/atlas/thoughts');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var html = '<div class="atlas-report-header" style="border-left-color:var(--agent-atlas);">Atlas Thoughts</div>';
    // Recent observations
    if (data.observations && data.observations.length > 0) {
      var body = '';
      for (var i = 0; i < data.observations.length; i++) {
        var obs = data.observations[i];
        var agent = obs.agent || obs.source || '';
        var tag = agent ? '<span class="atlas-agent-tag" style="color:' + atlasAgentColor(agent) + ';border-color:' + atlasAgentColor(agent) + ';">' + esc(agent) + '</span> ' : '';
        body += '<div class="atlas-rec-item"><span class="rec-num" style="color:var(--agent-atlas);">&#x25CF;</span><span class="rec-text">' + tag + esc(obs.observation || obs.content || obs.text || JSON.stringify(obs).substring(0, 200)) + '</span></div>';
      }
      html += atlasSection('Recent Observations (' + data.observations.length + ')', 'var(--agent-atlas)', body);
    }
    // Learnings
    if (data.learnings && data.learnings.length > 0) {
      var body = '';
      for (var i = 0; i < data.learnings.length; i++) {
        var l = data.learnings[i];
        body += '<div class="atlas-rec-item"><span class="rec-num">' + (i+1) + '</span><span class="rec-text">' + esc(l.learning || l.content || l.summary || JSON.stringify(l).substring(0, 200)) + '</span></div>';
      }
      html += atlasSection('Lessons Learned (' + data.learnings.length + ')', 'var(--success)', body);
    }
    // Experiments/Hypotheses
    if (data.experiments && data.experiments.length > 0) {
      var body = '';
      for (var i = 0; i < data.experiments.length; i++) {
        var ex = data.experiments[i];
        body += '<div class="atlas-rec-item"><span class="rec-num">' + (i+1) + '</span><span class="rec-text"><strong>' + esc(ex.hypothesis || ex.title || '?') + '</strong>';
        if (ex.status) body += ' <span class="atlas-impact-chip">' + esc(ex.status) + '</span>';
        if (ex.result) body += '<br><span style="color:var(--text-muted);font-size:0.72rem;">' + esc(ex.result) + '</span>';
        body += '</span></div>';
      }
      html += atlasSection('Active Experiments (' + data.experiments.length + ')', 'var(--agent-soren)', body);
    }
    // Recent research
    if (data.recent_research && data.recent_research.length > 0) {
      var body = '';
      for (var i = 0; i < data.recent_research.length; i++) {
        var r = data.recent_research[i];
        var q = r.query || r.topic || '';
        var insight = r.insight || r.summary || '';
        body += '<div class="atlas-rec-item"><span class="rec-num" style="color:var(--agent-garves);">&#x1F50D;</span><span class="rec-text"><strong>' + esc(q) + '</strong>';
        if (insight) body += '<br><span style="color:var(--text-muted);font-size:0.72rem;">' + esc(insight) + '</span>';
        body += '</span></div>';
      }
      html += atlasSection('Recent Research (' + data.recent_research.length + ')', 'var(--agent-garves)', body);
    }
    if (!data.observations?.length && !data.learnings?.length && !data.experiments?.length) {
      html += '<div class="atlas-report-section"><div class="section-body" style="text-align:center;color:var(--text-muted);padding:var(--space-8);">Atlas has no thoughts yet. Start the background loop to begin learning.</div></div>';
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}

async function atlasHubEval() {
  var el = document.getElementById('atlas-report');
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Evaluating agent hub...</div>';
  try {
    var resp = await fetch('/api/atlas/hub-eval');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var html = '<div class="atlas-report-header" style="border-left-color:var(--agent-atlas);">Hub Evaluation</div>';

    // Agent roster — compact grid
    if (data.our_system) {
      var sys = data.our_system;
      var body = '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">';
      body += '<span style="font-size:1.4rem;font-weight:700;color:var(--agent-atlas);">' + sys.total_agents + '</span>';
      body += '<span style="font-size:0.72rem;color:var(--text-muted);">agents</span>';
      body += '<span style="font-size:0.68rem;color:var(--text-secondary);margin-left:auto;">' + esc(sys.architecture || '') + '</span>';
      body += '</div>';
      // Agent chips
      var agents = sys.agents || [];
      body += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;">';
      for (var i = 0; i < agents.length; i++) {
        var a = agents[i];
        if (typeof a === 'object') {
          body += '<div style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;background:' + (a.color || '#666') + '15;border:1px solid ' + (a.color || '#666') + '30;border-radius:5px;font-size:0.66rem;">';
          body += '<span style="color:' + (a.color || '#fff') + ';font-weight:600;">' + esc(a.name) + '</span>';
          body += '<span style="color:var(--text-muted);">' + esc(a.role) + '</span></div>';
        } else {
          body += '<span style="padding:3px 8px;background:rgba(34,170,68,0.1);border-radius:5px;font-size:0.66rem;color:var(--success);">' + esc(a) + '</span>';
        }
      }
      body += '</div>';
      // Features as compact tags
      if (sys.features && sys.features.length > 0) {
        body += '<div style="font-size:0.62rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Capabilities (' + sys.features.length + ')</div>';
        body += '<div style="display:flex;flex-wrap:wrap;gap:3px;">';
        for (var i = 0; i < sys.features.length; i++) {
          body += '<span style="padding:2px 6px;background:rgba(34,170,68,0.08);border-radius:3px;font-size:0.62rem;color:var(--success);">' + esc(sys.features[i]) + '</span>';
        }
        body += '</div>';
      }
      html += atlasSection('Brotherhood', 'var(--agent-atlas)', body);
    }

    // Strengths + Gaps side by side
    var sgBody = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">';
    // Strengths column
    sgBody += '<div>';
    sgBody += '<div style="font-size:0.65rem;color:var(--success);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;font-weight:600;">Strengths (' + (data.strengths || []).length + ')</div>';
    if (data.strengths) {
      for (var i = 0; i < data.strengths.length; i++) {
        sgBody += '<div style="font-size:0.7rem;padding:3px 0;color:var(--text-secondary);"><span style="color:var(--success);margin-right:4px;">+</span>' + esc(data.strengths[i]) + '</div>';
      }
    }
    sgBody += '</div>';
    // Gaps column
    sgBody += '<div>';
    sgBody += '<div style="font-size:0.65rem;color:var(--error);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;font-weight:600;">Gaps (' + (data.gaps || []).length + ')</div>';
    if (data.gaps && data.gaps.length > 0) {
      for (var i = 0; i < data.gaps.length; i++) {
        sgBody += '<div style="font-size:0.7rem;padding:3px 0;color:var(--text-secondary);"><span style="color:var(--error);margin-right:4px;">-</span>' + esc(data.gaps[i]) + '</div>';
      }
    } else {
      sgBody += '<div style="font-size:0.7rem;color:var(--text-muted);">No critical gaps detected</div>';
    }
    sgBody += '</div></div>';
    html += atlasSection('Assessment', 'var(--warning)', sgBody);

    // Recommendations — compact with priority badges
    if (data.recommendations && data.recommendations.length > 0) {
      var body = '';
      for (var i = 0; i < data.recommendations.length; i++) {
        var rec = data.recommendations[i];
        body += '<div style="display:flex;align-items:flex-start;gap:6px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
        body += atlasPriorityBadge(rec.priority);
        body += '<span style="font-size:0.7rem;color:var(--text-secondary);line-height:1.4;">' + esc(rec.recommendation || '') + '</span>';
        body += '</div>';
      }
      html += atlasSection('Recommendations', 'var(--warning)', body);
    }

    // Research insights — compact cards with agent, quality, source
    if (data.research_insights && data.research_insights.length > 0) {
      var body = '';
      for (var i = 0; i < data.research_insights.length; i++) {
        var ri = data.research_insights[i];
        var agent = ri.agent || 'general';
        var quality = ri.quality_score || 0;
        var qColor = quality >= 9 ? 'var(--success)' : quality >= 7 ? 'var(--warning)' : 'var(--text-muted)';
        var insight = ri.insight || '';
        // Truncate at sentence boundary
        if (insight.length > 200) {
          var lastDot = insight.indexOf('.', 100);
          if (lastDot > 0 && lastDot < 250) insight = insight.substring(0, lastDot + 1);
          else insight = insight.substring(0, 200) + '...';
        }
        body += '<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
        body += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">';
        body += '<span style="font-size:0.62rem;font-weight:600;color:var(--agent-atlas);text-transform:uppercase;">' + esc(agent) + '</span>';
        body += '<span style="font-size:0.58rem;padding:1px 5px;border-radius:3px;background:' + qColor + '18;color:' + qColor + ';">' + quality + '/10</span>';
        if (ri.source) body += '<span style="font-size:0.6rem;color:var(--text-muted);margin-left:auto;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(ri.source) + '</span>';
        body += '</div>';
        body += '<div style="font-size:0.7rem;color:var(--text-secondary);line-height:1.4;">' + esc(insight) + '</div>';
        body += '</div>';
      }
      html += atlasSection('Research Insights', 'var(--agent-atlas)', body);
    }

    // Competitor intel — compact
    if (data.competitor_insights && data.competitor_insights.length > 0) {
      var body = '';
      for (var i = 0; i < data.competitor_insights.length; i++) {
        var ci = data.competitor_insights[i];
        body += '<div style="font-size:0.7rem;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.04);color:var(--text-secondary);">';
        body += '<span style="color:var(--text-primary);font-weight:500;">' + esc(ci.title || '') + '</span>';
        if (ci.snippet) body += '<div style="font-size:0.66rem;color:var(--text-muted);margin-top:1px;">' + esc(ci.snippet.substring(0, 120)) + '</div>';
        body += '</div>';
      }
      html += atlasSection('Industry Intel', 'var(--agent-soren)', body);
    }

    el.innerHTML = html;
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}

async function atlasSuggestAgent() {
  var el = document.getElementById('atlas-report');
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Analyzing if a new agent is needed...</div>';
  try {
    var resp = await fetch('/api/atlas/suggest-agent');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var html = '<div class="atlas-report-header" style="border-left-color:var(--agent-soren);">New Agent Suggestion</div>';
    // Verdict
    html += atlasSection('Verdict', 'var(--agent-atlas)', '<div style="padding:var(--space-3);color:var(--text);font-family:var(--font-mono);font-size:0.82rem;line-height:1.6;">' + esc(data.verdict || 'No verdict') + '</div><div class="atlas-kv-row"><span class="kv-key">Current Agents</span><span class="kv-val">' + (data.current_agents || 0) + '</span></div>');
    // Suggested by Atlas
    if (data.suggested_by_atlas && data.suggested_by_atlas.length > 0) {
      var body = '';
      for (var i = 0; i < data.suggested_by_atlas.length; i++) {
        var ag = data.suggested_by_atlas[i];
        body += '<div class="atlas-rec-item"><span class="rec-text"><strong style="color:var(--text);">' + esc(ag.name || '?') + '</strong> <span class="atlas-agent-tag">' + esc(ag.role || '?') + '</span><br><span style="color:var(--text-muted);font-size:0.72rem;">' + esc(ag.description || '') + '</span></span></div>';
      }
      html += atlasSection('Atlas Suggestions (' + data.suggested_by_atlas.length + ')', 'var(--agent-soren)', body);
    }
    // Gap analysis
    if (data.gap_analysis && data.gap_analysis.length > 0) {
      var body = '';
      for (var i = 0; i < data.gap_analysis.length; i++) {
        var gap = data.gap_analysis[i];
        body += '<div class="atlas-rec-item">' + atlasPriorityBadge(gap.need || 'medium') + '<span class="rec-text"><strong style="color:var(--text);">' + esc(gap.area || '') + '</strong> — ' + esc(gap.description || '') + '<br><span style="color:var(--text-muted);font-size:0.72rem;">' + esc(gap.reason || '') + '</span></span></div>';
      }
      html += atlasSection('Gap Analysis', 'var(--warning)', body);
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">Error: ' + esc(e.message) + '</div></div>'; }
}

async function loadCompetitorIntel() {
  try {
    var resp = await fetch('/api/atlas/competitors');
    var data = await resp.json();
    var el = document.getElementById('atlas-competitors');
    var sections = ['trading','content','ai_agents'];
    var labels = {trading:'Trading Intel',content:'Content Intel',ai_agents:'AI Agent Intel'};
    var hasData = false;
    var html = '';
    for (var s = 0; s < sections.length; s++) {
      var key = sections[s];
      var items = data[key] || [];
      if (items.length === 0) continue;
      hasData = true;
      html += '<div style="margin-bottom:var(--space-6);"><div style="font-family:var(--font-mono);font-size:0.78rem;font-weight:600;color:var(--agent-atlas);margin-bottom:var(--space-3);">' + labels[key] + '</div>';
      for (var i = 0; i < items.length && i < 5; i++) {
        var item = items[i];
        html += '<div style="padding:var(--space-2) 0;border-bottom:1px solid rgba(255,255,255,0.025);font-size:0.74rem;color:var(--text-secondary);">';
        html += esc(item.title || item.name || item.snippet || JSON.stringify(item).substring(0,100));
        if (item.source_url) html += ' <a href="' + esc(item.source_url) + '" target="_blank" style="font-size:0.68rem;">[source]</a>';
        html += '</div>';
      }
      html += '</div>';
    }
    if (!hasData) html = '<div class="text-muted" style="text-align:center;padding:var(--space-6);">No competitor intel yet. Start Atlas background to gather data.</div>';
    el.innerHTML = html;
  } catch (e) {}
}

function lisaScoreBadge(score) {
  if (score === null || score === undefined || score === -1) return '<span class="text-muted">--</span>';
  var color = score >= 7 ? '#22aa44' : score >= 4 ? '#ffaa00' : '#ff4444';
  var label = score >= 7 ? 'PASS' : score >= 4 ? 'WARN' : 'FAIL';
  return '<span style="color:' + color + ';font-weight:600;font-size:0.74rem;">' + score + '/10 ' + label + '</span>';
}

function renderLisa(data) {
  // Stats row
  var todayPosts = (data.recent_posts || []).filter(function(p) {
    return p.posted_at && p.posted_at.startsWith(new Date().toISOString().slice(0,10)) && p.status === 'posted';
  }).length;
  var el = document.getElementById('lisa-posted-today');
  if (el) el.textContent = todayPosts;

  // Recent posts
  var posts = data.recent_posts || [];
  var tbody = document.getElementById('lisa-posts-tbody');
  if (posts.length === 0) { tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px;">No posts yet</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < posts.length && i < 15; i++) {
    var p = posts[i];
    var timeStr = (p.posted_at || '--').substring(0, 19).replace('T', ' ');
    html += '<tr><td style="white-space:nowrap;font-size:0.72rem;">' + esc(timeStr) + '</td>';
    html += '<td>' + esc(p.platform || '--') + '</td>';
    html += '<td style="font-size:0.74rem;">' + esc((p.caption || p.content || '').substring(0,60)) + '</td>';
    html += '<td>' + (p.had_image ? '<span style="color:#22aa44;">Yes</span>' : '<span class="text-muted">No</span>') + '</td>';
    html += '<td><span class="badge badge-success">' + esc(p.status || 'posted') + '</span></td></tr>';
  }
  tbody.innerHTML = html;
}

// ── Lisa X Integration JS Functions ──

async function testXConnection() {
  var statusEl = document.getElementById('x-connection-status');
  var detailsEl = document.getElementById('x-connection-details');
  statusEl.textContent = 'Testing...';
  statusEl.style.color = '#ffaa00';
  try {
    var resp = await fetch('/api/lisa/x-test');
    var data = await resp.json();
    if (data.ok) {
      statusEl.textContent = 'Connected';
      statusEl.style.color = '#22aa44';
      detailsEl.innerHTML = '<span style="color:#22aa44;">@' + esc(data.username) + '</span>' +
        ' | <span class="text-muted">Followers: ' + (data.followers || 0) + '</span>' +
        ' | <span class="text-muted">Tweets: ' + (data.tweets || 0) + '</span>';
    } else {
      statusEl.textContent = 'Error';
      statusEl.style.color = '#ff4444';
      detailsEl.innerHTML = '<span style="color:#ff4444;">' + esc(data.error || 'Connection failed') + '</span>';
    }
  } catch(e) {
    statusEl.textContent = 'Error';
    statusEl.style.color = '#ff4444';
    detailsEl.innerHTML = '<span style="color:#ff4444;">Request failed</span>';
  }
}

async function generateXImage(style) {
  var caption = style === 'quote' ? prompt('Enter quote text for the card:') : '';
  if (style === 'quote' && !caption) return;
  if (!caption) caption = 'dark motivation lone wolf';
  var resultEl = document.getElementById('img-result');
  if (resultEl) resultEl.innerHTML = '<span class="text-muted">Generating ' + style + ' image...</span>';
  try {
    var resp = await fetch('/api/lisa/generate-image', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({style: style, caption: caption, pillar: 'dark_motivation'})
    });
    var data = await resp.json();
    if (data.ok) {
      var msg = '<span style="color:#22aa44;">Generated: ' + esc(data.filename) + '</span>';
      if (resultEl) resultEl.innerHTML = msg;
      alert('Image generated: ' + data.filename);
      loadImageCosts();
    } else {
      var err = esc(data.error || 'Failed');
      if (resultEl) resultEl.innerHTML = '<span style="color:#ff4444;">' + err + '</span>';
      alert('Image failed: ' + (data.error || 'Unknown error'));
    }
  } catch(e) {
    if (resultEl) resultEl.innerHTML = '<span style="color:#ff4444;">Request failed</span>';
    alert('Image generation request failed');
  }
}

async function generateXImageCustom() {
  var pillar = document.getElementById('img-pillar').value;
  var style = document.getElementById('img-style').value;
  var caption = document.getElementById('img-caption').value || 'dark motivation';
  var resultEl = document.getElementById('img-result');
  resultEl.innerHTML = '<span class="text-muted">Generating...</span>';
  try {
    var resp = await fetch('/api/lisa/generate-image', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({style: style, caption: caption, pillar: pillar})
    });
    var data = await resp.json();
    if (data.ok) {
      resultEl.innerHTML = '<span style="color:#22aa44;">Generated: ' + esc(data.filename) + '</span>';
      loadImageCosts();
    } else {
      resultEl.innerHTML = '<span style="color:#ff4444;">' + esc(data.error || 'Failed') + '</span>';
    }
  } catch(e) {
    resultEl.innerHTML = '<span style="color:#ff4444;">Request failed</span>';
  }
}

async function scanXCompetitors() {
  var hooksEl = document.getElementById('x-viral-hooks');
  hooksEl.innerHTML = '<span class="text-muted">Scanning competitors...</span>';
  try {
    var resp = await fetch('/api/lisa/x-competitors/scan', {method:'POST'});
    var data = await resp.json();
    loadXCompetitorIntel();
  } catch(e) {
    hooksEl.innerHTML = '<span style="color:#ff4444;">Scan failed</span>';
  }
}

async function loadXCompetitorIntel() {
  try {
    var resp = await fetch('/api/lisa/x-competitors');
    var data = await resp.json();
    var usage = data.usage || {};
    var el1 = document.getElementById('x-comp-accounts');
    var el2 = document.getElementById('x-comp-hooks');
    var el3 = document.getElementById('x-comp-reads');
    if (el1) el1.textContent = (data.account_data || []).length;
    if (el2) el2.textContent = (data.viral_hooks || []).length;
    if (el3) el3.textContent = (usage.monthly_reads || 0) + '/' + (usage.monthly_budget || 1500);

    var hooks = data.viral_hooks || [];
    var hooksEl = document.getElementById('x-viral-hooks');
    if (hooks.length === 0) {
      hooksEl.innerHTML = '<span class="text-muted">No hooks found. Click "Scan Competitors" to fetch.</span>';
      return;
    }
    var html = '';
    for (var i = 0; i < hooks.length && i < 10; i++) {
      var h = hooks[i];
      html += '<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
      html += '<div style="color:var(--text);">"' + esc(h.hook || h.full_text || '') + '"</div>';
      html += '<div style="color:var(--text-muted);font-size:0.68rem;">@' + esc(h.author || '') + ' | ❤ ' + (h.likes || 0) + ' | 🔄 ' + (h.retweets || 0) + '</div>';
      html += '</div>';
    }
    hooksEl.innerHTML = html;
  } catch(e) {}
}

async function loadXMentions() {
  var feedEl = document.getElementById('x-mentions-feed');
  feedEl.innerHTML = '<span class="text-muted">Loading mentions...</span>';
  try {
    var resp = await fetch('/api/lisa/x-mentions');
    var data = await resp.json();
    var mentions = data.mentions || [];
    var el = document.getElementById('lisa-mentions-count');
    if (el) el.textContent = mentions.length;

    if (mentions.length === 0) {
      feedEl.innerHTML = '<span class="text-muted">No recent mentions</span>';
      return;
    }
    var html = '';
    for (var i = 0; i < mentions.length && i < 15; i++) {
      var m = mentions[i];
      html += '<div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
      html += '<div style="display:flex;justify-content:space-between;">';
      html += '<span style="color:#1DA1F2;font-weight:600;">@' + esc(m.author_username || '') + '</span>';
      html += '<span style="font-size:0.66rem;color:var(--text-muted);">' + esc((m.created_at || '').substring(0,16)) + '</span>';
      html += '</div>';
      html += '<div style="color:var(--text);margin:4px 0;">' + esc(m.text || '') + '</div>';
      html += '<div style="display:flex;gap:8px;">';
      html += '<button class="btn" style="font-size:0.68rem;padding:2px 8px;" onclick="replyToMention(\'' + esc(m.tweet_id) + '\')">Reply</button>';
      html += '</div></div>';
    }
    feedEl.innerHTML = html;
  } catch(e) {
    feedEl.innerHTML = '<span style="color:#ff4444;">Failed to load mentions</span>';
  }
}

async function replyToMention(tweetId) {
  var reply = prompt('Reply as Soren:');
  if (!reply) return;
  try {
    await fetch('/api/lisa/x-reply/' + tweetId, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({tweet_id: tweetId, reply_text: reply})
    });
    loadXMentions();
  } catch(e) {}
}

async function postNowToX(itemId) {
  try {
    var resp = await fetch('/api/lisa/post-now/' + encodeURIComponent(itemId), {method:'POST'});
    var data = await resp.json();
    if (data.ok) {
      alert('Posted! Tweet: ' + (data.tweet_url || data.tweet_id));
      loadJordanQueue();
    } else {
      alert('Failed: ' + (data.error || 'Unknown error'));
    }
  } catch(e) { alert('Request failed'); }
}

async function postNextToX() {
  try {
    var resp = await fetch('/api/lisa/posting-schedule');
    var data = await resp.json();
    var schedule = data.schedule || [];
    if (schedule.length === 0) { alert('No scheduled items'); return; }
    var first = schedule[0];
    postNowToX(first.id || first.item_id || '');
  } catch(e) { alert('Failed to get schedule'); }
}

async function loadAutoPostStatus() {
  try {
    var resp = await fetch('/api/lisa/auto-poster-status');
    var data = await resp.json();
    var badge = document.getElementById('x-auto-poster-badge');
    if (badge) {
      if (data.running) {
        badge.textContent = 'Auto-poster: ON';
        badge.style.color = '#22aa44';
        badge.style.background = 'rgba(34,170,68,0.1)';
      } else {
        badge.textContent = 'Auto-poster: OFF';
        badge.style.color = 'var(--text-muted)';
      }
    }
  } catch(e) {}
}

async function loadImageCosts() {
  try {
    var resp = await fetch('/api/lisa/image-costs');
    var data = await resp.json();
    var budgetEl = document.getElementById('lisa-image-budget');
    if (budgetEl) budgetEl.textContent = data.today_count + '/' + data.max_per_day;
    var barEl = document.getElementById('img-cost-bar');
    if (barEl) {
      barEl.textContent = 'Budget: $' + data.today_spent.toFixed(2) + '/$' + data.today_budget.toFixed(2) +
        ' | Week: $' + data.week_spent.toFixed(2) + ' | Total: $' + data.total_spent.toFixed(2);
    }
  } catch(e) {}
}

async function huntReplies() {
  var listEl = document.getElementById('reply-opportunities-list');
  listEl.innerHTML = '<div class="text-muted" style="padding:var(--space-4);text-align:center;">Hunting for reply opportunities...</div>';
  try {
    var resp = await fetch('/api/lisa/reply-hunt', {method:'POST'});
    var data = await resp.json();
    loadReplyOpportunities();
  } catch(e) {
    listEl.innerHTML = '<div style="color:#ff4444;padding:var(--space-4);text-align:center;">Hunt failed</div>';
  }
}

async function loadReplyOpportunities() {
  try {
    var resp = await fetch('/api/lisa/reply-opportunities');
    var data = await resp.json();
    var opps = data.opportunities || [];
    var status = data.status || {};

    var counterEl = document.getElementById('reply-counter');
    if (counterEl) counterEl.textContent = (status.replies_today || 0) + '/' + (status.max_replies || 10) + ' today';

    var listEl = document.getElementById('reply-opportunities-list');
    if (opps.length === 0) {
      listEl.innerHTML = '<div class="text-muted" style="padding:var(--space-4);text-align:center;">No opportunities yet. Click "Hunt Replies" to scan.</div>';
      return;
    }
    var html = '';
    for (var i = 0; i < opps.length && i < 10; i++) {
      var o = opps[i];
      html += '<div class="glass-card" style="margin-bottom:8px;border-left:3px solid #1DA1F2;padding:10px 14px;">';
      html += '<div style="display:flex;justify-content:space-between;margin-bottom:4px;">';
      html += '<span style="color:#1DA1F2;font-weight:600;font-size:0.74rem;">@' + esc(o.author_username || '') +
        ' <span class="text-muted" style="font-weight:400;">(' + (o.author_followers || 0).toLocaleString() + ' followers)</span></span>';
      html += '<span style="font-size:0.68rem;color:var(--text-muted);">Score: ' + (o.score || 0) +
        ' | ❤ ' + (o.likes || 0) + ' | 🔄 ' + (o.retweets || 0) + '</span>';
      html += '</div>';
      html += '<div style="font-family:var(--font-mono);font-size:0.74rem;color:var(--text);margin-bottom:8px;">"' + esc(o.text || '') + '"</div>';

      var replies = o.suggested_replies || [];
      if (replies.length > 0) {
        html += '<div style="margin-left:12px;border-left:2px solid rgba(29,161,242,0.3);padding-left:10px;">';
        for (var j = 0; j < replies.length; j++) {
          var r = replies[j];
          var confColor = r.confidence >= 0.85 ? '#22aa44' : r.confidence >= 0.7 ? '#ffaa00' : 'var(--text-muted)';
          html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:8px;">';
          html += '<span style="font-size:0.72rem;color:var(--text-secondary);">' + esc(r.text || '') + '</span>';
          html += '<div style="display:flex;gap:4px;flex-shrink:0;">';
          html += '<span style="font-size:0.66rem;color:' + confColor + ';">' + ((r.confidence || 0) * 100).toFixed(0) + '%</span>';
          html += '<button class="btn" style="font-size:0.66rem;padding:1px 6px;" onclick="postReplyToX(\'' + esc(o.id) + '\',' + j + ')">Post</button>';
          html += '</div></div>';
        }
        html += '</div>';
      }
      html += '</div>';
    }
    listEl.innerHTML = html;
  } catch(e) {}
}

async function postReplyToX(oppId, replyIdx) {
  if (!confirm('Post this reply to X?')) return;
  try {
    var resp = await fetch('/api/lisa/reply-post/' + oppId, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({reply_idx: replyIdx})
    });
    var data = await resp.json();
    if (data.ok) {
      alert('Reply posted!');
      loadReplyOpportunities();
    } else {
      alert('Failed: ' + (data.error || 'Unknown'));
    }
  } catch(e) { alert('Request failed'); }
}

async function loadLisaScheduledCount() {
  try {
    var resp = await fetch('/api/lisa/posting-schedule');
    var data = await resp.json();
    var el = document.getElementById('lisa-scheduled-count');
    if (el) el.textContent = data.count || 0;
  } catch(e) {}
}

async function loadLisaIntelligence() {
  try {
    var resp = await fetch('/api/lisa/intelligence');
    var data = await resp.json();
    if (data.error) return;

    // Stats
    var mem = data.memory || {};
    var perf = data.performance || {};
    setTextSafe('intel-users-remembered', mem.total_users_remembered || 0);
    setTextSafe('intel-total-interactions', mem.total_interactions || 0);
    setTextSafe('intel-loyal-fans', mem.loyal_fans || 0);
    setTextSafe('intel-engagement-rate', (perf.engagement_rate || 0) + '%');

    // Reply guidance
    var rg = data.reply_guidance || {};
    var rgEl = document.getElementById('intel-reply-guidance');
    if (rgEl) {
      if (rg.tip) {
        var html = '<div style="color:var(--text-primary);margin-bottom:4px;">' + esc(rg.tip) + '</div>';
        if (rg.style_rankings && rg.style_rankings.length > 0) {
          html += '<div style="color:var(--text-muted);font-size:0.68rem;">';
          for (var i = 0; i < Math.min(3, rg.style_rankings.length); i++) {
            var s = rg.style_rankings[i];
            html += s.style + ': ' + s.avg_engagement.toFixed(0) + ' avg (' + s.sample_size + ' replies)' + (i < 2 ? ' | ' : '');
          }
          html += '</div>';
        }
        rgEl.innerHTML = html;
      }
    }

    // Posting guidance
    var pg = data.posting_guidance || {};
    var pgEl = document.getElementById('intel-posting-guidance');
    if (pgEl && pg.tip) {
      var html = '<div style="color:var(--text-primary);margin-bottom:4px;">' + esc(pg.tip) + '</div>';
      if (pg.best_hours && pg.best_hours.length > 0) {
        html += '<div style="color:var(--text-muted);font-size:0.68rem;">Best hours: ' + pg.best_hours.map(function(h) { return h + ':00 ET'; }).join(', ') + '</div>';
      }
      if (pg.best_days && pg.best_days.length > 0) {
        html += '<div style="color:var(--text-muted);font-size:0.68rem;">Best days: ' + pg.best_days.join(', ') + '</div>';
      }
      pgEl.innerHTML = html;
    }

    // Top engagers
    var te = data.top_engagers || [];
    var teEl = document.getElementById('intel-top-engagers');
    if (teEl) {
      if (te.length === 0) {
        teEl.innerHTML = '<span class="text-muted">No interactions tracked yet</span>';
      } else {
        var html = '<div style="display:flex;flex-wrap:wrap;gap:6px;">';
        for (var i = 0; i < Math.min(10, te.length); i++) {
          var u = te[i];
          var tagColor = u.tags && u.tags.indexOf('loyal_fan') !== -1 ? '#22aa44' : u.tags && u.tags.indexOf('high_value') !== -1 ? '#ffaa00' : 'var(--text-muted)';
          html += '<span style="padding:2px 8px;border-radius:99px;background:rgba(255,255,255,0.05);border:1px solid ' + tagColor + ';font-size:0.68rem;">';
          html += '@' + esc(u.username) + ' <span style="color:' + tagColor + ';">' + u.interactions + 'x</span></span>';
        }
        html += '</div>';
        teEl.innerHTML = html;
      }
    }

    // Content guidance
    var cg = data.content_guidance || {};
    var cgEl = document.getElementById('intel-content-guidance');
    if (cgEl && cg.tip) {
      var html = '<div style="color:var(--text-primary);margin-bottom:4px;">' + esc(cg.tip) + '</div>';
      var iv = cg.image_vs_text || {};
      if (iv.image_boost) {
        html += '<div style="color:var(--text-muted);font-size:0.68rem;">' + esc(iv.image_boost);
        if (iv.with_image_avg > 0) html += ' (img: ' + iv.with_image_avg + ' vs text: ' + iv.without_image_avg + ')';
        html += '</div>';
      }
      cgEl.innerHTML = html;
    }
  } catch(e) { console.error('loadLisaIntelligence:', e); }
}

async function checkEngagement() {
  try {
    var btn = event.target;
    btn.textContent = 'Checking...';
    btn.disabled = true;
    var resp = await fetch('/api/lisa/intelligence/check-engagement', {method:'POST'});
    var data = await resp.json();
    btn.textContent = 'Checked ' + (data.checked || 0) + ' items';
    btn.disabled = false;
    setTimeout(function() { btn.textContent = 'Refresh Engagement'; }, 3000);
    loadLisaIntelligence();
  } catch(e) {
    btn.textContent = 'Error';
    btn.disabled = false;
  }
}

function setTextSafe(id, val) {
  var el = document.getElementById(id);
  if (el) el.textContent = val;
}

async function lisaReviewCaption() {
  var textarea = document.getElementById('lisa-review-caption');
  var platform = document.getElementById('lisa-review-platform').value;
  var resultEl = document.getElementById('lisa-review-result');
  var caption = textarea.value.trim();
  if (!caption) { resultEl.innerHTML = '<span class="text-muted">Enter a caption to review.</span>'; return; }
  resultEl.innerHTML = '<span class="text-muted">Reviewing...</span>';
  try {
    var resp = await fetch('/api/lisa/review', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({caption: caption, platform: platform})
    });
    var data = await resp.json();
    if (data.error) { resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(data.error) + '</span>'; return; }
    var html = '<div style="padding:var(--space-3);border-left:3px solid ' + (data.score >= 7 ? '#22aa44' : data.score >= 4 ? '#ffaa00' : '#ff4444') + ';margin-bottom:var(--space-3);">';
    html += '<div style="margin-bottom:var(--space-2);">' + lisaScoreBadge(data.score) + '</div>';
    if (data.issues && data.issues.length > 0) {
      html += '<div style="color:var(--text-secondary);font-size:0.74rem;margin-bottom:var(--space-2);">Issues:</div>';
      for (var i = 0; i < data.issues.length; i++) {
        html += '<div style="color:var(--text-muted);font-size:0.72rem;padding-left:var(--space-3);">- ' + esc(data.issues[i]) + '</div>';
      }
    }
    if (data.suggested_fix) {
      html += '<div style="margin-top:var(--space-3);padding:var(--space-3);background:rgba(255,136,0,0.08);border-radius:var(--radius-sm);font-size:0.74rem;">';
      html += '<div style="color:var(--agent-lisa);font-weight:600;margin-bottom:var(--space-1);">Suggested Fix:</div>';
      html += '<div style="color:var(--text-secondary);">' + esc(data.suggested_fix) + '</div></div>';
    }
    html += '</div>';
    resultEl.innerHTML = html;
  } catch (e) { resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(e.message) + '</span>'; }
}

async function lisaReviewItem(itemId) {
  var spanEl = document.getElementById('ob-review-' + itemId);
  if (!spanEl) return;
  spanEl.innerHTML = '<span class="text-muted">Reviewing...</span>';
  try {
    var resp = await fetch('/api/lisa/review/' + encodeURIComponent(itemId), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({platform: 'instagram'})
    });
    var data = await resp.json();
    if (data.error) { spanEl.innerHTML = '<span style="color:#ff4444;font-size:0.72rem;">' + esc(data.error) + '</span>'; return; }
    var html = lisaScoreBadge(data.score);
    if (data.issues && data.issues.length > 0) {
      html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">' + esc(data.issues.join(', ')) + '</div>';
    }
    spanEl.innerHTML = html;
  } catch (e) { spanEl.innerHTML = '<span style="color:#ff4444;font-size:0.72rem;">Error</span>'; }
}

async function lisaMiniChat() {
  var input = document.getElementById('lisa-mini-chat-input');
  var messages = document.getElementById('lisa-mini-chat-messages');
  if (!input || !messages) return;
  var text = input.value.trim();
  if (!text) return;
  input.value = '';
  // Show user message
  if (messages.querySelector('.text-muted')) messages.innerHTML = '';
  messages.innerHTML += '<div style="margin-bottom:6px;"><span style="color:var(--text-muted);font-size:0.68rem;">You:</span> <span style="font-size:0.74rem;">' + esc(text) + '</span></div>';
  messages.innerHTML += '<div id="lisa-mini-typing" style="color:var(--agent-lisa);font-size:0.72rem;">Lisa is typing...</div>';
  messages.scrollTop = messages.scrollHeight;
  try {
    var resp = await fetch('/api/chat/agent/lisa', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    var data = await resp.json();
    var typing = document.getElementById('lisa-mini-typing');
    if (typing) typing.remove();
    var reply = data.content || data.reply || data.response || 'No response';
    messages.innerHTML += '<div style="margin-bottom:6px;"><span style="color:var(--agent-lisa);font-size:0.68rem;">Lisa:</span> <span style="font-size:0.74rem;">' + esc(reply) + '</span></div>';
    messages.scrollTop = messages.scrollHeight;
  } catch(e) {
    var typing = document.getElementById('lisa-mini-typing');
    if (typing) typing.textContent = 'Error: ' + e.message;
  }
}

async function loadLisaPlatformStatus() {
  var el = document.getElementById('lisa-platform-status');
  if (!el) return;
  try {
    var resp = await fetch('/api/lisa/platform-status');
    var data = await resp.json();
    var html = '';
    var names = {x:'X (Twitter)',tiktok:'TikTok',instagram:'Instagram'};
    ['x','tiktok','instagram'].forEach(function(plat) {
      var info = data[plat] || {};
      var connected = info.connected;
      var color = connected ? '#00ff44' : '#ff5555';
      var dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + color + ';margin-right:4px;box-shadow:0 0 4px ' + color + ';"></span>';
      html += '<div style="display:flex;align-items:center;gap:4px;font-size:0.74rem;">';
      html += dot + '<span style="color:' + color + ';">' + (names[plat] || plat) + '</span>';
      if (!connected && info.reason) html += '<span class="text-muted" style="font-size:0.65rem;">(' + esc(info.reason) + ')</span>';
      html += '</div>';
    });
    el.innerHTML = html;
  } catch(e) { el.innerHTML = ''; }
}

async function loadLisaPlan() {
  try {
    var resp = await fetch('/api/lisa/plan');
    var data = await resp.json();
    var el = document.getElementById('lisa-plan');
    if (data.error) { el.innerHTML = '<div class="text-muted">' + esc(data.error) + '</div>'; return; }
    var plan = data.plan || {};
    var phases = plan.phases || [];
    var html = '<div style="font-family:var(--font-mono);font-size:0.78rem;">';
    html += '<div style="color:var(--agent-lisa);font-weight:600;margin-bottom:var(--space-4);">Current Phase: ' + esc(plan.current_phase || '?') + '</div>';
    for (var i = 0; i < phases.length; i++) {
      var ph = phases[i];
      var isCurrent = ph.name === plan.current_phase;
      html += '<div style="padding:var(--space-3);margin-bottom:var(--space-2);border-left:2px solid ' + (isCurrent ? 'var(--agent-lisa)' : 'var(--border)') + ';padding-left:var(--space-4);">';
      html += '<div style="font-weight:600;color:' + (isCurrent ? 'var(--agent-lisa)' : 'var(--text-muted)') + ';">' + esc(ph.name || 'Phase ' + (i+1)) + '</div>';
      if (ph.goals) {
        var goals = ph.goals;
        if (typeof goals === 'object' && !Array.isArray(goals)) goals = Object.values(goals);
        if (Array.isArray(goals)) {
          for (var g = 0; g < goals.length; g++) {
            html += '<div style="color:var(--text-secondary);font-size:0.72rem;padding-left:var(--space-3);">- ' + esc(typeof goals[g] === 'string' ? goals[g] : JSON.stringify(goals[g])) + '</div>';
          }
        }
      }
      html += '</div>';
    }
    html += '</div>';
    var insights = data.insights || [];
    if (insights.length > 0) {
      html += '<div style="margin-top:var(--space-6);border-top:1px solid var(--border);padding-top:var(--space-4);">';
      html += '<div style="font-weight:600;color:var(--text-secondary);margin-bottom:var(--space-3);">Recent Insights</div>';
      for (var i = 0; i < insights.length && i < 5; i++) {
        html += '<div style="font-size:0.72rem;color:var(--text-muted);padding:2px 0;">' + esc(typeof insights[i] === 'string' ? insights[i] : insights[i].insight || JSON.stringify(insights[i])) + '</div>';
      }
      html += '</div>';
    }
    el.innerHTML = html;
  } catch (e) {}
}

async function loadLisaKnowledge() {
  try {
    var resp = await fetch('/api/lisa/knowledge');
    var data = await resp.json();
    var el = document.getElementById('lisa-knowledge');
    if (data.error) { el.innerHTML = '<div class="text-muted">' + esc(data.error) + '</div>'; return; }
    var sections = Object.keys(data);
    if (sections.length === 0) { el.innerHTML = '<div class="text-muted">No knowledge data.</div>'; return; }
    var html = '';
    for (var i = 0; i < sections.length; i++) {
      var key = sections[i];
      if (key === 'error') continue;
      var val = data[key];
      html += '<div class="expandable"><div class="expandable-header" onclick="this.parentElement.classList.toggle(&apos;open&apos;)">';
      html += '<span>' + esc(key.replace(/_/g, ' ')) + '</span>';
      html += '<span class="text-muted" style="font-size:0.7rem;">+</span></div>';
      html += '<div class="expandable-body"><div style="font-family:var(--font-mono);font-size:0.74rem;color:var(--text-secondary);white-space:pre-wrap;">';
      if (typeof val === 'string') { html += esc(val); }
      else if (Array.isArray(val)) { for (var j=0;j<val.length;j++) html += '- ' + esc(typeof val[j]==='string'?val[j]:JSON.stringify(val[j])) + '\\n'; }
      else { html += esc(JSON.stringify(val, null, 2)); }
      html += '</div></div></div>';
    }
    el.innerHTML = html;
  } catch (e) {}
}

async function lisaTestReply() {
  var input = document.getElementById('lisa-reply-input');
  var result = document.getElementById('lisa-reply-result');
  var comment = input.value.trim();
  if (!comment) return;
  result.textContent = 'Thinking...';
  try {
    var resp = await fetch('/api/lisa/reply', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({comment:comment})});
    var data = await resp.json();
    if (data.error) { result.textContent = 'Error: ' + data.error; return; }
    var txt = 'Category: ' + (data.category||'?') + '\\n';
    txt += 'Suggested Reply: ' + (data.reply||data.suggestion||'No reply generated') + '\\n';
    if (data.alternatives && data.alternatives.length > 0) {
      txt += '\\nAlternatives:\\n';
      for (var i=0;i<data.alternatives.length;i++) txt += '  ' + (i+1) + '. ' + data.alternatives[i] + '\\n';
    }
    result.textContent = txt;
  } catch (e) { result.textContent = 'Error: ' + e.message; }
}

// ── Pipeline, Rating, Writing, Timing, Algorithm panels ──

async function runPipeline() {
  var resultEl = document.getElementById('pipeline-results');
  if (resultEl) resultEl.innerHTML = '<span class="text-muted">Processing pending items...</span>';
  try {
    var resp = await fetch('/api/lisa/pipeline/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({platform:'x'})});
    var data = await resp.json();
    if (data.error) { resultEl.innerHTML = '<span style="color:#ff4444;">' + esc(data.error) + '</span>'; return; }
    var html = '<div style="margin-bottom:6px;">Processed: <strong>' + (data.processed||0) + '</strong> items</div>';
    if (data.auto_approved && data.auto_approved.length > 0) {
      html += '<div style="color:#22aa44;margin-bottom:4px;">Auto-approved: ' + data.auto_approved.length + '</div>';
      for (var i=0;i<data.auto_approved.length;i++) {
        var a = data.auto_approved[i];
        html += '<div style="padding-left:12px;font-size:0.72rem;color:var(--text-secondary);">' + esc(a.caption_preview) + ' (' + a.score + '/100)</div>';
      }
    }
    if (data.manual_review && data.manual_review.length > 0) {
      html += '<div style="color:#ffaa00;margin-bottom:4px;">Needs review: ' + data.manual_review.length + '</div>';
    }
    if (data.needs_work && data.needs_work.length > 0) {
      html += '<div style="color:#ff8800;margin-bottom:4px;">Needs work: ' + data.needs_work.length + '</div>';
    }
    if (data.auto_rejected && data.auto_rejected.length > 0) {
      html += '<div style="color:#ff4444;margin-bottom:4px;">Rejected: ' + data.auto_rejected.length + '</div>';
    }
    if (data.processed === 0) html = '<span class="text-muted">No pending items to process.</span>';
    resultEl.innerHTML = html;
    loadPipelineStats();
    loadJordanQueue();
  } catch (e) { if (resultEl) resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(e.message) + '</span>'; }
}

async function loadPipelineStats() {
  try {
    var resp = await fetch('/api/lisa/pipeline/stats');
    var data = await resp.json();
    if (data.error) return;
    var ba = data.by_action || {};
    document.getElementById('pipeline-approved').textContent = ba.auto_approved || 0;
    document.getElementById('pipeline-review').textContent = ba.flagged_for_review || 0;
    document.getElementById('pipeline-work').textContent = ba.returned_to_soren || 0;
    document.getElementById('pipeline-rejected').textContent = ba.auto_rejected || 0;
    var avgEl = document.getElementById('pipeline-avg-score');
    if (data.avg_score) avgEl.textContent = 'Avg score: ' + data.avg_score + '/100';

    // Render review queue items from queue_status
    var qs = data.queue_status || {};
    var reviewCount = qs.needs_review || 0;
    var queueEl = document.getElementById('pipeline-review-queue');
    if (reviewCount > 0) {
      queueEl.innerHTML = '<div style="font-size:0.74rem;color:var(--text-secondary);margin-bottom:4px;">' + reviewCount + ' item(s) awaiting manual review</div>';
    } else {
      queueEl.innerHTML = '';
    }
  } catch (e) {}
}

async function rateContent() {
  var caption = document.getElementById('rating-caption').value.trim();
  var platform = document.getElementById('rating-platform').value;
  var resultEl = document.getElementById('rating-result');
  if (!caption) { resultEl.innerHTML = '<span class="text-muted">Enter content to rate.</span>'; return; }
  resultEl.innerHTML = '<span class="text-muted">Rating...</span>';
  try {
    var resp = await fetch('/api/lisa/rate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({caption:caption, platform:platform})});
    var data = await resp.json();
    if (data.error) { resultEl.innerHTML = '<span style="color:#ff4444;">' + esc(data.error) + '</span>'; return; }
    var tierColors = {auto_approve:'#22aa44', manual_review:'#ffaa00', needs_work:'#ff8800', auto_reject:'#ff4444'};
    var tierLabels = {auto_approve:'AUTO-APPROVE', manual_review:'MANUAL REVIEW', needs_work:'NEEDS WORK', auto_reject:'AUTO-REJECT'};
    var tc = tierColors[data.tier] || '#888';
    var html = '<div style="padding:var(--space-3);border-left:3px solid ' + tc + ';">';
    html += '<div style="font-size:1.1rem;font-weight:700;color:' + tc + ';margin-bottom:6px;">' + (data.score||0) + '/100 ' + (tierLabels[data.tier]||data.tier) + '</div>';
    // Dimension bars
    var dims = data.dimension_scores || {};
    var dimNames = {brand_voice:'Brand Voice',hook_power:'Hook Power',engagement_potential:'Engagement',platform_fit:'Platform Fit',emotional_impact:'Emotional',authenticity:'Authenticity',pillar_relevance:'Pillar',timing_fit:'Timing'};
    html += '<div style="margin-bottom:8px;">';
    var dk = Object.keys(dimNames);
    for (var i=0;i<dk.length;i++) {
      var k = dk[i];
      var v = dims[k] || 0;
      var barColor = v >= 7 ? '#22aa44' : v >= 4 ? '#ffaa00' : '#ff4444';
      html += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">';
      html += '<span style="width:100px;font-size:0.68rem;color:var(--text-muted);text-align:right;">' + dimNames[k] + '</span>';
      html += '<div style="flex:1;height:8px;background:rgba(255,255,255,0.06);border-radius:4px;overflow:hidden;">';
      html += '<div style="width:' + (v*10) + '%;height:100%;background:' + barColor + ';border-radius:4px;"></div></div>';
      html += '<span style="font-size:0.68rem;color:' + barColor + ';width:20px;">' + v + '</span></div>';
    }
    html += '</div>';
    if (data.issues && data.issues.length > 0) {
      html += '<div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:4px;">Issues:</div>';
      for (var i=0;i<data.issues.length;i++) html += '<div style="font-size:0.7rem;color:var(--text-secondary);padding-left:8px;">- ' + esc(data.issues[i]) + '</div>';
    }
    if (data.suggested_improvements && data.suggested_improvements.length > 0) {
      html += '<div style="font-size:0.72rem;color:var(--agent-lisa);margin-top:6px;">Suggestions:</div>';
      for (var i=0;i<data.suggested_improvements.length;i++) html += '<div style="font-size:0.7rem;color:var(--text-secondary);padding-left:8px;">- ' + esc(data.suggested_improvements[i]) + '</div>';
    }
    html += '</div>';
    resultEl.innerHTML = html;
  } catch (e) { resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(e.message) + '</span>'; }
}

function toggleWriteFields() {}

async function generateWrite() {
  var writeType = document.getElementById('write-type').value;
  var topic = document.getElementById('write-topic').value.trim();
  var platform = document.getElementById('write-platform').value;
  var resultEl = document.getElementById('write-result');
  if (!topic) { resultEl.textContent = 'Enter a topic or theme.'; return; }
  resultEl.innerHTML = '<span class="text-muted">Generating...</span>';
  try {
    var body = {type: writeType, topic: topic, platform: platform};
    if (writeType === 'caption') body.pillar = 'dark_motivation';
    var resp = await fetch('/api/lisa/write', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    var data = await resp.json();
    if (data.error) { resultEl.innerHTML = '<span style="color:#ff4444;">' + esc(data.error) + '</span>'; return; }
    if (data.tweets) {
      var html = '<div style="color:var(--agent-lisa);font-weight:600;margin-bottom:8px;">X Thread (' + data.tweets.length + ' tweets)</div>';
      for (var i=0;i<data.tweets.length;i++) {
        html += '<div style="padding:8px 12px;margin-bottom:6px;background:rgba(255,136,0,0.05);border-left:2px solid var(--agent-lisa);border-radius:4px;font-size:0.76rem;">' + esc(data.tweets[i]) + '</div>';
      }
      resultEl.innerHTML = html;
    } else {
      resultEl.textContent = data.text || 'No output generated.';
    }
  } catch (e) { resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(e.message) + '</span>'; }
}

async function loadTimingPanel() {
  try {
    var resp = await fetch('/api/lisa/timing');
    var data = await resp.json();
    var el = document.getElementById('timing-panel');
    if (data.error) { el.innerHTML = '<div class="text-muted">' + esc(data.error) + '</div>'; return; }
    var platforms = Object.keys(data);
    if (platforms.length === 0) { el.innerHTML = '<div class="text-muted">No timing data.</div>'; return; }
    var platIcons = {x:'X', tiktok:'TikTok', instagram:'Instagram'};
    var html = '';
    for (var p=0;p<platforms.length;p++) {
      var plat = platforms[p];
      var pd = data[plat];
      html += '<div style="margin-bottom:12px;padding:8px 12px;border-left:2px solid var(--agent-lisa);border-radius:4px;">';
      html += '<div style="font-weight:600;font-size:0.78rem;color:var(--agent-lisa);margin-bottom:4px;">' + (platIcons[plat]||plat) + '</div>';
      var windows = pd.peak_windows || [];
      for (var w=0;w<windows.length;w++) {
        var win = windows[w];
        var hours = win.hours || [];
        var timeRange = hours.length > 0 ? hours[0] + ':00 - ' + (hours[hours.length-1]+1) + ':00' : '--';
        html += '<div style="font-size:0.72rem;color:var(--text-secondary);padding-left:8px;">';
        html += '<span style="color:#22aa44;">' + win.score + '</span> ' + timeRange + ' ET — ' + esc(win.label);
        html += '</div>';
      }
      if (pd.best_single_slot) {
        html += '<div style="font-size:0.7rem;color:var(--text-muted);padding-left:8px;margin-top:2px;">Best: ' + esc(pd.best_single_slot.label) + '</div>';
      }
      if (pd.critical_rule) {
        html += '<div style="font-size:0.68rem;color:var(--warning);padding-left:8px;margin-top:2px;">' + esc(pd.critical_rule) + '</div>';
      }
      html += '</div>';
    }
    el.innerHTML = html;
  } catch (e) {}
}

async function loadAlgorithmPanel() {
  try {
    var resp = await fetch('/api/lisa/knowledge');
    var data = await resp.json();
    var el = document.getElementById('algorithm-panel');
    if (data.error) { el.innerHTML = '<div class="text-muted">' + esc(data.error) + '</div>'; return; }
    var ps = data.platform_specific || {};
    var aw = data.algorithm_weights || {};
    var platforms = Object.keys(ps);
    if (platforms.length === 0) { el.innerHTML = '<div class="text-muted">No algorithm data.</div>'; return; }
    var platIcons = {x:'X', tiktok:'TikTok', instagram:'Instagram'};
    var html = '';
    for (var p=0;p<platforms.length;p++) {
      var plat = platforms[p];
      var pd = ps[plat];
      html += '<div class="expandable"><div class="expandable-header" onclick="this.parentElement.classList.toggle(&apos;open&apos;)">';
      html += '<span style="font-weight:600;">' + (platIcons[plat]||plat) + ' Algorithm</span>';
      html += '<span class="text-muted" style="font-size:0.7rem;">+</span></div>';
      html += '<div class="expandable-body"><div style="font-family:var(--font-mono);font-size:0.72rem;">';
      // Signals
      var signals = pd.algorithm_signals || [];
      if (signals.length > 0) {
        html += '<div style="color:var(--agent-lisa);margin-bottom:4px;">Algorithm Signals:</div>';
        for (var s=0;s<signals.length;s++) html += '<div style="color:var(--text-secondary);padding-left:8px;">- ' + esc(signals[s]) + '</div>';
      }
      // Key rules
      var rules = pd.key_rules || [];
      if (rules.length > 0) {
        html += '<div style="color:var(--agent-lisa);margin-top:6px;margin-bottom:4px;">Key Rules:</div>';
        for (var r=0;r<rules.length;r++) html += '<div style="color:var(--text-secondary);padding-left:8px;">- ' + esc(rules[r]) + '</div>';
      }
      // Weights
      var weights = aw[plat] || {};
      var wk = Object.keys(weights);
      if (wk.length > 0) {
        html += '<div style="color:var(--agent-lisa);margin-top:6px;margin-bottom:4px;">Signal Weights:</div>';
        for (var wi=0;wi<wk.length;wi++) {
          html += '<div style="color:var(--text-muted);padding-left:8px;">' + esc(wk[wi]) + ': <span style="color:var(--text-primary);">' + esc(String(weights[wk[wi]])) + '</span></div>';
        }
      }
      html += '</div></div></div>';
    }
    el.innerHTML = html;
  } catch (e) {}
}

// ── Inline Agent Chat (Soren & Lisa tabs) ──

function toggleInlineChat(agent) {
  var chatEl = document.getElementById(agent + '-inline-chat');
  if (!chatEl) return;
  var visible = chatEl.style.display !== 'none';
  chatEl.style.display = visible ? 'none' : 'block';
  if (!visible) {
    var inputEl = document.getElementById(agent + '-inline-chat-input');
    if (inputEl) inputEl.focus();
  }
}

async function sendInlineChat(agent) {
  var inputEl = document.getElementById(agent + '-inline-chat-input');
  var msgsEl = document.getElementById(agent + '-inline-chat-msgs');
  if (!inputEl || !msgsEl) return;
  var msg = inputEl.value.trim();
  if (!msg) return;
  inputEl.value = '';
  // Map agent name to API key
  var apiAgent = agent;
  var agentColor = AGENT_COLORS[apiAgent] || '#888';
  var agentName = AGENT_NAMES[apiAgent] || agent;
  // Clear placeholder
  var ph = msgsEl.querySelector('.text-muted');
  if (ph && msgsEl.children.length === 1) msgsEl.innerHTML = '';
  // Show user message
  msgsEl.innerHTML += '<div style="text-align:right;margin-bottom:4px;"><span style="background:rgba(255,255,255,0.08);padding:3px 8px;border-radius:6px;font-size:0.72rem;">' + esc(msg) + '</span></div>';
  msgsEl.innerHTML += '<div id="' + agent + '-chat-typing" style="margin-bottom:4px;"><span style="color:var(--text-muted);font-style:italic;font-size:0.72rem;">Thinking...</span></div>';
  msgsEl.scrollTop = msgsEl.scrollHeight;
  try {
    var resp = await fetch('/api/chat/agent/' + apiAgent, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    var data = await resp.json();
    var typing = document.getElementById(agent + '-chat-typing');
    if (typing) typing.remove();
    var history = data.history || [];
    var html = '';
    for (var i = 0; i < history.length; i++) {
      var m = history[i];
      if (m.role === 'user') {
        html += '<div style="text-align:right;margin-bottom:4px;"><span style="background:rgba(255,255,255,0.08);padding:3px 8px;border-radius:6px;font-size:0.72rem;">' + esc(m.content) + '</span></div>';
      } else {
        html += '<div style="margin-bottom:4px;"><span style="background:rgba(255,255,255,0.04);padding:3px 8px;border-radius:6px;font-size:0.72rem;border-left:2px solid ' + agentColor + ';">' + esc(m.content) + '</span></div>';
      }
    }
    msgsEl.innerHTML = html;
    msgsEl.scrollTop = msgsEl.scrollHeight;
  } catch(e) {
    var typing = document.getElementById(agent + '-chat-typing');
    if (typing) typing.innerHTML = '<span style="color:#ff4444;font-size:0.72rem;">Error: ' + esc(e.message) + '</span>';
  }
}

// ── Soren Tab Functions ──

var _sorenQueueFilter = 'all';
var _sorenQueueData = [];

async function sorenSendToLisa() {
  try {
    var resp = await fetch('/api/lisa/pipeline/run', {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({platform: 'instagram'})});
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    var msg = 'Pipeline processed ' + (data.processed || 0) + ' items';
    if (data.auto_approved) msg += ', ' + data.auto_approved.length + ' approved';
    if (data.manual_review) msg += ', ' + data.manual_review.length + ' for review';
    if (data.needs_work) msg += ', ' + data.needs_work.length + ' need work';
    if (data.auto_rejected) msg += ', ' + data.auto_rejected.length + ' rejected';
    alert(msg);
    refresh();
  } catch(e) { alert('Error: ' + e.message); }
}

function sorenCustomGenerate() {
  var genSection = document.querySelector('#soren-gen-prompt');
  if (genSection) genSection.scrollIntoView({behavior: 'smooth'});
}

async function sorenRefreshQueue() {
  refresh();
}

function sorenFilterQueue(filter, btn) {
  _sorenQueueFilter = filter;
  // Update button active states
  var btns = document.querySelectorAll('.soren-filter-btn');
  for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
  if (btn) btn.classList.add('active');
  // Re-render with filter
  renderSorenQueue(_sorenQueueData);
}

function renderSorenQueue(items) {
  _sorenQueueData = items || [];
  var el = document.getElementById('soren-queue');
  if (!el) return;
  var filtered = _sorenQueueData;
  if (_sorenQueueFilter !== 'all') {
    filtered = _sorenQueueData.filter(function(item) {
      return item.status === _sorenQueueFilter;
    });
  }
  if (filtered.length === 0) {
    el.innerHTML = '<div class="text-muted" style="padding:var(--space-4);text-align:center;">No items match filter "' + esc(_sorenQueueFilter) + '".</div>';
    return;
  }
  var html = '';
  var tierColors = {lisa_approved:'#22aa44', needs_review:'#ffaa00', needs_improvement:'#ff8800', jordan_approved:'#22aa44', rejected:'#ff4444', pending:'#888', posted:'#22aa44', approved:'#22aa44', generated:'#00d4ff', failed:'#ff4444'};
  for (var i = 0; i < filtered.length && i < 30; i++) {
    var item = filtered[i];
    var id = item.id || i;
    var status = item.status || 'pending';
    var sc = tierColors[status] || '#888';
    html += '<div class="queue-item" style="border-left:3px solid ' + sc + ';">';
    html += '<div class="queue-item-header"><span class="queue-item-title">' + esc(item.title || item.pillar || 'Content #' + id) + '</span>';
    html += '<div class="queue-item-badges"><span class="badge" style="background:' + sc + '22;color:' + sc + ';border:1px solid ' + sc + '44;">' + esc(status.replace(/_/g, ' ')) + '</span>';
    if (item.rating_score) html += '<span class="badge badge-neutral">' + item.rating_score + '/100</span>';
    if (item.platform) html += '<span class="badge badge-neutral">' + esc(item.platform) + '</span>';
    if (item.pillar) html += '<span class="badge badge-neutral">' + esc(item.pillar) + '</span>';
    html += '</div></div>';
    var caption = item.caption || item.content || '';
    if (caption) html += '<div class="queue-item-preview">' + esc(caption.substring(0, 140)) + '</div>';
    if (item.suggested_time) {
      html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">Scheduled: ' + esc(item.suggested_time) + '</div>';
    }
    html += '<div class="queue-item-actions">';
    if (status === 'pending') {
      html += '<button class="btn btn-primary" onclick="sorenGenerate(\'' + id + '\',\'full\')" style="font-size:0.7rem;">Generate</button>';
      html += '<button class="btn" onclick="sorenGenerate(\'' + id + '\',\'caption\')" style="font-size:0.7rem;">Caption Only</button>';
      html += '<button class="btn btn-success" onclick="sorenApprove(\'' + id + '\')" style="font-size:0.7rem;">Approve</button>';
      html += '<button class="btn btn-error" onclick="sorenReject(\'' + id + '\')" style="font-size:0.7rem;">Reject</button>';
    } else if (status === 'approved' || status === 'generated') {
      html += '<button class="btn btn-primary" onclick="sorenPreview(\'' + id + '\')" style="font-size:0.7rem;">Preview</button>';
      html += '<button class="btn" onclick="sorenDownload(\'' + id + '\')" style="font-size:0.7rem;">Download</button>';
      html += '<button class="btn" style="font-size:0.7rem;color:var(--agent-lisa);" onclick="sorenBrandCheck(\'' + id + '\',this)">Brand Check</button>';
    } else if (status === 'lisa_approved' || status === 'needs_review') {
      html += '<span style="font-size:0.68rem;color:#ffaa00;">Awaiting Jordan approval</span>';
    } else if (status === 'jordan_approved') {
      html += '<span style="font-size:0.68rem;color:#22aa44;">Scheduled for posting</span>';
    }
    html += '</div></div>';
  }
  el.innerHTML = html;
}

async function sorenGenerateCustom(mode) {
  var prompt = document.getElementById('soren-gen-prompt').value.trim();
  var pillar = document.getElementById('soren-gen-pillar').value;
  var resultEl = document.getElementById('soren-gen-result');
  resultEl.innerHTML = '<span class="text-muted">Generating...</span>';
  try {
    var resp = await fetch('/api/soren/custom-generate', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({prompt: prompt, pillar: pillar, mode: mode || 'full'})
    });
    var data = await resp.json();
    if (data.error) { resultEl.innerHTML = '<span style="color:#ff4444;">' + esc(data.error) + '</span>'; return; }
    var html = '<div style="padding:8px 12px;border-left:3px solid var(--agent-soren);margin-top:8px;">';
    if (data.caption) html += '<div style="font-size:0.76rem;color:var(--text-primary);margin-bottom:4px;white-space:pre-wrap;">' + esc(data.caption) + '</div>';
    if (data.title) html += '<div style="font-size:0.72rem;color:var(--text-muted);">Title: ' + esc(data.title) + '</div>';
    if (data.pillar) html += '<div style="font-size:0.72rem;color:var(--text-muted);">Pillar: ' + esc(data.pillar) + '</div>';
    if (data.id) html += '<div style="font-size:0.72rem;color:var(--text-muted);">ID: ' + esc(data.id) + '</div>';
    html += '</div>';
    resultEl.innerHTML = html;
    refresh();
  } catch(e) { resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(e.message) + '</span>'; }
}

async function loadPillarDistribution() {
  var el = document.getElementById('soren-pillar-dist');
  if (!el) return;
  try {
    var resp = await fetch('/api/soren');
    var data = await resp.json();
    var items = data.items || [];
    if (items.length === 0) { el.innerHTML = '<div class="text-muted">No content in queue.</div>'; return; }
    var counts = {};
    var total = items.length;
    for (var i = 0; i < items.length; i++) {
      var p = items[i].pillar || 'unknown';
      counts[p] = (counts[p] || 0) + 1;
    }
    var pillars = Object.keys(counts).sort(function(a,b) { return counts[b] - counts[a]; });
    var colors = ['#cc66ff','#ff6600','#00d4ff','#22aa44','#ffaa00','#ff4444','#ff8800','#8888ff','#44ccaa','#ff66aa'];
    var html = '';
    for (var i = 0; i < pillars.length; i++) {
      var p = pillars[i];
      var pct = Math.round(counts[p] / total * 100);
      var c = colors[i % colors.length];
      html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">';
      html += '<span style="width:120px;font-size:0.72rem;color:var(--text-muted);text-align:right;">' + esc(p.replace(/_/g, ' ')) + '</span>';
      html += '<div style="flex:1;height:12px;background:rgba(255,255,255,0.06);border-radius:6px;overflow:hidden;">';
      html += '<div style="width:' + pct + '%;height:100%;background:' + c + ';border-radius:6px;transition:width 0.3s;"></div></div>';
      html += '<span style="font-size:0.72rem;color:' + c + ';width:40px;">' + counts[p] + ' (' + pct + '%)</span>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) {}
}

// ── Lisa Tab — Jordan Approval Queue & Posting Schedule ──

var _jordanQueueFilter = 'all';

async function loadJordanQueue() {
  var el = document.getElementById('jordan-approval-queue');
  var countEl = document.getElementById('jordan-queue-count');
  if (!el) return;
  try {
    var resp = await fetch('/api/lisa/jordan-queue');
    var data = await resp.json();
    var allItems = data.items || [];

    // Platform filter
    var items = _jordanQueueFilter === 'all' ? allItems : allItems.filter(function(i) { return i.platform === _jordanQueueFilter; });
    var xCount = allItems.filter(function(i) { return i.platform === 'x'; }).length;
    var igCount = allItems.filter(function(i) { return i.platform === 'instagram' || i.platform === 'all'; }).length;

    if (countEl) countEl.textContent = items.length > 0 ? items.length + ' items' : '';

    // Filter tabs
    var html = '<div style="display:flex;gap:6px;margin-bottom:12px;">';
    var filters = [['all','All (' + allItems.length + ')'],['x','X (' + xCount + ')'],['instagram','IG (' + igCount + ')']];
    for (var f = 0; f < filters.length; f++) {
      var fk = filters[f][0], fl = filters[f][1];
      var active = _jordanQueueFilter === fk;
      html += '<button onclick="_jordanQueueFilter=\'' + fk + '\';loadJordanQueue();" style="font-size:0.72rem;padding:3px 10px;border-radius:99px;border:1px solid ' + (active ? '#1DA1F2' : 'rgba(255,255,255,0.1)') + ';background:' + (active ? 'rgba(29,161,242,0.15)' : 'transparent') + ';color:' + (active ? '#1DA1F2' : 'var(--text-muted)') + ';cursor:pointer;">' + fl + '</button>';
    }
    html += '</div>';

    if (items.length === 0) {
      html += '<div class="glass-card" style="text-align:center;padding:var(--space-6);"><div style="font-size:1rem;color:var(--text-muted);margin-bottom:4px;">No items in this filter</div><div style="font-size:0.72rem;color:var(--text-secondary);">Generate X content or run the pipeline to populate</div></div>';
      el.innerHTML = html;
      return;
    }

    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var isApproved = item.status === 'lisa_approved' || item.status === 'jordan_approved';
      var borderColor = isApproved ? '#22aa44' : '#ffaa00';
      var tierLabel = item.status === 'jordan_approved' ? 'APPROVED' : item.status === 'lisa_approved' ? 'LISA APPROVED' : 'NEEDS REVIEW';
      var platColor = item.platform === 'x' ? '#1DA1F2' : item.platform === 'instagram' ? '#E1306C' : 'var(--text-muted)';
      var platIcon = item.platform === 'x' ? '𝕏' : item.platform === 'instagram' ? 'IG' : item.platform === 'tiktok' ? 'TT' : '•';

      html += '<div class="glass-card" style="border-left:3px solid ' + borderColor + ';margin-bottom:8px;padding:10px 14px;">';

      // Top row: pillar + badges + score
      html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">';
      html += '<div>';
      html += '<div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center;">';
      html += '<span style="font-size:0.72rem;font-weight:700;color:' + platColor + ';padding:1px 6px;border:1px solid ' + platColor + '44;border-radius:4px;">' + platIcon + '</span>';
      html += '<span class="badge" style="background:' + borderColor + '22;color:' + borderColor + ';border:1px solid ' + borderColor + '44;font-size:0.66rem;">' + tierLabel + '</span>';
      if (item.pillar) html += '<span class="badge badge-neutral" style="font-size:0.66rem;">' + esc(item.pillar.replace(/_/g, ' ')) + '</span>';
      html += '</div></div>';
      if (item.rating_score) {
        var scoreColor = item.rating_score >= 80 ? '#22aa44' : item.rating_score >= 60 ? '#ffaa00' : '#ff4444';
        html += '<div style="text-align:right;"><div style="font-size:1.1rem;font-weight:700;color:' + scoreColor + ';">' + item.rating_score + '</div><div style="font-size:0.62rem;color:var(--text-muted);">/100</div></div>';
      }
      html += '</div>';

      // Caption preview
      if (item.caption) {
        html += '<div style="font-family:var(--font-mono);font-size:0.76rem;color:var(--text-primary);margin-bottom:8px;padding:8px 10px;background:rgba(255,255,255,0.04);border-radius:6px;border:1px solid rgba(255,255,255,0.06);">';
        html += esc(item.caption);
        html += '<div style="text-align:right;font-size:0.64rem;color:var(--text-muted);margin-top:4px;">' + (item.caption || '').length + ' chars</div>';
        html += '</div>';
      }

      // Rating dimensions as compact bar
      var dims = item.rating_dimensions || {};
      var dk = Object.keys(dims);
      if (dk.length > 0) {
        html += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;">';
        var dimNames = {brand_voice:'Voice',hook_power:'Hook',engagement_potential:'Engage',platform_fit:'Platform',emotional_impact:'Emotion',authenticity:'Auth',pillar_relevance:'Pillar',timing_fit:'Timing'};
        for (var d = 0; d < dk.length; d++) {
          var k = dk[d];
          var v = dims[k] || 0;
          var dc = v >= 7 ? '#22aa44' : v >= 4 ? '#ffaa00' : '#ff4444';
          html += '<span style="font-size:0.64rem;padding:1px 5px;border-radius:3px;background:' + dc + '15;color:' + dc + ';">' + (dimNames[k]||k) + ' ' + v + '</span>';
        }
        html += '</div>';
      }

      // Action buttons
      html += '<div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">';
      if (item.status !== 'jordan_approved') {
        html += '<button class="btn btn-primary" onclick="jordanApproveItem(\'' + esc(item.id) + '\',\'' + esc(item.platform || 'x') + '\')" style="font-size:0.72rem;padding:4px 12px;">Approve</button>';
      }
      if (item.platform === 'x' && (isApproved || item.status === 'needs_review')) {
        html += '<button class="btn" onclick="postNowToX(\'' + esc(item.id) + '\')" style="font-size:0.72rem;padding:4px 12px;border-color:#1DA1F2;color:#1DA1F2;">Post to X Now</button>';
      }
      html += '<button class="btn btn-error" onclick="jordanRejectItem(\'' + esc(item.id) + '\')" style="font-size:0.72rem;padding:4px 12px;">Reject</button>';
      html += '</div>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<div class="text-muted">Error loading queue: ' + esc(e.message) + '</div>'; }
}

async function jordanApproveItem(itemId, platform) {
  try {
    var resp = await fetch('/api/lisa/jordan-approve/' + encodeURIComponent(itemId), {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({platform: platform || 'x'})
    });
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    loadJordanQueue();
  } catch(e) { alert('Error: ' + e.message); }
}

async function jordanRejectItem(itemId) {
  var reason = prompt('Rejection reason (optional):') || '';
  try {
    var resp = await fetch('/api/lisa/pipeline/reject/' + encodeURIComponent(itemId), {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({reason: reason})
    });
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    refresh();
  } catch(e) { alert('Error: ' + e.message); }
}

async function loadPostingSchedule() {
  var el = document.getElementById('posting-schedule-timeline');
  var countEl = document.getElementById('posting-schedule-count');
  if (!el) return;
  try {
    var resp = await fetch('/api/lisa/posting-schedule');
    var data = await resp.json();
    var schedule = data.schedule || [];
    if (countEl) countEl.textContent = schedule.length > 0 ? schedule.length + ' scheduled' : '';
    var schedCountEl = document.getElementById('lisa-scheduled-count');
    if (schedCountEl) schedCountEl.textContent = data.count || schedule.length || 0;
    if (schedule.length === 0) {
      el.innerHTML = '<div class="glass-card" style="text-align:center;padding:var(--space-4);"><div style="font-size:0.76rem;color:var(--text-muted);">No posts scheduled yet</div><div style="font-size:0.7rem;color:var(--text-secondary);margin-top:2px;">Approve content from Jordan\'s queue to schedule posts</div></div>';
      return;
    }
    var html = '';
    var platIcons = {instagram:'IG', tiktok:'TT', x:'X'};
    for (var i = 0; i < schedule.length; i++) {
      var item = schedule[i];
      var timeStr = (item.scheduled_time || '').replace('T', ' ');
      html += '<div class="glass-card" style="border-left:3px solid #22aa44;margin-bottom:6px;padding:8px 12px;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
      html += '<div>';
      html += '<span style="font-size:0.78rem;font-weight:600;color:#22aa44;margin-right:8px;">' + esc(timeStr) + '</span>';
      html += '<span class="badge badge-neutral">' + (platIcons[item.platform] || esc(item.platform)) + '</span>';
      if (item.pillar) html += ' <span class="badge badge-neutral">' + esc(item.pillar.replace(/_/g, ' ')) + '</span>';
      html += '</div>';
      if (item.rating_score) {
        var sc = item.rating_score >= 80 ? '#22aa44' : '#ffaa00';
        html += '<span style="font-size:0.76rem;font-weight:600;color:' + sc + ';">' + item.rating_score + '/100</span>';
      }
      html += '</div>';
      if (item.caption) html += '<div style="font-size:0.72rem;color:var(--text-secondary);margin-top:4px;">' + esc(item.caption) + '</div>';
      if (item.scheduled_reason) html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">' + esc(item.scheduled_reason) + '</div>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<div class="text-muted">Error: ' + esc(e.message) + '</div>'; }
}

function renderSentinel(data) {
  // Overall status badge
  var statusEl = document.getElementById('rx-overall-status');
  if (data.error || data.status === 'offline') {
    if (statusEl) { statusEl.className = 'rx-status-badge rx-status-critical'; statusEl.querySelector('.rx-status-text').textContent = 'Offline'; }
    return;
  }
  var online = data.agents_online || 0;
  var total = data.agents_total || 0;
  var issues = data.active_issues || 0;
  var statusText = 'Healthy', statusClass = 'rx-status-healthy';
  if (issues > 2 || online < total - 2) { statusText = 'Critical'; statusClass = 'rx-status-critical'; }
  else if (issues > 0 || online < total) { statusText = 'Degraded'; statusClass = 'rx-status-degraded'; }
  if (statusEl) { statusEl.className = 'rx-status-badge ' + statusClass; statusEl.querySelector('.rx-status-text').textContent = statusText; }

  // Quick stats
  var el;
  el = document.getElementById('rx-stat-online'); if (el) { el.textContent = online + '/' + total; el.style.color = online === total ? 'var(--success)' : 'var(--warning)'; }
  el = document.getElementById('rx-stat-issues'); if (el) { el.textContent = issues; el.style.color = issues > 0 ? 'var(--error)' : 'var(--success)'; }
  el = document.getElementById('rx-stat-fixes'); if (el) el.textContent = data.total_fixes || 0;
  el = document.getElementById('rx-last-scan'); if (el) el.textContent = data.last_scan ? 'Last scan: ' + new Date(data.last_scan).toLocaleTimeString() : 'Last scan: --';

  // Summary
  el = document.getElementById('rx-summary');
  if (el) el.textContent = 'Watching ' + total + ' agents' + (issues === 0 ? ' \u2022 No critical issues' : ' \u2022 ' + issues + ' issue' + (issues > 1 ? 's' : '') + ' detected');

  // Error trend (mini-stat card format)
  var trendEl = document.getElementById('rx-error-trend');
  if (trendEl) {
    var valEl = trendEl.querySelector('.rx-mini-val');
    if (valEl && data.error_trend) {
      var et = data.error_trend;
      var arrow = et.direction === 'up' ? '\u25B2' : et.direction === 'down' ? '\u25BC' : '\u25AC';
      var color = et.spike ? 'var(--error)' : et.direction === 'up' ? 'var(--warning)' : et.direction === 'down' ? 'var(--success)' : 'var(--text-secondary)';
      valEl.innerHTML = '<span style="color:' + color + ';">' + arrow + '</span>';
      if (et.spike) valEl.innerHTML += ' <span style="font-size:0.6rem;color:var(--error);">SPIKE</span>';
    } else if (valEl) {
      valEl.innerHTML = '<span style="color:var(--success);">\u25AC</span>';
    }
  }

  // Alerts
  var alerts = data.recent_alerts || [];
  var alertEl = document.getElementById('sentinel-alerts');
  if (alertEl) {
    if (alerts.length === 0) {
      alertEl.innerHTML = '<div class="feed-item"><span class="badge badge-success">OK</span> <span style="font-family:var(--font-mono);font-size:0.76rem;color:var(--text-secondary);margin-left:8px;">All systems nominal</span></div>';
    } else {
      var html = '';
      for (var i = alerts.length - 1; i >= 0 && i >= alerts.length - 10; i--) {
        var a = alerts[i];
        var bcls = a.severity === 'critical' ? 'badge-error' : a.severity === 'warning' ? 'badge-warning' : 'badge-success';
        html += '<div class="feed-item"><span class="badge ' + bcls + '">' + esc(a.severity||'info') + '</span>';
        html += ' <span style="font-family:var(--font-mono);font-size:0.76rem;color:var(--text-secondary);margin-left:8px;">' + esc(a.message||'') + '</span>';
        html += ' <span class="text-muted" style="font-size:0.68rem;margin-left:8px;">' + esc(a.timestamp ? new Date(a.timestamp).toLocaleTimeString() : '') + '</span></div>';
      }
      alertEl.innerHTML = html;
    }
  }

  // Fixes
  var fixes = data.recent_fixes || [];
  var fixEl = document.getElementById('sentinel-fixes-tbody');
  if (fixEl) {
    if (fixes.length === 0) {
      fixEl.innerHTML = '<tr><td colspan="4" class="text-muted" style="text-align:center;padding:24px;">No fixes yet</td></tr>';
    } else {
      var html = '';
      for (var i = fixes.length - 1; i >= 0; i--) {
        var f = fixes[i];
        html += '<tr><td>' + esc(f.timestamp ? new Date(f.timestamp).toLocaleTimeString() : '--') + '</td>';
        html += '<td style="font-weight:600;text-transform:capitalize;">' + esc(f.agent||'--') + '</td>';
        html += '<td>' + esc(f.action||'--') + '</td>';
        html += '<td><span class="badge ' + (f.success ? 'badge-success' : 'badge-error') + '">' + (f.success ? 'Fixed' : 'Failed') + '</span></td></tr>';
      }
      fixEl.innerHTML = html;
    }
    var fb = document.getElementById('rx-fixes-badge');
    if (fb && fixes.length > 0) { fb.textContent = fixes.length; fb.style.background = 'rgba(0,255,136,0.12)'; fb.style.color = '#00ff88'; }
  }
}

/* ── Robotox Radar Chart ── */
function rxRenderRadar(dims) {
  var svg = document.getElementById('rx-radar-svg');
  if (!svg) return;
  var cx = 140, cy = 125, r = 90;
  var n = dims.length;
  var angleStep = (2 * Math.PI) / n;
  var startAngle = -Math.PI / 2;

  function polar(angle, radius) {
    return { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
  }

  var html = '';
  // Grid rings (3 levels: 33%, 66%, 100%)
  for (var level = 1; level <= 3; level++) {
    var lr = r * level / 3;
    var pts = [];
    for (var i = 0; i < n; i++) {
      var p = polar(startAngle + i * angleStep, lr);
      pts.push(p.x.toFixed(1) + ',' + p.y.toFixed(1));
    }
    html += '<polygon points="' + pts.join(' ') + '" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>';
  }

  // Axis lines
  for (var i = 0; i < n; i++) {
    var p = polar(startAngle + i * angleStep, r);
    html += '<line x1="' + cx + '" y1="' + cy + '" x2="' + p.x.toFixed(1) + '" y2="' + p.y.toFixed(1) + '" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>';
  }

  // Data polygon
  var dataPts = [];
  for (var i = 0; i < n; i++) {
    var val = Math.max(0, Math.min(100, dims[i].value || 0));
    var p = polar(startAngle + i * angleStep, r * val / 100);
    dataPts.push(p.x.toFixed(1) + ',' + p.y.toFixed(1));
  }
  html += '<polygon points="' + dataPts.join(' ') + '" fill="rgba(0,255,68,0.1)" stroke="rgba(0,255,68,0.6)" stroke-width="2"/>';

  // Data points + labels
  for (var i = 0; i < n; i++) {
    var val = Math.max(0, Math.min(100, dims[i].value || 0));
    var p = polar(startAngle + i * angleStep, r * val / 100);
    html += '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="3" fill="#00ff44"/>';

    // Labels
    var lp = polar(startAngle + i * angleStep, r + 22);
    var anchor = 'middle';
    if (lp.x < cx - 10) anchor = 'end';
    else if (lp.x > cx + 10) anchor = 'start';
    html += '<text x="' + lp.x.toFixed(1) + '" y="' + lp.y.toFixed(1) + '" text-anchor="' + anchor + '" font-family="monospace" font-size="9" fill="#8888aa" dominant-baseline="middle">' + esc(dims[i].name) + '</text>';
    html += '<text x="' + lp.x.toFixed(1) + '" y="' + (lp.y + 12).toFixed(1) + '" text-anchor="' + anchor + '" font-family="monospace" font-size="10" font-weight="700" fill="' + (val >= 70 ? '#00ff88' : val >= 40 ? '#ffaa00' : '#ff4444') + '" dominant-baseline="middle">' + val + '</text>';
  }

  svg.innerHTML = html;
}

/* ── Robotox Score Ring ── */
function rxRenderScore(score) {
  var arc = document.getElementById('rx-score-arc');
  var num = document.getElementById('rx-score-num');
  var lbl = document.getElementById('rx-score-lbl');
  if (!arc || !num || !lbl) return;

  score = Math.max(0, Math.min(100, Math.round(score)));
  var circumference = 2 * Math.PI * 52;
  var dash = (score / 100) * circumference;
  arc.setAttribute('stroke-dasharray', dash.toFixed(1) + ' ' + circumference.toFixed(1));

  // Color by score
  var color = score >= 80 ? '#00ff88' : score >= 60 ? '#00d4ff' : score >= 40 ? '#ffaa00' : '#ff4444';
  arc.setAttribute('stroke', color);

  num.textContent = score;
  num.style.color = color;

  var label = score >= 90 ? 'Elite' : score >= 76 ? 'Expert' : score >= 61 ? 'Skilled' : score >= 41 ? 'Fair' : score >= 21 ? 'Poor' : 'Critical';
  lbl.textContent = label;
}

/* ── Load Intelligence (Radar + Score) ── */
async function rxLoadIntelligence(sentinelData) {
  try {
    var resp = await fetch('/api/robotox/scorecards');
    var data = await resp.json();
    var agents = data.agents || {};

    // Compute dimensions from real data
    var fixSuccessRates = [];
    var uptimes = [];
    var restartCounts = 0;
    var totalAgents = Object.keys(agents).length || 1;

    for (var key in agents) {
      var a = agents[key];
      var u = a.uptime || {};
      if (u.uptime_pct !== undefined) uptimes.push(u.uptime_pct);
      if (u.fix_success_rate !== undefined && u.restart_count > 0) fixSuccessRates.push(u.fix_success_rate);
      restartCounts += (u.restart_count || 0);
    }

    var avgUptime = uptimes.length > 0 ? uptimes.reduce(function(a,b){return a+b;},0) / uptimes.length : 0;
    var avgFixRate = fixSuccessRates.length > 0 ? fixSuccessRates.reduce(function(a,b){return a+b;},0) / fixSuccessRates.length : 50;

    // Update uptime stat
    var uptimeEl = document.getElementById('rx-stat-uptime');
    if (uptimeEl) uptimeEl.textContent = avgUptime.toFixed(1) + '%';

    var online = sentinelData.agents_online || 0;
    var total = sentinelData.agents_total || 1;
    var issues = sentinelData.active_issues || 0;

    var dims = [
      { name: 'Auto-Fix', value: Math.round(avgFixRate) },
      { name: 'Coverage', value: Math.round(online / total * 100) },
      { name: 'Detection', value: Math.min(100, Math.round(70 + restartCounts * 2)) },
      { name: 'Response', value: Math.round(Math.max(0, 100 - issues * 15)) },
      { name: 'Vigilance', value: Math.round(avgUptime) }
    ];

    rxRenderRadar(dims);

    var overallScore = Math.round(dims.reduce(function(s,d){return s+d.value;},0) / dims.length);
    rxRenderScore(overallScore);

  } catch(e) {
    console.error('rxLoadIntelligence:', e);
    rxRenderRadar([
      {name:'Auto-Fix',value:50},{name:'Coverage',value:50},{name:'Detection',value:50},
      {name:'Response',value:50},{name:'Vigilance',value:50}
    ]);
    rxRenderScore(50);
  }
}

/* ── Load Trade Guard ── */
async function rxLoadTradeGuard() {
  try {
    const resp = await fetch('/api/robotox/trade-guard');
    const data = await resp.json();
    const el = document.getElementById('rx-trade-guard-content');
    const badge = document.getElementById('rx-tg-badge');
    if (!el) return;

    const agents = ['garves', 'hawk', 'odin'];
    let allReady = true;
    let cards = '';

    for (const agentId of agents) {
      const r = data[agentId];
      if (!r) continue;

      const ready = r.ready;
      const score = r.score || 0;
      const stale = r.stale;
      const blocked = r.blocked_reason;
      const checks = r.checks || {};
      const age = r.age_s || 0;

      if (!ready) allReady = false;

      const color = ready ? 'var(--success)' : 'var(--danger)';
      const statusText = stale ? 'STALE' : (ready ? 'READY' : 'BLOCKED');
      const statusColor = stale ? 'var(--warning)' : color;

      let checkItems = '';
      for (const [k, v] of Object.entries(checks)) {
        if (typeof v === 'boolean') {
          const icon = v ? '&#10003;' : '&#10007;';
          const c = v ? 'var(--success)' : 'var(--danger)';
          checkItems += '<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:0.72rem;">'
            + '<span style="color:var(--text-secondary);">' + k.replace(/_/g, ' ') + '</span>'
            + '<span style="color:' + c + ';font-weight:600;">' + icon + '</span></div>';
        } else if (typeof v === 'number') {
          checkItems += '<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:0.72rem;">'
            + '<span style="color:var(--text-secondary);">' + k.replace(/_/g, ' ') + '</span>'
            + '<span style="color:var(--text-primary);font-family:var(--font-mono);">' + v + '</span></div>';
        }
      }

      cards += '<div style="flex:1;min-width:220px;background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;border-left:3px solid ' + color + ';">'
        + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
        + '<span style="font-weight:700;text-transform:uppercase;font-size:0.82rem;">' + agentId + '</span>'
        + '<span style="font-size:0.7rem;padding:2px 8px;border-radius:10px;font-weight:600;background:' + statusColor + '20;color:' + statusColor + ';">' + statusText + '</span>'
        + '</div>'
        + '<div style="display:flex;align-items:baseline;gap:6px;margin-bottom:8px;">'
        + '<span style="font-size:1.6rem;font-weight:800;color:' + color + ';">' + score + '</span>'
        + '<span style="font-size:0.72rem;color:var(--text-muted);">/100</span>'
        + '</div>'
        + (blocked ? '<div style="font-size:0.7rem;color:var(--danger);margin-bottom:6px;padding:4px 6px;background:rgba(255,0,0,0.08);border-radius:4px;">' + blocked + '</div>' : '')
        + checkItems
        + '<div style="font-size:0.65rem;color:var(--text-muted);margin-top:6px;">Updated ' + Math.round(age) + 's ago</div>'
        + '</div>';
    }

    el.innerHTML = '<div style="display:flex;gap:12px;flex-wrap:wrap;">' + cards + '</div>';

    if (badge) {
      badge.textContent = allReady ? 'ALL CLEAR' : 'BLOCKED';
      badge.style.color = allReady ? 'var(--success)' : 'var(--danger)';
    }

  } catch (e) {
    const el = document.getElementById('rx-trade-guard-content');
    if (el) el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">Trade Guard data unavailable</div>';
  }
}

/* ── Load Predictive Monitors ── */
async function rxLoadPredictive() {
  var el = document.getElementById('rx-predictive-content');
  var badge = document.getElementById('rx-pred-badge');
  if (!el) return;
  try {
    var resp = await fetch('/api/robotox/predictive');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">' + esc(data.error) + '</span>'; return; }

    var leaks = data.memory_leaks || [];
    var accels = data.error_acceleration || [];
    var rots = data.log_rotations || [];

    // Badge
    if (badge) {
      var total = leaks.length + accels.length;
      if (total > 0) {
        var hasCrit = leaks.some(function(l){return l.severity==='critical';}) || accels.some(function(a){return a.severity==='critical';});
        badge.textContent = total + (hasCrit ? ' CRITICAL' : ' WARNING');
        badge.style.background = hasCrit ? 'rgba(255,68,68,0.15)' : 'rgba(255,170,0,0.12)';
        badge.style.color = hasCrit ? '#ff4444' : '#ffaa00';
      } else {
        badge.textContent = 'ALL CLEAR';
        badge.style.background = 'rgba(0,255,136,0.1)';
        badge.style.color = '#00ff88';
      }
    }

    var html = '<div class="rx-pred-row">';

    // Memory Leaks Card
    html += '<div class="rx-pred-card">';
    html += '<div class="rx-pred-title">Memory Leaks</div>';
    if (leaks.length === 0) {
      html += '<div class="rx-pred-status" style="color:var(--success);">None detected</div>';
    } else {
      for (var i = 0; i < leaks.length; i++) {
        var l = leaks[i];
        html += '<div class="rx-pred-status" style="color:' + (l.severity === 'critical' ? 'var(--error)' : 'var(--warning)') + ';">' + esc(l.agent.toUpperCase()) + ' +' + l.slope_mb_hr + ' MB/hr</div>';
        html += '<div class="rx-pred-detail">R\u00B2=' + l.r_squared + ' | ' + l.current_mem_mb + 'MB now' + (l.hours_to_2gb ? ' | 2GB in ' + l.hours_to_2gb + 'h' : '') + '</div>';
      }
    }
    html += '</div>';

    // Error Acceleration Card
    html += '<div class="rx-pred-card">';
    html += '<div class="rx-pred-title">Error Acceleration</div>';
    if (accels.length === 0) {
      html += '<div class="rx-pred-status" style="color:var(--success);">Stable</div>';
    } else {
      for (var i = 0; i < accels.length; i++) {
        var a = accels[i];
        html += '<div class="rx-pred-status" style="color:' + (a.severity === 'critical' ? 'var(--error)' : 'var(--warning)') + ';">' + esc(a.agent.toUpperCase()) + '</div>';
        html += '<div class="rx-pred-detail">' + a.window_counts.join(' \u2192 ') + ' per window | accel +' + a.acceleration + '/w\u00B2</div>';
      }
    }
    html += '</div>';

    // Log Rotations Card
    html += '<div class="rx-pred-card">';
    html += '<div class="rx-pred-title">Log Rotation</div>';
    if (rots.length === 0) {
      html += '<div class="rx-pred-status" style="color:var(--text-secondary);">No rotations needed</div>';
    } else {
      for (var i = 0; i < rots.length; i++) {
        html += '<div class="rx-pred-detail">' + esc(rots[i].agent) + ': ' + rots[i].size_mb + 'MB rotated</div>';
      }
    }
    html += '</div></div>';

    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span class="text-muted">Error: ' + esc(e.message) + '</span>'; }
}

/* ── Load Quiet Hours Status ── */
async function rxLoadQuietHours() {
  try {
    var resp = await fetch('/api/robotox/quiet-hours');
    var data = await resp.json();
    var tag = document.getElementById('rx-quiet-tag');
    if (tag) {
      if (data.is_quiet_hours) {
        tag.style.display = 'inline';
        if (data.pending_alerts > 0) tag.textContent = 'QUIET HOURS (' + data.pending_alerts + ' batched)';
        else tag.textContent = 'QUIET HOURS';
      } else {
        tag.style.display = 'none';
      }
    }
  } catch(e) { console.debug('rxLoadQuietHours:', e); }
}

/* ── Load Live Status Pills ── */
async function rxLoadLivePills() {
  var el = document.getElementById('rx-live-pills');
  if (!el) return;
  try {
    var resp = await fetch('/api/robotox/dep-health');
    var data = await resp.json();
    var deps = data.dependencies || data.checks || [];
    if (!Array.isArray(deps) && typeof deps === 'object') {
      deps = Object.keys(deps).map(function(k) { var d = deps[k]; d.name = d.name || k; return d; });
    }
    if (deps.length === 0) { el.innerHTML = '<span class="text-muted" style="font-size:0.68rem;">No dependency data</span>'; return; }
    var html = '';
    for (var i = 0; i < deps.length; i++) {
      var d = deps[i];
      var ok = d.status === 'ok' || d.status === 'healthy' || d.reachable === true;
      var warn = d.status === 'degraded' || d.status === 'slow';
      var cls = ok ? 'rx-pill-ok' : warn ? 'rx-pill-warn' : 'rx-pill-err';
      var name = d.name || d.dependency || ('Dep ' + i);
      var latency = d.latency_ms ? ' ' + d.latency_ms + 'ms' : '';
      html += '<span class="rx-pill ' + cls + '" title="' + esc(name) + latency + '"><span class="rx-pill-dot"></span>' + esc(name) + '</span>';
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span class="text-muted" style="font-size:0.68rem;">Deps unavailable</span>'; }
}

/* ── Export Report ── */
function rxExportReport() {
  var parts = ['ROBOTOX HEALTH REPORT', '=' .repeat(40), 'Generated: ' + new Date().toLocaleString(), ''];

  var status = document.getElementById('rx-overall-status');
  if (status) parts.push('Status: ' + (status.querySelector('.rx-status-text') || {}).textContent);

  var online = document.getElementById('rx-stat-online');
  var issues = document.getElementById('rx-stat-issues');
  var fixes = document.getElementById('rx-stat-fixes');
  var uptime = document.getElementById('rx-stat-uptime');
  parts.push('Agents: ' + (online ? online.textContent : '--'));
  parts.push('Issues: ' + (issues ? issues.textContent : '--'));
  parts.push('Fixes: ' + (fixes ? fixes.textContent : '--'));
  parts.push('Uptime: ' + (uptime ? uptime.textContent : '--'));
  var lost = document.getElementById('rx-stat-lost-opp');
  parts.push('Est. Lost: ' + (lost ? lost.textContent : '--'));
  parts.push('');

  var trend = document.getElementById('rx-error-trend');
  if (trend) parts.push('Error Trend: ' + trend.textContent.trim());
  parts.push('');

  var text = parts.join('\n');
  try {
    navigator.clipboard.writeText(text);
    var el = document.getElementById('sentinel-report');
    if (el) { el.style.display = 'block'; el.textContent = 'Report copied to clipboard!\n\n' + text; }
  } catch(e) {
    var el = document.getElementById('sentinel-report');
    if (el) { el.style.display = 'block'; el.textContent = text; }
  }
}

/* ── Force Self-Heal All ── */
async function rxForceHealAll() {
  var el = document.getElementById('sentinel-report');
  if (el) { el.style.display = 'block'; el.textContent = 'Forcing self-heal on all agents...'; }
  try {
    var resp = await fetch('/api/sentinel/scan', {method:'POST'});
    var data = await resp.json();
    if (data.error) { if (el) el.textContent = 'Error: ' + data.error; return; }
    var fixes = data.fixes_applied || [];
    var txt = 'FORCE SELF-HEAL RESULTS\n' + '='.repeat(30) + '\n';
    txt += 'Time: ' + new Date().toLocaleString() + '\n\n';
    if (fixes.length === 0) {
      txt += 'No issues found — all agents healthy.\n';
    } else {
      for (var i = 0; i < fixes.length; i++) {
        var f = fixes[i];
        txt += (f.agent||'?').toUpperCase() + ': ' + (f.action||'?') + ' -> ' + (f.success ? 'FIXED' : 'FAILED') + '\n';
      }
    }
    var agents = data.agents || {};
    var down = [];
    for (var k in agents) { if (agents[k] && !agents[k].alive) down.push(k); }
    if (down.length > 0) txt += '\nStill down: ' + down.join(', ').toUpperCase() + '\n';
    else txt += '\nAll agents online.\n';
    if (el) el.textContent = txt;
    refresh();
  } catch(e) { if (el) el.textContent = 'Error: ' + (e.message || e); }
}

/* ── Load PnL Impact (Est. Lost Today) ── */
async function rxLoadPnlImpact() {
  try {
    var resp = await fetch('/api/robotox/pnl');
    var data = await resp.json();
    if (data.error) return;
    var rev = data.revenue || {};
    var totalLost = 0;
    for (var agent in rev) {
      var a = rev[agent];
      if (a && typeof a.estimated_lost_opportunity === 'number') totalLost += a.estimated_lost_opportunity;
      else if (a && typeof a.lost_opportunity === 'number') totalLost += a.lost_opportunity;
    }
    var el = document.getElementById('rx-stat-lost-opp');
    if (el) {
      el.textContent = '$' + totalLost.toFixed(2);
      el.style.color = totalLost > 10 ? 'var(--error)' : totalLost > 0 ? 'var(--warning)' : 'var(--success)';
    }
  } catch(e) { console.debug('rxLoadPnlImpact:', e); }
}

async function sentinelScan() {
  var el = document.getElementById('sentinel-report');
  el.style.display = 'block';
  el.textContent = 'Running health scan...';
  try {
    var resp = await fetch('/api/sentinel/scan', {method:'POST'});
    var data = await resp.json();
    if (data.error) { el.textContent = 'Error: ' + data.error; return; }
    var txt = 'HEALTH SCAN RESULTS\\n' + '='.repeat(30) + '\\n';
    txt += 'Time: ' + (data.timestamp||'now') + '\\n\\n';
    var agents = data.agents || {};
    var akeys = Object.keys(agents);
    for (var i=0;i<akeys.length;i++) {
      var ak = akeys[i];
      var ag = agents[ak];
      txt += ak.toUpperCase() + ': ' + (ag.alive ? 'ONLINE' : 'DOWN') + (ag.pids ? ' (PID: ' + ag.pids.join(',') + ')' : '') + '\\n';
    }
    txt += '\\nPorts:\\n';
    var ports = data.ports || {};
    var pkeys = Object.keys(ports);
    for (var i=0;i<pkeys.length;i++) {
      txt += '  :' + pkeys[i] + ' -> ' + (ports[pkeys[i]].status||'?') + ' (' + (ports[pkeys[i]].agent||'?') + ')\\n';
    }
    if (data.issues && data.issues.length > 0) {
      txt += '\\nISSUES:\\n';
      for (var i=0;i<data.issues.length;i++) txt += '  [' + (data.issues[i].severity||'?') + '] ' + (data.issues[i].message||'') + '\\n';
    }
    if (data.fixes_applied && data.fixes_applied.length > 0) {
      txt += '\\nFIXES APPLIED:\\n';
      for (var i=0;i<data.fixes_applied.length;i++) txt += '  ' + (data.fixes_applied[i].action||'') + ' -> ' + (data.fixes_applied[i].success ? 'OK' : 'FAILED') + '\\n';
    }
    var sys = data.system || {};
    txt += '\\nSYSTEM: ' + (sys.status||'ok') + ' | Disk: ' + (sys.disk_free_gb||'?') + 'GB free | Load: ' + ((sys.load_avg||[]).join(', ')||'?') + '\\n';
    el.textContent = txt;
    refresh();
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}

async function sentinelBugs() {
  var el = document.getElementById('sentinel-report');
  el.style.display = 'block';
  el.textContent = 'Scanning for bugs...';
  try {
    var resp = await fetch('/api/sentinel/bugs');
    var data = await resp.json();
    if (data.error) { el.textContent = 'Error: ' + data.error; return; }
    var results = data.results || data.bugs || data;
    if (typeof results === 'object' && !Array.isArray(results)) {
      var txt = 'BUG SCAN RESULTS\\n' + '='.repeat(30) + '\\n\\n';
      var keys = Object.keys(results);
      for (var i=0;i<keys.length;i++) {
        var k = keys[i];
        var items = results[k];
        if (Array.isArray(items) && items.length > 0) {
          txt += k.toUpperCase() + ' (' + items.length + '):\\n';
          for (var j=0;j<items.length&&j<10;j++) {
            var it = items[j];
            txt += '  ' + (it.file||'?') + ':' + (it.line||'?') + ' - ' + (it.message||it.issue||JSON.stringify(it)) + '\\n';
          }
          txt += '\\n';
        }
      }
      el.textContent = txt || 'No bugs found.';
    } else {
      el.textContent = JSON.stringify(results, null, 2);
    }
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}


function toggleScheduleDetail(id) {
  var el = document.getElementById(id);
  if (el) el.classList.toggle('visible');
}

function toggleTaskInput(agent) {
  var el = document.getElementById('task-input-' + agent);
  if (el) el.classList.toggle('visible');
}

async function submitAgentTask(agent) {
  var input = document.getElementById('task-text-' + agent);
  var text = input ? input.value.trim() : '';
  if (!text) return;
  try {
    var resp = await fetch('/api/shelby/tasks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title: text, agent: agent})
    });
    var data = await resp.json();
    if (data.success) {
      input.value = '';
      var el = document.getElementById('task-input-' + agent);
      if (el) el.classList.remove('visible');
      var sr = await fetch('/api/shelby');
      renderShelby(await sr.json());
    } else {
      alert('Error: ' + (data.error || 'Unknown'));
    }
  } catch (e) { alert('Error: ' + e.message); }
}

async function loadActivityBrief() {
  try {
    var resp = await fetch('/api/shelby/activity-brief');
    var data = await resp.json();
    var el = document.getElementById('shelby-activity-brief');
    var html = '';
    var g = data.garves || {};
    html += '<div class="activity-item"><span class="activity-agent" style="color:var(--agent-garves);">Garves</span>';
    html += '<span class="activity-text">' + (g.trades_30m || 0) + ' trades (W:' + (g.wins || 0) + ' L:' + (g.losses || 0) + ')</span></div>';
    var s = data.soren || {};
    html += '<div class="activity-item"><span class="activity-agent" style="color:var(--agent-soren);">Soren</span>';
    html += '<span class="activity-text">' + (s.pending || 0) + ' pending, ' + (s.generated || 0) + ' generated</span></div>';
    var at = data.atlas || {};
    html += '<div class="activity-item"><span class="activity-agent" style="color:var(--agent-atlas);">Atlas</span>';
    html += '<span class="activity-text">State: ' + esc(at.state || 'idle') + ' | Cycles: ' + (at.cycles || 0) + '</span></div>';
    var m = data.lisa || {};
    html += '<div class="activity-item"><span class="activity-agent" style="color:var(--agent-lisa);">Lisa</span>';
    var mTxt = (m.posts_30m || 0) + ' posts in last 30 min';
    if (m.review_avg !== null && m.review_avg !== undefined) mTxt += ' | Review avg: ' + m.review_avg + '/10 (' + (m.reviews_total||0) + ' reviewed)';
    html += '<span class="activity-text">' + mTxt + '</span></div>';
    var sn = data.sentinel || {};
    html += '<div class="activity-item"><span class="activity-agent" style="color:var(--agent-sentinel);">Robotox</span>';
    html += '<span class="activity-text">Status: ' + esc(sn.status || 'idle') + '</span></div>';
    el.innerHTML = html;
  } catch (e) {}
}

async function loadSystemInfo() {
  try {
    var resp = await fetch('/api/shelby/system');
    var data = await resp.json();
    // System info
    var sysEl = document.getElementById('shelby-system-info');
    var load = data.load_avg || [0,0,0];
    var mem = data.memory || {};
    var disk = data.disk || {};
    var updates = data.updates || [];
    var html = '<div style="font-family:var(--font-mono);font-size:0.78rem;font-weight:600;color:var(--text-secondary);margin-bottom:var(--space-3);">macOS System</div>';
    html += '<div class="system-row"><span class="system-label">CPU Load</span><span class="system-value">' + load[0].toFixed(2) + ' / ' + load[1].toFixed(2) + ' / ' + load[2].toFixed(2) + '</span></div>';
    html += '<div class="system-row"><span class="system-label">Memory</span><span class="system-value">' + (mem.used_pct >= 0 ? mem.used_pct + '% used' : 'N/A');
    if (mem.total_gb) html += ' (' + mem.total_gb + ' GB)';
    html += '</span></div>';
    html += '<div class="system-row"><span class="system-label">Disk</span><span class="system-value">' + (disk.free_gb >= 0 ? disk.free_gb + ' GB free (' + disk.used_pct + '% used)' : 'N/A') + '</span></div>';
    if (updates.length > 0) {
      html += '<div class="system-row"><span class="system-label">Updates</span><span class="system-value" style="color:var(--warning);">' + updates.length + ' available</span></div>';
    } else {
      html += '<div class="system-row"><span class="system-label">Updates</span><span class="system-value text-success">Up to date</span></div>';
    }
    // Health badge
    var healthColor = 'var(--success)';
    var healthText = 'Good';
    if ((mem.used_pct || 0) > 90 || (disk.used_pct || 0) > 90) { healthColor = 'var(--error)'; healthText = 'Critical'; }
    else if ((mem.used_pct || 0) > 75 || (disk.used_pct || 0) > 75) { healthColor = 'var(--warning)'; healthText = 'Fair'; }
    document.getElementById('shelby-health-badge').textContent = healthText;
    document.getElementById('shelby-health-badge').style.color = healthColor;
    sysEl.innerHTML = html;
    // Weather
    var wEl = document.getElementById('shelby-weather');
    var w = data.weather || {};
    var whtml = '<div style="font-family:var(--font-mono);font-size:0.78rem;font-weight:600;color:var(--text-secondary);margin-bottom:var(--space-3);">Portsmouth, NH</div>';
    whtml += '<div class="system-row"><span class="system-label">Temp</span><span class="system-value">' + esc(w.temp_f || '?') + '&deg;F</span></div>';
    whtml += '<div class="system-row"><span class="system-label">Feels Like</span><span class="system-value">' + esc(w.feels_like_f || '?') + '&deg;F</span></div>';
    whtml += '<div class="system-row"><span class="system-label">Conditions</span><span class="system-value">' + esc(w.desc || '?') + '</span></div>';
    whtml += '<div class="system-row"><span class="system-label">Humidity</span><span class="system-value">' + esc(w.humidity || '?') + '%</span></div>';
    whtml += '<div class="system-row"><span class="system-label">Wind</span><span class="system-value">' + esc(w.wind_mph || '?') + ' mph</span></div>';
    wEl.innerHTML = whtml;
  } catch (e) {}
}

async function loadAssessments() {
  try {
    var resp = await fetch('/api/shelby/assessments');
    var data = await resp.json();
    var el = document.getElementById('shelby-assessments');
    var agents = Object.keys(data);
    if (agents.length === 0) { el.innerHTML = '<div class="text-muted">No assessments yet.</div>'; return; }
    var html = '';
    for (var i = 0; i < agents.length; i++) {
      var name = agents[i];
      var a = data[name];
      var color = AGENT_COLORS[name] || 'var(--text)';
      var trendClass = a.trend === 'up' ? 'trend-up' : a.trend === 'down' ? 'trend-down' : 'trend-stable';
      var trendSymbol = a.trend === 'up' ? '&#9650;' : a.trend === 'down' ? '&#9660;' : '&#9644;';
      var scoreColor = (a.score || 0) >= 70 ? 'var(--success)' : (a.score || 0) >= 50 ? 'var(--warning)' : 'var(--error)';
      html += '<div class="assessment-card">';
      html += '<div class="assessment-header">';
      var dn = name === 'lisa' ? 'Lisa' : name === 'sentinel' ? 'Robotox' : name.charAt(0).toUpperCase() + name.slice(1);
      html += '<span class="assessment-name" style="color:' + color + ';">' + dn + '</span>';
      html += '<span class="assessment-score" style="color:' + scoreColor + ';">' + (a.score || 0) + '<span class="trend-arrow ' + trendClass + '">' + trendSymbol + '</span></span>';
      html += '</div>';
      html += '<div class="progress-bar" style="margin:var(--space-2) 0;"><div class="progress-fill" style="width:' + (a.score || 0) + '%;background:' + scoreColor + ';"></div></div>';
      html += '<div class="assessment-opinion">' + esc(a.opinion || '') + '</div>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch (e) {}
}

async function loadAgentLearning(agent) {
  try {
    var resp = await fetch('/api/atlas/learning/' + agent);
    var data = await resp.json();
    var el = document.getElementById(agent + '-learning');
    if (!el) return;
    var score = data.learning_score || 'Novice';
    var obs = data.observations || 0;
    var imp = data.improvements_applied || 0;
    el.innerHTML = '<span class="wb-label">Learning:</span> <span style="color:var(--agent-atlas);">' + esc(score) + '</span> <span class="wb-label" style="margin-left:4px;">' + obs + ' obs</span> <span class="wb-label" style="margin-left:4px;">' + imp + ' improvements</span>';
  } catch (e) {}
}

async function loadShelbyNextRoutine() {
  try {
    var resp = await fetch('/api/shelby/schedule');
    var data = await resp.json();
    var schedule = data.schedule || {};
    var currentTime = data.current_time || '';
    var el = document.getElementById('shelby-next-routine');
    if (!el) return;
    var times = Object.keys(schedule).sort();
    var nextRoutine = null;
    for (var i = 0; i < times.length; i++) {
      if (!schedule[times[i]].completed && times[i] >= currentTime) {
        nextRoutine = times[i] + ' ' + (schedule[times[i]].name || '');
        break;
      }
    }
    if (nextRoutine) {
      el.textContent = nextRoutine.split(' ')[0];
      el.title = nextRoutine;
    } else {
      el.textContent = 'All done';
    }
  } catch (e) {}
}

async function openKPIModal(agent) {
  var modal = document.getElementById('kpi-modal');
  var title = document.getElementById('kpi-modal-title');
  var content = document.getElementById('kpi-modal-content');
  title.textContent = (AGENT_NAMES[agent] || agent.charAt(0).toUpperCase() + agent.slice(1)) + ' - Performance KPIs';
  title.style.color = AGENT_COLORS[agent] || 'var(--text)';
  content.innerHTML = '<div class="text-muted">Loading...</div>';
  modal.classList.add('active');
  try {
    var resp = await fetch('/api/agent/' + agent + '/kpis');
    var data = await resp.json();
    var keys = Object.keys(data);
    var html = '<div class="kpi-grid">';
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      var val = data[key];
      if (typeof val === 'object') {
        html += '<div class="kpi-item" style="grid-column:span 2;">';
        html += '<div class="kpi-label">' + esc(key.replace(/_/g, ' ')) + '</div>';
        var subkeys = Object.keys(val);
        for (var j = 0; j < subkeys.length; j++) {
          html += '<div style="display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:0.74rem;color:var(--text-secondary);padding:2px 0;">';
          html += '<span>' + esc(subkeys[j]) + '</span><span>' + esc(String(val[subkeys[j]])) + '</span></div>';
        }
        html += '</div>';
      } else {
        html += '<div class="kpi-item">';
        html += '<div class="kpi-value" style="color:' + (AGENT_COLORS[agent] || 'var(--text)') + ';">' + esc(String(val)) + '</div>';
        html += '<div class="kpi-label">' + esc(key.replace(/_/g, ' ')) + '</div>';
        html += '</div>';
      }
    }
    html += '</div>';
    content.innerHTML = html;
  } catch (e) { content.innerHTML = '<div class="text-error">Error: ' + esc(e.message) + '</div>'; }
}

function closeKPIModal() {
  document.getElementById('kpi-modal').classList.remove('active');
}


function exportAgentReport() {
  var a = document.createElement('a');
  a.href = '/api/shelby/export?format=csv';
  a.download = 'agent_report.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function healthCheckAgent(agent) {
  var el = document.getElementById(agent + '-health-result');
  if (!el) return;
  el.classList.add('visible');
  el.textContent = 'Running health check for ' + agent + '...';
  try {
    var resp = await fetch('/api/sentinel/scan', {method:'POST'});
    var data = await resp.json();
    var agents = data.agents || {};
    var agentData = agents[agent] || agents[agent + '_bot'] || {};
    var txt = agent.toUpperCase() + ' HEALTH CHECK\\n' + '='.repeat(25) + '\\n';
    if (Object.keys(agentData).length > 0) {
      txt += 'Status: ' + (agentData.alive ? 'ONLINE' : 'DOWN') + '\\n';
      if (agentData.pids) txt += 'PIDs: ' + agentData.pids.join(', ') + '\\n';
    } else {
      txt += 'Status: No specific data found\\n';
    }
    var sys = data.system || {};
    txt += '\\nSystem: ' + (sys.status || 'ok') + ' | Load: ' + ((sys.load_avg || []).join(', ') || '?') + '\\n';
    var issues = data.issues || [];
    var agentIssues = [];
    for (var i = 0; i < issues.length; i++) {
      if ((issues[i].agent || '').toLowerCase() === agent || (issues[i].message || '').toLowerCase().indexOf(agent) !== -1) {
        agentIssues.push(issues[i]);
      }
    }
    if (agentIssues.length > 0) {
      txt += '\\nISSUES:\\n';
      for (var i = 0; i < agentIssues.length; i++) {
        txt += '  [' + (agentIssues[i].severity || '?') + '] ' + (agentIssues[i].message || '') + '\\n';
      }
    } else {
      txt += '\\nNo issues found for ' + agent + '.\\n';
    }
    el.textContent = txt;
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}

async function refresh() {
  try {
    if (currentTab === 'overview') {
      var resp = await fetch('/api/overview');
      var data = await resp.json();
      _overviewCache = data;
      renderAgentGrid(data);
      loadInfrastructure();
      loadOverviewVitals();
      loadOverviewHealthBanner();
      if (_intelData) renderTeamIntelligence(_intelData);
      loadBrainNotes('claude');
      loadCommandTable('claude');
      loadAgentSmartActions('overview');
    } else if (currentTab === 'garves-live') {
      var resp = await fetch('/api/trades/live');
      var data = await resp.json();
      renderLiveStats(data);
      // Daily PnL for loss cap bar
      var _today = new Date().toISOString().slice(0,10);
      var _gDailyPnl = 0;
      (data.recent_trades || []).forEach(function(t) {
        if ((t.time || '').slice(0,10) === _today && t.est_pnl) _gDailyPnl += t.est_pnl;
      });
      renderLossCapBar('garves-loss-cap-bar', _gDailyPnl, 50);
      // Wire charts — PnL equity curve + Win Rate donut
      var _garvesResolved = data.recent_trades || [];
      renderPnLChart('garves-pnl-chart', _garvesResolved.slice().reverse().map(function(t){ return {pnl: t.est_pnl || 0, date: t.time}; }), 'Garves');
      renderWinRateDonut('garves-wr-donut', (data.summary||{}).wins||0, (data.summary||{}).losses||0);
      renderBreakdown('live-bd-asset', data.by_asset);
      renderBreakdown('live-bd-tf', data.by_timeframe);
      renderBreakdown('live-bd-dir', data.by_direction);
      renderLivePendingTrades(data.pending_trades);
      renderLiveResolvedTrades(data.recent_trades);
      fetch('/api/logs').then(function(r){return r.json();}).then(function(d){renderLiveLogs(d.lines);}).catch(function(){});
      fetch('/api/garves/balance').then(function(r){return r.json();}).then(function(d){renderLiveBalance(d);}).catch(function(){});
      fetch('/api/garves/positions').then(function(r){return r.json();}).then(function(d){renderOnChainPositions(d);}).catch(function(){});
      fetch('/api/garves/bankroll').then(function(r){return r.json();}).then(function(d){
        var el = document.getElementById('garves-bankroll-label');
        if(el && d.bankroll_usd) el.textContent = '$' + d.bankroll_usd.toFixed(0) + ' bankroll (' + d.multiplier.toFixed(2) + 'x)';
      }).catch(function(){});
      loadAgentActivity('garves-live');
      loadRegimeBadge();
      loadConvictionData();
      loadDailyReports();
      loadDerivatives();
      loadExternalData();
      loadAgentLearning('garves');
      loadNewsSentiment();
      loadMLWinPredictor();
      loadMLStatus();
      loadGarvesHealthWarnings();
      loadJournal();
      refreshClobPill();
      refreshBinancePill();
      refreshSnipeV7();
      refreshSnipeAssist();
      loadSignalCycle();
      loadGarvesV2Metrics();
      loadMakerStatus();
      loadEngineComparison();
      loadPortfolioAllocation();
      loadWhaleFollower();
      loadGarvesIntelligence();
      loadMomentumMode();
    } else if (currentTab === 'soren') {
      var resp = await fetch('/api/soren');
      renderSoren(await resp.json());
      loadAgentLearning('soren');
      loadSorenCompetitors();
      loadPillarDistribution();
      loadAgentSmartActions('soren');
      loadAgentActivity('soren');
      loadSorenMetrics();
    } else if (currentTab === 'shelby') {
      var resp = await fetch('/api/shelby');
      renderShelby(await resp.json());
      try { loadSchedule(); } catch(e) {}
      try { loadAssessments(); } catch(e) {}
      loadCommandTable('shelby');
      loadAgentSmartActions('shelby');
      loadAgentActivity('shelby');
    } else if (currentTab === 'atlas') {
      var resp = await fetch('/api/atlas');
      var atlasData = await resp.json();
      // Also fetch costs
      try {
        var costResp = await fetch('/api/atlas/costs');
        atlasData.costs = await costResp.json();
      } catch(e) {}
      renderAtlas(atlasData);
      loadAtlasBgStatus();
      loadAgentLearning('atlas');
      loadAgentActivity('atlas');
      loadAtlasKBHealth();
      loadAtlasPriorityQueue();
      loadAtlasDashboardSummary();
    } else if (currentTab === 'lisa') {
      var resp = await fetch('/api/lisa');
      renderLisa(await resp.json());
      loadJordanQueue();
      loadPostingSchedule();
      loadPipelineStats();
      loadAgentLearning('lisa');
      loadLisaGoLive();
      loadAgentActivity('lisa');
      loadAutoPostStatus();
      loadImageCosts();
      loadXCompetitorIntel();
      loadReplyOpportunities();
      loadLisaIntelligence();
      testXConnection();
    } else if (currentTab === 'sentinel') {
      var resp = await fetch('/api/sentinel');
      var sentinelData = await resp.json();
      renderSentinel(sentinelData);
      rxLoadIntelligence(sentinelData);
      rxLoadTradeGuard();
      rxLoadPredictive();
      rxLoadQuietHours();
      rxLoadLivePills();
      rxLoadPnlImpact();
      loadLogWatcherAlerts();
      loadRobotoxPerf();
      loadRobotoxDepHealth();
      loadRobotoxCorrelator();
      loadRobotoxDeployWatches();
      loadAgentActivity('sentinel');
    } else if (currentTab === 'thor') {
      loadThor();
      loadSmartActions();
      loadThorReflexion();
      loadThorCache();
      loadThorReview();
      loadThorProgress();
      loadThorCodebaseIndex();
      loadCommandTable('thor');
      loadAgentActivity('thor');
    } else if (currentTab === 'hawk') {
      loadHawkTab();
      loadHawkSimTab();
      loadAgentActivity('hawk');
    } else if (currentTab === 'viper') {
      loadViperTab();
    } else if (currentTab === 'quant') {
      loadQuantTab();
      loadQuantSmartActions();
    } else if (currentTab === 'odin') {
      loadOdinTab();
    } else if (currentTab === 'oracle') {
      oracleRefresh();
    } else if (currentTab === 'discord') {
      if (typeof discordRefresh === 'function') discordRefresh();
    } else if (currentTab === 'intelligence') {
      if (typeof refreshIntelligence === 'function') refreshIntelligence();
    } else if (currentTab === 'traders') {
      if (typeof tradersRefresh === 'function') tradersRefresh();
    } else if (currentTab === 'system') {
      loadSystemTab();
    }
    // Always cache overview + atlas bg for intel live panels
    if (currentTab !== 'overview') {
      try {
        var bgResp = await fetch('/api/atlas/background/status');
        _atlasBgCache = await bgResp.json();
      } catch(e) {}
      if (!_overviewCache) {
        try {
          var ovResp = await fetch('/api/overview');
          _overviewCache = await ovResp.json();
        } catch(e) {}
      }
    }
    document.getElementById('last-update').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch (e) {
    console.error('refresh error:', e);
    document.getElementById('last-update').textContent = 'Error: ' + (e.message || 'unknown');
  }
}

refresh();
setInterval(refresh, 15000);

// ── Intelligence Meters ──
var _intelData = null;

function agentQuickStatus(key) {
  var o = _overviewCache || {};
  var bg = _atlasBgCache || {};
  var s = '<div class="agent-quick-status">';
  if (key === 'atlas') {
    if (!bg.running) { s += '<span class="aqs-offline">Offline</span>'; }
    else {
      var isWorking = bg.state !== 'running' && bg.state !== 'idle' && bg.state !== 'stopped';
      if (isWorking) {
        s += '<span class="aqs-dot aqs-dot-active"></span>' + esc(bg.state_label || bg.state);
      } else if (bg.last_cycle && bg.cycle_minutes) {
        var remain = new Date(bg.last_cycle).getTime() + bg.cycle_minutes * 60000 - Date.now();
        if (remain > 0) {
          s += '<span class="aqs-dim">Next:</span> ' + Math.floor(remain / 60000) + 'm ' + Math.floor((remain % 60000) / 1000) + 's';
        } else { s += '<span class="aqs-dim">Starting...</span>'; }
      } else { s += 'Cycle ' + (bg.cycles || 0); }
    }
  } else if (key === 'garves') {
    var g = o.garves || {};
    s += '<span class="aqs-dim">WR:</span>' + (g.win_rate || 0) + '% <span class="aqs-sep">&middot;</span> ' + (g.pending || 0) + ' open';
  } else if (key === 'soren') {
    var sr = o.soren || {};
    s += (sr.queue_pending || 0) + ' queued <span class="aqs-sep">&middot;</span> ' + (sr.total_posted || 0) + ' posted';
  } else if (key === 'shelby') {
    var sh = o.shelby || {};
    s += (sh.running ? '<span class="aqs-dot aqs-dot-online"></span>Online' : '<span class="aqs-offline">Offline</span>');
  } else if (key === 'lisa') {
    var m = o.lisa || {};
    s += (m.total_posts || 0) + ' posts';
    if (m.review_avg) s += ' <span class="aqs-sep">&middot;</span> ' + m.review_avg + '/10';
  } else if (key === 'robotox') {
    s += '<span class="aqs-dot aqs-dot-online"></span>Watching';
  } else if (key === 'thor') {
    var th = o.thor || {};
    s += (th.pending || 0) + ' pending <span class="aqs-sep">&middot;</span> ' + (th.completed || 0) + ' done';
  } else if (key === 'hawk') {
    var hk = o.hawk || {};
    s += '<span class="aqs-dim">WR:</span>' + (hk.win_rate || 0) + '% <span class="aqs-sep">&middot;</span> ' + (hk.open_bets || 0) + ' open';
  } else if (key === 'viper') {
    var vp = o.viper || {};
    s += (vp.opportunities || 0) + ' found <span class="aqs-sep">&middot;</span> ' + (vp.pushed || 0) + ' pushed';
  }
  s += '</div>';
  return s;
}

function renderTeamIntelligence(data) {
  var el = document.getElementById('team-intelligence');
  if (!el || !data || !data.team) return;
  var team = data.team;
  var dims = team.dimensions || {};
  var overall = team.overall || 0;

  var iqLabel = overall >= 90 ? 'GENIUS' : overall >= 75 ? 'EXPERT' : overall >= 60 ? 'SKILLED' : overall >= 40 ? 'LEARNING' : 'NOVICE';
  var teamColor = overall >= 80 ? '#00ff88' : overall >= 60 ? '#22aa44' : overall >= 40 ? '#ffaa00' : '#ff4444';

  var dimKeys = Object.keys(dims);
  var dimValues = dimKeys.map(function(k) { return dims[k]; });

  var html = '<div class="radar-title" style="margin-bottom:8px;">Team Intelligence</div>';
  html += '<div style="text-align:center;margin-bottom:8px;">' + radarSVG(180, dimValues, dimKeys, teamColor) + '</div>';
  html += '<div style="display:flex;align-items:baseline;gap:8px;justify-content:center;margin-bottom:12px;">';
  html += '<span class="radar-score" style="color:' + teamColor + ';">' + overall + '</span>';
  html += '<span class="radar-level" style="color:' + teamColor + ';">' + iqLabel + '</span>';
  html += '</div>';

  for (var i = 0; i < dimKeys.length; i++) {
    var val = dims[dimKeys[i]];
    var vc = val >= 75 ? 'var(--success)' : val >= 50 ? teamColor : val >= 30 ? 'var(--warning)' : 'var(--error)';
    html += '<div class="ov-dim-row">';
    html += '<span class="ov-dim-label">' + dimKeys[i] + '</span>';
    html += '<div class="ov-dim-bar"><div class="ov-dim-fill" style="width:' + Math.min(100, val) + '%;background:' + vc + ';"></div></div>';
    html += '<span class="ov-dim-val" style="color:' + vc + ';">' + val + '</span>';
    html += '</div>';
  }

  el.innerHTML = html;
}
var _intelAgentMap = {garves:'garves',soren:'soren',shelby:'shelby',atlas:'atlas',lisa:'lisa',sentinel:'robotox',thor:'thor',hawk:'hawk',viper:'viper',quant:'quant',odin:'odin',oracle:'oracle'};
var _intelColors = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',lisa:'#ff8800',robotox:'#00ff44',thor:'#ff6600',hawk:'#FFD700',viper:'#00ff88',quant:'#00BFFF',odin:'#8B5CF6',oracle:'#F59E0B'};

function radarSVG(size, values, labels, color) {
  var cx = size / 2, cy = size / 2;
  var R = size / 2 - (labels ? 20 : 8);
  var n = values.length;
  var angles = [];
  for (var i = 0; i < n; i++) angles.push(-Math.PI / 2 + i * 2 * Math.PI / n);

  var svg = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '" style="filter:drop-shadow(0 0 8px ' + color + '33);">';

  var levels = [25, 50, 75, 100];
  for (var l = 0; l < levels.length; l++) {
    var pts = '';
    for (var i = 0; i < n; i++) {
      var x = cx + (levels[l] / 100) * R * Math.cos(angles[i]);
      var y = cy + (levels[l] / 100) * R * Math.sin(angles[i]);
      pts += x.toFixed(1) + ',' + y.toFixed(1) + ' ';
    }
    svg += '<polygon points="' + pts.trim() + '" fill="none" stroke="rgba(255,255,255,' + (levels[l] === 100 ? '0.1' : '0.04') + '" stroke-width="1"/>';
  }

  for (var i = 0; i < n; i++) {
    var x = cx + R * Math.cos(angles[i]);
    var y = cy + R * Math.sin(angles[i]);
    svg += '<line x1="' + cx + '" y1="' + cy + '" x2="' + x.toFixed(1) + '" y2="' + y.toFixed(1) + '" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>';
  }

  var dataPts = '';
  for (var i = 0; i < n; i++) {
    var v = Math.max(5, Math.min(100, values[i] || 0));
    var x = cx + (v / 100) * R * Math.cos(angles[i]);
    var y = cy + (v / 100) * R * Math.sin(angles[i]);
    dataPts += x.toFixed(1) + ',' + y.toFixed(1) + ' ';
  }
  svg += '<polygon points="' + dataPts.trim() + '" fill="' + color + '" fill-opacity="0.15" stroke="' + color + '" stroke-width="2" stroke-linejoin="round"/>';

  for (var i = 0; i < n; i++) {
    var v = Math.max(5, Math.min(100, values[i] || 0));
    var x = cx + (v / 100) * R * Math.cos(angles[i]);
    var y = cy + (v / 100) * R * Math.sin(angles[i]);
    svg += '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="3" fill="' + color + '"/>';
  }

  if (labels) {
    for (var i = 0; i < n; i++) {
      var lx = cx + (R + 13) * Math.cos(angles[i]);
      var ly = cy + (R + 13) * Math.sin(angles[i]);
      var anchor = Math.abs(Math.cos(angles[i])) < 0.15 ? 'middle' : Math.cos(angles[i]) > 0 ? 'start' : 'end';
      var dy = angles[i] < -0.3 ? '-0.3em' : angles[i] > 0.3 ? '0.8em' : '0.35em';
      svg += '<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1) + '" text-anchor="' + anchor + '" dy="' + dy + '" fill="rgba(255,255,255,0.45)" font-size="8" font-family="Inter,sans-serif">' + esc(labels[i]) + '</text>';
    }
  }

  svg += '</svg>';
  return svg;
}

function intelLivePanel(agentKey, color) {
  var o = _overviewCache || {};
  var bg = _atlasBgCache || {};
  var h = '<div class="intel-live-panel" style="border-left:1px solid ' + color + '22;">';
  h += '<div class="ilp-title" style="color:' + color + ';">Live Status</div>';

  if (agentKey === 'atlas') {
    // State
    if (bg.running) {
      var isWorking = bg.state && bg.state !== 'running' && bg.state !== 'idle' && bg.state !== 'stopped';
      h += '<div class="ilp-row"><span class="ilp-label">State</span>';
      if (isWorking) {
        h += '<span class="ilp-val" style="color:var(--agent-atlas);"><span class="aqs-dot aqs-dot-active"></span>' + esc(bg.state_label || bg.state) + '</span>';
      } else {
        h += '<span class="ilp-val" style="color:var(--text-secondary);">Idle</span>';
      }
      h += '</div>';
      // Countdown
      if (!isWorking && bg.last_cycle && bg.cycle_minutes) {
        var remain = new Date(bg.last_cycle).getTime() + bg.cycle_minutes * 60000 - Date.now();
        if (remain > 0) {
          h += '<div class="ilp-row"><span class="ilp-label">Next Cycle</span><span class="ilp-val" style="color:var(--agent-atlas);">' + Math.floor(remain / 60000) + 'm ' + Math.floor((remain % 60000) / 1000) + 's</span></div>';
        }
      }
      // Cycles
      h += '<div class="ilp-row"><span class="ilp-label">Cycles</span><span class="ilp-val">' + (bg.cycles || 0) + '</span></div>';
      // Researches
      h += '<div class="ilp-row"><span class="ilp-label">Researches</span><span class="ilp-val">' + (bg.total_researches || 0) + '</span></div>';
      // URLs
      h += '<div class="ilp-row"><span class="ilp-label">URLs Seen</span><span class="ilp-val">' + (bg.unique_urls || 0) + '</span></div>';
      // Target
      if (bg.current_target) {
        h += '<div class="ilp-row"><span class="ilp-label">Feeding</span><span class="ilp-val" style="color:#22cc55;">' + esc(bg.current_target === 'all' ? 'All Agents' : bg.current_target.charAt(0).toUpperCase() + bg.current_target.slice(1)) + '</span></div>';
      }
    } else {
      h += '<div class="ilp-row"><span class="ilp-val" style="color:var(--error);">Offline</span></div>';
    }
  } else if (agentKey === 'garves') {
    var g = o.garves || {};
    h += '<div class="ilp-row"><span class="ilp-label">Win Rate</span><span class="ilp-val" style="color:' + ((g.win_rate||0) >= 50 ? 'var(--success)' : 'var(--error)') + ';">' + (g.win_rate || 0) + '%</span></div>';
    h += '<div class="ilp-row"><span class="ilp-label">Total Trades</span><span class="ilp-val">' + (g.total_trades || 0) + '</span></div>';
    h += '<div class="ilp-row"><span class="ilp-label">Pending</span><span class="ilp-val">' + (g.pending || 0) + '</span></div>';
    h += '<div class="ilp-row"><span class="ilp-label">Status</span><span class="ilp-val" style="color:' + (g.running ? 'var(--success)' : 'var(--error)') + ';">' + (g.running ? 'Running' : 'Stopped') + '</span></div>';
  } else if (agentKey === 'soren') {
    var sr = o.soren || {};
    h += '<div class="ilp-row"><span class="ilp-label">Queue</span><span class="ilp-val">' + (sr.queue_pending || 0) + ' pending</span></div>';
    h += '<div class="ilp-row"><span class="ilp-label">Posted</span><span class="ilp-val">' + (sr.total_posted || 0) + '</span></div>';
  } else if (agentKey === 'shelby') {
    var sh = o.shelby || {};
    h += '<div class="ilp-row"><span class="ilp-label">Status</span><span class="ilp-val" style="color:' + (sh.running ? 'var(--success)' : 'var(--error)') + ';">' + (sh.running ? 'Online' : 'Offline') + '</span></div>';
  } else if (agentKey === 'lisa') {
    var m = o.lisa || {};
    h += '<div class="ilp-row"><span class="ilp-label">Posts</span><span class="ilp-val">' + (m.total_posts || 0) + '</span></div>';
    if (m.review_avg) h += '<div class="ilp-row"><span class="ilp-label">Review Avg</span><span class="ilp-val">' + m.review_avg + '/10</span></div>';
  } else if (agentKey === 'robotox') {
    h += '<div class="ilp-row"><span class="ilp-label">Status</span><span class="ilp-val" style="color:var(--success);">Watching</span></div>';
  } else if (agentKey === 'thor') {
    var th = o.thor || {};
    var thState = th.state || 'offline';
    var thStateColor = thState === 'coding' ? 'var(--agent-thor)' : thState === 'idle' ? 'var(--success)' : 'var(--error)';
    h += '<div class="ilp-row"><span class="ilp-label">State</span><span class="ilp-val" style="color:' + thStateColor + ';">' + thState.charAt(0).toUpperCase() + thState.slice(1) + '</span></div>';
    h += '<div class="ilp-row"><span class="ilp-label">Queue</span><span class="ilp-val">' + (th.pending || 0) + ' pending</span></div>';
    h += '<div class="ilp-row"><span class="ilp-label">Completed</span><span class="ilp-val" style="color:var(--success);">' + (th.completed || 0) + '</span></div>';
  }

  h += '</div>';
  return h;
}

function renderIntelMeter(containerId, agentKey, data) {
  var el = document.getElementById(containerId);
  if (!el || !data || !data.dimensions) return;
  var dims = data.dimensions;
  var overall = data.overall || 0;
  var title = data.title || '';
  var color = _intelColors[agentKey] || '#ffffff';

  var iqLabel = overall >= 90 ? 'GENIUS' : overall >= 75 ? 'EXPERT' : overall >= 60 ? 'SKILLED' : overall >= 40 ? 'LEARNING' : 'NOVICE';

  var keys = Object.keys(dims);
  var values = keys.map(function(k) { return dims[k]; });

  var html = '<div style="display:flex;align-items:flex-start;gap:16px;">';
  html += '<div style="flex-shrink:0;">' + radarSVG(140, values, keys, color) + '</div>';
  html += '<div style="min-width:140px;">';
  html += '<div class="radar-title">' + esc(title) + ' Intelligence</div>';
  html += '<div style="display:flex;align-items:baseline;gap:8px;">';
  html += '<span class="radar-score" style="color:' + color + ';">' + overall + '</span>';
  html += '<span class="radar-level" style="color:' + color + ';">' + iqLabel + '</span>';
  html += '</div>';
  html += '<div class="radar-dims">';
  for (var i = 0; i < keys.length; i++) {
    var val = dims[keys[i]];
    var vc = val >= 75 ? 'var(--success)' : val >= 50 ? color : val >= 30 ? 'var(--warning)' : 'var(--error)';
    html += '<div class="radar-dim"><span class="radar-dim-label">' + keys[i] + '</span><span class="radar-dim-val" style="color:' + vc + ';">' + val + '</span></div>';
  }
  html += '</div></div>';
  // Live status panel on the right
  html += intelLivePanel(agentKey, color);
  html += '</div>';

  el.innerHTML = html;
}

async function refreshAgentIntel() {
  try {
    var resp = await fetch('/api/intelligence');
    _intelData = await resp.json();

    // Render team intel on overview
    if (currentTab === 'overview' && _intelData.team) {
      renderTeamIntelligence(_intelData);
    }

    // Render the meter for whatever agent tab is active
    var activeAgent = _intelAgentMap[currentTab];
    if (activeAgent && _intelData[activeAgent]) {
      renderIntelMeter('intel-' + activeAgent, activeAgent, _intelData[activeAgent]);
    }
  } catch(e) {
    console.error('Intel refresh error:', e);
  }
}

// Refresh intel on tab switch too
var _origSwitchTab = switchTab;
switchTab = function(tab) {
  _origSwitchTab(tab);
  if (_intelData) {
    if (tab === 'overview' && _intelData.team) {
      renderTeamIntelligence(_intelData);
    }
    var activeAgent = _intelAgentMap[tab];
    if (activeAgent && _intelData[activeAgent]) {
      renderIntelMeter('intel-' + activeAgent, activeAgent, _intelData[activeAgent]);
    }
  }
  // Viper auto-refresh: start when on viper tab, stop when leaving
  if (tab === 'viper') {
    viperStartAutoRefresh();
  } else {
    viperStopAutoRefresh();
  }
};

// Load on startup + every 30s
refreshAgentIntel();
setInterval(refreshAgentIntel, 30000);

// ── Infrastructure Display ──

function healthBadge(h) {
  if (h === 'healthy') return '<span style="color:var(--success);">HEALTHY</span>';
  if (h === 'stale') return '<span style="color:var(--warning);">STALE</span>';
  return '<span style="color:var(--error);">DEAD</span>';
}

async function loadInfrastructure() {
  // Active Agents from /api/health (launchctl-based, reliable)
  try {
    var resp = await fetch('/api/health');
    var data = await resp.json();
    var agents = data.agents || {};
    var total = Object.keys(agents).length;
    var alive = 0;
    for (var k in agents) { if (agents[k].status === 'healthy' || agents[k].status === 'degraded') alive++; }

    var aaEl = document.getElementById('ov-agents');
    if (aaEl) {
      var aaColor = alive === total ? 'var(--success)' : alive > 0 ? 'var(--warning)' : 'var(--error)';
      aaEl.innerHTML = '<span style="color:' + aaColor + ';">' + alive + ' / ' + total + '</span>';
    }
    var badgeEl = document.getElementById('ov-agent-count-badge');
    if (badgeEl) badgeEl.innerHTML = '<span class="dot-online"></span> ' + alive + ' Online';
    window._healthData = agents;
  } catch(e) {}

  // PNL + Trades from system-summary
  try {
    var resp = await fetch('/api/system-summary');
    var d = await resp.json();
    var pnlEl = document.getElementById('ov-pnl');
    if (pnlEl) {
      var gPnl = d.garves ? d.garves.pnl : 0;
      var hPnl = d.hawk ? d.hawk.pnl : 0;
      var oPnl = d.odin ? (d.odin.pnl || 0) : 0;
      var totalPnl = gPnl + hPnl + oPnl;
      var pnlColor = totalPnl >= 0 ? 'var(--success)' : 'var(--error)';
      pnlEl.innerHTML = '<span style="color:' + pnlColor + ';">' + (totalPnl >= 0 ? '+' : '') + '$' + totalPnl.toFixed(2) + '</span>';
    }
    var tradesEl = document.getElementById('ov-trades');
    if (tradesEl) {
      var gt = d.garves ? d.garves.trades : 0;
      var ht = d.hawk ? d.hawk.trades : 0;
      var gReal = d.garves ? (d.garves.real_trades || 0) : 0;
      var hReal = d.hawk ? (d.hawk.real_trades || 0) : 0;
      var totalT = gt + ht;
      var totalReal = gReal + hReal;
      var totalPaper = totalT - totalReal;
      var label = '';
      if (totalReal > 0 && totalPaper > 0) {
        label = '<span style="font-size:0.68rem;color:var(--text-muted);display:block;margin-top:1px;">' + totalReal + ' real, ' + totalPaper + ' paper</span>';
      } else if (totalPaper > 0) {
        label = '<span style="font-size:0.68rem;color:var(--warning);display:block;margin-top:1px;">all paper</span>';
      } else if (totalReal > 0) {
        label = '<span style="font-size:0.68rem;color:var(--success);display:block;margin-top:1px;">all real</span>';
      }
      tradesEl.innerHTML = totalT + label;
    }
  } catch(e) {}

  // Critical alerts from Robotox
  try {
    var errResp = await fetch('/api/robotox/log-alerts');
    var errData = await errResp.json();
    var alerts = errData.alerts || [];
    var now = Date.now();
    var errCount = 0;
    for (var i = 0; i < alerts.length; i++) {
      var ts = new Date(alerts[i].timestamp || 0).getTime();
      if (now - ts < 86400000) errCount++;
    }
    var errEl = document.getElementById('ov-alerts');
    if (errEl) {
      var errColor = errCount === 0 ? 'var(--success)' : 'var(--error)';
      errEl.innerHTML = '<span style="color:' + errColor + ';">' + errCount + '</span>';
    }
  } catch(e) {
    var errEl = document.getElementById('ov-alerts');
    if (errEl) errEl.innerHTML = '<span style="color:var(--success);">0</span>';
  }

  // Update timestamp
  var tsEl = document.getElementById('ov-updated');
  if (tsEl) {
    var now = new Date();
    tsEl.textContent = now.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit', hour12:false});
  }

  // Agent Comms
  loadAgentComms();
}

async function loadTodayActivity() {
  try {
    var resp = await fetch('/api/system-summary');
    var d = await resp.json();

    var portfolioEl = document.getElementById('today-portfolio');
    if (portfolioEl && d.portfolio) {
      portfolioEl.textContent = '$' + d.portfolio.total_usd.toFixed(2);
    }

    var tradesEl = document.getElementById('today-trades');
    if (tradesEl) {
      var gt = d.garves ? d.garves.trades : 0;
      var ht = d.hawk ? d.hawk.trades : 0;
      tradesEl.textContent = gt + ht;
    }

    var contentEl = document.getElementById('today-content');
    if (contentEl && d.soren) {
      contentEl.textContent = d.soren.generated;
    }

    var costEl = document.getElementById('today-llm-cost');
    if (costEl && d.llm) {
      costEl.textContent = d.llm.cost_usd < 0.01 ? '$0.00' : '$' + d.llm.cost_usd.toFixed(2);
      costEl.style.color = d.llm.cost_usd < 1 ? 'var(--success)' : 'var(--warning)';
    }

    var detailEl = document.getElementById('today-detail');
    if (detailEl) {
      var parts = [];
      if (d.garves && d.garves.trades > 0) parts.push('Garves: ' + d.garves.trades + ' trades ($' + d.garves.pnl.toFixed(2) + ')');
      if (d.hawk && d.hawk.trades > 0) parts.push('Hawk: ' + d.hawk.trades + ' trades ($' + d.hawk.pnl.toFixed(2) + ')');
      if (d.llm) parts.push('LLM: ' + d.llm.local_calls + ' local / ' + d.llm.cloud_calls + ' cloud');
      if (d.atlas) parts.push('Atlas: ' + d.atlas.cycles + ' cycles, ' + d.atlas.patterns + ' patterns');
      if (d.events) parts.push('Events: ' + d.events.total + ' total');
      detailEl.textContent = parts.join(' | ');
    }
  } catch(e) {}
}

async function loadCrossAgentFlow() {
  try {
    var resp = await fetch('/api/system-summary');
    var d = await resp.json();
    var atlasEl = document.getElementById('xflow-atlas-count');
    var garvesEl = document.getElementById('xflow-garves-trades');
    var hawkEl = document.getElementById('xflow-hawk-opps');
    if (atlasEl && d.atlas) animateCount('xflow-atlas-count', d.atlas.patterns || 0, 600);
    if (garvesEl && d.garves) animateCount('xflow-garves-trades', d.garves.trades || 0, 600);
    if (hawkEl && d.hawk) animateCount('xflow-hawk-opps', d.hawk.trades || 0, 600);
  } catch(e) {}
}

// ═══════════════════════════════════════════════════════
// THOR — The Engineer
// ═══════════════════════════════════════════════════════
async function loadThor() {
  try {
    var resp = await fetch('/api/thor');
    var data = await resp.json();

    // State + Model
    var state = data.state || 'offline';
    var stateColor = state === 'coding' ? 'var(--agent-thor)' : state === 'idle' ? 'var(--success)' : 'var(--text-muted)';
    var stateLabel = document.getElementById('thor-state-label');
    if (stateLabel) {
      stateLabel.textContent = state.charAt(0).toUpperCase() + state.slice(1);
      stateLabel.style.color = stateColor;
    }
    setText('thor-model-name', data.model || '--');

    // Overall status pill
    var q = data.queue || {};
    var totalDone = (q.completed || 0) + (q.failed || 0);
    var successRate = totalDone > 0 ? Math.round((q.completed || 0) / totalDone * 100) : 0;
    var successRateStr = totalDone > 0 ? successRate + '%' : '--';
    var overallLabel = 'Healthy';
    var overallColor = 'var(--success)';
    if (state === 'coding') { overallLabel = 'Working'; overallColor = 'var(--agent-thor)'; }
    else if ((q.failed || 0) > (q.completed || 0) && totalDone > 0) { overallLabel = 'Degraded'; overallColor = 'var(--error)'; }
    else if (state === 'offline') { overallLabel = 'Offline'; overallColor = 'var(--text-muted)'; }
    var pillEl = document.getElementById('thor-overall-label');
    var dotEl = document.getElementById('thor-overall-dot');
    if (pillEl) { pillEl.textContent = overallLabel; pillEl.style.color = overallColor; }
    if (dotEl) { dotEl.style.background = overallColor; dotEl.style.boxShadow = '0 0 6px ' + overallColor; }

    // Updated time
    setText('thor-updated', new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}));

    // Stat cards
    setText('thor-queue-pending', q.pending || 0);
    setText('thor-completed', q.completed || 0);
    setText('thor-failed', q.failed || 0);
    setText('thor-success-rate', successRateStr);

    // Tokens used
    var tokens = data.total_tokens || 0;
    var tokensDisplay = tokens >= 1000000 ? (tokens / 1000000).toFixed(1) + 'M' : tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'K' : tokens;
    setText('thor-tokens', tokensDisplay);

    // Current task indicator
    if (data.current_task && data.state === 'coding') {
      setText('thor-queue-pending', (q.pending || 0) + ' +1');
    }

    // Radar chart — derive 5 dimensions from real data
    var radarEl = document.getElementById('thor-radar-container');
    if (radarEl) {
      var codeQuality = Math.min(100, totalDone > 0 ? successRate + 10 : 50);
      var efficiency = Math.min(100, tokens > 0 ? Math.round(80 - Math.min(40, tokens / 100000)) : 50);
      var knowledge = Math.min(100, (data.knowledge_entries || 0) * 5);
      var coverage = Math.min(100, totalDone * 4);
      var taskExec = Math.min(100, totalDone > 0 ? Math.round(((q.completed || 0) / Math.max(1, totalDone)) * 100) : 0);
      var dims = [codeQuality, efficiency, knowledge, coverage, taskExec];
      var dimLabels = ['Quality', 'Efficiency', 'Knowledge', 'Coverage', 'Execution'];
      var overall = Math.round((codeQuality + efficiency + knowledge + coverage + taskExec) / 5);
      var gradeLabel = overall >= 90 ? 'GENIUS' : overall >= 75 ? 'EXPERT' : overall >= 60 ? 'SKILLED' : overall >= 40 ? 'LEARNING' : 'NOVICE';
      var gradeColor = overall >= 80 ? '#FFD700' : overall >= 60 ? 'var(--success)' : overall >= 40 ? 'var(--warning)' : 'var(--error)';
      radarEl.innerHTML = radarSVG(180, dims, dimLabels, '#ff6600');
      setText('thor-score-value', overall);
      var gradeEl = document.getElementById('thor-score-grade');
      if (gradeEl) { gradeEl.textContent = gradeLabel; gradeEl.style.color = gradeColor; }
      var scoreEl = document.getElementById('thor-score-value');
      if (scoreEl) scoreEl.style.color = gradeColor;
    }
  } catch(e) {
    setText('thor-queue-pending', '--');
  }

  // Load sub-sections
  loadThorCosts();
  loadThorQueue();
  loadThorResults();
  loadThorActivity();
  thorLoadWakeStatus();
}

function thorRunSystemCheck() {
  var statusEl = document.getElementById('thor-action-status');
  if (statusEl) { statusEl.textContent = 'Running health scan...'; statusEl.style.color = 'var(--agent-thor)'; }
  fetch('/api/thor/quick-action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title: 'Full system health check', description: 'Run comprehensive health scan across all agents, check processes, verify endpoints, report status.', priority: 'high', source: 'dashboard'})
  }).then(function(r){return r.json();}).then(function(data) {
    if (data.error) { if (statusEl) { statusEl.textContent = 'Error: ' + data.error; statusEl.style.color = 'var(--error)'; } }
    else { if (statusEl) { statusEl.textContent = 'Health scan submitted'; statusEl.style.color = 'var(--success)'; } showToast('Health scan task submitted', 'success'); loadThorQueue(); }
  }).catch(function(e) { if (statusEl) { statusEl.textContent = 'Failed'; statusEl.style.color = 'var(--error)'; } });
}

function thorRunBugScan() {
  var statusEl = document.getElementById('thor-action-status');
  if (statusEl) { statusEl.textContent = 'Running bug scan...'; statusEl.style.color = 'var(--agent-thor)'; }
  fetch('/api/thor/quick-action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title: 'Bug scan across codebase', description: 'Scan all agent codebases for bugs, type errors, uncaught exceptions, deprecated patterns. Report findings.', priority: 'normal', source: 'dashboard'})
  }).then(function(r){return r.json();}).then(function(data) {
    if (data.error) { if (statusEl) { statusEl.textContent = 'Error: ' + data.error; statusEl.style.color = 'var(--error)'; } }
    else { if (statusEl) { statusEl.textContent = 'Bug scan submitted'; statusEl.style.color = 'var(--success)'; } showToast('Bug scan task submitted', 'success'); loadThorQueue(); }
  }).catch(function(e) { if (statusEl) { statusEl.textContent = 'Failed'; statusEl.style.color = 'var(--error)'; } });
}

async function loadThorCosts() {
  try {
    var resp = await fetch('/api/thor/costs');
    var data = await resp.json();
    var daily = data.daily_spend_usd || 0;
    var budget = data.daily_budget_usd || 5;
    var pct = data.daily_pct || 0;
    var total = data.total_spend_usd || 0;
    var calls = data.total_calls || 0;

    setText('thor-cost-daily', '$' + daily.toFixed(2));
    setText('thor-cost-budget', '$' + budget.toFixed(2));
    setText('thor-cost-total', '$' + total.toFixed(2));
    setText('thor-cost-calls', calls);

    var bar = document.getElementById('thor-cost-bar');
    if (bar) {
      bar.style.width = Math.min(pct, 100) + '%';
      bar.style.background = pct > 80 ? 'var(--error)' : pct > 50 ? 'var(--warning)' : 'var(--success)';
    }

    var models = data.by_model || {};
    var modelParts = [];
    for (var m in models) {
      var short = m.indexOf('opus') > -1 ? 'Opus' : m.indexOf('sonnet') > -1 ? 'Sonnet' : m.indexOf('haiku') > -1 ? 'Haiku' : m;
      modelParts.push(short + ':' + models[m].calls);
    }
    setText('thor-cost-models', modelParts.length > 0 ? modelParts.join(', ') : '--');
  } catch(e) {
    // silent
  }
}

async function loadThorReflexion() {
  var el = document.getElementById('thor-reflexion-stats');
  if (!el) return;
  try {
    var resp = await fetch('/api/thor/reflexion');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span class="text-muted">' + esc(data.error) + '</span>'; return; }
    var html = '<div style="margin-bottom:4px;"><span style="font-size:1rem;font-weight:700;">' + (data.total || 0) + '</span> <span class="text-muted">reflections</span></div>';
    var byType = data.by_type || {};
    var types = Object.keys(byType);
    if (types.length > 0) {
      html += '<div style="display:flex;gap:8px;flex-wrap:wrap;">';
      for (var i = 0; i < types.length; i++) {
        var t = types[i];
        var color = t === 'syntax' ? 'var(--error)' : t === 'quality_gate' ? 'var(--warning)' : 'var(--text-muted)';
        html += '<span style="color:' + color + ';">' + esc(t) + ': ' + byType[t] + '</span>';
      }
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span class="text-muted">--</span>'; }
}

async function loadThorCache() {
  var el = document.getElementById('thor-cache-stats');
  if (!el) return;
  try {
    var resp = await fetch('/api/thor/cache');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span class="text-muted">' + esc(data.error) + '</span>'; return; }
    var hitRate = data.total_lookups > 0 ? Math.round((data.cache_hits / data.total_lookups) * 100) : 0;
    var html = '<div style="margin-bottom:4px;"><span style="font-size:1rem;font-weight:700;">' + hitRate + '%</span> <span class="text-muted">hit rate</span></div>';
    html += '<div style="display:flex;gap:10px;">';
    html += '<span class="text-muted">Entries: ' + (data.cached_entries || 0) + '</span>';
    html += '<span style="color:var(--success);">Hits: ' + (data.cache_hits || 0) + '</span>';
    html += '<span class="text-muted">Lookups: ' + (data.total_lookups || 0) + '</span>';
    html += '</div>';
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span class="text-muted">--</span>'; }
}

async function loadThorReview() {
  var el = document.getElementById('thor-review-stats');
  if (!el) return;
  try {
    var resp = await fetch('/api/thor/review');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span class="text-muted">' + esc(data.error) + '</span>'; return; }
    var passRate = data.total > 0 ? Math.round((data.passed / data.total) * 100) : 0;
    var html = '<div style="margin-bottom:4px;"><span style="font-size:1rem;font-weight:700;">' + passRate + '%</span> <span class="text-muted">pass rate</span></div>';
    html += '<div style="display:flex;gap:10px;">';
    html += '<span style="color:var(--success);">Passed: ' + (data.passed || 0) + '</span>';
    html += '<span style="color:var(--error);">Failed: ' + (data.failed || 0) + '</span>';
    html += '<span class="text-muted">Avg: ' + (data.avg_score || 0) + '</span>';
    html += '</div>';
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span class="text-muted">--</span>'; }
}

async function loadThorProgress() {
  var el = document.getElementById('thor-progress-stats');
  if (!el) return;
  try {
    var resp = await fetch('/api/thor/progress');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span class="text-muted">' + esc(data.error) + '</span>'; return; }
    var stats = data.stats || {};
    var active = data.active || [];
    var html = '<div style="margin-bottom:4px;"><span style="font-size:1rem;font-weight:700;">' + (stats.total_tracked || 0) + '</span> <span class="text-muted">tracked</span></div>';
    html += '<div style="display:flex;gap:10px;">';
    html += '<span style="color:var(--success);">Done: ' + (stats.completed || 0) + '</span>';
    html += '<span style="color:var(--error);">Failed: ' + (stats.failed || 0) + '</span>';
    html += '<span style="color:var(--warning);">Active: ' + (stats.active || 0) + '</span>';
    html += '</div>';
    if (active.length > 0) {
      html += '<div style="margin-top:6px;font-size:0.72rem;color:var(--warning);">In progress: ' + esc(active[0].title || '') + '</div>';
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span class="text-muted">--</span>'; }
}

async function loadThorCodebaseIndex() {
  var el = document.getElementById('thor-codebase-index');
  if (!el) return;
  try {
    var resp = await fetch('/api/thor/codebase-index');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">' + esc(data.error) + '</span>'; return; }
    var stats = data.stats || {};
    var agents = data.agents || {};
    var stale = data.stale;
    var html = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;">';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;">' + (stats.total_files || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Files</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;">' + (stats.total_functions || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Functions</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;">' + (stats.total_classes || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Classes</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;">' + (stats.total_imports || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Imports</div></div>';
    if (stale) html += '<div style="text-align:center;"><div style="font-size:0.78rem;color:var(--warning);font-weight:600;">STALE</div><div class="text-muted" style="font-size:0.7rem;">Needs rebuild</div></div>';
    html += '</div>';
    // Agent breakdown
    var agentNames = Object.keys(agents);
    if (agentNames.length > 0) {
      html += '<table class="data-table"><thead><tr><th>Agent</th><th>Files</th><th>Functions</th><th>Classes</th><th>Lines</th><th>Avg Cx</th></tr></thead><tbody>';
      for (var i = 0; i < agentNames.length; i++) {
        var a = agents[agentNames[i]];
        var cxColor = a.avg_complexity > 6 ? 'var(--error)' : a.avg_complexity > 4 ? 'var(--warning)' : 'var(--text-secondary)';
        html += '<tr>';
        html += '<td style="font-weight:600;text-transform:uppercase;">' + esc(agentNames[i]) + '</td>';
        html += '<td style="font-family:var(--font-mono);">' + a.files + '</td>';
        html += '<td style="font-family:var(--font-mono);">' + a.functions + '</td>';
        html += '<td style="font-family:var(--font-mono);">' + a.classes + '</td>';
        html += '<td style="font-family:var(--font-mono);">' + (a.total_lines || 0).toLocaleString() + '</td>';
        html += '<td style="font-family:var(--font-mono);color:' + cxColor + ';">' + a.avg_complexity + '</td>';
        html += '</tr>';
      }
      html += '</tbody></table>';
    }
    if (stats.built_at) html += '<div class="text-muted" style="margin-top:6px;font-size:0.7rem;">Built: ' + esc(stats.built_at) + ' (' + (stats.elapsed_s || 0) + 's)</div>';
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span style="color:var(--error);">Failed: ' + esc(e.message) + '</span>'; }
}

async function loadAtlasKBHealth() {
  var barFill = document.getElementById('atlas-kb-bar-fill');
  var summary = document.getElementById('atlas-kb-health-summary');
  if (!summary) return;
  try {
    var resp = await fetch('/api/atlas/kb-health');
    var data = await resp.json();
    if (data.error) { summary.innerHTML = '<span style="color:var(--error);font-size:0.7rem;">' + esc(data.error) + '</span>'; return; }
    var conf = data.confidence || {};
    var total = (data.total_learnings || 0) + (data.total_observations || 0);
    var highPct = total > 0 ? Math.round((conf.high || 0) / total * 100) : 0;
    var contradictions = data.contradictions || 0;
    var stale = (data.age || {}).stale_30d_plus || 0;
    // Health score: high confidence %, minus stale penalty, minus contradiction rate penalty
    var contradictionRate = total > 0 ? contradictions / total : 0;
    var contradictionPenalty = Math.round(contradictionRate * 200);
    var healthScore = Math.min(100, Math.max(0, highPct + 20 - (stale > 10 ? 15 : stale > 5 ? 8 : 0) - contradictionPenalty));
    var barColor = healthScore >= 70 ? '#00ff44' : healthScore >= 40 ? '#FFD700' : '#ff5555';
    if (barFill) { barFill.style.width = healthScore + '%'; barFill.style.background = barColor; }
    var html = '';
    html += '<span style="font-size:0.72rem;font-weight:600;color:' + barColor + ';">' + healthScore + '% Health</span>';
    html += '<span class="text-muted" style="font-size:0.7rem;">' + (data.total_learnings || 0) + ' learnings</span>';
    html += '<span class="text-muted" style="font-size:0.7rem;">' + (data.total_observations || 0) + ' observations</span>';
    html += '<span class="text-muted" style="font-size:0.7rem;">' + (conf.high || 0) + ' high-confidence</span>';
    if (contradictions > 0) html += '<span style="font-size:0.7rem;color:var(--error);">' + contradictions + ' contradictions</span>';
    if (stale > 0) html += '<span style="font-size:0.7rem;color:var(--text-muted);">' + stale + ' stale</span>';
    html += '<button class="btn" onclick="atlasConsolidate()" style="font-size:0.66rem;margin-left:auto;padding:3px 8px;">Consolidate</button>';
    summary.innerHTML = html;
  } catch(e) { summary.innerHTML = '<span style="color:var(--error);font-size:0.7rem;">Failed to load</span>'; }
}

async function atlasConsolidate() {
  try {
    var resp = await fetch('/api/atlas/kb-consolidate', {method: 'POST'});
    var data = await resp.json();
    if (data.status === 'consolidated') {
      loadAtlasKBHealth();
    }
  } catch(e) { console.error('atlasConsolidate:', e); }
}

function setText(id, val) {
  var el = document.getElementById(id);
  if (el) el.textContent = val;
}

async function loadThorQueue() {
  try {
    var resp = await fetch('/api/thor/queue');
    var data = await resp.json();
    var tasks = data.tasks || [];
    var tbody = document.getElementById('thor-queue-tbody');
    if (!tbody) return;
    if (!tasks.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px;">No tasks in queue</td></tr>';
      return;
    }
    var html = '';
    for (var i = 0; i < Math.min(tasks.length, 20); i++) {
      var t = tasks[i];
      var statusColor = t.status === 'completed' ? 'var(--success)' : t.status === 'in_progress' ? 'var(--agent-thor)' : t.status === 'failed' ? 'var(--error)' : 'var(--text-muted)';
      html += '<tr>';
      html += '<td style="font-family:var(--font-mono);font-size:0.72rem;">' + esc(t.id || '').substring(0, 12) + '</td>';
      html += '<td>' + esc(t.title || '') + '</td>';
      html += '<td>' + esc(t.agent || 'general') + '</td>';
      html += '<td><span style="color:' + (t.priority === 'critical' ? 'var(--error)' : t.priority === 'high' ? 'var(--warning)' : 'var(--text-muted)') + ';">' + esc(t.priority || 'normal') + '</span></td>';
      html += '<td><span style="color:' + statusColor + ';">' + esc(t.status || '') + '</span></td>';
      html += '</tr>';
    }
    tbody.innerHTML = html;
  } catch(e) {}
}

async function loadThorResults() {
  try {
    var resp = await fetch('/api/thor/results');
    var data = await resp.json();
    var results = data.results || [];
    var tbody = document.getElementById('thor-results-tbody');
    if (!tbody) return;
    if (!results.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px;">No results yet</td></tr>';
      return;
    }
    var html = '';
    for (var i = 0; i < Math.min(results.length, 15); i++) {
      var r = results[i];
      var files = r.files_written || [];
      var fileCount = Array.isArray(files) ? files.length : Object.keys(files).length;
      var testStatus = r.test_passed === true ? 'PASS' : r.test_passed === false ? 'FAIL' : '--';
      var testColor = r.test_passed === true ? 'var(--success)' : r.test_passed === false ? 'var(--error)' : 'var(--text-muted)';
      var model = (r.model_used || '').replace('claude-', '').substring(0, 12);
      html += '<tr>';
      html += '<td style="font-size:0.72rem;">' + esc(r.task_id || '').substring(0, 12) + '</td>';
      html += '<td style="font-size:0.72rem;">' + esc(model) + '</td>';
      html += '<td>' + fileCount + ' file' + (fileCount !== 1 ? 's' : '') + '</td>';
      html += '<td style="color:' + testColor + ';">' + testStatus + '</td>';
      html += '<td style="font-style:italic;color:var(--agent-thor);font-size:0.72rem;">"' + esc(r.phrase || '') + '"</td>';
      html += '</tr>';
    }
    tbody.innerHTML = html;
  } catch(e) {}
}

var _smartActionsCache = [];
var _agentSmartActionsCache = {};

async function loadAgentSmartActions(agent) {
  var el = document.getElementById(agent + '-smart-actions');
  if (!el) return;
  try {
    var apiAgent = agent === 'overview' ? '' : agent;
    var resp = await fetch('/api/thor/smart-actions?agent=' + encodeURIComponent(apiAgent));
    var data = await resp.json();
    var actions = data.actions || [];
    _agentSmartActionsCache[agent] = actions;
    // Overview tab: only show real operational issues, not Atlas feature suggestions
    if (agent === 'overview') {
      var realActions = [];
      for (var k = 0; k < actions.length; k++) {
        var src = actions[k].source || '';
        // Skip Atlas suggestions — those belong in Atlas tab
        if (src === 'atlas') continue;
        realActions.push(actions[k]);
      }
      if (realActions.length === 0) {
        el.innerHTML = '<span style="font-size:0.76rem;color:var(--success);font-family:var(--font-mono);">All clear — no operational issues.</span>';
        return;
      }
      _agentSmartActionsCache[agent] = realActions;
      var html = '';
      var max = Math.min(realActions.length, 6);
      for (var i = 0; i < max; i++) {
        var a = realActions[i];
        var pClass = 'ov-action-badge-medium';
        var pLabel = 'MEDIUM';
        if (a.priority === 'critical') { pClass = 'ov-action-badge-critical'; pLabel = 'CRITICAL'; }
        else if (a.priority === 'high') { pClass = 'ov-action-badge-high'; pLabel = 'HIGH'; }
        else if (a.priority === 'low') { pClass = 'ov-action-badge-low'; pLabel = 'LOW'; }
        var sourceLabel = '';
        if (a.source === 'robotox') sourceLabel = 'Robotox';
        else if (a.source === 'shelby') sourceLabel = 'Shelby';
        else if (a.source === 'live_data') sourceLabel = 'Live';
        else if (a.source === 'thor') sourceLabel = 'Thor';
        else sourceLabel = a.source || '';
        html += '<div class="ov-action-row">';
        html += '<span class="ov-action-badge ' + pClass + '">' + pLabel + '</span>';
        html += '<span class="ov-action-title">' + esc(a.title) + '</span>';
        html += '<span class="ov-action-source">' + esc(sourceLabel) + '</span>';
        html += '<button class="ov-action-btn" onclick="submitAgentSmartAction(\'overview\',' + i + ')">Resolve</button>';
        html += '</div>';
      }
      el.innerHTML = html;
    } else {
      // Non-overview tabs: keep button style
      var html = '';
      for (var i = 0; i < actions.length; i++) {
        var a = actions[i];
        var color = a.color || '#888';
        var priorityBadge = '';
        if (a.priority === 'critical') priorityBadge = '<span style="background:#ff0000;color:#fff;font-size:0.6rem;padding:1px 5px;border-radius:3px;margin-right:4px;">CRITICAL</span>';
        else if (a.priority === 'high') priorityBadge = '<span style="background:rgba(255,100,0,0.3);color:#ff6644;font-size:0.6rem;padding:1px 5px;border-radius:3px;margin-right:4px;">HIGH</span>';
        var sourceIcon = '';
        if (a.source === 'atlas') sourceIcon = 'Atlas';
        else if (a.source === 'robotox') sourceIcon = 'Robotox';
        else if (a.source === 'shelby') sourceIcon = 'Shelby';
        else if (a.source === 'live_data') sourceIcon = 'Live';
        else if (a.source === 'thor') sourceIcon = 'Thor';
        html += '<button class="btn" onclick="submitAgentSmartAction(\'' + agent + '\',' + i + ')" style="color:' + color + ';border-color:' + color + '33;font-size:0.74rem;padding:6px 12px;position:relative;">';
        html += priorityBadge;
        html += esc(a.title.substring(0, 50));
        if (sourceIcon) html += ' <span style="font-size:0.6rem;opacity:0.6;margin-left:4px;">(' + sourceIcon + ')</span>';
        html += '</button>';
      }
      el.innerHTML = html;
    }
  } catch(e) {
    el.innerHTML = '<span class="text-muted" style="font-size:0.76rem;">Failed to load suggestions.</span>';
  }
}

async function submitAgentSmartAction(agent, index) {
  var actions = _agentSmartActionsCache[agent] || [];
  var a = actions[index];
  if (!a) return;
  if (!confirm('Submit to Thor:\n\n' + a.title + '\n\n' + (a.description || '').substring(0, 200) + '...')) return;
  try {
    var resp = await fetch('/api/thor/quick-action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        title: a.title,
        description: a.description,
        target_files: a.target_files || [],
        agent: a.agent || agent,
        priority: a.priority || 'normal',
        source: a.source || 'smart-action'
      })
    });
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    // Record in action history for learning
    fetch('/api/actions/accept', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        action_id: data.task_id || '',
        title: a.title,
        agent: a.agent || agent,
        source: a.source || 'smart-action',
        description: a.description || ''
      })
    }).catch(function(){});
    showToast('Task submitted: ' + a.title, 'success');
    loadAgentSmartActions(agent);
  } catch(e) {
    alert('Failed: ' + e.message);
  }
}

// ═══════════════════════════════════════════
// Intelligence Tab — Render Functions
// ═══════════════════════════════════════════

function renderIntelGrid(agents) {
  var el = document.getElementById('intel-agent-grid');
  if (!el) return;
  var html = '';
  var totalScore = 0, scoreCount = 0;
  _INTEL_AGENT_ORDER.forEach(function(ag) {
    var s = (agents || {})[ag] || {};
    var r = calcIntelScore(s);
    var g = getIntelGrade(r.score);
    var c = _INTEL_AGENT_COLORS[ag] || '#888';
    totalScore += r.score; scoreCount++;
    // Normalize dimensions to 0-100 for radar
    var dims = [
      Math.round((r.exp / 30) * 100),
      Math.round((r.know / 25) * 100),
      Math.round((r.acc / 20) * 100),
      Math.round((r.mat / 15) * 100),
      Math.round((r.rec / 10) * 100)
    ];
    var dimLabels = ['EXP','KNW','ACC','MAT','REC'];
    var dimMaxes = [30, 25, 20, 15, 10];
    var dimVals = [r.exp, r.know, r.acc, r.mat, r.rec];

    html += '<div class="intel-card" style="border-top-color:' + c + ';">';
    // Radar
    html += '<div class="intel-card-radar">' + radarSVG(80, dims, null, c) + '</div>';
    // Header: name + score + grade
    html += '<div class="intel-card-header">';
    html += '<span class="intel-card-name" style="color:' + c + ';">' + ag + '</span>';
    html += '<span class="intel-card-score" style="color:' + g.color + ';">' + r.score + '</span>';
    html += '<span class="intel-card-grade" style="color:' + g.color + ';background:' + g.color + '15;">' + g.label + '</span>';
    html += '</div>';
    // Dimension bars
    html += '<div class="intel-card-dims">';
    for (var i = 0; i < 5; i++) {
      var pct = dimMaxes[i] > 0 ? Math.round((dimVals[i] / dimMaxes[i]) * 100) : 0;
      html += '<div class="intel-dim-row">';
      html += '<span class="intel-dim-label">' + dimLabels[i] + '</span>';
      html += '<div class="intel-dim-bar"><div class="intel-dim-fill" style="width:' + pct + '%;background:' + c + ';"></div></div>';
      html += '<span class="intel-dim-val">' + dimVals[i] + '</span>';
      html += '</div>';
    }
    html += '</div>';
    html += '</div>';
  });
  el.innerHTML = html || '<span class="text-muted">No data yet</span>';
  var avgEl = document.getElementById('intel-avg-score');
  if (avgEl && scoreCount > 0) avgEl.textContent = Math.round(totalScore / scoreCount);
}

function renderBrainActivity(containerId, activity) {
  var el = document.getElementById(containerId);
  if (!el) return;
  var items = [];
  _INTEL_AGENT_ORDER.forEach(function(ag) {
    var a = activity[ag];
    var c = _INTEL_AGENT_COLORS[ag] || '#888';
    if (a && a.last_call_ts) {
      var ago = Math.round((Date.now() - new Date(a.last_call_ts).getTime()) / 1000);
      var isActive = ago < 300;
      var timeStr = ago < 60 ? ago + 's ago' : ago < 3600 ? Math.floor(ago/60) + 'm ago' : Math.floor(ago/3600) + 'h ago';
      items.push({ag: ag, c: c, active: isActive, task: a.task_type || 'idle', time: timeStr, ago: ago});
    } else {
      items.push({ag: ag, c: c, active: false, task: 'offline', time: '--', ago: 999999});
    }
  });
  items.sort(function(a,b) { return a.ago - b.ago; });
  var html = '';
  items.forEach(function(it) {
    html += '<div class="intel-brain-item">';
    html += '<div class="intel-brain-dot' + (it.active ? ' intel-brain-dot-active' : '') + '" style="background:' + (it.active ? it.c : 'var(--text-muted)') + ';' + (it.active ? 'box-shadow:0 0 6px ' + it.c + ';' : '') + '"></div>';
    html += '<span class="intel-brain-name" style="color:' + it.c + ';">' + it.ag + '</span>';
    html += '<span class="intel-brain-task">' + esc(it.task) + '</span>';
    html += '<span class="intel-brain-time">' + it.time + '</span>';
    html += '</div>';
  });
  el.innerHTML = html || '<span class="text-muted" style="font-size:0.72rem;">No brain activity data</span>';
}

function renderPatternFeed(containerId, patterns) {
  var el = document.getElementById(containerId);
  if (!el) return;
  if (!patterns || patterns.length === 0) {
    el.innerHTML = '<span class="text-muted" style="font-size:0.72rem;">No patterns recorded yet</span>';
    return;
  }
  var html = '';
  var max = Math.min(patterns.length, 15);
  for (var i = 0; i < max; i++) {
    var p = patterns[i];
    var c = _INTEL_AGENT_COLORS[p.agent] || '#888';
    var conf = p.confidence ? (p.confidence * 100).toFixed(0) + '%' : '';
    html += '<div class="intel-pattern-item">';
    html += '<div class="intel-pattern-dot" style="background:' + c + ';"></div>';
    html += '<span class="intel-pattern-agent" style="color:' + c + ';">' + esc(p.agent || '--') + '</span>';
    html += '<span class="intel-pattern-text">' + esc(p.pattern || p.text || '--') + '</span>';
    if (conf) html += '<span class="intel-pattern-conf">' + conf + '</span>';
    html += '</div>';
  }
  el.innerHTML = html;
}

var _chartCostPieInst = null;
function renderCostPie(chartId, localCalls, cloudCalls) {
  var canvas = document.getElementById(chartId);
  if (!canvas || typeof Chart === 'undefined') return;
  if (_chartCostPieInst) { _chartCostPieInst.destroy(); _chartCostPieInst = null; }
  var total = localCalls + cloudCalls;
  if (total === 0) return;
  var localPct = ((localCalls / total) * 100).toFixed(1);
  _chartCostPieInst = new Chart(canvas.getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: ['Local (' + localPct + '%)', 'Cloud (' + (100 - localPct).toFixed(1) + '%)'],
      datasets: [{
        data: [localCalls, cloudCalls],
        backgroundColor: ['#9B59B6', '#E67E22'],
        borderColor: ['#9B59B688', '#E67E2288'],
        borderWidth: 1
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '65%',
      plugins: {
        legend: { position: 'bottom', labels: { color: '#8888aa', font: { size: 10 }, padding: 8 } }
      }
    }
  });
}

var _chartCallTimelineInst = null;
function renderCallTimeline(chartId, calls) {
  var canvas = document.getElementById(chartId);
  if (!canvas || typeof Chart === 'undefined') return;
  if (_chartCallTimelineInst) { _chartCallTimelineInst.destroy(); _chartCallTimelineInst = null; }
  if (!calls || calls.length === 0) return;
  var buckets = {};
  for (var h = 0; h < 24; h++) buckets[h] = {local: 0, cloud: 0};
  calls.forEach(function(call) {
    if (!call.ts) return;
    var hour = parseInt(call.ts.split('T')[1].split(':')[0], 10);
    if (isNaN(hour)) return;
    if ((call.provider || '').indexOf('local') >= 0) buckets[hour].local++;
    else buckets[hour].cloud++;
  });
  var labels = [], localData = [], cloudData = [];
  for (var h = 0; h < 24; h++) {
    labels.push(h + ':00');
    localData.push(buckets[h].local);
    cloudData.push(buckets[h].cloud);
  }
  _chartCallTimelineInst = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        { label: 'Local', data: localData, backgroundColor: '#9B59B688', borderColor: '#9B59B6', borderWidth: 1 },
        { label: 'Cloud', data: cloudData, backgroundColor: '#E67E2288', borderColor: '#E67E22', borderWidth: 1 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { stacked: true, ticks: { color: '#505068', font: { size: 8 }, maxRotation: 0 }, grid: { display: false } },
        y: { stacked: true, ticks: { color: '#505068', font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.04)' } }
      },
      plugins: { legend: { labels: { color: '#8888aa', font: { size: 9 }, padding: 6 } } }
    }
  });
}

var _chartMemoryBarsInst = null;
function renderMemoryBars(chartId, agents) {
  var canvas = document.getElementById(chartId);
  if (!canvas || typeof Chart === 'undefined') return;
  if (_chartMemoryBarsInst) { _chartMemoryBarsInst.destroy(); _chartMemoryBarsInst = null; }
  var labels = [], data = [], colors = [];
  _INTEL_AGENT_ORDER.forEach(function(ag) {
    var s = agents[ag];
    if (!s || s.error) return;
    var kb = s.db_size_kb || 0;
    if (kb > 0) {
      labels.push(ag);
      data.push(kb);
      colors.push(_INTEL_AGENT_COLORS[ag] || '#888');
    }
  });
  if (data.length === 0) return;
  _chartMemoryBarsInst = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'DB Size (KB)',
        data: data,
        backgroundColor: colors.map(function(c) { return c + '88'; }),
        borderColor: colors,
        borderWidth: 1
      }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#505068', font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
        y: { ticks: { color: '#8888aa', font: { size: 10 } }, grid: { display: false } }
      },
      plugins: { legend: { display: false } }
    }
  });
}

function loadIntelSmartActions() {
  var el = document.getElementById('intel-smart-actions');
  if (!el) return;
  fetch('/api/thor/smart-actions?agent=').then(function(r){return r.json();}).then(function(data) {
    var actions = data.actions || [];
    _agentSmartActionsCache['intelligence'] = actions;
    if (actions.length === 0) {
      el.innerHTML = '<span style="font-size:0.76rem;color:var(--success);font-family:var(--font-mono);">All clear — no recommendations.</span>';
      return;
    }
    var countEl = document.getElementById('intel-reco-count');
    if (countEl) countEl.textContent = (data.source_count || actions.length) + ' sources';
    var html = '';
    var max = Math.min(actions.length, 10);
    for (var i = 0; i < max; i++) {
      var a = actions[i];
      var pClass = 'intel-reco-badge-medium';
      var pLabel = 'MEDIUM';
      if (a.priority === 'critical') { pClass = 'intel-reco-badge-critical'; pLabel = 'CRITICAL'; }
      else if (a.priority === 'high') { pClass = 'intel-reco-badge-high'; pLabel = 'HIGH'; }
      else if (a.priority === 'low') { pClass = 'intel-reco-badge-low'; pLabel = 'LOW'; }
      var sourceLabel = a.source || '';
      if (a.source === 'live_data') sourceLabel = 'Live Data';
      else if (a.source) sourceLabel = a.source.charAt(0).toUpperCase() + a.source.slice(1);
      var confStr = a.confidence ? 'conf ' + (a.confidence * 100).toFixed(0) + '%' : '';
      html += '<div class="intel-reco-row">';
      html += '<span class="intel-reco-badge ' + pClass + '">' + pLabel + '</span>';
      html += '<div class="intel-reco-content">';
      html += '<div class="intel-reco-title">' + esc(a.title) + '</div>';
      html += '<div class="intel-reco-meta">';
      if (sourceLabel) html += '<span>' + esc(sourceLabel) + '</span>';
      if (confStr) html += '<span>' + confStr + '</span>';
      html += '</div></div>';
      html += '<button class="intel-reco-btn" onclick="submitAgentSmartAction(\'intelligence\',' + i + ')">Resolve</button>';
      html += '</div>';
    }
    el.innerHTML = html;
  }).catch(function() {
    el.innerHTML = '<span class="text-muted" style="font-size:0.76rem;">Failed to load recommendations.</span>';
  });
}

function refreshIntelligence() {
  Promise.all([
    fetch('/api/llm/status').then(function(r){return r.json();}).catch(function(){return {};}),
    fetch('/api/llm/costs').then(function(r){return r.json();}).catch(function(){return {};}),
    fetch('/api/llm/memory-all').then(function(r){return r.json();}).catch(function(){return {};}),
    fetch('/api/llm/routing').then(function(r){return r.json();}).catch(function(){return {};}),
    fetch('/api/llm/recent-calls').then(function(r){return r.json();}).catch(function(){return {calls:[]};}),
    fetch('/api/llm/brain-activity').then(function(r){return r.json();}).catch(function(){return {activity:{}};}),
    fetch('/api/llm/pattern-feed').then(function(r){return r.json();}).catch(function(){return {patterns:[]};}),
    fetch('/api/llm/cost-savings').then(function(r){return r.json();}).catch(function(){return {};}),
  ]).then(function(results) {
    try {
    var status = results[0], costs = results[1], memoryAll = results[2],
        routing = results[3], recentCalls = results[4], brainActivity = results[5],
        patternFeed = results[6], costSavings = results[7];

    // S2: Agent Intelligence Grid
    renderIntelGrid(memoryAll.agents || {});

    // S4: Brain Activity + Patterns
    renderBrainActivity('intel-brain-activity', brainActivity.activity || {});
    renderPatternFeed('intel-pattern-feed', patternFeed.patterns || []);

    // S5: Charts
    var csLocal = costSavings.local_calls || 0, csCloud = costSavings.cloud_calls || 0;
    if ((csLocal + csCloud) > 0) renderCostPie('chart-cost-pie', csLocal, csCloud);
    setTextSafe('intel-total-saved', '$' + (costSavings.estimated_savings || 0).toFixed(2));
    if (recentCalls.calls) renderCallTimeline('chart-call-timeline', recentCalls.calls);
    if (memoryAll.agents) renderMemoryBars('chart-memory-bars', memoryAll.agents);

    // S1: Server status
    var online = status.server_online;
    setTextSafe('intel-server-badge', online ? 'LLM Online' : 'LLM Offline');
    setTextSafe('intel-server-state', online ? 'ONLINE' : 'OFFLINE');
    var dotEl = document.getElementById('intel-server-dot');
    if (dotEl) {
      dotEl.style.background = online ? 'var(--success)' : 'var(--error)';
      dotEl.style.boxShadow = online ? '0 0 6px rgba(34,170,68,0.6)' : '0 0 6px rgba(231,76,60,0.6)';
    }
    var stateEl = document.getElementById('intel-server-state');
    if (stateEl) stateEl.style.color = online ? 'var(--success)' : 'var(--error)';
    var modelEl = document.getElementById('intel-model-name');
    if (modelEl && status.models) { var m = status.models.local_large || ''; modelEl.textContent = 'Model: ' + (m.split('/').pop() || '--'); }

    // S1: Cost data
    var c24 = costs.last_24h || {};
    var byProv = c24.by_provider || {};
    var localCalls = (byProv.local || {}).calls || 0;
    var cloudCalls = (c24.total_calls || 0) - localCalls;
    setTextSafe('intel-calls-24h', c24.total_calls || 0);
    setTextSafe('intel-local-calls', localCalls);
    setTextSafe('intel-cloud-calls', cloudCalls);
    setTextSafe('intel-cost-24h', '$' + (c24.total_cost || 0).toFixed(4));
    setTextSafe('intel-savings-24h', '$' + (costs.estimated_savings_24h || 0).toFixed(4));

    // S1: Active brains
    var totals = (memoryAll.totals || {});
    setTextSafe('intel-active-brains', (totals.agents_with_memory || 0) + ' / 11');

    // S5: Per-agent memory table
    var memTable = document.getElementById('intel-memory-table');
    if (memTable && memoryAll.agents) {
      var rows = '';
      _INTEL_AGENT_ORDER.forEach(function(ag) {
        var s = memoryAll.agents[ag] || {};
        if (s.error) return;
        var c = _INTEL_AGENT_COLORS[ag] || '#888';
        var wr = s.total_decisions > 0 ? (s.win_rate || 0).toFixed(1) + '%' : '--';
        var wrColor = (s.win_rate || 0) >= 50 ? 'var(--success)' : (s.win_rate || 0) > 0 ? 'var(--error)' : 'var(--text-muted)';
        var dec = s.total_decisions || 0;
        var pat = s.active_patterns || 0;
        rows += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">' +
          '<td style="padding:5px 6px;font-size:0.7rem;"><span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:'+c+';margin-right:5px;vertical-align:middle;"></span><span style="color:'+c+';font-weight:600;text-transform:capitalize;">'+ag+'</span></td>' +
          '<td style="text-align:center;padding:5px 4px;font-family:var(--font-mono);font-size:0.7rem;">'+(dec > 0 ? dec : '<span style="color:var(--text-muted);">-</span>')+'</td>' +
          '<td style="text-align:center;padding:5px 4px;font-family:var(--font-mono);font-size:0.7rem;">'+(pat > 0 ? '<span style="color:var(--success);">'+pat+'</span>' : '<span style="color:var(--text-muted);">-</span>')+'</td>' +
          '<td style="text-align:center;padding:5px 4px;font-family:var(--font-mono);font-size:0.7rem;color:'+wrColor+';">'+wr+'</td>' +
          '<td style="text-align:center;padding:5px 4px;font-family:var(--font-mono);font-size:0.68rem;color:var(--text-muted);">'+((s.db_size_kb||0)>0?(s.db_size_kb).toFixed(0)+'KB':'--')+'</td></tr>';
      });
      memTable.innerHTML = rows || '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:12px;">No memory data</td></tr>';
    }

    // S5: Routing table
    var routeTable = document.getElementById('intel-routing-table');
    if (routeTable && routing.routing) {
      var routeColors = {local_large:'#9B59B6',local_small:'#3498DB',cloud_openai:'#E67E22',cloud_claude:'#E74C3C',cloud_gpt4o:'#F39C12'};
      var rRows = '';
      Object.keys(routing.routing).forEach(function(tt) {
        var route = routing.routing[tt]; var rc = routeColors[route] || '#888';
        rRows += '<tr style="border-bottom:1px solid var(--border);"><td style="padding:4px 6px;">'+tt+'</td><td style="padding:4px 6px;color:'+rc+';font-weight:500;">'+route.replace(/_/g,' ')+'</td></tr>';
      });
      routeTable.innerHTML = rRows;
    }
    var overEl = document.getElementById('intel-agent-overrides');
    if (overEl && routing.agent_overrides) {
      var overHtml = '';
      Object.keys(routing.agent_overrides).forEach(function(ag) {
        var ov = routing.agent_overrides[ag];
        overHtml += '<div style="margin-bottom:2px;"><span style="color:var(--text);font-weight:500;text-transform:capitalize;">'+ag+':</span> ';
        Object.keys(ov).forEach(function(k) { overHtml += k+' &rarr; <span style="color:#E67E22;">'+ov[k]+'</span> '; });
        overHtml += '</div>';
      });
      overEl.innerHTML = overHtml || 'None configured';
    }

    // S6: Recent calls feed
    var feedEl = document.getElementById('intel-activity-feed');
    if (feedEl && recentCalls.calls) {
      var fHtml = '';
      recentCalls.calls.slice(0, 30).forEach(function(call) {
        var provColor = (call.provider||'').indexOf('local')>=0 ? '#9B59B6' : '#E67E22';
        var ts = call.ts ? call.ts.split('T')[1].split('.')[0] : '--';
        var fb = call.fallback ? ' <span style="color:var(--error);font-size:0.6rem;">[FALLBACK]</span>' : '';
        fHtml += '<div style="padding:3px 0;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center;">' +
          '<span style="color:var(--text-muted);min-width:55px;">'+ts+'</span>' +
          '<span style="color:'+provColor+';min-width:50px;font-weight:500;">'+(call.provider||'--')+'</span>' +
          '<span style="min-width:50px;text-transform:capitalize;">'+(call.agent||'--')+'</span>' +
          '<span style="color:var(--text-muted);">'+(call.task_type||'--')+'</span>' +
          '<span style="margin-left:auto;color:var(--text-muted);">'+(call.latency_ms||0)+'ms</span>' +
          '<span style="color:'+((call.cost_usd||0)>0?'var(--warning)':'var(--success)')+';">$'+(call.cost_usd||0).toFixed(4)+'</span>'+fb+'</div>';
      });
      feedEl.innerHTML = fHtml || '<span class="text-muted">No calls recorded yet</span>';
    }

    // S5: Cost by agent
    var costEl = document.getElementById('intel-cost-by-agent');
    if (costEl && c24.by_agent) {
      var cHtml = '<div style="display:flex;flex-wrap:wrap;gap:8px;">';
      Object.keys(c24.by_agent).sort(function(a,b){return (c24.by_agent[b].cost||0)-(c24.by_agent[a].cost||0);}).forEach(function(ag) {
        var d = c24.by_agent[ag]; var agColor = _INTEL_AGENT_COLORS[ag] || '#888';
        cHtml += '<div style="background:var(--glass-bg);padding:6px 10px;border-radius:6px;border:1px solid var(--glass-border);">' +
          '<div style="color:'+agColor+';font-weight:600;text-transform:capitalize;font-size:0.72rem;">'+ag+'</div>' +
          '<div style="font-size:0.68rem;">'+(d.calls||0)+' calls | $'+((d.cost||0)).toFixed(4)+'</div></div>';
      });
      costEl.innerHTML = cHtml + '</div>';
    }

    // S3: Smart Actions / Recommendations
    loadIntelSmartActions();

  } catch(e) {
    console.error('[Intelligence] render error:', e);
  }
  }).catch(function(err) {
    console.error('[Intelligence] fetch error:', err);
  });
}

// ── Thor Wake Control ──
async function thorWakeNow() {
  var btn = document.getElementById('btn-thor-wake');
  var status = document.getElementById('thor-wake-status');
  btn.disabled = true;
  btn.textContent = 'Waking...';
  try {
    var resp = await fetch('/api/thor/wake', {method: 'POST'});
    var data = await resp.json();
    if (data.status === 'waking') {
      status.textContent = 'Thor awake (PID ' + data.pid + ') — ' + data.pending_tasks + ' tasks';
      status.style.color = '#ff6600';
    } else if (data.status === 'already_running') {
      status.textContent = 'Already running (PID ' + data.pid + ')';
      status.style.color = 'var(--warning)';
    } else if (data.status === 'no_tasks') {
      status.textContent = 'No pending tasks — Thor stays asleep';
      status.style.color = 'var(--text-muted)';
    }
  } catch(e) {
    status.textContent = 'Error: ' + e.message;
    status.style.color = 'var(--error)';
  }
  btn.disabled = false;
  btn.textContent = 'Wake Now';
}

async function thorToggleAutoWake() {
  var enabled = document.getElementById('thor-auto-wake').checked;
  await fetch('/api/thor/schedule', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({auto_enabled: enabled})
  });
}

async function thorUpdateInterval() {
  var hours = parseInt(document.getElementById('thor-wake-interval').value);
  await fetch('/api/thor/schedule', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({interval_hours: hours})
  });
}

async function thorLoadWakeStatus() {
  try {
    var resp = await fetch('/api/thor/wake-status');
    var data = await resp.json();
    var statusEl = document.getElementById('thor-wake-status');
    var nextEl = document.getElementById('thor-next-wake');
    var lastEl = document.getElementById('thor-last-wake');
    var autoEl = document.getElementById('thor-auto-wake');
    var intervalEl = document.getElementById('thor-wake-interval');

    if (data.batch_running) {
      statusEl.textContent = 'Running (PID ' + data.batch_pid + ')';
      statusEl.style.color = '#ff6600';
    } else {
      statusEl.textContent = 'Sleeping';
      statusEl.style.color = 'var(--text-muted)';
    }

    if (nextEl) nextEl.textContent = 'Next wake: ' + data.next_wake_in;
    if (lastEl) lastEl.textContent = 'Last wake: ' + data.last_wake_ago + ' ago';
    if (autoEl) autoEl.checked = data.auto_enabled;
    if (intervalEl) intervalEl.value = String(data.interval_hours);
  } catch(e) {}
}

async function thorUpdateSheet() {
  var btn = document.getElementById('btn-update-sheet');
  var status = document.getElementById('thor-action-status');
  btn.disabled = true;
  btn.textContent = 'Updating...';
  status.textContent = '';
  try {
    var resp = await fetch('/api/thor/update-sheet', {method: 'POST'});
    var data = await resp.json();
    if (data.error) {
      status.textContent = 'Error: ' + data.error;
      status.style.color = 'var(--error)';
    } else {
      status.textContent = 'Sheet updated — ' + data.added + ' new entries (total: ' + data.total_rows + ' rows)';
      status.style.color = 'var(--success)';
    }
  } catch(e) {
    status.textContent = 'Failed: ' + e.message;
    status.style.color = 'var(--error)';
  }
  btn.disabled = false;
  btn.textContent = 'Update Progress Sheet';
}

async function thorUpdateDashboard() {
  var btn = document.getElementById('btn-update-dash');
  var status = document.getElementById('thor-action-status');
  btn.disabled = true;
  btn.textContent = 'Submitting...';
  status.textContent = '';
  try {
    var resp = await fetch('/api/thor/update-dashboard', {method: 'POST'});
    var data = await resp.json();
    if (data.error) {
      status.textContent = 'Error: ' + data.error;
      status.style.color = 'var(--error)';
    } else {
      status.textContent = 'Task submitted to Thor (' + data.task_id.substring(0, 12) + ') — he will update the dashboard';
      status.style.color = 'var(--success)';
    }
  } catch(e) {
    status.textContent = 'Failed: ' + e.message;
    status.style.color = 'var(--error)';
  }
  btn.disabled = false;
  btn.textContent = 'Update Dashboard';
}

async function thorUpdateBrotherhood() {
  var btn = document.getElementById('btn-update-brotherhood');
  var status = document.getElementById('thor-action-status');
  btn.disabled = true;
  btn.textContent = 'Submitting...';
  status.textContent = '';
  try {
    var resp = await fetch('/api/thor/update-brotherhood', {method: 'POST'});
    var data = await resp.json();
    if (data.error) {
      status.textContent = 'Error: ' + data.error;
      status.style.color = 'var(--error)';
    } else {
      status.textContent = 'Task submitted to Thor (' + (data.task_id || '').substring(0, 12) + ') — he will update the Brotherhood Sheet';
      status.style.color = 'var(--success)';
    }
  } catch(e) {
    status.textContent = 'Failed: ' + e.message;
    status.style.color = 'var(--error)';
  }
  btn.disabled = false;
  btn.textContent = 'Update Brotherhood Sheet';
}

async function loadSmartActions() {
  var el = document.getElementById('thor-smart-actions');
  if (!el) return;
  try {
    var resp = await fetch('/api/thor/smart-actions');
    var data = await resp.json();
    _smartActionsCache = data.actions || [];
    if (_smartActionsCache.length === 0) {
      el.innerHTML = '<span class="text-muted">No suggested actions right now. All systems nominal.</span>';
      return;
    }
    var html = '';
    for (var i = 0; i < _smartActionsCache.length; i++) {
      var a = _smartActionsCache[i];
      var color = a.color || '#888';
      var priorityBadge = '';
      if (a.priority === 'critical') priorityBadge = '<span style="background:#ff0000;color:#fff;font-size:0.6rem;padding:1px 5px;border-radius:3px;margin-right:4px;">CRITICAL</span>';
      else if (a.priority === 'high') priorityBadge = '<span style="background:rgba(255,100,0,0.3);color:#ff6644;font-size:0.6rem;padding:1px 5px;border-radius:3px;margin-right:4px;">HIGH</span>';
      var sourceIcon = '';
      if (a.source === 'atlas') sourceIcon = 'Atlas';
      else if (a.source === 'robotox') sourceIcon = 'Robotox';
      else if (a.source === 'shelby') sourceIcon = 'Shelby';
      else if (a.source === 'live_data') sourceIcon = 'Live';
      else if (a.source === 'thor') sourceIcon = 'Thor';
      html += '<button class="btn" onclick="submitSmartAction(' + i + ')" style="color:' + color + ';border-color:' + color + '33;font-size:0.74rem;padding:6px 12px;position:relative;">';
      html += priorityBadge;
      html += esc(a.title.substring(0, 50));
      if (sourceIcon) html += ' <span style="font-size:0.6rem;opacity:0.6;margin-left:4px;">(' + sourceIcon + ')</span>';
      html += '</button>';
    }
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<span class="text-muted">Failed to load suggestions.</span>';
  }
}

async function submitSmartAction(index) {
  var a = _smartActionsCache[index];
  if (!a) return;
  if (!confirm('Submit to Thor:\\n\\n' + a.title + '\\n\\n' + (a.description || '').substring(0, 200) + '...')) return;
  try {
    var resp = await fetch('/api/thor/quick-action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        title: a.title,
        description: a.description,
        target_files: a.target_files || [],
        agent: a.agent || '',
        priority: a.priority || 'normal',
        source: a.source || 'smart-action'
      })
    });
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    // Record in action history for learning
    fetch('/api/actions/accept', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        action_id: data.task_id || '',
        title: a.title,
        agent: a.agent || '',
        source: a.source || 'smart-action',
        description: a.description || ''
      })
    }).catch(function(){});
    showToast('Task submitted: ' + a.title, 'success');
    loadThorQueue();
    loadThor();
    loadSmartActions();
  } catch(e) {
    alert('Failed: ' + e.message);
  }
}

async function thorSubmitCustomTask() {
  var title = document.getElementById('thor-custom-title');
  var desc = document.getElementById('thor-custom-desc');
  var files = document.getElementById('thor-custom-files');
  var priority = document.getElementById('thor-custom-priority');
  if (!title || !desc || !title.value.trim() || !desc.value.trim()) {
    alert('Title and description are required');
    return;
  }
  var targetFiles = files && files.value.trim() ? files.value.split(',').map(function(f) { return f.trim(); }) : [];
  try {
    var resp = await fetch('/api/thor/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        title: title.value.trim(),
        description: desc.value.trim(),
        target_files: targetFiles,
        priority: priority ? priority.value : 'normal',
        assigned_by: 'dashboard'
      })
    });
    var data = await resp.json();
    if (data.error) {
      alert('Error: ' + data.error);
      return;
    }
    showToast('Custom task submitted: ' + title.value.trim(), 'success');
    title.value = '';
    desc.value = '';
    if (files) files.value = '';
    loadThorQueue();
    loadThor();
  } catch(e) {
    alert('Failed to submit task: ' + e.message);
  }
}

// Legacy showToast removed — components.js provides stacking toast with typed variants
// (info/success/warning/error/trade), slide-in/out animations, emoji icons

async function loadThorActivity() {
  try {
    var resp = await fetch('/api/thor/activity');
    var data = await resp.json();
    var activities = data.activities || [];
    var el = document.getElementById('thor-activity-log');
    if (!el) return;
    if (!activities.length) {
      el.innerHTML = '<span class="text-muted">No activity yet. Thor is standing by.</span>';
      return;
    }
    var html = '';
    var recent = activities.slice(-30).reverse();
    for (var i = 0; i < recent.length; i++) {
      var a = recent[i];
      var color = a.success ? 'var(--agent-thor)' : 'var(--error)';
      var icon = a.success ? '+' : '!';
      html += '<div style="margin-bottom:4px;">';
      html += '<span style="color:' + color + ';">[' + icon + ']</span> ';
      html += '<span class="text-muted">' + esc(a.time || '') + '</span> ';
      html += '<span style="color:var(--agent-thor);">' + esc(a.action || '') + '</span> ';
      html += esc(a.details || '').substring(0, 100);
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) {}
}

// ══════════════════════════════════════════════
// TIER 2: News Sentiment (Garves)
// ══════════════════════════════════════════════
async function loadNewsSentiment() {
  try {
    var resp = await fetch('/api/garves/news-sentiment');
    var data = await resp.json();
    var el = document.getElementById('garves-sentiment-grid');
    if (!el) return;
    var assets = data.assets || {};
    var keys = Object.keys(assets);
    if (keys.length === 0) {
      el.innerHTML = '<div class="text-muted" style="padding:var(--space-4);grid-column:1/-1;text-align:center;">No sentiment data yet. Atlas will scan on next cycle.</div>';
      return;
    }
    var html = '';
    for (var i = 0; i < keys.length; i++) {
      var asset = keys[i];
      var d = assets[asset];
      var sentiment = (d.sentiment || 'neutral').toLowerCase();
      var score = d.score || 0;
      var color = sentiment === 'bullish' ? 'var(--success)' : sentiment === 'bearish' ? 'var(--error)' : 'var(--text-secondary)';
      var icon = sentiment === 'bullish' ? '&#9650;' : sentiment === 'bearish' ? '&#9660;' : '&#9654;';
      html += '<div class="glass-card" style="text-align:center;">';
      html += '<div style="font-size:0.82rem;font-weight:600;margin-bottom:4px;">' + esc(asset.toUpperCase()) + '</div>';
      html += '<div style="font-size:1.4rem;font-weight:700;color:' + color + ';">' + icon + ' ' + esc(sentiment.toUpperCase()) + '</div>';
      html += '<div style="font-size:0.74rem;color:var(--text-secondary);margin-top:4px;">Score: ' + score.toFixed(1) + '/10</div>';
      if (d.headlines && d.headlines.length > 0) {
        html += '<div style="font-size:0.7rem;color:var(--text-secondary);margin-top:6px;text-align:left;">';
        for (var j = 0; j < Math.min(d.headlines.length, 2); j++) {
          html += '<div style="margin-bottom:2px;">' + esc((d.headlines[j] || '').substring(0, 80)) + '</div>';
        }
        html += '</div>';
      }
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) { console.error('loadNewsSentiment:', e); }
}

// ══════════════════════════════════════════════
// TIER 2: Soren Competitors
// ══════════════════════════════════════════════
async function loadSorenMetrics() {
  try {
    var resp = await fetch('/api/soren/metrics');
    var data = await resp.json();
    setEl('soren-metric-velocity', data.velocity || 0);
    setEl('soren-metric-quality', data.avg_quality ? data.avg_quality.toFixed(1) : '--');
    setEl('soren-metric-approval', data.approval_rate ? data.approval_rate + '%' : '--');
    setEl('soren-metric-pillars', data.active_pillars || 0);
    var detail = document.getElementById('soren-metrics-detail');
    if (detail) {
      var html = '<span class="text-muted">Top pillar:</span> <span style="color:var(--agent-soren);font-weight:600;">' + esc(data.top_pillar || 'none') + '</span>';
      html += ' &nbsp;|&nbsp; <span class="text-muted">Total content:</span> ' + (data.total_content || 0);
      html += ' &nbsp;|&nbsp; <span class="text-muted">Total posted:</span> <span style="color:var(--success);">' + (data.total_posted || 0) + '</span>';
      detail.innerHTML = html;
    }
  } catch(e) { console.warn('loadSorenMetrics error:', e); }
}

async function loadSorenCompetitors() {
  try {
    var resp = await fetch('/api/soren/competitors');
    var data = await resp.json();
    var el = document.getElementById('soren-competitors');
    if (!el) return;
    var competitors = data.competitors || [];
    if (competitors.length === 0) {
      el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">No competitor data yet. Atlas will scan on next cycle.</div>';
      return;
    }
    var html = '';
    if (data.takeaways && data.takeaways.length > 0) {
      html += '<div style="margin-bottom:var(--space-4);padding:8px 12px;background:rgba(204,102,255,0.06);border-radius:6px;border:1px solid rgba(204,102,255,0.15);">';
      html += '<div style="font-size:0.74rem;font-weight:600;color:var(--agent-soren);margin-bottom:4px;">Takeaways</div>';
      for (var i = 0; i < data.takeaways.length; i++) {
        html += '<div style="font-size:0.74rem;color:var(--text-secondary);margin-bottom:2px;">' + esc(data.takeaways[i]) + '</div>';
      }
      html += '</div>';
    }
    html += '<div style="font-size:0.72rem;color:var(--text-secondary);margin-bottom:6px;">Found ' + competitors.length + ' competitors (' + (data.new_count || 0) + ' new) &middot; Scanned ' + esc(data.scanned_at || 'never') + '</div>';
    for (var i = 0; i < Math.min(competitors.length, 10); i++) {
      var c = competitors[i];
      var isNew = c.is_new ? '<span class="badge badge-success" style="font-size:0.6rem;margin-left:6px;">NEW</span>' : '';
      html += '<div style="margin-bottom:8px;padding:6px 8px;background:var(--surface-secondary);border-radius:4px;">';
      html += '<div style="font-size:0.76rem;font-weight:500;">' + esc(c.title || '') + isNew + '</div>';
      html += '<div style="font-size:0.7rem;color:var(--text-secondary);margin-top:2px;">' + esc((c.snippet || '').substring(0, 150)) + '</div>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) { console.error('loadSorenCompetitors:', e); }
}

// ══════════════════════════════════════════════
// TIER 2: Shelby Decision Memory
// ══════════════════════════════════════════════
async function loadShelbyDecisions() {
  try {
    var resp = await fetch('/api/shelby/decisions?limit=15');
    var data = await resp.json();
    var el = document.getElementById('shelby-decisions');
    if (!el) return;
    var decisions = data.decisions || [];
    var stats = data.stats || {};
    if (decisions.length === 0) {
      el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">No decisions recorded yet.</div>';
      return;
    }
    var html = '<div style="display:flex;gap:var(--space-4);margin-bottom:var(--space-4);font-size:0.74rem;">';
    html += '<span style="color:var(--agent-shelby);">Total: ' + (stats.total || 0) + '</span>';
    if (stats.top_tags && stats.top_tags.length > 0) {
      html += '<span class="text-muted">Tags: ' + stats.top_tags.slice(0, 5).map(function(t) { return esc(t); }).join(', ') + '</span>';
    }
    html += '</div>';
    for (var i = 0; i < decisions.length; i++) {
      var d = decisions[i];
      var tags = (d.tags || []).map(function(t) { return '<span class="badge" style="font-size:0.6rem;margin-right:4px;">' + esc(t) + '</span>'; }).join('');
      html += '<div style="margin-bottom:8px;padding:8px 10px;background:var(--surface-secondary);border-radius:6px;border-left:3px solid var(--agent-shelby);">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">';
      html += '<span style="font-size:0.78rem;font-weight:600;">' + esc(d.topic || '') + '</span>';
      html += '<span class="text-muted" style="font-size:0.68rem;">' + esc((d.timestamp || '').substring(0, 16)) + '</span>';
      html += '</div>';
      html += '<div style="font-size:0.74rem;color:var(--text-primary);margin-bottom:4px;">' + esc(d.decision || '') + '</div>';
      if (d.context) html += '<div style="font-size:0.7rem;color:var(--text-secondary);">' + esc(d.context) + '</div>';
      if (tags) html += '<div style="margin-top:4px;">' + tags + '</div>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) { console.error('loadShelbyDecisions:', e); }
}

// ══════════════════════════════════════════════
// TIER 2: Atlas Trade Analysis
// ══════════════════════════════════════════════
async function loadTradeAnalysis() {
  try {
    var resp = await fetch('/api/atlas/trade-analysis');
    var data = await resp.json();
    var el = document.getElementById('atlas-trade-analysis');
    if (!el) return;
    if (!data.analyzed_at && !data.total_trades) {
      el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">No trade analysis yet. Atlas will analyze on next cycle.</div>';
      return;
    }
    var html = '<div style="font-size:0.72rem;color:var(--text-secondary);margin-bottom:8px;">Analyzed ' + (data.total_trades || 0) + ' trades &middot; ' + esc(data.analyzed_at || '') + '</div>';
    // Suggestions
    if (data.suggestions && data.suggestions.length > 0) {
      html += '<div style="margin-bottom:var(--space-4);padding:8px 12px;background:rgba(34,170,68,0.06);border-radius:6px;border:1px solid rgba(34,170,68,0.15);">';
      html += '<div style="font-size:0.74rem;font-weight:600;color:var(--agent-atlas);margin-bottom:4px;">AI Suggestions</div>';
      for (var i = 0; i < data.suggestions.length; i++) {
        html += '<div style="font-size:0.74rem;color:var(--text-secondary);margin-bottom:2px;">' + esc(data.suggestions[i]) + '</div>';
      }
      html += '</div>';
    }
    // Key stats
    var stats = [];
    if (data.by_asset) {
      var assetKeys = Object.keys(data.by_asset);
      for (var i = 0; i < assetKeys.length; i++) {
        var a = data.by_asset[assetKeys[i]];
        stats.push(assetKeys[i].toUpperCase() + ': ' + (a.total || 0) + ' trades, ' + ((a.win_rate || 0)).toFixed(1) + '% WR');
      }
    }
    if (stats.length > 0) {
      html += '<div style="font-size:0.74rem;color:var(--text-secondary);">' + stats.join(' &middot; ') + '</div>';
    }
    el.innerHTML = html;
  } catch(e) { console.error('loadTradeAnalysis:', e); }
}

// ══════════════════════════════════════════════
// TIER 2: Lisa Go-Live Toggle
// ══════════════════════════════════════════════
async function loadLisaGoLive() {
  try {
    var resp = await fetch('/api/lisa/live-config');
    var config = await resp.json();
    var el = document.getElementById('lisa-golive-toggles');
    if (!el) return;
    var platforms = ['instagram', 'tiktok', 'x'];
    var html = '';
    for (var i = 0; i < platforms.length; i++) {
      var p = platforms[i];
      var c = config[p] || {};
      var isLive = c.live === true;
      var statusColor = isLive ? 'var(--success)' : 'var(--text-secondary)';
      var statusText = isLive ? 'LIVE' : 'DRY RUN';
      var btnText = isLive ? 'Go Dry Run' : 'Go Live';
      var btnClass = isLive ? 'btn btn-warning' : 'btn btn-success';
      html += '<div class="glass-card" style="text-align:center;">';
      html += '<div style="font-size:0.82rem;font-weight:600;margin-bottom:6px;">' + esc(p.charAt(0).toUpperCase() + p.slice(1)) + '</div>';
      html += '<div style="font-size:1.1rem;font-weight:700;color:' + statusColor + ';margin-bottom:8px;">' + statusText + '</div>';
      if (isLive && c.enabled_at) {
        html += '<div style="font-size:0.68rem;color:var(--text-secondary);margin-bottom:6px;">Since: ' + esc(c.enabled_at.substring(0, 16)) + '</div>';
      }
      html += '<button class="' + btnClass + '" onclick="toggleLisaGoLive(\'' + p + '\',' + !isLive + ')" style="font-size:0.72rem;">' + btnText + '</button>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) { console.error('loadLisaGoLive:', e); }
}

async function toggleLisaGoLive(platform, enable) {
  try {
    var resp = await fetch('/api/lisa/go-live', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({platform: platform, enable: enable})
    });
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    loadLisaGoLive();
  } catch(e) { alert('Failed: ' + e.message); }
}

// ══════════════════════════════════════════════
// TIER 2: Lisa Comment Analyzer
// ══════════════════════════════════════════════
async function loadLisaCommentStats() {
  try {
    var resp = await fetch('/api/lisa/comments?limit=10');
    var data = await resp.json();
    var el = document.getElementById('lisa-comment-stats');
    if (!el) return;
    var stats = data.stats || {};
    if (!stats.total || stats.total === 0) {
      el.innerHTML = '<div class="text-muted" style="grid-column:1/-1;text-align:center;">No comments analyzed yet. Use the form below to test.</div>';
      return;
    }
    var html = '';
    html += '<div class="stat-card" data-accent="lisa" style="padding:8px;"><div class="stat-value" style="font-size:1rem;">' + (stats.total || 0) + '</div><div class="stat-label">Analyzed</div></div>';
    html += '<div class="stat-card" data-accent="success" style="padding:8px;"><div class="stat-value" style="font-size:1rem;">' + (stats.positive || 0) + '</div><div class="stat-label">Positive</div></div>';
    html += '<div class="stat-card" data-accent="error" style="padding:8px;"><div class="stat-value" style="font-size:1rem;">' + (stats.negative || 0) + '</div><div class="stat-label">Negative</div></div>';
    el.innerHTML = html;
  } catch(e) { console.error('loadLisaCommentStats:', e); }
}

async function lisaAnalyzeComment() {
  var comment = document.getElementById('lisa-comment-input').value.trim();
  var platform = document.getElementById('lisa-comment-platform').value;
  var el = document.getElementById('lisa-comment-result');
  if (!comment) { el.innerHTML = '<span class="text-muted">Enter a comment first.</span>'; return; }
  el.innerHTML = '<span class="text-muted">Analyzing...</span>';
  try {
    var resp = await fetch('/api/lisa/comment/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({comment: comment, platform: platform})
    });
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">Error: ' + esc(data.error) + '</span>'; return; }
    var sentColor = data.sentiment === 'positive' ? 'var(--success)' : data.sentiment === 'negative' ? 'var(--error)' : 'var(--text-secondary)';
    var html = '<div style="margin-bottom:6px;">';
    html += '<span style="font-weight:600;">Sentiment:</span> <span style="color:' + sentColor + ';">' + esc(data.sentiment || 'unknown') + '</span>';
    html += ' &middot; <span style="font-weight:600;">Intent:</span> ' + esc(data.intent || 'unknown');
    html += ' &middot; <span style="font-weight:600;">Priority:</span> ' + esc(data.priority || 'low');
    html += '</div>';
    if (data.suggested_reply) {
      html += '<div style="padding:8px 10px;background:rgba(204,102,255,0.06);border-radius:6px;border:1px solid rgba(204,102,255,0.15);margin-top:6px;">';
      html += '<div style="font-size:0.72rem;font-weight:600;color:var(--agent-soren);margin-bottom:4px;">Suggested Reply (Soren Voice):</div>';
      html += '<div>' + esc(data.suggested_reply) + '</div>';
      html += '</div>';
    }
    el.innerHTML = html;
    loadLisaCommentStats();
  } catch(e) { el.innerHTML = '<span style="color:var(--error);">Failed: ' + esc(e.message) + '</span>'; }
}

// ══════════════════════════════════════════════
// TIER 2: Robotox Dependency Checker
// ══════════════════════════════════════════════
async function loadRobotoxDeps() {
  try {
    var resp = await fetch('/api/robotox/dependencies');
    var data = await resp.json();
    var el = document.getElementById('robotox-deps-content');
    var lastEl = document.getElementById('robotox-deps-last-check');
    if (!el) return;
    if (data.error || !data.checked_at) {
      el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">No dependency report yet. Click "Run Check" to scan.</div>';
      return;
    }
    if (lastEl) lastEl.textContent = 'Last check: ' + (data.checked_at || 'never');
    var html = '';
    // CVE section
    var cves = data.cve_check || {};
    var totalCves = cves.total_cves || 0;
    if (totalCves > 0) {
      html += '<div style="margin-bottom:var(--space-4);padding:8px 12px;background:rgba(255,68,68,0.08);border-radius:6px;border:1px solid rgba(255,68,68,0.2);">';
      html += '<div style="font-size:0.78rem;font-weight:600;color:var(--error);margin-bottom:4px;">CVEs Found: ' + totalCves + '</div>';
      var vulns = cves.vulnerabilities || [];
      for (var i = 0; i < Math.min(vulns.length, 5); i++) {
        var v = vulns[i];
        html += '<div style="font-size:0.72rem;margin-bottom:2px;"><span style="color:var(--error);">' + esc(v.id || '') + '</span> ' + esc(v.package || '') + ' ' + esc(v.affected || '') + '</div>';
      }
      html += '</div>';
    } else {
      html += '<div style="margin-bottom:var(--space-3);padding:6px 10px;background:rgba(0,255,136,0.06);border-radius:6px;font-size:0.76rem;color:var(--success);">No CVEs found. All clear.</div>';
    }
    // Outdated packages
    var outdated = data.outdated || {};
    var outdatedTotal = outdated.total || 0;
    if (outdatedTotal > 0) {
      html += '<div style="font-size:0.76rem;font-weight:600;margin-bottom:4px;">Outdated Packages: ' + outdatedTotal + '</div>';
      html += '<table class="data-table" style="font-size:0.72rem;"><thead><tr><th>Package</th><th>Current</th><th>Latest</th></tr></thead><tbody>';
      var pkgs = outdated.packages || [];
      for (var i = 0; i < Math.min(pkgs.length, 10); i++) {
        var p = pkgs[i];
        html += '<tr><td>' + esc(p.name || '') + '</td><td>' + esc(p.current || '') + '</td><td style="color:var(--success);">' + esc(p.latest || '') + '</td></tr>';
      }
      html += '</tbody></table>';
    } else {
      html += '<div style="font-size:0.76rem;color:var(--success);">All packages up to date.</div>';
    }
    el.innerHTML = html;
  } catch(e) { console.error('loadRobotoxDeps:', e); }
}

async function robotoxDepCheck() {
  var el = document.getElementById('robotox-deps-content');
  if (el) el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">Running dependency check...</div>';
  try {
    var resp = await fetch('/api/robotox/dependencies/check', {method: 'POST'});
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">Error: ' + esc(data.error) + '</span>'; return; }
    loadRobotoxDeps();
  } catch(e) { el.innerHTML = '<span style="color:var(--error);">Failed: ' + esc(e.message) + '</span>'; }
}

// ══════════════════════════════════════════════
// ROBOTOX: Log Watcher — Smart Pattern Detection
// ══════════════════════════════════════════════
async function loadLogWatcherAlerts() {
  var patternsEl = document.getElementById('robotox-log-patterns');
  var alertsEl = document.getElementById('robotox-log-alerts');
  if (!patternsEl && !alertsEl) return;
  try {
    var resp = await fetch('/api/robotox/log-alerts');
    var data = await resp.json();
    if (data.error) return;

    // Render pattern status badges
    var patterns = data.patterns || [];
    if (patternsEl && patterns.length > 0) {
      var html = '';
      for (var i = 0; i < patterns.length; i++) {
        var p = patterns[i];
        var color = p.severity === 'critical' ? 'var(--error)' : p.severity === 'warning' ? 'var(--warning)' : 'var(--text-muted)';
        var bg = p.active_hits > 0 ? 'rgba(255,68,68,0.15)' : 'rgba(255,255,255,0.05)';
        html += '<div style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;font-size:0.7rem;font-family:var(--font-mono);background:' + bg + ';border:1px solid rgba(255,255,255,0.08);">';
        html += '<span style="width:6px;height:6px;border-radius:50%;background:' + (p.active_hits > 0 ? color : 'var(--text-muted)') + ';"></span>';
        html += '<span style="color:' + (p.active_hits > 0 ? color : 'var(--text-secondary)') + ';">' + esc(p.title) + '</span>';
        if (p.active_hits > 0) html += ' <span style="color:' + color + ';font-weight:600;">(' + p.active_hits + ')</span>';
        html += '</div>';
      }
      patternsEl.innerHTML = html;
    }

    // Render recent alerts
    var alerts = data.alerts || [];
    if (alertsEl) {
      if (alerts.length === 0) {
        alertsEl.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">No log alerts yet — all clear.</div>';
      } else {
        var html = '<table class="data-table"><thead><tr><th>Time</th><th>Agent</th><th>Issue</th><th>Hits</th><th>Action</th></tr></thead><tbody>';
        var shown = alerts.slice(-15).reverse();
        for (var i = 0; i < shown.length; i++) {
          var a = shown[i];
          var sevColor = a.severity === 'critical' ? 'var(--error)' : a.severity === 'warning' ? 'var(--warning)' : 'var(--text-secondary)';
          var ts = (a.timestamp || '').substring(11, 19);
          html += '<tr>';
          html += '<td style="font-family:var(--font-mono);font-size:0.72rem;">' + esc(ts) + '</td>';
          html += '<td style="color:' + sevColor + ';font-weight:600;">' + esc((a.agent||'').toUpperCase()) + '</td>';
          html += '<td style="font-size:0.74rem;">' + esc(a.title || '') + '</td>';
          html += '<td style="font-family:var(--font-mono);text-align:center;">' + (a.hit_count || 0) + 'x</td>';
          html += '<td style="font-size:0.72rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(a.action || '') + '">' + esc(a.action || '') + '</td>';
          html += '</tr>';
        }
        html += '</tbody></table>';
        alertsEl.innerHTML = html;
      }
    }
  } catch(e) { console.error('loadLogWatcherAlerts:', e); }
}

// ══════════════════════════════════════════════
// ROBOTOX: Performance Baselines
// ══════════════════════════════════════════════
async function loadRobotoxPerf() {
  var el = document.getElementById('robotox-perf-content');
  if (!el) return;
  try {
    var resp = await fetch('/api/robotox/perf');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">' + esc(data.error) + '</span>'; return; }
    var current = data.current || {};
    var baselines = data.baselines || {};
    var agents = Object.keys(current);
    if (agents.length === 0) { el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">No performance data yet — waiting for scan cycle.</div>'; return; }
    var html = '<table class="data-table"><thead><tr><th>Agent</th><th>CPU %</th><th>Mem MB</th><th>Threads</th><th>Baseline Mem</th><th>Status</th></tr></thead><tbody>';
    for (var i = 0; i < agents.length; i++) {
      var a = agents[i];
      var c = current[a] || {};
      var b = baselines[a] || {};
      var bMem = (b.mem_mb || {}).mean || 0;
      var memRatio = bMem > 0 ? (c.mem_mb || 0) / bMem : 0;
      var status = memRatio > 1.5 ? '<span style="color:var(--error);">SPIKE</span>' : memRatio > 1.2 ? '<span style="color:var(--warning);">HIGH</span>' : '<span style="color:var(--success);">OK</span>';
      html += '<tr>';
      html += '<td style="font-weight:600;text-transform:uppercase;">' + esc(a) + '</td>';
      html += '<td style="font-family:var(--font-mono);">' + (c.cpu_pct || 0).toFixed(1) + '%</td>';
      html += '<td style="font-family:var(--font-mono);">' + (c.mem_mb || 0).toFixed(1) + '</td>';
      html += '<td style="font-family:var(--font-mono);text-align:center;">' + (c.threads || 0) + '</td>';
      html += '<td style="font-family:var(--font-mono);">' + (bMem > 0 ? bMem.toFixed(1) : '--') + '</td>';
      html += '<td>' + status + '</td>';
      html += '</tr>';
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span style="color:var(--error);">Failed: ' + esc(e.message) + '</span>'; }
}

// ══════════════════════════════════════════════
// ROBOTOX: External Dependency Health
// ══════════════════════════════════════════════
async function loadRobotoxDepHealth() {
  var el = document.getElementById('robotox-dep-health-content');
  if (!el) return;
  el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">Checking dependencies...</div>';
  try {
    var resp = await fetch('/api/robotox/dep-health');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">' + esc(data.error) + '</span>'; return; }
    var deps = data.dependencies || {};
    var names = Object.keys(deps);
    if (names.length === 0) { el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">No data — click Check Now to test.</div>'; return; }
    var html = '<div style="display:flex;flex-wrap:wrap;gap:8px;">';
    for (var i = 0; i < names.length; i++) {
      var n = names[i];
      var d = deps[n] || {};
      var ok = d.status === 'ok' || d.reachable === true;
      var color = ok ? 'var(--success)' : 'var(--error)';
      var bg = ok ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)';
      var latency = d.latency_ms ? d.latency_ms + 'ms' : '--';
      html += '<div style="display:flex;align-items:center;gap:6px;padding:6px 12px;border-radius:8px;background:' + bg + ';border:1px solid rgba(255,255,255,0.06);">';
      html += '<span style="width:8px;height:8px;border-radius:50%;background:' + color + ';"></span>';
      html += '<span style="font-weight:600;font-size:0.76rem;">' + esc(n) + '</span>';
      html += '<span style="font-family:var(--font-mono);font-size:0.7rem;color:var(--text-muted);">' + latency + '</span>';
      html += '</div>';
    }
    html += '</div>';
    if (data.last_check) {
      html += '<div class="text-muted" style="margin-top:8px;font-size:0.7rem;">Last check: ' + esc(data.last_check) + '</div>';
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span style="color:var(--error);">Failed: ' + esc(e.message) + '</span>'; }
}

// ══════════════════════════════════════════════
// ROBOTOX: Alert Correlator Stats
// ══════════════════════════════════════════════
async function loadRobotoxCorrelator() {
  var el = document.getElementById('robotox-correlator-content');
  if (!el) return;
  try {
    var resp = await fetch('/api/robotox/correlator');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">' + esc(data.error) + '</span>'; return; }
    var html = '<div style="display:flex;gap:16px;flex-wrap:wrap;">';
    html += '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:700;color:var(--text-primary);">' + (data.total_received || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Alerts Received</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:700;color:var(--success);">' + (data.suppressed || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Suppressed</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:700;color:var(--warning);">' + (data.correlated || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Correlated</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:700;color:var(--agent-sentinel);">' + (data.noise_reduction_pct || 0) + '%</div><div class="text-muted" style="font-size:0.7rem;">Noise Reduced</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:700;color:var(--text-muted);">' + (data.pending || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Pending</div></div>';
    html += '</div>';
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span style="color:var(--error);">Failed: ' + esc(e.message) + '</span>'; }
}

// ══════════════════════════════════════════════
// ROBOTOX: Deploy Watches (Auto-Rollback)
// ══════════════════════════════════════════════
async function loadRobotoxDeployWatches() {
  var el = document.getElementById('robotox-deploy-content');
  if (!el) return;
  try {
    var resp = await fetch('/api/robotox/deploy-watches');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">' + esc(data.error) + '</span>'; return; }
    var watches = data.active_watches || [];
    var history = data.rollback_history || [];
    var html = '';
    if (watches.length > 0) {
      html += '<div style="margin-bottom:12px;"><span style="font-weight:600;font-size:0.78rem;color:var(--warning);">Active Watches (' + watches.length + ')</span></div>';
      for (var i = 0; i < watches.length; i++) {
        var w = watches[i];
        var elapsed = Math.round((Date.now()/1000 - (w.started_at || 0)) / 60);
        var soakMin = Math.round(((w.soak_until || 0) - (w.started_at || 0)) / 60);
        var pct = Math.min(100, Math.round(elapsed / soakMin * 100));
        html += '<div style="padding:8px 12px;background:rgba(255,180,0,0.08);border-radius:8px;border:1px solid rgba(255,180,0,0.2);margin-bottom:6px;">';
        html += '<div style="display:flex;justify-content:space-between;"><span style="font-weight:600;">' + esc(w.agent || '').toUpperCase() + '</span><span class="text-muted" style="font-size:0.72rem;">Task: ' + esc(w.task_id || '') + '</span></div>';
        html += '<div style="margin-top:4px;height:4px;background:rgba(255,255,255,0.1);border-radius:2px;overflow:hidden;"><div style="height:100%;width:' + pct + '%;background:var(--warning);border-radius:2px;transition:width 0.3s;"></div></div>';
        html += '<div class="text-muted" style="font-size:0.7rem;margin-top:3px;">' + elapsed + '/' + soakMin + ' min soaked | ' + (w.files || []).length + ' files</div>';
        html += '</div>';
      }
    } else {
      html += '<div class="text-muted" style="font-size:0.76rem;margin-bottom:8px;">No active watches.</div>';
    }
    if (history.length > 0) {
      html += '<div style="margin-top:8px;"><span style="font-weight:600;font-size:0.78rem;color:var(--error);">Rollback History (' + history.length + ')</span></div>';
      html += '<table class="data-table" style="margin-top:6px;"><thead><tr><th>Time</th><th>Agent</th><th>Reason</th><th>Files</th></tr></thead><tbody>';
      var shown = history.slice(-5).reverse();
      for (var i = 0; i < shown.length; i++) {
        var r = shown[i];
        var ts = (r.timestamp || '').substring(11, 19);
        html += '<tr>';
        html += '<td style="font-family:var(--font-mono);font-size:0.72rem;">' + esc(ts) + '</td>';
        html += '<td style="font-weight:600;text-transform:uppercase;">' + esc(r.agent || '') + '</td>';
        html += '<td style="font-size:0.72rem;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(r.reason || '') + '">' + esc(r.reason || '') + '</td>';
        html += '<td style="text-align:center;">' + (r.files_rolled_back || []).length + '</td>';
        html += '</tr>';
      }
      html += '</tbody></table>';
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span style="color:var(--error);">Failed: ' + esc(e.message) + '</span>'; }
}

// ══════════════════════════════════════════════
// TIER 2: System Health (Overview)
// ══════════════════════════════════════════════
async function loadSystemHealth() {
  try {
    var resp = await fetch('/api/health');
    return await resp.json();
  } catch(e) { return null; }
}

// ══════════════════════════════════════════════
// BRAIN MANAGEMENT — Store/Delete Notes
// ══════════════════════════════════════════════
async function loadBrainNotes(agent) {
  var el = document.getElementById(agent + '-brain-list');
  if (!el) return;
  try {
    var resp = await fetch('/api/brain/' + agent);
    var data = await resp.json();
    var notes = data.notes || [];
    if (notes.length === 0) {
      el.innerHTML = '<div class="text-muted" style="text-align:center;padding:12px;font-size:0.74rem;">No notes yet. Add knowledge, commands, or memories above.</div>';
      return;
    }
    var typeColors = {note: '#3b82f6', command: '#ef4444', memory: '#22c55e'};
    var typeIcons = {note: '&#x1F4DD;', command: '&#x26A1;', memory: '&#x1F9E0;'};
    var typeLabels = {note: 'NOTE', command: 'CMD', memory: 'MEM'};
    var agentColor = AGENT_COLORS[agent] || '#3b82f6';
    var html = '';
    for (var i = notes.length - 1; i >= 0; i--) {
      var n = notes[i];
      var ts = n.created_at ? n.created_at.substring(11, 16) : '';
      var dateStr = n.created_at ? n.created_at.substring(5, 10) : '';
      var ntype = n.type || 'note';
      var tcolor = typeColors[ntype] || '#3b82f6';
      var ticon = typeIcons[ntype] || '&#x1F4DD;';
      html += '<div style="background:rgba(255,255,255,0.02);border-radius:8px;padding:10px 12px;margin-bottom:6px;border:1px solid rgba(255,255,255,0.04);transition:border-color 0.15s;" onmouseenter="this.style.borderColor=\'rgba(255,255,255,0.1)\'" onmouseleave="this.style.borderColor=\'rgba(255,255,255,0.04)\'">';
      // Header row: icon + topic + actions
      html += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">';
      html += '<span style="font-size:0.78rem;">' + ticon + '</span>';
      html += '<span style="flex:1;font-weight:600;font-size:0.78rem;color:var(--text-primary);">' + esc(n.topic) + '</span>';
      html += '<span style="background:' + tcolor + '18;color:' + tcolor + ';padding:1px 6px;border-radius:10px;font-size:0.58rem;font-weight:700;letter-spacing:0.05em;">' + typeLabels[ntype] + '</span>';
      // Execute button
      html += '<button onclick="executeBrainNote(\'' + agent + '\',' + JSON.stringify(n.content).replace(/'/g, "\\'") + ')" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:var(--text-secondary);cursor:pointer;font-size:0.64rem;padding:2px 8px;border-radius:6px;transition:all 0.15s;" onmouseenter="this.style.background=\'rgba(59,130,246,0.2)\';this.style.color=\'#60a5fa\'" onmouseleave="this.style.background=\'rgba(255,255,255,0.06)\';this.style.color=\'var(--text-secondary)\'" title="Send to agent">Run</button>';
      // Delete button
      html += '<button onclick="deleteBrainNote(\'' + agent + '\',\'' + n.id + '\')" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:0.9rem;padding:0 4px;opacity:0.5;transition:opacity 0.15s;" onmouseenter="this.style.opacity=\'1\';this.style.color=\'var(--error)\'" onmouseleave="this.style.opacity=\'0.5\';this.style.color=\'var(--text-muted)\'" title="Delete">&times;</button>';
      html += '</div>';
      // Content
      html += '<div style="font-size:0.74rem;color:var(--text-secondary);line-height:1.5;white-space:pre-wrap;word-break:break-word;">' + esc(n.content) + '</div>';
      // AI Response
      if (n.response) {
        html += '<div style="margin-top:6px;padding:6px 10px;background:rgba(0,0,0,0.25);border-left:2px solid ' + agentColor + ';border-radius:0 6px 6px 0;">';
        html += '<div style="font-size:0.74rem;color:var(--text-secondary);font-style:italic;white-space:pre-wrap;word-break:break-word;">' + esc(n.response) + '</div>';
        html += '</div>';
      }
      // Footer: timestamp
      html += '<div style="margin-top:4px;font-size:0.62rem;color:var(--text-muted);">' + dateStr + ' ' + ts + '</div>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<span style="color:var(--error);">Failed to load: ' + esc(e.message) + '</span>';
  }
}

function setBrainType(agent, type, btn) {
  var typeEl = document.getElementById(agent + '-brain-type');
  if (typeEl) typeEl.value = type;
  // Update pill styles
  var pills = btn.parentElement.querySelectorAll('.brain-pill');
  for (var i = 0; i < pills.length; i++) pills[i].classList.remove('active');
  btn.classList.add('active');
}

async function executeBrainNote(agent, content) {
  // Brain note execution (chat removed)
  alert('Note sent to ' + agent + ': ' + content.substring(0, 100));
}

async function addBrainNote(agent) {
  var topicEl = document.getElementById(agent + '-brain-topic');
  var contentEl = document.getElementById(agent + '-brain-content');
  var typeEl = document.getElementById(agent + '-brain-type');
  if (!topicEl || !contentEl) return;
  var topic = topicEl.value.trim();
  var content = contentEl.value.trim();
  var noteType = typeEl ? typeEl.value : 'note';
  if (!topic || !content) { alert('Both topic and content are required.'); return; }
  try {
    var resp = await fetch('/api/brain/' + agent, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({topic: topic, content: content, type: noteType})
    });
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    topicEl.value = '';
    contentEl.value = '';
    // Reset pills to Note
    var pills = document.querySelectorAll('#' + agent + '-brain-type-pills .brain-pill');
    for (var p = 0; p < pills.length; p++) {
      pills[p].classList.remove('active');
      if (pills[p].getAttribute('data-type') === 'note') pills[p].classList.add('active');
    }
    if (typeEl) typeEl.value = 'note';
    loadBrainNotes(agent);
    var noteId = data.note ? data.note.id : null;
    if (noteId) {
      var statusEl = document.getElementById(agent + '-brain-list');
      if (statusEl) {
        var spinner = document.createElement('div');
        spinner.id = 'brain-interpret-spinner';
        spinner.style.cssText = 'text-align:center;padding:8px;font-size:0.74rem;color:var(--text-secondary);font-style:italic;';
        spinner.textContent = 'Interpreting...';
        statusEl.insertBefore(spinner, statusEl.firstChild);
      }
      var polls = 0;
      var pollInterval = setInterval(async function() {
        polls++;
        try {
          var r2 = await fetch('/api/brain/' + agent);
          var d2 = await r2.json();
          var found = (d2.notes || []).find(function(nn) { return nn.id === noteId; });
          if (found && found.response) {
            clearInterval(pollInterval);
            loadBrainNotes(agent);
          } else if (polls >= 15) {
            clearInterval(pollInterval);
            var sp = document.getElementById('brain-interpret-spinner');
            if (sp) sp.remove();
          }
        } catch(pe) {
          clearInterval(pollInterval);
        }
      }, 1000);
    }
  } catch(e) { alert('Failed: ' + e.message); }
}


// ══════════════════════════════════════════════
// COMMAND TABLES — Agent capabilities registry
// ══════════════════════════════════════════════
var _commandsCache = {};
var _commandsFetched = false;

async function fetchAllCommands() {
  if (_commandsFetched) return;
  try {
    var resp = await fetch('/api/commands');
    var data = await resp.json();
    var agents = data.agents || [];
    for (var i = 0; i < agents.length; i++) {
      var a = agents[i];
      var key = (a.agent_name || '').toLowerCase();
      _commandsCache[key] = a;
      if (key === 'command center dashboard') _commandsCache['dashboard'] = a;
    }
    _commandsFetched = true;
  } catch(e) { console.error('fetchAllCommands:', e); }
}

function renderCommandTable(agent) {
  var el = document.getElementById(agent + '-commands');
  if (!el) return;
  var data = _commandsCache[agent];
  if (!data || !data.commands || data.commands.length === 0) {
    el.innerHTML = '<span class="text-muted">No commands registered.</span>';
    return;
  }
  var cmds = data.commands;
  var typeColors = {cli: '#00CED1', api: '#FFD700', tool: '#9370DB', capability: '#32CD32'};
  var html = '<table class="data-table" style="font-size:0.74rem;"><thead><tr><th style="width:35%;">Command</th><th style="width:12%;">Type</th><th>Description</th></tr></thead><tbody>';
  for (var i = 0; i < cmds.length; i++) {
    var c = cmds[i];
    var typeColor = typeColors[c.type] || '#888';
    html += '<tr>';
    html += '<td style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-primary);word-break:break-all;">' + esc(c.name) + '</td>';
    html += '<td><span style="background:' + typeColor + '22;color:' + typeColor + ';padding:1px 6px;border-radius:4px;font-size:0.66rem;font-weight:600;">' + esc(c.type || 'other') + '</span></td>';
    html += '<td style="font-size:0.72rem;color:var(--text-secondary);">' + esc(c.description || '') + '</td>';
    html += '</tr>';
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function loadCommandTable(agent) {
  await fetchAllCommands();
  renderCommandTable(agent);
}

async function deleteBrainNote(agent, noteId) {
  if (!confirm('Delete this brain note?')) return;
  try {
    var resp = await fetch('/api/brain/' + agent + '/' + noteId, {method: 'DELETE'});
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    loadBrainNotes(agent);
  } catch(e) { alert('Failed: ' + e.message); }
}

// ── Shared Event Bus ──

var EVENT_SEVERITY_COLORS = {info: 'var(--text-secondary)', warning: 'var(--warning)', critical: 'var(--error)', error: 'var(--error)'};
var EVENT_AGENT_COLORS = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',lisa:'#ff8800',robotox:'#00ff44',thor:'#ff6600',hawk:'#FFD700',viper:'#00ff88'};

function eventTimeAgo(ts) {
  if (!ts) return '';
  try {
    var d = new Date(ts);
    var now = new Date();
    var diff = Math.floor((now - d) / 1000);
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  } catch(e) { return ''; }
}

function renderEventFeed(events, containerId) {
  var el = document.getElementById(containerId);
  if (!el) return;
  if (!events || events.length === 0) {
    el.innerHTML = '<span class="text-muted" style="padding:8px;">No events yet. Agents will publish here as they work.</span>';
    return;
  }
  var html = '';
  for (var i = 0; i < events.length; i++) {
    var e = events[i];
    var agentColor = EVENT_AGENT_COLORS[e.agent] || '#888';
    var sevColor = EVENT_SEVERITY_COLORS[e.severity] || 'var(--text-secondary)';
    var agentLabel = (AGENT_NAMES[e.agent] || e.agent || '?');
    html += '<div style="display:flex;align-items:flex-start;gap:8px;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
    html += '<span style="width:6px;height:6px;border-radius:50%;background:' + agentColor + ';margin-top:5px;flex-shrink:0;"></span>';
    html += '<div style="flex:1;min-width:0;">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
    html += '<span style="font-weight:600;color:' + agentColor + ';font-size:0.72rem;">' + esc(agentLabel) + '</span>';
    html += '<span style="color:' + sevColor + ';font-size:0.66rem;padding:1px 5px;border-radius:3px;background:rgba(255,255,255,0.04);">' + esc(e.type || '') + '</span>';
    html += '</div>';
    html += '<div style="color:var(--text-primary);font-size:0.72rem;margin-top:1px;">' + esc(e.summary || '') + '</div>';
    html += '<div style="color:var(--text-muted);font-size:0.64rem;margin-top:1px;">' + eventTimeAgo(e.ts) + '</div>';
    html += '</div></div>';
  }
  el.innerHTML = html;
}

async function loadEventFeed() {
  try {
    var resp = await fetch('/api/events?limit=15');
    var data = await resp.json();
    renderEventFeed(data.events || [], 'event-feed');
  } catch(e) {}
}

// ═══════════════════════════════════════════════════════
// AGENT COMMS — Overview chat-like event feed
// ═══════════════════════════════════════════════════════
async function loadAgentComms() {
  try {
    var resp = await fetch('/api/events?limit=20');
    var data = await resp.json();
    var events = data.events || [];
    var el = document.getElementById('agent-comms-feed');
    if (!el) return;
    if (!events.length) {
      el.innerHTML = '<span class="text-muted" style="padding:8px;">No agent communications yet. Agents publish here as they work.</span>';
      return;
    }
    var html = '';
    for (var i = 0; i < events.length; i++) {
      var e = events[i];
      var agentColor = EVENT_AGENT_COLORS[e.agent] || '#888';
      var agentName = AGENT_NAMES[e.agent] || e.agent || '?';
      var typeLabel = (e.type || '').replace(/_/g, ' ');
      html += '<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
      html += '<div style="width:28px;height:28px;border-radius:50%;background:' + agentColor + '22;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:0.62rem;font-weight:700;color:' + agentColor + ';">' + (AGENT_INITIALS[e.agent] || '??') + '</div>';
      html += '<div style="flex:1;min-width:0;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
      html += '<span style="font-weight:600;color:' + agentColor + ';font-size:0.74rem;">' + esc(agentName) + '</span>';
      html += '<span class="text-muted" style="font-size:0.64rem;">' + eventTimeAgo(e.ts) + '</span>';
      html += '</div>';
      html += '<div style="color:var(--text-primary);font-size:0.74rem;margin-top:1px;">' + esc(e.summary || typeLabel) + '</div>';
      if (e.severity && e.severity !== 'info') {
        var sc = EVENT_SEVERITY_COLORS[e.severity] || 'var(--text-muted)';
        html += '<span style="font-size:0.62rem;color:' + sc + ';padding:1px 5px;border-radius:3px;background:rgba(255,255,255,0.04);margin-top:2px;display:inline-block;">' + esc(e.severity) + '</span>';
      }
      html += '</div></div>';
    }
    el.innerHTML = html;
  } catch(e) {}
}

// ═══════════════════════════════════════════════════════
// AGENT ACTIVITY — Per-agent event feed in each tab
// ═══════════════════════════════════════════════════════
var _agentActivityMap = {
  'garves-live': {agent: 'garves', id: 'garves-activity-feed'},
  'soren': {agent: 'soren', id: 'soren-activity-feed'},
  'shelby': {agent: 'shelby', id: 'shelby-activity-feed'},
  'atlas': {agent: 'atlas', id: 'atlas-activity-feed'},
  'lisa': {agent: 'lisa', id: 'lisa-activity-feed'},
  'hawk': {agent: 'hawk', id: 'hawk-activity-feed'},
  'sentinel': {agent: 'robotox', id: 'robotox-activity-feed'},
  'thor': {agent: 'thor', id: 'thor-activity-feed'},
};

async function loadAgentActivity(tabName) {
  var cfg = _agentActivityMap[tabName];
  if (!cfg) return;
  try {
    var resp = await fetch('/api/events?agent=' + cfg.agent + '&limit=8');
    var data = await resp.json();
    var events = data.events || [];
    var el = document.getElementById(cfg.id);
    if (!el) return;
    if (!events.length) {
      el.innerHTML = '<span class="text-muted">No recent activity from this agent.</span>';
      return;
    }
    var html = '';
    for (var i = 0; i < events.length; i++) {
      var e = events[i];
      var sevColor = EVENT_SEVERITY_COLORS[e.severity] || 'var(--text-secondary)';
      var typeLabel = (e.type || '').replace(/_/g, ' ');
      html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.03);">';
      html += '<div style="flex:1;min-width:0;">';
      html += '<span style="color:' + sevColor + ';font-size:0.66rem;padding:0 4px;border-radius:2px;background:rgba(255,255,255,0.04);margin-right:6px;">' + esc(typeLabel) + '</span>';
      html += '<span style="color:var(--text-primary);font-size:0.72rem;">' + esc(e.summary || '') + '</span>';
      html += '</div>';
      html += '<span class="text-muted" style="font-size:0.62rem;flex-shrink:0;margin-left:8px;">' + eventTimeAgo(e.ts) + '</span>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) {}
}

// ═══════════════════════════════════════════════════════
// OVERVIEW — Resource vitals + errors strip
// ═══════════════════════════════════════════════════════
async function loadOverviewVitals() {
  try {
    var resp = await fetch('/api/system/metrics');
    var d = await resp.json();
    var cpu = (d.cpu || {}).percent || 0;
    var mem = (d.memory || {}).percent || 0;
    var disk = (d.disk || {}).percent || 0;
    function vColor(v) { return v > 85 ? 'var(--error)' : v > 60 ? 'var(--warning)' : 'var(--success)'; }
    var cpuVal = document.getElementById('ov-cpu-val');
    var cpuFill = document.getElementById('ov-cpu-fill');
    if (cpuVal) cpuVal.textContent = cpu + '%';
    if (cpuVal) cpuVal.style.color = vColor(cpu);
    if (cpuFill) { cpuFill.style.width = cpu + '%'; cpuFill.style.background = vColor(cpu); }
    var memVal = document.getElementById('ov-mem-val');
    var memFill = document.getElementById('ov-mem-fill');
    if (memVal) memVal.textContent = mem + '%';
    if (memVal) memVal.style.color = vColor(mem);
    if (memFill) { memFill.style.width = mem + '%'; memFill.style.background = vColor(mem); }
    var diskVal = document.getElementById('ov-disk-val');
    var diskFill = document.getElementById('ov-disk-fill');
    if (diskVal) diskVal.textContent = disk + '%';
    if (diskVal) diskVal.style.color = vColor(disk);
    if (diskFill) { diskFill.style.width = disk + '%'; diskFill.style.background = vColor(disk); }
    // Recent errors
    var errs = d.errors || [];
    var countEl = document.getElementById('ov-error-count');
    if (countEl) {
      countEl.textContent = errs.length;
      countEl.style.background = errs.length > 0 ? 'var(--error)' : 'var(--success)';
    }
    var errEl = document.getElementById('ov-errors');
    if (errEl) {
      if (!errs.length) {
        errEl.innerHTML = '<span style="color:var(--success);">No recent errors detected.</span>';
      } else {
        var eHtml = '';
        for (var i = 0; i < errs.length; i++) {
          var err = errs[i];
          eHtml += '<div style="padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
          eHtml += '<div style="display:flex;justify-content:space-between;">';
          eHtml += '<span style="color:var(--error);font-size:0.72rem;">' + esc(err.pattern || err.type || 'error') + '</span>';
          eHtml += '<span class="text-muted" style="font-size:0.64rem;">' + esc(err.agent || '') + ' | ' + eventTimeAgo(err.timestamp || err.ts) + '</span>';
          eHtml += '</div>';
          eHtml += '<div class="text-muted" style="font-size:0.68rem;margin-top:1px;">' + esc((err.message || err.line || '').substring(0, 120)) + '</div>';
          eHtml += '</div>';
        }
        errEl.innerHTML = eHtml;
      }
    }
    // LLM Server status
    try {
      var llmResp = await fetch('/api/llm/status');
      var llmData = await llmResp.json();
      var online = llmData.server_online;
      var llmBadge = document.getElementById('ov-llm-badge');
      if (llmBadge) {
        llmBadge.innerHTML = '<span class="' + (online ? 'dot-online' : 'dot-offline') + '"></span> LLM ' + (online ? 'Online' : 'Offline');
        llmBadge.style.background = online ? 'rgba(0,255,136,0.08)' : 'rgba(231,76,60,0.08)';
        llmBadge.style.borderColor = online ? 'rgba(0,255,136,0.15)' : 'rgba(231,76,60,0.15)';
        llmBadge.style.color = online ? 'var(--success)' : 'var(--error)';
      }
    } catch(e) {}

  } catch(e) {
    console.warn('Overview vitals load error:', e);
  }
}

// ═══════════════════════════════════════════════════════
// SYSTEM TAB — Real-time infrastructure monitoring
// ═══════════════════════════════════════════════════════
async function loadSystemTab() {
  try {
    var resp = await fetch('/api/system/metrics');
    var d = await resp.json();

    // 6 stat cards at top
    function sysColor(v) { return v > 85 ? 'var(--error)' : v > 60 ? 'var(--warning)' : 'var(--success)'; }
    var cpuPct = (d.cpu || {}).percent || 0;
    var memPct = (d.memory || {}).percent || 0;
    var diskPct = (d.disk || {}).percent || 0;
    var scpu = document.getElementById('sys-cpu');
    if (scpu) { scpu.textContent = cpuPct + '%'; scpu.style.color = sysColor(cpuPct); }
    var smem = document.getElementById('sys-memory');
    if (smem) { smem.textContent = memPct + '%'; smem.style.color = sysColor(memPct); }
    var sdisk = document.getElementById('sys-disk');
    if (sdisk) { sdisk.textContent = diskPct + '%'; sdisk.style.color = sysColor(diskPct); }
    var sup = document.getElementById('sys-uptime');
    if (sup) sup.textContent = (d.uptime && typeof d.uptime === 'object') ? (d.uptime.text || '--') : (d.uptime || '--');
    var sproc = document.getElementById('sys-processes');
    if (sproc) sproc.textContent = (d.processes || []).length;
    var sports = document.getElementById('sys-ports');
    if (sports) sports.textContent = (d.ports || []).length;

    // Process table
    var ptbody = document.getElementById('sys-process-tbody');
    if (ptbody) {
      var procs = d.processes || [];
      if (!procs.length) {
        ptbody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;">No Python processes found</td></tr>';
      } else {
        var ph = '';
        for (var i = 0; i < procs.length; i++) {
          var p = procs[i];
          var agentColor = AGENT_COLORS[p.agent] || '#888';
          var agentName = AGENT_NAMES[p.agent] || p.agent || 'unknown';
          ph += '<tr>';
          ph += '<td style="color:' + agentColor + ';font-weight:600;">' + esc(agentName) + '</td>';
          ph += '<td>' + p.pid + '</td>';
          ph += '<td>' + p.cpu_percent + '</td>';
          ph += '<td>' + p.mem_mb + '</td>';
          ph += '<td>' + esc(p.uptime) + '</td>';
          ph += '<td style="font-size:0.68rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(p.command) + '</td>';
          ph += '</tr>';
        }
        ptbody.innerHTML = ph;
      }
    }

    // LaunchAgents
    var laEl = document.getElementById('sys-launchagents');
    if (laEl) {
      var las = d.launchagents || [];
      if (!las.length) {
        laEl.innerHTML = '<span class="text-muted">No LaunchAgents found.</span>';
      } else {
        var laHtml = '';
        for (var j = 0; j < las.length; j++) {
          var la = las[j];
          var statusColor = la.loaded ? (la.pid ? 'var(--success)' : 'var(--warning)') : 'var(--error)';
          var statusText = la.loaded ? (la.pid ? 'Running (PID ' + la.pid + ')' : 'Loaded (not running)') : 'Not loaded';
          laHtml += '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
          laHtml += '<span style="font-size:0.72rem;">' + esc(la.label) + '</span>';
          laHtml += '<span style="color:' + statusColor + ';font-size:0.72rem;">' + statusText + '</span>';
          laHtml += '</div>';
        }
        laEl.innerHTML = laHtml;
      }
    }

    // Ports
    var plEl = document.getElementById('sys-port-list');
    if (plEl) {
      var ports = d.ports || [];
      if (!ports.length) {
        plEl.innerHTML = '<span class="text-muted">No listening ports found.</span>';
      } else {
        var plHtml = '';
        for (var k = 0; k < ports.length; k++) {
          var pt = ports[k];
          plHtml += '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
          plHtml += '<span style="font-size:0.72rem;">:' + pt.port + ' <span class="text-muted" style="font-size:0.66rem;">(' + esc(pt.process) + ')</span></span>';
          plHtml += '<span style="color:var(--success);font-size:0.72rem;">' + esc(pt.service) + '</span>';
          plHtml += '</div>';
        }
        plEl.innerHTML = plHtml;
      }
    }

    // Recent errors
    var errEl = document.getElementById('sys-errors');
    if (errEl) {
      var errs = d.errors || [];
      if (!errs.length) {
        errEl.innerHTML = '<span style="color:var(--success);">No recent errors detected.</span>';
      } else {
        var eHtml = '';
        for (var m = 0; m < errs.length; m++) {
          var err = errs[m];
          eHtml += '<div style="padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);">';
          eHtml += '<div style="display:flex;justify-content:space-between;">';
          eHtml += '<span style="color:var(--error);font-size:0.72rem;">' + esc(err.pattern || err.type || 'error') + '</span>';
          eHtml += '<span class="text-muted" style="font-size:0.64rem;">' + esc(err.agent || '') + ' | ' + eventTimeAgo(err.timestamp || err.ts) + '</span>';
          eHtml += '</div>';
          eHtml += '<div class="text-muted" style="font-size:0.68rem;margin-top:1px;">' + esc((err.message || err.line || '').substring(0, 120)) + '</div>';
          eHtml += '</div>';
        }
        errEl.innerHTML = eHtml;
      }
    }

  } catch(e) {
    console.error('System tab load error:', e);
  }

  // Codebase stats
  try {
    var csResp = await fetch('/api/system/codebase-stats');
    var cs = await csResp.json();

    var cfEl = document.getElementById('sys-code-files');
    if (cfEl) cfEl.textContent = (cs.total_files || 0).toLocaleString();

    var clEl = document.getElementById('sys-code-lines');
    if (clEl) clEl.textContent = (cs.total_lines || 0).toLocaleString();

    var csizeEl = document.getElementById('sys-code-size');
    if (csizeEl) csizeEl.textContent = cs.size_formatted || '--';

    var cpEl = document.getElementById('sys-code-projects');
    if (cpEl) cpEl.textContent = Object.keys(cs.by_project || {}).length;

    var topExts = cs.top_extensions || [];
    var teEl = document.getElementById('sys-code-top-ext');
    if (teEl && topExts.length) teEl.textContent = topExts[0].ext;

    var pyEl = document.getElementById('sys-code-py-lines');
    if (pyEl) {
      var pyLines = 0;
      for (var te = 0; te < topExts.length; te++) {
        if (topExts[te].ext === '.py') { pyLines = topExts[te].lines; break; }
      }
      pyEl.textContent = pyLines.toLocaleString();
    }

    var cbEl = document.getElementById('sys-code-breakdown');
    if (cbEl && cs.by_project) {
      var cbHtml = '';
      var projects = Object.keys(cs.by_project);
      for (var cp = 0; cp < projects.length; cp++) {
        var pkey = projects[cp];
        var proj = cs.by_project[pkey];
        var pctOfTotal = cs.total_lines > 0 ? Math.round(proj.lines / cs.total_lines * 100) : 0;
        var barW = Math.max(pctOfTotal, 1);
        var pColor = AGENT_COLORS[pkey] || AGENT_COLORS[proj.label.toLowerCase().split('/')[0]] || '#888';
        cbHtml += '<div style="margin-bottom:8px;">';
        cbHtml += '<div style="display:flex;justify-content:space-between;font-size:0.72rem;margin-bottom:2px;">';
        cbHtml += '<span style="color:' + pColor + ';font-weight:600;">' + esc(proj.label) + ' <span class="text-muted" style="font-weight:400;">(' + pkey + '/)</span></span>';
        cbHtml += '<span class="text-muted">' + proj.files + ' files | ' + proj.lines.toLocaleString() + ' lines | ' + proj.size_kb + ' KB</span>';
        cbHtml += '</div>';
        cbHtml += '<div style="width:100%;height:6px;background:rgba(255,255,255,0.06);border-radius:3px;overflow:hidden;">';
        cbHtml += '<div style="width:' + barW + '%;height:100%;background:' + pColor + ';border-radius:3px;transition:width 0.5s ease;"></div>';
        cbHtml += '</div></div>';
      }
      // Extension breakdown
      if (topExts.length) {
        cbHtml += '<div style="margin-top:10px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.06);">';
        cbHtml += '<span class="text-muted" style="font-size:0.68rem;">BY LANGUAGE</span>';
        var extColors = {'.py': '#3572A5', '.html': '#e34c26', '.js': '#f1e05a', '.css': '#563d7c', '.json': '#292929', '.md': '#083fa1', '.sh': '#89e051'};
        for (var ex = 0; ex < topExts.length; ex++) {
          var extItem = topExts[ex];
          var extPct = cs.total_lines > 0 ? Math.round(extItem.lines / cs.total_lines * 100) : 0;
          var eColor = extColors[extItem.ext] || '#888';
          cbHtml += '<div style="display:flex;align-items:center;gap:8px;padding:2px 0;font-size:0.72rem;">';
          cbHtml += '<span style="width:8px;height:8px;border-radius:50%;background:' + eColor + ';display:inline-block;"></span>';
          cbHtml += '<span style="width:50px;">' + esc(extItem.ext) + '</span>';
          cbHtml += '<div style="flex:1;height:4px;background:rgba(255,255,255,0.06);border-radius:2px;overflow:hidden;">';
          cbHtml += '<div style="width:' + Math.max(extPct, 1) + '%;height:100%;background:' + eColor + ';border-radius:2px;"></div>';
          cbHtml += '</div>';
          cbHtml += '<span class="text-muted" style="width:80px;text-align:right;">' + extItem.lines.toLocaleString() + ' (' + extPct + '%)</span>';
          cbHtml += '</div>';
        }
        cbHtml += '</div>';
      }
      cbEl.innerHTML = cbHtml;
    }
  } catch(e) {
    console.error('Codebase stats error:', e);
  }

  // Also load event feed in system tab
  try {
    var evResp = await fetch('/api/events?limit=20');
    var evData = await evResp.json();
    renderEventFeed(evData.events || [], 'sys-event-feed');
  } catch(e) {}
}

async function sysAction(action) {
  var resultEl = document.getElementById('sys-action-result');
  if (resultEl) resultEl.innerHTML = '<span style="color:var(--warning);">Executing ' + esc(action) + '...</span>';
  try {
    var resp = await fetch('/api/system/action/' + action, {method: 'POST'});
    var data = await resp.json();
    if (resultEl) {
      if (data.success) {
        resultEl.innerHTML = '<span style="color:var(--success);">' + esc(data.message || 'Done') + '</span>';
      } else {
        resultEl.innerHTML = '<span style="color:var(--error);">' + esc(data.error || 'Failed') + '</span>';
      }
    }
    // Refresh after action
    setTimeout(function() { loadSystemTab(); }, 2000);
  } catch(e) {
    if (resultEl) resultEl.innerHTML = '<span style="color:var(--error);">Error: ' + esc(e.message) + '</span>';
  }
}

async function atlasEventBus() {
  var reportEl = document.getElementById('atlas-report');
  if (!reportEl) return;
  reportEl.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-6);">Loading event bus...</div>';
  try {
    var evResp = await fetch('/api/events?limit=50');
    var evData = await evResp.json();
    var stResp = await fetch('/api/events/stats');
    var stData = await stResp.json();
    var events = evData.events || [];
    var html = '<h3 style="margin-bottom:12px;">Event Bus — Stats</h3>';
    html += '<div style="display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;">';
    html += '<div style="padding:8px 14px;background:rgba(255,255,255,0.04);border-radius:6px;"><div style="font-size:1.2rem;font-weight:700;">' + (stData.total || 0) + '</div><div style="font-size:0.68rem;color:var(--text-secondary);">Total Events</div></div>';
    var byAgent = stData.by_agent || {};
    var agentKeys = Object.keys(byAgent);
    for (var a = 0; a < agentKeys.length; a++) {
      var ak = agentKeys[a];
      var ac = EVENT_AGENT_COLORS[ak] || '#888';
      html += '<div style="padding:8px 14px;background:rgba(255,255,255,0.04);border-radius:6px;"><div style="font-size:1.2rem;font-weight:700;color:' + ac + ';">' + byAgent[ak] + '</div><div style="font-size:0.68rem;color:var(--text-secondary);">' + esc(AGENT_NAMES[ak] || ak) + '</div></div>';
    }
    html += '</div>';
    html += '<h3 style="margin-bottom:8px;">Recent Events (' + events.length + ')</h3>';
    html += '<div id="atlas-event-feed" style="max-height:400px;overflow-y:auto;"></div>';
    reportEl.innerHTML = html;
    renderEventFeed(events, 'atlas-event-feed');
  } catch(e) {
    reportEl.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-6);">Failed to load event bus: ' + esc(e.message) + '</div>';
  }
}

// ══════════════════════════════════════
// CONTENT INTELLIGENCE PIPELINE
// ══════════════════════════════════════

async function atlasContentFeed(target, feedType) {
  var resultEl = document.getElementById('atlas-content-feed-result');
  if (!resultEl) return;
  resultEl.style.display = 'block';
  var targetLabel = target === 'soren' ? 'Soren' : 'Lisa';
  var typeLabel = {niche_trends:'Niche Trends',viral_hooks:'Viral Hooks',strategy:'Strategy',revenue:'Revenue Intel'}[feedType] || feedType;
  resultEl.innerHTML = '<div style="color:var(--agent-atlas);display:flex;align-items:center;gap:8px;"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--agent-atlas);border-radius:50%;animation:atlas-spin 0.8s linear infinite;"></div>Pushing ' + esc(typeLabel) + ' to ' + esc(targetLabel) + '...</div>';
  try {
    var resp = await fetch('/api/atlas/feed-content', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({target:target,type:feedType})
    });
    var data = await resp.json();
    if (data.error) {
      resultEl.innerHTML = '<div style="color:var(--error);">' + esc(data.error) + '</div>';
      return;
    }
    var color = target === 'soren' ? 'var(--agent-soren)' : 'var(--agent-lisa)';
    resultEl.innerHTML = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"><span style="color:' + color + ';font-weight:600;">&#x2713; ' + esc(data.message || 'Intel pushed') + '</span></div>'
      + '<div style="font-size:0.72rem;color:var(--text-muted);">Topic: ' + esc(data.topic || '') + '</div>';
    // Refresh pipeline status
    loadAtlasContentPipeline();
  } catch(e) {
    resultEl.innerHTML = '<div style="color:var(--error);">Failed: ' + esc(e.message) + '</div>';
  }
}

async function atlasNicheScan() {
  var resultEl = document.getElementById('atlas-content-feed-result');
  if (!resultEl) return;
  resultEl.style.display = 'block';
  resultEl.innerHTML = '<div style="color:var(--agent-atlas);display:flex;align-items:center;gap:8px;"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--agent-atlas);border-radius:50%;animation:atlas-spin 0.8s linear infinite;"></div>Scanning dark motivation niche...</div>';
  try {
    var resp = await fetch('/api/atlas/niche-scan', {method:'POST'});
    var data = await resp.json();
    if (data.error) {
      resultEl.innerHTML = '<div style="color:var(--error);">' + esc(data.error) + '</div>';
      return;
    }
    var html = '<div style="color:var(--agent-atlas);font-weight:600;margin-bottom:6px;">&#x2713; Niche Scan Complete — ' + (data.total || 0) + ' found, ' + (data.new_count || 0) + ' new</div>';
    if (data.takeaways && data.takeaways.length > 0) {
      html += '<div style="margin-top:6px;">';
      data.takeaways.forEach(function(t) {
        html += '<div style="padding:3px 0;padding-left:10px;border-left:2px solid var(--agent-soren);margin-bottom:4px;font-size:0.74rem;color:var(--text-secondary);">' + esc(t) + '</div>';
      });
      html += '</div>';
    }
    resultEl.innerHTML = html;
  } catch(e) {
    resultEl.innerHTML = '<div style="color:var(--error);">Scan failed: ' + esc(e.message) + '</div>';
  }
}

async function loadAtlasContentPipeline() {
  try {
    var resp = await fetch('/api/atlas/content-pipeline');
    var data = await resp.json();
    var stats = data.stats || {};
    var totalEl = document.getElementById('atlas-pipe-total');
    var sorenEl = document.getElementById('atlas-pipe-soren');
    var lisaEl = document.getElementById('atlas-pipe-lisa');
    var lastEl = document.getElementById('atlas-pipe-last');
    if (totalEl) totalEl.textContent = stats.total_feeds || 0;
    if (sorenEl) sorenEl.textContent = stats.soren_feeds || 0;
    if (lisaEl) lisaEl.textContent = stats.lisa_feeds || 0;
    if (lastEl) {
      var feeds = data.recent_feeds || [];
      if (feeds.length > 0) {
        var last = feeds[feeds.length - 1];
        var ts = last.timestamp ? new Date(last.timestamp) : null;
        var ago = ts ? Math.round((Date.now() - ts.getTime()) / 60000) : null;
        var agoStr = ago !== null ? (ago < 60 ? ago + 'm ago' : Math.round(ago/60) + 'h ago') : '';
        lastEl.textContent = 'Last: ' + (last.type || '').replace(/_/g,' ') + ' \u2192 ' + (last.target || '') + (agoStr ? ' (' + agoStr + ')' : '');
      }
    }
    // Render recent feeds log
    var feedsEl = document.getElementById('atlas-recent-feeds');
    if (feedsEl && data.recent_feeds && data.recent_feeds.length > 0) {
      var html = '<div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Recent Feeds</div>';
      data.recent_feeds.slice().reverse().forEach(function(f) {
        var color = f.target === 'soren' ? 'var(--agent-soren)' : 'var(--agent-lisa)';
        var ts = f.timestamp ? new Date(f.timestamp) : null;
        var timeStr = ts ? ts.toLocaleTimeString('en-US', {hour:'numeric',minute:'2-digit',hour12:true}) : '';
        html += '<div style="display:flex;gap:8px;align-items:center;padding:3px 0;font-size:0.72rem;border-bottom:1px solid rgba(255,255,255,0.03);">';
        html += '<span style="color:' + color + ';font-weight:600;min-width:50px;">' + esc(f.target || '') + '</span>';
        html += '<span style="color:var(--text-secondary);">' + esc((f.type || '').replace(/_/g,' ')) + '</span>';
        html += '<span style="color:var(--text-muted);margin-left:auto;font-size:0.66rem;">' + esc(timeStr) + '</span>';
        html += '</div>';
      });
      feedsEl.innerHTML = html;
    }
  } catch(e) { /* silent */ }
}

// ══════════════════════════════════════
// HAWK TAB — Market Predator
// ══════════════════════════════════════
var _hawkScanPoller = null;
var _hawkOppsCache = [];

async function loadHawkTab() {
  try {
    var resp = await fetch('/api/hawk');
    var d = await resp.json();
    var s = d.summary || {};
    var st = d.status || {};
    animateCount('hawk-winrate', s.win_rate || 0, 800, '', '%');
    document.getElementById('hawk-winrate').style.color = wrColor(s.win_rate || 0);
    animateCount('hawk-pnl', Math.abs(s.pnl || 0), 800, (s.pnl || 0) >= 0 ? '$' : '-$');
    document.getElementById('hawk-pnl').style.color = (s.pnl || 0) >= 0 ? 'var(--success)' : 'var(--error)';
    document.getElementById('hawk-open-bets').textContent = s.open_positions || 0;
    document.getElementById('hawk-total-trades').textContent = s.total_trades || 0;
    // V8: Live on-chain capital (replaces static bankroll)
    loadHawkCapital();
    animateCount('hawk-daily-pnl', Math.abs(s.daily_pnl || 0), 800, (s.daily_pnl || 0) >= 0 ? '$' : '-$');
    document.getElementById('hawk-daily-pnl').style.color = (s.daily_pnl || 0) >= 0 ? 'var(--success)' : 'var(--error)';
    renderLossCapBar('hawk-loss-cap-bar', s.daily_pnl || 0, 50);
    // Wire Win Rate donut + streak
    renderWinRateDonut('hawk-wr-donut', s.wins || 0, s.losses || 0);
    var streakEl = document.getElementById('hawk-streak-num');
    if (streakEl) {
      var streak = s.current_streak || st.current_streak || 0;
      streakEl.textContent = Math.abs(streak);
      streakEl.style.color = streak >= 0 ? 'var(--success)' : 'var(--error)';
    }
    // Scan stats
    var scan = st.scan || {};
    var scanTotal = document.getElementById('hawk-scan-total');
    var scanContested = document.getElementById('hawk-scan-contested');
    var scanSports = document.getElementById('hawk-scan-sports');
    var scanNonsports = document.getElementById('hawk-scan-nonsports');
    var scanAnalyzed = document.getElementById('hawk-scan-analyzed');
    if (scanTotal) scanTotal.textContent = scan.total_eligible || '--';
    if (scanContested) scanContested.textContent = scan.contested || '--';
    if (scanSports) scanSports.textContent = scan.sports_analyzed || '--';
    var scanWeather = document.getElementById('hawk-scan-weather');
    if (scanWeather) scanWeather.textContent = scan.weather_analyzed || '--';
    if (scanNonsports) scanNonsports.textContent = scan.non_sports_analyzed || '--';
    if (scanAnalyzed) scanAnalyzed.textContent = scan.total_analyzed || '--';
  } catch(e) { console.error('hawk status:', e); }

  // V2: Risk meter summary
  try {
    var rmResp = await fetch('/api/hawk/risk-meter');
    var rm = await rmResp.json();
    var dist = rm.distribution || {};
    var lowEl = document.getElementById('hawk-risk-low');
    var medEl = document.getElementById('hawk-risk-med');
    var highEl = document.getElementById('hawk-risk-high');
    var extEl = document.getElementById('hawk-risk-extreme');
    var avgEl = document.getElementById('hawk-avg-risk');
    if (lowEl) lowEl.textContent = (dist.low || 0) + ' Low';
    if (medEl) medEl.textContent = (dist.medium || 0) + ' Med';
    if (highEl) highEl.textContent = (dist.high || 0) + ' High';
    if (extEl) extEl.textContent = (dist.extreme || 0) + ' Extreme';
    if (avgEl) { avgEl.textContent = (rm.avg_risk || 0).toFixed(1) + '/10'; avgEl.style.color = hawkRiskColor(rm.avg_risk || 5); }
  } catch(e) {}

  // Category heatmap
  try {
    var catResp = await fetch('/api/hawk/categories');
    var catData = await catResp.json();
    renderHawkCategories(catData.categories || {}, catData.opp_categories || {});
  } catch(e) {}

  // Opportunities
  try {
    var oppResp = await fetch('/api/hawk/opportunities');
    var oppData = await oppResp.json();
    _hawkOppsCache = oppData.opportunities || [];
    renderHawkOpportunities(_hawkOppsCache);
    renderHawkScanSummary(oppData);
  } catch(e) {}

  // Positions (live on-chain)
  try {
    var posResp = await fetch('/api/hawk/positions?t=' + Date.now());
    var posData = await posResp.json();
    renderHawkPositions(posData.positions || []);
    var posCountBadge = document.getElementById('hawk-pos-count-badge');
    var posLiveDot = document.getElementById('hawk-pos-live-dot');
    if (posCountBadge) posCountBadge.textContent = (posData.positions || []).length;
    if (posLiveDot && posData.live) posLiveDot.style.display = 'inline';
  } catch(e) {}

  // Trade history
  try {
    var histResp = await fetch('/api/hawk/history');
    var histData = await histResp.json();
    renderHawkHistory(histData.trades || []);
  } catch(e) {}

  // Intel Sync (Hawk <-> Viper)
  try {
    var syncResp = await fetch('/api/hawk/intel-sync');
    var syncData = await syncResp.json();
    renderHawkIntelSync(syncData);
  } catch(e) {}

  // V2: Trade Reviews
  try {
    var revResp = await fetch('/api/hawk/reviews');
    var revData = await revResp.json();
    renderHawkReviews(revData);
  } catch(e) {}

  // Weather Intelligence
  loadHawkWeather();

  // Mode badge
  loadHawkMode();
  loadHawkSignalCycle();

  // Trade suggestions
  loadHawkSuggestions();

  // Performance breakdowns
  hawkLoadPerformance();

  // Smart actions from Thor
  loadAgentSmartActions('hawk');

  // V6: New loaders
  loadHawkNextCycle();
  loadHawkDomainWR();
  loadHawkGapHeatmap();
  loadHawkLearner();
  loadHawkArbStatus();
  loadHawkCLV();
  loadHawkTuning();

  // V9: Live in-play monitor
  loadHawkLiveMonitor();

  // V10: Intelligence & Self-Improvement
  loadHawkIntelligence();

  // V8: Auto-refresh capital + positions every 30s
  if (window._hawkLiveRefresh) clearInterval(window._hawkLiveRefresh);
  window._hawkLiveRefresh = setInterval(function() {
    loadHawkCapital();
    fetch('/api/hawk/positions?t=' + Date.now())
      .then(function(r){return r.json();})
      .then(function(d){
        renderHawkPositions(d.positions || []);
        var cb = document.getElementById('hawk-pos-count-badge');
        if (cb) cb.textContent = (d.positions || []).length;
      })
      .catch(function(){});
    loadHawkLiveMonitor();
  }, 30000);
}

// ═══ V9: Live In-Play Monitor ═══

async function loadHawkLiveMonitor() {
  try {
    var resp = await fetch('/api/hawk/live-positions?t=' + Date.now());
    var d = await resp.json();
    var container = document.getElementById('hawk-live-monitor');
    var cardsEl = document.getElementById('hawk-live-cards');
    var countEl = document.getElementById('hawk-live-count');
    var actionsLog = document.getElementById('hawk-live-actions-log');
    var actionsList = document.getElementById('hawk-live-actions-list');
    if (!container || !cardsEl) return;

    var positions = d.positions || [];
    var livePositions = positions.filter(function(p) { return p.is_live; });
    var liveGames = d.live_games_count || 0;
    var actions = d.actions || [];

    // Show/hide the section
    if (positions.length === 0) {
      container.style.display = 'none';
      return;
    }
    container.style.display = 'block';

    // Update count
    if (countEl) {
      countEl.textContent = livePositions.length + ' live / ' + positions.length + ' total | ' + liveGames + ' games on';
    }

    // Render position cards
    var html = '';
    positions.forEach(function(p) {
      var isLive = p.is_live;
      var borderColor = isLive ? '#ff4444' : 'var(--border)';
      var statusDot = isLive
        ? '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#ff4444;animation:rxPulse 1s ease-in-out infinite;margin-right:4px;"></span>LIVE'
        : '<span style="color:var(--text-muted);">PRE-GAME</span>';

      var pnlColor = 'var(--text-muted)';
      var pnlText = '--';
      if (isLive && p.game) {
        var g = p.game;
        pnlText = g.home_team + ' ' + g.home_score + ' - ' + g.away_score + ' ' + g.away_team;
        if (g.period) pnlText += ' | P' + g.period;
        if (g.clock) pnlText += ' ' + g.clock;
      }

      var edgeColor = p.edge >= 20 ? 'var(--success)' : p.edge >= 10 ? 'var(--warning)' : 'var(--text-muted)';

      html += '<div style="background:var(--card-bg);border:1px solid ' + borderColor + ';border-radius:8px;padding:10px;font-size:0.75rem;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">';
      html += '<span style="font-size:0.68rem;">' + statusDot + '</span>';
      html += '<span style="font-size:0.65rem;color:var(--text-muted);">' + (p.category || '').toUpperCase() + '</span>';
      html += '</div>';
      html += '<div style="font-weight:600;margin-bottom:4px;line-height:1.3;">' + (p.question || '').substring(0, 80) + '</div>';
      var hasCurPrice = p.current_price !== null && p.current_price !== undefined && p.current_price > 0;
      var pnlVal = p.pnl || 0;
      var pnlPctVal = p.pnl_pct || 0;
      var priceColor = pnlVal >= 0 ? 'var(--success)' : 'var(--error)';

      html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;margin-bottom:6px;">';
      html += '<div><span style="color:var(--text-muted);font-size:0.65rem;">Direction</span><br><span style="color:' + (p.direction === 'yes' ? 'var(--success)' : 'var(--error)') + ';font-weight:600;">' + (p.direction || '').toUpperCase() + '</span></div>';
      html += '<div><span style="color:var(--text-muted);font-size:0.65rem;">Entry</span><br><span style="font-weight:600;">$' + (p.entry_price || 0).toFixed(2) + '</span></div>';
      if (hasCurPrice) {
        html += '<div><span style="color:var(--text-muted);font-size:0.65rem;">Now</span><br><span style="font-weight:600;color:' + priceColor + ';">$' + p.current_price.toFixed(2) + '</span></div>';
      } else {
        html += '<div><span style="color:var(--text-muted);font-size:0.65rem;">Now</span><br><span style="color:var(--text-muted);">--</span></div>';
      }
      html += '<div><span style="color:var(--text-muted);font-size:0.65rem;">Size</span><br><span style="font-weight:600;">$' + (p.size_usd || 0).toFixed(2) + '</span></div>';
      html += '<div><span style="color:var(--text-muted);font-size:0.65rem;">Edge</span><br><span style="font-weight:600;color:' + edgeColor + ';">' + (p.edge || 0).toFixed(1) + '%</span></div>';
      if (hasCurPrice) {
        html += '<div><span style="color:var(--text-muted);font-size:0.65rem;">PnL</span><br><span style="font-weight:700;color:' + priceColor + ';">' + (pnlVal >= 0 ? '+' : '') + '$' + pnlVal.toFixed(2) + ' (' + (pnlPctVal >= 0 ? '+' : '') + pnlPctVal.toFixed(1) + '%)</span></div>';
      } else {
        html += '<div><span style="color:var(--text-muted);font-size:0.65rem;">PnL</span><br><span style="color:var(--text-muted);">--</span></div>';
      }
      html += '</div>';

      if (isLive && p.game) {
        html += '<div style="background:rgba(255,68,68,0.1);border-radius:6px;padding:6px;margin-bottom:4px;text-align:center;">';
        html += '<div style="font-weight:700;font-size:0.82rem;">' + pnlText + '</div>';
        html += '</div>';
      }

      if (p.match_confidence !== null && p.match_confidence !== undefined && p.match_confidence < 90) {
        var confColor = p.match_confidence < 50 ? '#ff4444' : '#ffaa00';
        html += '<div style="background:rgba(255,170,0,0.15);border:1px solid ' + confColor + ';border-radius:6px;padding:4px 8px;margin-bottom:4px;font-size:0.65rem;color:' + confColor + ';">';
        html += '&#9888; Live score verification low confidence (' + p.match_confidence + '%) for this market';
        html += '</div>';
      }

      html += '<div style="display:flex;gap:4px;margin-top:6px;">';
      if (isLive) {
        html += '<button data-cid="' + p.condition_id + '" data-act="pause" class="hawk-live-btn" style="flex:1;padding:3px 6px;font-size:0.65rem;background:var(--card-bg);border:1px solid var(--border);border-radius:4px;color:var(--text-muted);cursor:pointer;">Pause</button>';
        html += '<button data-cid="' + p.condition_id + '" data-act="exit" class="hawk-live-btn" style="flex:1;padding:3px 6px;font-size:0.65rem;background:rgba(255,68,68,0.1);border:1px solid #ff4444;border-radius:4px;color:#ff4444;cursor:pointer;">Exit Now</button>';
      }
      html += '</div>';
      html += '</div>';
    });
    cardsEl.innerHTML = html;

    // Actions log
    if (actions.length > 0 && actionsLog && actionsList) {
      actionsLog.style.display = 'block';
      var ahtml = '';
      actions.slice(-10).reverse().forEach(function(a) {
        var actionColor = a.action === 'EXIT' ? '#ff4444' : a.action === 'ADD' ? 'var(--success)' : 'var(--text-muted)';
        var ts = a.timestamp ? new Date(a.timestamp * 1000).toLocaleTimeString() : '';
        ahtml += '<div style="padding:3px 0;border-bottom:1px solid var(--border);">';
        ahtml += '<span style="color:' + actionColor + ';font-weight:600;">' + (a.action || '') + '</span> ';
        ahtml += '<span style="color:var(--text-muted);">' + ts + '</span> ';
        ahtml += '<span>' + (a.reason || '').substring(0, 80) + '</span>';
        ahtml += '</div>';
      });
      actionsList.innerHTML = ahtml;
    }
  } catch(e) {
    console.log('hawk live monitor:', e);
  }
}

// Event delegation for live action buttons
document.addEventListener('click', function(e) {
  var btn = e.target.closest('.hawk-live-btn');
  if (!btn) return;
  var cid = btn.getAttribute('data-cid');
  var action = btn.getAttribute('data-act');
  if (!cid || !action) return;
  if (!confirm('Confirm ' + action.toUpperCase() + ' for position ' + cid + '?')) return;
  fetch('/api/hawk/live-action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({condition_id: cid, action: action})
  })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        loadHawkLiveMonitor();
      } else {
        alert('Action failed: ' + (d.error || 'unknown'));
      }
    })
    .catch(function(e) { alert('Error: ' + e); });
});

// ═══ V8: Live Capital ═══

async function loadHawkCapital() {
  try {
    var balResp = await fetch('/api/garves/balance');
    var bal = await balResp.json();
    var portfolio = bal.portfolio || 0;
    var cash = bal.cash || 0;

    // Calculate hawk positions value from on-chain positions
    var posResp = await fetch('/api/hawk/positions?t=' + Date.now());
    var posData = await posResp.json();
    var hawkPosValue = 0;
    var hawkPosCost = 0;
    (posData.positions || []).forEach(function(p) {
      hawkPosValue += (p.value || 0);
      hawkPosCost += (p.size_usd || 0);
    });

    // Capital = total portfolio (shared wallet)
    var capitalEl = document.getElementById('hawk-capital');
    var cashEl = document.getElementById('hawk-capital-cash');
    var posEl = document.getElementById('hawk-capital-positions');
    var liveEl = document.getElementById('hawk-capital-live');

    if (capitalEl) {
      animateCount('hawk-capital', portfolio, 600, '$');
      capitalEl.style.color = portfolio >= 200 ? 'var(--success)' : portfolio >= 100 ? '#FFD700' : 'var(--error)';
    }
    if (cashEl) cashEl.textContent = '$' + cash.toFixed(2);
    if (posEl) posEl.textContent = '$' + hawkPosValue.toFixed(2);
    if (liveEl) liveEl.style.display = 'inline';
  } catch(e) {
    var capitalEl = document.getElementById('hawk-capital');
    if (capitalEl) capitalEl.textContent = '$200';
  }
}

// ═══ V6: New Hawk Functions ═══

var _hawkCycleInterval = null;

async function loadHawkNextCycle() {
  try {
    var resp = await fetch('/api/hawk/next-cycle');
    var d = await resp.json();
    var nextAt = d.next_at || 0;
    var numEl = document.getElementById('hawk-cycle-num');
    if (numEl) numEl.textContent = d.cycle_count || 0;
    if (_hawkCycleInterval) clearInterval(_hawkCycleInterval);
    function hawkCycleTick() {
      var el = document.getElementById('hawk-cycle-countdown');
      var pill = document.getElementById('hawk-cycle-timer');
      if (!el) return;
      var now = Date.now() / 1000;
      var diff = Math.max(0, Math.round(nextAt - now));
      if (diff <= 0) {
        el.textContent = 'NOW';
        el.style.color = 'var(--success)';
        if (pill) pill.style.boxShadow = '0 0 12px rgba(34,197,94,0.4)';
      } else {
        var m = Math.floor(diff / 60);
        var s = diff % 60;
        el.textContent = m + ':' + (s < 10 ? '0' : '') + s;
        if (diff < 30) {
          el.style.color = 'var(--warning)';
          if (pill) pill.style.boxShadow = '0 0 10px rgba(243,156,18,0.3)';
        } else {
          el.style.color = '';
          if (pill) pill.style.boxShadow = '';
        }
      }
    }
    hawkCycleTick();
    _hawkCycleInterval = setInterval(hawkCycleTick, 1000);
    // Flash pill on load
    var pill = document.getElementById('hawk-cycle-timer');
    if (pill) {
      pill.style.boxShadow = '0 0 16px rgba(255,215,0,0.6)';
      setTimeout(function(){ pill.style.boxShadow = ''; }, 1500);
    }
  } catch(e) { console.error('hawk next-cycle:', e); }
}

async function loadHawkDomainWR() {
  try {
    var resp = await fetch('/api/hawk/domain-winrates');
    var d = await resp.json();
    var domains = d.domains || {};
    var setDomain = function(name, data) {
      var wrEl = document.getElementById('hawk-domain-' + name + '-wr');
      var pnlEl = document.getElementById('hawk-domain-' + name + '-pnl');
      if (wrEl) {
        wrEl.textContent = data.win_rate > 0 ? data.win_rate.toFixed(1) + '%' : '--';
        wrEl.style.color = data.win_rate >= 50 ? 'var(--success)' : data.win_rate > 0 ? 'var(--warning)' : 'var(--text-muted)';
      }
      if (pnlEl) {
        var prefix = data.pnl >= 0 ? '$' : '-$';
        pnlEl.textContent = prefix + Math.abs(data.pnl).toFixed(2) + ' P&L (' + (data.wins + data.losses) + ' trades)';
      }
    };
    if (domains.sports) setDomain('sports', domains.sports);
    if (domains.weather) setDomain('weather', domains.weather);
    if (domains.arb) setDomain('arb', domains.arb);
  } catch(e) { console.error('hawk domain-wr:', e); }
}

async function loadHawkGapHeatmap() {
  try {
    var resp = await fetch('/api/hawk/gap-heatmap');
    var d = await resp.json();
    var points = d.points || [];
    var canvas = document.getElementById('hawk-gap-scatter');
    if (!canvas || points.length === 0) return;
    var catColors = {sports: '#00ff44', weather: '#4FC3F7', politics: '#FFD700', other: '#aaa'};
    var datasets = {};
    for (var i = 0; i < points.length; i++) {
      var p = points[i];
      var cat = p.category || 'other';
      if (!datasets[cat]) datasets[cat] = {label: cat, data: [], backgroundColor: catColors[cat] || '#aaa', pointRadius: 5};
      datasets[cat].data.push({x: p.market_price, y: p.estimated_prob});
    }
    if (canvas._chartInstance) canvas._chartInstance.destroy();
    canvas._chartInstance = new Chart(canvas, {
      type: 'scatter',
      data: {datasets: Object.values(datasets)},
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: {title: {display: true, text: 'Market Price', color: '#888'}, min: 0, max: 1, grid: {color: 'rgba(255,255,255,0.05)'}},
          y: {title: {display: true, text: 'Model Estimate', color: '#888'}, min: 0, max: 1, grid: {color: 'rgba(255,255,255,0.05)'}}
        },
        plugins: {
          legend: {labels: {color: '#ccc', font: {size: 10}}},
          annotation: {annotations: {line1: {type: 'line', yMin: 0, yMax: 1, xMin: 0, xMax: 1, borderColor: 'rgba(255,255,255,0.2)', borderDash: [5,5]}}}
        }
      }
    });
  } catch(e) { console.error('hawk gap-heatmap:', e); }
}

async function loadHawkLearner() {
  try {
    var resp = await fetch('/api/hawk/learner');
    var d = await resp.json();
    var dims = d.dimensions || {};
    var dimMap = {
      'Edge Source': 'edge-source',
      'Category': 'category',
      'Direction': 'direction',
      'Confidence': 'confidence',
      'Risk Level': 'risk',
      'Time Horizon': 'time'
    };
    for (var dimName in dimMap) {
      var elId = 'hawk-learner-' + dimMap[dimName] + '-wr';
      var el = document.getElementById(elId);
      if (!el) continue;
      var dimData = dims[dimName] || {};
      var totalW = 0, totalL = 0;
      for (var k in dimData) {
        totalW += dimData[k].wins || 0;
        totalL += dimData[k].losses || 0;
      }
      var total = totalW + totalL;
      if (total > 0) {
        var wr = (totalW / total * 100).toFixed(1);
        el.textContent = wr + '%';
        el.style.color = wr >= 50 ? 'var(--success)' : 'var(--warning)';
        var parent = el.parentElement;
        var detailEl = parent.querySelector('.learner-detail');
        if (!detailEl) {
          detailEl = document.createElement('div');
          detailEl.className = 'learner-detail';
          detailEl.style.cssText = 'font-size:0.58rem;color:var(--text-muted);margin-top:4px;';
          parent.appendChild(detailEl);
        }
        detailEl.textContent = totalW + 'W / ' + totalL + 'L (' + total + ')';
      } else {
        el.textContent = '--';
      }
    }
  } catch(e) { console.error('hawk learner:', e); }
}

async function loadHawkArbStatus() {
  try {
    var resp = await fetch('/api/hawk/arb-status');
    var d = await resp.json();
    var openEl = document.getElementById('hawk-arb-open');
    var totalEl = document.getElementById('hawk-arb-total');
    var profitEl = document.getElementById('hawk-arb-profit');
    if (openEl) openEl.textContent = d.open_arbs || 0;
    if (totalEl) totalEl.textContent = d.total_executed || 0;
    if (profitEl) {
      var profit = d.total_profit || 0;
      profitEl.textContent = (profit >= 0 ? '$' : '-$') + Math.abs(profit).toFixed(2);
      profitEl.style.color = profit >= 0 ? 'var(--success)' : 'var(--error)';
    }
    var tbody = document.getElementById('hawk-arb-tbody');
    if (tbody) {
      var positions = d.positions || [];
      if (positions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:16px;">No open arb positions</td></tr>';
      } else {
        var html = '';
        for (var i = 0; i < positions.length; i++) {
          var p = positions[i];
          html += '<tr>'
            + '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(p.question) + '">' + esc(p.question) + '</td>'
            + '<td>$' + (p.combined_cost || 0).toFixed(4) + '</td>'
            + '<td style="color:var(--success);">$' + (p.profit_per_share || 0).toFixed(4) + '</td>'
            + '<td>' + (p.shares || 0).toFixed(1) + '</td>'
            + '<td style="color:var(--success);">$' + (p.expected_profit || 0).toFixed(2) + '</td>'
            + '<td style="font-size:0.72rem;">' + esc(p.time_str) + '</td>'
            + '</tr>';
        }
        tbody.innerHTML = html;
      }
    }
  } catch(e) { console.error('hawk arb-status:', e); }
}

async function hawkRefreshIntel() {
  try {
    var resp = await fetch('/api/hawk/intel-sync');
    var data = await resp.json();
    renderHawkIntelSync(data);
    showToast('Intel sync refreshed', 'success');
  } catch(e) { console.error('hawk intel refresh:', e); showToast('Intel refresh failed', 'error'); }
}

async function hawkLoadPerformance() {
  try {
    var resp = await fetch('/api/hawk/performance');
    var data = await resp.json();
    renderHawkPerformance(data);
  } catch(e) { console.error('hawk performance:', e); }
}

function renderHawkPerformance(data) {
  var catEl = document.getElementById('hawk-perf-category');
  var riskEl = document.getElementById('hawk-perf-risk');
  var edgeEl = document.getElementById('hawk-perf-edge');
  if (!catEl || !riskEl || !edgeEl) return;
  if (!data || data.total === 0) {
    var empty = '<span class="text-muted" style="font-size:0.76rem;">No resolved trades yet</span>';
    catEl.innerHTML = empty; riskEl.innerHTML = empty; edgeEl.innerHTML = empty;
    return;
  }
  catEl.innerHTML = buildPerfTable(data.by_category || {});
  riskEl.innerHTML = buildPerfTable(data.by_risk || {});
  edgeEl.innerHTML = buildPerfTable(data.by_edge || {});
}

function buildPerfTable(breakdown) {
  var keys = Object.keys(breakdown);
  if (keys.length === 0) return '<span class="text-muted" style="font-size:0.76rem;">No data</span>';
  var html = '<table class="data-table" style="font-size:0.72rem;"><thead><tr><th>Bucket</th><th>W/L</th><th>WR%</th><th>P&L</th></tr></thead><tbody>';
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var v = breakdown[k];
    var w = v.wins || 0, l = v.losses || 0;
    var wr = (w + l) > 0 ? ((w / (w + l)) * 100).toFixed(1) : '0.0';
    var pnl = v.pnl || 0;
    var pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--error)';
    var wrColor2 = parseFloat(wr) >= 55 ? 'var(--success)' : parseFloat(wr) >= 45 ? 'var(--agent-hawk)' : 'var(--error)';
    html += '<tr><td style="text-transform:capitalize;">' + esc(k) + '</td>';
    html += '<td>' + w + '/' + l + '</td>';
    html += '<td style="color:' + wrColor2 + ';font-weight:600;">' + wr + '%</td>';
    html += '<td style="color:' + pnlColor + ';">$' + pnl.toFixed(2) + '</td></tr>';
  }
  html += '</tbody></table>';
  return html;
}

async function loadHawkWeather() {
  try {
    var resp = await fetch('/api/hawk/weather');
    var d = await resp.json();
    var mktsEl = document.getElementById('hawk-weather-markets');
    var tradesEl = document.getElementById('hawk-weather-trades');
    var pnlEl = document.getElementById('hawk-weather-pnl');
    var wxScanned = document.getElementById('hawk-wx-scanned');
    var wxOpps = document.getElementById('hawk-wx-opps');
    var wxWr = document.getElementById('hawk-wx-wr');
    if (mktsEl) mktsEl.textContent = d.weather_markets_scanned || 0;
    if (tradesEl) tradesEl.textContent = d.weather_trades || 0;
    if (pnlEl) {
      var pnl = d.weather_pnl || 0;
      pnlEl.textContent = (pnl >= 0 ? '$' : '-$') + Math.abs(pnl).toFixed(2);
      pnlEl.style.color = pnl >= 0 ? '#00ff44' : 'var(--error)';
    }
    if (wxScanned) wxScanned.textContent = d.weather_markets_scanned || 0;
    if (wxOpps) wxOpps.textContent = d.weather_opportunities || 0;
    if (wxWr) {
      var wr = d.weather_win_rate || 0;
      wxWr.textContent = d.weather_resolved > 0 ? wr.toFixed(1) + '%' : '--';
      if (d.weather_resolved > 0) wxWr.style.color = wrColor(wr);
    }
    renderHawkWeatherTrades(d);
  } catch(e) { console.error('hawk weather:', e); }
}

function renderHawkWeatherTrades(data) {
  var tbody = document.getElementById('hawk-wx-trades-tbody');
  if (!tbody) return;
  if (!data.weather_trades || data.weather_trades === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:16px;">No weather trades yet</td></tr>';
    return;
  }
  // Weather trades are embedded in the main trade history — show summary
  var resolved = data.weather_resolved || 0;
  var wins = data.weather_wins || 0;
  var losses = data.weather_losses || 0;
  var pnl = data.weather_pnl || 0;
  var pnlColor = pnl >= 0 ? '#00ff44' : 'var(--error)';
  var html = '<tr>';
  html += '<td style="color:#4FC3F7;font-weight:600;">Weather Markets (all)</td>';
  html += '<td>--</td>';
  html += '<td>--</td>';
  html += '<td>' + (resolved > 0 ? wins + 'W / ' + losses + 'L' : 'No resolved') + '</td>';
  html += '<td style="color:' + (resolved > 0 ? wrColor(data.weather_win_rate || 0) : 'var(--text-muted)') + ';">' + (resolved > 0 ? (data.weather_win_rate || 0).toFixed(1) + '%' : '--') + '</td>';
  html += '<td style="color:' + pnlColor + ';font-weight:700;">$' + pnl.toFixed(2) + '</td>';
  html += '</tr>';
  tbody.innerHTML = html;
}

function renderHawkReviews(data) {
  var countEl = document.getElementById('hawk-review-count');
  var listEl = document.getElementById('hawk-reviews-list');
  if (!listEl) return;
  var reviews = data.trade_reviews || [];
  if (countEl) countEl.textContent = reviews.length > 0 ? '(' + reviews.length + ' reviewed)' : '';
  if (reviews.length === 0) {
    listEl.innerHTML = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:24px;">No trade reviews yet</div></div>';
    return;
  }
  // Show recommendations first
  var recs = data.recommendations || [];
  var html = '';
  if (recs.length > 0) {
    html += '<div style="background:rgba(255,215,0,0.08);border:1px solid rgba(255,215,0,0.25);border-radius:8px;padding:12px 16px;margin-bottom:12px;">';
    html += '<div style="font-size:0.72rem;color:#FFD700;font-weight:700;margin-bottom:6px;">HAWK RECOMMENDATIONS</div>';
    for (var r = 0; r < recs.length; r++) {
      html += '<div style="font-size:0.74rem;color:var(--text-secondary);margin-bottom:4px;">- ' + esc(recs[r]) + '</div>';
    }
    html += '</div>';
  }
  // Calibration + stats
  html += '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">';
  html += '<div class="widget-badge"><span class="wb-label">Win Rate:</span> <span style="color:' + wrColor(data.win_rate || 0) + ';">' + (data.win_rate || 0).toFixed(1) + '%</span></div>';
  html += '<div class="widget-badge"><span class="wb-label">Calibration:</span> <span style="color:' + ((data.calibration_score || 0) < 0.3 ? '#00ff88' : '#ff6600') + ';">' + (data.calibration_score || 0).toFixed(3) + '</span></div>';
  html += '<div class="widget-badge"><span class="wb-label">P&L:</span> <span style="color:' + ((data.total_pnl || 0) >= 0 ? 'var(--success)' : 'var(--error)') + ';">$' + (data.total_pnl || 0).toFixed(2) + '</span></div>';
  html += '</div>';
  // Review cards (last 10)
  var shown = reviews.slice(-10).reverse();
  for (var i = 0; i < shown.length; i++) {
    var rv = shown[i];
    var wonColor = rv.won ? '#00ff88' : '#ff6666';
    var wonText = rv.won ? 'WON' : 'LOST';
    html += '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:12px;margin-bottom:8px;">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">';
    html += '<span style="font-size:0.78rem;font-weight:600;color:#fff;">' + esc((rv.question || '').substring(0,100)) + '</span>';
    html += '<span style="background:' + wonColor + '22;color:' + wonColor + ';padding:2px 10px;border-radius:4px;font-size:0.68rem;font-weight:700;">' + wonText + ' $' + Math.abs(rv.pnl || 0).toFixed(2) + '</span>';
    html += '</div>';
    html += '<div style="font-size:0.72rem;color:var(--text-secondary);">' + esc(rv.verdict || '') + '</div>';
    html += '<div style="display:flex;gap:10px;margin-top:4px;font-size:0.68rem;color:var(--text-muted);">';
    html += '<span>Risk: ' + (rv.risk_score || '?') + '/10</span>';
    html += '<span>Calibration: ' + (rv.prob_calibration || '?') + '</span>';
    html += '<span>Category: ' + esc(rv.category || '?') + '</span>';
    html += '<span>' + esc(rv.time_str || '') + '</span>';
    html += '</div>';
    html += '</div>';
  }
  listEl.innerHTML = html;

  // Render enhanced analytics sections
  renderHawkEdgeEffectiveness(data.edge_source_effectiveness || {});
  renderHawkCalibrationCurve(data.calibration_curve || {});
  renderHawkFailurePatterns(data.failure_patterns || []);
  renderHawkDynamicRecs(data.dynamic_recommendations || []);
}

function hawkRefreshReviews() {
  fetch('/api/hawk/reviews/refresh', {method:'POST'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok) {
        fetch('/api/hawk/reviews').then(function(r){return r.json();}).then(function(rd){renderHawkReviews(rd);});
      }
    })
    .catch(function(){});
}

function renderHawkEdgeEffectiveness(data) {
  var el = document.getElementById('hawk-edge-effectiveness');
  if (!el) return;
  var sources = data.sources || {};
  var keys = Object.keys(sources);
  if (keys.length === 0) {
    el.innerHTML = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:16px;">No edge source data yet</div></div>';
    return;
  }
  var html = '<div class="glass-card" style="padding:12px;">';
  if (data.best) html += '<div style="font-size:0.72rem;margin-bottom:8px;"><span style="color:#00ff88;font-weight:600;">Best:</span> ' + esc(data.best) + ' &nbsp; <span style="color:#ff6666;font-weight:600;">Worst:</span> ' + esc(data.worst || 'N/A') + '</div>';
  html += '<table class="data-table"><thead><tr><th>Source</th><th>W/L</th><th>WR</th><th>Avg P&L</th><th>Trend</th></tr></thead><tbody>';
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i], s = sources[k];
    var trendColor = s.trend === 'improving' ? '#00ff88' : s.trend === 'declining' ? '#ff6666' : 'var(--text-muted)';
    html += '<tr><td style="font-weight:600;">' + esc(k) + '</td>';
    html += '<td>' + s.wins + 'W/' + s.losses + 'L</td>';
    html += '<td style="color:' + wrColor(s.win_rate) + ';font-weight:700;">' + s.win_rate.toFixed(1) + '%</td>';
    html += '<td style="color:' + (s.avg_pnl >= 0 ? '#00ff88' : '#ff6666') + ';">$' + s.avg_pnl.toFixed(2) + '</td>';
    html += '<td style="color:' + trendColor + ';text-transform:capitalize;">' + esc(s.trend) + '</td></tr>';
  }
  html += '</tbody></table></div>';
  el.innerHTML = html;
}

function renderHawkCalibrationCurve(data) {
  var el = document.getElementById('hawk-calibration-curve');
  if (!el) return;
  var buckets = data.buckets || [];
  if (buckets.length === 0) {
    el.innerHTML = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:16px;">No calibration data yet</div></div>';
    return;
  }
  var html = '<div class="glass-card" style="padding:12px;">';
  html += '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;">';
  if (data.brier_score !== null) html += '<div class="widget-badge"><span class="wb-label">Brier Score:</span> <span style="color:' + (data.brier_score < 0.25 ? '#00ff88' : '#ff6600') + ';">' + data.brier_score.toFixed(4) + '</span></div>';
  if (data.overconfidence_bias !== null) html += '<div class="widget-badge"><span class="wb-label">Bias:</span> <span style="color:' + (data.overconfidence_bias > 0 ? '#ff6600' : '#00ff88') + ';">' + (data.overconfidence_bias > 0 ? 'Over' : 'Under') + 'confident (' + Math.abs(data.overconfidence_bias).toFixed(4) + ')</span></div>';
  html += '</div>';
  html += '<table class="data-table"><thead><tr><th>Prob Bucket</th><th>Trades</th><th>Avg Estimated</th><th>Actual WR</th><th>Gap</th></tr></thead><tbody>';
  for (var i = 0; i < buckets.length; i++) {
    var b = buckets[i];
    if (b.count === 0) continue;
    var gap = b.avg_estimated !== null && b.actual_wr !== null ? (b.avg_estimated - b.actual_wr).toFixed(1) : '--';
    var gapColor = gap !== '--' ? (parseFloat(gap) > 10 ? '#ff6600' : parseFloat(gap) < -10 ? '#4FC3F7' : '#00ff88') : 'var(--text-muted)';
    html += '<tr><td>' + esc(b.label) + '</td><td>' + b.count + '</td>';
    html += '<td>' + (b.avg_estimated !== null ? b.avg_estimated.toFixed(1) + '%' : '--') + '</td>';
    html += '<td style="color:' + wrColor(b.actual_wr || 0) + ';font-weight:700;">' + (b.actual_wr !== null ? b.actual_wr.toFixed(1) + '%' : '--') + '</td>';
    html += '<td style="color:' + gapColor + ';">' + (gap !== '--' ? gap + 'pp' : '--') + '</td></tr>';
  }
  html += '</tbody></table></div>';
  el.innerHTML = html;
}

function renderHawkFailurePatterns(patterns) {
  var el = document.getElementById('hawk-failure-patterns');
  if (!el) return;
  if (!patterns || patterns.length === 0) {
    el.innerHTML = '<div class="glass-card"><div style="text-align:center;padding:16px;color:#00ff88;font-size:0.76rem;">No toxic patterns detected</div></div>';
    return;
  }
  var html = '';
  for (var i = 0; i < patterns.length; i++) {
    var p = patterns[i];
    var borderColor = p.severity === 'critical' ? '#ff4444' : '#ff9800';
    html += '<div style="background:rgba(255,0,0,0.05);border:1px solid ' + borderColor + '44;border-left:3px solid ' + borderColor + ';border-radius:8px;padding:10px 14px;margin-bottom:8px;">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
    html += '<span style="font-size:0.76rem;font-weight:600;color:#fff;">' + esc(p.combo) + '</span>';
    html += '<span style="font-size:0.68rem;font-weight:700;color:' + borderColor + ';text-transform:uppercase;">' + esc(p.severity) + '</span>';
    html += '</div>';
    html += '<div style="font-size:0.72rem;color:var(--text-secondary);margin-top:4px;">' + p.wins + 'W/' + p.losses + 'L (' + p.win_rate.toFixed(1) + '% WR) &mdash; P&L: <span style="color:' + (p.total_pnl >= 0 ? '#00ff88' : '#ff6666') + ';">$' + p.total_pnl.toFixed(2) + '</span></div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function renderHawkDynamicRecs(recs) {
  var el = document.getElementById('hawk-dynamic-recs');
  if (!el) return;
  if (!recs || recs.length === 0) {
    el.innerHTML = '<div class="glass-card"><div style="text-align:center;padding:16px;color:#00ff88;font-size:0.76rem;">No recommendations — all looking good</div></div>';
    return;
  }
  var html = '';
  var sevColors = {critical:'#ff4444', high:'#ff6600', medium:'#FFD700', low:'#4FC3F7'};
  for (var i = 0; i < recs.length; i++) {
    var r = recs[i];
    var sc = sevColors[r.severity] || '#FFD700';
    html += '<div style="background:rgba(255,255,255,0.03);border:1px solid ' + sc + '33;border-left:3px solid ' + sc + ';border-radius:8px;padding:10px 14px;margin-bottom:8px;">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
    html += '<span style="font-size:0.76rem;font-weight:600;color:#fff;">' + esc(r.message) + '</span>';
    html += '<span style="font-size:0.64rem;font-weight:700;color:' + sc + ';text-transform:uppercase;background:' + sc + '18;padding:2px 8px;border-radius:4px;">' + esc(r.severity) + '</span>';
    html += '</div>';
    html += '<div style="font-size:0.70rem;color:var(--text-muted);margin-top:4px;">Evidence: ' + esc(r.evidence) + '</div>';
    html += '<div style="font-size:0.70rem;color:' + sc + ';margin-top:2px;">Suggested: ' + esc(r.suggested_value) + '</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function loadHawkCLV() {
  fetch('/api/hawk/clv')
    .then(function(r){return r.json();})
    .then(function(d){renderHawkCLV(d);})
    .catch(function(){});
}

function renderHawkCLV(data) {
  var el = document.getElementById('hawk-clv-content');
  if (!el) return;
  var html = '';

  // Hero cards
  html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px;">';
  html += '<div class="glass-card" style="text-align:center;padding:12px;"><div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;">Avg CLV</div><div style="font-size:1.3rem;font-weight:700;color:' + ((data.avg_clv||0) >= 0 ? '#00ff88' : '#ff6666') + ';">' + (data.avg_clv||0).toFixed(4) + '</div></div>';
  html += '<div class="glass-card" style="text-align:center;padding:12px;"><div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;">Avg CLV%</div><div style="font-size:1.3rem;font-weight:700;color:' + ((data.avg_clv_pct||0) >= 0 ? '#00ff88' : '#ff6666') + ';">' + (data.avg_clv_pct||0).toFixed(2) + '%</div></div>';
  html += '<div class="glass-card" style="text-align:center;padding:12px;"><div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;">Positive CLV Rate</div><div style="font-size:1.3rem;font-weight:700;color:' + ((data.positive_clv_rate||0) >= 50 ? '#00ff88' : '#ff6666') + ';">' + (data.positive_clv_rate||0).toFixed(1) + '%</div></div>';
  html += '</div>';
  html += '<div style="font-size:0.70rem;color:var(--text-muted);margin-bottom:10px;">' + (data.resolved||0) + ' resolved / ' + (data.total_trades||0) + ' total CLV records</div>';

  // By Category table
  var byCat = data.by_category || {};
  var catKeys = Object.keys(byCat);
  if (catKeys.length > 0) {
    html += '<div class="glass-card" style="padding:12px;margin-bottom:10px;"><div style="font-size:0.72rem;font-weight:700;color:var(--agent-hawk);margin-bottom:6px;">CLV by Category</div>';
    html += '<table class="data-table"><thead><tr><th>Category</th><th>Count</th><th>Avg CLV</th><th>+CLV Rate</th></tr></thead><tbody>';
    for (var i = 0; i < catKeys.length; i++) {
      var ck = catKeys[i], cv = byCat[ck];
      html += '<tr><td style="font-weight:600;">' + esc(ck) + '</td><td>' + cv.count + '</td>';
      html += '<td style="color:' + (cv.avg_clv >= 0 ? '#00ff88' : '#ff6666') + ';">' + cv.avg_clv.toFixed(4) + '</td>';
      html += '<td style="color:' + (cv.positive_rate >= 50 ? '#00ff88' : '#ff6666') + ';">' + cv.positive_rate.toFixed(1) + '%</td></tr>';
    }
    html += '</tbody></table></div>';
  }

  // By Edge Source table
  var bySrc = data.by_edge_source || {};
  var srcKeys = Object.keys(bySrc);
  if (srcKeys.length > 0) {
    html += '<div class="glass-card" style="padding:12px;margin-bottom:10px;"><div style="font-size:0.72rem;font-weight:700;color:var(--agent-hawk);margin-bottom:6px;">CLV by Edge Source</div>';
    html += '<table class="data-table"><thead><tr><th>Source</th><th>Count</th><th>Avg CLV</th><th>+CLV Rate</th></tr></thead><tbody>';
    for (var j = 0; j < srcKeys.length; j++) {
      var sk = srcKeys[j], sv = bySrc[sk];
      html += '<tr><td style="font-weight:600;">' + esc(sk) + '</td><td>' + sv.count + '</td>';
      html += '<td style="color:' + (sv.avg_clv >= 0 ? '#00ff88' : '#ff6666') + ';">' + sv.avg_clv.toFixed(4) + '</td>';
      html += '<td style="color:' + (sv.positive_rate >= 50 ? '#00ff88' : '#ff6666') + ';">' + sv.positive_rate.toFixed(1) + '%</td></tr>';
    }
    html += '</tbody></table></div>';
  }

  // Trade history (last 10)
  var trades = data.trades || [];
  if (trades.length > 0) {
    html += '<div class="glass-card" style="padding:12px;"><div style="font-size:0.72rem;font-weight:700;color:var(--agent-hawk);margin-bottom:6px;">CLV Trade History (last 10)</div>';
    html += '<table class="data-table"><thead><tr><th>Market</th><th>Dir</th><th>Entry</th><th>CLV</th><th>CLV%</th><th>Status</th></tr></thead><tbody>';
    var shown = trades.slice(-10).reverse();
    for (var t = 0; t < shown.length; t++) {
      var tr = shown[t];
      var clvVal = tr.clv !== null && tr.clv !== undefined ? tr.clv.toFixed(4) : '--';
      var clvPct = tr.clv_pct !== null && tr.clv_pct !== undefined ? tr.clv_pct.toFixed(2) + '%' : '--';
      var clvColor = tr.clv !== null ? (tr.clv >= 0 ? '#00ff88' : '#ff6666') : 'var(--text-muted)';
      html += '<tr><td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc((tr.question||'').substring(0,60)) + '</td>';
      html += '<td>' + esc(tr.direction||'?') + '</td>';
      html += '<td>' + (tr.entry_price||0).toFixed(4) + '</td>';
      html += '<td style="color:' + clvColor + ';">' + clvVal + '</td>';
      html += '<td style="color:' + clvColor + ';">' + clvPct + '</td>';
      html += '<td>' + (tr.resolved ? 'Resolved' : 'Open') + '</td></tr>';
    }
    html += '</tbody></table></div>';
  }

  if (!html) html = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:16px;">No CLV data available</div></div>';
  el.innerHTML = html;
}

function loadHawkTuning() {
  fetch('/api/hawk/tune')
    .then(function(r){return r.json();})
    .then(function(d){renderHawkTuning(d);})
    .catch(function(){
      var el = document.getElementById('hawk-tuning-content');
      if(el) el.innerHTML = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:16px;">Tuning not available</div></div>';
    });
}

function renderHawkTuning(data) {
  var el = document.getElementById('hawk-tuning-content');
  if (!el) return;
  var overrides = data.overrides || {};
  var recs = data.recommendations || [];
  var html = '';

  // Current overrides table
  var oKeys = Object.keys(overrides);
  if (oKeys.length > 0) {
    html += '<div class="glass-card" style="padding:12px;margin-bottom:10px;"><div style="font-size:0.72rem;font-weight:700;color:var(--agent-hawk);margin-bottom:6px;">Active Category Overrides</div>';
    html += '<table class="data-table"><thead><tr><th>Category</th><th>Enabled</th><th>Min Edge</th><th>Max Bet</th><th>Kelly</th></tr></thead><tbody>';
    for (var i = 0; i < oKeys.length; i++) {
      var cat = oKeys[i], ov = overrides[cat];
      var enabledColor = ov.enabled === false ? '#ff6666' : '#00ff88';
      html += '<tr><td style="font-weight:600;">' + esc(cat) + '</td>';
      html += '<td style="color:' + enabledColor + ';">' + (ov.enabled === false ? 'DISABLED' : 'Active') + '</td>';
      html += '<td>' + (ov.min_edge !== null && ov.min_edge !== undefined ? (ov.min_edge*100).toFixed(1) + '%' : 'global') + '</td>';
      html += '<td>' + (ov.max_bet_usd !== null && ov.max_bet_usd !== undefined ? '$' + ov.max_bet_usd.toFixed(0) : 'global') + '</td>';
      html += '<td>' + (ov.kelly_fraction !== null && ov.kelly_fraction !== undefined ? ov.kelly_fraction.toFixed(2) : 'global') + '</td></tr>';
    }
    html += '</tbody></table></div>';
  }

  // Recommendations
  if (recs.length > 0) {
    html += '<div style="font-size:0.72rem;font-weight:700;color:var(--agent-hawk);margin-bottom:6px;">Auto-Tune Recommendations</div>';
    for (var j = 0; j < recs.length; j++) {
      var r = recs[j];
      var sevColors = {critical:'#ff4444', high:'#ff6600', medium:'#FFD700', low:'#4FC3F7'};
      var sc = sevColors[r.severity] || '#FFD700';
      html += '<div style="background:rgba(255,255,255,0.03);border:1px solid ' + sc + '33;border-left:3px solid ' + sc + ';border-radius:8px;padding:10px 14px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">';
      html += '<div><div style="font-size:0.76rem;font-weight:600;color:#fff;">' + esc(r.category) + ' — ' + esc(r.action) + '</div>';
      html += '<div style="font-size:0.70rem;color:var(--text-muted);">WR: ' + (r.win_rate||0).toFixed(1) + '% | ' + (r.total||0) + ' trades | Significance: ' + (r.significant ? 'YES' : 'NO') + '</div></div>';
      html += '<button onclick="applyHawkTuning(\'' + esc(r.category) + '\')" style="background:rgba(255,152,0,0.15);color:var(--agent-hawk);border:1px solid rgba(255,152,0,0.3);border-radius:6px;padding:4px 12px;font-size:0.68rem;cursor:pointer;white-space:nowrap;">Apply</button>';
      html += '</div>';
    }
  }

  if (!html) html = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:16px;">No category overrides or recommendations</div></div>';
  el.innerHTML = html;
}

function applyHawkTuning(category) {
  fetch('/api/hawk/tune/apply', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({category: category})
  })
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok) loadHawkTuning();
    })
    .catch(function(){});
}

function renderHawkIntelSync(data) {
  var section = document.getElementById('hawk-intel-sync-section');
  var badges = document.getElementById('hawk-intel-badges');
  var tbody = document.getElementById('hawk-intel-tbody');
  if (!section || !badges || !tbody) return;

  var bf = data.briefing;
  var ctx = data.context;

  if (!bf) {
    section.style.display = 'none';
    return;
  }
  section.style.display = 'block';

  // Badges
  var bh = '';
  var syncColor = data.sync_active ? '#00ff88' : 'var(--warning)';
  var syncLabel = data.sync_active ? 'SYNCED' : 'STALE';
  bh += '<div class="widget-badge"><span class="wb-label">Status:</span> <span style="color:' + syncColor + ';font-weight:700;">' + syncLabel + '</span></div>';
  bh += '<div class="widget-badge"><span class="wb-label">Briefed:</span> <span style="color:#FFD700;">' + (bf.briefed_markets || 0) + ' markets</span></div>';
  if (ctx) {
    bh += '<div class="widget-badge"><span class="wb-label">Intel Links:</span> <span style="color:#00ff88;">' + (ctx.total_links || 0) + '</span></div>';
    bh += '<div class="widget-badge"><span class="wb-label">Markets w/ Intel:</span> <span>' + (ctx.markets_with_intel || 0) + '</span></div>';
  }
  bh += '<div class="widget-badge"><span class="wb-label">Age:</span> <span style="color:' + (bf.stale ? 'var(--error)' : 'var(--text-muted)') + ';">' + (bf.age_minutes || 0).toFixed(0) + 'm</span></div>';
  badges.innerHTML = bh;

  // Per-market intel table
  if (!ctx || !ctx.market_intel || ctx.market_intel.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="text-muted" style="text-align:center;padding:16px;">Briefing active but no intel matched yet. Trigger a Viper scan.</td></tr>';
    return;
  }
  var html = '';
  for (var i = 0; i < ctx.market_intel.length; i++) {
    var mi = ctx.market_intel[i];
    var entStr = (mi.entities || []).slice(0, 3).join(', ');
    if (mi.intel_count === 0) {
      html += '<tr>';
      html += '<td style="max-width:200px;"><div style="font-size:0.74rem;line-height:1.3;white-space:normal;">' + esc(mi.question) + '</div><div style="font-size:0.6rem;color:var(--text-muted);margin-top:2px;">' + esc(entStr) + '</div></td>';
      html += '<td colspan="3" class="text-muted" style="font-size:0.72rem;">No intel yet</td>';
      html += '</tr>';
      continue;
    }
    for (var j = 0; j < mi.intel_items.length; j++) {
      var it = mi.intel_items[j];
      var typeColor = it.match_type === 'pre_linked' ? '#00ff88' : '#FFD700';
      var typeLabel = it.match_type === 'pre_linked' ? 'Targeted' : 'Keyword Match';
      html += '<tr>';
      if (j === 0) {
        html += '<td rowspan="' + mi.intel_items.length + '" style="max-width:200px;vertical-align:top;"><div style="font-size:0.74rem;line-height:1.3;white-space:normal;">' + esc(mi.question) + '</div><div style="font-size:0.6rem;color:var(--text-muted);margin-top:2px;">' + esc(entStr) + '</div></td>';
      }
      html += '<td style="max-width:220px;"><div style="font-size:0.72rem;line-height:1.3;white-space:normal;">' + esc((it.headline || '').substring(0, 80)) + '</div></td>';
      html += '<td><span class="badge" style="background:rgba(0,255,136,0.1);color:' + typeColor + ';font-size:0.64rem;">' + typeLabel + '</span></td>';
      html += '<td style="font-size:0.68rem;color:var(--text-muted);">' + esc(it.source || '') + '</td>';
      html += '</tr>';
    }
  }
  tbody.innerHTML = html;
}

function renderHawkScanSummary(data) {
  var el = document.getElementById('hawk-scan-summary');
  if (!el) return;
  var opps = data.opportunities || [];
  if (opps.length === 0 && !data.updated) { el.style.display = 'none'; return; }
  el.style.display = 'block';
  var setEl = function(id, val) { var e = document.getElementById(id); if(e) e.textContent = val; };
  setEl('hawk-total-scanned', data.total_scanned || '?');
  setEl('hawk-contested', data.contested || '?');
  setEl('hawk-analyzed', data.analyzed || '?');
  setEl('hawk-opp-count', opps.length);
  var totalEv = 0;
  for (var i = 0; i < opps.length; i++) totalEv += (opps[i].expected_value || 0);
  setEl('hawk-total-ev', '$' + totalEv.toFixed(2));
  if (data.updated) {
    var d = new Date(data.updated * 1000);
    setEl('hawk-last-scan', d.toLocaleTimeString('en-US', {hour:'numeric',minute:'2-digit',hour12:true}));
  }
}

function renderHawkCategories(cats, oppCats) {
  var el = document.getElementById('hawk-category-heatmap');
  if (!el) return;
  var catColors = {politics:'#4488ff',sports:'#ff8844',crypto_event:'#FFD700',culture:'#cc66ff',weather:'#00ccaa',other:'#888888'};
  var catLabels = {politics:'Politics',sports:'Sports',crypto_event:'Crypto',culture:'Culture',weather:'Weather',other:'Other'};
  var hasResolved = Object.keys(cats).length > 0;
  var hasOpps = oppCats && Object.keys(oppCats).length > 0;
  if (!hasResolved && !hasOpps) { el.innerHTML = '<div class="text-muted" style="text-align:center;padding:12px;">No category data yet — trigger a scan</div>'; return; }

  var html = '<div style="display:flex;flex-wrap:wrap;gap:10px;">';
  // Show opportunity-based category cards + resolved-only categories
  if (hasOpps) {
    var oKeys = Object.keys(oppCats).sort(function(a,b){ return (oppCats[b].total_ev||0)-(oppCats[a].total_ev||0); });
    for (var i = 0; i < oKeys.length; i++) {
      var k = oKeys[i];
      var c = oppCats[k];
      var rc = cats[k] || null;
      var color = catColors[k] || '#888';
      var label = catLabels[k] || k;
      html += '<div style="background:rgba(255,255,255,0.04);border-left:3px solid ' + color + ';border-radius:8px;padding:12px 16px;min-width:140px;flex:1;">';
      html += '<div style="font-size:0.72rem;color:' + color + ';text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin-bottom:4px;">' + esc(label) + '</div>';
      html += '<div style="font-size:1.3rem;font-weight:800;color:#fff;">' + c.count + ' <span style="font-size:0.68rem;color:var(--text-muted);font-weight:400;">markets</span></div>';
      html += '<div style="font-size:0.72rem;color:var(--text-secondary);margin-top:4px;">Avg Edge: <span style="color:' + color + ';font-weight:600;">' + (c.avg_edge||0).toFixed(1) + '%</span></div>';
      html += '<div style="font-size:0.72rem;color:var(--text-secondary);">Total Est. Profit: <span style="color:var(--success);font-weight:600;">+$' + (c.total_ev||0).toFixed(2) + '</span></div>';
      html += '<div style="font-size:0.72rem;color:var(--text-secondary);">$30/each: <span style="color:#FFD700;font-weight:600;">+$' + (c.potential_30||0).toFixed(2) + '</span></div>';
      if (rc) {
        var wr = (rc.wins+rc.losses)>0?(rc.wins/(rc.wins+rc.losses)*100):0;
        html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px;border-top:1px solid rgba(255,255,255,0.06);padding-top:4px;">Resolved: ' + rc.wins + 'W-' + rc.losses + 'L (' + wr.toFixed(0) + '%) | $' + (rc.pnl||0).toFixed(2) + '</div>';
      }
      html += '</div>';
    }
    // Show resolved-only categories that have no current opportunities (e.g. sports)
    if (hasResolved) {
      var rKeys = Object.keys(cats);
      for (var i = 0; i < rKeys.length; i++) {
        var k = rKeys[i];
        if (oppCats[k]) continue;
        var c = cats[k];
        var wr = (c.wins + c.losses) > 0 ? (c.wins / (c.wins + c.losses) * 100) : 0;
        var color = catColors[k] || '#888';
        var label = catLabels[k] || k;
        html += '<div style="background:rgba(255,255,255,0.04);border-left:3px solid ' + color + ';border-radius:8px;padding:12px 16px;min-width:140px;flex:1;opacity:0.85;">';
        html += '<div style="font-size:0.72rem;color:' + color + ';text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin-bottom:4px;">' + esc(label) + '</div>';
        html += '<div style="font-size:1.1rem;font-weight:700;color:' + wrColor(wr) + ';">' + wr.toFixed(1) + '% <span style="font-size:0.68rem;color:var(--text-muted);font-weight:400;">WR</span></div>';
        html += '<div style="font-size:0.72rem;color:var(--text-secondary);margin-top:2px;">' + c.wins + 'W-' + c.losses + 'L | P&L: <span style="color:' + (c.pnl >= 0 ? 'var(--success)' : 'var(--danger)') + ';font-weight:600;">$' + (c.pnl||0).toFixed(2) + '</span></div>';
        html += '<div style="font-size:0.65rem;color:var(--text-muted);margin-top:4px;font-style:italic;">No current opportunities</div>';
        html += '</div>';
      }
    }
  } else if (hasResolved) {
    var keys = Object.keys(cats);
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      var c = cats[k];
      var wr = (c.wins + c.losses) > 0 ? (c.wins / (c.wins + c.losses) * 100) : 0;
      var color = catColors[k] || '#888';
      html += '<div style="background:rgba(255,255,255,0.04);border-left:3px solid ' + color + ';border-radius:6px;padding:10px 14px;min-width:120px;">';
      html += '<div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;">' + esc(k) + '</div>';
      html += '<div style="font-size:1.1rem;font-weight:700;color:' + wrColor(wr) + ';">' + wr.toFixed(1) + '%</div>';
      html += '<div style="font-size:0.68rem;color:var(--text-secondary);">' + c.wins + 'W-' + c.losses + 'L | $' + (c.pnl || 0).toFixed(2) + '</div>';
      html += '</div>';
    }
  }
  html += '</div>';
  el.innerHTML = html;
}

var _hawkCatFilter = 'all';

function hawkSetCatFilter(cat) {
  _hawkCatFilter = cat;
  var pills = document.querySelectorAll('#hawk-cat-filter-pills .hawk-filter-pill');
  for (var i = 0; i < pills.length; i++) {
    pills[i].style.background = pills[i].getAttribute('data-cat') === cat ? 'rgba(255,215,0,0.2)' : 'rgba(255,255,255,0.06)';
    pills[i].style.borderColor = pills[i].getAttribute('data-cat') === cat ? '#FFD700' : 'rgba(255,255,255,0.1)';
  }
  renderHawkOpportunities(_hawkOppsCache);
}

function hawkCalcTimeLeft(endDate, curPrice, category) {
  if (!endDate) return {text: 'No deadline', label: '', color: 'var(--text-muted)', sort: 99999999};
  var now = Date.now();
  var end = new Date(endDate).getTime();
  if (isNaN(end)) return {text: 'Unknown', label: '', color: 'var(--text-muted)', sort: 99999999};
  var diff = end - now;
  var isSports = (category || '').match(/sport|soccer|nba|nfl|nhl|mlb|ufc|mma|hockey|basketball|football|unknown/i);
  var isCrypto = (category || '').match(/crypto|up.down|garves/i);
  if (diff <= 0) {
    var ago = Math.abs(diff);
    var cp = curPrice || 0;
    // Resolved — price at 0 or 1
    if (cp >= 0.95) return {text: 'WON', label: 'Resolved', color: '#00ff44', sort: -2};
    if (cp <= 0.05) return {text: 'LOST', label: 'Resolved', color: '#ff4444', sort: -2};
    // Still resolving — show how long ago event started
    var agoMin = Math.floor(ago / 60000);
    var agoHrs = Math.floor(ago / 3600000);
    var agoStr;
    if (agoHrs >= 24) agoStr = Math.floor(agoHrs / 24) + 'd ' + (agoHrs % 24) + 'h ago';
    else if (agoHrs >= 1) agoStr = agoHrs + 'h ' + Math.floor((ago % 3600000) / 60000) + 'm ago';
    else agoStr = agoMin + 'm ago';
    var waitLabel = isCrypto ? 'Resolving' : isSports ? 'In play' : 'Awaiting result';
    return {text: agoStr, label: waitLabel, color: '#ff6b35', sort: -1};
  }
  var hours = diff / 3600000;
  var days = Math.floor(hours / 24);
  var hrs = Math.floor(hours % 24);
  // Label: crypto = resolves (end_date IS resolution), sports = event starts, other = market closes
  var countLabel = isCrypto ? 'Resolves' : isSports ? 'Event starts' : 'Market closes';
  if (days > 365) return {text: Math.floor(days/365) + 'y ' + Math.floor((days%365)/30) + 'mo', label: countLabel, color: 'var(--text-muted)', sort: diff};
  if (days > 30) return {text: Math.floor(days/30) + 'mo ' + (days%30) + 'd', label: countLabel, color: '#888', sort: diff};
  if (days > 7) return {text: days + 'd', label: countLabel, color: 'var(--text-secondary)', sort: diff};
  if (days > 1) return {text: days + 'd ' + hrs + 'h', label: countLabel, color: '#FFD700', sort: diff};
  if (hours > 1) return {text: Math.floor(hours) + 'h ' + Math.floor((diff%3600000)/60000) + 'm', label: countLabel, color: '#ff8844', sort: diff};
  return {text: Math.floor(diff/60000) + 'm', label: countLabel, color: 'var(--error)', sort: diff};
}

function hawkCalc30Profit(o) {
  var mp = o.market_price || 0.5;
  var ep = o.estimated_prob || 0.5;
  var buyPrice, winProb;
  if (o.direction === 'yes') { buyPrice = mp; winProb = ep; }
  else { buyPrice = 1 - mp; winProb = 1 - ep; }
  if (buyPrice <= 0 || buyPrice >= 1) return {profit: 0, roi: 0, payout: 30};
  var shares = 30.0 / buyPrice;
  var payout = shares * 1.0;
  var profit = payout - 30.0;
  var roi = (profit / 30.0) * 100;
  var expProfit = profit * winProb;
  return {profit: profit, roi: roi, payout: payout, expProfit: expProfit, winProb: winProb, buyPrice: buyPrice, shares: shares};
}

function renderHawkOpportunities(opps) {
  var groupEl = document.getElementById('hawk-opp-groups');
  var pillEl = document.getElementById('hawk-cat-filter-pills');
  var totalLabel = document.getElementById('hawk-opp-total-label');
  if (!groupEl) return;
  if (opps.length === 0) {
    groupEl.innerHTML = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:24px;">No opportunities found yet — hit Trigger Scan</div></div>';
    if (pillEl) pillEl.innerHTML = '';
    return;
  }
  var catColors = {politics:'#4488ff',sports:'#ff8844',crypto_event:'#FFD700',culture:'#cc66ff',other:'#888'};
  var catLabels = {politics:'Politics',sports:'Sports',crypto_event:'Crypto',culture:'Culture',other:'Other'};

  // Build category counts for filter pills
  var catCounts = {};
  for (var i = 0; i < opps.length; i++) {
    var cat = opps[i].category || 'other';
    catCounts[cat] = (catCounts[cat]||0) + 1;
  }
  if (pillEl) {
    var ph = '<button class="hawk-filter-pill" data-cat="all" onclick="hawkSetCatFilter(\'all\')" style="font-size:0.7rem;padding:4px 10px;border-radius:12px;border:1px solid ' + (_hawkCatFilter==='all'?'#FFD700':'rgba(255,255,255,0.1)') + ';background:' + (_hawkCatFilter==='all'?'rgba(255,215,0,0.2)':'rgba(255,255,255,0.06)') + ';color:#fff;cursor:pointer;font-weight:600;">All (' + opps.length + ')</button>';
    var ckeys = Object.keys(catCounts).sort(function(a,b){return catCounts[b]-catCounts[a];});
    for (var j = 0; j < ckeys.length; j++) {
      var ck = ckeys[j];
      var cc = catColors[ck]||'#888';
      var active = _hawkCatFilter === ck;
      ph += '<button class="hawk-filter-pill" data-cat="' + ck + '" onclick="hawkSetCatFilter(\'' + ck + '\')" style="font-size:0.7rem;padding:4px 10px;border-radius:12px;border:1px solid ' + (active?cc:'rgba(255,255,255,0.1)') + ';background:' + (active?'rgba(255,215,0,0.15)':'rgba(255,255,255,0.06)') + ';color:' + cc + ';cursor:pointer;font-weight:600;">' + (catLabels[ck]||ck) + ' (' + catCounts[ck] + ')</button>';
    }
    pillEl.innerHTML = ph;
  }

  // Filter by selected category
  var filtered = [];
  for (var i = 0; i < opps.length; i++) {
    if (_hawkCatFilter === 'all' || opps[i].category === _hawkCatFilter) filtered.push(opps[i]);
  }

  // Group by time horizon
  var groups = {urgent:[], shortTerm:[], medTerm:[], longTerm:[]};
  var groupNames = {urgent:'Expiring Soon (< 7 days)', shortTerm:'Short Term (1-4 weeks)', medTerm:'Medium Term (1-6 months)', longTerm:'Long Term (6+ months)'};
  var groupColors = {urgent:'#ff4444', shortTerm:'#ff8844', medTerm:'#FFD700', longTerm:'#4488ff'};

  for (var i = 0; i < filtered.length; i++) {
    var o = filtered[i];
    var tl = hawkCalcTimeLeft(o.end_date, 0, o.category);
    o._timeLeft = tl;
    var daysLeft = tl.sort / 86400000;
    if (daysLeft < 0) continue; // expired
    if (daysLeft <= 7) groups.urgent.push(o);
    else if (daysLeft <= 28) groups.shortTerm.push(o);
    else if (daysLeft <= 180) groups.medTerm.push(o);
    else groups.longTerm.push(o);
  }
  // Also push no-deadline ones to longTerm
  for (var i = 0; i < filtered.length; i++) {
    if (filtered[i]._timeLeft && filtered[i]._timeLeft.sort === 99999999) groups.longTerm.push(filtered[i]);
  }

  var totalPot30 = 0;
  for (var i = 0; i < filtered.length; i++) {
    totalPot30 += hawkCalc30Profit(filtered[i]).expProfit;
  }
  if (totalLabel) totalLabel.textContent = filtered.length + ' markets | $30 each = $' + (filtered.length*30) + ' total risk | +$' + totalPot30.toFixed(2) + ' expected profit';

  var html = '';
  var gkeys = ['urgent','shortTerm','medTerm','longTerm'];
  var globalIdx = 0;
  for (var g = 0; g < gkeys.length; g++) {
    var gk = gkeys[g];
    var items = groups[gk];
    if (items.length === 0) continue;

    // Sort within group by expected value descending
    items.sort(function(a,b){ return (b.expected_value||0)-(a.expected_value||0); });

    var grpEv = 0, grp30 = 0;
    for (var x = 0; x < items.length; x++) { grpEv += items[x].expected_value||0; grp30 += hawkCalc30Profit(items[x]).expProfit; }

    html += '<div style="margin-bottom:16px;">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">';
    html += '<div style="font-size:0.78rem;font-weight:700;color:' + groupColors[gk] + ';">' + groupNames[gk] + ' <span style="color:var(--text-muted);font-weight:400;">(' + items.length + ')</span></div>';
    html += '<div style="font-size:0.7rem;color:var(--text-muted);">Group Est. Profit: <span style="color:var(--success);font-weight:600;">+$' + grpEv.toFixed(2) + '</span> | $30/ea profit: <span style="color:#FFD700;font-weight:600;">+$' + grp30.toFixed(2) + '</span></div>';
    html += '</div>';
    html += '<div class="glass-card" style="overflow-x:auto;padding:0;">';
    html += '<table class="data-table" style="margin:0;"><thead><tr>';
    html += '<th style="width:26px;">#</th>';
    html += '<th style="min-width:200px;">Market</th>';
    html += '<th>Category</th>';
    html += '<th>Time Left</th>';
    html += '<th>Pick</th>';
    html += '<th>Crowd</th>';
    html += '<th>Hawk</th>';
    html += '<th>Edge</th>';
    html += '<th style="min-width:110px;">$30 Profit</th>';
    html += '<th>Exp. Return</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < items.length; i++) {
      var o = items[i];
      var edgePct = (o.edge || 0) * 100;
      var edgeColor = edgePct >= 20 ? '#00ff88' : edgePct >= 10 ? '#FFD700' : edgePct >= 5 ? 'var(--text)' : 'var(--text-muted)';
      var dirColor = o.direction === 'yes' ? '#00ff88' : '#ff6666';
      var dirIcon = o.direction === 'yes' ? '&#9650;' : '&#9660;';
      var dirLabel = o.direction === 'yes' ? 'YES' : 'NO';
      var catColor = catColors[o.category] || '#888';
      var tl = o._timeLeft || {text:'?',color:'var(--text-muted)'};
      var p30 = hawkCalc30Profit(o);
      var realIdx = _hawkOppsCache.indexOf(o);

      var pmUrl = '';
      if (o.event_slug && o.market_slug) pmUrl = 'https://polymarket.com/event/' + o.event_slug + '/' + o.market_slug;
      else if (o.market_slug) pmUrl = 'https://polymarket.com/event/' + o.market_slug;

      html += '<tr style="cursor:pointer;" onclick="showHawkReasoning(' + realIdx + ')">';
      html += '<td style="color:var(--text-muted);font-size:0.7rem;">' + (globalIdx+1) + '</td>';
      // Market column: question + polymarket link + volume
      html += '<td style="max-width:260px;"><div style="font-size:0.76rem;line-height:1.3;white-space:normal;" title="' + esc(o.question||'') + '">' + esc(o.question||'') + '</div>';
      html += '<div style="display:flex;align-items:center;gap:6px;margin-top:3px;">';
      if (pmUrl) html += '<a href="' + pmUrl + '" target="_blank" onclick="event.stopPropagation()" style="font-size:0.62rem;color:#8B5CF6;text-decoration:none;font-weight:600;display:flex;align-items:center;gap:2px;">&#x1F517; Polymarket</a>';
      else html += '<span style="font-size:0.62rem;color:var(--text-muted);">Polymarket</span>';
      html += '<span style="font-size:0.62rem;color:var(--text-muted);">| Vol: $' + formatCompact(o.volume||0) + '</span>';
      html += '</div></td>';
      // Category
      html += '<td><span style="color:' + catColor + ';font-size:0.66rem;font-weight:700;text-transform:uppercase;letter-spacing:0.03em;">' + esc(catLabels[o.category]||o.category||'?') + '</span></td>';
      // Time left
      html += '<td style="font-size:0.74rem;font-weight:600;color:' + tl.color + ';">' + tl.text + '</td>';
      // Pick
      html += '<td style="font-weight:700;font-size:0.78rem;"><span style="color:' + dirColor + ';">' + dirIcon + ' ' + dirLabel + '</span></td>';
      // Crowd price
      html += '<td style="font-family:var(--font-mono);font-size:0.76rem;">' + ((o.market_price||0)*100).toFixed(0) + '%</td>';
      // Hawk prob
      html += '<td style="font-family:var(--font-mono);font-size:0.76rem;">' + ((o.estimated_prob||0)*100).toFixed(0) + '%</td>';
      // Edge
      html += '<td style="color:' + edgeColor + ';font-weight:700;font-family:var(--font-mono);font-size:0.78rem;">+' + edgePct.toFixed(1) + '%</td>';
      // $30 Profit column
      html += '<td style="font-size:0.72rem;">';
      html += '<div style="color:var(--success);font-weight:700;font-family:var(--font-mono);">+$' + p30.profit.toFixed(2) + ' <span style="font-size:0.62rem;color:var(--text-muted);">(' + p30.roi.toFixed(0) + '% ROI)</span></div>';
      html += '<div style="font-size:0.62rem;color:var(--text-muted);">Buy ' + p30.shares.toFixed(1) + ' @ ' + (p30.buyPrice*100).toFixed(0) + 'c</div>';
      html += '</td>';
      // Expected return (probability-weighted)
      html += '<td style="font-family:var(--font-mono);font-size:0.76rem;color:#FFD700;font-weight:600;">+$' + p30.expProfit.toFixed(2) + '</td>';
      html += '</tr>';
      globalIdx++;
    }
    html += '</tbody></table></div></div>';
  }

  if (html === '') {
    html = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:24px;">No opportunities match the selected filter</div></div>';
  }
  groupEl.innerHTML = html;
}

function formatCompact(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(0) + 'K';
  return n.toFixed(0);
}

function showHawkReasoning(idx) {
  var panel = document.getElementById('hawk-reasoning-panel');
  if (!panel || !_hawkOppsCache[idx]) return;
  var o = _hawkOppsCache[idx];
  var dirLabel = o.direction === 'yes' ? 'Bet YES' : 'Bet NO';
  document.getElementById('hawk-reasoning-market').textContent = dirLabel + ' | Mispriced by +' + ((o.edge||0)*100).toFixed(1) + '% | ' + (o.question || '');
  document.getElementById('hawk-reasoning-text').textContent = o.reasoning || 'No reasoning available';
  panel.style.display = 'block';
  panel.scrollIntoView({behavior:'smooth', block:'nearest'});
}

var _hawkPositionsData = [];

function renderHawkPositions(positions) {
  var el = document.getElementById('hawk-pos-tbody');
  if (!el) return;
  _hawkPositionsData = positions;
  if (positions.length === 0) { el.innerHTML = '<tr><td colspan="11" class="text-muted" style="text-align:center;padding:24px;">No open positions</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < positions.length; i++) {
    var p = positions[i];
    var tl = hawkCalcTimeLeft(p.end_date, p.cur_price || 0, p.category);
    var pnl = p.pnl || 0;
    var pnlColor = pnl >= 0 ? '#00ff44' : '#ff4444';
    var pnlPct = p.pnl_pct || 0;
    var payout = p.payout || (p.shares || 0);
    var estRet = p.est_return || (payout - (p.size_usd || 0));
    var estRetPct = p.est_return_pct || ((p.size_usd || 0) > 0 ? (estRet / (p.size_usd || 1) * 100) : 0);
    html += '<tr>';
    html += '<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc((p.question || '').substring(0, 45)) + '</td>';
    html += '<td>' + esc(p.direction || '?') + '</td>';
    html += '<td>$' + (p.size_usd || 0).toFixed(2) + '</td>';
    html += '<td>' + ((p.entry_price || 0) * 100).toFixed(0) + '\u00A2</td>';
    html += '<td style="font-weight:600;">' + ((p.cur_price || 0) * 100).toFixed(0) + '\u00A2</td>';
    html += '<td id="hawk-pos-timer-' + i + '" style="color:' + tl.color + ';font-weight:600;font-size:0.76rem;white-space:nowrap;">' + tl.text + '<div style="font-size:0.58rem;font-weight:400;opacity:0.7;color:' + tl.color + ';">' + (tl.label || '') + '</div></td>';
    html += '<td style="color:' + pnlColor + ';font-weight:600;">' + (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) + ' <span style="font-size:0.68rem;opacity:0.7;">(' + (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(0) + '%)</span></td>';
    html += '<td style="color:#00d4ff;font-weight:600;">$' + payout.toFixed(2) + '</td>';
    html += '<td style="color:' + (estRet >= 0 ? '#00ff44' : '#ff4444') + ';font-weight:600;">+$' + estRet.toFixed(2) + ' <span style="font-size:0.68rem;opacity:0.7;">(+' + estRetPct.toFixed(0) + '%)</span></td>';
    html += '<td><span class="badge" style="background:rgba(255,255,255,0.08);">' + esc(p.category || '?') + '</span></td>';
    html += '<td style="font-size:0.72rem;color:var(--text-muted);">' + (p.risk_score || '-') + '</td>';
    html += '</tr>';
  }
  el.innerHTML = html;
  _ensurePositionCountdownTicker();
}

function renderHawkHistory(trades) {
  var el = document.getElementById('hawk-hist-tbody');
  if (!el) return;
  if (trades.length === 0) { el.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px;">No trade history</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < Math.min(trades.length, 30); i++) {
    var t = trades[i];
    var won = t.won;
    html += '<tr>';
    html += '<td>' + esc(t.time || '') + '</td>';
    html += '<td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc((t.question || '').substring(0, 40)) + '</td>';
    html += '<td><span class="badge" style="background:rgba(255,255,255,0.08);">' + esc(t.category || '?') + '</span></td>';
    html += '<td>' + ((t.edge || 0) * 100).toFixed(1) + '%</td>';
    html += '<td><span class="badge ' + (won ? 'badge-success' : 'badge-error') + '">' + (won ? 'WIN' : 'LOSS') + '</span></td>';
    html += '</tr>';
  }
  el.innerHTML = html;
}

function _hawkUpdateProgress(data) {
  var prog = document.getElementById('hawk-scan-progress');
  if (!prog) return;
  prog.style.display = 'block';
  var step = document.getElementById('hawk-scan-step');
  var bar = document.getElementById('hawk-scan-bar');
  var pct = document.getElementById('hawk-scan-pct');
  var detail = document.getElementById('hawk-scan-detail');
  if (step) step.textContent = data.step || 'Working...';
  if (bar) bar.style.width = (data.pct || 0) + '%';
  if (pct) pct.textContent = (data.pct || 0) + '%';
  if (detail) detail.textContent = data.detail || '';
}

async function hawkTriggerScan() {
  var btn = document.getElementById('hawk-scan-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Scanning...'; btn.style.opacity = '0.6'; }

  // Show progress bar immediately
  _hawkUpdateProgress({step: 'Starting scan...', detail: 'Initializing', pct: 5});

  try {
    var resp = await fetch('/api/hawk/scan', {method:'POST'});
    var d = await resp.json();
    if (!d.success) {
      _hawkUpdateProgress({step: d.message || 'Failed', detail: '', pct: 0});
      if (btn) { btn.disabled = false; btn.textContent = 'Trigger Scan'; btn.style.opacity = '1'; }
      return;
    }

    // Poll scan progress every 2 seconds
    if (_hawkScanPoller) clearInterval(_hawkScanPoller);
    _hawkScanPoller = setInterval(async function() {
      try {
        var sr = await fetch('/api/hawk/scan-status');
        var sd = await sr.json();
        _hawkUpdateProgress(sd);

        if (sd.done || !sd.scanning) {
          clearInterval(_hawkScanPoller);
          _hawkScanPoller = null;

          // Reload tab data after scan completes
          await loadHawkTab();

          // Reset button
          if (btn) { btn.disabled = false; btn.textContent = 'Trigger Scan'; btn.style.opacity = '1'; }

          // Hide progress after 5 seconds
          setTimeout(function() {
            var prog = document.getElementById('hawk-scan-progress');
            if (prog) prog.style.display = 'none';
          }, 5000);
        }
      } catch(e) { console.error('hawk poll:', e); }
    }, 2000);

  } catch(e) {
    console.error('hawk scan:', e);
    _hawkUpdateProgress({step: 'Error: ' + e.message, detail: '', pct: 0});
    if (btn) { btn.disabled = false; btn.textContent = 'Trigger Scan'; btn.style.opacity = '1'; }
  }
}

// ═══ V10: Intelligence & Self-Improvement ═══

async function loadHawkIntelligence() {
  try {
    // Fetch reviews and learner data in parallel
    var results = await Promise.allSettled([
      fetch('/api/hawk/reviews').then(function(r){return r.json();}),
      fetch('/api/hawk/learner').then(function(r){return r.json();}),
      fetch('/api/hawk/performance').then(function(r){return r.json();})
    ]);
    var reviewData = results[0].status === 'fulfilled' ? results[0].value : {};
    var learnerData = results[1].status === 'fulfilled' ? results[1].value : {};
    var perfData = results[2].status === 'fulfilled' ? results[2].value : {};

    // 1. Model Accuracy (average of 6D learner dimensions)
    var dims = learnerData.dimensions || {};
    var dimNames = Object.keys(dims);
    var totalAcc = 0, dimCount = 0;
    for (var i = 0; i < dimNames.length; i++) {
      var dimData = dims[dimNames[i]] || {};
      var dw = 0, dl = 0;
      for (var k in dimData) { dw += dimData[k].wins || 0; dl += dimData[k].losses || 0; }
      if (dw + dl > 0) { totalAcc += (dw / (dw + dl)) * 100; dimCount++; }
    }
    var avgAccuracy = dimCount > 0 ? totalAcc / dimCount : 0;
    var accEl = document.getElementById('hawk-model-accuracy');
    var accDetailEl = document.getElementById('hawk-model-accuracy-detail');
    if (accEl && avgAccuracy > 0) {
      accEl.textContent = avgAccuracy.toFixed(1) + '%';
      accEl.style.color = avgAccuracy >= 55 ? '#00ff88' : avgAccuracy >= 45 ? '#FFD700' : '#ff4444';
    }
    if (accDetailEl && dimCount > 0) accDetailEl.textContent = dimCount + ' dimensions tracked';

    // 2. Lessons count + last lessons
    var reviews = reviewData.trade_reviews || [];
    var recs = reviewData.recommendations || [];
    var lessonsEl = document.getElementById('hawk-lessons-count');
    var lessonsDetailEl = document.getElementById('hawk-lessons-count-detail');
    var lessonsListEl = document.getElementById('hawk-lessons-list');
    if (lessonsEl) lessonsEl.textContent = recs.length;
    if (lessonsDetailEl) lessonsDetailEl.textContent = reviews.length + ' trades reviewed';
    if (lessonsListEl && recs.length > 0) {
      var lhtml = '';
      var shown = recs.slice(0, 3);
      for (var j = 0; j < shown.length; j++) {
        lhtml += '<div style="background:rgba(255,215,0,0.04);border:1px solid rgba(255,215,0,0.1);border-left:3px solid var(--agent-hawk);border-radius:8px;padding:10px 14px;margin-bottom:8px;">';
        lhtml += '<div style="font-size:0.76rem;color:var(--text-secondary);line-height:1.4;">' + esc(shown[j]) + '</div>';
        lhtml += '</div>';
      }
      lessonsListEl.innerHTML = lhtml;
    }

    // 3. Failure patterns count
    var patterns = reviewData.failure_patterns || [];
    var mistakeEl = document.getElementById('hawk-mistake-freq');
    var mistakeTrendEl = document.getElementById('hawk-mistake-trend');
    if (mistakeEl) {
      mistakeEl.textContent = patterns.length;
      mistakeEl.style.color = patterns.length === 0 ? '#00ff88' : patterns.length <= 2 ? '#FFD700' : '#ff4444';
    }
    if (mistakeTrendEl) {
      var critCount = patterns.filter(function(p){return p.severity === 'critical';}).length;
      mistakeTrendEl.textContent = patterns.length === 0 ? 'Clean — no toxic combos' : critCount + ' critical, ' + (patterns.length - critCount) + ' warning';
    }

    // 4. Self-improvement score (composite)
    var score = 50; // baseline
    if (avgAccuracy > 55) score += 15; else if (avgAccuracy > 50) score += 8;
    if (patterns.length === 0) score += 15; else if (patterns.length <= 1) score += 8;
    if (recs.length >= 3) score += 10; else if (recs.length >= 1) score += 5;
    var wr = perfData.overall_wr || reviewData.win_rate || 0;
    if (wr >= 55) score += 10; else if (wr >= 50) score += 5;
    score = Math.min(100, Math.max(0, score));
    var scoreEl = document.getElementById('hawk-self-score');
    var scoreDetailEl = document.getElementById('hawk-self-score-detail');
    if (scoreEl) {
      scoreEl.textContent = score;
      scoreEl.style.color = score >= 75 ? '#00ff88' : score >= 50 ? '#FFD700' : '#ff4444';
    }
    if (scoreDetailEl) {
      scoreDetailEl.textContent = score >= 75 ? 'Strong — system learning' : score >= 50 ? 'Stable — room to improve' : 'Needs attention';
    }
  } catch(e) {
    console.error('hawk intelligence:', e);
  }
}

// ══════════════════════════════════════
// HAWK SIM TAB — Paper Trading Evaluation
// ══════════════════════════════════════

async function loadHawkSimTab() {
  try {
    var resp = await fetch('/api/hawk/sim');
    var d = await resp.json();
    var setEl = function(id, val) { var e = document.getElementById(id); if(e) e.textContent = val; };
    var setClr = function(id, val, clr) { var e = document.getElementById(id); if(e) { e.textContent = val; e.style.color = clr; } };

    setEl('hsim-total', d.total_trades || 0);
    setEl('hsim-open', d.open || 0);
    setClr('hsim-wr', (d.win_rate || 0).toFixed(1) + '%', wrColor(d.win_rate || 0));
    var pnl = d.total_pnl || 0;
    setClr('hsim-pnl', (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2), pnl >= 0 ? 'var(--success)' : 'var(--error)');
    var roi = d.roi || 0;
    setClr('hsim-roi', (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%', roi >= 0 ? 'var(--success)' : 'var(--error)');
    setEl('hsim-wagered', '$' + (d.total_wagered || 0).toFixed(0));
    setClr('hsim-wins', d.wins || 0, 'var(--success)');
    setClr('hsim-losses', d.losses || 0, 'var(--error)');
    setEl('hsim-avg-edge', (d.avg_edge || 0).toFixed(1) + '%');

    // Status badge
    var badge = document.getElementById('hsim-status-badge');
    if (badge) {
      var statusSpan = badge.querySelector('span:last-child');
      if (statusSpan) {
        if (d.open > 0) { statusSpan.textContent = d.open + ' pending'; statusSpan.style.color = '#ffaa00'; }
        else if (d.resolved > 0) { statusSpan.textContent = 'Evaluated'; statusSpan.style.color = 'var(--success)'; }
        else { statusSpan.textContent = 'No trades'; statusSpan.style.color = 'var(--text-muted)'; }
      }
    }

    // Best / Worst trade cards
    renderHawkSimTradeCard('hsim-best-trade', d.best_trade, 'BEST TRADE', 'var(--success)');
    renderHawkSimTradeCard('hsim-worst-trade', d.worst_trade, 'WORST TRADE', 'var(--error)');

    // Category heatmap
    renderHawkSimCategories(d.categories || {});

    // Open positions
    renderHawkSimOpen(d.open_positions || []);

    // Resolved trades
    renderHawkSimResolved(d.recent_resolved || []);

  } catch(e) { console.error('hawk sim:', e); }
}

function renderHawkSimTradeCard(elId, trade, label, color) {
  var el = document.getElementById(elId);
  if (!el) return;
  if (!trade) { el.innerHTML = '<div class="text-muted" style="text-align:center;padding:12px;">No resolved trades yet</div>'; return; }
  var pnl = trade.pnl || 0;
  var pnlClr = pnl >= 0 ? 'var(--success)' : 'var(--error)';
  var dirClr = trade.direction === 'yes' ? '#00ff88' : '#ff6666';
  var dirArr = trade.direction === 'yes' ? '\u25B2 YES' : '\u25BC NO';
  el.innerHTML = '<div style="padding:4px;">' +
    '<div style="font-size:0.68rem;color:' + color + ';text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">' + label + '</div>' +
    '<div style="font-size:0.82rem;font-weight:600;color:#fff;margin-bottom:4px;">' + esc((trade.question || '').substring(0,80)) + '</div>' +
    '<div style="display:flex;gap:12px;flex-wrap:wrap;font-size:0.74rem;color:var(--text-secondary);">' +
      '<span style="color:' + dirClr + ';font-weight:700;">' + dirArr + '</span>' +
      '<span>$' + (trade.size_usd || 0).toFixed(0) + ' bet</span>' +
      '<span>Edge: ' + ((trade.edge || 0) * 100).toFixed(1) + '%</span>' +
      '<span style="color:' + pnlClr + ';font-weight:700;">P&L: ' + (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) + '</span>' +
      '<span>' + esc(trade.category || '') + '</span>' +
    '</div>' +
  '</div>';
}

function renderHawkSimCategories(cats) {
  var el = document.getElementById('hsim-category-map');
  if (!el) return;
  var keys = Object.keys(cats);
  if (keys.length === 0) { el.innerHTML = '<div class="text-muted" style="text-align:center;padding:12px;">No category data yet</div>'; return; }
  var catColors = {politics:'#4488ff',sports:'#ff8844',crypto_event:'#FFD700',culture:'#cc66ff',other:'#888888'};
  var html = '<div style="display:flex;flex-wrap:wrap;gap:10px;">';
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var c = cats[k];
    var wr = c.win_rate || 0;
    var color = catColors[k] || '#888';
    html += '<div style="background:rgba(255,255,255,0.04);border-left:3px solid ' + color + ';border-radius:6px;padding:12px 16px;min-width:140px;flex:1;">';
    html += '<div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:2px;">' + esc(k) + '</div>';
    html += '<div style="font-size:1.2rem;font-weight:700;color:' + wrColor(wr) + ';">' + wr.toFixed(0) + '%</div>';
    html += '<div style="font-size:0.7rem;color:var(--text-secondary);">' + c.wins + 'W-' + c.losses + 'L</div>';
    var catPnl = c.pnl || 0;
    html += '<div style="font-size:0.72rem;font-weight:600;color:' + (catPnl >= 0 ? 'var(--success)' : 'var(--error)') + ';">' + (catPnl >= 0 ? '+' : '') + '$' + catPnl.toFixed(2) + '</div>';
    html += '</div>';
  }
  html += '</div>';
  el.innerHTML = html;
}

function renderHawkSimOpen(positions) {
  var el = document.getElementById('hsim-open-tbody');
  if (!el) return;
  if (positions.length === 0) { el.innerHTML = '<tr><td colspan="7" class="text-muted" style="text-align:center;padding:24px;">No open positions</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < positions.length; i++) {
    var p = positions[i];
    var dirClr = p.direction === 'yes' ? '#00ff88' : '#ff6666';
    var dirArr = p.direction === 'yes' ? '\u25B2 YES' : '\u25BC NO';
    html += '<tr>';
    html += '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.76rem;" title="' + esc(p.question || '') + '">' + esc((p.question || '').substring(0, 50)) + '</td>';
    html += '<td style="color:' + dirClr + ';font-weight:700;font-size:0.78rem;">' + dirArr + '</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.76rem;">$' + (p.size_usd || 0).toFixed(0) + '</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.76rem;">' + ((p.entry_price || 0) * 100).toFixed(0) + '%</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.76rem;color:#FFD700;">' + ((p.edge || 0) * 100).toFixed(1) + '%</td>';
    html += '<td><span style="font-size:0.7rem;color:var(--text-secondary);">' + esc(p.category || '?') + '</span></td>';
    html += '<td style="font-size:0.72rem;color:var(--text-muted);">' + esc(p.time_str || '') + '</td>';
    html += '</tr>';
  }
  el.innerHTML = html;
}

function renderHawkSimResolved(trades) {
  var el = document.getElementById('hsim-resolved-tbody');
  if (!el) return;
  if (trades.length === 0) { el.innerHTML = '<tr><td colspan="7" class="text-muted" style="text-align:center;padding:24px;">No resolved trades yet</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    var won = t.won;
    var pnl = t.pnl || 0;
    var dirClr = t.direction === 'yes' ? '#00ff88' : '#ff6666';
    var dirArr = t.direction === 'yes' ? '\u25B2 YES' : '\u25BC NO';
    html += '<tr>';
    html += '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.76rem;" title="' + esc(t.question || '') + '">' + esc((t.question || '').substring(0, 50)) + '</td>';
    html += '<td style="color:' + dirClr + ';font-weight:700;font-size:0.78rem;">' + dirArr + '</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.76rem;">$' + (t.size_usd || 0).toFixed(0) + '</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.76rem;">' + ((t.edge || 0) * 100).toFixed(1) + '%</td>';
    html += '<td><span class="badge ' + (won ? 'badge-success' : 'badge-error') + '">' + (won ? 'WIN' : 'LOSS') + '</span></td>';
    html += '<td style="font-family:var(--font-mono);font-weight:600;color:' + (pnl >= 0 ? 'var(--success)' : 'var(--error)') + ';">' + (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) + '</td>';
    html += '<td><span style="font-size:0.7rem;color:var(--text-secondary);">' + esc(t.category || '?') + '</span></td>';
    html += '</tr>';
  }
  el.innerHTML = html;
}

async function hawkSimResolve() {
  var msg = document.getElementById('hawk-sim-resolve-msg');
  if (msg) { msg.style.display = 'block'; msg.style.background = 'rgba(255,215,0,0.08)'; msg.style.color = '#FFD700'; msg.textContent = 'Checking market resolutions...'; }
  try {
    var resp = await fetch('/api/hawk/resolve', {method:'POST'});
    var d = await resp.json();
    if (msg) {
      if (d.resolved > 0) {
        msg.style.background = 'rgba(0,255,136,0.08)';
        msg.style.color = 'var(--success)';
        msg.textContent = 'Resolved ' + d.resolved + ' trades: ' + d.wins + ' wins, ' + d.losses + ' losses. ' + d.skipped + ' still pending.';
      } else {
        msg.style.background = 'rgba(255,255,255,0.04)';
        msg.style.color = 'var(--text-muted)';
        msg.textContent = 'No trades resolved yet. ' + (d.checked || 0) + ' checked, ' + (d.skipped || 0) + ' markets still open.';
      }
    }
    await loadHawkSimTab();
    await loadHawkTab();
  } catch(e) {
    if (msg) { msg.style.background = 'rgba(255,0,0,0.08)'; msg.style.color = 'var(--error)'; msg.textContent = 'Resolution check failed: ' + e.message; }
  }
}

// ══════════════════════════════════════
// VIPER TAB — 24/7 Intelligence Engine
// ══════════════════════════════════════
var _viperScanPoller = null;
var _viperAutoTimer = null;
var _viperNextRefresh = 0;
var _viperCountdownTimer = null;

function viperStartAutoRefresh() {
  viperStopAutoRefresh();
  _viperNextRefresh = Date.now() + 600000; // 10 minutes
  _viperAutoTimer = setInterval(function() {
    loadViperTab();
    _viperNextRefresh = Date.now() + 600000;
  }, 600000);
  _viperCountdownTimer = setInterval(viperUpdateCountdown, 1000);
  viperUpdateCountdown();
}

function viperStopAutoRefresh() {
  if (_viperAutoTimer) { clearInterval(_viperAutoTimer); _viperAutoTimer = null; }
  if (_viperCountdownTimer) { clearInterval(_viperCountdownTimer); _viperCountdownTimer = null; }
}

function viperUpdateCountdown() {
  var badge = document.getElementById('viper-auto-timer');
  if (!badge) return;
  var remaining = Math.max(0, _viperNextRefresh - Date.now());
  var mins = Math.floor(remaining / 60000);
  var secs = Math.floor((remaining % 60000) / 1000);
  badge.textContent = 'Auto: ' + mins + 'm ' + (secs < 10 ? '0' : '') + secs + 's';
}

function viperRefreshNow() {
  loadViperTab();
  _viperNextRefresh = Date.now() + 600000;
  viperUpdateCountdown();
}

function viperGetCostProfit(type, estimatedValue) {
  var cost = '--';
  var profit = '--';
  switch(type) {
    case 'brand_deal':
      cost = '$0 (free)';
      profit = estimatedValue || '--';
      break;
    case 'affiliate':
      cost = '$0 (free)';
      profit = estimatedValue || '--';
      break;
    case 'trending_content':
      cost = 'Time only';
      profit = 'Organic reach';
      break;
    case 'collab':
      cost = 'Content trade';
      profit = estimatedValue || '--';
      break;
    case 'ad_revenue':
      cost = '$0 (free)';
      profit = estimatedValue || '--';
      break;
    default:
      cost = '--';
      profit = estimatedValue || '--';
  }
  return {cost: cost, profit: profit};
}

async function loadViperTab() {
  try {
    var resp = await fetch('/api/viper');
    var d = await resp.json();
    var s = d.summary || {};
    document.getElementById('viper-intel-count').textContent = s.total_intel || s.last_scan_items || 0;
    document.getElementById('viper-matched').textContent = s.total_matched || s.last_scan_matched || 0;
    var srcCount = s.sources ? Object.keys(s.sources).length : 0;
    document.getElementById('viper-sources').textContent = srcCount || 3;
    document.getElementById('viper-cycle').textContent = s.cycle || 0;

    // Hawk Briefing badges
    var hb = d.hawk_briefing;
    var briefSec = document.getElementById('viper-hawk-briefing');
    var briefBadges = document.getElementById('viper-briefing-badges');
    if (briefSec && briefBadges && hb) {
      briefSec.style.display = 'block';
      var bb = '';
      var modeColor = hb.active ? '#00ff88' : 'var(--text-muted)';
      var modeLabel = hb.active ? 'TARGETED' : 'GENERIC';
      bb += '<div class="widget-badge"><span class="wb-label">Hawk Briefing:</span> <span style="color:' + modeColor + ';font-weight:700;">' + modeLabel + '</span></div>';
      bb += '<div class="widget-badge"><span class="wb-label">Briefed Markets:</span> <span style="color:#FFD700;">' + (hb.briefed_markets || 0) + '</span></div>';
      if (hb.age_minutes !== null) {
        var ageColor = hb.age_minutes > 120 ? 'var(--error)' : hb.age_minutes > 60 ? 'var(--warning)' : 'var(--text-muted)';
        bb += '<div class="widget-badge"><span class="wb-label">Briefing Age:</span> <span style="color:' + ageColor + ';">' + Math.round(hb.age_minutes) + 'm</span></div>';
      }
      if (s.tavily_ran !== undefined) {
        var tavColor = s.tavily_ran ? '#00ff88' : 'var(--text-muted)';
        bb += '<div class="widget-badge"><span class="wb-label">Tavily:</span> <span style="color:' + tavColor + ';">' + (s.tavily_ran ? 'RAN' : 'SKIPPED') + '</span></div>';
      }
      briefBadges.innerHTML = bb;
    }
  } catch(e) { console.error('viper status:', e); }

  // Intelligence feed
  try {
    var oppResp = await fetch('/api/viper/opportunities');
    var oppData = await oppResp.json();
    renderViperIntel(oppData.opportunities || []);
  } catch(e) {}

  // Cost audit
  var costData = null;
  try {
    var costResp = await fetch('/api/viper/costs');
    costData = await costResp.json();
    renderViperCosts(costData);
  } catch(e) {}

  // Soren metrics
  try {
    var sorenResp = await fetch('/api/viper/soren-metrics');
    var sorenData = await sorenResp.json();
    renderViperSorenMetrics(sorenData);
  } catch(e) {}

  // Soren opportunity feed
  try {
    var sorenOppResp = await fetch('/api/viper/soren-opportunities');
    var sorenOppData = await sorenOppResp.json();
    renderSorenOpportunities(sorenOppData);
  } catch(e) {}

  // Brotherhood P&L
  loadViperPnl();

  // Anomalies
  loadViperAnomalies();

  // Agent Digests
  loadViperDigests();

  // Cost Optimization (from cost data)
  renderCostOptimization(costData);

  // Shelby push count from status
  try {
    var pushEl = document.getElementById('viper-push-count');
    if (pushEl && s) pushEl.textContent = s.pushes || 0;
  } catch(e) {}

  // Brand Channel
  loadBrandChannel();
}

function renderViperIntel(items) {
  var el = document.getElementById('viper-intel-tbody');
  if (!el) return;
  if (!items || items.length === 0) {
    var lastScan = document.getElementById('viper-cycle') ? document.getElementById('viper-cycle').textContent : '0';
    el.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px;">No intelligence collected yet' + (lastScan !== '0' ? ' (last scan: cycle ' + esc(lastScan) + ')' : '') + '</td></tr>';
    return;
  }
  var html = '';
  var srcColors = {tavily:'#8B5CF6', reddit:'#FF4500', polymarket:'#00d4ff'};
  var srcIcons = {tavily:'&#x1F4F0;', reddit:'&#x1F4AC;', polymarket:'&#x1F4CA;'};
  for (var i = 0; i < Math.min(items.length, 25); i++) {
    var item = items[i];
    var sent = item.sentiment || 0;
    var sentColor = sent > 0.2 ? 'var(--success)' : sent < -0.2 ? 'var(--error)' : 'var(--text-muted)';
    var sentLabel = sent > 0.2 ? 'Positive' : sent < -0.2 ? 'Negative' : 'Neutral';
    var scoreColor = (item.score || 0) >= 70 ? 'var(--success)' : (item.score || 0) >= 40 ? 'var(--warning)' : 'var(--text-muted)';
    var srcKey = (item.source || 'unknown').split('/')[0];
    var srcFull = item.source || 'unknown';
    var srcColor = srcColors[srcKey] || '#00ff88';
    var srcIcon = srcIcons[srcKey] || '&#x1F50D;';
    // Extract domain from URL for display
    var domain = '';
    try { if (item.url) domain = new URL(item.url).hostname.replace('www.',''); } catch(e){}
    html += '<tr>';
    html += '<td><span class="badge" style="background:rgba(0,255,136,0.12);color:' + srcColor + ';font-size:0.68rem;">' + srcIcon + ' ' + esc(srcKey) + '</span>';
    if (srcFull.includes('/')) html += '<div style="font-size:0.58rem;color:var(--text-muted);">' + esc(srcFull.split('/').slice(1).join('/')) + '</div>';
    html += '</td>';
    html += '<td style="max-width:280px;"><div style="font-size:0.76rem;line-height:1.3;white-space:normal;">' + esc((item.headline || item.title || '').substring(0, 80)) + '</div>';
    if (item.url) {
      html += '<a href="' + esc(item.url) + '" target="_blank" style="font-size:0.6rem;color:#8B5CF6;text-decoration:none;display:inline-flex;align-items:center;gap:2px;margin-top:2px;">&#x1F517; ' + esc(domain || 'Link') + '</a>';
    }
    html += '</td>';
    html += '<td><span class="badge" style="background:rgba(255,255,255,0.06);">' + esc(item.category || 'other') + '</span></td>';
    html += '<td style="color:' + sentColor + ';font-weight:600;">' + sentLabel + '</td>';
    html += '<td style="color:' + scoreColor + ';font-weight:600;">' + (item.score || 0) + '</td>';
    html += '</tr>';
  }
  el.innerHTML = html;
}

function renderViperCosts(data) {
  var costs = data.costs || [];
  var totals = data.agent_totals || {};
  var totalMonthly = data.total_monthly || 0;
  var daysTracked = data.days_tracked || 1;

  var sumEl = document.getElementById('viper-cost-summary');
  if (sumEl) {
    var sh = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">';
    sh += '<div style="background:rgba(0,255,136,0.08);border:1px solid rgba(0,255,136,0.2);border-radius:8px;padding:10px 16px;">';
    sh += '<div style="font-size:0.68rem;color:var(--text-muted);">Total / Month</div>';
    sh += '<div style="font-size:1.3rem;font-weight:700;color:#00ff88;">$' + totalMonthly.toFixed(2) + '</div>';
    sh += '<div style="font-size:0.62rem;color:var(--text-muted);">' + daysTracked + ' day' + (daysTracked > 1 ? 's' : '') + ' tracked</div></div>';
    var sortedAgents = Object.keys(totals).sort(function(a,b){ return totals[b] - totals[a]; });
    for (var i = 0; i < sortedAgents.length; i++) {
      var ag = sortedAgents[i];
      var cost = totals[ag];
      var agColor = ag === 'infrastructure' ? '#aaaaaa' : (AGENT_COLORS[ag] || EVENT_AGENT_COLORS[ag] || '#888');
      sh += '<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:10px 16px;min-width:80px;">';
      sh += '<div style="font-size:0.68rem;color:var(--text-muted);">' + esc(ag === 'infrastructure' ? 'Infra' : (AGENT_NAMES[ag] || ag)) + '</div>';
      sh += '<div style="font-size:1.1rem;font-weight:700;color:' + agColor + ';">$' + cost.toFixed(2) + '</div></div>';
    }
    sh += '</div>';
    sumEl.innerHTML = sh;
  }

  var el = document.getElementById('viper-cost-tbody');
  if (!el) return;
  if (costs.length === 0) { el.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:24px;">No cost data</td></tr>'; return; }
  var html = '';
  for (var j = 0; j < costs.length; j++) {
    var c = costs[j];
    var ac = c.agent === 'infrastructure' ? '#aaaaaa' : (AGENT_COLORS[c.agent] || EVENT_AGENT_COLORS[c.agent] || '#888');
    var cc = c.cost_usd > 0 ? (c.cost_usd > 30 ? 'var(--warning)' : 'var(--text)') : 'var(--text-muted)';
    html += '<tr>';
    html += '<td style="color:' + ac + ';font-weight:600;">' + esc(c.agent === 'infrastructure' ? 'Infra' : (AGENT_NAMES[c.agent] || c.agent || '')) + '</td>';
    html += '<td>' + esc(c.service || '') + '</td>';
    html += '<td style="font-family:var(--font-mono);color:' + cc + ';">$' + (c.cost_usd || 0).toFixed(2) + '</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.7rem;">' + (c.cost_per_call ? '$' + c.cost_per_call.toFixed(4) : '--') + '</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.7rem;">' + (c.daily_calls ? Math.round(c.daily_calls) + '/day' : '--') + '</td>';
    html += '<td style="font-size:0.66rem;color:var(--text-muted);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(c.source || '') + '">' + esc(c.source || '--') + '</td>';
    html += '</tr>';
  }
  el.innerHTML = html;
}

function renderViperSorenMetrics(data) {
  var el = document.getElementById('viper-soren-metrics');
  if (!el) return;
  if (data === null || data === undefined || data.followers === undefined) {
    el.innerHTML = '<div class="text-muted" style="padding:12px;">No Soren metrics available yet.</div>';
    return;
  }

  var followers = data.followers || 0;
  var engRate = data.engagement_rate || 0;
  var cpm = data.estimated_cpm || 0;
  var growthRate = data.growth_rate || 0;
  var brandReady = data.brand_ready;
  var brandOpps = data.brand_opportunities || [];

  // Milestone progress
  var milestone = followers < 1000 ? 1000 : followers < 10000 ? 10000 : 100000;
  var milestoneLabel = milestone >= 100000 ? '100K' : milestone >= 10000 ? '10K' : '1K';
  var milestonePct = Math.min(100, Math.round((followers / milestone) * 100));

  var html = '';
  // Row 1: Metrics grid
  html += '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px;">';

  // Followers with progress bar
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:12px 14px;">';
  html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-bottom:4px;">Followers</div>';
  html += '<div style="font-size:1.2rem;font-weight:700;">' + followers.toLocaleString() + '</div>';
  html += '<div style="margin-top:6px;height:4px;background:rgba(255,255,255,0.08);border-radius:2px;overflow:hidden;">';
  html += '<div style="height:100%;width:' + milestonePct + '%;background:linear-gradient(90deg,#cc66ff,#8B5CF6);border-radius:2px;"></div></div>';
  html += '<div style="font-size:0.58rem;color:var(--text-muted);margin-top:3px;">' + milestonePct + '% to ' + milestoneLabel + '</div>';
  html += '</div>';

  // Engagement Rate
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:12px 14px;">';
  html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-bottom:4px;">Engagement</div>';
  html += '<div style="font-size:1.2rem;font-weight:700;">' + (engRate * 100).toFixed(1) + '%</div>';
  var engColor = engRate >= 0.05 ? 'var(--success)' : engRate >= 0.02 ? 'var(--warning)' : 'var(--text-muted)';
  var engLabel = engRate >= 0.05 ? 'Excellent' : engRate >= 0.02 ? 'Good' : 'Building';
  html += '<div style="font-size:0.62rem;color:' + engColor + ';margin-top:4px;">' + engLabel + '</div>';
  html += '</div>';

  // Est. CPM
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:12px 14px;">';
  html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-bottom:4px;">Est. CPM</div>';
  html += '<div style="font-size:1.2rem;font-weight:700;color:var(--success);">$' + cpm.toFixed(2) + '</div>';
  if (growthRate > 0) {
    html += '<div style="font-size:0.62rem;color:var(--success);margin-top:4px;">+' + growthRate.toFixed(1) + '% growth</div>';
  } else {
    html += '<div style="font-size:0.62rem;color:var(--text-muted);margin-top:4px;">No growth data yet</div>';
  }
  html += '</div>';

  // Brand Ready
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:12px 14px;">';
  html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-bottom:4px;">Brand Ready</div>';
  var brandColor = brandReady ? 'var(--success)' : 'var(--warning)';
  var brandLabel = brandReady ? 'Yes' : 'Not yet';
  html += '<div style="font-size:1.2rem;font-weight:700;color:' + brandColor + ';">' + brandLabel + '</div>';
  html += '<div style="font-size:0.62rem;color:var(--text-muted);margin-top:4px;">' + brandOpps.length + ' opportunities</div>';
  html += '</div>';

  html += '</div>';

  // Row 2: Brand Opportunities
  if (brandOpps.length > 0) {
    html += '<div style="margin-top:4px;">';
    html += '<div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:8px;font-weight:600;">Growth Opportunities</div>';
    html += '<div style="display:flex;gap:8px;flex-wrap:wrap;">';
    for (var i = 0; i < brandOpps.length; i++) {
      var bo = brandOpps[i];
      var readyIcon = bo.ready ? '&#x2705;' : '&#x1F512;';
      var bgStyle = bo.ready ? 'rgba(0,255,136,0.08);border:1px solid rgba(0,255,136,0.2)' : 'rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.06)';
      html += '<div style="background:' + bgStyle + ';border-radius:6px;padding:8px 12px;flex:1;min-width:140px;">';
      html += '<div style="font-size:0.72rem;font-weight:600;">' + readyIcon + ' ' + esc(bo.platform || bo.name || 'Opportunity') + '</div>';
      if (bo.requirement) html += '<div style="font-size:0.6rem;color:var(--text-muted);margin-top:2px;">' + esc(bo.requirement) + '</div>';
      if (bo.potential_value) html += '<div style="font-size:0.64rem;color:#cc66ff;margin-top:2px;">' + esc(bo.potential_value) + '</div>';
      html += '</div>';
    }
    html += '</div></div>';
  }

  // Last updated timestamp
  var tsEl = document.getElementById('viper-metrics-updated');
  if (tsEl) tsEl.textContent = 'Last updated: ' + new Date().toLocaleTimeString();

  el.innerHTML = html;
}

function renderSorenOpportunities(data) {
  var badges = document.getElementById('soren-opp-badges');
  var tbody = document.getElementById('soren-opp-tbody');
  if (!badges || !tbody) return;

  var opps = data.opportunities || [];
  var types = data.types || {};
  var updated = data.updated || 0;

  // Badges
  var bh = '';
  bh += '<div class="widget-badge"><span class="wb-label">Total:</span> <span style="color:#cc66ff;font-weight:700;">' + opps.length + '</span></div>';
  var typeColors = {brand_deal:'#FFD700', affiliate:'#00ff88', trending_content:'#ff6b6b', collab:'#8B5CF6', ad_revenue:'#00d4ff'};
  var typeLabels = {brand_deal:'Brand Deals', affiliate:'Affiliate', trending_content:'Trends', collab:'Collabs', ad_revenue:'Ad Revenue'};
  var typeKeys = Object.keys(types);
  for (var i = 0; i < typeKeys.length; i++) {
    var tk = typeKeys[i];
    var tc = typeColors[tk] || '#888';
    bh += '<div class="widget-badge"><span class="wb-label">' + (typeLabels[tk] || tk) + ':</span> <span style="color:' + tc + ';">' + types[tk] + '</span></div>';
  }
  if (updated > 0) {
    var age = Math.round((Date.now() / 1000 - updated) / 60);
    bh += '<div class="widget-badge"><span class="wb-label">Updated:</span> <span style="color:var(--text-muted);">' + age + 'm ago</span></div>';
  }
  badges.innerHTML = bh;

  // Table
  if (opps.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-muted" style="text-align:center;padding:24px;">No Soren opportunities yet — trigger a scan</td></tr>';
    return;
  }

  var html = '';
  for (var j = 0; j < Math.min(opps.length, 25); j++) {
    var o = opps[j];
    var typeColor = typeColors[o.type] || '#888';
    var typeLabel = typeLabels[o.type] || o.type;
    var fitColor = o.fit_score >= 60 ? 'var(--success)' : o.fit_score >= 35 ? 'var(--warning)' : 'var(--text-muted)';
    var urgColor = o.urgency === 'high' ? 'var(--error)' : o.urgency === 'medium' ? 'var(--warning)' : 'var(--text-muted)';
    var cp = viperGetCostProfit(o.type, o.estimated_value);

    html += '<tr>';
    html += '<td><span class="badge" style="background:rgba(255,255,255,0.06);color:' + typeColor + ';font-size:0.64rem;">' + esc(typeLabel) + '</span></td>';
    html += '<td style="max-width:220px;"><div style="font-size:0.74rem;line-height:1.3;white-space:normal;">' + esc((o.title || '').substring(0, 80)) + '</div>';
    if (o.url) {
      var domain = '';
      try { domain = new URL(o.url).hostname.replace('www.',''); } catch(e){}
      html += '<a href="' + esc(o.url) + '" target="_blank" style="font-size:0.58rem;color:#8B5CF6;text-decoration:none;">&#x1F517; ' + esc(domain || 'Link') + '</a>';
    }
    html += '</td>';
    html += '<td style="color:' + fitColor + ';font-weight:600;font-size:0.8rem;">' + (o.fit_score || 0) + '</td>';
    html += '<td style="font-size:0.72rem;color:var(--text-secondary);">' + esc(o.estimated_value || '--') + '</td>';
    html += '<td style="font-size:0.68rem;color:var(--text-muted);">' + esc(cp.cost) + '</td>';
    html += '<td style="font-size:0.68rem;color:#cc66ff;font-weight:600;">' + esc(cp.profit) + '</td>';
    html += '<td style="color:' + urgColor + ';font-weight:600;font-size:0.72rem;">' + esc((o.urgency || 'low').toUpperCase()) + '</td>';
    html += '<td style="font-size:0.68rem;color:var(--text-muted);max-width:140px;white-space:normal;">' + esc(o.action || '') + '</td>';
    html += '</tr>';
  }
  tbody.innerHTML = html;
}

function _viperUpdateProgress(data) {
  var prog = document.getElementById('viper-scan-progress');
  if (!prog) return;
  prog.style.display = 'block';
  var step = document.getElementById('viper-scan-step');
  var bar = document.getElementById('viper-scan-bar');
  var pct = document.getElementById('viper-scan-pct');
  var detail = document.getElementById('viper-scan-detail');
  if (step) step.textContent = data.step || 'Working...';
  if (bar) bar.style.width = (data.pct || 0) + '%';
  if (pct) pct.textContent = (data.pct || 0) + '%';
  if (detail) detail.textContent = data.detail || '';
}

async function viperTriggerScan() {
  var btn = document.getElementById('viper-scan-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Scanning...'; btn.style.opacity = '0.6'; }

  _viperUpdateProgress({step: 'Starting scan...', detail: 'Initializing intelligence sources', pct: 5});

  try {
    var resp = await fetch('/api/viper/scan', {method:'POST'});
    var d = await resp.json();
    if (!d.success) {
      _viperUpdateProgress({step: d.message || 'Failed', detail: '', pct: 0});
      if (btn) { btn.disabled = false; btn.textContent = 'Trigger Scan'; btn.style.opacity = '1'; }
      return;
    }

    if (_viperScanPoller) clearInterval(_viperScanPoller);
    _viperScanPoller = setInterval(async function() {
      try {
        var sr = await fetch('/api/viper/scan-status');
        var sd = await sr.json();
        _viperUpdateProgress(sd);

        if (sd.done || !sd.scanning) {
          clearInterval(_viperScanPoller);
          _viperScanPoller = null;
          await loadViperTab();
          if (btn) { btn.disabled = false; btn.textContent = 'Trigger Scan'; btn.style.opacity = '1'; }
          setTimeout(function() {
            var prog = document.getElementById('viper-scan-progress');
            if (prog) prog.style.display = 'none';
          }, 5000);
        }
      } catch(e) { console.error('viper poll:', e); }
    }, 2000);

  } catch(e) {
    console.error('viper scan:', e);
    _viperUpdateProgress({step: 'Error: ' + e.message, detail: '', pct: 0});
    if (btn) { btn.disabled = false; btn.textContent = 'Trigger Scan'; btn.style.opacity = '1'; }
  }
}

// ══════════════════════════════════════
// VIPER P&L, ANOMALIES, DIGESTS
// ══════════════════════════════════════

async function loadViperPnl() {
  try {
    var resp = await fetch('/api/viper/pnl');
    var d = await resp.json();
    var netEl = document.getElementById('viper-pnl-net');
    var monthEl = document.getElementById('viper-pnl-monthly');
    var costEl = document.getElementById('viper-pnl-cost');
    var trendEl = document.getElementById('viper-pnl-trend');
    if (netEl) {
      var net = d.net_daily || 0;
      netEl.textContent = '$' + net.toFixed(2);
      netEl.style.color = net >= 0 ? 'var(--success)' : 'var(--error)';
    }
    if (monthEl) {
      var monthly = d.net_monthly_est || 0;
      monthEl.textContent = '$' + monthly.toFixed(2);
      monthEl.style.color = monthly >= 0 ? 'var(--success)' : 'var(--error)';
    }
    if (costEl) {
      var cost = (d.costs || {}).daily_api || 0;
      var infra = (d.costs || {}).infrastructure_daily || 0;
      costEl.textContent = '$' + (cost + infra).toFixed(2);
      costEl.style.color = 'var(--warning)';
    }
    if (trendEl) {
      var trendMap = {profitable: 'Profitable', near_breakeven: 'Near Breakeven', needs_improvement: 'Needs Work'};
      var trendColors = {profitable: 'var(--success)', near_breakeven: 'var(--warning)', needs_improvement: 'var(--error)'};
      var trend = d.trend || 'unknown';
      trendEl.textContent = trendMap[trend] || trend;
      trendEl.style.color = trendColors[trend] || 'var(--text-muted)';
    }
    // Revenue detail badges
    var detailEl = document.getElementById('viper-pnl-detail');
    if (detailEl && d.revenue) {
      var r = d.revenue;
      var h = '';
      h += '<div class="widget-badge"><span class="wb-label">Garves P&L:</span> <span style="color:' + ((r.garves || 0) >= 0 ? 'var(--success)' : 'var(--error)') + ';font-weight:700;">$' + (r.garves || 0).toFixed(2) + '</span></div>';
      h += '<div class="widget-badge"><span class="wb-label">Hawk P&L:</span> <span style="color:' + ((r.hawk || 0) >= 0 ? 'var(--success)' : 'var(--error)') + ';font-weight:700;">$' + (r.hawk || 0).toFixed(2) + '</span></div>';
      if (r.garves_win_rate !== undefined) h += '<div class="widget-badge"><span class="wb-label">Garves WR:</span> <span style="color:' + (r.garves_win_rate >= 0.5 ? 'var(--success)' : 'var(--warning)') + ';">' + (r.garves_win_rate * 100).toFixed(1) + '%</span></div>';
      if (r.hawk_win_rate !== undefined) h += '<div class="widget-badge"><span class="wb-label">Hawk WR:</span> <span style="color:' + (r.hawk_win_rate >= 0.5 ? 'var(--success)' : 'var(--warning)') + ';">' + (r.hawk_win_rate * 100).toFixed(1) + '%</span></div>';
      h += '<div class="widget-badge"><span class="wb-label">Best:</span> <span style="color:#FFD700;">' + esc(d.best_performer || '--') + '</span></div>';
      detailEl.innerHTML = h;
    }
  } catch(e) { console.error('viper pnl:', e); }
}

async function loadViperAnomalies() {
  try {
    var resp = await fetch('/api/viper/anomalies');
    var d = await resp.json();
    var el = document.getElementById('viper-anomalies');
    if (!el) return;
    var alerts = d.anomalies || [];
    if (alerts.length === 0) {
      el.innerHTML = '<div style="background:rgba(0,255,136,0.06);border:1px solid rgba(0,255,136,0.15);border-radius:8px;padding:12px 16px;text-align:center;color:var(--success);font-size:0.78rem;font-weight:600;">All Clear — No anomalies detected</div>';
      return;
    }
    var h = '';
    var sevColors = {critical: 'var(--error)', warning: 'var(--warning)', info: 'var(--text-muted)'};
    var sevIcons = {critical: '&#x1F6A8;', warning: '&#x26A0;&#xFE0F;', info: '&#x2139;&#xFE0F;'};
    for (var i = 0; i < alerts.length; i++) {
      var a = alerts[i];
      var sc = sevColors[a.severity] || 'var(--text-muted)';
      var si = sevIcons[a.severity] || '';
      h += '<div style="background:rgba(255,80,80,0.06);border:1px solid ' + sc + ';border-radius:8px;padding:10px 16px;margin-bottom:6px;">';
      h += '<div style="display:flex;justify-content:space-between;align-items:center;">';
      h += '<span style="font-size:0.78rem;font-weight:600;color:' + sc + ';">' + si + ' ' + esc(a.message || '') + '</span>';
      h += '<span class="badge" style="background:rgba(255,255,255,0.06);color:' + sc + ';font-size:0.62rem;">' + esc((a.severity || '').toUpperCase()) + '</span>';
      h += '</div>';
      h += '<div style="font-size:0.66rem;color:var(--text-muted);margin-top:4px;">Agent: ' + esc(a.agent || '') + ' | Type: ' + esc(a.type || '') + '</div>';
      h += '</div>';
    }
    el.innerHTML = h;
  } catch(e) { console.error('viper anomalies:', e); }
}

async function loadViperDigests() {
  try {
    var resp = await fetch('/api/viper/digests');
    var d = await resp.json();
    var el = document.getElementById('viper-digests');
    if (!el) return;
    var agents = ['garves', 'hawk', 'soren', 'shelby', 'atlas'];
    var agentColors = {garves:'#FFD700', hawk:'#00d4ff', soren:'#cc66ff', shelby:'#ff6b6b', atlas:'#8B5CF6'};
    var h = '';
    for (var i = 0; i < agents.length; i++) {
      var ag = agents[i];
      var digest = d[ag] || {};
      var fresh = digest.fresh;
      var count = digest.item_count || 0;
      var age = digest.age_minutes;
      var borderColor = fresh ? 'rgba(0,255,136,0.3)' : 'rgba(255,255,255,0.08)';
      var statusColor = fresh ? 'var(--success)' : 'var(--text-muted)';
      var statusText = fresh ? 'Fresh' : (age ? Math.round(age) + 'm old' : 'No data');
      h += '<div style="background:rgba(255,255,255,0.04);border:1px solid ' + borderColor + ';border-radius:8px;padding:10px 14px;min-width:120px;flex:1;">';
      h += '<div style="font-size:0.72rem;font-weight:700;color:' + (agentColors[ag] || '#888') + ';">' + esc(ag.charAt(0).toUpperCase() + ag.slice(1)) + '</div>';
      h += '<div style="font-size:1rem;font-weight:700;">' + count + ' <span style="font-size:0.66rem;color:var(--text-muted);">items</span></div>';
      h += '<div style="font-size:0.62rem;color:' + statusColor + ';">' + statusText + '</div>';
      h += '</div>';
    }
    el.innerHTML = h;
  } catch(e) { console.error('viper digests:', e); }
}

function renderCostOptimization(costData) {
  var el = document.getElementById('viper-cost-recs');
  if (!el || !costData) return;
  var waste = costData.waste || [];
  var patterns = costData.llm_patterns || [];
  var recs = costData.recommendations || '';
  if (waste.length === 0 && patterns.length === 0 && !recs) {
    el.innerHTML = '<div class="text-muted" style="text-align:center;padding:12px;">No optimization opportunities found</div>';
    return;
  }
  var h = '';
  // Waste flags
  if (waste.length > 0) {
    h += '<div style="margin-bottom:10px;">';
    for (var i = 0; i < waste.length; i++) {
      var w = waste[i];
      h += '<div style="background:rgba(255,200,0,0.06);border:1px solid rgba(255,200,0,0.2);border-radius:8px;padding:8px 14px;margin-bottom:6px;">';
      h += '<span style="font-weight:600;color:var(--warning);font-size:0.76rem;">' + esc(w.agent || '') + ': $' + (w.monthly || 0).toFixed(2) + '/mo</span>';
      h += ' <span style="font-size:0.66rem;color:var(--text-muted);">' + esc(w.reason || '') + '</span>';
      h += '</div>';
    }
    h += '</div>';
  }
  // LLM pattern recommendations
  if (patterns.length > 0) {
    h += '<div style="margin-bottom:10px;">';
    for (var j = 0; j < patterns.length; j++) {
      var p = patterns[j];
      var sev = p.severity === 'high' ? 'var(--error)' : 'var(--warning)';
      h += '<div style="background:rgba(139,92,246,0.06);border:1px solid rgba(139,92,246,0.2);border-radius:8px;padding:8px 14px;margin-bottom:6px;">';
      h += '<div style="font-weight:600;color:' + sev + ';font-size:0.76rem;">' + esc(p.agent || '') + ': ' + esc(p.issue || '') + '</div>';
      h += '<div style="font-size:0.68rem;color:var(--text-secondary);">' + esc(p.recommendation || '') + '</div>';
      if (p.estimated_savings_monthly) h += '<div style="font-size:0.66rem;color:var(--success);">Est. savings: $' + p.estimated_savings_monthly.toFixed(2) + '/mo</div>';
      h += '</div>';
    }
    h += '</div>';
  }
  // LLM text recommendations
  if (recs) {
    h += '<div style="background:rgba(0,255,136,0.04);border:1px solid rgba(0,255,136,0.12);border-radius:8px;padding:10px 14px;font-size:0.72rem;color:var(--text-secondary);white-space:pre-wrap;line-height:1.5;">' + esc(recs) + '</div>';
  }
  el.innerHTML = h;
}

// ══════════════════════════════════════
// BRAND CHANNEL — Viper → Soren → Lisa
// ══════════════════════════════════════

async function loadBrandChannel() {
  try {
    var resp = await fetch('/api/viper/brand-channel');
    var d = await resp.json();
    var msgs = d.messages || [];
    var stats = d.stats || {};
    renderBrandChannelBadges(stats);
    renderBrandChannelReview(msgs.filter(function(m) { return m.status === 'assessed'; }));
    renderBrandChannelTable(msgs);
  } catch(e) { console.error('brand channel:', e); }
}

function renderBrandChannelBadges(stats) {
  var el = document.getElementById('brand-channel-badges');
  if (!el) return;
  var total = stats.total || 0;
  var approved = stats.approved || 0;
  var review = stats.assessed || 0;
  var rejected = stats.rejected || 0;
  var planned = stats.content_planned || 0;
  var h = '';
  h += '<div class="widget-badge"><span class="wb-label">Total:</span> <span style="color:#00ff88;font-weight:700;">' + total + '</span></div>';
  h += '<div class="widget-badge" style="background:rgba(0,255,136,0.08);"><span class="wb-label">Approved:</span> <span style="color:var(--success);font-weight:700;">' + approved + '</span></div>';
  h += '<div class="widget-badge" style="background:rgba(255,200,0,0.08);"><span class="wb-label">Needs Review:</span> <span style="color:var(--warning);font-weight:700;">' + review + '</span></div>';
  h += '<div class="widget-badge" style="background:rgba(255,80,80,0.08);"><span class="wb-label">Rejected:</span> <span style="color:var(--error);font-weight:700;">' + rejected + '</span></div>';
  h += '<div class="widget-badge" style="background:rgba(200,100,255,0.08);"><span class="wb-label">Content Planned:</span> <span style="color:#cc66ff;font-weight:700;">' + planned + '</span></div>';
  el.innerHTML = h;
}

function renderBrandChannelReview(msgs) {
  var el = document.getElementById('brand-channel-review');
  if (!el) return;
  if (!msgs || msgs.length === 0) {
    el.innerHTML = '';
    return;
  }
  var h = '<div style="display:flex;flex-direction:column;gap:10px;">';
  for (var i = 0; i < msgs.length; i++) {
    var m = msgs[i];
    var opp = m.opportunity || {};
    var ba = m.brand_assessment || {};
    var score = ba.brand_fit_score || 0;
    var scoreColor = score >= 70 ? 'var(--success)' : score >= 40 ? 'var(--warning)' : 'var(--error)';
    h += '<div style="background:rgba(255,200,0,0.06);border:1px solid rgba(255,200,0,0.2);border-radius:10px;padding:14px 18px;">';
    h += '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">';
    h += '<div><div style="font-weight:700;font-size:0.82rem;color:var(--text-primary);">' + esc(opp.title || 'Untitled') + '</div>';
    h += '<div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">' + esc(opp.type || '') + ' | ' + esc(opp.source || '') + '</div></div>';
    h += '<div style="text-align:right;"><div style="font-size:1.3rem;font-weight:700;color:' + scoreColor + ';">' + score + '</div>';
    h += '<div style="font-size:0.6rem;color:var(--text-muted);">Brand Fit</div></div></div>';
    if (ba.pillar_match && ba.pillar_match !== 'none') {
      h += '<div style="margin-bottom:6px;"><span class="badge" style="background:rgba(200,100,255,0.12);color:#cc66ff;font-size:0.66rem;">' + esc(ba.pillar_match.replace(/_/g, ' ')) + '</span>';
      if (ba.archetype_alignment) h += ' <span class="badge" style="background:rgba(255,255,255,0.06);font-size:0.66rem;">' + esc(ba.archetype_alignment) + '</span>';
      h += '</div>';
    }
    if (ba.content_suggestion) {
      h += '<div style="font-size:0.72rem;color:var(--text-secondary);margin-bottom:8px;line-height:1.4;">' + esc(ba.content_suggestion) + '</div>';
    }
    if (ba.reasoning) {
      h += '<div style="font-size:0.66rem;color:var(--text-muted);margin-bottom:8px;font-style:italic;">' + esc(ba.reasoning) + '</div>';
    }
    h += '<div style="display:flex;gap:8px;">';
    h += '<button class="btn" onclick="brandChannelApprove(\'' + esc(m.id) + '\')" style="background:var(--success);color:#000;font-weight:700;font-size:0.72rem;padding:5px 14px;">Approve</button>';
    h += '<button class="btn" onclick="brandChannelReject(\'' + esc(m.id) + '\')" style="background:var(--error);color:#fff;font-weight:700;font-size:0.72rem;padding:5px 14px;">Reject</button>';
    h += '</div></div>';
  }
  h += '</div>';
  el.innerHTML = h;
}

function renderBrandChannelTable(msgs) {
  var el = document.getElementById('brand-channel-tbody');
  if (!el) return;
  if (!msgs || msgs.length === 0) {
    el.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:24px;">No brand channel activity yet</td></tr>';
    return;
  }
  var statusColors = {approved:'var(--success)', rejected:'var(--error)', assessed:'var(--warning)', content_planned:'#cc66ff'};
  var statusLabels = {approved:'Approved', rejected:'Rejected', assessed:'Needs Review', content_planned:'Planned'};
  var h = '';
  for (var i = 0; i < Math.min(msgs.length, 30); i++) {
    var m = msgs[i];
    var opp = m.opportunity || {};
    var ba = m.brand_assessment || {};
    var score = ba.brand_fit_score || 0;
    var scoreColor = score >= 70 ? 'var(--success)' : score >= 40 ? 'var(--warning)' : 'var(--error)';
    var st = m.status || 'unknown';
    var stColor = statusColors[st] || 'var(--text-muted)';
    var stLabel = statusLabels[st] || st;
    var verdict = ba.auto_verdict || '';
    var verdictColor = verdict === 'auto_approved' ? 'var(--success)' : verdict === 'auto_rejected' ? 'var(--error)' : 'var(--warning)';
    h += '<tr>';
    h += '<td><span class="badge" style="background:rgba(255,255,255,0.06);color:' + stColor + ';font-size:0.66rem;">' + esc(stLabel) + '</span></td>';
    h += '<td style="max-width:220px;"><div style="font-size:0.74rem;line-height:1.3;white-space:normal;">' + esc((opp.title || '').substring(0, 60)) + '</div>';
    if (opp.url) h += '<a href="' + esc(opp.url) + '" target="_blank" style="font-size:0.58rem;color:#8B5CF6;">Link</a>';
    h += '</td>';
    h += '<td style="color:' + scoreColor + ';font-weight:700;">' + score + '</td>';
    h += '<td><span style="font-size:0.68rem;color:#cc66ff;">' + esc((ba.pillar_match || 'none').replace(/_/g, ' ')) + '</span></td>';
    h += '<td><span style="font-size:0.68rem;color:' + verdictColor + ';">' + esc((verdict || '').replace(/_/g, ' ')) + '</span></td>';
    h += '<td>';
    if (st === 'assessed') {
      h += '<button class="btn" onclick="brandChannelApprove(\'' + esc(m.id) + '\')" style="font-size:0.62rem;padding:2px 8px;background:var(--success);color:#000;">OK</button> ';
      h += '<button class="btn" onclick="brandChannelReject(\'' + esc(m.id) + '\')" style="font-size:0.62rem;padding:2px 8px;background:var(--error);color:#fff;">No</button>';
    } else if (st === 'approved') {
      h += '<button class="btn" onclick="brandChannelPlan(\'' + esc(m.id) + '\')" style="font-size:0.62rem;padding:2px 8px;background:#cc66ff;color:#000;">Plan</button>';
    } else {
      h += '<span style="font-size:0.62rem;color:var(--text-muted);">—</span>';
    }
    h += '</td></tr>';
  }
  el.innerHTML = h;
}

async function brandChannelApprove(id) {
  try {
    var resp = await fetch('/api/viper/brand-channel/' + id + '/approve', {method:'POST'});
    var d = await resp.json();
    if (d.success) loadBrandChannel();
  } catch(e) { console.error('brand approve:', e); }
}

async function brandChannelReject(id) {
  var reason = prompt('Rejection reason (optional):') || '';
  try {
    var resp = await fetch('/api/viper/brand-channel/' + id + '/reject', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({reason: reason})
    });
    var d = await resp.json();
    if (d.success) loadBrandChannel();
  } catch(e) { console.error('brand reject:', e); }
}

async function brandChannelPlan(id) {
  try {
    var resp = await fetch('/api/viper/brand-channel/' + id + '/plan', {method:'POST'});
    var d = await resp.json();
    if (d.success) loadBrandChannel();
  } catch(e) { console.error('brand plan:', e); }
}

// ══════════════════════════════════════
// MODE TOGGLE — Garves & Hawk
// ══════════════════════════════════════

async function toggleGarvesMode() {
  var btn = document.getElementById('garves-mode-toggle');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    var resp = await fetch('/api/garves/toggle-mode', {method:'POST'});
    var d = await resp.json();
    if (d.success) {
      updateGarvesModeBadge(d.dry_run);
    }
  } catch(e) { console.error('garves toggle:', e); }
  if (btn) { btn.disabled = false; btn.textContent = 'Switch'; }
}

async function toggleHawkMode() {
  var btn = document.getElementById('hawk-mode-toggle');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    var resp = await fetch('/api/hawk/toggle-mode', {method:'POST'});
    var d = await resp.json();
    if (d.success) {
      updateHawkModeBadge(d.dry_run);
    }
  } catch(e) { console.error('hawk toggle:', e); }
  if (btn) { btn.disabled = false; btn.textContent = 'Switch'; }
}

function updateGarvesModeBadge(isDryRun) {
  var badge = document.getElementById('garves-mode-badge');
  if (badge) {
    badge.classList.remove('trading-mode-live', 'trading-mode-paper');
    if (isDryRun) {
      badge.classList.add('trading-mode-paper');
      badge.textContent = 'Trading: Paper Money';
    } else {
      badge.classList.add('trading-mode-live');
      badge.textContent = 'Trading: Real Money';
    }
  }
}

function updateHawkModeBadge(isDryRun) {
  var badge = document.getElementById('hawk-mode-badge');
  if (badge) {
    badge.classList.remove('trading-mode-live', 'trading-mode-paper');
    if (isDryRun) {
      badge.classList.add('trading-mode-paper');
      badge.textContent = 'Trading: Paper Money';
    } else {
      badge.classList.add('trading-mode-live');
      badge.textContent = 'Trading: Real Money';
    }
  }
}

async function loadGarvesMode() {
  try {
    var resp = await fetch('/api/garves/mode');
    var d = await resp.json();
    updateGarvesModeBadge(d.dry_run);
  } catch(e) {}
}

async function loadMomentumMode() {
  try {
    var resp = await fetch('/api/garves/momentum-mode');
    var d = await resp.json();
    var pill = document.getElementById('momentum-pill');
    var dot = document.getElementById('momentum-dot');
    var label = document.getElementById('momentum-label');
    var countdown = document.getElementById('momentum-countdown');
    var forceBtn = document.getElementById('momentum-force-btn');
    var endBtn = document.getElementById('momentum-end-btn');
    if (!pill) return;
    if (d.active) {
      var isUp = d.direction === 'up';
      var color = isUp ? '#22c55e' : '#ef4444';
      pill.style.background = isUp ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)';
      pill.style.borderColor = color;
      pill.style.boxShadow = '0 0 12px ' + (isUp ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)');
      dot.style.background = color;
      dot.style.animation = 'gep-pulse 1.5s infinite';
      var arrow = isUp ? ' \u25B2' : ' \u25BC';
      label.textContent = 'MOMENTUM: ' + (isUp ? 'LONG' : 'SHORT') + arrow;
      label.style.color = color;
      var rem = d.remaining_s || 0;
      var h = Math.floor(rem / 3600);
      var m = Math.floor((rem % 3600) / 60);
      countdown.textContent = h + 'h ' + m + 'm remaining';
      if (forceBtn) forceBtn.style.display = 'none';
      if (endBtn) endBtn.style.display = '';
    } else {
      pill.style.background = 'rgba(255,255,255,0.06)';
      pill.style.borderColor = 'rgba(255,255,255,0.1)';
      pill.style.boxShadow = 'none';
      dot.style.background = '#6b7280';
      dot.style.animation = 'none';
      label.textContent = 'Momentum: OFF';
      label.style.color = '';
      countdown.textContent = '';
      if (forceBtn) forceBtn.style.display = '';
      if (endBtn) endBtn.style.display = 'none';
    }
  } catch(e) {}
}

function toggleMomentumForce() {
  var dir = prompt('Force momentum direction:\n\nEnter "up" or "down":', 'up');
  if (!dir || (dir !== 'up' && dir !== 'down')) return;
  var hours = prompt('Duration in hours (1-24):', '6');
  if (!hours) return;
  var h = parseFloat(hours);
  if (isNaN(h) || h < 1 || h > 24) { alert('Invalid duration'); return; }
  fetch('/api/garves/momentum-force', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({direction: dir, duration_h: h})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) { loadMomentumMode(); } else { alert('Error: ' + (d.error || 'unknown')); }
  });
}

function endMomentumMode() {
  if (!confirm('End Momentum Capture Mode?')) return;
  fetch('/api/garves/momentum-end', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: '{}'
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) { loadMomentumMode(); } else { alert('Error: ' + (d.error || 'unknown')); }
  });
}

async function loadGarvesHealthWarnings() {
  try {
    var resp = await fetch('/api/garves/health-warnings');
    var d = await resp.json();
    var banner = document.getElementById('garves-health-banner');
    if (!banner) return;
    var criticals = (d.warnings || []).filter(function(w) { return w.level === 'critical'; });
    var infos = (d.warnings || []).filter(function(w) { return w.level === 'info'; });
    if (criticals.length > 0) {
      banner.style.display = 'block';
      banner.style.background = 'rgba(255,60,60,0.15)';
      banner.style.border = '1px solid rgba(255,60,60,0.4)';
      banner.style.color = '#ff6b6b';
      banner.innerHTML = criticals.map(function(w) { return w.message; }).join('<br>');
    } else if (infos.length > 0) {
      banner.style.display = 'block';
      banner.style.background = 'rgba(80,200,120,0.12)';
      banner.style.border = '1px solid rgba(80,200,120,0.3)';
      banner.style.color = '#50c878';
      banner.innerHTML = infos.map(function(w) { return w.message; }).join('<br>');
    } else {
      banner.style.display = 'none';
    }
  } catch(e) {}
}

async function loadOverviewHealthBanner() {
  try {
    var resp = await fetch('/api/garves/health-warnings');
    var d = await resp.json();
    var banner = document.getElementById('ov-garves-health-banner');
    if (!banner) return;
    var criticals = (d.warnings || []).filter(function(w) { return w.level === 'critical'; });
    var warnings = (d.warnings || []).filter(function(w) { return w.level === 'warning'; });
    if (criticals.length > 0) {
      banner.style.display = 'block';
      banner.style.background = 'rgba(255,60,60,0.15)';
      banner.style.border = '1px solid rgba(255,60,60,0.4)';
      banner.style.color = '#ff6b6b';
      banner.innerHTML = criticals.map(function(w) { return w.message; }).join('<br>');
    } else if (warnings.length > 0) {
      banner.style.display = 'block';
      banner.style.background = 'rgba(255,180,60,0.12)';
      banner.style.border = '1px solid rgba(255,180,60,0.3)';
      banner.style.color = '#ffb43c';
      banner.innerHTML = warnings.map(function(w) { return w.message; }).join('<br>');
    } else {
      banner.style.display = 'none';
    }
  } catch(e) {}
}

var _signalCycleInterval = null;
var _signalCycleData = null;

async function loadSignalCycle() {
  try {
    var resp = await fetch('/api/garves/signal-cycle');
    _signalCycleData = await resp.json();
    var numEl = document.getElementById('signal-cycle-num');
    if (numEl) numEl.textContent = _signalCycleData.cycle_count || 0;
    renderSignalCycle();
    if (!_signalCycleInterval) {
      _signalCycleInterval = setInterval(renderSignalCycle, 1000);
    }
  } catch(e) {}
}

function renderSignalCycle() {
  var d = _signalCycleData;
  if (!d || !d.last_eval_at) return;
  var elapsed = (Date.now() / 1000) - d.last_eval_at;
  var interval = d.tick_interval_s || 5;
  var remaining = Math.max(0, interval - elapsed);
  var el = document.getElementById('signal-cycle-countdown');
  var pill = document.getElementById('signal-cycle-pill');
  var mktsEl = document.getElementById('signal-cycle-markets');
  var detailEl = document.getElementById('signal-cycle-detail');
  if (el) {
    if (remaining <= 0) {
      el.textContent = 'NOW';
      el.style.color = 'var(--success)';
      if (pill) pill.style.boxShadow = '0 0 12px rgba(34,197,94,0.4)';
    } else {
      var m = Math.floor(remaining / 60);
      var s = Math.floor(remaining % 60);
      el.textContent = m > 0 ? m + ':' + (s < 10 ? '0' : '') + s : s + 's';
      if (remaining < 2) {
        el.style.color = 'var(--warning)';
        if (pill) pill.style.boxShadow = '0 0 10px rgba(243,156,18,0.3)';
      } else {
        el.style.color = '';
        if (pill) pill.style.boxShadow = '';
      }
    }
  }
  if (mktsEl) mktsEl.textContent = (d.markets_evaluated || 0) + ' mkts';
  if (detailEl) {
    var agoText = elapsed < 60 ? Math.floor(elapsed) + 's' : Math.floor(elapsed/60) + 'm';
    detailEl.innerHTML = 'Last scan: ' + agoText + ' ago | ' + (d.markets_evaluated || 0) + ' markets | Regime: ' + (d.regime || '--');
    if (d.trades_this_tick > 0) {
      detailEl.innerHTML += ' | <span style="color:#22c55e;">' + d.trades_this_tick + ' trade(s)</span>';
    }
  }
}

(function() {
  var pill = document.getElementById('signal-cycle-pill');
  if (pill) {
    pill.addEventListener('mouseenter', function() {
      var tt = document.getElementById('signal-cycle-tooltip');
      if (tt) tt.style.display = 'block';
    });
    pill.addEventListener('mouseleave', function() {
      var tt = document.getElementById('signal-cycle-tooltip');
      if (tt) tt.style.display = 'none';
    });
  }
})();

async function loadHawkMode() {
  try {
    var resp = await fetch('/api/hawk/mode');
    var d = await resp.json();
    updateHawkModeBadge(d.dry_run);
  } catch(e) {}
}

var _hawkScInterval = null;
var _hawkScData = null;

async function loadHawkSignalCycle() {
  try {
    var resp = await fetch('/api/hawk/signal-cycle');
    _hawkScData = await resp.json();
    renderHawkSignalCycle();
    if (!_hawkScInterval) {
      _hawkScInterval = setInterval(renderHawkSignalCycle, 1000);
    }
  } catch(e) {}
}

function renderHawkSignalCycle() {
  var d = _hawkScData;
  if (!d || !d.last_eval_at) return;
  var elapsed = (Date.now() / 1000) - d.last_eval_at;
  var agoText = elapsed < 60 ? Math.floor(elapsed) + 's' : elapsed < 3600 ? Math.floor(elapsed/60) + 'm' : Math.floor(elapsed/3600) + 'h';
  var timerEl = document.getElementById('hawk-sc-timer');
  var mktsEl = document.getElementById('hawk-sc-markets');
  var dotEl = document.getElementById('hawk-sc-dot');
  var detailEl = document.getElementById('hawk-sc-detail');
  if (timerEl) timerEl.textContent = agoText;
  if (mktsEl) mktsEl.textContent = (d.markets_scanned || 0) + ' mkts';
  if (dotEl) {
    dotEl.style.color = elapsed < 2400 ? '#22c55e' : elapsed < 7200 ? '#f59e0b' : '#ef4444';
  }
  if (detailEl) {
    detailEl.innerHTML = 'Last scan: ' + agoText + ' ago | ' + (d.markets_scanned || 0) + '/' + (d.markets_eligible || 0) + ' markets | Regime: ' + (d.regime || '--');
    if (d.trades_placed > 0) {
      detailEl.innerHTML += ' | <span style="color:#22c55e;">' + d.trades_placed + ' trade(s)</span>';
    }
  }
}

async function toggleOdinMode() {
  var btn = document.getElementById('odin-mode-toggle');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    var resp = await fetch('/api/odin/toggle-mode', {method:'POST'});
    var d = await resp.json();
    if (d.success) {
      var badge = document.getElementById('odin-mode-badge');
      if (badge) {
        badge.classList.remove('trading-mode-live', 'trading-mode-paper');
        if (d.mode === 'live') {
          badge.classList.add('trading-mode-live');
          badge.textContent = 'Trading: Real Money';
        } else {
          badge.classList.add('trading-mode-paper');
          badge.textContent = 'Trading: Paper Money';
        }
      }
    }
  } catch(e) { console.error('odin toggle:', e); }
  if (btn) { btn.disabled = false; btn.textContent = 'Switch'; }
}

async function toggleOracleMode() {
  var btn = document.getElementById('oracle-mode-toggle');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    var resp = await fetch('/api/oracle/toggle-mode', {method:'POST'});
    var d = await resp.json();
    if (d.success) {
      var badge = document.getElementById('oracle-mode-badge');
      if (badge) {
        badge.classList.remove('trading-mode-live', 'trading-mode-paper');
        if (d.dry_run) {
          badge.classList.add('trading-mode-paper');
          badge.textContent = 'Trading: Paper Money';
        } else {
          badge.classList.add('trading-mode-live');
          badge.textContent = 'Trading: Real Money';
        }
      }
    }
  } catch(e) { console.error('oracle toggle:', e); }
  if (btn) { btn.disabled = false; btn.textContent = 'Switch'; }
}

// ══════════════════════════════════════
// HAWK SUGGESTIONS — Trade Approval Queue
// ══════════════════════════════════════

async function loadHawkSuggestions() {
  try {
    var resp = await fetch('/api/hawk/suggestions');
    var d = await resp.json();
    var suggestions = d.suggestions || [];
    renderHawkSuggestions(suggestions);
    var countEl = document.getElementById('hawk-sug-count');
    if (countEl) countEl.textContent = suggestions.length > 0 ? '(' + suggestions.length + ' pending)' : '';
  } catch(e) { console.error('hawk suggestions:', e); }
}

function hawkRiskColor(rs) {
  if (rs <= 3) return '#00ff44';
  if (rs <= 6) return '#FFD700';
  if (rs <= 8) return '#ff6600';
  return '#ff3333';
}

function hawkUrgencyBadge(label) {
  if (!label) return '';
  var colors = {'ENDING NOW':'#ff3333','ENDING SOON':'#ff6600','TOMORROW':'#FFD700','THIS WEEK':'#888'};
  var c = colors[label] || '#888';
  return '<span style="background:' + c + '22;color:' + c + ';padding:2px 8px;border-radius:4px;font-size:0.64rem;font-weight:700;letter-spacing:0.03em;">' + label + '</span>';
}

function hawkRiskBadge(rs) {
  var c = hawkRiskColor(rs);
  return '<span style="display:inline-flex;align-items:center;gap:4px;"><span style="width:12px;height:12px;border-radius:50%;background:' + c + ';display:inline-block;"></span><span style="font-size:0.68rem;font-weight:700;color:' + c + ';">' + rs + '/10</span></span>';
}

function renderHawkSuggestions(suggestions) {
  var el = document.getElementById('hawk-suggestions-list');
  if (!el) return;
  if (suggestions.length === 0) {
    el.innerHTML = '<div class="glass-card"><div class="text-muted" style="text-align:center;padding:24px;">No suggestions yet — trigger a scan</div></div>';
    return;
  }

  var tierColors = {HIGH:'#00ff88', MEDIUM:'#FFD700', SPECULATIVE:'#ff8844'};
  var tierBg = {HIGH:'rgba(0,255,136,0.08)', MEDIUM:'rgba(255,215,0,0.08)', SPECULATIVE:'rgba(255,136,68,0.08)'};
  var tierBorder = {HIGH:'rgba(0,255,136,0.3)', MEDIUM:'rgba(255,215,0,0.3)', SPECULATIVE:'rgba(255,136,68,0.3)'};

  // Sort by conviction score — highest first
  suggestions.sort(function(a, b) { return (b.score || 0) - (a.score || 0); });

  var html = '';
  for (var i = 0; i < suggestions.length; i++) {
    var s = suggestions[i];
    var tier = s.tier || 'SPECULATIVE';
    var tc = tierColors[tier] || '#888';
    var tbg = tierBg[tier] || 'rgba(255,255,255,0.04)';
    var tbr = tierBorder[tier] || 'rgba(255,255,255,0.1)';
    var dirColor = s.direction === 'yes' ? '#00ff88' : '#ff6666';
    var dirArrow = s.direction === 'yes' ? '\u25B2 YES' : '\u25BC NO';
    var tl = hawkCalcTimeLeft(s.end_date, 0, s.category);
    var rs = s.risk_score || 5;

    html += '<div style="background:' + tbg + ';border:1px solid ' + tbr + ';border-radius:10px;padding:16px;margin-bottom:10px;">';

    // Header row: tier badge + risk + urgency + question
    html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;">';
    html += '<div style="flex:1;">';
    html += '<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px;flex-wrap:wrap;">';
    html += '<span style="background:' + tc + ';color:#000;font-weight:800;padding:2px 10px;border-radius:4px;font-size:0.68rem;letter-spacing:0.05em;">' + tier + '</span>';
    html += hawkRiskBadge(rs);
    if (s.urgency_label) html += hawkUrgencyBadge(s.urgency_label);
    html += '<span style="font-size:0.72rem;font-weight:700;color:' + tc + ';">Score: ' + (s.score || 0) + '/100</span>';
    if (s.viper_intel_count > 0) {
      html += '<span style="background:rgba(0,255,136,0.15);color:#00ff88;padding:2px 8px;border-radius:4px;font-size:0.64rem;font-weight:600;">' + s.viper_intel_count + ' intel</span>';
    }
    html += '</div>';
    html += '<div style="font-size:0.84rem;font-weight:600;color:#fff;line-height:1.4;">' + esc(s.question) + '</div>';
    html += '</div>';
    html += '</div>';

    // Conviction meter — visual bar showing how confident Hawk is
    var convScore = s.score || 0;
    var convPct = Math.min(100, Math.max(0, convScore));
    var convLabel, convColor, convGlow;
    if (convPct >= 85) { convLabel = 'STRONG BET'; convColor = '#00ff88'; convGlow = 'rgba(0,255,136,0.4)'; }
    else if (convPct >= 70) { convLabel = 'GOOD BET'; convColor = '#FFD700'; convGlow = 'rgba(255,215,0,0.3)'; }
    else if (convPct >= 55) { convLabel = 'FAIR'; convColor = '#ff8844'; convGlow = 'rgba(255,136,68,0.2)'; }
    else { convLabel = 'WEAK'; convColor = '#ff4444'; convGlow = 'rgba(255,68,68,0.2)'; }
    html += '<div style="margin-bottom:10px;">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">';
    html += '<span style="font-size:0.68rem;font-weight:800;color:' + convColor + ';letter-spacing:0.08em;">' + convLabel + '</span>';
    html += '<span style="font-size:0.66rem;color:var(--text-muted);">Conviction ' + convPct + '/100</span>';
    html += '</div>';
    html += '<div style="height:6px;background:rgba(255,255,255,0.06);border-radius:3px;overflow:hidden;">';
    html += '<div style="width:' + convPct + '%;height:100%;background:' + convColor + ';border-radius:3px;box-shadow:0 0 8px ' + convGlow + ';transition:width 0.5s ease;"></div>';
    html += '</div>';
    html += '</div>';

    // Stats row
    html += '<div style="display:flex;gap:14px;flex-wrap:wrap;font-size:0.76rem;margin-bottom:10px;">';
    html += '<span style="color:' + dirColor + ';font-weight:700;">' + dirArrow + '</span>';
    html += '<span>Category: <b style="color:var(--text-secondary);">' + esc(s.category || 'other') + '</b></span>';
    html += '<span>Edge: <b style="color:#FFD700;">' + ((s.edge || 0) * 100).toFixed(1) + '%</b></span>';
    html += '<span>Est. Profit: <b style="color:var(--success);">+$' + (s.expected_value || 0).toFixed(2) + '</b></span>';
    html += '<span>Bet: <b style="color:#fff;">$' + (s.position_size || 0).toFixed(0) + '</b></span>';
    html += '<span>Vol: <b>$' + ((s.volume || 0) / 1000).toFixed(0) + 'k</b></span>';
    // Time left with urgency color
    var tlHrs = s.time_left_hours || 0;
    var tlText = tlHrs < 1 ? '<1h' : tlHrs < 24 ? tlHrs.toFixed(0) + 'h' : (tlHrs/24).toFixed(1) + 'd';
    var tlColor = tlHrs < 6 ? '#ff3333' : tlHrs < 24 ? '#ff6600' : tlHrs < 48 ? '#FFD700' : 'var(--text-muted)';
    html += '<span style="color:' + tlColor + ';font-weight:600;">' + tlText + ' left</span>';
    html += '</div>';

    // Reasoning
    if (s.reasoning) {
      html += '<div style="font-size:0.72rem;color:var(--text-secondary);line-height:1.5;margin-bottom:8px;padding:8px 10px;background:rgba(255,255,255,0.03);border-radius:6px;">' + esc(s.reasoning) + '</div>';
    }

    // Action buttons + Why This Trade
    html += '<div style="display:flex;gap:8px;align-items:center;">';
    html += '<button onclick="approveHawkTrade(\'' + esc(s.condition_id) + '\')" style="background:#00ff88;color:#000;font-weight:700;padding:6px 18px;border:none;border-radius:6px;cursor:pointer;font-size:0.76rem;">Approve</button>';
    html += '<button onclick="dismissHawkSuggestion(\'' + esc(s.condition_id) + '\')" style="background:rgba(255,255,255,0.08);color:var(--text-muted);font-weight:600;padding:6px 18px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;cursor:pointer;font-size:0.76rem;">Dismiss</button>';
    html += '<button onclick="showHawkWhyTrade(' + i + ')" style="background:rgba(255,215,0,0.12);color:#FFD700;font-weight:600;padding:6px 14px;border:1px solid rgba(255,215,0,0.25);border-radius:6px;cursor:pointer;font-size:0.72rem;">Why This Trade?</button>';
    html += '</div>';

    html += '</div>';
  }
  el.innerHTML = html;

  // Store for Why This Trade panel
  window._hawkSuggestions = suggestions;
}

function showHawkWhyTrade(idx) {
  var s = (window._hawkSuggestions || [])[idx];
  if (!s) return;
  var panel = document.getElementById('hawk-reasoning-panel');
  if (!panel) return;
  document.getElementById('hawk-reasoning-market').textContent = s.question || '';
  // Badges
  var badges = document.getElementById('hawk-reasoning-badges');
  var bh = '';
  bh += hawkRiskBadge(s.risk_score || 5);
  if (s.urgency_label) bh += hawkUrgencyBadge(s.urgency_label);
  bh += '<span style="font-size:0.68rem;color:var(--text-muted);">Edge: ' + ((s.edge||0)*100).toFixed(1) + '% | Conf: ' + ((s.confidence||0)*100).toFixed(0) + '%</span>';
  badges.innerHTML = bh;
  document.getElementById('hawk-reasoning-text').textContent = s.reasoning || 'No reasoning available';
  document.getElementById('hawk-reasoning-money').textContent = s.money_thesis || 'N/A';
  document.getElementById('hawk-reasoning-news').textContent = s.news_factor || 'N/A';
  document.getElementById('hawk-reasoning-edge-src').textContent = s.edge_source || 'N/A';
  panel.style.display = 'block';
  panel.scrollIntoView({behavior:'smooth', block:'nearest'});
}

async function approveHawkTrade(conditionId) {
  if (!confirm('Approve this trade for execution?')) return;
  try {
    var resp = await fetch('/api/hawk/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({condition_id: conditionId})
    });
    var d = await resp.json();
    if (d.success) {
      alert('Trade approved! Order ID: ' + (d.order_id || 'unknown') + ' (' + (d.mode || 'unknown') + ')');
      loadHawkSuggestions();
      loadHawkTab();
    } else {
      alert('Failed: ' + (d.error || 'Unknown error'));
    }
  } catch(e) { alert('Error: ' + e.message); }
}

async function dismissHawkSuggestion(conditionId) {
  try {
    var resp = await fetch('/api/hawk/dismiss', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({condition_id: conditionId})
    });
    var d = await resp.json();
    if (d.success) {
      loadHawkSuggestions();
    }
  } catch(e) { console.error('dismiss:', e); }
}

// === QUANT TAB ===
var _quantRunPolling = null;

async function loadQuantTab() {
  try {
    var resp = await fetch('/api/quant');
    var data = await resp.json();
    document.getElementById('quant-mode').textContent = data.mode || 'Historical Replay';
    document.getElementById('quant-cycle').textContent = data.cycle || '--';
    document.getElementById('quant-total-combos').textContent = data.total_combos_tested || '--';
    var bestWrEl = document.getElementById('quant-best-wr');
    if (bestWrEl) {
      bestWrEl.textContent = data.best_win_rate ? data.best_win_rate + '%' : '--';
      bestWrEl.style.color = wrColor(data.best_win_rate || 0);
      bestWrEl.style.background = wrColor(data.best_win_rate || 0) + '18';
      bestWrEl.style.padding = '2px 8px';
      bestWrEl.style.borderRadius = '999px';
    }
    document.getElementById('quant-baseline-wr').textContent = data.baseline_win_rate ? data.baseline_win_rate + '%' : '--';
    document.getElementById('quant-baseline-wr').style.color = wrColor(data.baseline_win_rate || 0);
    document.getElementById('quant-trades').textContent = data.trade_count || '--';
    document.getElementById('quant-trade-count').textContent = data.trade_count || '--';
    var sigSum = '';
    if (data.baseline_signals && data.best_signals) {
      sigSum = '(' + data.baseline_signals + ' sig, best ' + data.best_signals + ')';
    } else if (data.baseline_signals) {
      sigSum = '(' + data.baseline_signals + ' sig)';
    }
    document.getElementById('quant-signal-summary').textContent = sigSum;
    document.getElementById('quant-avg-edge').textContent = data.baseline_avg_edge ? data.baseline_avg_edge + '%' : '--';
    document.getElementById('quant-last-update').textContent = data.last_run || '--';
    var fr = data.filter_reasons || {};
    var totalFiltered = 0;
    for (var k in fr) totalFiltered += fr[k];
    document.getElementById('quant-filtered').textContent = totalFiltered || '--';

    // Update status pill
    var dot = document.querySelector('.qt-status-dot');
    if (dot) {
      dot.style.background = data.running ? 'var(--agent-quant)' : (data.total_combos_tested > 0 ? 'var(--success)' : 'var(--text-muted)');
    }

    // Strategy Verdict Banner
    var banner = document.getElementById('quant-verdict-banner');
    var currentWR = data.baseline_win_rate || 0;
    var bestWR = data.best_win_rate || 0;
    if (currentWR || bestWR) {
      banner.style.display = 'block';
      var delta = bestWR - currentWR;
      var bannerColor = currentWR >= 60 ? 'var(--success)' : currentWR >= 50 ? 'var(--warning)' : 'var(--error)';
      banner.style.borderColor = bannerColor;
      document.getElementById('quant-verdict-current').textContent = currentWR + '%';
      document.getElementById('quant-verdict-current').style.color = wrColor(currentWR);
      document.getElementById('quant-verdict-best').textContent = bestWR + '%';
      document.getElementById('quant-verdict-best').style.color = wrColor(bestWR);
      var deltaEl = document.getElementById('quant-verdict-delta');
      deltaEl.textContent = (delta > 0 ? '+' : '') + delta.toFixed(1) + 'pp';
      deltaEl.style.background = delta > 0 ? 'rgba(34,170,68,0.13)' : 'rgba(255,85,85,0.13)';
      deltaEl.style.color = delta > 0 ? 'var(--success)' : 'var(--error)';
      var verdictText = '';
      if (currentWR >= 60) verdictText = 'Strategy is performing well. Current parameters are near optimal.';
      else if (currentWR >= 50) verdictText = 'Strategy is profitable but has room to improve. Consider applying recommended changes.';
      else verdictText = 'Strategy is underperforming. Parameter optimization strongly recommended.';
      if (delta > 5) verdictText += ' Backtest found +' + delta.toFixed(1) + 'pp improvement available.';
      document.getElementById('quant-verdict-text').textContent = verdictText;
    }

    // Store data for radar (will be rendered after analytics loads)
    window._quantStatusData = data;
  } catch(e) { console.error('quant status:', e); }

  loadQuantResults();
  loadQuantRecommendations();
  loadQuantParams();
  loadQuantHawkReview();
  loadQuantWalkForward();
  loadQuantAnalytics();
  loadQuantLiveParams();
  loadQuantTradeLearning();
  loadQuantPhase1();
  loadQuantPhase2();
  loadQuantPNLImpact();
  loadQuantSmartActions();
  loadQuantOdinBacktest();
}

async function loadQuantResults() {
  try {
    var resp = await fetch('/api/quant/results');
    var data = await resp.json();
    renderQuantResults(data.top_results || []);
    renderQuantSensitivity(data.sensitivity || {});
  } catch(e) { console.error('quant results:', e); }
}

function renderQuantResults(results) {
  var tbody = document.getElementById('quant-results-tbody');
  if (!results || results.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-muted" style="text-align:center;padding:20px;">No results yet</td></tr>';
    return;
  }
  var html = '';
  var max = Math.min(results.length, 10);
  for (var i = 0; i < max; i++) {
    var r = results[i];
    html += '<tr>';
    html += '<td>' + r.rank + '</td>';
    html += '<td style="font-size:0.68rem;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(r.label) + '">' + esc(r.label) + '</td>';
    html += '<td style="color:#00BFFF;">' + r.score + '</td>';
    html += '<td style="color:' + wrColor(r.win_rate) + ';">' + r.win_rate + '%</td>';
    html += '<td>' + r.profit_factor + '</td>';
    html += '<td>' + r.total_signals + '</td>';
    html += '<td>' + r.wins + '/' + r.losses + '</td>';
    html += '<td>' + (r.avg_edge || 0) + '%</td>';
    html += '</tr>';
  }
  tbody.innerHTML = html;
}

function renderQuantSensitivity(sensitivity) {
  var cTbody = document.getElementById('quant-consensus-tbody');
  var eTbody = document.getElementById('quant-edge-tbody');
  var consensus = sensitivity.consensus || {};
  var edge = sensitivity.edge || {};

  var cKeys = Object.keys(consensus).sort();
  if (cKeys.length === 0) {
    cTbody.innerHTML = '<tr><td colspan="4" class="text-muted" style="text-align:center;">--</td></tr>';
  } else {
    var html = '';
    for (var i = 0; i < cKeys.length; i++) {
      var k = cKeys[i];
      var v = consensus[k];
      html += '<tr><td>' + k + '</td>';
      html += '<td style="color:' + wrColor(v.avg_win_rate || 0) + ';">' + (v.avg_win_rate || 0) + '%</td>';
      html += '<td>' + (v.avg_signals || 0) + '</td>';
      html += '<td>' + (v.count || 0) + '</td></tr>';
    }
    cTbody.innerHTML = html;
  }

  var eKeys = Object.keys(edge).sort();
  if (eKeys.length === 0) {
    eTbody.innerHTML = '<tr><td colspan="4" class="text-muted" style="text-align:center;">--</td></tr>';
  } else {
    var html = '';
    for (var i = 0; i < eKeys.length; i++) {
      var k = eKeys[i];
      var v = edge[k];
      html += '<tr><td>' + (parseFloat(k)*100).toFixed(0) + '%</td>';
      html += '<td style="color:' + wrColor(v.avg_win_rate || 0) + ';">' + (v.avg_win_rate || 0) + '%</td>';
      html += '<td>' + (v.avg_signals || 0) + '</td>';
      html += '<td>' + (v.count || 0) + '</td></tr>';
    }
    eTbody.innerHTML = html;
  }
}

async function loadQuantRecommendations() {
  try {
    var resp = await fetch('/api/quant/recommendations');
    var data = await resp.json();
    renderQuantRecommendations(data.recommendations || []);
  } catch(e) { console.error('quant recs:', e); }
}

function renderQuantRecommendations(recs) {
  var el = document.getElementById('quant-recommendations');
  if (!recs || recs.length === 0) {
    el.innerHTML = '<div class="text-muted" style="text-align:center;padding:8px;font-size:0.72rem;">No recommendations</div>';
    return;
  }
  var html = '<div style="display:flex;flex-wrap:wrap;gap:6px;padding:4px 0;">';
  for (var i = 0; i < recs.length; i++) {
    var r = recs[i];
    var confColor = r.confidence === 'high' ? 'var(--success)' : r.confidence === 'medium' ? 'var(--warning)' : 'var(--text-muted)';
    html += '<div style="display:inline-flex;align-items:center;gap:6px;padding:5px 10px;background:rgba(0,191,255,0.06);border:1px solid rgba(0,191,255,0.12);border-radius:6px;font-size:0.68rem;">';
    html += '<span style="color:#00BFFF;font-weight:600;">' + esc(r.param) + '</span>';
    html += '<span class="text-muted">' + esc(r.current) + ' &rarr; ' + esc(r.suggested) + '</span>';
    html += '<span style="padding:1px 6px;border-radius:3px;font-size:0.58rem;font-weight:600;background:' + confColor + '22;color:' + confColor + ';">' + esc(r.confidence) + '</span>';
    html += '</div>';
  }
  html += '</div>';
  el.innerHTML = html;
}

async function loadQuantParams() {
  try {
    var resp = await fetch('/api/quant/params');
    var data = await resp.json();
    renderQuantParams(data);
  } catch(e) { console.error('quant params:', e); }
}

function renderQuantParams(data) {
  var tbody = document.getElementById('quant-params-tbody');
  var current = data.current || {};
  var best = data.best || {};
  if (!current.min_consensus && !best.min_consensus) {
    tbody.innerHTML = '<tr><td colspan="4" class="text-muted" style="text-align:center;padding:20px;">Run a backtest to see comparison</td></tr>';
    return;
  }
  var params = [
    {name: 'Consensus', key: 'min_consensus'},
    {name: 'Confidence', key: 'min_confidence'},
    {name: 'UP Premium', key: 'up_confidence_premium'},
    {name: 'Min Edge', key: 'min_edge_absolute'},
  ];
  function cleanVal(v) {
    if (v === undefined || v === null) return '--';
    var s = String(v);
    // Strip verbose parts like "70% (floor=3)" → just the number
    var m = s.match(/^([\d.]+)/);
    return m ? m[1] : s;
  }
  var html = '';
  for (var i = 0; i < params.length; i++) {
    var p = params[i];
    var cv = cleanVal(current[p.key]);
    var bv = cleanVal(best[p.key]);
    var match = cv === bv;
    var statusBadge = match
      ? '<span style="padding:2px 8px;border-radius:4px;font-size:0.62rem;font-weight:600;background:rgba(34,170,68,0.13);color:var(--success);">APPLIED</span>'
      : '<span style="padding:2px 8px;border-radius:4px;font-size:0.62rem;font-weight:600;background:rgba(255,170,0,0.13);color:var(--warning);">PENDING</span>';
    html += '<tr>';
    html += '<td style="font-size:0.74rem;">' + p.name + '</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.74rem;">' + cv + '</td>';
    html += '<td style="font-family:var(--font-mono);font-size:0.74rem;color:#00BFFF;">' + bv + '</td>';
    html += '<td>' + statusBadge + '</td>';
    html += '</tr>';
  }
  // Performance row
  var cp = data.current_performance || {};
  var bp = data.best_performance || {};
  var wrDelta = (bp.win_rate || 0) - (cp.win_rate || 0);
  html += '<tr style="border-top:2px solid var(--border);">';
  html += '<td style="font-weight:600;font-size:0.74rem;">Win Rate</td>';
  html += '<td style="font-family:var(--font-mono);font-size:0.74rem;color:' + wrColor(cp.win_rate || 0) + ';">' + (cp.win_rate || '--') + '%</td>';
  html += '<td style="font-family:var(--font-mono);font-size:0.74rem;color:' + wrColor(bp.win_rate || 0) + ';">' + (bp.win_rate || '--') + '%</td>';
  html += '<td style="font-family:var(--font-mono);font-size:0.74rem;font-weight:600;color:' + (wrDelta > 0 ? 'var(--success)' : wrDelta < 0 ? 'var(--error)' : 'var(--text-muted)') + ';">' + (wrDelta > 0 ? '+' : '') + wrDelta.toFixed(1) + 'pp</td>';
  html += '</tr>';
  tbody.innerHTML = html;
}

function loadQuantHawkReview() {
  var el = document.getElementById('quant-hawk-review');
  if (el) el.innerHTML = '<div class="text-muted" style="text-align:center;padding:12px;font-size:0.72rem;">Hawk trade calibration data will appear after running a backtest cycle</div>';
}

async function loadQuantWalkForward() {
  try {
    var resp = await fetch('/api/quant/walk-forward');
    var data = await resp.json();
    var wf = data.walk_forward || {};
    var ci = data.confidence_interval || {};

    // Update stat cards
    if (ci.ci_lower !== undefined) {
      document.getElementById('quant-ci').textContent = '[' + ci.ci_lower + '%, ' + ci.ci_upper + '%]';
    }
    if (wf.test_win_rate !== undefined) {
      document.getElementById('quant-wf-oos').textContent = wf.test_win_rate + '%';
      document.getElementById('quant-wf-oos').style.color = wrColor(wf.test_win_rate);
    }
    if (wf.overfit_drop !== undefined) {
      var od = wf.overfit_drop;
      document.getElementById('quant-overfit').textContent = od + 'pp';
      document.getElementById('quant-overfit').style.color = od > 15 ? 'var(--error)' : od > 5 ? 'var(--warning)' : 'var(--success)';
    }

    // Walk-forward fold table
    var folds = wf.fold_results || [];
    var tbody = document.getElementById('quant-wf-tbody');
    if (folds.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:20px;">Run a backtest to see walk-forward results</td></tr>';
      return;
    }
    var html = '';
    for (var i = 0; i < folds.length; i++) {
      var f = folds[i];
      var foldDrop = f.train_wr - f.test_wr;
      var foldStatus, foldColor;
      if (f.test_wr > f.train_wr + 5) { foldStatus = 'LUCKY'; foldColor = 'var(--warning)'; }
      else if (foldDrop > 15) { foldStatus = 'OVERFIT'; foldColor = 'var(--error)'; }
      else { foldStatus = 'PASS'; foldColor = 'var(--success)'; }
      html += '<tr>';
      html += '<td>Fold ' + f.fold + '</td>';
      html += '<td>' + f.train_size + ' (' + f.train_signals + ' sig)</td>';
      html += '<td style="color:' + wrColor(f.train_wr) + ';">' + f.train_wr + '%</td>';
      html += '<td>' + f.test_size + ' (' + f.test_signals + ' sig)</td>';
      html += '<td style="color:' + wrColor(f.test_wr) + ';">' + f.test_wr + '%</td>';
      html += '<td><span style="padding:2px 8px;border-radius:4px;font-size:0.65rem;font-weight:600;background:' + foldColor + '22;color:' + foldColor + ';">' + foldStatus + '</span></td>';
      html += '</tr>';
    }
    // Summary row
    html += '<tr style="border-top:2px solid var(--border);font-weight:600;">';
    html += '<td>Average</td>';
    html += '<td>--</td>';
    html += '<td style="color:' + wrColor(wf.train_win_rate) + ';">' + wf.train_win_rate + '%</td>';
    html += '<td>--</td>';
    html += '<td style="color:' + wrColor(wf.test_win_rate) + ';">' + wf.test_win_rate + '%</td>';
    html += '<td>--</td>';
    html += '</tr>';
    tbody.innerHTML = html;
  } catch(e) { console.error('quant walk-forward:', e); }
}

async function loadQuantAnalytics() {
  try {
    var resp = await fetch('/api/quant/analytics');
    var data = await resp.json();
    var kelly = data.kelly || {};
    var diversity = data.diversity || {};
    var decay = data.decay || {};

    // Stat cards
    if (kelly.half_kelly_pct !== undefined) {
      document.getElementById('quant-kelly').textContent = '$' + kelly.recommended_usd + ' (' + kelly.half_kelly_pct + '%)';
    }
    if (diversity.diversity_score !== undefined) {
      var ds = diversity.diversity_score;
      document.getElementById('quant-diversity').textContent = ds + '/100';
      document.getElementById('quant-diversity').style.color = ds >= 70 ? 'var(--success)' : ds >= 40 ? 'var(--warning)' : 'var(--error)';
    }
    if (decay.trend_direction !== undefined) {
      var td = decay.trend_direction;
      var decayEl = document.getElementById('quant-decay-status');
      decayEl.textContent = td.charAt(0).toUpperCase() + td.slice(1);
      decayEl.style.color = td === 'improving' ? 'var(--success)' : td === 'stable' ? 'var(--warning)' : 'var(--error)';
    }
    document.getElementById('quant-optimizer').textContent = 'Optuna';

    // Analytics detail panel (Kelly + Decay)
    var detailEl = document.getElementById('quant-analytics-detail');
    var html = '';

    // Kelly section
    html += '<div style="margin-bottom:16px;">';
    html += '<div style="font-weight:600;font-size:0.74rem;color:#00BFFF;margin-bottom:8px;">Kelly Position Sizing</div>';
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:0.7rem;">';
    html += '<div class="text-muted">Full Kelly:</div><div>' + (kelly.full_kelly_pct || 0) + '% ($' + ((kelly.full_kelly_pct || 0) * (kelly.bankroll || 250) / 100).toFixed(0) + ')</div>';
    html += '<div class="text-muted">Half Kelly (recommended):</div><div style="color:var(--success);font-weight:600;">$' + (kelly.recommended_usd || 0) + '</div>';
    html += '<div class="text-muted">Quarter Kelly (conservative):</div><div>$' + ((kelly.quarter_kelly_pct || 0) * (kelly.bankroll || 250) / 100).toFixed(0) + '</div>';
    html += '<div class="text-muted">Current trade size:</div><div>$' + (kelly.current_size_usd || 10) + '</div>';
    html += '</div></div>';

    // Decay section
    html += '<div>';
    html += '<div style="font-weight:600;font-size:0.74rem;color:' + (decay.is_decaying ? 'var(--error)' : 'var(--success)') + ';margin-bottom:8px;">Strategy Health</div>';
    html += '<div style="font-size:0.7rem;">';
    html += '<div style="margin-bottom:4px;"><span class="text-muted">Rolling WR:</span> ' + (decay.current_rolling_wr || 0) + '% (peak: ' + (decay.peak_rolling_wr || 0) + '%)</div>';
    html += '<div style="margin-bottom:4px;"><span class="text-muted">Alert:</span> <span style="color:' + (decay.is_decaying ? 'var(--error)' : 'var(--text-primary)') + ';">' + esc(decay.alert_message || 'No data') + '</span></div>';
    html += '</div></div>';

    detailEl.innerHTML = html;

    // Diversity detail
    var divEl = document.getElementById('quant-diversity-detail');
    var dhtml = '';
    dhtml += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">';

    // Redundant pairs
    var pairs = diversity.redundant_pairs || [];
    dhtml += '<div>';
    dhtml += '<div style="font-weight:600;font-size:0.74rem;color:var(--error);margin-bottom:8px;">Redundant Pairs (' + pairs.length + ')</div>';
    if (pairs.length === 0) {
      dhtml += '<div class="text-muted" style="font-size:0.7rem;">No redundant pairs detected</div>';
    } else {
      for (var i = 0; i < Math.min(pairs.length, 8); i++) {
        var p = pairs[i];
        dhtml += '<div style="font-size:0.68rem;padding:3px 0;border-bottom:1px solid var(--border);">';
        dhtml += '<span style="color:var(--error);">' + esc(p.indicator_a) + '</span>';
        dhtml += ' <span class="text-muted">&harr;</span> ';
        dhtml += '<span style="color:var(--error);">' + esc(p.indicator_b) + '</span>';
        dhtml += '<span style="float:right;color:var(--warning);">' + p.agreement + '%</span>';
        dhtml += '</div>';
      }
    }
    dhtml += '</div>';

    // Independent indicators
    var indep = diversity.independent_indicators || [];
    dhtml += '<div>';
    dhtml += '<div style="font-weight:600;font-size:0.74rem;color:var(--success);margin-bottom:8px;">Independent (' + indep.length + ')</div>';
    dhtml += '<div style="font-size:0.7rem;color:var(--text-secondary);">Avg pairwise agreement: ' + (diversity.avg_agreement || 0) + '%</div>';
    if (indep.length > 0) {
      dhtml += '<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px;">';
      for (var i = 0; i < indep.length; i++) {
        dhtml += '<span class="badge" style="background:var(--success)22;color:var(--success);font-size:0.62rem;">' + esc(indep[i]) + '</span>';
      }
      dhtml += '</div>';
    }
    dhtml += '</div>';

    dhtml += '</div>';
    divEl.innerHTML = dhtml;

    // Update diversity badge in panel
    var divBadge = document.getElementById('quant-diversity-badge');
    if (divBadge && diversity.diversity_score !== undefined) {
      divBadge.textContent = diversity.diversity_score + '/100';
      var dc = diversity.diversity_score >= 70 ? 'var(--success)' : diversity.diversity_score >= 40 ? 'var(--warning)' : 'var(--error)';
      divBadge.style.background = dc + '18';
      divBadge.style.color = dc;
    }

    // Render radar chart
    renderQuantRadar(data);

  } catch(e) { console.error('quant analytics:', e); }
}

async function loadQuantLiveParams() {
  try {
    var resp = await fetch('/api/quant/live-params');
    var data = await resp.json();
    var card = document.getElementById('quant-live-params-card');
    if (!data.active) {
      card.style.display = 'none';
      return;
    }
    card.style.display = 'block';
    var v = data.validation || {};
    var params = data.params || {};
    var badge = document.getElementById('quant-lp-badge');
    badge.textContent = 'AUTO-PILOT';
    badge.style.background = '#00BFFF22';
    badge.style.color = '#00BFFF';
    var summary = 'Quant auto-applied params: WR ' + (v.baseline_wr||0) + '% \u2192 ' + (v.best_wr||0) + '% (+' + (v.improvement_pp||0) + 'pp)';
    document.getElementById('quant-lp-summary').textContent = summary;
    document.getElementById('quant-lp-detail').textContent = 'OOS ' + (v.wf_oos_wr||0) + '% | Overfit ' + (v.overfit_drop||0) + 'pp | ' + (v.trades_analyzed||0) + ' trades | Applied ' + (data.applied_at||'--');
    var paramsEl = document.getElementById('quant-lp-params');
    var phtml = '';
    var paramLabels = {min_confidence:'Confidence',up_confidence_premium:'UP Premium',min_edge_absolute:'Edge Floor',consensus_floor:'Consensus'};
    for (var k in params) {
      var label = paramLabels[k] || k;
      var val = typeof params[k] === 'number' ? (params[k] < 1 ? (params[k]*100).toFixed(0) + '%' : params[k]) : params[k];
      phtml += '<span style="display:inline-flex;align-items:center;gap:4px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:3px 8px;font-size:0.65rem;">';
      phtml += '<span class="text-muted">' + esc(label) + ':</span>';
      phtml += '<span style="color:#00BFFF;font-weight:600;">' + val + '</span>';
      phtml += '</span>';
    }
    paramsEl.innerHTML = phtml;
  } catch(e) { console.error('quant live-params:', e); }
}

async function quantTriggerRun() {
  var btn = document.getElementById('quant-run-btn');
  btn.disabled = true;
  btn.textContent = 'Running...';
  document.getElementById('quant-progress-bar').style.display = 'block';

  try {
    var resp = await fetch('/api/quant/run', {method: 'POST'});
    var d = await resp.json();
    if (!d.success) {
      alert(d.message || 'Failed to start backtest');
      btn.disabled = false;
      btn.textContent = 'Trigger Backtest';
      return;
    }
    // Start polling
    _quantRunPolling = setInterval(quantPollProgress, 1500);
  } catch(e) {
    alert('Error: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'Trigger Backtest';
  }
}

async function quantPollProgress() {
  try {
    var resp = await fetch('/api/quant/run-status');
    var d = await resp.json();
    document.getElementById('quant-progress-label').textContent = d.step || 'Running...';
    document.getElementById('quant-progress-pct').textContent = (d.pct || 0) + '%';
    document.getElementById('quant-progress-fill').style.width = (d.pct || 0) + '%';
    document.getElementById('quant-progress-detail').textContent = d.detail || '';

    if (d.done) {
      clearInterval(_quantRunPolling);
      _quantRunPolling = null;
      document.getElementById('quant-run-btn').disabled = false;
      document.getElementById('quant-run-btn').textContent = 'Trigger Backtest';
      // Reload all data
      setTimeout(function() {
        loadQuantTab();
        document.getElementById('quant-progress-bar').style.display = 'none';
      }, 1000);
    }
  } catch(e) { console.error('quant poll:', e); }
}

async function loadQuantTradeLearning() {
  var card = document.getElementById('quant-learning-card');
  if (!card) return;
  try {
    var resp = await fetch('/api/quant/trade-learning');
    var d = await resp.json();
    if (!d.total_studied) return;
    // Open panel if data exists
    if (!card.classList.contains('open')) card.classList.add('open');
    document.getElementById('quant-learn-total').textContent = d.total_studied + ' studied';
    document.getElementById('quant-learn-studied').textContent = d.total_studied;
    var fc = d.filter_correctness || 0;
    var fcEl = document.getElementById('quant-learn-filter');
    fcEl.textContent = fc + '%';
    fcEl.style.color = fc >= 60 ? 'var(--success)' : fc >= 45 ? 'var(--warning)' : 'var(--error)';
    var ia = d.avg_indicator_accuracy || 0;
    var iaEl = document.getElementById('quant-learn-ind-acc');
    iaEl.textContent = ia + '%';
    iaEl.style.color = ia >= 55 ? 'var(--success)' : ia >= 45 ? 'var(--warning)' : 'var(--error)';
    document.getElementById('quant-learn-mini-opts').textContent = d.mini_opt ? (d.mini_opt.mini_opt_number || 0) : '0';

    // Indicator accuracy chips
    var chips = d.indicator_chips || [];
    var chipHtml = '';
    chips.forEach(function(c) {
      var color = c.accuracy >= 55 ? '#22aa44' : c.accuracy >= 45 ? '#FFD700' : '#ff5555';
      chipHtml += '<span style="display:inline-block;font-size:0.62rem;padding:2px 7px;border-radius:4px;background:' + color + '18;color:' + color + ';border:1px solid ' + color + '33;">';
      chipHtml += c.name + ' ' + c.accuracy + '% <span style="opacity:0.6;">(' + c.votes + ')</span></span>';
    });
    document.getElementById('quant-learn-indicators').innerHTML = chipHtml || '<span class="text-muted" style="font-size:0.65rem;">No indicator data</span>';

    // Mini-opt summary
    var moDiv = document.getElementById('quant-learn-mini-opt-summary');
    if (d.mini_opt && d.mini_opt.baseline_wr) {
      moDiv.style.display = 'block';
      var imp = d.mini_opt.improvement_pp || 0;
      var impColor = imp > 0 ? 'var(--success)' : 'var(--error)';
      moDiv.innerHTML = 'Latest mini-opt #' + (d.mini_opt.mini_opt_number || '?') + ': baseline ' + d.mini_opt.baseline_wr + '% &rarr; best ' + d.mini_opt.best_wr + '% (<span style="color:' + impColor + ';">' + (imp > 0 ? '+' : '') + imp + 'pp</span>) on ' + d.mini_opt.trades_used + ' trades';
    } else {
      moDiv.style.display = 'none';
    }

    // Recent studies feed
    var recent = d.recent_studies || [];
    var rEl = document.getElementById('quant-learn-recent');
    if (!recent.length) {
      rEl.innerHTML = '<div class="text-muted" style="font-size:0.7rem;text-align:center;">No studies yet</div>';
    } else {
      var rHtml = '';
      recent.forEach(function(s) {
        var icon = s.won ? '<span style="color:var(--success);">W</span>' : '<span style="color:var(--error);">L</span>';
        var filterIcon = s.correctly_filtered ? '<span style="color:var(--success);" title="Filter correct">&#10003;</span>' : '<span style="color:var(--error);" title="Filter missed">&#10007;</span>';
        rHtml += '<div style="display:flex;gap:8px;align-items:center;padding:3px 0;border-bottom:1px solid var(--border);font-size:0.68rem;">';
        rHtml += '<span style="width:16px;text-align:center;font-weight:600;">' + icon + '</span>';
        rHtml += '<span style="color:var(--text-muted);min-width:80px;">' + (s.asset || '').toUpperCase() + '/' + (s.timeframe || '') + '</span>';
        rHtml += '<span style="min-width:28px;">' + (s.direction || '').toUpperCase() + '</span>';
        rHtml += '<span style="color:#00BFFF;min-width:50px;">ind:' + (s.indicator_accuracy * 100).toFixed(0) + '%</span>';
        rHtml += '<span style="min-width:16px;">' + filterIcon + '</span>';
        rHtml += '<span class="text-muted" style="font-size:0.6rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (s.trade_id || '').substring(0, 16) + '</span>';
        rHtml += '</div>';
      });
      rEl.innerHTML = rHtml;
    }
  } catch(e) {
    console.error('quant trade learning:', e);
  }
}

async function loadQuantSmartActions() {
  var el = document.getElementById('quant-smart-actions');
  if (!el) return;
  try {
    var resp = await fetch('/api/quant/smart-actions');
    var d = await resp.json();
    var actions = d.actions || [];
    if (!actions.length) {
      el.innerHTML = '<span class="text-muted" style="font-size:0.76rem;">No suggestions right now.</span>';
      return;
    }
    var html = '';
    actions.forEach(function(a) {
      var color = a.priority === 'high' ? '#ff5555' : a.priority === 'medium' ? '#FFD700' : '#00BFFF';
      html += '<div style="background:rgba(0,191,255,0.06);border:1px solid rgba(0,191,255,0.15);border-radius:8px;padding:10px 14px;flex:1 1 280px;min-width:260px;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">';
      html += '<span style="font-weight:600;font-size:0.78rem;color:' + color + ';">' + (a.title || '') + '</span>';
      html += '<span style="font-size:0.62rem;padding:2px 6px;border-radius:3px;background:' + color + '22;color:' + color + ';">' + (a.priority || 'low') + '</span>';
      html += '</div>';
      html += '<div class="text-muted" style="font-size:0.7rem;line-height:1.4;">' + (a.description || '') + '</div>';
      html += '</div>';
    });
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<span class="text-muted">Failed to load</span>';
  }
}

async function loadQuantOdinBacktest() {
  try {
    var resp = await fetch('/api/quant/odin-backtest');
    var d = await resp.json();
    var ov = d.overview || {};
    var pnl = d.pnl || {};
    var rm = d.r_multiples || {};
    var st = d.streaks || {};
    var tm = d.timing || {};

    if (!ov.total_trades) {
      var badge = document.getElementById('qt-odin-bt-badge');
      if (badge) badge.textContent = 'No Data';
      return;
    }

    function s(id, val) { var e = document.getElementById(id); if (e) e.textContent = val; }
    function pnlColor(v) { return v >= 0 ? 'var(--success)' : 'var(--error)'; }

    s('qt-odin-bt-trades', ov.total_trades);
    var wrEl = document.getElementById('qt-odin-bt-wr');
    if (wrEl) { wrEl.textContent = ov.win_rate.toFixed(1) + '%'; wrEl.style.color = ov.win_rate >= 50 ? 'var(--success)' : 'var(--error)'; }
    var pnlEl = document.getElementById('qt-odin-bt-pnl');
    if (pnlEl) { pnlEl.textContent = '$' + ov.total_pnl.toFixed(2); pnlEl.style.color = pnlColor(ov.total_pnl); }
    s('qt-odin-bt-sharpe', ov.sharpe_ratio.toFixed(2));
    var ddEl = document.getElementById('qt-odin-bt-dd');
    if (ddEl) { ddEl.textContent = ov.max_drawdown_pct.toFixed(1) + '%'; ddEl.style.color = ov.max_drawdown_pct > 15 ? 'var(--error)' : 'var(--warning)'; }
    s('qt-odin-bt-pf', ov.profit_factor.toFixed(2));

    s('qt-odin-bt-avgr', (rm.avg_r || 0).toFixed(2) + 'R');
    s('qt-odin-bt-avgwinr', '+' + (rm.avg_win_r || 0).toFixed(2) + 'R');
    s('qt-odin-bt-avglossr', (rm.avg_loss_r || 0).toFixed(2) + 'R');
    s('qt-odin-bt-maxw', st.max_consecutive_wins || 0);
    s('qt-odin-bt-maxl', st.max_consecutive_losses || 0);

    s('qt-odin-bt-avgwin', '$' + (pnl.avg_win || 0).toFixed(2));
    s('qt-odin-bt-avgloss', '$' + (pnl.avg_loss || 0).toFixed(2));
    s('qt-odin-bt-bigwin', '$' + (pnl.largest_win || 0).toFixed(2));
    s('qt-odin-bt-bigloss', '$' + (pnl.largest_loss || 0).toFixed(2));
    s('qt-odin-bt-hold', (tm.avg_hold_hours || 0).toFixed(1) + 'h');

    // Exit analysis
    var exitEl = document.getElementById('qt-odin-bt-exits');
    if (exitEl && d.exit_analysis) {
      var eh = '';
      Object.keys(d.exit_analysis).forEach(function(reason) {
        var ex = d.exit_analysis[reason];
        var wr = ex.count > 0 ? (ex.wins / ex.count * 100).toFixed(0) : '0';
        eh += '<div class="qt-phase-lbl">' + reason + ':</div>';
        eh += '<div>' + ex.count + ' (' + wr + '% W, $' + ex.pnl.toFixed(2) + ')</div>';
      });
      exitEl.innerHTML = eh || '<div class="qt-placeholder">--</div>';
    }

    // Breakdowns
    function renderBreakdown(elId, data) {
      var el = document.getElementById(elId);
      if (!el || !data) return;
      var h = '';
      Object.keys(data).forEach(function(k) {
        var b = data[k];
        h += '<div class="qt-phase-lbl">' + k + ':</div>';
        h += '<div>' + b.trades + 'T, ' + b.win_rate + '% WR, $' + b.pnl.toFixed(2) + '</div>';
      });
      el.innerHTML = h || '<div class="qt-placeholder">--</div>';
    }
    renderBreakdown('qt-odin-bt-dir', d.by_direction);
    renderBreakdown('qt-odin-bt-regime', d.by_regime);
    renderBreakdown('qt-odin-bt-symbol', d.by_symbol);

    // Badge
    var badge = document.getElementById('qt-odin-bt-badge');
    if (badge) {
      badge.textContent = ov.win_rate.toFixed(0) + '% WR | $' + ov.total_pnl.toFixed(0);
      badge.style.color = ov.total_pnl >= 0 ? 'var(--success)' : 'var(--error)';
    }

    // Meta
    var meta = d.meta || {};
    var metaEl = document.getElementById('qt-odin-bt-meta');
    if (metaEl) {
      metaEl.textContent = 'Symbols: ' + (meta.symbols_tested || []).join(', ') +
        ' | Candles: ' + (meta.total_candles || 0).toLocaleString() +
        ' | Signals: ' + (meta.signals_generated || 0) +
        ' (filtered: ' + (meta.signals_filtered || 0) + ')' +
        ' | Time: ' + (meta.elapsed_seconds || 0).toFixed(1) + 's';
    }
  } catch(e) { console.error('odin backtest:', e); }
}

async function loadQuantPhase1() {
  try {
    var resp = await fetch('/api/quant/phase1');
    var d = await resp.json();
    var wfv2 = d.walk_forward_v2 || {};
    var mc = d.monte_carlo || {};
    var cusum = d.cusum || {};

    // WFV2
    var wfBadge = document.getElementById('quant-wfv2-badge');
    if (wfv2.passed) {
      wfBadge.textContent = 'PASS';
      wfBadge.style.background = 'rgba(34,170,68,0.13)';
      wfBadge.style.color = 'var(--success)';
    } else if (wfv2.rejection_reason && wfv2.rejection_reason !== 'No data yet') {
      wfBadge.textContent = 'FAIL';
      wfBadge.style.background = 'rgba(255,85,85,0.13)';
      wfBadge.style.color = 'var(--error)';
    }
    var gap = wfv2.overfit_gap;
    if (gap !== undefined) {
      var gapEl = document.getElementById('quant-wfv2-gap');
      gapEl.textContent = gap.toFixed(1) + 'pp';
      gapEl.style.color = gap > 10 ? 'var(--error)' : gap > 5 ? 'var(--warning)' : 'var(--success)';
    }
    if (wfv2.stability_score !== undefined) document.getElementById('quant-wfv2-stability').textContent = wfv2.stability_score.toFixed(0);
    if (wfv2.daily_pnl !== undefined) {
      var pnlEl = document.getElementById('quant-wfv2-pnl');
      pnlEl.textContent = '$' + wfv2.daily_pnl.toFixed(2);
      pnlEl.style.color = wfv2.daily_pnl > 0 ? 'var(--success)' : 'var(--error)';
    }
    if (wfv2.method) document.getElementById('quant-wfv2-method').textContent = wfv2.method;

    // Monte Carlo
    var mcBadge = document.getElementById('quant-mc-badge');
    if (mc.ruin_probability !== undefined) {
      var ruin = mc.ruin_probability;
      if (ruin <= 5) {
        mcBadge.textContent = 'SAFE';
        mcBadge.style.background = 'rgba(34,170,68,0.13)';
        mcBadge.style.color = 'var(--success)';
      } else if (ruin <= 20) {
        mcBadge.textContent = 'CAUTION';
        mcBadge.style.background = 'rgba(255,170,0,0.13)';
        mcBadge.style.color = 'var(--warning)';
      } else {
        mcBadge.textContent = 'DANGER';
        mcBadge.style.background = 'rgba(255,85,85,0.13)';
        mcBadge.style.color = 'var(--error)';
      }
      var ruinEl = document.getElementById('quant-mc-ruin');
      ruinEl.textContent = ruin.toFixed(1) + '%';
      ruinEl.style.color = ruin <= 5 ? 'var(--success)' : ruin <= 20 ? 'var(--warning)' : 'var(--error)';
    }
    if (mc.avg_max_drawdown_pct !== undefined) document.getElementById('quant-mc-dd').textContent = mc.avg_max_drawdown_pct.toFixed(1) + '%';
    if (mc.avg_sharpe !== undefined) {
      var sharpeEl = document.getElementById('quant-mc-sharpe');
      sharpeEl.textContent = mc.avg_sharpe.toFixed(2);
      sharpeEl.style.color = mc.avg_sharpe > 0.5 ? 'var(--success)' : mc.avg_sharpe > 0 ? 'var(--warning)' : 'var(--error)';
    }
    if (mc.profitable_pct !== undefined) document.getElementById('quant-mc-profitable').textContent = mc.profitable_pct.toFixed(0) + '%';

    // CUSUM
    var cusumBadge = document.getElementById('quant-cusum-badge');
    var sev = cusum.severity || 'none';
    if (sev === 'none') {
      cusumBadge.textContent = 'HEALTHY';
      cusumBadge.style.background = 'rgba(34,170,68,0.13)';
      cusumBadge.style.color = 'var(--success)';
    } else if (sev === 'warning') {
      cusumBadge.textContent = 'WARNING';
      cusumBadge.style.background = 'rgba(255,170,0,0.13)';
      cusumBadge.style.color = 'var(--warning)';
    } else {
      cusumBadge.textContent = 'CRITICAL';
      cusumBadge.style.background = 'rgba(255,85,85,0.13)';
      cusumBadge.style.color = 'var(--error)';
    }
    document.getElementById('quant-cusum-severity').textContent = sev;
    if (cusum.current_rolling_wr !== undefined) {
      var cwrEl = document.getElementById('quant-cusum-wr');
      cwrEl.textContent = cusum.current_rolling_wr.toFixed(0) + '%';
      cwrEl.style.color = wrColor(cusum.current_rolling_wr);
    }
    if (cusum.wr_drop_pp !== undefined) {
      var dropEl = document.getElementById('quant-cusum-drop');
      dropEl.textContent = cusum.wr_drop_pp.toFixed(1) + 'pp';
      dropEl.style.color = cusum.wr_drop_pp > 5 ? 'var(--error)' : 'var(--success)';
    }
    if (cusum.trades_since_change !== undefined) document.getElementById('quant-cusum-since').textContent = cusum.trades_since_change + ' trades';

    // Version History
    var versions = d.version_history || [];
    var vCard = document.getElementById('quant-version-history');
    if (versions.length > 0) {
      vCard.style.display = 'block';
      var vhtml = '';
      versions.forEach(function(v) {
        vhtml += '<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border);">';
        vhtml += '<span style="color:#00BFFF;font-weight:600;min-width:32px;">v' + v.version + '</span>';
        vhtml += '<span class="text-muted" style="min-width:100px;">' + (v.timestamp || '').substring(0, 16) + '</span>';
        vhtml += '<span style="min-width:60px;">' + (v.target || '') + '</span>';
        vhtml += '<span style="color:var(--success);">' + (v.baseline_wr || 0) + '% &rarr; ' + (v.best_wr || 0) + '%</span>';
        vhtml += '</div>';
      });
      document.getElementById('quant-versions-list').innerHTML = vhtml;
    } else {
      vCard.style.display = 'none';
    }

    // Update Phase 1 panel badge
    var p1Badge = document.getElementById('qt-phase1-badge');
    if (p1Badge) {
      var wfOk = wfv2.passed;
      var mcOk = mc.ruin_probability !== undefined && mc.ruin_probability <= 5;
      var cusumOk = (cusum.severity || 'none') === 'none';
      var passed = (wfOk ? 1 : 0) + (mcOk ? 1 : 0) + (cusumOk ? 1 : 0);
      if (passed === 3) {
        p1Badge.textContent = 'ALL PASSED';
        p1Badge.style.background = 'rgba(34,170,68,0.13)';
        p1Badge.style.color = 'var(--success)';
      } else {
        p1Badge.textContent = (3 - passed) + ' FAILED';
        p1Badge.style.background = 'rgba(255,85,85,0.13)';
        p1Badge.style.color = 'var(--error)';
      }
    }
  } catch(e) { console.error('quant phase1:', e); }
}

async function loadQuantPhase2() {
  try {
    var resp = await fetch('/api/quant/phase2');
    var d = await resp.json();
    var regime = d.regime || {};
    var corr = d.correlation || {};
    var learn = d.learning || {};

    // Regime
    var cur = regime.current || 'unknown';
    document.getElementById('quant-regime-current').textContent = cur;
    document.getElementById('quant-regime-best').textContent = regime.best_regime || '--';
    document.getElementById('quant-regime-worst').textContent = regime.worst_regime || '--';
    document.getElementById('quant-regime-count').textContent = regime.regime_count || '0';

    // Regime performance mini table
    var perf = regime.performance || {};
    var pKeys = Object.keys(perf);
    if (pKeys.length > 0) {
      var rpHtml = '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">';
      pKeys.forEach(function(k) {
        var p = perf[k];
        var wr = p.win_rate || 0;
        var color = wrColor(wr);
        rpHtml += '<span style="font-size:0.6rem;padding:2px 6px;border-radius:3px;background:' + color + '18;color:' + color + ';border:1px solid ' + color + '33;">';
        rpHtml += k + ' ' + wr + '% (' + (p.total || 0) + ')</span>';
      });
      rpHtml += '</div>';
      document.getElementById('quant-regime-perf').innerHTML = rpHtml;
    }

    // Correlation
    var risk = corr.overall_risk || 'low';
    var corrBadge = document.getElementById('quant-corr-badge');
    if (risk === 'low') {
      corrBadge.textContent = 'LOW';
      corrBadge.style.background = 'rgba(34,170,68,0.13)';
      corrBadge.style.color = 'var(--success)';
    } else if (risk === 'medium') {
      corrBadge.textContent = 'MEDIUM';
      corrBadge.style.background = 'rgba(255,170,0,0.13)';
      corrBadge.style.color = 'var(--warning)';
    } else {
      corrBadge.textContent = risk.toUpperCase();
      corrBadge.style.background = 'rgba(255,85,85,0.13)';
      corrBadge.style.color = 'var(--error)';
    }
    document.getElementById('quant-corr-risk').textContent = risk;
    document.getElementById('quant-corr-direct').textContent = corr.direct_overlaps || '0';
    document.getElementById('quant-corr-correlated').textContent = corr.correlated_overlaps || '0';
    document.getElementById('quant-corr-exposure').textContent = '$' + (corr.combined_exposure || 0).toFixed(0);
    if (corr.alert_message && corr.alert_message !== 'No data yet') {
      document.getElementById('quant-corr-alert').textContent = corr.alert_message;
    }

    // Self-Learning
    var acc = learn.recommendation_accuracy || 0;
    var accEl = document.getElementById('quant-learn-accuracy');
    accEl.textContent = acc + '%';
    accEl.style.color = acc >= 60 ? 'var(--success)' : acc >= 40 ? 'var(--warning)' : 'var(--text-muted)';
    document.getElementById('quant-learn-recs').textContent = learn.total_recommendations || '0';
    var cwr = learn.combined_wr || 0;
    if (cwr > 0) {
      var cwrEl = document.getElementById('quant-learn-combined-wr');
      cwrEl.textContent = cwr.toFixed(1) + '%';
      cwrEl.style.color = wrColor(cwr);
    }
    document.getElementById('quant-learn-odin').textContent = learn.odin_trades || '0';

    // Odin insights
    var insights = learn.odin_insights || [];
    if (insights.length > 0) {
      var iHtml = '<div style="display:flex;flex-direction:column;gap:2px;">';
      insights.slice(0, 3).forEach(function(ins) {
        iHtml += '<div style="font-size:0.62rem;color:var(--text-secondary);">' + esc(ins) + '</div>';
      });
      iHtml += '</div>';
      document.getElementById('quant-learn-insights').innerHTML = iHtml;
    }

    // Update Phase 2 panel badge
    var p2Badge = document.getElementById('qt-phase2-badge');
    if (p2Badge) {
      var cur = regime.current || 'unknown';
      var risk = corr.overall_risk || 'low';
      p2Badge.textContent = cur.toUpperCase();
      if (risk === 'high') {
        p2Badge.style.background = 'rgba(255,85,85,0.13)';
        p2Badge.style.color = 'var(--error)';
      } else if (risk === 'medium' || cur === 'high_vol') {
        p2Badge.style.background = 'rgba(255,170,0,0.13)';
        p2Badge.style.color = 'var(--warning)';
      } else {
        p2Badge.style.background = 'rgba(34,170,68,0.13)';
        p2Badge.style.color = 'var(--success)';
      }
    }
  } catch(e) { console.error('quant phase2:', e); }
}

async function loadQuantPNLImpact() {
  try {
    var resp = await fetch('/api/quant/pnl-impact');
    var d = await resp.json();
    if (!d.updated) return;

    // Update panel badge
    var daily = d.daily_pnl || 0;
    var pBadge = document.getElementById('qt-phase3-badge');
    if (pBadge) {
      if (daily > 0) {
        pBadge.textContent = '+$' + daily.toFixed(2) + '/day';
        pBadge.style.background = 'rgba(34,170,68,0.13)';
        pBadge.style.color = 'var(--success)';
      } else if (daily < 0) {
        pBadge.textContent = '-$' + Math.abs(daily).toFixed(2) + '/day';
        pBadge.style.background = 'rgba(255,85,85,0.13)';
        pBadge.style.color = 'var(--error)';
      } else {
        pBadge.textContent = '$0/day';
        pBadge.style.background = 'rgba(0,191,255,0.13)';
        pBadge.style.color = '#00BFFF';
      }
    }

    // Hidden compat badge
    var badge = document.getElementById('quant-pnl-badge');
    if (badge) badge.textContent = (daily >= 0 ? '+' : '-') + '$' + Math.abs(daily).toFixed(2) + '/day';

    // Stats
    var dailyEl = document.getElementById('quant-pnl-daily');
    dailyEl.textContent = (daily >= 0 ? '+' : '') + '$' + daily.toFixed(2);
    dailyEl.style.color = daily >= 0 ? 'var(--success)' : 'var(--error)';

    var monthly = d.monthly_pnl || 0;
    var moEl = document.getElementById('quant-pnl-monthly');
    moEl.textContent = (monthly >= 0 ? '+' : '') + '$' + monthly.toFixed(2);
    moEl.style.color = monthly >= 0 ? 'var(--success)' : 'var(--error)';

    var wrDelta = d.wr_delta || 0;
    var wrEl = document.getElementById('quant-pnl-wr-delta');
    wrEl.textContent = (wrDelta >= 0 ? '+' : '') + wrDelta.toFixed(1) + 'pp';
    wrEl.style.color = wrDelta >= 0 ? 'var(--success)' : 'var(--error)';

    var net = d.net_trade_change || 0;
    var netEl = document.getElementById('quant-pnl-net-trades');
    netEl.textContent = (net >= 0 ? '+' : '') + net;
    netEl.style.color = net >= 0 ? 'var(--success)' : 'var(--error)';

    // By asset
    var byAsset = d.by_asset || {};
    var aKeys = Object.keys(byAsset);
    if (aKeys.length > 0) {
      var aHtml = '<div class="qt-phase-card-title" style="margin-bottom:6px;">By Asset</div>';
      aKeys.forEach(function(a) {
        var s = byAsset[a];
        var delta = s.pnl_delta || 0;
        var color = delta >= 0 ? 'var(--success)' : 'var(--error)';
        aHtml += '<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid var(--border);">';
        aHtml += '<span>' + a.charAt(0).toUpperCase() + a.slice(1) + '</span>';
        aHtml += '<span style="color:' + color + ';">' + (delta >= 0 ? '+' : '') + '$' + delta.toFixed(2) + ' (' + (s.prop_wr || 0) + '%)</span>';
        aHtml += '</div>';
      });
      document.getElementById('quant-pnl-by-asset').innerHTML = aHtml;
    }

    // Param attribution
    var attrs = d.param_attribution || [];
    if (attrs.length > 0) {
      var atHtml = '<div class="qt-phase-card-title" style="margin-bottom:6px;">Param Impact</div>';
      attrs.forEach(function(a) {
        var delta = a.pnl_impact || 0;
        var color = delta >= 0 ? 'var(--success)' : 'var(--error)';
        atHtml += '<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid var(--border);">';
        atHtml += '<span>' + esc(a.param) + ' <span class="text-muted">' + a.old_value + ' &rarr; ' + a.new_value + '</span></span>';
        atHtml += '<span style="color:' + color + ';">' + (delta >= 0 ? '+' : '') + '$' + delta.toFixed(2) + ' (' + (a.net_trades >= 0 ? '+' : '') + a.net_trades + ' trades)</span>';
        atHtml += '</div>';
      });
      document.getElementById('quant-pnl-attribution').innerHTML = atHtml;
    }
  } catch(e) {
    console.error('quant pnl-impact:', e);
  }
}

function renderQuantRadar(analyticsData) {
  var container = document.getElementById('qt-radar-container');
  if (!container) return;
  var status = window._quantStatusData || {};
  var kelly = analyticsData.kelly || {};
  var diversity = analyticsData.diversity || {};
  var decay = analyticsData.decay || {};

  // Calculate 5 radar axes (0-100 scale)
  var baseWR = status.baseline_win_rate || 0;
  var wrAxis = Math.min(100, Math.max(0, (baseWR / 70) * 100));

  // OOS from walk-forward (stored as text in stat card)
  var oosEl = document.getElementById('quant-wf-oos');
  var oosText = oosEl ? oosEl.textContent : '0';
  var oosWR = parseFloat(oosText) || 0;
  var oosAxis = Math.min(100, Math.max(0, (oosWR / 70) * 100));

  // Diversity
  var divScore = diversity.diversity_score || 0;
  var divAxis = Math.min(100, divScore);

  // Stability (inverted overfit gap)
  var ofEl = document.getElementById('quant-overfit');
  var ofText = ofEl ? ofEl.textContent : '0';
  var ofGap = parseFloat(ofText) || 0;
  var stbAxis = Math.min(100, Math.max(0, 100 - ofGap * 5));

  // Edge
  var avgEdge = status.baseline_avg_edge || 0;
  var edgAxis = Math.min(100, Math.max(0, avgEdge * 5));

  var values = [wrAxis, oosAxis, divAxis, stbAxis, edgAxis];
  var labels = ['WR', 'OOS', 'DIV', 'STB', 'EDG'];
  container.innerHTML = radarSVG(180, values, labels, '#00BFFF');

  // Score = weighted average
  var score = Math.round(wrAxis * 0.3 + oosAxis * 0.25 + divAxis * 0.15 + stbAxis * 0.2 + edgAxis * 0.1);
  document.getElementById('qt-score-value').textContent = score;

  var grade;
  if (score >= 80) grade = 'SHARP';
  else if (score >= 60) grade = 'SOLID';
  else if (score >= 40) grade = 'FAIR';
  else grade = 'WEAK';

  var gradeEl = document.getElementById('qt-score-grade');
  gradeEl.textContent = grade;
  gradeEl.style.color = score >= 80 ? 'var(--success)' : score >= 60 ? 'var(--agent-quant)' : score >= 40 ? 'var(--warning)' : 'var(--error)';
}

async function quantApplyParams() {
  var status = document.getElementById('qt-action-status');
  if (status) status.textContent = 'Applying best params...';
  try {
    var resp = await fetch('/api/quant/apply-params', {method: 'POST'});
    var d = await resp.json();
    if (status) {
      status.textContent = d.success ? d.message : ('Failed: ' + d.message);
      status.style.color = d.success ? 'var(--success)' : 'var(--error)';
    }
    if (d.success) setTimeout(loadQuantTab, 1000);
  } catch(e) {
    if (status) { status.textContent = 'Error: ' + e.message; status.style.color = 'var(--error)'; }
  }
}

async function quantRollbackParams() {
  var status = document.getElementById('qt-action-status');
  if (status) status.textContent = 'Rolling back params...';
  try {
    var resp = await fetch('/api/quant/rollback-params', {method: 'POST'});
    var d = await resp.json();
    if (status) {
      status.textContent = d.success ? d.message : ('Failed: ' + d.message);
      status.style.color = d.success ? 'var(--success)' : 'var(--error)';
    }
    if (d.success) setTimeout(loadQuantTab, 1000);
  } catch(e) {
    if (status) { status.textContent = 'Error: ' + e.message; status.style.color = 'var(--error)'; }
  }
}

function quantExportReport() {
  var status = document.getElementById('qt-action-status');
  if (status) status.textContent = 'Generating report...';
  var report = {
    generated: new Date().toISOString(),
    agent: 'quant',
    status: window._quantStatusData || {},
    verdict: {
      current_wr: document.getElementById('quant-verdict-current') ? document.getElementById('quant-verdict-current').textContent : '--',
      best_wr: document.getElementById('quant-verdict-best') ? document.getElementById('quant-verdict-best').textContent : '--',
    },
    score: document.getElementById('qt-score-value') ? document.getElementById('qt-score-value').textContent : '--',
    grade: document.getElementById('qt-score-grade') ? document.getElementById('qt-score-grade').textContent : '--',
  };
  var blob = new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = 'quant_report_' + new Date().toISOString().slice(0, 10) + '.json';
  a.click();
  URL.revokeObjectURL(url);
  if (status) { status.textContent = 'Report downloaded'; status.style.color = 'var(--success)'; }
}

function quantHealthCheck() {
  fetch('/api/quant').then(function(r){return r.json();}).then(function(d){
    var msg = 'Quant Health Check\n';
    msg += '---\n';
    msg += 'Last Run: ' + (d.last_run || 'Never') + '\n';
    msg += 'Trades: ' + (d.trade_count || 0) + '\n';
    msg += 'Combos Tested: ' + (d.total_combos_tested || 0) + '\n';
    msg += 'Baseline WR: ' + (d.baseline_win_rate || 0) + '%\n';
    msg += 'Best WR: ' + (d.best_win_rate || 0) + '%\n';
    msg += 'Improvement: ' + (d.improvement || 0) + 'pp\n';
    msg += 'Recommendations: ' + (d.recommendations_count || 0);
    alert(msg);
  }).catch(function(e){ alert('Health check failed: ' + e.message); });
}

// ── ODIN — Futures Swing Trading Bot ──
// The complete Odin renderer lives in the inline <script> in odin.html.
// loadOdinTab() delegates to that authoritative implementation.

async function loadOdinTab() {
  if (typeof odinRefresh === 'function') {
    odinRefresh();
  }
}

// ── Snipe Engine v7 Dashboard Cards ─────────────────────────

var _snipeV7Timer = null;

function refreshSnipeV7() {
  fetch('/api/garves/snipe-v7').then(function(r){ return r.json(); }).then(function(d) {
    if (!d || !d.enabled) {
      document.getElementById('snipe-signal-score').textContent = 'OFF';
      return;
    }

    // ── BTC Scanner Card (v9 flow sniper) ──
    var slots = d.slots || {};
    var btcSlot = slots['bitcoin'] || {};
    var scoreEl_a = document.getElementById('snipe-score-bitcoin');
    var stateEl_a = document.getElementById('snipe-state-bitcoin');
    var cardEl = document.getElementById('snipe-card-bitcoin');
    if (scoreEl_a) {
      var slotScore = btcSlot.last_score || 0;
      var slotDir = (btcSlot.last_direction || '').toUpperCase();
      var slotState = (btcSlot.state || 'idle').toUpperCase();
      if (slotScore > 0) {
        scoreEl_a.textContent = slotScore.toFixed(0);
        scoreEl_a.style.color = slotScore >= 75 ? '#22c55e' : slotScore >= 50 ? '#eab308' : '#f7931a';
      } else {
        scoreEl_a.textContent = '--';
        scoreEl_a.style.color = '#f7931a';
      }
      var stateLabel = slotState;
      if (slotDir && slotState === 'TRACKING') stateLabel = slotState + ' ' + slotDir;
      stateEl_a.textContent = stateLabel;
      if (cardEl) {
        if (slotState === 'EXECUTING' || slotState === 'ARMED') {
          cardEl.style.background = 'rgba(34,197,94,0.08)';
        } else if (slotState === 'TRACKING') {
          cardEl.style.background = 'rgba(234,179,8,0.06)';
        } else {
          cardEl.style.background = '';
        }
      }
    }

    // Flow detector status
    var flow = d.flow_detector || {};
    var flowDirEl = document.getElementById('flow-direction-badge');
    var flowActiveBadge = document.getElementById('flow-active-badge');
    var flowStrBar = document.getElementById('flow-strength-bar');
    var flowStrPct = document.getElementById('flow-strength-pct');
    var flowSusLabel = document.getElementById('flow-sustained-label');
    if (flowDirEl) {
      var fDir = (flow.direction || 'none').toUpperCase();
      flowDirEl.textContent = fDir;
      flowDirEl.style.color = fDir === 'UP' ? '#22c55e' : fDir === 'DOWN' ? '#ef4444' : 'var(--text-muted)';
    }
    if (flowActiveBadge) {
      if (flow.is_strong) {
        flowActiveBadge.style.display = '';
        flowActiveBadge.style.background = 'rgba(34,197,94,0.15)';
        flowActiveBadge.style.color = '#22c55e';
      } else {
        flowActiveBadge.style.display = 'none';
      }
    }
    if (flowStrBar) {
      var strPct = Math.round((flow.strength || 0) * 100);
      flowStrBar.style.width = strPct + '%';
      flowStrBar.style.background = strPct >= 60 ? '#22c55e' : strPct >= 30 ? '#eab308' : '#8b5cf6';
    }
    if (flowStrPct) flowStrPct.textContent = Math.round((flow.strength || 0) * 100) + '%';
    if (flowSusLabel) flowSusLabel.textContent = (flow.sustained_ticks || 0) + ' sustained ticks | ' + (flow.snapshots || 0) + ' snapshots';

    // Flow status card highlight
    var flowCard = document.getElementById('flow-status-card');
    if (flowCard) {
      if (flow.is_strong) {
        flowCard.style.background = 'rgba(34,197,94,0.08)';
        flowCard.style.borderLeftColor = '#22c55e';
      } else if (flow.direction && flow.direction !== 'none') {
        flowCard.style.background = 'rgba(234,179,8,0.06)';
        flowCard.style.borderLeftColor = '#eab308';
      } else {
        flowCard.style.background = '';
        flowCard.style.borderLeftColor = '#8b5cf6';
      }
    }

    // Execution routing badge + last route
    var execVenueEl = document.getElementById('snipe-exec-venue');
    var execBadge = document.getElementById('exec-routing-badge');
    var execLastRoute = document.getElementById('exec-last-route');
    if (execVenueEl) {
      var execTf = d.default_exec_tf || '15m';
      execVenueEl.textContent = '5m \u2192 ' + execTf;
    }
    if (execBadge) {
      var execTf2 = d.default_exec_tf || '15m';
      execBadge.textContent = '5m \u2192 ' + execTf2;
    }
    if (execLastRoute && d.last_exec_routing && d.last_exec_routing.timestamp) {
      var lr = d.last_exec_routing;
      var ago = Math.round(Date.now() / 1000 - lr.timestamp);
      var agoStr = ago < 60 ? ago + 's ago' : Math.round(ago / 60) + 'm ago';
      execLastRoute.style.display = '';
      execLastRoute.innerHTML = 'Last: <span style="color:' +
        (lr.direction === 'up' ? '#22c55e' : '#ef4444') + ';">' +
        (lr.direction || '').toUpperCase() + '</span> ' +
        lr.scanner_tf + ' \u2192 ' + lr.exec_tf +
        ' | score=' + (lr.score || 0).toFixed(0) +
        ' | flow=' + (lr.flow_strength || 0).toFixed(2) +
        ' | ' + agoStr;
    }

    // Positions badge
    var posBadge = document.getElementById('snipe-positions-badge');
    if (posBadge) {
      var ap = d.active_positions || 0;
      var mp = d.max_positions || 3;
      posBadge.textContent = ap + '/' + mp;
      posBadge.style.color = ap >= mp ? '#ef4444' : ap > 0 ? '#eab308' : '#22c55e';
    }

    // Hot windows table
    var hotWindows = d.hot_windows || [];
    var hotEl = document.getElementById('snipe-hot-windows');
    var hotTbody = document.getElementById('snipe-hot-tbody');
    if (hotWindows.length > 0 && hotEl && hotTbody) {
      hotEl.style.display = 'block';
      var hh = '';
      for (var hi = 0; hi < hotWindows.length; hi++) {
        var hw = hotWindows[hi];
        var dirColor = hw.direction === 'up' ? '#22c55e' : '#ef4444';
        hh += '<tr><td>' + (hw.asset || '').toUpperCase() + '</td>' +
          '<td style="color:' + dirColor + ';">' + (hw.direction || '').toUpperCase() + '</td>' +
          '<td>' + (hw.score || 0).toFixed(0) + '</td>' +
          '<td>' + (hw.state || '').toUpperCase() + '</td></tr>';
      }
      hotTbody.innerHTML = hh;
    } else if (hotEl) {
      hotEl.style.display = 'none';
    }

    // CandleStore warm-up indicator
    // Snipe Engine Warm-up Status card
    var warmupCard = document.getElementById('snipe-warmup-card');
    var diag = d.warmup_diagnostics;
    if (warmupCard && diag) {
      if (diag.ready) {
        warmupCard.style.display = 'none';
      } else {
        warmupCard.style.display = 'block';
        var pct = Math.min(100, Math.round(diag.elapsed_min / diag.target_min * 100));
        var barColor = diag.can_reach_threshold ? '#eab308' : '#ef4444';
        var scoreColor = diag.can_reach_threshold ? '#22c55e' : '#ef4444';
        var compLabels = {
          clob_spread_compression: 'CLOB Spread', clob_yes_no_pressure: 'CLOB Pressure',
          bos_choch_5m: 'BOS 5m', bos_choch_15m: 'BOS 15m', volume_delta: 'Volume Delta'
        };
        var compHtml = '';
        for (var i = 0; i < diag.base_components.length; i++) {
          var c = diag.base_components[i];
          var lbl = compLabels[c.name] || c.name;
          compHtml += '<span style="display:inline-block;padding:2px 6px;border-radius:3px;' +
            'background:rgba(239,68,68,0.1);color:#ef4444;font-size:0.68rem;margin:2px;">' +
            lbl + ' (' + c.current.toFixed(1) + '/' + c.max + ')</span>';
        }
        if (!compHtml) compHtml = '<span style="color:#22c55e;font-size:0.72rem;">All active</span>';
        warmupCard.innerHTML = '<div class="glass-card" style="padding:10px 14px;">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">' +
          '<span style="font-weight:700;font-size:0.76rem;">Snipe Engine Warm-up</span>' +
          '<span style="font-size:0.72rem;color:' + scoreColor + ';">Max Score: ~' +
          diag.max_realistic_score + '/100 (thresh=' + diag.threshold + ')</span></div>' +
          '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">' +
          '<span style="font-size:0.72rem;color:rgba(255,255,255,0.6);min-width:80px;">' +
          diag.elapsed_min.toFixed(0) + '/' + diag.target_min + ' min</span>' +
          '<div style="flex:1;height:6px;background:rgba(255,255,255,0.06);border-radius:3px;overflow:hidden;">' +
          '<div style="width:' + pct + '%;height:100%;background:' + barColor +
          ';border-radius:3px;transition:width 0.5s;"></div></div>' +
          '<span style="font-size:0.72rem;color:rgba(255,255,255,0.6);">' + pct + '%</span></div>' +
          '<div style="margin-bottom:4px;font-size:0.68rem;color:rgba(255,255,255,0.5);">Components at base values:</div>' +
          '<div>' + compHtml + '</div>' +
          (diag.clob_bypassed ? '<div style="margin-top:6px;font-size:0.68rem;color:#eab308;">5m CLOB books dead — executing via MTF gate (15m/1h)</div>' : '') +
          '</div>';
      }
    }

    // Snipe Threshold Status Card
    var threshCard = document.getElementById('snipe-threshold-card');
    var threshInfo = d.threshold_info;
    if (threshCard && threshInfo) {
      var perAsset = threshInfo.per_asset || {};
      var assetNames = {bitcoin: 'BTC'};
      var assetColors = {bitcoin: '#f7931a'};
      var badges = '';
      var hasAny = false;
      for (var ta in perAsset) {
        hasAny = true;
        var tv = perAsset[ta];
        var tColor = tv <= 66 ? '#22c55e' : (tv > 70 ? '#ef4444' : '#eab308');
        var tBg = tv <= 66 ? 'rgba(34,197,94,0.12)' : (tv > 70 ? 'rgba(239,68,68,0.12)' : 'rgba(234,179,8,0.12)');
        badges += '<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:6px;' +
          'background:' + tBg + ';border:1px solid ' + tColor + '30;font-size:0.72rem;font-weight:600;">' +
          '<span style="color:' + (assetColors[ta] || '#fff') + ';">' + (assetNames[ta] || ta.toUpperCase()) + '</span>' +
          '<span style="color:' + tColor + ';">T=' + tv + '</span></span> ';
      }
      if (hasAny) {
        threshCard.style.display = 'block';
        var overrideHtml = '';
        if (threshInfo.override_active) {
          var ttlMin = Math.round((threshInfo.override_ttl_s || 0) / 60);
          overrideHtml = '<div style="margin-top:6px;padding:4px 10px;border-radius:4px;' +
            'background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.25);' +
            'font-size:0.68rem;color:#eab308;">Override active: T=' +
            (threshInfo.override_value || '?') + ' (expires in ' + ttlMin + 'm)</div>';
        }
        threshCard.innerHTML = '<div class="glass-card" style="padding:10px 14px;">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">' +
          '<span style="font-weight:700;font-size:0.76rem;">Snipe Thresholds</span>' +
          '<span style="font-size:0.62rem;padding:2px 8px;border-radius:10px;' +
          'background:rgba(139,92,246,0.15);color:#8b5cf6;font-weight:600;">Data Quality Mode</span></div>' +
          '<div style="display:flex;gap:8px;flex-wrap:wrap;">' + badges + '</div>' +
          overrideHtml + '</div>';
      } else {
        threshCard.style.display = 'none';
      }
    }

    // Per-asset threshold labels on scanner cards
    if (d.slots) {
      var threshAssets = ['bitcoin'];
      for (var ti = 0; ti < threshAssets.length; ti++) {
        var tAsset = threshAssets[ti];
        var tEl = document.getElementById('snipe-thresh-' + tAsset);
        if (tEl && d.slots[tAsset]) {
          var slotThresh = d.slots[tAsset].threshold;
          if (slotThresh) {
            var stColor = slotThresh <= 66 ? '#22c55e' : (slotThresh > 70 ? '#ef4444' : '#eab308');
            tEl.innerHTML = '<span style="color:' + stColor + ';">T=' + slotThresh + '</span>';
          }
        }
      }
    }

    // Price Feed Status (WS health + freshness)
    var freshEl = document.getElementById('snipe-price-freshness');
    var freshness = d.price_freshness;
    if (freshEl && freshness) {
      var wsCount = 0; var restCount = 0; var failCount = 0; var total = 0;
      var details = [];
      for (var a in freshness) {
        total++;
        var f = freshness[a];
        if (f.source === 'ws' && !f.stale) { wsCount++; }
        else if (f.source === 'rest') { restCount++; details.push(a.toUpperCase() + ' ' + f.age_s + 's'); }
        else if (f.stale) { failCount++; details.push(a.toUpperCase() + ' STALE'); }
      }
      var color, bg, border, label;
      if (wsCount === total) {
        color = '#22c55e'; bg = 'rgba(34,197,94,0.08)'; border = 'rgba(34,197,94,0.2)';
        label = 'Binance WS: Healthy (' + total + '/' + total + ' live)';
      } else if (restCount > 0 && failCount === 0) {
        color = '#eab308'; bg = 'rgba(234,179,8,0.08)'; border = 'rgba(234,179,8,0.2)';
        label = 'Binance WS: REST Fallback (' + restCount + '/' + total + ') — ' + details.join(', ');
      } else {
        color = '#ef4444'; bg = 'rgba(239,68,68,0.08)'; border = 'rgba(239,68,68,0.2)';
        label = 'Binance WS: Failed — ' + details.join(', ');
      }
      freshEl.style.display = 'block';
      freshEl.innerHTML = '<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;' +
        'background:' + bg + ';border:1px solid ' + border + ';border-radius:6px;">' +
        '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + color + ';"></span>' +
        '<span style="color:' + color + ';font-size:0.85em;">' + label + '</span></div>';
    }

    // Signal Score card (last scorer result)
    var scorer = d.scorer || {};
    var scoreEl = document.getElementById('snipe-signal-score');
    var scoreDirEl = document.getElementById('snipe-score-dir');
    if (scorer.active && scorer.last_score != null) {
      var sc = scorer.last_score;
      scoreEl.textContent = sc.toFixed(0) + '/100';
      scoreEl.style.color = sc >= 65 ? '#22c55e' : sc >= 50 ? '#eab308' : '#ef4444';
      scoreDirEl.textContent = (scorer.last_direction || '').toUpperCase() + ' | thresh=' + scorer.threshold;

      // Show score breakdown
      var barsEl = document.getElementById('snipe-score-bars');
      var comps = scorer.components || {};
      var html = '';
      var compNames = ['flow_strength','delta_magnitude','binance_imbalance',
        'clob_spread_compression','flow_sustained','delta_sustained',
        'bos_choch_5m','time_positioning','implied_price_edge'];
      var compLabels = {
        flow_strength: 'Flow Str', delta_magnitude: 'Delta Mag',
        binance_imbalance: 'Binance OB', clob_spread_compression: 'Spread Comp',
        flow_sustained: 'Flow Sus', delta_sustained: 'Delta Sus',
        bos_choch_5m: 'BOS 5m',
        time_positioning: 'Timing', implied_price_edge: 'Price Edge'
      };
      for (var i = 0; i < compNames.length; i++) {
        var cn = compNames[i];
        var cv = comps[cn] || {};
        var pct = ((cv.score || 0) * 100).toFixed(0);
        var barColor = pct >= 60 ? '#22c55e' : pct >= 30 ? '#eab308' : '#ef4444';
        html += '<div style="display:flex;align-items:center;gap:6px;">' +
          '<span style="width:72px;text-align:right;color:var(--text-muted);">' + (compLabels[cn] || cn) + '</span>' +
          '<div style="flex:1;height:6px;background:rgba(255,255,255,0.06);border-radius:3px;overflow:hidden;">' +
          '<div style="width:' + pct + '%;height:100%;background:' + barColor + ';border-radius:3px;"></div></div>' +
          '<span style="width:24px;font-family:var(--font-mono);">' + (cv.weighted != null ? cv.weighted.toFixed(1) : '--') + '</span></div>';
      }
      barsEl.innerHTML = html;
      document.getElementById('snipe-v7-breakdown').style.display = 'block';
    } else {
      scoreEl.textContent = '--';
      scoreEl.style.color = '#8b5cf6';
      scoreDirEl.textContent = '';
      document.getElementById('snipe-v7-breakdown').style.display = 'none';
    }

    // Success Rate card
    var rateEl = document.getElementById('snipe-success-rate');
    var wlEl = document.getElementById('snipe-wl-record');
    var stats = d.stats || {};
    if (d.success_rate_50 != null) {
      rateEl.textContent = d.success_rate_50.toFixed(1) + '%';
      rateEl.style.color = d.success_rate_50 >= 60 ? '#22c55e' : d.success_rate_50 >= 45 ? '#eab308' : '#ef4444';
    } else {
      rateEl.textContent = '--';
    }
    wlEl.textContent = (stats.wins || 0) + 'W-' + (stats.losses || 0) + 'L | $' + (stats.pnl || 0).toFixed(2);

    // Avg Latency card
    var latEl = document.getElementById('snipe-avg-latency');
    var latDetailEl = document.getElementById('snipe-latency-detail');
    if (d.avg_latency_ms != null) {
      latEl.textContent = d.avg_latency_ms.toFixed(0) + 'ms';
      latEl.style.color = d.avg_latency_ms < 500 ? '#22c55e' : d.avg_latency_ms < 2000 ? '#eab308' : '#ef4444';
      latDetailEl.textContent = d.dry_run ? 'paper mode' : 'live';
    } else {
      latEl.textContent = '--';
      latDetailEl.textContent = 'no trades yet';
    }

    // CLOB Spread card
    var spreadEl = document.getElementById('snipe-clob-spread');
    var stateEl = document.getElementById('snipe-state-label');
    stateEl.textContent = (d.threshold_mode || '') +
      ' | pos=' + (d.active_positions || 0) + '/' + (d.max_positions || 3) +
      ' | ' + (d.strategy || 'flow_sniper');

    // Try to get spread from scorer CLOB data
    if (scorer.active && scorer.components && scorer.components.clob_spread_compression) {
      var spreadDetail = scorer.components.clob_spread_compression.detail || '';
      var spreadMatch = spreadDetail.match(/spread=([0-9.]+)/);
      if (spreadMatch) {
        spreadEl.textContent = '$' + parseFloat(spreadMatch[1]).toFixed(4);
      } else {
        spreadEl.textContent = '--';
      }
    } else {
      spreadEl.textContent = '--';
    }

    // Performance tracking section
    var perfEl = document.getElementById('snipe-perf-stats');
    var perf = d.performance || {};
    if (perfEl && perf.total_trades > 0) {
      var ph = '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px;">';
      ph += '<div><span style="color:var(--text-muted);">WR</span><br><b style="font-size:1.1em;color:' +
        (perf.win_rate >= 60 ? '#22c55e' : perf.win_rate >= 45 ? '#eab308' : '#ef4444') + ';">' +
        perf.win_rate.toFixed(1) + '%</b></div>';
      ph += '<div><span style="color:var(--text-muted);">PnL</span><br><b style="color:' +
        (perf.total_pnl >= 0 ? '#22c55e' : '#ef4444') + ';">$' + perf.total_pnl.toFixed(2) + '</b></div>';
      ph += '<div><span style="color:var(--text-muted);">Streak</span><br><b>' + (perf.streak || '--') + '</b></div>';
      ph += '</div>';

      // Score buckets table
      var bk = perf.score_buckets || {};
      ph += '<table style="width:100%;font-size:0.85em;"><tr style="color:var(--text-muted);">' +
        '<th style="text-align:left;">Score</th><th>Trades</th><th>WR</th><th>PnL</th></tr>';
      var bucketKeys = ['60-69','70-79','80-89','90-100'];
      for (var bi = 0; bi < bucketKeys.length; bi++) {
        var bkey = bucketKeys[bi];
        var bv = bk[bkey] || {};
        if (bv.trades > 0) {
          ph += '<tr><td>' + bkey + '</td><td style="text-align:center;">' + bv.trades + '</td>' +
            '<td style="text-align:center;color:' + (bv.wr >= 60 ? '#22c55e' : bv.wr >= 45 ? '#eab308' : '#ef4444') +
            ';">' + bv.wr.toFixed(0) + '%</td>' +
            '<td style="text-align:center;color:' + (bv.pnl >= 0 ? '#22c55e' : '#ef4444') + ';">$' +
            bv.pnl.toFixed(2) + '</td></tr>';
        }
      }
      ph += '</table>';

      // Avg score winners vs losers
      ph += '<div style="margin-top:6px;font-size:0.85em;color:var(--text-muted);">Avg score: ' +
        '<span style="color:#22c55e;">W=' + (perf.avg_score_winners || 0).toFixed(0) + '</span> vs ' +
        '<span style="color:#ef4444;">L=' + (perf.avg_score_losers || 0).toFixed(0) + '</span>';

      // Direction stats
      var dir = perf.direction || {};
      if (dir.up && dir.down) {
        ph += ' | UP ' + (dir.up.wr || 0).toFixed(0) + '% (' + dir.up.trades + ')' +
          ' | DOWN ' + (dir.down.wr || 0).toFixed(0) + '% (' + dir.down.trades + ')';
      }
      ph += '</div>';

      perfEl.innerHTML = ph;
      perfEl.style.display = 'block';
    } else if (perfEl) {
      perfEl.innerHTML = '<span style="color:var(--text-muted);">No resolved trades yet</span>';
      perfEl.style.display = 'block';
    }

    // ── Resolution Scalper Status ──
    var rs = d.resolution_scalper || {};
    var rsEnabled = rs.enabled;
    var rsDot = document.getElementById('res-scalp-dot');
    var rsBadge = document.getElementById('res-scalp-badge');
    if (rsDot && rsBadge) {
      if (rsEnabled) {
        rsDot.style.background = '#f97316';
        rsBadge.textContent = (rs.active_count || 0) + '/' + ((rs.thresholds || {}).max_concurrent || 3);
        rsBadge.style.background = 'rgba(249,115,22,0.15)';
        rsBadge.style.color = '#f97316';
      } else {
        rsDot.style.background = '#6b7280';
        rsBadge.textContent = 'OFF';
      }
    }
    // Overview card
    var rsStats = rs.stats || {};
    var rsLearner = rs.learner || {};
    var ovResStatus = document.getElementById('ov-res-scalp-status');
    if (ovResStatus) {
      if (rsEnabled) {
        ovResStatus.textContent = rs.dry_run ? 'DRY RUN' : 'LIVE';
        ovResStatus.style.background = rs.dry_run ? 'rgba(249,115,22,0.15)' : 'rgba(34,197,94,0.15)';
        ovResStatus.style.color = rs.dry_run ? '#f97316' : '#22c55e';
      } else {
        ovResStatus.textContent = 'OFF';
      }
    }
    var ovResWr = document.getElementById('ov-res-wr');
    if (ovResWr) ovResWr.textContent = rsLearner.total_trades > 0 ? rsLearner.win_rate.toFixed(1) + '%' : '--';
    var ovResTrades = document.getElementById('ov-res-trades');
    if (ovResTrades) ovResTrades.textContent = rsLearner.total_trades || 0;
    var ovResPnl = document.getElementById('ov-res-pnl');
    if (ovResPnl) {
      var rsPnl = rsLearner.total_pnl || 0;
      ovResPnl.textContent = '$' + rsPnl.toFixed(2);
      ovResPnl.style.color = rsPnl >= 0 ? '#22c55e' : '#ef4444';
    }
    var ovResCal = document.getElementById('ov-res-cal');
    if (ovResCal) ovResCal.textContent = rsLearner.calibration_score != null ? rsLearner.calibration_score.toFixed(2) : '--';
    var ovResActive = document.getElementById('ov-res-active');
    if (ovResActive) ovResActive.textContent = rs.active_count || 0;
    // Live opportunities table
    var rsOpps = rs.opportunities || [];
    var rsOppsWrap = document.getElementById('res-scalp-opps');
    var rsOppsTbody = document.getElementById('res-scalp-opps-body');
    if (rsOppsWrap && rsOppsTbody) {
      if (rsOpps.length > 0) {
        rsOppsWrap.style.display = '';
        var oh = '';
        for (var oi = 0; oi < rsOpps.length; oi++) {
          var o = rsOpps[oi];
          var oColor = o.direction === 'up' ? '#22c55e' : '#ef4444';
          oh += '<tr><td>' + (o.asset || '').toUpperCase() + '</td>' +
            '<td style="color:' + oColor + ';">' + (o.direction || '').toUpperCase() + '</td>' +
            '<td>' + (o.probability || 0).toFixed(1) + '%</td>' +
            '<td>$' + (o.market_price || 0).toFixed(2) + '</td>' +
            '<td style="color:#f97316;">' + (o.edge || 0).toFixed(1) + '%</td>' +
            '<td>' + (o.z_score || 0).toFixed(2) + '</td>' +
            '<td>' + (o.remaining_s || 0) + 's</td>' +
            '<td>$' + (o.kelly_bet || 0).toFixed(2) + '</td></tr>';
        }
        rsOppsTbody.innerHTML = oh;
      } else {
        rsOppsWrap.style.display = 'none';
      }
    }
    // Recent resolved trades
    var rsRecent = rs.recent_trades || [];
    var rsRecentWrap = document.getElementById('res-scalp-recent');
    var rsRecentTbody = document.getElementById('res-scalp-recent-body');
    if (rsRecentWrap && rsRecentTbody) {
      if (rsRecent.length > 0) {
        rsRecentWrap.style.display = '';
        var rh = '';
        for (var ri = 0; ri < rsRecent.length; ri++) {
          var rt = rsRecent[ri];
          var rtColor = rt.direction === 'up' ? '#22c55e' : '#ef4444';
          var rtResult = rt.won ? 'WIN' : 'LOSS';
          var rtBadge = rt.won ? 'badge-success' : 'badge-error';
          rh += '<tr><td>' + (rt.asset || '').toUpperCase() + '</td>' +
            '<td style="color:' + rtColor + ';">' + (rt.direction || '').toUpperCase() + '</td>' +
            '<td>' + (rt.probability || 0).toFixed(1) + '%</td>' +
            '<td>' + (rt.edge || 0).toFixed(1) + '%</td>' +
            '<td>$' + (rt.size_usd || 0).toFixed(2) + '</td>' +
            '<td><span class="badge ' + rtBadge + '">' + rtResult + '</span></td>' +
            '<td style="color:' + (rt.pnl >= 0 ? '#22c55e' : '#ef4444') + ';text-align:right;">$' + (rt.pnl || 0).toFixed(2) + '</td></tr>';
        }
        rsRecentTbody.innerHTML = rh;
      } else {
        rsRecentWrap.style.display = 'none';
      }
    }
    // Engine panel details
    var resEngMode = document.getElementById('res-scalp-engine-mode');
    if (resEngMode) resEngMode.textContent = rs.dry_run ? 'DRY RUN' : 'LIVE';
    var resEngTrades = document.getElementById('res-eng-trades');
    if (resEngTrades) resEngTrades.textContent = rsLearner.total_trades || 0;
    var resEngWr = document.getElementById('res-eng-wr');
    if (resEngWr) resEngWr.textContent = rsLearner.total_trades > 0 ? rsLearner.win_rate.toFixed(1) + '%' : '--';
    var resEngPnl = document.getElementById('res-eng-pnl');
    if (resEngPnl) {
      var rePnl = rsLearner.total_pnl || 0;
      resEngPnl.textContent = '$' + rePnl.toFixed(2);
      resEngPnl.style.color = rePnl >= 0 ? '#22c55e' : '#ef4444';
    }
    var resEngCal = document.getElementById('res-eng-cal');
    if (resEngCal) resEngCal.textContent = rsLearner.calibration_score != null ? rsLearner.calibration_score.toFixed(2) : '--';
    // Thresholds
    var rsThresh = rs.thresholds || {};
    var reMinP = document.getElementById('res-eng-min-p');
    if (reMinP) reMinP.textContent = ((rsThresh.min_probability || 0.75) * 100).toFixed(0) + '%';
    var reMinEdge = document.getElementById('res-eng-min-edge');
    if (reMinEdge) reMinEdge.textContent = ((rsThresh.min_edge || 0.08) * 100).toFixed(0) + '%';
    var reMaxPrice = document.getElementById('res-eng-max-price');
    if (reMaxPrice) reMaxPrice.textContent = '$' + (rsThresh.max_market_price || 0.88).toFixed(2);
    var reMaxBet = document.getElementById('res-eng-max-bet');
    if (reMaxBet) reMaxBet.textContent = '$' + (rsThresh.max_bet || 20).toFixed(0);
    var reKelly = document.getElementById('res-eng-kelly');
    if (reKelly) reKelly.textContent = ((rsThresh.kelly_fraction || 0.25) * 100).toFixed(0) + '%';

    // Mode badge gv- style
    if (resEngMode) {
      if (rs.dry_run) {
        resEngMode.className = 'gv-mode-badge dry';
        resEngMode.textContent = 'DRY RUN';
      } else {
        resEngMode.className = 'gv-mode-badge live';
        resEngMode.textContent = 'LIVE';
      }
    }

    // Active Windows Scanner grid
    var scanWrap = document.getElementById('res-eng-scanner-wrap');
    var scanGrid = document.getElementById('res-eng-scanner-grid');
    if (scanWrap && scanGrid) {
      if (rsOpps.length > 0) {
        scanWrap.style.display = '';
        var shtml = '';
        for (var si = 0; si < rsOpps.length; si++) {
          var so = rsOpps[si];
          var soActive = so.state === 'EXECUTING';
          var soDir = (so.direction || '--').toUpperCase();
          var soDirClr = soDir === 'UP' ? '#22c55e' : soDir === 'DOWN' ? '#ef4444' : 'var(--text-muted)';
          var soState = (so.state || 'WATCHING').toUpperCase();
          var soStateBg = soState === 'EXECUTING' ? 'rgba(34,197,94,0.15)' : soState === 'WATCHING' ? 'rgba(249,115,22,0.1)' : 'rgba(255,255,255,0.06)';
          var soStateClr = soState === 'EXECUTING' ? '#22c55e' : soState === 'WATCHING' ? '#f97316' : 'var(--text-muted)';
          var soRemPct = so.remaining_s ? Math.min(100, Math.round((300 - so.remaining_s) / 300 * 100)) : 0;
          shtml += '<div class="gv-scanner-card' + (soActive ? ' active' : '') + '">';
          shtml += '<div class="asset-name">' + (so.asset || '?').toUpperCase() + '</div>';
          shtml += '<div style="color:' + soDirClr + ';font-weight:700;font-size:0.78rem;margin-bottom:4px;">' + soDir + '</div>';
          shtml += '<div style="font-family:var(--font-mono);font-size:0.66rem;color:var(--text-secondary);">';
          shtml += 'P:' + ((so.probability || 0) * 100).toFixed(1) + '% &bull; E:' + ((so.edge || 0) * 100).toFixed(1) + '%';
          shtml += '</div>';
          shtml += '<div style="margin:6px 0 4px;background:rgba(255,255,255,0.06);height:4px;border-radius:2px;overflow:hidden;">';
          shtml += '<div style="width:' + soRemPct + '%;height:100%;background:#f97316;border-radius:2px;transition:width 1s;"></div>';
          shtml += '</div>';
          shtml += '<div style="display:flex;justify-content:space-between;align-items:center;">';
          shtml += '<span class="gv-countdown">' + (so.remaining_s || 0) + 's</span>';
          shtml += '<span class="gv-scanner-status" style="background:' + soStateBg + ';color:' + soStateClr + ';">' + soState + '</span>';
          shtml += '</div>';
          shtml += '</div>';
        }
        scanGrid.innerHTML = shtml;
      } else {
        scanWrap.style.display = 'none';
      }
    }

    // Active positions table
    var rsActive = rs.active_positions || [];
    var resActiveTbody = document.getElementById('res-eng-active-tbody');
    if (resActiveTbody) {
      if (rsActive.length > 0) {
        var ah = '';
        for (var ai = 0; ai < rsActive.length; ai++) {
          var ap = rsActive[ai];
          var apColor = ap.direction === 'up' ? '#22c55e' : '#ef4444';
          ah += '<tr><td>' + (ap.asset || '').toUpperCase() + '</td>' +
            '<td style="color:' + apColor + ';">' + (ap.direction || '').toUpperCase() + '</td>' +
            '<td>$' + (ap.entry_price || 0).toFixed(3) + '</td>' +
            '<td>$' + (ap.size_usd || 0).toFixed(2) + '</td>' +
            '<td>' + (ap.shares || 0).toFixed(1) + '</td>' +
            '<td>' + (ap.probability || 0).toFixed(1) + '%</td>' +
            '<td>' + (ap.edge || 0).toFixed(1) + '%</td>' +
            '<td>' + (ap.z_score || 0).toFixed(2) + '</td>' +
            '<td>' + (ap.remaining_s || 0) + 's</td></tr>';
        }
        resActiveTbody.innerHTML = ah;
      } else {
        resActiveTbody.innerHTML = '<tr><td colspan="9" class="text-muted" style="text-align:center;">No active positions</td></tr>';
      }
    }
    // Resolved trades table
    var resTradesTbody = document.getElementById('res-eng-trades-tbody');
    if (resTradesTbody) {
      if (rsRecent.length > 0) {
        var th = '';
        for (var ti2 = 0; ti2 < rsRecent.length; ti2++) {
          var t2 = rsRecent[ti2];
          var t2Color = t2.direction === 'up' ? '#22c55e' : '#ef4444';
          var t2Result = t2.won ? 'WIN' : 'LOSS';
          var t2Badge = t2.won ? 'badge-success' : 'badge-error';
          th += '<tr><td>' + (t2.asset || '').toUpperCase() + '</td>' +
            '<td style="color:' + t2Color + ';">' + (t2.direction || '').toUpperCase() + '</td>' +
            '<td>$' + (t2.entry_price || 0).toFixed(3) + '</td>' +
            '<td>$' + (t2.size_usd || 0).toFixed(2) + '</td>' +
            '<td>' + (t2.probability || 0).toFixed(1) + '%</td>' +
            '<td>' + (t2.edge || 0).toFixed(1) + '%</td>' +
            '<td><span class="badge ' + t2Badge + '">' + t2Result + '</span></td>' +
            '<td style="color:' + (t2.pnl >= 0 ? '#22c55e' : '#ef4444') + ';text-align:right;">$' + (t2.pnl || 0).toFixed(2) + '</td></tr>';
        }
        resTradesTbody.innerHTML = th;
      } else {
        resTradesTbody.innerHTML = '<tr><td colspan="8" class="text-muted" style="text-align:center;">No trades yet</td></tr>';
      }
    }

    // Auto-refresh while on garves tab
    clearTimeout(_snipeV7Timer);
    if (currentTab === 'garves-live') {
      _snipeV7Timer = setTimeout(refreshSnipeV7, 5000);
    }
  }).catch(function() {
    clearTimeout(_snipeV7Timer);
    if (currentTab === 'garves-live') {
      _snipeV7Timer = setTimeout(refreshSnipeV7, 5000);
    }
  });
}

// ── Snipe Assist Live ───────────────────────────────────────

var _snipeAssistTimer = null;

function refreshSnipeAssist() {
  fetch('/api/snipe-assist/status').then(function(r){ return r.json(); }).then(function(d) {
    var scoreEl = document.getElementById('sa-timing-score');
    var dirEl = document.getElementById('sa-direction');
    var sizeEl = document.getElementById('sa-size-rec');
    var accEl = document.getElementById('sa-accuracy');
    var badgeEl = document.getElementById('sa-action-badge');
    var ageEl = document.getElementById('sa-age');
    var factorsEl = document.getElementById('sa-factors');
    var agentsEl = document.getElementById('sa-agents-row');
    var learnerEl = document.getElementById('sa-learner-footer');
    var overrideEl = document.getElementById('sa-override-badge');

    if (!scoreEl || !d || d.active === false) {
      if (scoreEl) scoreEl.textContent = '--';
      clearTimeout(_snipeAssistTimer);
      if (currentTab === 'garves-live') _snipeAssistTimer = setTimeout(refreshSnipeAssist, 5000);
      return;
    }

    // Timing Score
    var sc = d.timing_score || 0;
    scoreEl.textContent = sc.toFixed(0) + '/100';
    scoreEl.style.color = sc >= 80 ? '#22c55e' : sc >= 65 ? '#eab308' : '#ef4444';

    // Direction
    var dir = (d.direction || '--').toUpperCase();
    dirEl.textContent = dir;
    dirEl.style.color = dir === 'UP' ? '#22c55e' : dir === 'DOWN' ? '#ef4444' : 'var(--text-muted)';

    // Size Rec
    var sizePct = d.recommended_size_pct || 0;
    sizeEl.textContent = (sizePct * 100).toFixed(0) + '%';
    sizeEl.style.color = sizePct >= 1.0 ? '#22c55e' : sizePct >= 0.5 ? '#eab308' : '#ef4444';

    // Accuracy from learner
    var learner = d.learner || {};
    if (learner.overall_wr != null) {
      accEl.textContent = learner.overall_wr.toFixed(1) + '%';
      accEl.style.color = learner.overall_wr >= 55 ? '#22c55e' : learner.overall_wr >= 45 ? '#eab308' : '#ef4444';
    } else {
      accEl.textContent = '--';
    }

    // Action badge
    var action = (d.action || 'idle').replace('_', ' ').toUpperCase();
    badgeEl.textContent = action;
    if (d.action === 'auto_execute') {
      badgeEl.style.background = 'rgba(34,197,94,0.2)'; badgeEl.style.color = '#22c55e';
    } else if (d.action === 'conservative') {
      badgeEl.style.background = 'rgba(234,179,8,0.2)'; badgeEl.style.color = '#eab308';
    } else {
      badgeEl.style.background = 'rgba(239,68,68,0.2)'; badgeEl.style.color = '#ef4444';
    }

    // Age
    if (d.age_s != null) {
      ageEl.textContent = d.stale ? 'STALE (' + d.age_s.toFixed(0) + 's)' : d.age_s.toFixed(0) + 's ago';
      ageEl.style.color = d.stale ? '#ef4444' : 'var(--text-muted)';
    }

    // Confidence factors
    var factors = d.confidence_factors || {};
    var fhtml = '';
    var fLabels = {snipe_signal_strength:'Signal(40)',liquidity_quality:'Liquidity(20)',time_positioning:'Timing(15)',regime_alignment:'Regime(10)',historical_accuracy:'History(15)'};
    var fKeys = ['snipe_signal_strength','liquidity_quality','time_positioning','regime_alignment','historical_accuracy'];
    for (var i = 0; i < fKeys.length; i++) {
      var fk = fKeys[i];
      var fv = factors[fk] || {};
      var fpct = ((fv.raw || 0) * 100).toFixed(0);
      var fColor = fpct >= 60 ? '#22c55e' : fpct >= 30 ? '#eab308' : '#ef4444';
      fhtml += '<div style="display:flex;align-items:center;gap:4px;">' +
        '<span style="width:80px;text-align:right;color:var(--text-muted);">' + (fLabels[fk] || fk) + '</span>' +
        '<div style="flex:1;height:5px;background:rgba(255,255,255,0.06);border-radius:3px;overflow:hidden;">' +
        '<div style="width:' + fpct + '%;height:100%;background:' + fColor + ';border-radius:3px;"></div></div>' +
        '<span style="width:24px;font-family:var(--font-mono);">' + (fv.weighted != null ? fv.weighted.toFixed(1) : '--') + '</span></div>';
    }
    factorsEl.innerHTML = fhtml;

    // Agent overrides row
    var overrides = d.agent_overrides || {};
    var ahtml = '';
    var agentNames = {garves_snipe:'Garves Snipe',garves_taker:'Garves Taker',odin:'Odin',hawk:'Hawk'};
    for (var ak in agentNames) {
      if (!overrides[ak]) continue;
      var ao = overrides[ak];
      var aColor = ao.action === 'auto_execute' ? '#22c55e' : ao.action === 'conservative' ? '#eab308' : '#ef4444';
      ahtml += '<span style="color:' + aColor + ';">' + agentNames[ak] + ': ' + (ao.action || '--').replace('_',' ').toUpperCase() + ' ' + ((ao.size_pct || 0) * 100).toFixed(0) + '%</span>';
    }
    agentsEl.innerHTML = ahtml;

    // Learner footer
    if (learner.total_records > 0) {
      learnerEl.textContent = 'Learning: ' + learner.resolved + '/' + learner.total_records + ' resolved | ' +
        (learner.overall_wr != null ? learner.overall_wr.toFixed(1) + '% WR' : 'no data') +
        ' | PnL $' + (learner.total_pnl || 0).toFixed(2);
    } else {
      learnerEl.textContent = 'Learning: no data yet';
    }

    // Override badge
    if (d.action === 'auto_execute' || d.action === 'auto_skip') {
      // Check if it is from override file
    }
    overrideEl.style.display = 'none';

    clearTimeout(_snipeAssistTimer);
    if (currentTab === 'garves-live') _snipeAssistTimer = setTimeout(refreshSnipeAssist, 5000);
  }).catch(function() {
    clearTimeout(_snipeAssistTimer);
    if (currentTab === 'garves-live') _snipeAssistTimer = setTimeout(refreshSnipeAssist, 5000);
  });
}

function snipeAssistOverride(action) {
  fetch('/api/snipe-assist/override', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: action, duration_s: 300})
  }).then(function(r){ return r.json(); }).then(function(d) {
    if (d.status === 'ok') {
      refreshSnipeAssist();
    }
  }).catch(function(e) {
    console.warn('Override error:', e);
  });
}

// ── CLOB Status Pill ────────────────────────────────────────

var _clobPopoverOpen = false;
var _clobAutoTimer = null;

function refreshClobPill() {
  fetch('/api/garves/clob-status').then(function(r){ return r.json(); }).then(function(d) {
    var pill = document.getElementById('clob-pill');
    var dot = document.getElementById('clob-pill-dot');
    var label = document.getElementById('clob-pill-label');
    var age = document.getElementById('clob-pill-age');
    if (!pill) return;

    var status = (d.status || 'UNKNOWN').toUpperCase();
    var emoji, color, bgColor, alertClass;

    if (status === 'CONNECTED') {
      emoji = '\uD83D\uDFE2'; color = '#22c55e'; bgColor = 'rgba(34,197,94,0.1)';
      alertClass = '';
    } else if (status === 'CONNECTING') {
      emoji = '\uD83D\uDFE1'; color = '#eab308'; bgColor = 'rgba(234,179,8,0.1)';
      alertClass = '';
    } else if (status === 'REST_FALLBACK') {
      emoji = '\uD83D\uDFE0'; color = '#f97316'; bgColor = 'rgba(249,115,22,0.12)';
      alertClass = 'clob-pill-alert';
    } else if (status === 'DEGRADED') {
      emoji = '\uD83D\uDFE1'; color = '#eab308'; bgColor = 'rgba(234,179,8,0.12)';
      alertClass = 'clob-pill-alert';
    } else {
      emoji = '\uD83D\uDD34'; color = '#ef4444'; bgColor = 'rgba(239,68,68,0.12)';
      alertClass = 'clob-pill-alert';
    }

    dot.textContent = emoji;
    label.textContent = status === 'REST_FALLBACK' ? 'REST' : status;
    label.style.color = color;
    pill.style.background = bgColor;
    pill.style.borderColor = color + '33';
    pill.className = alertClass;

    // Age / last seen
    if (status === 'CONNECTED' && d.uptime_s > 0) {
      age.textContent = formatDuration(d.uptime_s);
      age.style.color = '#22c55e';
    } else if (status === 'REST_FALLBACK') {
      age.textContent = '-20 penalty';
      age.style.color = '#f97316';
    } else if (d.silence_s > 0) {
      age.textContent = 'Last seen ' + formatDuration(d.silence_s) + ' ago';
      age.style.color = status === 'DISCONNECTED' ? '#ef4444' : 'var(--text-muted)';
    } else {
      age.textContent = '';
    }

    // Popover data
    var popStatus = document.getElementById('clob-pop-status');
    var popDetail = document.getElementById('clob-pop-detail');
    var popReconn = document.getElementById('clob-pop-reconnects');
    var popProactive = document.getElementById('clob-pop-proactive');
    var popUptime = document.getElementById('clob-pop-uptime');
    var popSilence = document.getElementById('clob-pop-silence');
    if (popStatus) popStatus.innerHTML = '<span style="color:' + color + ';font-weight:700;">' + emoji + ' ' + status + '</span>' + (d.rest_fallback ? ' <span style="color:#f97316;font-size:0.68rem;">(REST polling, -20 score)</span>' : '');
    if (popDetail) popDetail.textContent = d.detail ? 'Detail: ' + d.detail : '';
    if (popReconn) popReconn.textContent = 'Reconnects today: ' + (d.reconnects_today || 0);
    if (popProactive) popProactive.textContent = 'Proactive cycles: ' + (d.proactive_reconnects || 0);
    if (popUptime) popUptime.textContent = status === 'CONNECTED' ? 'Uptime: ' + formatDuration(d.uptime_s || 0) : 'Last connected: ' + formatTimestamp(d.last_connected);
    if (popSilence) popSilence.textContent = d.silence_s > 0 ? 'Last data: ' + formatDuration(d.silence_s) + ' ago' : 'Last data: just now';

    // Auto-refresh while on garves tab
    clearTimeout(_clobAutoTimer);
    if (currentTab === 'garves-live') {
      _clobAutoTimer = setTimeout(refreshClobPill, 5000);
    }
  }).catch(function() {
    clearTimeout(_clobAutoTimer);
    if (currentTab === 'garves-live') {
      _clobAutoTimer = setTimeout(refreshClobPill, 5000);
    }
  });
}

function formatDuration(s) {
  s = Math.round(s);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
}

function formatTimestamp(ts) {
  if (!ts || ts <= 0) return 'never';
  var d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:true});
}

function toggleClobPopover() {
  var pop = document.getElementById('clob-popover');
  if (!pop) return;
  _clobPopoverOpen = !_clobPopoverOpen;
  pop.style.display = _clobPopoverOpen ? 'block' : 'none';
}

function toggleExecVenue() {
  var el = document.getElementById('snipe-exec-venue');
  if (!el) return;
  var current = el.textContent.trim();
  var next = current.indexOf('1h') >= 0 ? '5m \u2192 15m' : '5m \u2192 1h';
  el.textContent = next;
  showToast('Exec venue toggled to ' + next + ' (UI only — edit DEFAULT_EXEC_TF in engine.py to apply)', 'info');
}

function forceReconnectClob() {
  fetch('/api/garves/force-reconnect', {method:'POST'}).then(function(r){ return r.json(); }).then(function(d) {
    if (d.ok) {
      showToast('Force reconnect sent', 'info');
    } else {
      showToast('Reconnect failed: ' + (d.error || 'unknown'), 'error');
    }
    _clobPopoverOpen = false;
    document.getElementById('clob-popover').style.display = 'none';
    setTimeout(refreshClobPill, 1000);
  }).catch(function() {
    showToast('Reconnect request failed', 'error');
  });
}

// Close popovers on outside click
document.addEventListener('click', function(e) {
  var clobWrap = document.getElementById('clob-pill-wrap');
  if (clobWrap && !clobWrap.contains(e.target) && _clobPopoverOpen) {
    _clobPopoverOpen = false;
    document.getElementById('clob-popover').style.display = 'none';
  }
  var binWrap = document.getElementById('binance-pill-wrap');
  if (binWrap && !binWrap.contains(e.target) && _binancePopoverOpen) {
    _binancePopoverOpen = false;
    document.getElementById('binance-popover').style.display = 'none';
  }
});

// ── Binance WS Status Pill ──────────────────────────────────
var _binancePopoverOpen = false;
var _binanceAutoTimer = null;

function refreshBinancePill() {
  fetch('/api/garves/binance-status').then(function(r){ return r.json(); }).then(function(d) {
    var pill = document.getElementById('binance-pill');
    var dot = document.getElementById('binance-pill-dot');
    var label = document.getElementById('binance-pill-label');
    var age = document.getElementById('binance-pill-age');
    if (!pill) return;

    var status = (d.status || 'UNKNOWN').toUpperCase();
    var emoji, color, bgColor;

    if (status === 'CONNECTED') {
      emoji = '\uD83D\uDFE2'; color = '#22c55e'; bgColor = 'rgba(34,197,94,0.1)';
    } else if (status === 'CONNECTING' || status === 'RECONNECTING') {
      emoji = '\uD83D\uDFE1'; color = '#eab308'; bgColor = 'rgba(234,179,8,0.1)';
    } else if (status === 'REST_FALLBACK') {
      emoji = '\uD83D\uDFE0'; color = '#f97316'; bgColor = 'rgba(249,115,22,0.12)';
    } else {
      emoji = '\uD83D\uDD34'; color = '#ef4444'; bgColor = 'rgba(239,68,68,0.12)';
    }

    dot.textContent = emoji;
    label.textContent = status === 'REST_FALLBACK' ? 'REST' : 'Binance';
    label.style.color = color;
    pill.style.background = bgColor;
    pill.style.borderColor = color + '33';

    // Age display
    if (status === 'REST_FALLBACK') {
      age.textContent = '-20 penalty';
      age.style.color = '#f97316';
    } else if (status === 'CONNECTED' && d.silence_s >= 0 && d.silence_s < 30) {
      age.textContent = d.silence_s + 's';
      age.style.color = '#22c55e';
    } else if (d.silence_s > 0) {
      age.textContent = formatDuration(d.silence_s) + ' ago';
      age.style.color = d.silence_s > 30 ? '#ef4444' : 'var(--text-muted)';
    } else {
      age.textContent = '';
    }

    // Popover data
    var popStatus = document.getElementById('binance-pop-status');
    var popReconn = document.getElementById('binance-pop-reconnects');
    var popStream = document.getElementById('binance-pop-stream');
    var popSilence = document.getElementById('binance-pop-silence');
    if (popStatus) popStatus.innerHTML = '<span style="color:' + color + ';font-weight:700;">' + emoji + ' ' + status + '</span>' + (d.rest_fallback ? ' <span style="color:#f97316;font-size:0.68rem;">(REST polling, -20 score)</span>' : '');
    if (popReconn) popReconn.textContent = 'Reconnects: ' + (d.reconnect_count || 0);
    if (popStream) popStream.textContent = 'Stream: ' + (d.stream_url || 'unknown');
    if (popSilence) popSilence.textContent = d.silence_s > 0 ? 'Last data: ' + formatDuration(d.silence_s) + ' ago' : 'Last data: just now';

    clearTimeout(_binanceAutoTimer);
    if (currentTab === 'garves-live') {
      _binanceAutoTimer = setTimeout(refreshBinancePill, 5000);
    }
  }).catch(function() {
    clearTimeout(_binanceAutoTimer);
    if (currentTab === 'garves-live') {
      _binanceAutoTimer = setTimeout(refreshBinancePill, 5000);
    }
  });
}

function toggleBinancePopover() {
  var pop = document.getElementById('binance-popover');
  if (!pop) return;
  _binancePopoverOpen = !_binancePopoverOpen;
  pop.style.display = _binancePopoverOpen ? 'block' : 'none';
}

// ── Garves V2 Intelligence ──────────────────────────────────────
function loadGarvesV2Metrics() {
  // Fetch V2 metrics
  fetch('/api/garves/v2-metrics').then(function(r){return r.json();}).then(function(d) {
    // Kill switch badge
    var badge = document.getElementById('v2-kill-switch-badge');
    var reasonEl = document.getElementById('v2-kill-switch-reason');
    if (badge) {
      if (d.kill_switch_active) {
        badge.textContent = 'KILLED';
        badge.style.background = 'var(--danger)';
        badge.style.color = '#fff';
        if (reasonEl) { reasonEl.textContent = d.kill_switch_reason || ''; reasonEl.style.display = 'block'; }
      } else {
        badge.textContent = 'SAFE';
        badge.style.background = 'var(--success)';
        badge.style.color = '#000';
        if (reasonEl) reasonEl.style.display = 'none';
      }
    }

    // Core metrics
    var cm = d.core_metrics || {};
    var wr20El = document.getElementById('v2-wr-20');
    var wr50El = document.getElementById('v2-wr-50');
    var evEl = document.getElementById('v2-ev-capture');
    var ddEl = document.getElementById('v2-drawdown');
    if (wr20El) { wr20El.textContent = cm.wr_20 != null ? (cm.wr_20 * 100).toFixed(0) + '%' : '--'; wr20El.style.color = cm.wr_20 != null && cm.wr_20 >= 0.55 ? 'var(--success)' : cm.wr_20 != null && cm.wr_20 < 0.50 ? 'var(--danger)' : 'var(--text)'; }
    if (wr50El) { wr50El.textContent = cm.wr_50 != null ? (cm.wr_50 * 100).toFixed(0) + '%' : '--'; wr50El.style.color = cm.wr_50 != null && cm.wr_50 >= 0.55 ? 'var(--success)' : cm.wr_50 != null && cm.wr_50 < 0.52 ? 'var(--danger)' : 'var(--text)'; }
    if (evEl) { evEl.textContent = cm.ev_capture_pct != null ? (cm.ev_capture_pct * 100).toFixed(0) + '%' : '--'; evEl.style.color = cm.ev_capture_pct > 0.5 ? 'var(--success)' : cm.ev_capture_pct < 0.3 ? 'var(--danger)' : 'var(--text)'; }
    if (ddEl) { ddEl.textContent = cm.current_drawdown_pct != null ? cm.current_drawdown_pct.toFixed(0) + '%' : '--'; ddEl.style.color = cm.current_drawdown_pct > 20 ? 'var(--danger)' : cm.current_drawdown_pct > 10 ? 'var(--warning)' : 'var(--text)'; }

    // Hero card population from V2 metrics
    var heroWr20 = document.getElementById('garves-hero-wr-20');
    var heroWr50 = document.getElementById('garves-hero-wr-50');
    var heroWr100 = document.getElementById('garves-hero-wr-100');
    var heroEv = document.getElementById('garves-hero-ev-capture');
    var heroEdge = document.getElementById('garves-hero-edge-quality');
    if (heroWr20 && cm.wr_20 != null) { heroWr20.textContent = (cm.wr_20 * 100).toFixed(0) + '%'; heroWr20.style.color = cm.wr_20 >= 0.55 ? 'var(--success)' : cm.wr_20 < 0.50 ? 'var(--danger)' : 'var(--text)'; }
    if (heroWr50 && cm.wr_50 != null) heroWr50.textContent = (cm.wr_50 * 100).toFixed(0) + '%';
    if (heroWr100 && cm.wr_100 != null) heroWr100.textContent = (cm.wr_100 * 100).toFixed(0) + '%';
    if (heroEv && cm.ev_capture_pct != null) { heroEv.textContent = (cm.ev_capture_pct * 100).toFixed(0) + '%'; heroEv.style.color = cm.ev_capture_pct > 0.5 ? 'var(--success)' : cm.ev_capture_pct < 0.3 ? 'var(--danger)' : 'var(--text)'; }
    if (heroEdge && cm.ev_capture_20 != null) heroEdge.textContent = (cm.ev_capture_20 * 100).toFixed(0) + '%';

    // Second row: rolling EV capture + slippage + timing
    var ev20El = document.getElementById('v2-ev-20');
    var ev50El = document.getElementById('v2-ev-50');
    var slipEl = document.getElementById('v2-slippage');
    var timingEl = document.getElementById('v2-timing');
    if (ev20El) { ev20El.textContent = cm.ev_capture_20 != null ? (cm.ev_capture_20 * 100).toFixed(0) + '%' : '--'; ev20El.style.color = cm.ev_capture_20 != null && cm.ev_capture_20 > 0.5 ? 'var(--success)' : cm.ev_capture_20 != null && cm.ev_capture_20 < 0.2 ? 'var(--danger)' : 'var(--text)'; }
    if (ev50El) { ev50El.textContent = cm.ev_capture_50 != null ? (cm.ev_capture_50 * 100).toFixed(0) + '%' : '--'; ev50El.style.color = cm.ev_capture_50 != null && cm.ev_capture_50 > 0.5 ? 'var(--success)' : cm.ev_capture_50 != null && cm.ev_capture_50 < 0.2 ? 'var(--danger)' : 'var(--text)'; }
    if (slipEl) { slipEl.textContent = cm.avg_slippage_pct != null ? (cm.avg_slippage_pct * 100).toFixed(1) + '%' : '--'; slipEl.style.color = cm.avg_slippage_pct > 0.03 ? 'var(--danger)' : 'var(--text)'; }
    if (timingEl) { timingEl.textContent = cm.avg_timing_impact != null ? (cm.avg_timing_impact * 100).toFixed(2) + '%' : '--'; timingEl.style.color = cm.avg_timing_impact > 0.02 ? 'var(--warning)' : 'var(--text)'; }

    // Warnings
    var warningsCard = document.getElementById('v2-warnings-card');
    var warningsList = document.getElementById('v2-warnings-list');
    if (warningsCard && warningsList) {
      if (d.warnings && d.warnings.length > 0) {
        warningsCard.style.display = 'block';
        warningsList.innerHTML = d.warnings.map(function(w){ return '<div style="margin-bottom:3px;color:var(--danger);">' + w + '</div>'; }).join('');
      } else {
        warningsCard.style.display = 'none';
      }
    }
  }).catch(function(){});

  // Fetch post-trade analysis
  fetch('/api/garves/post-trade-analysis').then(function(r){return r.json();}).then(function(d) {
    var feed = document.getElementById('v2-post-trade-feed');
    if (!feed) return;
    if (!d.recent || d.recent.length === 0) {
      feed.innerHTML = '<span class="text-muted">No analyses yet</span>';
      return;
    }
    var html = d.recent.slice(-10).reverse().map(function(a) {
      var icon = a.mistake_type === 'none' ? '<span style="color:var(--success);">OK</span>' : '<span style="color:var(--danger);">' + a.mistake_type.toUpperCase() + '</span>';
      return '<div style="margin-bottom:3px;">' + icon + ' ' + (a.trade_id || '').slice(0,12) + ' | EV capture: ' + ((a.ev_capture_pct || 0) * 100).toFixed(0) + '% | Exec: ' + (a.execution_quality || 'n/a') + '</div>';
    }).join('');
    feed.innerHTML = '<div style="margin-bottom:4px;color:var(--text-muted);">Total: ' + d.total + ' | Mistakes: ' + d.mistakes + ' | Avg EV: ' + (d.avg_ev_capture_pct || 0).toFixed(0) + '%</div>' + html;
  }).catch(function(){});

  // Fetch auto-rules
  fetch('/api/garves/auto-rules').then(function(r){return r.json();}).then(function(d) {
    var el = document.getElementById('v2-auto-rules');
    if (!el) return;
    if (!d.active_rules || d.active_rules.length === 0) {
      el.innerHTML = '<span class="text-muted">No active rules</span>';
      return;
    }
    el.innerHTML = d.active_rules.map(function(r) {
      var action = r.action || {};
      return '<div style="margin-bottom:3px;">' + (action.type || 'unknown') + ' on ' + (action.asset || '?') + '/' + (action.timeframe || '?') + ' <span class="text-muted">(x' + (r.count || 0) + ')</span></div>';
    }).join('');
  }).catch(function(){});

  // Fetch edge report
  fetch('/api/garves/edge-report').then(function(r){return r.json();}).then(function(d) {
    var el = document.getElementById('v2-edge-decay');
    if (!el) return;
    var decay = d.edge_decay || [];
    if (decay.length === 0) {
      var cc = d.competitive_check || {};
      if (cc.status === 'improving') {
        el.innerHTML = '<span style="color:var(--success);">Edges improving (' + (cc.wr_trend_pp || 0).toFixed(1) + 'pp)</span>';
      } else if (cc.status === 'declining') {
        el.innerHTML = '<span style="color:var(--danger);">Edges declining (' + (cc.wr_trend_pp || 0).toFixed(1) + 'pp)</span>';
      } else {
        el.innerHTML = '<span class="text-muted">Edges stable</span>';
      }
      return;
    }
    el.innerHTML = decay.map(function(d) {
      return '<div style="margin-bottom:3px;color:var(--danger);">' + d.indicator + ': ' + (d.alltime_accuracy * 100).toFixed(0) + '% -> ' + (d.recent_accuracy * 100).toFixed(0) + '% (-' + d.drop_pp.toFixed(0) + 'pp)</div>';
    }).join('');

    // Weekly competitive check recommendations
    var weeklyCard = document.getElementById('v2-weekly-card');
    var weeklyContent = document.getElementById('v2-weekly-content');
    var cc = d.competitive_check || {};
    if (weeklyCard && weeklyContent && cc.recommendations && cc.recommendations.length > 0) {
      weeklyCard.style.display = 'block';
      var statusColor = cc.status === 'improving' ? 'var(--success)' : cc.status === 'declining' ? 'var(--danger)' : 'var(--text-muted)';
      var header = '<div style="margin-bottom:4px;">Status: <span style="color:' + statusColor + ';font-weight:700;">' + (cc.status || 'unknown').toUpperCase() + '</span> | WR trend: ' + (cc.wr_trend_pp || 0).toFixed(1) + 'pp | Edge trend: ' + (cc.edge_trend_pp || 0).toFixed(2) + 'pp</div>';
      var recs = cc.recommendations.map(function(r) {
        var pcolor = r.priority === 'high' ? 'var(--danger)' : r.priority === 'medium' ? 'var(--warning)' : 'var(--text-muted)';
        return '<div style="margin-bottom:2px;"><span style="color:' + pcolor + ';font-weight:600;">[' + r.priority.toUpperCase() + ']</span> ' + r.action + '</div>';
      }).join('');
      weeklyContent.innerHTML = header + recs;
    }
  }).catch(function(){});

  // Fetch diagnostics for auto-debug suggestions
  fetch('/api/garves/diagnostics').then(function(r){return r.json();}).then(function(d) {
    var debugCard = document.getElementById('v2-debug-card');
    var debugFixes = document.getElementById('v2-debug-fixes');
    if (!debugCard || !debugFixes) return;
    var fixes = d.suggested_fixes || [];
    if (fixes.length === 0) {
      debugCard.style.display = 'none';
      return;
    }
    debugCard.style.display = 'block';
    debugFixes.innerHTML = fixes.map(function(f) {
      var scolor = f.status === 'normal' || f.status === 'healthy' ? 'var(--success)' : 'var(--warning)';
      return '<div style="margin-bottom:4px;"><span style="color:' + scolor + ';font-weight:600;">' + f.check + '</span>: ' + f.fix + '</div>';
    }).join('');
  }).catch(function(){});
}

// ── Maker Engine Status ──────────────────────────────────────────
function loadPortfolioAllocation() {
  fetch('/api/portfolio-allocation').then(function(r){ return r.json(); }).then(function(d) {
    var card = document.getElementById('portfolio-alloc-card');
    if (!card || d.error) return;
    card.style.display = '';

    var el = function(id) { return document.getElementById(id); };
    el('pa-deployable').textContent = '$' + (d.deployable || 0).toFixed(0);
    el('pa-total-exposure').textContent = '$' + (d.total_exposure || 0).toFixed(0);
    el('pa-reserve').textContent = '$' + (d.reserve || 0).toFixed(0);
    el('pa-total-util').textContent = (d.total_utilization_pct || 0).toFixed(0) + '%';

    var walletAge = (d.wallet && d.wallet.age_s) ? d.wallet.age_s : 0;
    var ageLabel = walletAge < 60 ? Math.round(walletAge) + 's ago' : Math.round(walletAge/60) + 'm ago';
    el('pa-wallet-age').textContent = 'Wallet: ' + ageLabel;
    if (walletAge > 300) el('pa-wallet-age').style.color = '#ef4444';

    var agentColors = {garves:'#f7931a', hawk:'#22c55e', oracle:'#06b6d4', maker:'#8b5cf6'};
    var barsEl = el('pa-agent-bars');
    if (!barsEl) return;
    var html = '';
    (d.agents || []).forEach(function(a) {
      var col = agentColors[a.name] || '#888';
      var pct = Math.min(100, a.utilization_pct || 0);
      var alive = a.alive ? '' : ' (stale)';
      html += '<div style="display:flex;align-items:center;gap:8px;font-size:0.72rem;">' +
        '<span style="width:52px;font-weight:600;text-transform:capitalize;color:' + col + ';">' + a.name + '</span>' +
        '<div style="flex:1;height:8px;background:rgba(255,255,255,0.06);border-radius:4px;overflow:hidden;">' +
          '<div style="width:' + pct + '%;height:100%;background:' + col + ';border-radius:4px;transition:width 0.5s;"></div>' +
        '</div>' +
        '<span style="width:100px;font-family:var(--font-mono);font-size:0.68rem;text-align:right;color:var(--text-muted);">$' +
          (a.exposure || 0).toFixed(0) + ' / $' + (a.allocation || 0).toFixed(0) + alive + '</span>' +
      '</div>';
    });
    barsEl.innerHTML = html;
  }).catch(function(){});
}

function loadMakerStatus() {
  fetch('/api/garves/maker-status').then(function(r){ return r.json(); }).then(function(d) {
    var badge = document.getElementById('maker-status-badge');
    if (badge) {
      if (d.pnl && d.pnl.kill_reason) {
        badge.textContent = 'KILLED';
        badge.style.background = 'rgba(239,68,68,0.2)';
        badge.style.color = '#ef4444';
      } else if (d.enabled) {
        badge.textContent = 'ACTIVE';
        badge.style.background = 'rgba(34,197,94,0.2)';
        badge.style.color = '#22c55e';
      } else {
        badge.textContent = 'OFF';
        badge.style.background = 'rgba(255,255,255,0.06)';
        badge.style.color = 'var(--text-muted)';
      }
    }

    // P&L cards
    var pnl = d.pnl || {};
    var sessionEl = document.getElementById('maker-session-pnl');
    if (sessionEl) {
      var sp = pnl.session_pnl || 0;
      sessionEl.textContent = '$' + sp.toFixed(2);
      sessionEl.style.color = sp >= 0 ? 'var(--success)' : 'var(--error)';
    }
    var spreadEl = document.getElementById('maker-spread-captured');
    if (spreadEl) spreadEl.textContent = '$' + (pnl.spread_captured || 0).toFixed(2);
    var resEl = document.getElementById('maker-resolution-losses');
    if (resEl) {
      var rl = pnl.resolution_losses || 0;
      resEl.textContent = '-$' + rl.toFixed(2);
      resEl.style.color = rl > 0 ? 'var(--error)' : 'var(--text-muted)';
    }
    var fillsEl = document.getElementById('maker-fills-today');
    if (fillsEl) fillsEl.textContent = (d.stats || {}).fills_today || 0;
    var rebateEl = document.getElementById('maker-rebate-label');
    if (rebateEl) {
      var reb = (d.stats || {}).estimated_rebate_today || 0;
      rebateEl.textContent = reb > 0 ? ('~$' + reb.toFixed(3) + ' rebates') : '';
    }

    // Warnings
    var warnCard = document.getElementById('maker-warnings-card');
    var warnList = document.getElementById('maker-warnings-list');
    var warnings = d.warnings || [];
    if (warnCard && warnList) {
      if (warnings.length > 0) {
        warnCard.style.display = 'block';
        warnList.innerHTML = warnings.map(function(w) {
          return '<div style="margin-bottom:2px;">' + esc(w) + '</div>';
        }).join('');
      } else {
        warnCard.style.display = 'none';
      }
    }

    // Inventory table with TTR
    var invBody = document.getElementById('maker-inventory-tbody');
    if (invBody) {
      var inv = d.inventory || {};
      var invKeys = Object.keys(inv).filter(function(k) {
        var v = inv[k];
        return Math.abs(v.net_shares || 0) >= 0.1 || (v.fills_today || 0) > 0;
      });
      if (invKeys.length === 0) {
        invBody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;">No inventory</td></tr>';
      } else {
        var html = '';
        for (var i = 0; i < invKeys.length; i++) {
          var k = invKeys[i];
          var v = inv[k];
          var ns = v.net_shares || 0;
          var absNs = Math.abs(ns);
          var dir = ns > 0.1 ? 'LONG' : ns < -0.1 ? 'SHORT' : 'FLAT';
          var dirClr = dir === 'LONG' ? 'var(--success)' : dir === 'SHORT' ? 'var(--error)' : 'var(--text-muted)';
          var rem = v.remaining_s || 9999;
          var ttrLabel = rem > 3600 ? Math.floor(rem/60) + 'm' : rem > 60 ? Math.floor(rem/60) + 'm' : rem + 's';
          var ttrClr = rem < 600 ? '#ef4444' : rem < 1800 ? '#eab308' : 'var(--text-muted)';
          var risk = '';
          var riskClr = 'var(--text-muted)';
          if (absNs >= 15) { risk = 'CAP HIT'; riskClr = '#ef4444'; }
          else if (absNs > 10 && rem < 600) { risk = 'FORCE FLAT'; riskClr = '#ef4444'; }
          else if (absNs > 10 && rem < 1800) { risk = 'REDUCING'; riskClr = '#eab308'; }
          else if (absNs > 5) { risk = 'MODERATE'; riskClr = '#eab308'; }
          else { risk = 'OK'; riskClr = 'var(--success)'; }
          html += '<tr>';
          html += '<td>' + esc(v.asset || '?').toUpperCase() + '</td>';
          html += '<td style="color:' + dirClr + ';font-weight:600;">' + dir + '</td>';
          html += '<td>' + absNs.toFixed(1) + '</td>';
          html += '<td>' + (v.fills_today || 0) + '</td>';
          html += '<td style="color:' + ttrClr + ';">' + ttrLabel + '</td>';
          html += '<td style="color:' + riskClr + ';font-weight:600;">' + risk + '</td>';
          html += '</tr>';
        }
        invBody.innerHTML = html;
      }
    }

    // Active Quotes table
    var qCount = document.getElementById('maker-quote-count');
    var qBody = document.getElementById('maker-quotes-tbody');
    var quotes = d.active_quotes || [];
    if (qCount) qCount.textContent = quotes.length;
    if (qBody) {
      if (quotes.length === 0) {
        qBody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;">No active quotes</td></tr>';
      } else {
        var html = '';
        for (var i = 0; i < quotes.length; i++) {
          var q = quotes[i];
          var sideClr = q.side === 'BUY' ? 'var(--success)' : 'var(--error)';
          html += '<tr>';
          html += '<td>' + esc(q.asset || '?').toUpperCase() + '</td>';
          html += '<td style="color:' + sideClr + ';font-weight:600;">' + q.side + '</td>';
          html += '<td>$' + (q.price || 0).toFixed(3) + '</td>';
          html += '<td>$' + (q.size_usd || 0).toFixed(1) + '</td>';
          html += '<td>' + (q.age_s || 0) + 's</td>';
          html += '</tr>';
        }
        qBody.innerHTML = html;
      }
    }

    // Recent Fills table
    var fBody = document.getElementById('maker-fills-tbody');
    var fills = d.recent_fills || [];
    if (fBody) {
      if (fills.length === 0) {
        fBody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;">No fills yet</td></tr>';
      } else {
        var html = '';
        for (var i = fills.length - 1; i >= 0 && i >= fills.length - 10; i--) {
          var f = fills[i];
          var sideClr = f.side === 'BUY' ? 'var(--success)' : 'var(--error)';
          var ts = f.ts ? new Date(f.ts * 1000).toLocaleTimeString() : '--';
          html += '<tr>';
          html += '<td style="font-size:0.66rem;">' + ts + '</td>';
          html += '<td>' + esc(f.asset || '?').toUpperCase() + '</td>';
          html += '<td style="color:' + sideClr + ';font-weight:600;">' + f.side + '</td>';
          html += '<td>$' + (f.price || 0).toFixed(3) + '</td>';
          html += '<td>$' + (f.fair || 0).toFixed(3) + '</td>';
          html += '<td style="color:var(--success);">$' + (f.spread_captured || 0).toFixed(4) + '</td>';
          html += '</tr>';
        }
        fBody.innerHTML = html;
      }
    }

    // Status badge (new gv- style)
    var statusBadge = document.getElementById('maker-engine-status-badge');
    if (statusBadge) {
      if (d.pnl && d.pnl.kill_reason) {
        statusBadge.textContent = 'KILLED';
        statusBadge.className = 'gv-mode-badge';
        statusBadge.style.background = 'rgba(239,68,68,0.15)';
        statusBadge.style.borderColor = 'rgba(239,68,68,0.3)';
        statusBadge.style.color = '#ef4444';
      } else if (d.enabled) {
        statusBadge.textContent = 'ACTIVE';
        statusBadge.className = 'gv-mode-badge live';
      } else {
        statusBadge.textContent = 'OFF';
        statusBadge.className = 'gv-mode-badge off';
      }
    }

    // Exposure heat bar
    var expFill = document.getElementById('maker-exposure-fill');
    var expLabel = document.getElementById('maker-exposure-label');
    if (expFill && expLabel) {
      var inv = d.inventory || {};
      var invKeys = Object.keys(inv);
      var totalVal = 0;
      for (var ei = 0; ei < invKeys.length; ei++) {
        var iv = inv[invKeys[ei]];
        totalVal += Math.abs(iv.net_shares || 0) * (iv.avg_price || 0.5);
      }
      var maxExp = (d.config || {}).max_total_exposure || 100;
      var expPct = Math.min(100, Math.round(totalVal / maxExp * 100));
      expFill.style.width = expPct + '%';
      expFill.style.background = expPct > 75 ? '#ef4444' : expPct > 50 ? '#eab308' : 'var(--success)';
      expLabel.textContent = expPct + '% ($' + totalVal.toFixed(1) + ' / $' + maxExp + ')';
      expLabel.style.color = expPct > 75 ? '#ef4444' : expPct > 50 ? '#eab308' : 'var(--success)';
    }

    // Inventory grid (card view)
    var invGrid = document.getElementById('maker-inventory-grid');
    if (invGrid) {
      var inv2 = d.inventory || {};
      var invKeys2 = Object.keys(inv2).filter(function(k) {
        var v2 = inv2[k];
        return Math.abs(v2.net_shares || 0) >= 0.1 || (v2.fills_today || 0) > 0;
      });
      if (invKeys2.length === 0) {
        invGrid.innerHTML = '<div style="text-align:center;color:var(--text-muted);font-size:0.72rem;grid-column:1/-1;">No inventory</div>';
      } else {
        var ghtml = '';
        for (var gi = 0; gi < invKeys2.length; gi++) {
          var gk = invKeys2[gi];
          var gv = inv2[gk];
          var gns = Math.abs(gv.net_shares || 0);
          var gdir = (gv.net_shares || 0) > 0.1 ? 'LONG' : (gv.net_shares || 0) < -0.1 ? 'SHORT' : 'FLAT';
          var gdirClr = gdir === 'LONG' ? '#22c55e' : gdir === 'SHORT' ? '#ef4444' : 'var(--text-muted)';
          var gval = gns * (gv.avg_price || 0.5);
          var grem = gv.remaining_s || 9999;
          var gttr = grem > 3600 ? Math.floor(grem/60) + 'm' : grem > 60 ? Math.floor(grem/60) + 'm' : grem + 's';
          var griskPct = Math.min(100, Math.round(gns / 15 * 100));
          var griskClr = griskPct > 75 ? '#ef4444' : griskPct > 50 ? '#eab308' : '#22c55e';
          ghtml += '<div class="gv-inventory-card">';
          ghtml += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">';
          ghtml += '<span style="font-family:var(--font-heading);font-size:0.82rem;font-weight:700;">' + esc(gv.asset || gk).toUpperCase() + ' <span style="color:' + gdirClr + ';font-size:0.72rem;">' + gdir + '</span></span>';
          ghtml += '</div>';
          ghtml += '<div style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-secondary);">' + gns.toFixed(1) + ' shares &bull; $' + gval.toFixed(2) + '</div>';
          ghtml += '<div style="font-family:var(--font-mono);font-size:0.68rem;color:var(--text-muted);margin-top:2px;">' + (gv.fills_today || 0) + ' fills &bull; TTR: ' + gttr + '</div>';
          ghtml += '<div class="gv-risk-gauge"><div class="gv-risk-gauge-fill" style="width:' + griskPct + '%;background:' + griskClr + ';"></div></div>';
          ghtml += '</div>';
        }
        invGrid.innerHTML = ghtml;
      }
    }

    // Config label
    var cfgEl = document.getElementById('maker-config-label');
    if (cfgEl && d.config) {
      var c = d.config;
      cfgEl.textContent = 'Quote: $' + c.quote_size_usd + ' | Max inv: $' + c.max_inventory_usd
        + ' | Max exp: $' + c.max_total_exposure + ' | Tick: ' + c.tick_interval_s + 's';
    }
  }).catch(function(){});
}


// ═══ Engine Performance Comparison ═══
function loadEngineComparison() {
  fetch('/api/garves/engine-comparison')
    .then(r => r.json())
    .then(data => {
      renderEngineComparison(data);
    })
    .catch(() => {});
}

function renderEngineComparison(data) {
  const body = document.getElementById('engine-comp-body');
  const totalsRow = document.getElementById('engine-comp-totals');
  const modeEl = document.getElementById('engine-comp-mode');
  const bankrollEl = document.getElementById('engine-comp-bankroll');
  if (!body) return;

  // Mode badge
  if (modeEl) {
    if (data.dry_run) {
      modeEl.textContent = 'PAPER';
      modeEl.style.background = 'rgba(234,179,8,0.15)';
      modeEl.style.color = '#eab308';
    } else {
      modeEl.textContent = 'LIVE';
      modeEl.style.background = 'rgba(239,68,68,0.15)';
      modeEl.style.color = '#ef4444';
    }
  }
  if (bankrollEl) {
    bankrollEl.textContent = '$' + data.bankroll.toLocaleString() + ' bankroll';
  }

  // Engine colors
  var colors = {
    taker: '#ef4444',
    snipe: '#8b5cf6',
    maker: '#22c55e',
    whale: '#3b82f6',
    res_scalp: '#f97316'
  };

  var order = ['snipe', 'res_scalp', 'maker', 'whale', 'taker'];
  var rows = '';

  for (var i = 0; i < order.length; i++) {
    var key = order[i];
    var e = data.engines[key];
    if (!e) continue;
    var color = colors[key] || '#888';
    var pnlColor = e.pnl > 0 ? '#22c55e' : (e.pnl < 0 ? '#ef4444' : 'var(--text-muted)');
    var wrColor = e.win_rate >= 55 ? '#22c55e' : (e.win_rate >= 45 ? '#eab308' : '#ef4444');
    var extra = '';
    if (key === 'maker' && e.rebate !== undefined) {
      extra = ' title="Rebate: $' + e.rebate.toFixed(4) + '"';
    }
    if (key === 'whale' && e.tracked_wallets !== undefined) {
      extra = ' title="Tracking ' + e.tracked_wallets + ' wallets"';
    }

    rows += '<tr style="border-bottom:1px solid rgba(255,255,255,0.05);"' + extra + '>';
    rows += '<td style="padding:8px 12px;"><span style="color:' + color + ';font-weight:600;">' + e.name + '</span></td>';
    rows += '<td style="padding:8px 10px;text-align:center;">' + e.allocation_pct + '%</td>';
    if (key === 'maker') {
      rows += '<td style="padding:8px 10px;text-align:center;">' + (e.trades > 0 ? e.trades + ' fills' : '') + '<span style="color:#8b5cf6;font-weight:600;">' + (e.trades > 0 ? ' / ' : '') + e.pending + ' quoting</span></td>';
      rows += '<td style="padding:8px 10px;text-align:center;color:#06b6d4;font-weight:600;">' + (e.inventory_count || 0) + ' pos</td>';
      rows += '<td style="padding:8px 10px;text-align:center;color:#eab308;font-weight:600;">$' + (e.exposure_usd || 0).toFixed(0) + '</td>';
      rows += '<td style="padding:8px 10px;text-align:center;color:var(--text-muted);">' + (e.trades > 0 ? e.win_rate.toFixed(1) + '%' : 'PAPER') + '</td>';
      rows += '<td style="padding:8px 10px;text-align:right;color:' + pnlColor + ';font-weight:600;">$' + (e.pnl >= 0 ? '+' : '') + e.pnl.toFixed(2) + '</td>';
      rows += '<td style="padding:8px 10px;text-align:right;color:#eab308;">$' + (e.inventory_value || 0).toFixed(0) + ' inv</td>';
    } else {
      rows += '<td style="padding:8px 10px;text-align:center;">' + e.trades + (e.pending > 0 ? '<span style="color:var(--text-muted);font-size:0.75rem;"> (' + e.pending + ' pending)</span>' : '') + '</td>';
      rows += '<td style="padding:8px 10px;text-align:center;color:#22c55e;">' + e.wins + '</td>';
      rows += '<td style="padding:8px 10px;text-align:center;color:#ef4444;">' + e.losses + '</td>';
      rows += '<td style="padding:8px 10px;text-align:center;color:' + wrColor + ';font-weight:600;">' + (e.trades > 0 ? e.win_rate.toFixed(1) + '%' : '--') + '</td>';
      rows += '<td style="padding:8px 10px;text-align:right;color:' + pnlColor + ';font-weight:600;">$' + (e.pnl >= 0 ? '+' : '') + e.pnl.toFixed(2) + '</td>';
      rows += '<td style="padding:8px 10px;text-align:right;">$' + e.avg_size.toFixed(0) + '</td>';
    }
    rows += '<td style="padding:8px 10px;text-align:center;"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + color + ';"></span></td>';
    rows += '</tr>';
  }
  body.innerHTML = rows;

  // Totals row
  if (totalsRow) {
    var t = data.totals;
    var tPnlColor = t.pnl > 0 ? '#22c55e' : (t.pnl < 0 ? '#ef4444' : 'var(--text-muted)');
    var tWrColor = t.win_rate >= 55 ? '#22c55e' : (t.win_rate >= 45 ? '#eab308' : '#ef4444');
    totalsRow.innerHTML = '<td style="padding:8px 10px;">TOTAL</td>'
      + '<td style="padding:8px;text-align:center;">100%</td>'
      + '<td style="padding:8px;text-align:center;">' + t.trades + '</td>'
      + '<td style="padding:8px;text-align:center;color:#22c55e;">' + t.wins + '</td>'
      + '<td style="padding:8px;text-align:center;color:#ef4444;">' + t.losses + '</td>'
      + '<td style="padding:8px;text-align:center;color:' + tWrColor + ';font-weight:700;">' + (t.trades > 0 ? t.win_rate.toFixed(1) + '%' : '--') + '</td>'
      + '<td style="padding:8px;text-align:right;color:' + tPnlColor + ';font-weight:700;">$' + (t.pnl >= 0 ? '+' : '') + t.pnl.toFixed(2) + '</td>'
      + '<td style="padding:8px;text-align:right;"></td>'
      + '<td style="padding:8px;text-align:center;"></td>';
  }
}


// ═══ Whale Follower Dashboard ═══
function loadWhaleFollower() {
  fetch('/api/garves/whale-status')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      // Status badge
      var badge = document.getElementById('whale-status-badge');
      if (badge) {
        if (d.enabled) {
          badge.textContent = 'ACTIVE';
          badge.style.background = 'rgba(34,197,94,0.15)';
          badge.style.color = '#22c55e';
        } else {
          badge.textContent = 'OFF';
          badge.style.background = 'rgba(255,255,255,0.06)';
          badge.style.color = 'var(--text-muted)';
        }
      }

      // Stats
      var el;
      el = document.getElementById('whale-tracked-wallets');
      if (el) el.textContent = d.tracked_wallets || 0;
      el = document.getElementById('whale-tracked-count');
      if (el) el.textContent = (d.tracked_wallets || 0) + ' whales';
      el = document.getElementById('whale-engine-count-badge');
      if (el) el.textContent = (d.tracked_wallets || 0) + ' whales';
      el = document.getElementById('whale-copy-count');
      if (el) el.textContent = (d.performance || {}).total_copies || 0;
      var wr = (d.performance || {}).win_rate || 0;
      el = document.getElementById('whale-win-rate');
      if (el) el.textContent = wr > 0 ? wr.toFixed(1) + '%' : '--';
      var pnl = (d.performance || {}).total_pnl || 0;
      el = document.getElementById('whale-total-pnl');
      if (el) {
        el.textContent = '$' + (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
        el.style.color = pnl > 0 ? '#22c55e' : (pnl < 0 ? '#ef4444' : 'var(--text-muted)');
      }

      // Top wallets table
      var tbody = document.getElementById('whale-top-wallets-body');
      if (tbody && d.top_wallets) {
        var html = '';
        var wallets = d.top_wallets.slice(0, 10);
        for (var i = 0; i < wallets.length; i++) {
          var w = wallets[i];
          var pnlColor = w.pnl > 0 ? '#22c55e' : '#ef4444';
          html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">';
          html += '<td style="padding:4px 8px;font-size:0.68rem;">' + (w.username || w.wallet) + '</td>';
          html += '<td style="padding:4px 6px;text-align:center;font-size:0.68rem;">' + (w.score || 0).toFixed(0) + '</td>';
          html += '<td style="padding:4px 6px;text-align:center;font-size:0.68rem;">' + (w.trades || 0) + '</td>';
          html += '<td style="padding:4px 6px;text-align:right;font-size:0.68rem;color:' + pnlColor + ';">$' + ((w.pnl || 0)/1000).toFixed(1) + 'K</td>';
          html += '</tr>';
        }
        tbody.innerHTML = html;
      }

      // Signals + backtest
      el = document.getElementById('whale-signals-count');
      if (el) el.textContent = d.active_signals || 0;
      el = document.getElementById('whale-ticks');
      if (el) el.textContent = d.tick_count || 0;

      var bt = d.backtest || {};
      el = document.getElementById('whale-backtest-status');
      if (el) {
        if (bt.passed) {
          el.innerHTML = '<span style="color:#22c55e;">PASSED</span> (' + (bt.total_trades || 0) + ' trades, ' + (bt.win_rate || 0).toFixed(0) + '% WR, $' + (bt.total_pnl || 0).toFixed(0) + ')';
        } else {
          el.innerHTML = '<span style="color:#eab308;">Pending</span>';
        }
      }
    })
    .catch(function() {});
}
