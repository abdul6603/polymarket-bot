var currentTab = 'overview';
var chatLoaded = false;
var econPeriod = 'month';
var _atlasBgCache = null;
var _overviewCache = null;
var AGENT_COLORS = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',mercury:'#ff8800',sentinel:'#00ff44',thor:'#ff6600'};
var AGENT_INITIALS = {garves:'GA',soren:'SO',shelby:'SH',atlas:'AT',mercury:'LI',sentinel:'RO',thor:'TH'};
var AGENT_ROLES = {garves:'Trading Bot',soren:'Content Creator',shelby:'Team Leader',atlas:'Data Scientist',mercury:'Social Media',sentinel:'Health Monitor',thor:'Coding Lieutenant'};
var AGENT_NAMES = {garves:'Garves',soren:'Soren',shelby:'Shelby',atlas:'Atlas',mercury:'Lisa',sentinel:'Robotox',thor:'Thor'};

function switchTab(tab) {
  currentTab = tab;
  var tabs = document.querySelectorAll('.tab-content');
  for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove('active');
  var btns = document.querySelectorAll('.sidebar-btn');
  for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
  var el = document.getElementById('tab-' + tab);
  if (el) el.classList.add('active');
  var btn = document.querySelector('.sidebar-btn[data-tab="' + tab + '"]');
  if (btn) btn.classList.add('active');
  refresh();
}

function wrColor(wr) { return wr >= 50 ? 'var(--success)' : wr >= 40 ? 'var(--warning)' : 'var(--error)'; }
function esc(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function pct(w, l) { var t = w + l; return t > 0 ? (w / t * 100).toFixed(1) + '%' : '--'; }

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
  var brainAgentMap = {garves:'garves',soren:'soren',shelby:'shelby',atlas:'atlas',mercury:'lisa',sentinel:'robotox',thor:'thor'};
  var cards = [
    {id:'garves', stats:[['Win Rate',(g.win_rate||0)+'%'],['Trades',g.total_trades||0],['Pending',g.pending||0]], online:g.running},
    {id:'soren', stats:[['Queue',s.queue_pending||0],['Posted',s.total_posted||0]], online:true},
    {id:'shelby', stats:[['Status',sh.running?'Online':'Offline']], online:sh.running},
    {id:'atlas', stats:[['Status','Active']], online:true},
    {id:'mercury', stats:[['Posts',(overview.mercury||{}).total_posts||0],['Review Avg',(overview.mercury||{}).review_avg ? (overview.mercury.review_avg+'/10') : '--']], online:true},
    {id:'sentinel', stats:[['Role','Monitor']], online:true},
    {id:'thor', stats:[['Tasks',(overview.thor||{}).completed||0],['Queue',(overview.thor||{}).pending||0]], online:(overview.thor||{}).state !== 'offline'}
  ];
  var html = '';
  for (var i = 0; i < cards.length; i++) {
    var c = cards[i];
    var brainKey = brainAgentMap[c.id] || c.id;
    var brainCount = _brainCountsCache[brainKey] || 0;
    html += '<div class="agent-card" data-agent="' + c.id + '" onclick="switchTab(&apos;' + c.id + '&apos;)">';
    html += '<div class="agent-card-header"><span class="agent-card-name">' + (AGENT_NAMES[c.id] || c.id.charAt(0).toUpperCase() + c.id.slice(1)) + '</span>';
    if (brainCount > 0) {
      html += '<span class="brain-badge" title="' + brainCount + ' brain note' + (brainCount > 1 ? 's' : '') + '">' + brainCount + '</span>';
    }
    html += '<span class="status-dot ' + (c.online !== false ? 'online' : 'offline') + '"></span></div>';
    html += '<div class="agent-card-role">' + (AGENT_ROLES[c.id] || '') + '</div>';
    html += '<div class="agent-card-stats">';
    for (var j = 0; j < c.stats.length; j++) {
      html += '<div class="agent-card-stat"><span class="label">' + c.stats[j][0] + '</span><span>' + c.stats[j][1] + '</span></div>';
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

async function generate4hReport() {
  var el = document.getElementById('report-4h-content');
  el.textContent = 'Generating report...';
  try {
    var resp = await fetch('/api/garves/report-4h');
    var data = await resp.json();
    if (data.report) { el.textContent = data.report; }
    else if (data.error) { el.textContent = 'Error: ' + data.error; }
    else { el.textContent = JSON.stringify(data, null, 2); }
  } catch (e) { el.textContent = 'Error: ' + e.message; }
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
  document.getElementById('live-winrate').textContent = (s.win_rate || 0) + '%';
  document.getElementById('live-winrate').style.color = wrColor(s.win_rate || 0);
  document.getElementById('live-pnl').textContent = '$' + (s.pnl || 0).toFixed(2);
  document.getElementById('live-pnl').style.color = (s.pnl || 0) >= 0 ? 'var(--success)' : 'var(--error)';
  document.getElementById('live-wins-losses').textContent = (s.wins || 0) + ' / ' + (s.losses || 0);
  document.getElementById('live-total').textContent = s.total_trades || 0;
  document.getElementById('live-resolved').textContent = s.resolved || 0;
  document.getElementById('live-pending').textContent = s.pending || 0;
}

function renderLivePendingTrades(trades) {
  var el = document.getElementById('live-pending-tbody');
  if (!trades || trades.length === 0) { el.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:24px;">No pending live trades</td></tr>'; return; }
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

function renderLiveResolvedTrades(trades) {
  var el = document.getElementById('live-resolved-tbody');
  if (!trades || trades.length === 0) { el.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:24px;">No resolved live trades yet</td></tr>'; return; }
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

async function loadConvictionData() {
  try {
    var resp = await fetch('/api/garves/conviction');
    var data = await resp.json();
    if (data.error) return;
    var es = data.engine_status || {};
    document.getElementById('conv-rolling-wr').textContent = es.rolling_wr || 'N/A';
    var streak = es.current_streak || 0;
    var streakEl = document.getElementById('conv-streak');
    streakEl.textContent = (streak > 0 ? '+' : '') + streak;
    streakEl.style.color = streak > 0 ? 'var(--success)' : streak < 0 ? 'var(--error)' : 'var(--text-secondary)';
    var dpEl = document.getElementById('conv-daily-pnl');
    dpEl.textContent = es.daily_pnl || '$0.00';
    dpEl.style.color = (es.daily_pnl || '').indexOf('-') !== -1 ? 'var(--error)' : 'var(--success)';
    document.getElementById('conv-total-resolved').textContent = es.total_resolved || 0;
    // Asset signals cards
    var assets = es.asset_signals || {};
    var assetHtml = '';
    var assetKeys = ['bitcoin','ethereum','solana'];
    for (var i = 0; i < assetKeys.length; i++) {
      var ak = assetKeys[i];
      var sig = assets[ak] || {};
      var label = ak.charAt(0).toUpperCase() + ak.slice(1);
      if (sig.status === 'no_signal') {
        assetHtml += '<div class="glass-card" style="text-align:center;"><h4 style="margin:0 0 4px;font-size:0.76rem;color:var(--text-secondary);">' + label + '</h4><span class="text-muted">No signal</span></div>';
      } else {
        var dirColor = sig.direction === 'up' ? 'var(--success)' : 'var(--error)';
        assetHtml += '<div class="glass-card" style="text-align:center;"><h4 style="margin:0 0 4px;font-size:0.76rem;color:var(--agent-garves);">' + label + '</h4>';
        assetHtml += '<div style="font-size:1.1rem;font-weight:700;color:' + dirColor + ';">' + (sig.direction || '').toUpperCase() + '</div>';
        assetHtml += '<div style="font-size:0.68rem;color:var(--text-secondary);">Consensus: ' + (sig.consensus || '--') + ' | Edge: ' + (sig.edge || '--') + '</div>';
        var badges = '';
        if (sig.volume_spike) badges += '<span class="badge badge-success" style="font-size:0.6rem;margin:2px;">VOL</span>';
        if (sig.temporal_arb) badges += '<span class="badge badge-success" style="font-size:0.6rem;margin:2px;">ARB</span>';
        if (badges) assetHtml += '<div style="margin-top:3px;">' + badges + '</div>';
        assetHtml += '</div>';
      }
    }
    document.getElementById('conv-asset-signals').innerHTML = assetHtml;
    // Indicator weights table
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
  var items = data.items || [];
  var el = document.getElementById('soren-queue');
  if (items.length === 0) { el.innerHTML = '<div class="text-muted" style="padding:var(--space-6);text-align:center;">No content in queue.</div>'; return; }
  var html = '';
  for (var i = 0; i < items.length && i < 20; i++) {
    var item = items[i];
    var id = item.id || i;
    var status = item.status || 'pending';
    var bcls = status === 'posted' ? 'badge-success' : status === 'failed' ? 'badge-error' : 'badge-warning';
    html += '<div class="queue-item">';
    html += '<div class="queue-item-header"><span class="queue-item-title">' + esc(item.title || item.pillar || 'Content #' + id) + '</span>';
    html += '<div class="queue-item-badges"><span class="badge ' + bcls + '">' + esc(status) + '</span>';
    if (item.freshness) html += freshnessBadge(item.freshness);
    if (item.platform) html += '<span class="badge badge-neutral">' + esc(item.platform) + '</span>';
    if (item.pillar) html += '<span class="badge badge-neutral">' + esc(item.pillar) + '</span>';
    html += '</div></div>';
    if (item.content) html += '<div class="queue-item-preview">' + esc((item.content || '').substring(0, 120)) + '</div>';
    if (status === 'pending') {
      html += '<div class="queue-item-actions">';
      html += '<button class="btn btn-primary" onclick="sorenGenerate(&apos;' + id + '&apos;,&apos;full&apos;)">Generate</button>';
      html += '<button class="btn" onclick="sorenGenerate(&apos;' + id + '&apos;,&apos;caption&apos;)">Caption Only</button>';
      html += '<button class="btn btn-success" onclick="sorenApprove(&apos;' + id + '&apos;)">Approve</button>';
      html += '<button class="btn btn-error" onclick="sorenReject(&apos;' + id + '&apos;)">Reject</button>';
      html += '</div>';
    } else if (status === 'approved' || status === 'generated') {
      html += '<div class="queue-item-actions">';
      html += '<button class="btn btn-primary" onclick="sorenPreview(&apos;' + id + '&apos;)">Preview</button>';
      html += '<button class="btn" onclick="sorenDownload(&apos;' + id + '&apos;)">Download</button>';
      html += '<button class="btn" style="color:var(--agent-mercury);" onclick="sorenBrandCheck(&apos;' + id + '&apos;,this)">Brand Check</button>';
      html += '</div>';
    }
    html += '</div>';
  }
  el.innerHTML = html;
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
    var resp = await fetch('/api/mercury/review/' + encodeURIComponent(itemId), {
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
        if (data.suggested_fix) dtxt += '<br><span style="color:var(--agent-mercury);">Fix:</span> ' + esc(data.suggested_fix);
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

function renderShelby(data) {
  document.getElementById('shelby-tasks-total').textContent = data.tasks_total || 0;
  // Agents working = tasks in progress
  var tasks = data.tasks || [];
  var agentsWorking = 0;
  var agentNames = ['garves','soren','atlas','mercury','sentinel'];
  for (var i = 0; i < tasks.length; i++) {
    if (tasks[i].status === 'in_progress' || tasks[i].status === 'pending') {
      var assignee = (tasks[i].agent || tasks[i].assigned_to || '').toLowerCase();
      if (agentNames.indexOf(assignee) !== -1) agentsWorking++;
    }
  }
  document.getElementById('shelby-agents-working').textContent = agentsWorking;
  // Render agent task cards
  var el = document.getElementById('shelby-agent-tasks');
  var html = '';
  for (var a = 0; a < agentNames.length; a++) {
    var name = agentNames[a];
    var agentTasks = [];
    for (var i = 0; i < tasks.length; i++) {
      var assignee = (tasks[i].agent || tasks[i].assigned_to || '').toLowerCase();
      if (assignee === name) agentTasks.push(tasks[i]);
    }
    var done = 0; var total = agentTasks.length;
    for (var i = 0; i < agentTasks.length; i++) {
      if (agentTasks[i].status === 'done' || agentTasks[i].status === 'completed') done++;
    }
    var pct = total > 0 ? Math.round(done / total * 100) : 0;
    var color = AGENT_COLORS[name] || 'var(--text)';
    html += '<div class="glass-card" style="padding:var(--space-4) var(--space-5);margin-bottom:var(--space-2);">';
    html += '<div class="flex items-center justify-between mb-4">';
    html += '<span style="font-family:var(--font-mono);font-size:0.82rem;font-weight:600;color:' + color + ';">' + name.charAt(0).toUpperCase() + name.slice(1) + '</span>';
    html += '<span class="badge badge-neutral">' + done + '/' + total + ' done</span></div>';
    html += '<div class="progress-bar"><div class="progress-fill" style="width:' + pct + '%;background:' + color + ';"></div></div>';
    if (agentTasks.length > 0) {
      html += '<div style="margin-top:var(--space-3);">';
      for (var i = 0; i < agentTasks.length && i < 3; i++) {
        var t = agentTasks[i];
        var st = t.status || 'pending';
        var dotCls = st === 'done' || st === 'completed' ? 'online' : st === 'in_progress' ? 'idle' : 'offline';
        html += '<div class="task-item" style="margin-bottom:2px;"><span class="status-dot ' + dotCls + '"></span>';
        html += '<span class="task-item-text">' + esc(t.title || t.description || 'Task') + '</span></div>';
      }
      html += '</div>';
    }
    html += '<div class="inline-task-input" id="task-input-' + name + '">';
    html += '<input type="text" class="input" id="task-text-' + name + '" placeholder="New task..." style="flex:1;" />';
    html += '<button class="btn btn-success" onclick="submitAgentTask(&apos;' + name + '&apos;)">Add</button>';
    html += '</div>';
    html += '<button class="btn" style="margin-top:var(--space-2);font-size:0.7rem;" onclick="toggleTaskInput(&apos;' + name + '&apos;)">+ Add Task</button>';
    html += '</div>';
  }
  el.innerHTML = html;
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
  } catch (e) {}
}

function setEconPeriod(p) { econPeriod = p; loadEconomics(); }

async function loadEconomics() {
  try {
    var resp = await fetch('/api/shelby/economics?period=' + econPeriod);
    var data = await resp.json();
    var el = document.getElementById('shelby-economics');
    var agents = data.agents || {};
    var agentNames = ['garves','soren','atlas','mercury','sentinel'];
    var html = '<div class="econ-row" style="font-weight:600;color:var(--text-muted);font-size:0.68rem;text-transform:uppercase;letter-spacing:0.06em;">';
    html += '<span style="flex:1;">Agent</span><span style="width:100px;text-align:right;">Cost</span><span style="width:100px;text-align:right;">Revenue</span><span style="width:100px;text-align:right;">Net</span></div>';
    for (var i = 0; i < agentNames.length; i++) {
      var name = agentNames[i];
      var a = agents[name] || {costs:0,revenue:0};
      var net = (a.revenue || 0) - (a.costs || 0);
      html += '<div class="econ-row"><span style="flex:1;color:' + (AGENT_COLORS[name]||'var(--text)') + ';font-weight:500;">' + name.charAt(0).toUpperCase() + name.slice(1) + '</span>';
      html += '<span style="width:100px;text-align:right;color:var(--error);">$' + (a.costs||0).toFixed(2) + '</span>';
      html += '<span style="width:100px;text-align:right;color:var(--success);">$' + (a.revenue||0).toFixed(2) + '</span>';
      html += '<span style="width:100px;text-align:right;color:' + (net >= 0 ? 'var(--success)' : 'var(--error)') + ';">$' + net.toFixed(2) + '</span></div>';
    }
    html += '<div class="econ-row" style="border-top:1px solid var(--border);margin-top:var(--space-2);padding-top:var(--space-3);font-weight:600;">';
    html += '<span style="flex:1;">Total (' + esc(econPeriod) + ')</span>';
    html += '<span style="width:100px;text-align:right;color:var(--error);">$' + (data.total_cost||0).toFixed(2) + '</span>';
    html += '<span style="width:100px;text-align:right;color:var(--success);">$' + (data.total_revenue||0).toFixed(2) + '</span>';
    html += '<span style="width:100px;text-align:right;color:' + ((data.net||0) >= 0 ? 'var(--success)' : 'var(--error)') + ';">$' + (data.net||0).toFixed(2) + '</span></div>';
    el.innerHTML = html;
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
    var total = lm.agents_total || 7;
    agentsFedEl.textContent = fed + '/' + total;
    agentsFedEl.style.color = fed >= 5 ? 'var(--success)' : fed >= 3 ? 'var(--warning)' : 'var(--error)';
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
  // Cost panel
  var costEl = document.getElementById('atlas-cost-panel');
  if (costEl && data.costs) {
    var c = data.costs;
    var totalProj = (c.total_cost_projected||0);
    var totalBudget = (c.total_budget||140);
    var budgetColor = totalProj > totalBudget ? 'var(--error)' : totalProj > totalBudget * 0.8 ? 'var(--warning)' : 'var(--success)';
    costEl.innerHTML = '<div style="display:flex;gap:20px;flex-wrap:wrap;">' +
      '<div><span class="stat-label">TODAY</span><div style="font-size:1.1rem;font-weight:600;">Tavily: ' + (c.today_tavily||0) + ' researches | OpenAI: ' + (c.today_openai||0) + ' analyses</div></div>' +
      '<div><span class="stat-label">THIS MONTH</span><div style="font-size:1.1rem;font-weight:600;">Tavily: ' + (c.month_tavily||0) + ' researches | OpenAI: ' + (c.month_openai||0) + ' analyses</div></div>' +
      '<div><span class="stat-label">PROJECTED MONTHLY</span><div style="font-size:1.1rem;font-weight:600;">Tavily: ' + (c.projected_tavily||0) + ' / 12,000 researches | OpenAI: ' + (c.projected_openai||0) + ' analyses</div></div>' +
      '<div><span class="stat-label">PROJECTED COST</span><div style="font-size:1.1rem;font-weight:600;color:' + budgetColor + ';">$' + totalProj.toFixed(2) + ' / $' + totalBudget.toFixed(2) + ' budget</div>' +
      '<div style="font-size:0.8rem;color:var(--text-secondary);">Tavily $' + (c.tavily_cost_projected||0).toFixed(2) + ' / $' + (c.tavily_budget||90) + ' | OpenAI $' + (c.openai_cost_projected||0).toFixed(2) + ' / $' + (c.openai_budget||50) + '</div></div>' +
      '</div>';
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
      var agentColors = {garves:'var(--agent-garves)',soren:'var(--agent-soren)',shelby:'var(--agent-shelby)',lisa:'var(--agent-mercury)',atlas:'var(--agent-atlas)'};
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

function atlasPriorityBadge(p) {
  var lvl = (p||'medium').toLowerCase();
  var cls = 'atlas-priority atlas-priority--' + ({'critical':'critical','high':'high','medium':'medium','low':'low'}[lvl]||'medium');
  return '<span class="' + cls + '">' + esc(lvl) + '</span>';
}
function atlasAgentColor(agent) {
  return {garves:'var(--agent-garves)',soren:'var(--agent-soren)',shelby:'var(--agent-shelby)',mercury:'var(--agent-mercury)',lisa:'var(--agent-mercury)',atlas:'var(--agent-atlas)',thor:'var(--agent-thor)',robotox:'var(--agent-sentinel)',sentinel:'var(--agent-sentinel)'}[agent] || 'var(--text-secondary)';
}
function atlasAgentLabel(agent) {
  return {garves:'Garves',soren:'Soren',shelby:'Shelby',mercury:'Lisa',lisa:'Lisa',atlas:'Atlas',thor:'Thor',robotox:'Robotox',sentinel:'Robotox'}[agent] || agent;
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
    var agents = ['garves','soren','shelby','mercury'];
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
    var agents = ['garves','soren','shelby','mercury'];
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
  el.innerHTML = '<div class="atlas-report-loading"><div class="spinner"></div>Evaluating agent hub vs best practices...</div>';
  try {
    var resp = await fetch('/api/atlas/hub-eval');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<div class="atlas-report-section"><div class="section-body" style="color:var(--error);">' + esc(data.error) + '</div></div>'; return; }
    var html = '<div class="atlas-report-header" style="border-left-color:var(--warning);">Hub Evaluation — System vs Industry</div>';
    // Our system
    if (data.our_system) {
      var body = '<div class="atlas-kv-row"><span class="kv-key">Total Agents</span><span class="kv-val">' + data.our_system.total_agents + '</span></div>';
      body += '<div class="atlas-kv-row"><span class="kv-key">Architecture</span><span class="kv-val">' + esc(data.our_system.architecture || '') + '</span></div>';
      if (data.our_system.features) {
        body += '<div style="margin-top:var(--space-3);font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:var(--space-2);">Features</div>';
        for (var i = 0; i < data.our_system.features.length; i++) {
          body += '<div class="atlas-rec-item"><span class="rec-num" style="color:var(--success);">&#x2713;</span><span class="rec-text">' + esc(data.our_system.features[i]) + '</span></div>';
        }
      }
      html += atlasSection('Our System', 'var(--agent-atlas)', body);
    }
    // Strengths
    if (data.strengths && data.strengths.length > 0) {
      var body = '';
      for (var i = 0; i < data.strengths.length; i++) {
        body += '<div class="atlas-rec-item"><span class="rec-num" style="color:var(--success);">&#x2713;</span><span class="rec-text">' + esc(data.strengths[i]) + '</span></div>';
      }
      html += atlasSection('Strengths', 'var(--success)', body);
    }
    // Gaps
    if (data.gaps && data.gaps.length > 0) {
      var body = '';
      for (var i = 0; i < data.gaps.length; i++) {
        body += '<div class="atlas-rec-item"><span class="rec-num" style="color:var(--error);">&#x2717;</span><span class="rec-text">' + esc(data.gaps[i]) + '</span></div>';
      }
      html += atlasSection('Gaps', 'var(--error)', body);
    }
    // Competitor insights
    if (data.competitor_insights && data.competitor_insights.length > 0) {
      var body = '';
      for (var i = 0; i < data.competitor_insights.length; i++) {
        var ci = data.competitor_insights[i];
        body += '<div class="atlas-rec-item"><span class="rec-num">' + (i+1) + '</span><span class="rec-text">' + esc(ci.title || ci.name || ci.snippet || JSON.stringify(ci).substring(0, 150)) + '</span></div>';
      }
      html += atlasSection('Competitor / Industry Intel', 'var(--agent-soren)', body);
    }
    // Research insights
    if (data.research_insights && data.research_insights.length > 0) {
      var body = '';
      for (var i = 0; i < data.research_insights.length; i++) {
        var ri = data.research_insights[i];
        body += '<div class="atlas-rec-item"><span class="rec-num">&#x1F50D;</span><span class="rec-text">' + esc(ri.insight || ri.query || JSON.stringify(ri).substring(0, 150)) + '</span></div>';
      }
      html += atlasSection('Research Insights', 'var(--agent-garves)', body);
    }
    // Recommendations
    if (data.recommendations && data.recommendations.length > 0) {
      var body = '';
      for (var i = 0; i < data.recommendations.length; i++) {
        var rec = data.recommendations[i];
        body += '<div class="atlas-rec-item">' + atlasPriorityBadge(rec.priority) + '<span class="rec-text">' + esc(rec.recommendation || '') + '</span></div>';
      }
      html += atlasSection('Recommendations', 'var(--warning)', body);
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

function mercuryScoreBadge(score) {
  if (score === null || score === undefined || score === -1) return '<span class="text-muted">--</span>';
  var color = score >= 7 ? '#22aa44' : score >= 4 ? '#ffaa00' : '#ff4444';
  var label = score >= 7 ? 'PASS' : score >= 4 ? 'WARN' : 'FAIL';
  return '<span style="color:' + color + ';font-weight:600;font-size:0.74rem;">' + score + '/10 ' + label + '</span>';
}

function renderMercury(data) {
  document.getElementById('mercury-outbox-stat').textContent = data.outbox_count || 0;
  document.getElementById('mercury-total-posted').textContent = data.total_posts || 0;
  var platforms = data.platforms || {};
  document.getElementById('mercury-platforms-count').textContent = Object.keys(platforms).length || 0;

  // Review stats
  var rs = data.review_stats || {};
  var avgEl = document.getElementById('mercury-review-avg');
  var statsEl = document.getElementById('mercury-review-stats');
  if (rs.total_reviewed) {
    avgEl.textContent = rs.avg_score + '/10';
    avgEl.style.color = rs.avg_score >= 7 ? '#22aa44' : rs.avg_score >= 4 ? '#ffaa00' : '#ff4444';
    statsEl.innerHTML = '<span style="color:#22aa44;">Passed: ' + rs.passed + '</span>' +
      '<span style="color:#ffaa00;">Warned: ' + rs.warned + '</span>' +
      '<span style="color:#ff4444;">Failed: ' + rs.failed + '</span>' +
      '<span class="text-muted">Total: ' + rs.total_reviewed + '</span>';
  } else {
    avgEl.textContent = '--';
    avgEl.style.color = '';
    statsEl.innerHTML = '<span class="text-muted">No reviews yet</span>';
  }

  // Outbox with review buttons
  var outbox = data.outbox || [];
  var obEl = document.getElementById('mercury-outbox-review');
  if (outbox.length === 0) {
    obEl.innerHTML = '<div class="text-muted">No approved items in outbox.</div>';
  } else {
    var obHtml = '<table class="data-table"><thead><tr><th>Pillar</th><th>Caption</th><th>Review</th></tr></thead><tbody>';
    for (var i = 0; i < outbox.length && i < 15; i++) {
      var item = outbox[i];
      var iid = item.id || '';
      var cap = (item.caption || item.content || '').substring(0, 80);
      obHtml += '<tr><td style="white-space:nowrap;">' + esc(item.pillar || '--') + '</td>';
      obHtml += '<td style="font-size:0.74rem;">' + esc(cap) + '</td>';
      obHtml += '<td style="white-space:nowrap;"><span id="ob-review-' + esc(iid) + '">';
      obHtml += '<button class="btn" style="font-size:0.7rem;padding:2px 8px;" onclick="mercuryReviewItem(&apos;' + esc(iid) + '&apos;)">Review</button>';
      obHtml += '</span></td></tr>';
    }
    obHtml += '</tbody></table>';
    obEl.innerHTML = obHtml;
  }

  // Recent posts with score column
  var posts = data.recent_posts || [];
  var tbody = document.getElementById('mercury-posts-tbody');
  if (posts.length === 0) { tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px;">No posts yet</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < posts.length && i < 15; i++) {
    var p = posts[i];
    var timeStr = (p.posted_at || '--').substring(0, 19).replace('T', ' ');
    html += '<tr><td style="white-space:nowrap;font-size:0.72rem;">' + esc(timeStr) + '</td>';
    html += '<td>' + esc(p.platform || '--') + '</td>';
    html += '<td style="font-size:0.74rem;">' + esc((p.caption || p.content || '').substring(0,60)) + '</td>';
    html += '<td>' + mercuryScoreBadge(p.review_score !== undefined ? p.review_score : null) + '</td>';
    html += '<td><span class="badge badge-success">' + esc(p.status || 'posted') + '</span></td></tr>';
  }
  tbody.innerHTML = html;
}

async function mercuryReviewCaption() {
  var textarea = document.getElementById('mercury-review-caption');
  var platform = document.getElementById('mercury-review-platform').value;
  var resultEl = document.getElementById('mercury-review-result');
  var caption = textarea.value.trim();
  if (!caption) { resultEl.innerHTML = '<span class="text-muted">Enter a caption to review.</span>'; return; }
  resultEl.innerHTML = '<span class="text-muted">Reviewing...</span>';
  try {
    var resp = await fetch('/api/mercury/review', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({caption: caption, platform: platform})
    });
    var data = await resp.json();
    if (data.error) { resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(data.error) + '</span>'; return; }
    var html = '<div style="padding:var(--space-3);border-left:3px solid ' + (data.score >= 7 ? '#22aa44' : data.score >= 4 ? '#ffaa00' : '#ff4444') + ';margin-bottom:var(--space-3);">';
    html += '<div style="margin-bottom:var(--space-2);">' + mercuryScoreBadge(data.score) + '</div>';
    if (data.issues && data.issues.length > 0) {
      html += '<div style="color:var(--text-secondary);font-size:0.74rem;margin-bottom:var(--space-2);">Issues:</div>';
      for (var i = 0; i < data.issues.length; i++) {
        html += '<div style="color:var(--text-muted);font-size:0.72rem;padding-left:var(--space-3);">- ' + esc(data.issues[i]) + '</div>';
      }
    }
    if (data.suggested_fix) {
      html += '<div style="margin-top:var(--space-3);padding:var(--space-3);background:rgba(255,136,0,0.08);border-radius:var(--radius-sm);font-size:0.74rem;">';
      html += '<div style="color:var(--agent-mercury);font-weight:600;margin-bottom:var(--space-1);">Suggested Fix:</div>';
      html += '<div style="color:var(--text-secondary);">' + esc(data.suggested_fix) + '</div></div>';
    }
    html += '</div>';
    resultEl.innerHTML = html;
  } catch (e) { resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(e.message) + '</span>'; }
}

async function mercuryReviewItem(itemId) {
  var spanEl = document.getElementById('ob-review-' + itemId);
  if (!spanEl) return;
  spanEl.innerHTML = '<span class="text-muted">Reviewing...</span>';
  try {
    var resp = await fetch('/api/mercury/review/' + encodeURIComponent(itemId), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({platform: 'instagram'})
    });
    var data = await resp.json();
    if (data.error) { spanEl.innerHTML = '<span style="color:#ff4444;font-size:0.72rem;">' + esc(data.error) + '</span>'; return; }
    var html = mercuryScoreBadge(data.score);
    if (data.issues && data.issues.length > 0) {
      html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">' + esc(data.issues.join(', ')) + '</div>';
    }
    spanEl.innerHTML = html;
  } catch (e) { spanEl.innerHTML = '<span style="color:#ff4444;font-size:0.72rem;">Error</span>'; }
}

async function loadMercuryPlan() {
  try {
    var resp = await fetch('/api/mercury/plan');
    var data = await resp.json();
    var el = document.getElementById('mercury-plan');
    if (data.error) { el.innerHTML = '<div class="text-muted">' + esc(data.error) + '</div>'; return; }
    var plan = data.plan || {};
    var phases = plan.phases || [];
    var html = '<div style="font-family:var(--font-mono);font-size:0.78rem;">';
    html += '<div style="color:var(--agent-mercury);font-weight:600;margin-bottom:var(--space-4);">Current Phase: ' + esc(plan.current_phase || '?') + '</div>';
    for (var i = 0; i < phases.length; i++) {
      var ph = phases[i];
      var isCurrent = ph.name === plan.current_phase;
      html += '<div style="padding:var(--space-3);margin-bottom:var(--space-2);border-left:2px solid ' + (isCurrent ? 'var(--agent-mercury)' : 'var(--border)') + ';padding-left:var(--space-4);">';
      html += '<div style="font-weight:600;color:' + (isCurrent ? 'var(--agent-mercury)' : 'var(--text-muted)') + ';">' + esc(ph.name || 'Phase ' + (i+1)) + '</div>';
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

async function loadMercuryKnowledge() {
  try {
    var resp = await fetch('/api/mercury/knowledge');
    var data = await resp.json();
    var el = document.getElementById('mercury-knowledge');
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

async function mercuryTestReply() {
  var input = document.getElementById('mercury-reply-input');
  var result = document.getElementById('mercury-reply-result');
  var comment = input.value.trim();
  if (!comment) return;
  result.textContent = 'Thinking...';
  try {
    var resp = await fetch('/api/mercury/reply', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({comment:comment})});
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

function renderSentinel(data) {
  if (data.error || data.status === 'offline') {
    document.getElementById('sentinel-agents-online').textContent = '--';
    document.getElementById('sentinel-agents-online').style.color = 'var(--error)';
    return;
  }
  document.getElementById('sentinel-agents-online').textContent = data.agents_online || 0;
  document.getElementById('sentinel-agents-online').style.color = 'var(--success)';
  document.getElementById('sentinel-active-issues').textContent = data.active_issues || 0;
  document.getElementById('sentinel-active-issues').style.color = (data.active_issues||0) > 0 ? 'var(--error)' : 'var(--success)';
  document.getElementById('sentinel-total-fixes').textContent = data.total_fixes || 0;
  document.getElementById('sentinel-last-scan').textContent = data.last_scan ? new Date(data.last_scan).toLocaleTimeString() : '--';

  // Error trend
  var trendEl = document.getElementById('sentinel-error-trend');
  if (trendEl && data.error_trend) {
    var et = data.error_trend;
    var arrow = et.direction === 'up' ? '&#9650;' : et.direction === 'down' ? '&#9660;' : '&#9644;';
    var arrowColor = et.spike ? 'var(--error)' : et.direction === 'up' ? 'var(--warning)' : et.direction === 'down' ? 'var(--success)' : 'var(--text-secondary)';
    var spikeTag = et.spike ? ' <span class="badge badge-error">SPIKE</span>' : '';
    trendEl.innerHTML = '<span style="font-size:0.8rem;color:var(--text-secondary);">Error Trend: </span>' +
      '<span style="font-size:1.2rem;color:' + arrowColor + ';">' + arrow + '</span> ' +
      '<span style="font-size:0.85rem;">' + (et.latest !== undefined ? et.latest : '--') + ' errors</span>' +
      '<span style="font-size:0.75rem;color:var(--text-secondary);margin-left:8px;">(avg: ' + (et.avg_last_5||0) + ')</span>' + spikeTag;
  }

  var alerts = data.recent_alerts || [];
  var alertEl = document.getElementById('sentinel-alerts');
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

  var fixes = data.recent_fixes || [];
  var fixEl = document.getElementById('sentinel-fixes-tbody');
  if (fixes.length === 0) {
    fixEl.innerHTML = '<tr><td colspan="4" class="text-muted" style="text-align:center;padding:24px;">No fixes</td></tr>';
  } else {
    var html = '';
    for (var i = fixes.length - 1; i >= 0; i--) {
      var f = fixes[i];
      html += '<tr><td>' + esc(f.timestamp ? new Date(f.timestamp).toLocaleTimeString() : '--') + '</td>';
      html += '<td>' + esc(f.agent||'--') + '</td>';
      html += '<td>' + esc(f.action||'--') + '</td>';
      html += '<td><span class="badge ' + (f.success ? 'badge-success' : 'badge-error') + '">' + (f.success ? 'Fixed' : 'Failed') + '</span></td></tr>';
    }
    fixEl.innerHTML = html;
  }
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

async function loadChatHistory() {
  try {
    var resp = await fetch('/api/chat/history');
    var data = await resp.json();
    renderChatMessages(data.history || []);
    chatLoaded = true;
  } catch (e) {}
}

function renderChatMessages(history) {
  var container = document.getElementById('chat-messages');
  if (!history || history.length === 0) {
    container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:40px;font-family:var(--font-mono);font-size:0.78rem;">Send a message to talk with all agents.</div>';
    return;
  }
  var html = '';
  for (var i = 0; i < history.length; i++) {
    var msg = history[i];
    var isUser = msg.role === 'user';
    var agent = msg.agent || 'shelby';
    var color = isUser ? 'var(--agent-garves)' : (AGENT_COLORS[agent] || 'var(--text-secondary)');
    var initials = isUser ? 'YOU' : (AGENT_INITIALS[agent] || agent.substring(0,2).toUpperCase());
    var name = isUser ? 'You' : agent.charAt(0).toUpperCase() + agent.slice(1);
    html += '<div class="chat-msg' + (isUser ? ' user' : '') + '">';
    html += '<div class="chat-msg-avatar" style="color:' + color + ';border-color:' + color + ';">' + initials + '</div>';
    html += '<div class="chat-msg-body"><div style="font-size:0.68rem;color:' + color + ';margin-bottom:2px;font-weight:600;">' + esc(name) + '</div>';
    html += esc(msg.content || '').replace(/\\n/g, '<br>');
    html += '</div></div>';
  }
  container.innerHTML = html;
  container.scrollTop = container.scrollHeight;
}

function chatKeyDown(e) { if (e.key === 'Enter') sendMessage(); }

async function sendMessage() {
  var input = document.getElementById('chat-input');
  var btn = document.getElementById('chat-send-btn');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  btn.disabled = true;
  btn.textContent = 'Sending...';

  var container = document.getElementById('chat-messages');
  container.innerHTML += '<div class="chat-msg user"><div class="chat-msg-avatar" style="color:var(--agent-garves);">YOU</div><div class="chat-msg-body"><div style="font-size:0.68rem;color:var(--agent-garves);margin-bottom:2px;font-weight:600;">You</div>' + esc(msg) + '</div></div>';
  container.innerHTML += '<div id="chat-typing" style="color:var(--text-muted);padding:8px;font-family:var(--font-mono);font-size:0.72rem;">Agents are typing...</div>';
  container.scrollTop = container.scrollHeight;

  try {
    var agentSelect = document.getElementById('chat-agent-select').value;
    var resp = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:msg, agent:agentSelect})});
    var data = await resp.json();
    var typing = document.getElementById('chat-typing');
    if (typing) typing.remove();
    renderChatMessages(data.history || []);
  } catch (e) {
    var typing = document.getElementById('chat-typing');
    if (typing) typing.textContent = 'Error sending message.';
  }
  btn.disabled = false;
  btn.textContent = 'Send';
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
    var resp = await fetch('/api/shelby', {method:'GET'});
    var data = await resp.json();
    var tasks = data.tasks || [];
    tasks.push({title: text, agent: agent, status: 'pending', created: new Date().toISOString()});
    // We cannot POST to /api/shelby to add tasks unless there is an endpoint,
    // so write directly by informing the user
    input.value = '';
    var el = document.getElementById('task-input-' + agent);
    if (el) el.classList.remove('visible');
    alert('Task "' + text + '" queued for ' + agent.charAt(0).toUpperCase() + agent.slice(1) + '. (Save to tasks.json manually or via Shelby.)');
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
    var m = data.mercury || {};
    html += '<div class="activity-item"><span class="activity-agent" style="color:var(--agent-mercury);">Lisa</span>';
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
      html += '<span class="assessment-name" style="color:' + color + ';">' + name.charAt(0).toUpperCase() + name.slice(1) + '</span>';
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

async function loadShelbyOnlineCount() {
  try {
    var resp = await fetch('/api/sentinel');
    var data = await resp.json();
    document.getElementById('shelby-agents-online').textContent = data.agents_online || 0;
    document.getElementById('shelby-agents-online').style.color = (data.agents_online || 0) > 0 ? 'var(--success)' : 'var(--error)';
  } catch (e) {}
}

async function openKPIModal(agent) {
  var modal = document.getElementById('kpi-modal');
  var title = document.getElementById('kpi-modal-title');
  var content = document.getElementById('kpi-modal-content');
  title.textContent = agent.charAt(0).toUpperCase() + agent.slice(1) + ' - Performance KPIs';
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
      renderIndicatorAccuracy(data);
      loadInfrastructure();
      loadEventFeed();
      // Fetch atlas bg status for countdown on overview
      try {
        var bgResp = await fetch('/api/atlas/background/status');
        var bgData = await bgResp.json();
        _atlasBgCache = bgData;
        updateOverviewCountdown();
      } catch(e) {}
      // Re-render intel cards with fresh overview data
      if (_intelData) renderTeamIntelligence(_intelData);
      loadBrainNotes('claude');
      loadCommandTable('claude');
    } else if (currentTab === 'garves-live') {
      var resp = await fetch('/api/trades/live');
      var data = await resp.json();
      renderLiveStats(data);
      renderBreakdown('live-bd-asset', data.by_asset);
      renderBreakdown('live-bd-tf', data.by_timeframe);
      renderBreakdown('live-bd-dir', data.by_direction);
      renderLivePendingTrades(data.pending_trades);
      renderLiveResolvedTrades(data.recent_trades);
      fetch('/api/logs').then(function(r){return r.json();}).then(function(d){renderLiveLogs(d.lines);}).catch(function(){});
    } else if (currentTab === 'garves') {
      var resp = await fetch('/api/trades/sim');
      var data = await resp.json();
      renderGarvesStats(data);
      renderBreakdown('bd-asset', data.by_asset);
      renderBreakdown('bd-tf', data.by_timeframe);
      renderBreakdown('bd-dir', data.by_direction);
      renderPendingTrades(data.pending_trades);
      renderResolvedTrades(data.recent_trades);
      fetch('/api/logs').then(function(r){return r.json();}).then(function(d){renderLogs(d.lines);}).catch(function(){});
      loadRegimeBadge();
      loadConvictionData();
      loadDailyReports();
      loadDerivatives();
      loadAgentLearning('garves');
      loadNewsSentiment();
      loadBrainNotes('garves');
      loadCommandTable('garves');
      loadAgentSmartActions('garves');
    } else if (currentTab === 'soren') {
      var resp = await fetch('/api/soren');
      renderSoren(await resp.json());
      loadAgentLearning('soren');
      loadSorenCompetitors();
      loadBrainNotes('soren');
      loadCommandTable('soren');
      loadAgentSmartActions('soren');
    } else if (currentTab === 'shelby') {
      var resp = await fetch('/api/shelby');
      renderShelby(await resp.json());
      try { loadSchedule(); } catch(e) {}
      try { loadEconomics(); } catch(e) {}
      try { loadActivityBrief(); } catch(e) {}
      try { loadSystemInfo(); } catch(e) {}
      try { loadAssessments(); } catch(e) {}
      try { loadShelbyOnlineCount(); } catch(e) {}
      try { loadShelbyDecisions(); } catch(e) {}
      loadBrainNotes('shelby');
      loadCommandTable('shelby');
      loadAgentSmartActions('shelby');
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
      loadCompetitorIntel();
      loadAgentLearning('atlas');
      loadTradeAnalysis();
      loadBrainNotes('atlas');
      loadCommandTable('atlas');
      loadAgentSmartActions('atlas');
    } else if (currentTab === 'mercury') {
      var resp = await fetch('/api/mercury');
      renderMercury(await resp.json());
      loadMercuryPlan();
      loadMercuryKnowledge();
      loadAgentLearning('mercury');
      loadLisaGoLive();
      loadLisaCommentStats();
      loadBrainNotes('lisa');
      loadCommandTable('lisa');
      loadAgentSmartActions('lisa');
    } else if (currentTab === 'sentinel') {
      var resp = await fetch('/api/sentinel');
      renderSentinel(await resp.json());
      loadAgentLearning('sentinel');
      loadRobotoxDeps();
      loadLogWatcherAlerts();
      loadBrainNotes('robotox');
      loadCommandTable('robotox');
      loadAgentSmartActions('robotox');
    } else if (currentTab === 'thor') {
      loadThor();
      loadSmartActions();
      loadBrainNotes('thor');
      loadCommandTable('thor');
    } else if (currentTab === 'chat') {
      if (!chatLoaded) loadChatHistory();
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
setInterval(refresh, 5000);

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
    var m = o.mercury || {};
    s += (m.total_posts || 0) + ' posts';
    if (m.review_avg) s += ' <span class="aqs-sep">&middot;</span> ' + m.review_avg + '/10';
  } else if (key === 'robotox') {
    s += '<span class="aqs-dot aqs-dot-online"></span>Watching';
  } else if (key === 'thor') {
    var th = o.thor || {};
    s += (th.pending || 0) + ' pending <span class="aqs-sep">&middot;</span> ' + (th.completed || 0) + ' done';
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
  var agentScores = team.agent_scores || {};

  var iqLabel = overall >= 90 ? 'GENIUS' : overall >= 75 ? 'EXPERT' : overall >= 60 ? 'SKILLED' : overall >= 40 ? 'LEARNING' : 'NOVICE';
  var teamColor = overall >= 80 ? '#00ff88' : overall >= 60 ? '#22aa44' : overall >= 40 ? '#ffaa00' : '#ff4444';

  var dimKeys = Object.keys(dims);
  var dimValues = dimKeys.map(function(k) { return dims[k]; });

  var html = '<div style="display:flex;align-items:center;gap:20px;margin-bottom:14px;">';
  html += '<div style="flex-shrink:0;">' + radarSVG(160, dimValues, dimKeys, teamColor) + '</div>';
  html += '<div style="flex:1;">';
  html += '<div class="radar-title">Team Intelligence</div>';
  html += '<div style="display:flex;align-items:baseline;gap:8px;">';
  html += '<span class="radar-score" style="color:' + teamColor + ';">' + overall + '</span>';
  html += '<span class="radar-level" style="color:' + teamColor + ';">' + iqLabel + '</span>';
  html += '</div>';
  html += '<div class="radar-dims">';
  for (var i = 0; i < dimKeys.length; i++) {
    var val = dims[dimKeys[i]];
    var vc = val >= 75 ? 'var(--success)' : val >= 50 ? teamColor : val >= 30 ? 'var(--warning)' : 'var(--error)';
    html += '<div class="radar-dim"><span class="radar-dim-label">' + dimKeys[i] + '</span><span class="radar-dim-val" style="color:' + vc + ';">' + val + '</span></div>';
  }
  html += '</div></div></div>';

  var agentMeta = [
    {key:'atlas',  name:'Atlas',   color:'#22aa44'},
    {key:'garves', name:'Garves',  color:'#00d4ff'},
    {key:'soren',  name:'Soren',   color:'#cc66ff'},
    {key:'shelby', name:'Shelby',  color:'#ffaa00'},
    {key:'lisa',   name:'Lisa',    color:'#ff8800'},
    {key:'robotox',name:'Robotox', color:'#00ff44'},
    {key:'thor',   name:'Thor',    color:'#ff6600'},
  ];

  html += '<div class="team-agents-row">';
  for (var j = 0; j < agentMeta.length; j++) {
    var ag = agentMeta[j];
    var score = agentScores[ag.key] || 0;
    var agData = data[ag.key] || {};
    var agDims = agData.dimensions || {};
    var agDimKeys = Object.keys(agDims);
    var agDimValues = agDimKeys.map(function(k) { return agDims[k]; });
    var level = score >= 90 ? 'GENIUS' : score >= 75 ? 'EXPERT' : score >= 60 ? 'SKILLED' : score >= 40 ? 'LEARNING' : 'NOVICE';
    var levelColor = score >= 75 ? 'var(--success)' : score >= 50 ? ag.color : score >= 30 ? 'var(--warning)' : 'var(--error)';

    var tabKey = ag.key === 'lisa' ? 'mercury' : ag.key === 'robotox' ? 'sentinel' : ag.key;
    html += '<div class="team-agent-card" onclick="switchTab(\'' + tabKey + '\')" style="cursor:pointer;">';
    if (agDimValues.length > 0) {
      html += '<div style="margin:0 auto 2px;">' + radarSVG(68, agDimValues, null, ag.color) + '</div>';
    }
    html += '<div class="team-agent-name" style="color:' + ag.color + ';">' + ag.name + '</div>';
    html += '<div style="font-family:var(--font-mono);font-size:0.85rem;font-weight:700;color:' + ag.color + ';">' + score + '</div>';
    html += '<div class="team-agent-level" style="color:' + levelColor + ';">' + level + '</div>';
    html += agentQuickStatus(ag.key);
    html += '</div>';
  }
  html += '</div>';

  el.innerHTML = html;
}
var _intelAgentMap = {garves:'garves',soren:'soren',shelby:'shelby',atlas:'atlas',mercury:'lisa',sentinel:'robotox',thor:'thor'};
var _intelColors = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',lisa:'#ff8800',robotox:'#00ff44',thor:'#ff6600'};

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
    var m = o.mercury || {};
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

async function refreshIntelligence() {
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
};

// Load on startup + every 30s
refreshIntelligence();
setInterval(refreshIntelligence, 30000);

// ── Infrastructure Display ──

function healthBadge(h) {
  if (h === 'healthy') return '<span style="color:var(--success);">HEALTHY</span>';
  if (h === 'stale') return '<span style="color:var(--warning);">STALE</span>';
  return '<span style="color:var(--error);">DEAD</span>';
}

async function loadInfrastructure() {
  // Heartbeats
  try {
    var resp = await fetch('/api/heartbeats');
    var data = await resp.json();
    var hbs = data.heartbeats || {};
    var keys = Object.keys(hbs);
    var aliveCount = 0;
    var html = '';
    for (var i = 0; i < keys.length; i++) {
      var name = keys[i];
      var hb = hbs[name];
      if (hb.health === 'healthy') aliveCount++;
      var ageStr = hb.age_seconds < 60 ? hb.age_seconds + 's ago' :
                   hb.age_seconds < 3600 ? Math.floor(hb.age_seconds / 60) + 'm ago' :
                   Math.floor(hb.age_seconds / 3600) + 'h ago';
      html += '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);">';
      html += '<span>' + esc(hb.display_name || name) + '</span>';
      html += '<span>' + healthBadge(hb.health) + ' <span class="text-muted" style="font-size:0.68rem;">' + ageStr + '</span></span>';
      html += '</div>';
    }
    if (!keys.length) html = '<span class="text-muted">No heartbeats yet. Agents will register shortly.</span>';
    var el = document.getElementById('infra-heartbeat-list');
    if (el) el.innerHTML = html;
    var hbEl = document.getElementById('infra-agents-alive');
    if (hbEl) hbEl.textContent = aliveCount + '/' + keys.length;
  } catch(e) {
    console.error('heartbeat load error:', e);
  }

  // System health
  try {
    var resp2 = await fetch('/api/system-health');
    var health = await resp2.json();
    var el2 = document.getElementById('infra-health');
    if (el2) {
      var overall = (health.overall || 'unknown').toUpperCase();
      var color = overall === 'HEALTHY' ? 'var(--success)' : overall === 'DEGRADED' ? 'var(--warning)' : 'var(--error)';
      el2.innerHTML = '<span style="color:' + color + ';">' + overall + '</span>';
    }
    var servEl = document.getElementById('infra-services');
    if (servEl) {
      var regKeys = Object.keys(health.registry || {});
      servEl.textContent = regKeys.length;
    }
  } catch(e) {}

  // Broadcasts
  try {
    var resp3 = await fetch('/api/broadcasts');
    var bcData = await resp3.json();
    var bcs = bcData.broadcasts || [];
    var bcCountEl = document.getElementById('infra-broadcasts');
    if (bcCountEl) bcCountEl.textContent = bcs.length;
    var bcListEl = document.getElementById('infra-broadcast-list');
    if (bcListEl) {
      var bcHtml = '';
      var recent = bcs.slice(-5).reverse();
      for (var b = 0; b < recent.length; b++) {
        var bc = recent[b];
        var acks = bc.acks || {};
        var ackCount = Object.keys(acks).length;
        var deliveredCount = (bc.delivered_to || []).length;
        var prioClass = bc.priority === 'high' ? 'color:var(--warning);' : 'color:var(--text-secondary);';
        var ts = bc.timestamp || '';
        var shortTs = ts.length > 16 ? ts.substring(11, 16) : ts;
        var msg = (bc.message || '').substring(0, 80);
        if ((bc.message || '').length > 80) msg += '...';
        bcHtml += '<div style="padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);">';
        bcHtml += '<div style="display:flex;justify-content:space-between;">';
        bcHtml += '<span style="' + prioClass + '">' + esc(bc.from || 'shelby') + '</span>';
        bcHtml += '<span class="text-muted" style="font-size:0.68rem;">' + esc(shortTs) + ' | ' + ackCount + '/' + deliveredCount + ' ack</span>';
        bcHtml += '</div>';
        bcHtml += '<div class="text-muted" style="font-size:0.68rem;margin-top:2px;">' + esc(msg) + '</div>';
        bcHtml += '</div>';
      }
      if (!recent.length) bcHtml = '<span class="text-muted">No broadcasts yet.</span>';
      bcListEl.innerHTML = bcHtml;
    }
  } catch(e) {}
}

// ═══════════════════════════════════════════════════════
// THOR — The Engineer
// ═══════════════════════════════════════════════════════
async function loadThor() {
  try {
    var resp = await fetch('/api/thor');
    var data = await resp.json();

    // Status widgets
    var stateEl = document.querySelector('#thor-state span:last-child');
    var modelEl = document.querySelector('#thor-model span:last-child');
    if (stateEl) {
      var state = data.state || 'offline';
      var stateColor = state === 'coding' ? 'var(--agent-thor)' : state === 'idle' ? 'var(--success)' : 'var(--text-muted)';
      stateEl.textContent = state.charAt(0).toUpperCase() + state.slice(1);
      stateEl.style.color = stateColor;
    }
    if (modelEl) modelEl.textContent = data.model || '--';

    // Stat cards
    var q = data.queue || {};
    setText('thor-queue-pending', q.pending || 0);
    setText('thor-completed', q.completed || 0);
    setText('thor-failed', q.failed || 0);
    setText('thor-knowledge', data.knowledge_entries || 0);

    // Tokens used
    var tokens = data.total_tokens || 0;
    var tokensDisplay = tokens >= 1000000 ? (tokens / 1000000).toFixed(1) + 'M' : tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'K' : tokens;
    setText('thor-tokens', tokensDisplay);

    // Success rate
    var totalDone = (q.completed || 0) + (q.failed || 0);
    var successRate = totalDone > 0 ? Math.round((q.completed || 0) / totalDone * 100) + '%' : '--';
    setText('thor-success-rate', successRate);

    // Current task
    if (data.current_task && data.state === 'coding') {
      setText('thor-queue-pending', (q.pending || 0) + ' (active: ' + data.current_task.substring(0, 30) + ')');
    }
  } catch(e) {
    setText('thor-queue-pending', '--');
  }

  // Load queue
  loadThorQueue();
  // Load results
  loadThorResults();
  // Load activity
  loadThorActivity();
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
    var resp = await fetch('/api/thor/smart-actions?agent=' + encodeURIComponent(agent));
    var data = await resp.json();
    var actions = data.actions || [];
    _agentSmartActionsCache[agent] = actions;
    if (actions.length === 0) {
      el.innerHTML = '<span class="text-muted" style="font-size:0.76rem;">No suggestions right now — all good.</span>';
      return;
    }
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
    showToast('Task submitted: ' + a.title, 'var(--agent-thor)');
    loadAgentSmartActions(agent);
  } catch(e) {
    alert('Failed: ' + e.message);
  }
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
    showToast('Task submitted: ' + a.title, 'var(--agent-thor)');
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
    showToast('Custom task submitted: ' + title.value.trim(), 'var(--agent-thor)');
    title.value = '';
    desc.value = '';
    if (files) files.value = '';
    loadThorQueue();
    loadThor();
  } catch(e) {
    alert('Failed to submit task: ' + e.message);
  }
}

function showToast(msg, color) {
  var existing = document.getElementById('cc-toast');
  if (existing) existing.remove();
  var toast = document.createElement('div');
  toast.id = 'cc-toast';
  toast.style.cssText = 'position:fixed;bottom:24px;right:24px;background:var(--surface-secondary);border:1px solid ' + (color || 'var(--success)') + ';color:var(--text-primary);padding:12px 20px;border-radius:8px;font-size:0.82rem;font-family:var(--font-mono);z-index:9999;box-shadow:0 4px 20px rgba(0,0,0,0.5);';
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(function() { toast.remove(); }, 3500);
}

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
    html += '<div class="stat-card" data-accent="mercury" style="padding:8px;"><div class="stat-value" style="font-size:1rem;">' + (stats.total || 0) + '</div><div class="stat-label">Analyzed</div></div>';
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
      el.innerHTML = '<div class="text-muted" style="text-align:center;padding:var(--space-4);">No brain notes yet.</div>';
      return;
    }
    var typeColors = {note: '#3b82f6', command: '#ef4444', memory: '#22c55e'};
    var typeLabels = {note: 'NOTE', command: 'CMD', memory: 'MEM'};
    var agentColor = AGENT_COLORS[agent] || '#3b82f6';
    var html = '';
    for (var i = notes.length - 1; i >= 0; i--) {
      var n = notes[i];
      var ts = n.created_at ? n.created_at.substring(0, 16).replace('T', ' ') : '';
      var ntype = n.type || 'note';
      var tcolor = typeColors[ntype] || '#3b82f6';
      var tlabel = typeLabels[ntype] || 'NOTE';
      var tags = (n.tags && n.tags.length > 0) ? n.tags.map(function(t) { return '<span style="background:rgba(255,255,255,0.08);padding:1px 6px;border-radius:4px;font-size:0.68rem;margin-right:4px;">' + esc(t) + '</span>'; }).join('') : '';
      html += '<div style="border-bottom:1px solid rgba(255,255,255,0.06);padding:8px 0;display:flex;justify-content:space-between;align-items:flex-start;">';
      html += '<div style="flex:1;">';
      html += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">';
      html += '<span style="background:' + tcolor + '22;color:' + tcolor + ';padding:1px 6px;border-radius:4px;font-size:0.62rem;font-weight:700;font-family:var(--font-mono);letter-spacing:0.05em;">' + tlabel + '</span>';
      html += '<span style="font-weight:600;font-size:0.82rem;color:var(--text-primary);">' + esc(n.topic) + '</span>';
      html += '</div>';
      html += '<div style="font-size:0.76rem;color:var(--text-secondary);white-space:pre-wrap;word-break:break-word;">' + esc(n.content) + '</div>';
      if (n.response) {
        html += '<div style="margin-top:6px;padding:6px 10px;background:rgba(0,0,0,0.3);border-left:3px solid ' + agentColor + ';border-radius:4px;">';
        html += '<div style="font-size:0.65rem;font-weight:600;color:' + agentColor + ';margin-bottom:2px;font-family:var(--font-mono);letter-spacing:0.05em;">AI RESPONSE</div>';
        html += '<div style="font-size:0.76rem;color:var(--text-secondary);font-style:italic;white-space:pre-wrap;word-break:break-word;">' + esc(n.response) + '</div>';
        html += '</div>';
      }
      html += '<div style="margin-top:4px;font-size:0.68rem;color:var(--text-muted);">' + ts + ' ' + tags + '</div>';
      html += '</div>';
      html += '<button onclick="deleteBrainNote(\'' + agent + '\',\'' + n.id + '\')" style="background:none;border:none;color:var(--error);cursor:pointer;font-size:1rem;padding:4px 8px;opacity:0.7;" title="Delete">&times;</button>';
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<span style="color:var(--error);">Failed to load: ' + esc(e.message) + '</span>';
  }
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
    if (typeEl) typeEl.value = 'note';
    // Show interpreting spinner then poll for response
    loadBrainNotes(agent);
    var noteId = data.note ? data.note.id : null;
    if (noteId) {
      var statusEl = document.getElementById(agent + '-brain-list');
      if (statusEl) {
        var spinner = document.createElement('div');
        spinner.id = 'brain-interpret-spinner';
        spinner.style.cssText = 'text-align:center;padding:8px;font-size:0.78rem;color:var(--text-secondary);font-style:italic;';
        spinner.textContent = 'Interpreting...';
        statusEl.insertBefore(spinner, statusEl.firstChild);
      }
      // Poll for interpretation result (background thread writes it)
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
var EVENT_AGENT_COLORS = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',lisa:'#ff8800',robotox:'#00ff44',thor:'#ff6600'};

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
