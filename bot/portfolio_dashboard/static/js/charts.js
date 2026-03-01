/**
 * Agent Swarm Dashboard — Chart.js Integration
 * PnL curves, win rate donuts, radar charts, cost pie charts
 */

// ── Chart Registry (destroy before re-creating) ──
var _charts = {};
function getOrCreateChart(id, config) {
  if (_charts[id]) {
    _charts[id].destroy();
  }
  var ctx = document.getElementById(id);
  if (!ctx) return null;
  _charts[id] = new Chart(ctx.getContext('2d'), config);
  return _charts[id];
}

// ── Color Palette ──
var CHART_COLORS = {
  garves: '#00d4ff', soren: '#cc66ff', shelby: '#ffaa00',
  atlas: '#22aa44', lisa: '#ff8800', robotox: '#00ff44',
  thor: '#ff6600', hawk: '#FFD700', viper: '#00ff88', quant: '#00BFFF',
  up: '#00ff88', down: '#ff4466', neutral: '#888',
  local: '#00d4ff', cloud: '#ff8800', savings: '#00ff88',
};

// ── Chart Defaults ──
Chart.defaults.color = '#aaa';
Chart.defaults.borderColor = 'rgba(255,255,255,0.05)';
Chart.defaults.font.family = "'Inter', 'JetBrains Mono', monospace";

// ═══════════════════════════════════════════
//  PnL Equity Curve (Line Chart)
// ═══════════════════════════════════════════
function renderPnLChart(canvasId, trades, label) {
  if (!trades || trades.length === 0) return;
  var cumPnl = [];
  var labels = [];
  var running = 0;
  for (var i = 0; i < trades.length; i++) {
    running += (trades[i].pnl || 0);
    cumPnl.push(parseFloat(running.toFixed(2)));
    labels.push(trades[i].date || ('#' + (i + 1)));
  }
  var gradient = null;
  var ctx = document.getElementById(canvasId);
  if (ctx) {
    var g = ctx.getContext('2d');
    gradient = g.createLinearGradient(0, 0, 0, 200);
    var isPositive = running >= 0;
    gradient.addColorStop(0, isPositive ? 'rgba(0,255,136,0.3)' : 'rgba(255,68,102,0.3)');
    gradient.addColorStop(1, 'rgba(0,0,0,0)');
  }
  getOrCreateChart(canvasId, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: label || 'Cumulative PnL ($)',
        data: cumPnl,
        borderColor: running >= 0 ? CHART_COLORS.up : CHART_COLORS.down,
        backgroundColor: gradient || 'rgba(0,255,136,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { grid: { color: 'rgba(255,255,255,0.05)' } }
      }
    }
  });
}

// ═══════════════════════════════════════════
//  Win Rate Donut
// ═══════════════════════════════════════════
function renderWinRateDonut(canvasId, wins, losses) {
  var total = wins + losses;
  if (total === 0) return;
  getOrCreateChart(canvasId, {
    type: 'doughnut',
    data: {
      labels: ['Wins', 'Losses'],
      datasets: [{
        data: [wins, losses],
        backgroundColor: [CHART_COLORS.up, CHART_COLORS.down],
        borderWidth: 0,
        cutout: '75%',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
      }
    }
  });
}

// ═══════════════════════════════════════════
//  Agent Health Radar Chart
// ═══════════════════════════════════════════
function renderAgentRadar(canvasId, agentData) {
  if (!agentData || Object.keys(agentData).length === 0) return;
  var labels = [];
  var scores = [];
  var colors = [];
  for (var agent in agentData) {
    labels.push(agent.charAt(0).toUpperCase() + agent.slice(1));
    scores.push(agentData[agent]);
    colors.push(CHART_COLORS[agent] || '#888');
  }
  getOrCreateChart(canvasId, {
    type: 'radar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Health',
        data: scores,
        backgroundColor: 'rgba(0,212,255,0.15)',
        borderColor: '#00d4ff',
        borderWidth: 2,
        pointBackgroundColor: colors,
        pointRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          beginAtZero: true,
          max: 100,
          grid: { color: 'rgba(255,255,255,0.08)' },
          ticks: { display: false },
          pointLabels: { color: '#ccc', font: { size: 11 } },
        }
      },
      plugins: { legend: { display: false } }
    }
  });
}

// ═══════════════════════════════════════════
//  Cost Savings Pie (Local vs Cloud)
// ═══════════════════════════════════════════
function renderCostPie(canvasId, localCalls, cloudCalls) {
  if (localCalls + cloudCalls === 0) return;
  getOrCreateChart(canvasId, {
    type: 'doughnut',
    data: {
      labels: ['Local MLX', 'Cloud API'],
      datasets: [{
        data: [localCalls, cloudCalls],
        backgroundColor: [CHART_COLORS.local, CHART_COLORS.cloud],
        borderWidth: 0,
        cutout: '65%',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#ccc', padding: 12, usePointStyle: true } },
      }
    }
  });
}

// ═══════════════════════════════════════════
//  LLM Call Volume Timeline (Bar Chart)
// ═══════════════════════════════════════════
function renderCallTimeline(canvasId, calls) {
  if (!calls || calls.length === 0) return;
  // Group calls by hour
  var hourBuckets = {};
  for (var i = 0; i < calls.length; i++) {
    var ts = calls[i].timestamp || '';
    var hour = ts.substring(11, 13) || '??';
    if (!hourBuckets[hour]) hourBuckets[hour] = { local: 0, cloud: 0 };
    var provider = (calls[i].provider || '').toLowerCase();
    if (provider.includes('local')) hourBuckets[hour].local++;
    else hourBuckets[hour].cloud++;
  }
  var hours = Object.keys(hourBuckets).sort();
  var localData = hours.map(function(h) { return hourBuckets[h].local; });
  var cloudData = hours.map(function(h) { return hourBuckets[h].cloud; });

  getOrCreateChart(canvasId, {
    type: 'bar',
    data: {
      labels: hours.map(function(h) { return h + ':00'; }),
      datasets: [
        { label: 'Local', data: localData, backgroundColor: CHART_COLORS.local + '99', borderRadius: 3 },
        { label: 'Cloud', data: cloudData, backgroundColor: CHART_COLORS.cloud + '99', borderRadius: 3 },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' } },
      },
      plugins: {
        legend: { position: 'bottom', labels: { color: '#ccc', usePointStyle: true, padding: 8 } }
      }
    }
  });
}

// ═══════════════════════════════════════════
//  Memory Growth Bars (per agent)
// ═══════════════════════════════════════════
function renderMemoryBars(canvasId, agentMemory) {
  if (!agentMemory || Object.keys(agentMemory).length === 0) return;
  var agents = Object.keys(agentMemory).filter(function(a) {
    var s = agentMemory[a];
    return s && !s.error && (s.total_decisions > 0 || s.active_patterns > 0);
  });
  if (agents.length === 0) return;

  var decisions = agents.map(function(a) { return agentMemory[a].total_decisions || 0; });
  var patterns = agents.map(function(a) { return agentMemory[a].active_patterns || 0; });
  var bgColors = agents.map(function(a) { return (CHART_COLORS[a] || '#888') + 'cc'; });

  getOrCreateChart(canvasId, {
    type: 'bar',
    data: {
      labels: agents.map(function(a) { return a.charAt(0).toUpperCase() + a.slice(1); }),
      datasets: [
        { label: 'Decisions', data: decisions, backgroundColor: bgColors, borderRadius: 4 },
        { label: 'Patterns', data: patterns, backgroundColor: bgColors.map(function(c) { return c.replace('cc', '66'); }), borderRadius: 4 },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.05)' } },
        y: { grid: { display: false } },
      },
      plugins: {
        legend: { position: 'bottom', labels: { color: '#ccc', usePointStyle: true, padding: 8 } }
      }
    }
  });
}
