/**
 * 个股多信号扫描前端。
 *
 * 数据: GET /api/market/signals?code=&period=&count=
 * 口径: 规则引擎逐条列出触发条件与证据；strength 是规则权重，不是上涨概率。
 */
(function () {
  'use strict';

  var CACHE_MS = 30000;
  var state = { key: '', loadedAt: 0, data: null, loading: false, seq: 0 };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : String(s == null ? '' : s); }
  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(Number(v)); }

  function codeValue() {
    var el = $('ssCode');
    return (el && el.value.trim()) || '600519';
  }

  function periodValue() {
    var el = $('ssPeriod');
    return (el && el.value) || 'daily';
  }

  function stanceClass(stance) {
    if (stance === '偏多') return 'is-bull';
    if (stance === '偏空') return 'is-bear';
    return 'is-flat';
  }

  function render(data) {
    var body = $('ssBody');
    if (!body) return;
    if (!data) {
      body.innerHTML = '<div class="ss-empty">信号数据暂不可用，请稍后重试。</div>';
      return;
    }
    if (!data.available) {
      body.innerHTML = '<div class="ss-empty is-warn"><b>当前无法扫描</b><br>'
        + esc(data.reason || 'K 线不足或上游未返回') + '<br>'
        + '<span class="ss-foot-note">' + esc(data.note || '') + '</span></div>';
      return;
    }

    var s = data.summary || {};
    var signals = data.signals || [];
    var total = (Number(s.bull_score) || 0) + (Number(s.bear_score) || 0);
    var bullW = total ? (Number(s.bull_score) / total * 100) : 0;
    var bearW = total ? (Number(s.bear_score) / total * 100) : 0;

    var head = '<div class="ss-head">'
      + '<span class="ss-stance ' + stanceClass(s.stance) + '">' + esc(s.stance || '中性') + '</span>'
      + '<span class="ss-meta">' + esc(s.date || '—') + ' · 收盘 ' + (isNum(s.close) ? Number(s.close).toFixed(2) : '—') + '</span>'
      + '<span class="ss-counts">多头 <b class="is-bull">' + (s.bull_count || 0) + '</b> · 空头 <b class="is-bear">' + (s.bear_count || 0) + '</b></span>'
      + '<span class="ss-score">多空强度 ' + (s.bull_score || 0) + ' / ' + (s.bear_score || 0) + '</span>'
      + '</div>';

    var bar = '<div class="ss-strength" role="img" aria-label="多头强度 ' + (s.bull_score || 0) + '，空头强度 ' + (s.bear_score || 0) + '">'
      + '<span class="is-bull" style="width:' + bullW.toFixed(1) + '%"></span>'
      + '<span class="is-bear" style="width:' + bearW.toFixed(1) + '%"></span>'
      + '</div>';

    var items = signals.length ? signals.map(function (x) {
      var dir = x.direction === 'bull' ? 'is-bull' : (x.direction === 'bear' ? 'is-bear' : 'is-flat');
      var st = Math.max(0, Math.min(100, Number(x.strength) || 0));
      return '<div class="ss-item ' + dir + '">'
        + '<div class="ss-item-top"><span class="ss-dot"></span><span class="ss-name">' + esc(x.name) + '</span><b>' + st + '</b></div>'
        + '<div class="ss-meter"><i style="width:' + st + '%"></i></div>'
        + '<div class="ss-ev">' + esc(x.evidence) + '</div>'
        + '</div>';
    }).join('') : '<div class="ss-empty">当前没有触发任何规则信号</div>';

    body.innerHTML = head + bar + '<div class="ss-grid">' + items + '</div>'
      + '<div class="ss-foot">' + esc(data.note || '') + ' · 扫描 ' + esc(String(data.bars || '—')) + ' 根 K 线</div>';
  }

  async function load(force) {
    if (state.loading) return;
    var code = codeValue();
    var period = periodValue();
    var key = code + ':' + period;
    if (!force && state.data && state.key === key && Date.now() - state.loadedAt < CACHE_MS) {
      render(state.data);
      return;
    }
    state.loading = true;
    var seq = ++state.seq;
    var body = $('ssBody');
    if (body) body.innerHTML = '<div class="ss-empty">正在拉取 K 线并逐条核对信号…</div>';
    try {
      var resp = await api('/api/market/signals?code=' + encodeURIComponent(code)
        + '&period=' + encodeURIComponent(period) + '&count=180');
      if (seq !== state.seq) return;
      state.data = (resp && resp.data) ? resp.data : null;
      state.key = key;
      state.loadedAt = Date.now();
      render(state.data);
    } catch (e) {
      if (seq !== state.seq) return;
      if (body) {
        body.innerHTML = '<div class="ss-empty is-warn"><b>扫描失败</b><br>'
          + esc(e && e.message ? e.message : '请求失败')
          + '<br><span class="ss-foot-note">不会用 0 或模拟数据代替真实 K 线。</span></div>';
      }
    } finally {
      if (seq === state.seq) state.loading = false;
    }
  }

  function init() {
    var body = $('ssBody');
    if (!body) return;
    var codeEl = $('ssCode');
    if (codeEl && !codeEl.dataset.ssBound) {
      codeEl.dataset.ssBound = '1';
      codeEl.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') load(true); });
    }
  }

  window.SignalScan = { load: load };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
