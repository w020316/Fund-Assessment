/**
 * 缠论结构（简化）前端渲染。
 *
 * 数据: GET /api/market/chanlun?code=&period=&count=
 * 展示: 分型 / 笔 / 中枢 / MACD 面积背驰 / 买卖点观察 + SVG 结构图。
 * 口径: 后端已声明未实现线段与级别递归；这里只做可视化，不推断收益。
 */
(function () {
  'use strict';

  var CACHE_MS = 30000;
  var state = { key: '', loadedAt: 0, data: null, loading: false, seq: 0 };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : String(s == null ? '' : s); }
  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(Number(v)); }

  function codeValue() {
    var el = $('clCode');
    return (el && el.value.trim()) || '600519';
  }

  function periodValue() {
    var el = $('clPeriod');
    return (el && el.value) || 'daily';
  }

  function pct(n) { return isNum(n) ? Number(n).toFixed(2) : '—'; }

  function renderEmpty(text) {
    var body = $('clBody');
    if (body) body.innerHTML = '<div class="cl-empty">' + esc(text) + '</div>';
  }

  /* ---------- SVG 结构图 ---------- */
  function renderChart(data) {
    var bars = Array.isArray(data.bars) ? data.bars.slice(-120) : [];
    if (!bars.length) return '<div class="cl-empty">K 线为空，无法绘图</div>';

    var W = 920, H = 320, padL = 8, padR = 56, padT = 14, padB = 22;
    var n = bars.length;
    var min = Infinity, max = -Infinity;
    bars.forEach(function (b) {
      min = Math.min(min, b.low);
      max = Math.max(max, b.high);
    });
    if (!isFinite(min) || !isFinite(max) || max <= min) return '<div class="cl-empty">K 线价格异常，无法绘图</div>';
    var span = max - min;
    min -= span * 0.05;
    max += span * 0.05;

    var x = function (i) { return padL + (n <= 1 ? 0 : i * (W - padL - padR) / (n - 1)); };
    var y = function (p) { return padT + (max - p) * (H - padT - padB) / (max - min); };
    var idx = {};
    bars.forEach(function (b, i) { idx[b.date] = i; });
    /* 结构端点可能早于可视窗口：夹到首尾，保证中枢/笔仍能画出可见部分 */
    var clampIdx = function (date) {
      if (idx[date] !== undefined) return idx[date];
      if (!date) return null;
      if (date < bars[0].date) return 0;
      if (date > bars[n - 1].date) return n - 1;
      return null;
    };
    /* S9-13(2026-11-24): 完全早于可视窗口的元素不再夹到 0 画假矩形/竖线——
       9216f1b 只处理了"部分可见", 这里补"完全不可见"的分支; 部分可见的
       仍夹到窗口边缘正常绘制。 */
    var beforeWindow = function (date) {
      return !!bars.length && String(date) < String(bars[0].date);
    };

    var grid = '';
    for (var g = 0; g <= 4; g++) {
      var gy = padT + g * (H - padT - padB) / 4;
      var gp = max - g * (max - min) / 4;
      grid += '<line class="cl-grid" x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + gy.toFixed(1) + '"/>'
        + '<text class="cl-axis" x="' + (W - padR + 6) + '" y="' + (gy + 3).toFixed(1) + '">' + gp.toFixed(2) + '</text>';
    }

    var candles = bars.map(function (b, i) {
      var cx = x(i);
      var bw = Math.max(1.6, Math.min(5, (W - padL - padR) / n * 0.62));
      var up = b.close >= b.open;
      var cls = up ? 'cl-candle-up' : 'cl-candle-down';
      var yo = y(b.open), yc = y(b.close);
      var top = Math.min(yo, yc), hgt = Math.max(1, Math.abs(yc - yo));
      return '<g class="' + cls + '">'
        + '<line x1="' + cx.toFixed(1) + '" y1="' + y(b.high).toFixed(1) + '" x2="' + cx.toFixed(1) + '" y2="' + y(b.low).toFixed(1) + '"/>'
        + '<rect x="' + (cx - bw / 2).toFixed(1) + '" y="' + top.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + hgt.toFixed(1) + '"/>'
        + '</g>';
    }).join('');

    var pivots = (data.pivots || []).map(function (p) {
      if (beforeWindow(p.start_date) && beforeWindow(p.end_date)) return '';
      var a = clampIdx(p.start_date), b = clampIdx(p.end_date);
      if (a === null || b === null || b < a) return '';
      var x1 = x(a), x2 = x(b);
      var y1 = y(p.zg), y2 = y(p.zd);
      return '<g class="cl-pivot">'
        + '<rect x="' + x1.toFixed(1) + '" y="' + Math.min(y1, y2).toFixed(1) + '" width="' + Math.max(2, x2 - x1).toFixed(1) + '" height="' + Math.max(2, Math.abs(y2 - y1)).toFixed(1) + '"/>'
        + '<text x="' + (x1 + 4).toFixed(1) + '" y="' + (Math.min(y1, y2) - 4).toFixed(1) + '">中枢 ' + p.zd.toFixed(2) + '~' + p.zg.toFixed(2) + '</text>'
        + '</g>';
    }).join('');

    var points = (data.strokes || []).map(function (s) {
      if (beforeWindow(s.start_date) && beforeWindow(s.end_date)) return null;
      var a = clampIdx(s.start_date), b = clampIdx(s.end_date);
      if (a === null || b === null) return null;
      return [x(a), y(s.start_price), x(b), y(s.end_price)];
    }).filter(Boolean);
    var strokes = points.length
      ? '<polyline class="cl-stroke" points="' + points.map(function (p) {
        return p[0].toFixed(1) + ',' + p[1].toFixed(1);
      }).join(' ') + '"/>'
      : '';

    var markers = (data.signals || []).map(function (s, i) {
      var a = idx[s.date];
      if (a === undefined || !isNum(s.price)) return '';
      var cx = x(a), cy = y(Number(s.price));
      var isBuy = s.label.indexOf('买') >= 0;
      return '<g class="cl-signal ' + (isBuy ? 'is-buy' : 'is-sell') + '">'
        + '<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="3.4"/>'
        + '<text x="' + (cx - 12).toFixed(1) + '" y="' + (cy - 8 - i * 2).toFixed(1) + '">' + esc(s.label) + '</text>'
        + '</g>';
    }).join('');

    var first = bars[0].date, last = bars[n - 1].date;
    return '<div class="cl-chart-wrap">'
      + '<svg class="cl-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="缠论结构图：K 线、笔、中枢与买卖点观察">'
      + grid + candles + pivots + strokes + markers
      + '<text class="cl-axis" x="' + padL + '" y="' + (H - 6) + '">' + esc(first) + '</text>'
      + '<text class="cl-axis" x="' + (W - padR - 64) + '" y="' + (H - 6) + '">' + esc(last) + '</text>'
      + '</svg></div>'
      + '<div class="cl-legend"><span><i class="cl-lg-stroke"></i>笔</span><span><i class="cl-lg-pivot"></i>中枢</span>'
      + '<span><i class="cl-lg-up"></i>涨</span><span><i class="cl-lg-down"></i>跌</span>'
      + '<span class="cl-legend-note">最近 ' + n + ' 根 K 线</span></div>';
  }

  /* ---------- 摘要与明细 ---------- */
  function renderSummary(data) {
    var s = data.summary || {};
    var ls = s.last_stroke || {};
    var lp = s.last_pivot || {};
    var items = [
      {
        k: '当前笔',
        v: ls.direction ? ((ls.direction === 'up' ? '向上' : '向下') + ' ' + pct(ls.start_price) + ' → ' + pct(ls.end_price)) : '—',
        s: ls.start_date ? (ls.start_date + ' → ' + ls.end_date) : '笔数不足',
        cls: ls.direction === 'up' ? 'is-up' : (ls.direction === 'down' ? 'is-down' : '')
      },
      {
        k: '最近中枢',
        v: lp.zg != null ? (pct(lp.zd) + ' ~ ' + pct(lp.zg)) : '—',
        s: lp.position || '未形成三笔重叠',
        cls: ''
      },
      {
        k: '背驰',
        v: s.divergence || '未识别',
        s: data.divergence ? (data.divergence.date + ' · ' + pct(data.divergence.price)) : '同向笔 MACD 面积未缩小',
        cls: data.divergence ? (data.divergence.type === 'top' ? 'is-down' : 'is-up') : ''
      },
      {
        k: '最新信号',
        v: (data.signals && data.signals.length) ? data.signals[data.signals.length - 1].label : '无',
        s: (data.signals && data.signals.length) ? data.signals[data.signals.length - 1].note : '只输出可解释的结构观察',
        cls: ''
      }
    ];
    return '<div class="cl-summary">' + items.map(function (it) {
      return '<div class="cl-sum-item"><div class="cl-sum-k">' + esc(it.k) + '</div>'
        + '<div class="cl-sum-v ' + it.cls + '">' + esc(String(it.v)) + '</div>'
        + '<div class="cl-sum-s">' + esc(String(it.s)) + '</div></div>';
    }).join('') + '</div>';
  }

  function renderDetails(data) {
    var pivots = data.pivots || [];
    var signals = data.signals || [];
    var pRows = pivots.length
      ? pivots.slice().reverse().map(function (p) {
        return '<tr><td>' + esc(p.start_date) + ' → ' + esc(p.end_date) + '</td>'
          + '<td class="cl-num">' + pct(p.zd) + ' ~ ' + pct(p.zg) + '</td>'
          + '<td class="cl-num">' + pct(p.dd) + ' ~ ' + pct(p.gg) + '</td>'
          + '<td class="cl-num">' + p.strokes + '</td></tr>';
      }).join('')
      : '<tr><td colspan="4" class="cl-td-empty">暂无三笔重叠中枢</td></tr>';
    var sRows = signals.length
      ? signals.slice().reverse().map(function (x) {
        return '<tr><td><span class="cl-signal-tag ' + (x.label.indexOf('买') >= 0 ? 'is-buy' : 'is-sell') + '">' + esc(x.label) + '</span></td>'
          + '<td>' + esc(x.date) + '</td><td class="cl-num">' + pct(x.price) + '</td>'
          + '<td class="cl-td-note">' + esc(x.note) + '</td></tr>';
      }).join('')
      : '<tr><td colspan="4" class="cl-td-empty">暂无可解释的买卖点观察</td></tr>';

    return '<div class="cl-tables">'
      + '<div class="cl-table-block"><div class="cl-table-title">最近中枢</div>'
      + '<div class="table-wrap" tabindex="0"><table class="cl-table"><thead><tr><th>区间</th><th>中枢 ZD~ZG</th><th>波动 DD~GG</th><th>笔数</th></tr></thead><tbody>' + pRows + '</tbody></table></div></div>'
      + '<div class="cl-table-block"><div class="cl-table-title">买卖点观察</div>'
      + '<div class="table-wrap" tabindex="0"><table class="cl-table"><thead><tr><th>类型</th><th>日期</th><th>价格</th><th>说明</th></tr></thead><tbody>' + sRows + '</tbody></table></div></div>'
      + '</div>';
  }

  function render(data) {
    var body = $('clBody');
    if (!body) return;
    if (!data) {
      renderEmpty('结构数据暂不可用，请稍后重试。');
      return;
    }
    if (!data.available) {
      body.innerHTML = '<div class="cl-empty cl-empty-warn"><b>当前无法给出结构结论</b><br>'
        + esc(data.reason || 'K 线不足或上游未返回') + '<br>'
        + '<span class="cl-note">' + esc(data.note || '') + '</span></div>';
      return;
    }
    body.innerHTML = renderSummary(data) + renderChart(data) + renderDetails(data)
      + '<div class="cl-foot">' + esc(data.note || '') + ' · 合并 K 线 ' + esc(String(data.merged_count || '—')) + ' 根</div>';
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
    renderEmpty('正在拉取 K 线并计算分型 / 笔 / 中枢…');
    try {
      var resp = await api('/api/market/chanlun?code=' + encodeURIComponent(code)
        + '&period=' + encodeURIComponent(period) + '&count=180');
      if (seq !== state.seq) return;
      state.data = (resp && resp.data) ? resp.data : null;
      state.key = key;
      state.loadedAt = Date.now();
      render(state.data);
    } catch (e) {
      if (seq !== state.seq) return;
      var body = $('clBody');
      if (body) {
        body.innerHTML = '<div class="cl-empty cl-empty-warn"><b>结构分析失败</b><br>'
          + esc(e && e.message ? e.message : '请求失败') + '<br><span class="cl-note">不会用 0 或模拟数据代替真实 K 线。</span></div>';
      }
    } finally {
      if (seq === state.seq) state.loading = false;
    }
  }

  function wrapSwitchPage() {
    if (typeof window.switchPage !== 'function' || window.__chanlunWrapped) return;
    window.__chanlunWrapped = true;
    var orig = window.switchPage;
    window.switchPage = function (name) {
      var out = orig.apply(this, arguments);
      if (name === 'strategy') {
        try { load(false); } catch (e) { /* ignore */ }
      }
      return out;
    };
  }

  function init() {
    wrapSwitchPage();
    var btn = $('clBody');
    if (!btn) return;
    var codeEl = $('clCode');
    if (codeEl && !codeEl.dataset.clBound) {
      codeEl.dataset.clBound = '1';
      codeEl.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') load(true); });
    }
    var page = $('page-strategy');
    if (page && page.classList.contains('active')) load(false);
  }

  window.Chanlun = { load: load };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
