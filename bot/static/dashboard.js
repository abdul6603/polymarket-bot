```javascript
// ... (keep all existing code exactly as is until line 1247) ...

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

// ... (keep all remaining code exactly as is) ...
```