var currentTab = 'overview';
var chatLoaded = false;
var econPeriod = 'month';
var _atlasBgCache = null;
var _overviewCache = null;
var AGENT_COLORS = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',mercury:'#ff8800',sentinel:'#00ff44',thor:'#ff6600',hawk:'#FFD700',viper:'#00ff88'};
var AGENT_INITIALS = {garves:'GA',soren:'SO',shelby:'SH',atlas:'AT',mercury:'LI',sentinel:'RO',thor:'TH',hawk:'HK',viper:'VP'};
var AGENT_ROLES = {garves:'Trading Bot',soren:'Content Creator',shelby:'Team Leader',atlas:'Data Scientist',mercury:'Social Media',sentinel:'Health Monitor',thor:'Coding Lieutenant',hawk:'Market Predator',viper:'Opportunity Hunter'};
var AGENT_NAMES = {garves:'Garves',soren:'Soren',shelby:'Shelby',atlas:'Atlas',mercury:'Lisa',sentinel:'Robotox',thor:'Thor',hawk:'Hawk',viper:'Viper'};
var AGENT_AVATARS = {soren:'/static/soren_profile.png'};

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
  var brainAgentMap = {garves:'garves',soren:'soren',shelby:'shelby',atlas:'atlas',mercury:'lisa',sentinel:'robotox',thor:'thor',hawk:'hawk',viper:'viper'};
  var cards = [
    {id:'garves', stats:[['Win Rate',(g.win_rate||0)+'%'],['Trades',g.total_trades||0],['Pending',g.pending||0]], online:g.running},
    {id:'soren', stats:[['Queue',s.queue_pending||0],['Posted',s.total_posted||0]], online:true},
    {id:'shelby', stats:[['Status',sh.running?'Online':'Offline']], online:sh.running},
    {id:'atlas', stats:[['Status','Active']], online:true},
    {id:'mercury', stats:[['Posts',(overview.mercury||{}).total_posts||0],['Review Avg',(overview.mercury||{}).review_avg ? (overview.mercury.review_avg+'/10') : '--']], online:true},
    {id:'sentinel', stats:[['Role','Monitor']], online:true},
    {id:'thor', stats:[['Tasks',(overview.thor||{}).completed||0],['Queue',(overview.thor||{}).pending||0]], online:(overview.thor||{}).state !== 'offline'},
    {id:'hawk', stats:[['Win Rate',((overview.hawk||{}).win_rate||0)+'%'],['Open',(overview.hawk||{}).open_bets||0]], online:(overview.hawk||{}).running},
    {id:'viper', stats:[['Found',(overview.viper||{}).opportunities||0],['Pushed',(overview.viper||{}).pushed||0]], online:(overview.viper||{}).running}
  ];
  var html = '';
  for (var i = 0; i < cards.length; i++) {
    var c = cards[i];
    var brainKey = brainAgentMap[c.id] || c.id;
    var brainCount = _brainCountsCache[brainKey] || 0;
    html += '<div class="agent-card" data-agent="' + c.id + '" onclick="switchTab(&apos;' + c.id + '&apos;)">';
    html += '<div class="agent-card-header">';
    if (AGENT_AVATARS[c.id]) {
      html += '<img class="agent-card-avatar" src="' + AGENT_AVATARS[c.id] + '" alt="' + (AGENT_NAMES[c.id]||c.id) + '">';
    } else {
      var agentColor = AGENT_COLORS[c.id] || '#888';
      html += '<div class="agent-card-initial" style="background:' + agentColor + '22;color:' + agentColor + ';">' + (AGENT_INITIALS[c.id]||'??') + '</div>';
    }
    html += '<span class="agent-card-name">' + (AGENT_NAMES[c.id] || c.id.charAt(0).toUpperCase() + c.id.slice(1)) + '</span>';
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

// ── Shelby Task V2 ──
var _shelbyAllTasks = [];

function renderShelby(data) {
  var tasks = data.tasks || [];
  _shelbyAllTasks = tasks;
  var todayStr = new Date().toISOString().substring(0, 10);

  var highPri = 0, pending = 0, doneToday = 0, nextDue = null;
  for (var i = 0; i < tasks.length; i++) {
    var t = tasks[i], st = t.status || '';
    if (st !== 'done' && (t.priority || 0) > 70) highPri++;
    if (st === 'pending') pending++;
    if (st === 'done' && (t.completed || '').substring(0, 10) === todayStr) doneToday++;
    if (st !== 'done' && t.due && t.due.length >= 10 && (!nextDue || t.due < nextDue)) nextDue = t.due;
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

  if (sortVal === 'priority') tasks.sort(function(a, b) { return (b.priority || 0) - (a.priority || 0); });
  else if (sortVal === 'due') tasks.sort(function(a, b) { return (a.due || 'zzzz').localeCompare(b.due || 'zzzz'); });
  else if (sortVal === 'agent') tasks.sort(function(a, b) { return (a.agent || '').localeCompare(b.agent || ''); });
  else if (sortVal === 'created') tasks.sort(function(a, b) { return (b.created || '').localeCompare(a.created || ''); });

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
    var pri = t.priority || 0;
    var priColor = pri > 80 ? '#ef4444' : pri > 50 ? '#f59e0b' : '#64748b';
    var st = t.status || 'pending';
    var diff = t.difficulty || 2;
    var cat = t.category || 'ops';
    var agent = t.agent || '';
    var agentName = agent ? (AGENT_NAMES[agent] || agent.charAt(0).toUpperCase() + agent.slice(1)) : 'Unassigned';
    var agentColor = agent ? (AGENT_COLORS[agent] || 'var(--text)') : 'var(--text-muted)';
    var doneOpacity = st === 'done' ? 'opacity:0.45;' : '';
    var doneStrike = st === 'done' ? 'text-decoration:line-through;' : '';
    var dueStr = '';
    if (t.due && t.due.length >= 10) {
      var today = new Date(); today.setHours(0,0,0,0);
      var dueDate = new Date(t.due.substring(0,10) + 'T00:00:00');
      var daysDiff = Math.round((dueDate - today) / 86400000);
      if (daysDiff < 0) dueStr = '<span style="color:#ef4444;font-weight:600;">Overdue ' + Math.abs(daysDiff) + 'd</span>';
      else if (daysDiff === 0) dueStr = '<span style="color:#f59e0b;font-weight:600;">Due today</span>';
      else if (daysDiff === 1) dueStr = '<span style="color:#f59e0b;">Tomorrow</span>';
      else if (daysDiff <= 7) dueStr = '<span style="color:var(--text-muted);">' + t.due.substring(5,10) + ' (' + daysDiff + 'd)</span>';
      else dueStr = '<span style="color:var(--text-muted);">' + t.due.substring(5,10) + '</span>';
    }

    // Left border = priority color
    html += '<div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-left:3px solid ' + priColor + ';border-bottom:1px solid rgba(255,255,255,0.04);' + doneOpacity + '">';

    // Priority badge
    html += '<div style="min-width:36px;text-align:center;"><span style="background:' + priColor + '22;color:' + priColor + ';padding:3px 8px;border-radius:4px;font-weight:700;font-size:0.76rem;">' + pri + '</span></div>';

    // Main content
    html += '<div style="flex:1;min-width:0;">';
    html += '<div style="font-size:0.82rem;font-weight:500;' + doneStrike + 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(t.title || '') + '">' + esc(t.title || '') + '</div>';
    // Meta row: agent, category, difficulty, due
    html += '<div style="display:flex;gap:8px;align-items:center;margin-top:3px;flex-wrap:wrap;">';
    html += '<span style="color:' + agentColor + ';font-size:0.72rem;font-weight:600;">' + esc(agentName) + '</span>';
    html += '<span style="background:' + (catColors[cat] || '#94a3b8') + '18;color:' + (catColors[cat] || '#94a3b8') + ';padding:1px 6px;border-radius:3px;font-size:0.68rem;">' + esc(cat) + '</span>';
    html += '<span style="color:' + diffColors[diff] + ';font-size:0.7rem;">' + diffLabels[diff] + '</span>';
    if (dueStr) html += dueStr;
    html += '</div>';
    // Notes preview (dispatch results)
    if (t.notes) {
      var notesPreview = t.notes.length > 120 ? t.notes.substring(0, 117) + '...' : t.notes;
      var notesBg = st === 'done' ? 'rgba(16,185,129,0.08)' : 'rgba(59,130,246,0.08)';
      var notesColor = st === 'done' ? '#10b981' : '#3b82f6';
      html += '<div style="margin-top:4px;padding:3px 8px;background:' + notesBg + ';border-radius:4px;font-size:0.68rem;color:' + notesColor + ';line-height:1.4;cursor:pointer;white-space:pre-line;" onclick="this.textContent=this.dataset.full||this.textContent" data-full="' + esc(t.notes) + '">' + esc(notesPreview) + '</div>';
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
      var dn = name === 'mercury' ? 'Lisa' : name === 'sentinel' ? 'Robotox' : name.charAt(0).toUpperCase() + name.slice(1);
      html += '<div class="econ-row"><span style="flex:1;color:' + (AGENT_COLORS[name]||'var(--text)') + ';font-weight:500;">' + dn + '</span>';
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

// ── Pipeline, Rating, Writing, Timing, Algorithm panels ──

async function runPipeline() {
  var btn = document.getElementById('pipeline-run-btn');
  var resultEl = document.getElementById('pipeline-results');
  btn.disabled = true;
  btn.textContent = 'Running...';
  resultEl.innerHTML = '<span class="text-muted">Processing pending items...</span>';
  try {
    var resp = await fetch('/api/lisa/pipeline/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({platform:'instagram'})});
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
  } catch (e) { resultEl.innerHTML = '<span style="color:#ff4444;">Error: ' + esc(e.message) + '</span>'; }
  finally { btn.disabled = false; btn.textContent = 'Run Pipeline'; }
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
      html += '<div style="font-size:0.72rem;color:var(--agent-mercury);margin-top:6px;">Suggestions:</div>';
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
      var html = '<div style="color:var(--agent-mercury);font-weight:600;margin-bottom:8px;">X Thread (' + data.tweets.length + ' tweets)</div>';
      for (var i=0;i<data.tweets.length;i++) {
        html += '<div style="padding:8px 12px;margin-bottom:6px;background:rgba(255,136,0,0.05);border-left:2px solid var(--agent-mercury);border-radius:4px;font-size:0.76rem;">' + esc(data.tweets[i]) + '</div>';
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
      html += '<div style="margin-bottom:12px;padding:8px 12px;border-left:2px solid var(--agent-mercury);border-radius:4px;">';
      html += '<div style="font-weight:600;font-size:0.78rem;color:var(--agent-mercury);margin-bottom:4px;">' + (platIcons[plat]||plat) + '</div>';
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
    var resp = await fetch('/api/mercury/knowledge');
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
        html += '<div style="color:var(--agent-mercury);margin-bottom:4px;">Algorithm Signals:</div>';
        for (var s=0;s<signals.length;s++) html += '<div style="color:var(--text-secondary);padding-left:8px;">- ' + esc(signals[s]) + '</div>';
      }
      // Key rules
      var rules = pd.key_rules || [];
      if (rules.length > 0) {
        html += '<div style="color:var(--agent-mercury);margin-top:6px;margin-bottom:4px;">Key Rules:</div>';
        for (var r=0;r<rules.length;r++) html += '<div style="color:var(--text-secondary);padding-left:8px;">- ' + esc(rules[r]) + '</div>';
      }
      // Weights
      var weights = aw[plat] || {};
      var wk = Object.keys(weights);
      if (wk.length > 0) {
        html += '<div style="color:var(--agent-mercury);margin-top:6px;margin-bottom:4px;">Signal Weights:</div>';
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
  var apiAgent = agent === 'lisa' ? 'mercury' : agent;
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
      html += '<button class="btn" style="font-size:0.7rem;color:var(--agent-mercury);" onclick="sorenBrandCheck(\'' + id + '\',this)">Brand Check</button>';
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

async function loadJordanQueue() {
  var el = document.getElementById('jordan-approval-queue');
  var countEl = document.getElementById('jordan-queue-count');
  if (!el) return;
  try {
    var resp = await fetch('/api/lisa/jordan-queue');
    var data = await resp.json();
    var items = data.items || [];
    if (countEl) countEl.textContent = items.length > 0 ? items.length + ' awaiting' : '';
    if (items.length === 0) {
      el.innerHTML = '<div class="glass-card" style="text-align:center;padding:var(--space-6);"><div style="font-size:1.2rem;color:var(--text-muted);margin-bottom:4px;">No items pending</div><div style="font-size:0.72rem;color:var(--text-secondary);">Run the pipeline on pending Soren content to populate this queue</div></div>';
      return;
    }
    var html = '';
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var isApproved = item.status === 'lisa_approved';
      var borderColor = isApproved ? '#22aa44' : '#ffaa00';
      var tierLabel = isApproved ? 'LISA APPROVED' : 'NEEDS REVIEW';
      html += '<div class="glass-card" style="border-left:3px solid ' + borderColor + ';margin-bottom:8px;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">';
      html += '<div>';
      html += '<div style="font-weight:600;font-size:0.78rem;color:var(--text-primary);margin-bottom:2px;">' + esc(item.title || item.pillar || 'Content') + '</div>';
      html += '<div style="display:flex;gap:4px;flex-wrap:wrap;">';
      html += '<span class="badge" style="background:' + borderColor + '22;color:' + borderColor + ';border:1px solid ' + borderColor + '44;">' + tierLabel + '</span>';
      if (item.pillar) html += '<span class="badge badge-neutral">' + esc(item.pillar.replace(/_/g, ' ')) + '</span>';
      if (item.platform) html += '<span class="badge badge-neutral">' + esc(item.platform) + '</span>';
      if (item.format) html += '<span class="badge badge-neutral">' + esc(item.format) + '</span>';
      html += '</div></div>';
      if (item.rating_score) {
        var scoreColor = item.rating_score >= 80 ? '#22aa44' : item.rating_score >= 60 ? '#ffaa00' : '#ff4444';
        html += '<div style="text-align:right;"><div style="font-size:1.1rem;font-weight:700;color:' + scoreColor + ';">' + item.rating_score + '</div><div style="font-size:0.66rem;color:var(--text-muted);">/100</div></div>';
      }
      html += '</div>';
      // Caption preview
      if (item.caption) html += '<div style="font-family:var(--font-mono);font-size:0.74rem;color:var(--text-secondary);margin-bottom:8px;padding:6px 8px;background:rgba(255,255,255,0.03);border-radius:4px;">' + esc(item.caption) + '</div>';
      // Rating dimensions mini-bars
      var dims = item.rating_dimensions || {};
      var dk = Object.keys(dims);
      if (dk.length > 0) {
        html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;">';
        var dimNames = {brand_voice:'Voice',hook_power:'Hook',engagement_potential:'Engage',platform_fit:'Platform',emotional_impact:'Emotion',authenticity:'Auth',pillar_relevance:'Pillar',timing_fit:'Timing'};
        for (var d = 0; d < dk.length; d++) {
          var k = dk[d];
          var v = dims[k] || 0;
          var dc = v >= 7 ? '#22aa44' : v >= 4 ? '#ffaa00' : '#ff4444';
          html += '<span style="font-size:0.66rem;color:' + dc + ';">' + (dimNames[k]||k) + ':' + v + '</span>';
        }
        html += '</div>';
      }
      // Issues and suggestions
      if (item.rating_issues && item.rating_issues.length > 0) {
        html += '<div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:4px;">';
        for (var r = 0; r < item.rating_issues.length; r++) html += '<div style="padding-left:8px;">- ' + esc(item.rating_issues[r]) + '</div>';
        html += '</div>';
      }
      // Suggested time
      if (item.suggested_time) {
        html += '<div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:6px;">Suggested: <span style="color:var(--agent-mercury);">' + esc(item.suggested_time.replace('T', ' ')) + '</span>';
        if (item.suggested_reason) html += ' — ' + esc(item.suggested_reason);
        html += '</div>';
      }
      // Action buttons
      html += '<div style="display:flex;gap:6px;margin-top:4px;">';
      html += '<button class="btn btn-primary" onclick="jordanApproveItem(\'' + esc(item.id) + '\',\'' + esc(item.platform || 'instagram') + '\')" style="font-size:0.72rem;">Approve</button>';
      html += '<button class="btn btn-error" onclick="jordanRejectItem(\'' + esc(item.id) + '\')" style="font-size:0.72rem;">Reject</button>';
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
      body: JSON.stringify({platform: platform || 'instagram'})
    });
    var data = await resp.json();
    if (data.error) { alert('Error: ' + data.error); return; }
    refresh();
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
    var name = isUser ? 'You' : (AGENT_NAMES[agent] || agent.charAt(0).toUpperCase() + agent.slice(1));
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
      var dn = name === 'mercury' ? 'Lisa' : name === 'sentinel' ? 'Robotox' : name.charAt(0).toUpperCase() + name.slice(1);
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
      loadAgentActivity('garves-live');
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
      loadPillarDistribution();
      loadBrainNotes('soren');
      loadCommandTable('soren');
      loadAgentSmartActions('soren');
      loadAgentActivity('soren');
    } else if (currentTab === 'shelby') {
      var resp = await fetch('/api/shelby');
      renderShelby(await resp.json());
      try { loadSchedule(); } catch(e) {}
      try { loadEconomics(); } catch(e) {}
      try { loadAssessments(); } catch(e) {}
      try { loadShelbyNextRoutine(); } catch(e) {}
      loadBrainNotes('shelby');
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
      loadCompetitorIntel();
      loadAgentLearning('atlas');
      loadTradeAnalysis();
      loadBrainNotes('atlas');
      loadCommandTable('atlas');
      loadAgentSmartActions('atlas');
      loadAgentActivity('atlas');
      loadAtlasKBHealth();
    } else if (currentTab === 'mercury') {
      var resp = await fetch('/api/mercury');
      renderMercury(await resp.json());
      loadJordanQueue();
      loadPostingSchedule();
      loadMercuryPlan();
      loadMercuryKnowledge();
      loadPipelineStats();
      loadTimingPanel();
      loadAlgorithmPanel();
      loadAgentLearning('mercury');
      loadLisaGoLive();
      loadLisaCommentStats();
      loadBrainNotes('lisa');
      loadCommandTable('lisa');
      loadAgentSmartActions('lisa');
      loadAgentActivity('mercury');
    } else if (currentTab === 'sentinel') {
      var resp = await fetch('/api/sentinel');
      renderSentinel(await resp.json());
      loadAgentLearning('sentinel');
      loadRobotoxDeps();
      loadLogWatcherAlerts();
      loadRobotoxPerf();
      loadRobotoxDepHealth();
      loadRobotoxCorrelator();
      loadRobotoxDeployWatches();
      loadBrainNotes('robotox');
      loadCommandTable('robotox');
      loadAgentSmartActions('robotox');
      loadAgentActivity('sentinel');
    } else if (currentTab === 'thor') {
      loadThor();
      loadSmartActions();
      loadThorReflexion();
      loadThorCache();
      loadThorReview();
      loadThorProgress();
      loadThorCodebaseIndex();
      loadBrainNotes('thor');
      loadCommandTable('thor');
      loadAgentActivity('thor');
    } else if (currentTab === 'hawk-sim') {
      loadHawkSimTab();
    } else if (currentTab === 'hawk') {
      loadHawkTab();
      loadBrainNotes('hawk');
      loadCommandTable('hawk');
    } else if (currentTab === 'viper') {
      loadViperTab();
      loadBrainNotes('viper');
      loadCommandTable('viper');
    } else if (currentTab === 'system') {
      loadSystemTab();
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
    {key:'hawk',   name:'Hawk',    color:'#FFD700'},
    {key:'viper',  name:'Viper',   color:'#00ff88'},
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
var _intelAgentMap = {garves:'garves',soren:'soren',shelby:'shelby',atlas:'atlas',mercury:'lisa',sentinel:'robotox',thor:'thor',hawk:'hawk',viper:'viper'};
var _intelColors = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',lisa:'#ff8800',robotox:'#00ff44',thor:'#ff6600',hawk:'#FFD700',viper:'#00ff88'};

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
  // Use system-health for both health status and agent counts
  try {
    var resp = await fetch('/api/system-health');
    var data = await resp.json();
    var hbs = data.heartbeats || {};
    var reg = data.registry || {};

    // Build combined agent list (heartbeats + registry, exclude dashboard)
    var allAgents = {};
    for (var k in reg) { if (k !== 'dashboard') allAgents[k] = {registered: true, healthy: false}; }
    for (var k in hbs) { if (k !== 'dashboard') { if (!allAgents[k]) allAgents[k] = {registered: false, healthy: false}; allAgents[k].healthy = hbs[k].health === 'healthy'; } }
    var total = Object.keys(allAgents).length;
    var alive = 0;
    for (var k in allAgents) { if (allAgents[k].healthy) alive++; }

    // System Health
    var el2 = document.getElementById('infra-health');
    if (el2) {
      var healthLabel = alive === total ? 'HEALTHY' : alive >= Math.ceil(total/2) ? 'DEGRADED' : 'CRITICAL';
      var color = healthLabel === 'HEALTHY' ? 'var(--success)' : healthLabel === 'DEGRADED' ? 'var(--warning)' : 'var(--error)';
      el2.innerHTML = '<span style="color:' + color + ';">' + healthLabel + '</span>';
    }

    // Active Agents (exclude dashboard from count)
    var aaEl = document.getElementById('infra-active-agents');
    if (aaEl) {
      var aaColor = alive === total ? 'var(--success)' : alive > 0 ? 'var(--warning)' : 'var(--error)';
      aaEl.innerHTML = '<span style="color:' + aaColor + ';">' + alive + '/' + total + '</span>';
    }
  } catch(e) {}

  // Events (24h) from event bus stats
  try {
    var stResp = await fetch('/api/events/stats');
    var stData = await stResp.json();
    var etEl = document.getElementById('infra-events-today');
    if (etEl) etEl.textContent = stData.total || 0;
  } catch(e) {}

  // Errors (24h) from Robotox log alerts
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
    var errEl = document.getElementById('infra-errors-today');
    if (errEl) {
      var errColor = errCount === 0 ? 'var(--success)' : 'var(--error)';
      errEl.innerHTML = '<span style="color:' + errColor + ';">' + errCount + '</span>';
    }
  } catch(e) {
    var errEl = document.getElementById('infra-errors-today');
    if (errEl) errEl.innerHTML = '<span style="color:var(--success);">0</span>';
  }

  // Agent Comms — event bus events in chat-like format
  loadAgentComms();
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

  // Load cost tracker
  loadThorCosts();
  // Load queue
  loadThorQueue();
  // Load results
  loadThorResults();
  // Load activity
  loadThorActivity();
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
  var el = document.getElementById('atlas-kb-health');
  if (!el) return;
  try {
    var resp = await fetch('/api/atlas/kb-health');
    var data = await resp.json();
    if (data.error) { el.innerHTML = '<span style="color:var(--error);">' + esc(data.error) + '</span>'; return; }
    var html = '<div style="display:flex;gap:16px;flex-wrap:wrap;">';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;">' + (data.total_learnings || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Learnings</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;">' + (data.total_observations || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Observations</div></div>';
    var conf = data.confidence || {};
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;color:var(--success);">' + (conf.high || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">High Conf</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;color:var(--warning);">' + (conf.medium || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Medium</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;color:var(--error);">' + (conf.low || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Low Conf</div></div>';
    var age = data.age || {};
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;color:var(--agent-atlas);">' + (age.fresh_7d || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Fresh (7d)</div></div>';
    html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;color:var(--text-muted);">' + (age.stale_30d_plus || 0) + '</div><div class="text-muted" style="font-size:0.7rem;">Stale (30d+)</div></div>';
    if (data.contradictions > 0) {
      html += '<div style="text-align:center;"><div style="font-size:1.1rem;font-weight:700;color:var(--error);">' + data.contradictions + '</div><div class="text-muted" style="font-size:0.7rem;">Contradictions</div></div>';
    }
    html += '</div>';
    html += '<div style="margin-top:8px;display:flex;gap:8px;">';
    html += '<span class="text-muted" style="font-size:0.72rem;">Applied: ' + (data.applied || 0) + '/' + (data.total_learnings || 0) + '</span>';
    if (data.last_consolidated) html += '<span class="text-muted" style="font-size:0.72rem;">Last consolidated: ' + esc(data.last_consolidated).substring(0, 16) + '</span>';
    html += '<button class="btn" onclick="atlasConsolidate()" style="font-size:0.68rem;margin-left:auto;">Consolidate</button>';
    html += '</div>';
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<span style="color:var(--error);">Failed: ' + esc(e.message) + '</span>'; }
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
  // Send the note content to the agent via chat
  openAgentChat(agent);
  var inputEl = document.getElementById('agent-chat-input');
  if (inputEl) {
    inputEl.value = content;
    sendAgentChat();
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

// ═══════════════════════════════════════════════════════
// AGENT CHAT POPUP — Per-agent direct chat
// ═══════════════════════════════════════════════════════
var _agentChatTarget = null;

function openAgentChat(agent) {
  _agentChatTarget = agent;
  var popup = document.getElementById('agent-chat-popup');
  var titleEl = document.getElementById('agent-chat-title');
  var agentName = AGENT_NAMES[agent] || agent;
  var agentColor = AGENT_COLORS[agent] || '#888';
  if (titleEl) titleEl.innerHTML = '<span style="color:' + agentColor + ';">Chat with ' + esc(agentName) + '</span>';
  if (popup) popup.style.display = 'flex';
  // Load history
  loadAgentChatHistory(agent);
  var inputEl = document.getElementById('agent-chat-input');
  if (inputEl) inputEl.focus();
}

function closeAgentChat() {
  var popup = document.getElementById('agent-chat-popup');
  if (popup) popup.style.display = 'none';
  _agentChatTarget = null;
}

async function loadAgentChatHistory(agent) {
  var msgsEl = document.getElementById('agent-chat-messages');
  if (!msgsEl) return;
  try {
    var resp = await fetch('/api/chat/agent/' + agent + '/history');
    var data = await resp.json();
    renderAgentChatMessages(data.history || [], agent);
  } catch(e) {
    msgsEl.innerHTML = '<div class="text-muted" style="padding:12px;text-align:center;">Start a conversation...</div>';
  }
}

function renderAgentChatMessages(history, agent) {
  var msgsEl = document.getElementById('agent-chat-messages');
  if (!msgsEl) return;
  if (!history.length) {
    var agentName = AGENT_NAMES[agent] || agent;
    msgsEl.innerHTML = '<div class="text-muted" style="padding:20px;text-align:center;font-size:0.74rem;">Send a message to ' + esc(agentName) + '...</div>';
    return;
  }
  var agentColor = AGENT_COLORS[agent] || '#888';
  var html = '';
  for (var i = 0; i < history.length; i++) {
    var m = history[i];
    if (m.role === 'user') {
      html += '<div class="agent-chat-msg agent-chat-msg-user"><div class="chat-bubble">' + esc(m.content) + '</div></div>';
    } else {
      html += '<div class="agent-chat-msg agent-chat-msg-agent"><div class="chat-bubble" style="border-left:2px solid ' + agentColor + ';">' + esc(m.content) + '</div></div>';
    }
  }
  msgsEl.innerHTML = html;
  msgsEl.scrollTop = msgsEl.scrollHeight;
}

async function sendAgentChat() {
  if (!_agentChatTarget) return;
  var inputEl = document.getElementById('agent-chat-input');
  var sendBtn = document.getElementById('agent-chat-send');
  if (!inputEl || !inputEl.value.trim()) return;
  var msg = inputEl.value.trim();
  inputEl.value = '';
  if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = '...'; }

  // Show user message immediately
  var msgsEl = document.getElementById('agent-chat-messages');
  if (msgsEl) {
    // Remove "Start a conversation" placeholder
    var placeholder = msgsEl.querySelector('.text-muted');
    if (placeholder && msgsEl.children.length === 1) msgsEl.innerHTML = '';
    msgsEl.innerHTML += '<div class="agent-chat-msg agent-chat-msg-user"><div class="chat-bubble">' + esc(msg) + '</div></div>';
    msgsEl.innerHTML += '<div id="agent-chat-typing" class="agent-chat-msg agent-chat-msg-agent"><div class="chat-bubble" style="color:var(--text-muted);font-style:italic;">Thinking...</div></div>';
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  try {
    var resp = await fetch('/api/chat/agent/' + _agentChatTarget, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    var data = await resp.json();
    // Remove typing indicator and render full history
    renderAgentChatMessages(data.history || [], _agentChatTarget);
  } catch(e) {
    var typing = document.getElementById('agent-chat-typing');
    if (typing) typing.querySelector('.chat-bubble').textContent = 'Error: ' + e.message;
  }
  if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = 'Send'; }
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
  'mercury': {agent: 'lisa', id: 'lisa-activity-feed'},
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
// SYSTEM TAB — Real-time infrastructure monitoring
// ═══════════════════════════════════════════════════════
async function loadSystemTab() {
  try {
    var resp = await fetch('/api/system/metrics');
    var d = await resp.json();

    // CPU stat card
    var cpuEl = document.getElementById('sys-cpu');
    if (cpuEl) {
      var cpuPct = (d.cpu || {}).percent || 0;
      var cpuColor = cpuPct > 80 ? 'var(--error)' : cpuPct > 50 ? 'var(--warning)' : 'var(--success)';
      cpuEl.innerHTML = '<span style="color:' + cpuColor + ';">' + cpuPct + '%</span>';
    }

    // Memory stat card
    var memEl = document.getElementById('sys-memory');
    if (memEl) {
      var mem = d.memory || {};
      var memColor = mem.percent > 85 ? 'var(--error)' : mem.percent > 70 ? 'var(--warning)' : 'var(--success)';
      memEl.innerHTML = '<span style="color:' + memColor + ';">' + mem.percent + '%</span>';
    }

    // Disk stat card
    var diskEl = document.getElementById('sys-disk');
    if (diskEl) {
      var disk = d.disk || {};
      var diskColor = disk.percent > 90 ? 'var(--error)' : disk.percent > 75 ? 'var(--warning)' : 'var(--success)';
      diskEl.innerHTML = '<span style="color:' + diskColor + ';">' + disk.percent + '%</span>';
    }

    // Uptime stat card
    var upEl = document.getElementById('sys-uptime');
    if (upEl) upEl.textContent = (d.uptime || {}).text || '--';

    // Processes stat card
    var procEl = document.getElementById('sys-processes');
    if (procEl) procEl.textContent = (d.processes || []).length;

    // Ports stat card
    var portEl = document.getElementById('sys-ports');
    if (portEl) portEl.textContent = (d.ports || []).length;

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

    // Resource bars
    var rbEl = document.getElementById('sys-resource-bars');
    if (rbEl) {
      var cpu = d.cpu || {};
      var mem = d.memory || {};
      var disk = d.disk || {};
      var rbHtml = '';
      var bars = [
        {label: 'CPU', pct: cpu.percent || 0, detail: 'Load: ' + (cpu.load_1m || 0) + ' / ' + (cpu.cores || 1) + ' cores'},
        {label: 'Memory', pct: mem.percent || 0, detail: (mem.used_gb || 0) + ' / ' + (mem.total_gb || 0) + ' GB'},
        {label: 'Disk', pct: disk.percent || 0, detail: (disk.used_gb || 0) + ' / ' + (disk.total_gb || 0) + ' GB'}
      ];
      for (var b = 0; b < bars.length; b++) {
        var bar = bars[b];
        var barColor = bar.pct > 85 ? 'var(--error)' : bar.pct > 60 ? 'var(--warning)' : 'var(--success)';
        rbHtml += '<div style="margin-bottom:10px;">';
        rbHtml += '<div style="display:flex;justify-content:space-between;font-size:0.72rem;margin-bottom:3px;">';
        rbHtml += '<span>' + bar.label + '</span>';
        rbHtml += '<span class="text-muted">' + bar.detail + '</span>';
        rbHtml += '</div>';
        rbHtml += '<div style="width:100%;height:8px;background:rgba(255,255,255,0.06);border-radius:4px;overflow:hidden;">';
        rbHtml += '<div style="width:' + Math.min(bar.pct, 100) + '%;height:100%;background:' + barColor + ';border-radius:4px;transition:width 0.5s ease;"></div>';
        rbHtml += '</div></div>';
      }
      rbEl.innerHTML = rbHtml;
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
// HAWK TAB — Market Predator
// ══════════════════════════════════════
var _hawkScanPoller = null;
var _hawkOppsCache = [];

async function loadHawkTab() {
  try {
    var resp = await fetch('/api/hawk');
    var d = await resp.json();
    var s = d.summary || {};
    document.getElementById('hawk-winrate').textContent = (s.win_rate || 0).toFixed(1) + '%';
    document.getElementById('hawk-winrate').style.color = wrColor(s.win_rate || 0);
    document.getElementById('hawk-pnl').textContent = '$' + (s.pnl || 0).toFixed(2);
    document.getElementById('hawk-pnl').style.color = (s.pnl || 0) >= 0 ? 'var(--success)' : 'var(--error)';
    document.getElementById('hawk-open-bets').textContent = s.open_positions || 0;
    document.getElementById('hawk-total-trades').textContent = s.total_trades || 0;
    document.getElementById('hawk-resolved').textContent = s.resolved || 0;
    document.getElementById('hawk-daily-pnl').textContent = '$' + (s.daily_pnl || 0).toFixed(2);
    document.getElementById('hawk-daily-pnl').style.color = (s.daily_pnl || 0) >= 0 ? 'var(--success)' : 'var(--error)';
  } catch(e) { console.error('hawk status:', e); }

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

  // Positions
  try {
    var posResp = await fetch('/api/hawk/positions');
    var posData = await posResp.json();
    renderHawkPositions(posData.positions || []);
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
      var typeLabel = it.match_type === 'pre_linked' ? 'Targeted' : 'Entity';
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
  var catColors = {politics:'#4488ff',sports:'#ff8844',crypto_event:'#FFD700',culture:'#cc66ff',other:'#888888'};
  var catLabels = {politics:'Politics',sports:'Sports',crypto_event:'Crypto',culture:'Culture',other:'Other'};
  var hasResolved = Object.keys(cats).length > 0;
  var hasOpps = oppCats && Object.keys(oppCats).length > 0;
  if (!hasResolved && !hasOpps) { el.innerHTML = '<div class="text-muted" style="text-align:center;padding:12px;">No category data yet — trigger a scan</div>'; return; }

  var html = '<div style="display:flex;flex-wrap:wrap;gap:10px;">';
  // Show opportunity-based category cards (always if we have scan data)
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
      html += '<div style="font-size:0.72rem;color:var(--text-secondary);">Total EV: <span style="color:var(--success);font-weight:600;">+$' + (c.total_ev||0).toFixed(2) + '</span></div>';
      html += '<div style="font-size:0.72rem;color:var(--text-secondary);">$30/each: <span style="color:#FFD700;font-weight:600;">+$' + (c.potential_30||0).toFixed(2) + '</span></div>';
      if (rc) {
        var wr = (rc.wins+rc.losses)>0?(rc.wins/(rc.wins+rc.losses)*100):0;
        html += '<div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px;border-top:1px solid rgba(255,255,255,0.06);padding-top:4px;">Resolved: ' + rc.wins + 'W-' + rc.losses + 'L (' + wr.toFixed(0) + '%) | $' + (rc.pnl||0).toFixed(2) + '</div>';
      }
      html += '</div>';
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

function hawkCalcTimeLeft(endDate) {
  if (!endDate) return {text: 'No deadline', color: 'var(--text-muted)', sort: 99999999};
  var now = Date.now();
  var end = new Date(endDate).getTime();
  if (isNaN(end)) return {text: 'Unknown', color: 'var(--text-muted)', sort: 99999999};
  var diff = end - now;
  if (diff <= 0) return {text: 'Expired', color: 'var(--error)', sort: -1};
  var hours = diff / 3600000;
  var days = Math.floor(hours / 24);
  var hrs = Math.floor(hours % 24);
  if (days > 365) return {text: Math.floor(days/365) + 'y ' + Math.floor((days%365)/30) + 'mo', color: 'var(--text-muted)', sort: diff};
  if (days > 30) return {text: Math.floor(days/30) + 'mo ' + (days%30) + 'd', color: '#888', sort: diff};
  if (days > 7) return {text: days + 'd', color: 'var(--text-secondary)', sort: diff};
  if (days > 1) return {text: days + 'd ' + hrs + 'h', color: '#FFD700', sort: diff};
  if (hours > 1) return {text: Math.floor(hours) + 'h ' + Math.floor((diff%3600000)/60000) + 'm', color: '#ff8844', sort: diff};
  return {text: Math.floor(diff/60000) + 'm', color: 'var(--error)', sort: diff};
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
    var tl = hawkCalcTimeLeft(o.end_date);
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
    html += '<div style="font-size:0.7rem;color:var(--text-muted);">Group EV: <span style="color:var(--success);font-weight:600;">+$' + grpEv.toFixed(2) + '</span> | $30/ea profit: <span style="color:#FFD700;font-weight:600;">+$' + grp30.toFixed(2) + '</span></div>';
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

function renderHawkPositions(positions) {
  var el = document.getElementById('hawk-pos-tbody');
  if (!el) return;
  if (positions.length === 0) { el.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px;">No open positions</td></tr>'; return; }
  var html = '';
  for (var i = 0; i < positions.length; i++) {
    var p = positions[i];
    html += '<tr>';
    html += '<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc((p.question || '').substring(0, 45)) + '</td>';
    html += '<td>' + esc(p.direction || '?') + '</td>';
    html += '<td>$' + (p.size_usd || 0).toFixed(2) + '</td>';
    html += '<td>' + ((p.entry_price || 0) * 100).toFixed(0) + '%</td>';
    html += '<td><span class="badge" style="background:rgba(255,255,255,0.08);">' + esc(p.category || '?') + '</span></td>';
    html += '</tr>';
  }
  el.innerHTML = html;
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
  } catch(e) {
    if (msg) { msg.style.background = 'rgba(255,0,0,0.08)'; msg.style.color = 'var(--error)'; msg.textContent = 'Resolution check failed: ' + e.message; }
  }
}

// ══════════════════════════════════════
// VIPER TAB — 24/7 Intelligence Engine
// ══════════════════════════════════════
var _viperScanPoller = null;

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
  try {
    var costResp = await fetch('/api/viper/costs');
    var costData = await costResp.json();
    renderViperCosts(costData);
  } catch(e) {}

  // Soren metrics
  try {
    var sorenResp = await fetch('/api/viper/soren-metrics');
    var sorenData = await sorenResp.json();
    renderViperSorenMetrics(sorenData);
  } catch(e) {}
}

function renderViperIntel(items) {
  var el = document.getElementById('viper-intel-tbody');
  if (!el) return;
  if (items.length === 0) { el.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:24px;">No intelligence yet — hit Trigger Scan</td></tr>'; return; }
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
  if (!data || !data.followers) { el.innerHTML = '<div class="text-muted" style="padding:12px;">No Soren metrics available yet.</div>'; return; }
  var html = '<div style="display:flex;gap:12px;flex-wrap:wrap;">';
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:10px 14px;"><div style="font-size:0.72rem;color:var(--text-muted);">Followers</div><div style="font-size:1.1rem;font-weight:700;">' + (data.followers || 0) + '</div></div>';
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:10px 14px;"><div style="font-size:0.72rem;color:var(--text-muted);">Engagement</div><div style="font-size:1.1rem;font-weight:700;">' + ((data.engagement_rate || 0) * 100).toFixed(1) + '%</div></div>';
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:10px 14px;"><div style="font-size:0.72rem;color:var(--text-muted);">Est. CPM</div><div style="font-size:1.1rem;font-weight:700;color:var(--success);">$' + (data.estimated_cpm || 0).toFixed(2) + '</div></div>';
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:10px 14px;"><div style="font-size:0.72rem;color:var(--text-muted);">Brand Ready</div><div style="font-size:1.1rem;font-weight:700;">' + (data.brand_ready ? 'Yes' : 'Not yet') + '</div></div>';
  html += '</div>';
  el.innerHTML = html;
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
