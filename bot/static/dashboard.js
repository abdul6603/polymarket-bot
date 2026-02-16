var currentTab = 'overview';
var chatLoaded = false;
var econPeriod = 'month';
var AGENT_COLORS = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',mercury:'#ff8800',sentinel:'#00ff44'};
var AGENT_INITIALS = {garves:'GA',soren:'SO',shelby:'SH',atlas:'AT',mercury:'LI',sentinel:'RO'};
var AGENT_ROLES = {garves:'Trading Bot',soren:'Content Creator',shelby:'Team Leader',atlas:'Data Scientist',mercury:'Social Media',sentinel:'Health Monitor'};
var AGENT_NAMES = {garves:'Garves',soren:'Soren',shelby:'Shelby',atlas:'Atlas',mercury:'Lisa',sentinel:'Robotox'};

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

function renderAgentGrid(overview) {
  var g = overview.garves || {};
  var s = overview.soren || {};
  var sh = overview.shelby || {};
  var cards = [
    {id:'garves', stats:[['Win Rate',(g.win_rate||0)+'%'],['Trades',g.total_trades||0],['Pending',g.pending||0]], online:g.running},
    {id:'soren', stats:[['Queue',s.queue_pending||0],['Posted',s.total_posted||0]], online:true},
    {id:'shelby', stats:[['Status',sh.running?'Online':'Offline']], online:sh.running},
    {id:'atlas', stats:[['Status','Active']], online:true},
    {id:'mercury', stats:[['Posts',(overview.mercury||{}).total_posts||0],['Review Avg',(overview.mercury||{}).review_avg ? (overview.mercury.review_avg+'/10') : '--']], online:true},
    {id:'sentinel', stats:[['Role','Monitor']], online:true}
  ];
  var html = '';
  for (var i = 0; i < cards.length; i++) {
    var c = cards[i];
    html += '<div class="agent-card" data-agent="' + c.id + '" onclick="switchTab(&apos;' + c.id + '&apos;)">';
    html += '<div class="agent-card-header"><span class="agent-card-name">' + (AGENT_NAMES[c.id] || c.id.charAt(0).toUpperCase() + c.id.slice(1)) + '</span>';
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
  document.getElementById('atlas-observations').textContent = brain.observations || 0;
  document.getElementById('atlas-learnings').textContent = brain.learnings || 0;
  document.getElementById('atlas-improvements').textContent = brain.unapplied || 0;
  document.getElementById('atlas-cycles').textContent = bg.cycles || 0;
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
    renderAtlasHierarchy(d);
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

  var agents = [
    {key:'garves', name:'Garves', color:'#00d4ff', x:80, y:30},
    {key:'soren', name:'Soren', color:'#cc66ff', x:200, y:30},
    {key:'shelby', name:'Shelby', color:'#ffaa00', x:320, y:30},
    {key:'lisa', name:'Lisa', color:'#ff8800', x:440, y:30},
    {key:'robotox', name:'Robotox', color:'#00ff44', x:560, y:30}
  ];
  var atlasX = 320, atlasY = 140;
  var w = 640, h = 180;

  var html = '<div class="hierarchy-header">';
  html += '<span class="hierarchy-title">Live Feed Hierarchy</span>';
  if (running && (state === 'learning' || learnCount > 0)) {
    html += '<span class="learn-pulse"><span class="learn-pulse-dot"></span>Learning (' + learnCount + ' new)</span>';
  }
  if (running && state !== 'running' && state !== 'idle') {
    html += '<span style="font-family:var(--font-mono);font-size:0.7rem;color:var(--agent-atlas);margin-left:auto;">' + esc(stateLabel) + '</span>';
  }
  html += '</div>';

  var svg = '<svg viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;max-height:180px;">';

  // Draw lines from Atlas to each agent
  for (var i = 0; i < agents.length; i++) {
    var ag = agents[i];
    var isActive = target === ag.key || target === 'all';
    var lineColor = isActive ? ag.color : 'rgba(255,255,255,0.08)';
    var lineWidth = isActive ? 2 : 1;
    var dashAttr = isActive ? ' stroke-dasharray="8 8" style="animation:hierarchy-flow 0.8s linear infinite;"' : '';
    svg += '<line x1="' + atlasX + '" y1="' + atlasY + '" x2="' + ag.x + '" y2="' + (ag.y + 16) + '" stroke="' + lineColor + '" stroke-width="' + lineWidth + '"' + dashAttr + '/>';
    if (isActive) {
      svg += '<circle cx="' + ag.x + '" cy="' + (ag.y + 16) + '" r="4" fill="' + ag.color + '" opacity="0.6"><animate attributeName="r" values="3;6;3" dur="1.5s" repeatCount="indefinite"/><animate attributeName="opacity" values="0.8;0.3;0.8" dur="1.5s" repeatCount="indefinite"/></circle>';
    }
  }

  // Draw agent nodes
  for (var i = 0; i < agents.length; i++) {
    var ag = agents[i];
    var isActive = target === ag.key || target === 'all';
    var opacity = isActive ? '1' : '0.4';
    var glow = isActive ? ' filter="url(#glow-' + ag.key + ')"' : '';

    if (isActive) {
      svg += '<defs><filter id="glow-' + ag.key + '"><feGaussianBlur stdDeviation="4" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';
    }

    svg += '<g opacity="' + opacity + '"' + glow + '>';
    svg += '<circle cx="' + ag.x + '" cy="' + ag.y + '" r="16" fill="rgba(8,8,16,0.9)" stroke="' + ag.color + '" stroke-width="' + (isActive ? 2 : 1) + '"/>';
    svg += '<text x="' + ag.x + '" y="' + (ag.y + 4) + '" text-anchor="middle" fill="' + ag.color + '" font-size="9" font-family="\'JetBrains Mono\',monospace" font-weight="600">' + ag.name.substring(0,2).toUpperCase() + '</text>';
    svg += '<text x="' + ag.x + '" y="' + (ag.y - 22) + '" text-anchor="middle" fill="' + ag.color + '" font-size="8" font-family="Inter,sans-serif" opacity="0.7">' + ag.name + '</text>';
    svg += '</g>';
  }

  // Draw Atlas node (center, larger)
  var atlasGlow = running && state !== 'running' && state !== 'idle' ? ' filter="url(#glow-atlas)"' : '';
  if (running && state !== 'running' && state !== 'idle') {
    svg += '<defs><filter id="glow-atlas"><feGaussianBlur stdDeviation="6" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';
  }
  svg += '<g' + atlasGlow + '>';
  svg += '<circle cx="' + atlasX + '" cy="' + atlasY + '" r="24" fill="rgba(8,8,16,0.95)" stroke="#22cc55" stroke-width="2"/>';
  if (running && state !== 'running' && state !== 'idle') {
    svg += '<circle cx="' + atlasX + '" cy="' + atlasY + '" r="24" fill="none" stroke="#22cc55" stroke-width="1" opacity="0.3"><animate attributeName="r" values="24;32;24" dur="2s" repeatCount="indefinite"/><animate attributeName="opacity" values="0.3;0;0.3" dur="2s" repeatCount="indefinite"/></circle>';
  }
  svg += '<text x="' + atlasX + '" y="' + (atlasY + 1) + '" text-anchor="middle" fill="#22cc55" font-size="10" font-family="\'Exo 2\',sans-serif" font-weight="700" letter-spacing="1">ATLAS</text>';
  svg += '<text x="' + atlasX + '" y="' + (atlasY + 12) + '" text-anchor="middle" fill="rgba(255,255,255,0.35)" font-size="7" font-family="Inter,sans-serif">' + (running ? 'FEEDING' : 'OFFLINE') + '</text>';
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

async function atlasFullReport() {
  var el = document.getElementById('atlas-report');
  el.textContent = 'Generating full report...';
  try {
    var resp = await fetch('/api/atlas/report', {method:'POST'});
    var data = await resp.json();
    if (data.error) { el.textContent = 'Error: ' + data.error; return; }
    var txt = 'FULL ATLAS REPORT\\n' + '='.repeat(50) + '\\n';
    txt += 'Generated: ' + (data.generated_at || 'now') + '\\n\\n';
    if (data.cross_agent_insights && data.cross_agent_insights.length > 0) {
      txt += 'CROSS-AGENT INSIGHTS\\n' + '-'.repeat(30) + '\\n';
      for (var i = 0; i < data.cross_agent_insights.length; i++) {
        txt += '  * ' + data.cross_agent_insights[i] + '\\n';
      }
      txt += '\\n';
    }
    var agents = ['garves','soren','shelby','mercury'];
    for (var a = 0; a < agents.length; a++) {
      var name = agents[a];
      var section = data[name];
      if (!section) continue;
      txt += name.toUpperCase() + '\\n' + '-'.repeat(30) + '\\n';
      if (section.overview) {
        var ov = section.overview;
        var ovKeys = Object.keys(ov);
        for (var j = 0; j < ovKeys.length; j++) {
          txt += '  ' + ovKeys[j] + ': ' + ov[ovKeys[j]] + '\\n';
        }
      }
      if (section.recommendations && section.recommendations.length > 0) {
        txt += '  Recommendations:\\n';
        for (var r = 0; r < section.recommendations.length; r++) {
          var rec = section.recommendations[r];
          if (typeof rec === 'string') { txt += '    - ' + rec + '\\n'; }
          else { txt += '    - [' + (rec.priority||'') + '] ' + (rec.recommendation || rec.description || JSON.stringify(rec)) + '\\n'; }
        }
      }
      txt += '\\n';
    }
    if (data.action_items && data.action_items.length > 0) {
      txt += 'ACTION ITEMS (' + data.action_items.length + ')\\n' + '-'.repeat(30) + '\\n';
      for (var k = 0; k < data.action_items.length; k++) {
        var item = data.action_items[k];
        txt += '  ' + (k+1) + '. [' + (item.priority||'?').toUpperCase() + '] [' + (item.agent||'?').toUpperCase() + '] ' + (item.recommendation || item.description || '') + '\\n';
      }
    }
    el.textContent = txt;
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}

async function atlasAnalyze(agent) {
  var el = document.getElementById('atlas-report');
  el.textContent = 'Analyzing ' + agent + '...';
  try {
    var resp = await fetch('/api/atlas/' + agent);
    var data = await resp.json();
    if (data.error) { el.textContent = 'Error: ' + data.error; return; }
    var txt = agent.toUpperCase() + ' DEEP ANALYSIS\\n' + '='.repeat(50) + '\\n\\n';
    if (data.overview) {
      txt += 'OVERVIEW\\n' + '-'.repeat(30) + '\\n';
      var ovKeys = Object.keys(data.overview);
      for (var i = 0; i < ovKeys.length; i++) {
        txt += '  ' + ovKeys[i].replace(/_/g, ' ') + ': ' + data.overview[ovKeys[i]] + '\\n';
      }
      txt += '\\n';
    }
    if (data.edge_analysis) {
      txt += 'EDGE ANALYSIS\\n' + '-'.repeat(30) + '\\n';
      var ea = data.edge_analysis;
      var eaKeys = Object.keys(ea);
      for (var j = 0; j < eaKeys.length; j++) {
        txt += '  ' + eaKeys[j].replace(/_/g, ' ') + ': ' + ea[eaKeys[j]] + '\\n';
      }
      txt += '\\n';
    }
    if (data.breakdowns) {
      txt += 'BREAKDOWNS\\n' + '-'.repeat(30) + '\\n';
      var bKeys = Object.keys(data.breakdowns);
      for (var b = 0; b < bKeys.length; b++) {
        txt += '  ' + bKeys[b].replace(/_/g, ' ').toUpperCase() + ':\\n';
        var bd = data.breakdowns[bKeys[b]];
        if (typeof bd === 'object') {
          var bdKeys = Object.keys(bd);
          for (var x = 0; x < bdKeys.length; x++) {
            var bv = bd[bdKeys[x]];
            if (typeof bv === 'object') {
              txt += '    ' + bdKeys[x] + ': ' + JSON.stringify(bv) + '\\n';
            } else {
              txt += '    ' + bdKeys[x] + ': ' + bv + '\\n';
            }
          }
        }
      }
      txt += '\\n';
    }
    var sections = ['regime_analysis','straddle_analysis','time_analysis','indicator_analysis',
                    'queue_audit','pillar_balance','caption_quality','hashtag_audit','ab_testing',
                    'posting_overview','scheduling_analysis','platform_health','outbox_status',
                    'tasks','scheduler','economics','profile'];
    for (var s = 0; s < sections.length; s++) {
      var sec = data[sections[s]];
      if (!sec) continue;
      txt += sections[s].replace(/_/g, ' ').toUpperCase() + '\\n' + '-'.repeat(30) + '\\n';
      if (typeof sec === 'object' && !Array.isArray(sec)) {
        var sKeys = Object.keys(sec);
        for (var sk = 0; sk < sKeys.length; sk++) {
          var sv = sec[sKeys[sk]];
          if (typeof sv === 'object' && sv !== null) {
            txt += '  ' + sKeys[sk].replace(/_/g, ' ') + ': ' + JSON.stringify(sv) + '\\n';
          } else {
            txt += '  ' + sKeys[sk].replace(/_/g, ' ') + ': ' + sv + '\\n';
          }
        }
      } else if (Array.isArray(sec)) {
        for (var si = 0; si < sec.length; si++) {
          if (typeof sec[si] === 'string') { txt += '  - ' + sec[si] + '\\n'; }
          else { txt += '  - ' + (sec[si].description || sec[si].recommendation || JSON.stringify(sec[si])) + '\\n'; }
        }
      }
      txt += '\\n';
    }
    if (data.recommendations && data.recommendations.length > 0) {
      txt += 'RECOMMENDATIONS\\n' + '-'.repeat(30) + '\\n';
      for (var ri = 0; ri < data.recommendations.length; ri++) {
        var rec = data.recommendations[ri];
        if (typeof rec === 'string') { txt += '  ' + (ri+1) + '. ' + rec + '\\n'; }
        else { txt += '  ' + (ri+1) + '. [' + (rec.priority||'') + '] ' + (rec.recommendation || rec.description || '') + '\\n'; }
      }
    }
    el.textContent = txt;
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}

async function atlasSuggestImprovements() {
  var el = document.getElementById('atlas-report');
  el.textContent = 'Generating improvements...';
  try {
    var resp = await fetch('/api/atlas/improvements', {method:'POST'});
    var data = await resp.json();
    if (data.error) { el.textContent = 'Error: ' + data.error; return; }
    var txt = 'SUGGESTED IMPROVEMENTS\\n' + '='.repeat(50) + '\\n\\n';
    var count = 0;
    var agents = ['garves','soren','shelby','mercury'];
    for (var a = 0; a < agents.length; a++) {
      var name = agents[a];
      var items = data[name];
      if (!items || items.length === 0) continue;
      txt += name.toUpperCase() + ' (' + items.length + ' suggestions)\\n' + '-'.repeat(30) + '\\n';
      for (var i = 0; i < items.length; i++) {
        var imp = items[i];
        count++;
        txt += '  ' + count + '. [' + (imp.priority||'?').toUpperCase() + '] ' + (imp.description || imp.title || '') + '\\n';
        if (imp.impact) txt += '     Impact: ' + imp.impact + '\\n';
        if (imp.effort) txt += '     Effort: ' + imp.effort + '\\n';
        if (imp.skill) txt += '     Skill: ' + imp.skill + '\\n';
      }
      txt += '\\n';
    }
    if (data.system_wide && data.system_wide.length > 0) {
      txt += 'SYSTEM-WIDE (' + data.system_wide.length + ')\\n' + '-'.repeat(30) + '\\n';
      for (var sw = 0; sw < data.system_wide.length; sw++) {
        var s = data.system_wide[sw];
        count++;
        txt += '  ' + count + '. [' + (s.priority||'?').toUpperCase() + '] ' + (s.suggestion || s.description || '') + '\\n';
        if (s.area) txt += '     Area: ' + s.area + '\\n';
      }
      txt += '\\n';
    }
    if (data.new_agents && data.new_agents.length > 0) {
      txt += 'NEW AGENTS SUGGESTED (' + data.new_agents.length + ')\\n' + '-'.repeat(30) + '\\n';
      for (var na = 0; na < data.new_agents.length; na++) {
        var ag = data.new_agents[na];
        txt += '  * ' + (ag.name||'?') + ' (' + (ag.role||'?') + ')\\n';
        txt += '    ' + (ag.description||'') + '\\n';
      }
      txt += '\\n';
    }
    if (data.new_skills && data.new_skills.length > 0) {
      txt += 'NEW SKILLS SUGGESTED (' + data.new_skills.length + ')\\n' + '-'.repeat(30) + '\\n';
      for (var ns = 0; ns < data.new_skills.length; ns++) {
        var sk = data.new_skills[ns];
        txt += '  * [' + (sk.agent||'?').toUpperCase() + '] ' + (sk.skill||'') + ': ' + (sk.description||'') + '\\n';
      }
      txt += '\\n';
    }
    if (count === 0) { txt += 'No specific improvements found at this time.\\n'; }
    else { txt += '\\nTotal: ' + count + ' improvements suggested\\n'; }
    el.textContent = txt;
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}


async function atlasAcknowledgeImprovements() {
  var el = document.getElementById('atlas-report');
  try {
    var resp = await fetch('/api/atlas/improvements/acknowledge', {method:'POST'});
    var data = await resp.json();
    if (data.error) { el.textContent = 'Error: ' + data.error; return; }
    el.textContent = 'Acknowledged ' + data.acknowledged + ' suggestions. Atlas will generate fresh insights next cycle.\\nTotal dismissed: ' + data.total_dismissed;
  } catch (e) { el.textContent = 'Error: ' + e.message; }
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
      renderAgentGrid(data);
      renderIndicatorAccuracy(data);
    } else if (currentTab === 'garves') {
      var resp = await fetch('/api/trades');
      var data = await resp.json();
      renderGarvesStats(data);
      renderBreakdown('bd-asset', data.by_asset);
      renderBreakdown('bd-tf', data.by_timeframe);
      renderBreakdown('bd-dir', data.by_direction);
      renderPendingTrades(data.pending_trades);
      renderResolvedTrades(data.recent_trades);
      fetch('/api/logs').then(function(r){return r.json();}).then(function(d){renderLogs(d.lines);}).catch(function(){});
      loadRegimeBadge();
      loadAgentLearning('garves');
    } else if (currentTab === 'soren') {
      var resp = await fetch('/api/soren');
      renderSoren(await resp.json());
      loadAgentLearning('soren');
    } else if (currentTab === 'shelby') {
      var resp = await fetch('/api/shelby');
      renderShelby(await resp.json());
      try { loadSchedule(); } catch(e) {}
      try { loadEconomics(); } catch(e) {}
      try { loadActivityBrief(); } catch(e) {}
      try { loadSystemInfo(); } catch(e) {}
      try { loadAssessments(); } catch(e) {}
      try { loadShelbyOnlineCount(); } catch(e) {}
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
    } else if (currentTab === 'mercury') {
      var resp = await fetch('/api/mercury');
      renderMercury(await resp.json());
      loadMercuryPlan();
      loadMercuryKnowledge();
      loadAgentLearning('mercury');
    } else if (currentTab === 'sentinel') {
      var resp = await fetch('/api/sentinel');
      renderSentinel(await resp.json());
      loadAgentLearning('sentinel');
    } else if (currentTab === 'chat') {
      if (!chatLoaded) loadChatHistory();
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
    html += '</div>';
  }
  html += '</div>';

  el.innerHTML = html;
}
var _intelAgentMap = {garves:'garves',soren:'soren',shelby:'shelby',atlas:'atlas',mercury:'lisa',sentinel:'robotox'};
var _intelColors = {garves:'#00d4ff',soren:'#cc66ff',shelby:'#ffaa00',atlas:'#22aa44',lisa:'#ff8800',robotox:'#00ff44'};

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

  var html = '<div style="display:flex;align-items:center;gap:16px;">';
  html += '<div style="flex-shrink:0;">' + radarSVG(140, values, keys, color) + '</div>';
  html += '<div style="flex:1;min-width:0;">';
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
  html += '</div></div></div>';

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
