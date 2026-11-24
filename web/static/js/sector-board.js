/**
 * STAGE6-PRED: sector-board.js — 行情容器"板块"二级页(R2.1)。
 *
 * - 数据源: GET /api/market/sector_flow; 点击板块下钻 GET /api/market/sector_stocks
 * - 三个视图: 涨跌榜 / 资金流 / 强弱分布
 * - 强弱分布: 横轴主力净流入、纵轴涨跌幅的四象限气泡图(不虚构板块关系)
 * - 缺失数据一律「—」并说明原因, 不按 0 计入排序与图表
 * - 深链 #sector-board 首次落地自动加载
 */
/* global escapeHtml, Freshness */
(function () {
  'use strict';

  var state = {
    loading: false,
    data: null,
    meta: null,
    loadedAt: 0,
    tab: 'board',
    filter: 'all',
    query: '',
    lastError: '',
    drawer: {
      seq: 0,
      sector: '',
      code: '',
      loading: false,
      items: null,
      meta: null,
      error: '',
      trigger: null
    }
  };
  var searchTimer = null;
  var CACHE_MS = 15000;

  function $(id) { return document.getElementById(id); }
  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : ''; }

  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(Number(v)); }
  function hasPct(it) { return it && isNum(it.change_pct); }

  function fmtPct(v) {
    if (!isNum(v)) return '—';
    var n = Number(v);
    return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  function fmtYi(v, signed) {
    if (!isNum(v)) return '—';
    var n = Number(v);
    var sign = signed && n > 0 ? '+' : '';
    return sign + (n / 1e8).toFixed(2) + '\u00a0亿';
  }

  function fmtAmount(v) {
    if (!isNum(v)) return '—';
    var n = Number(v);
    var abs = Math.abs(n);
    if (abs >= 1e8) return (n / 1e8).toFixed(2) + '\u00a0亿';
    if (abs >= 1e4) return (n / 1e4).toFixed(1) + '\u00a0万';
    return n.toFixed(0) + '\u00a0元';
  }

  function clsOf(v) {
    if (!isNum(v) || Number(v) === 0) return '';
    return Number(v) > 0 ? 'is-up' : 'is-down';
  }

  function pctOf(n, total) {
    if (!total) return '—';
    return (n / total * 100).toFixed(0) + '%';
  }

  function sourceText(meta) {
    if (!meta) return '板块行情';
    if (meta.data_source === 'sina') return '新浪板块资金';
    if (meta.data_source === 'eastmoney') return '东财板块行情';
    return meta.data_source || '板块行情';
  }

  function flowUnavailableReason() {
    var meta = state.meta || {};
    var unavailable = meta.unavailable_fields || [];
    if (meta.degraded && unavailable.indexOf('main_net_inflow') >= 0) {
      return '上游未返回板块资金流';
    }
    var items = state.data || [];
    var known = items.filter(function (it) { return isNum(it.main_net_inflow); });
    if (!known.length) return '上游未返回板块资金流';
    if (known.every(function (it) { return Number(it.main_net_inflow) === 0; })) {
      return '上游资金流字段全为 0，按未提供处理';
    }
    return '';
  }

  function ratioUnavailable() {
    var meta = state.meta || {};
    var unavailable = meta.unavailable_fields || [];
    return !!(meta.degraded && unavailable.indexOf('large_order_ratio') >= 0);
  }

  function setKpi(id, value, note, cls) {
    var v = $(id);
    if (!v) return;
    v.textContent = value;
    v.className = 'qf-kpi-value' + (cls ? ' ' + cls : '');
    var n = $(id + 'Note');
    if (n) n.textContent = note;
  }

  function renderKpis() {
    var items = state.data || [];
    if (!items.length) {
      setKpi('sbKpiUp', '—', '暂无板块数据', '');
      setKpi('sbKpiDown', '—', '暂无板块数据', '');
      setKpi('sbKpiIn', '—', '暂无板块数据', '');
      setKpi('sbKpiOut', '—', '暂无板块数据', '');
      return;
    }
    var pctItems = items.filter(hasPct);
    var up = pctItems.filter(function (it) { return Number(it.change_pct) > 0; }).length;
    var down = pctItems.filter(function (it) { return Number(it.change_pct) < 0; }).length;
    var flat = pctItems.length - up - down;
    var noPct = items.length - pctItems.length;
    /* 口径(2026-11-22 收口): 分母恒为「有涨跌幅的板块数」pctItems.length,
       且 up + flat + down === pctItems.length(构造上必然成立);
       缺失涨跌幅的板块既不进分子也不进分母, 两个 KPI 用同一句话说明, 不再一处写
       「N 个缺失涨跌幅」、一处写「缺失不计入」。 */
    var denom = pctItems.length;
    var missNote = noPct ? ' · 缺失涨跌幅 ' + noPct + ' 个不计入' : '';
    var upNote = '占比 ' + pctOf(up, denom) + '（分母 ' + denom + ' 个）'
      + (flat ? ' · 平盘 ' + flat : '')
      + missNote;
    var downNote = '占比 ' + pctOf(down, denom) + '（分母 ' + denom + ' 个）' + missNote;

    setKpi('sbKpiUp', up + ' 个', upNote, up > 0 ? 'is-up' : '');
    setKpi('sbKpiDown', down + ' 个', downNote, down > 0 ? 'is-down' : '');

    var reason = flowUnavailableReason();
    if (reason) {
      setKpi('sbKpiIn', '—', reason + '，不按 0 计', '');
      setKpi('sbKpiOut', '—', reason + '，不按 0 计', '');
      return;
    }
    var flows = items.filter(function (it) { return isNum(it.main_net_inflow); });
    var total = flows.reduce(function (sum, it) { return sum + Number(it.main_net_inflow); }, 0);
    var positives = flows.filter(function (it) { return Number(it.main_net_inflow) > 0; })
      .sort(function (a, b) { return Number(b.main_net_inflow) - Number(a.main_net_inflow); });
    var negatives = flows.filter(function (it) { return Number(it.main_net_inflow) < 0; })
      .sort(function (a, b) { return Number(a.main_net_inflow) - Number(b.main_net_inflow); });
    var topIn = positives[0];
    var topOut = negatives[0];

    if (topIn) {
      setKpi('sbKpiIn', fmtYi(topIn.main_net_inflow, true),
        (topIn.sector || '—') + ' · ' + fmtPct(topIn.change_pct) + ' · 合计 ' + fmtYi(total, true), 'is-up');
    } else {
      setKpi('sbKpiIn', '—', '今日暂无净流入板块 · 合计 ' + fmtYi(total, true), '');
    }
    if (topOut) {
      setKpi('sbKpiOut', fmtYi(topOut.main_net_inflow, true),
        (topOut.sector || '—') + ' · ' + fmtPct(topOut.change_pct), 'is-down');
    } else {
      setKpi('sbKpiOut', '—', '今日暂无净流出板块（不是 0）', '');
    }
  }

  function renderMeta() {
    var el = $('sectorMeta');
    var badge = $('predSectorFreshness');
    var meta = state.meta || {};
    var asOf = meta.timestamp ? String(meta.timestamp).slice(11, 19) : '';
    var n = (state.data || []).length;
    if (el) {
      el.textContent = !state.data && state.lastError
        ? '数据源暂不可用'
        : sourceText(meta)
          + (asOf ? ' · ' + asOf : '')
          + ' · ' + n + ' 个板块'
          + (state.loading ? ' · 刷新中…' : (state.lastError ? ' · 刷新失败，保留上次数据' : ''));
    }
    if (badge) {
      badge.innerHTML = asOf
        ? Freshness.html({ asOf: asOf, note: sourceText(meta) })
        : '';
    }
  }

  function sortedItems() {
    var items = state.data || [];
    var has = items.filter(hasPct);
    var noPct = items.filter(function (it) { return !hasPct(it); });
    has.sort(function (a, b) { return Number(b.change_pct) - Number(a.change_pct); });
    return has.concat(noPct);
  }

  function filteredItems() {
    var list = sortedItems();
    var q = state.query.trim().toLowerCase();
    if (q) {
      list = list.filter(function (it) { return String(it.sector || '').toLowerCase().indexOf(q) >= 0; });
    }
    var f = state.filter;
    if (f === 'up') list = list.filter(function (it) { return hasPct(it) && Number(it.change_pct) > 0; });
    else if (f === 'down') list = list.filter(function (it) { return hasPct(it) && Number(it.change_pct) < 0; });
    else if (f === 'inflow') list = list.filter(function (it) { return isNum(it.main_net_inflow) && Number(it.main_net_inflow) > 0; });
    else if (f === 'outflow') list = list.filter(function (it) { return isNum(it.main_net_inflow) && Number(it.main_net_inflow) < 0; });
    return list;
  }

  function boardRowHTML(item, idx, flowOk, ratioOk) {
    var pctKnown = hasPct(item);
    var pct = pctKnown ? Number(item.change_pct) : null;
    var flowCell = flowOk
      ? '<td class="sb-num ' + clsOf(item.main_net_inflow) + '">' + esc(fmtYi(item.main_net_inflow, true)) + '</td>'
      : '<td class="sb-num">—</td>';
    var ratioCell = ratioOk
      ? '<td class="sb-num">' + (isNum(item.large_order_ratio) ? Number(item.large_order_ratio).toFixed(2) : '—') + '</td>'
      : '<td class="sb-num">—</td>';
    return '<tr class="sb-row-link" data-sb-sector="' + esc(item.sector || '') + '" data-sb-code="' + esc(item.code || '') + '"'
      + ' tabindex="0" role="button" aria-label="查看' + esc(item.sector || '该板块') + '成分股">'
      + '<td class="sb-rank">' + (idx + 1) + '</td>'
      + '<td class="sb-name">' + esc(item.sector || '—') + '</td>'
      + '<td class="sb-num ' + clsOf(pct) + '">' + (pctKnown ? esc(fmtPct(pct)) : '—') + '</td>'
      + flowCell + ratioCell + '</tr>';
  }

  function renderBoard() {
    var el = $('predSectorBoardBody');
    if (!el) return;
    var reason = flowUnavailableReason();
    var flowOk = !reason;
    var ratioOk = !ratioUnavailable();
    var list = filteredItems();
    if (!list.length) {
      el.innerHTML = '<div class="pred-empty">没有符合筛选条件的板块。'
        + '<button class="btn btn-sm btn-outline" type="button" data-sb-reset style="margin-left:8px;">清除筛选</button></div>';
      return;
    }
    var rows = list.map(function (it, i) { return boardRowHTML(it, i, flowOk, ratioOk); }).join('');
    var banner = reason
      ? '<div class="sb-banner">⚠ ' + esc(reason) + '，主力净流入与占比列显示「—」（不是 0）</div>'
      : '';
    el.innerHTML = banner
      + '<div class="table-wrap sb-table-wrap" tabindex="0">'
      + '<table class="pred-sector-table sb-table qf-table">'
      + '<thead><tr><th class="sb-rank">#</th><th>板块名称</th><th class="sb-num" data-sort-key="change_pct" data-sort-type="num">涨跌幅(%)</th>'
      + '<th class="sb-num" data-sort-key="net_inflow" data-sort-type="num">主力净流入(亿)</th><th class="sb-num" data-sort-key="net_ratio" data-sort-type="num">净流入占比(%)</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table></div>'
      + '<div class="pred-muted sb-foot">共 ' + list.length + ' 个板块'
      + (list.length !== (state.data || []).length ? '（已筛选，全市场 ' + (state.data || []).length + ' 个）' : '')
      + ' · 红涨绿跌 · 盘中快照，非投资建议</div>';
  }

  function flowRows(list, maxAbs, direction) {
    if (!list.length) return '<div class="pred-empty">暂无' + (direction > 0 ? '净流入' : '净流出') + '板块</div>';
    return list.map(function (it) {
      var v = Number(it.main_net_inflow);
      var w = Math.max(2, Math.round(Math.abs(v) / maxAbs * 100));
      return '<div class="sb-flow-row sb-row-link" data-sb-sector="' + esc(it.sector || '') + '" data-sb-code="' + esc(it.code || '') + '"'
        + ' tabindex="0" role="button" aria-label="查看' + esc(it.sector || '该板块') + '成分股">'
        + '<span class="sb-flow-name" title="' + esc(it.sector || '') + '">' + esc(it.sector || '—') + '</span>'
        + '<span class="sb-flow-bar"><i class="' + clsOf(v) + '" style="width:' + w + '%"></i></span>'
        + '<span class="sb-flow-val ' + clsOf(v) + '">' + esc(fmtYi(v, true)) + '</span>'
        + '<span class="sb-flow-pct ' + clsOf(it.change_pct) + '">' + esc(fmtPct(it.change_pct)) + '</span>'
        + '</div>';
    }).join('');
  }

  function renderFlow() {
    var el = $('predSectorBoardBody');
    if (!el) return;
    var reason = flowUnavailableReason();
    if (reason) {
      el.innerHTML = '<div class="pred-empty"><b>资金流暂不可用</b><br>' + esc(reason)
        + '。涨跌幅仍可在「涨跌榜」查看；缺失字段显示「—」而不是 0。</div>';
      return;
    }
    var items = (state.data || []).filter(function (it) { return isNum(it.main_net_inflow); });
    var pos = items.filter(function (it) { return Number(it.main_net_inflow) > 0; })
      .sort(function (a, b) { return Number(b.main_net_inflow) - Number(a.main_net_inflow); }).slice(0, 8);
    var neg = items.filter(function (it) { return Number(it.main_net_inflow) < 0; })
      .sort(function (a, b) { return Number(a.main_net_inflow) - Number(b.main_net_inflow); }).slice(0, 8);
    var maxPos = pos.length ? Math.abs(Number(pos[0].main_net_inflow)) : 1;
    var maxNeg = neg.length ? Math.abs(Number(neg[0].main_net_inflow)) : 1;
    var total = items.reduce(function (sum, it) { return sum + Number(it.main_net_inflow); }, 0);
    var inflowCount = items.filter(function (it) { return Number(it.main_net_inflow) > 0; }).length;
    var outflowCount = items.filter(function (it) { return Number(it.main_net_inflow) < 0; }).length;
    var src = sourceText(state.meta);

    el.innerHTML = '<div class="sb-flow-head">'
      + '<div><span class="sb-flow-head-label">板块主力合计</span>'
      + '<b class="sb-flow-head-val ' + clsOf(total) + '">' + esc(fmtYi(total, true)) + '</b></div>'
      + '<div class="pred-muted">净流入 ' + inflowCount + ' 个 · 净流出 ' + outflowCount
      + ' 个 · 共 ' + items.length + ' 个板块</div></div>'
      + '<div class="sb-flow-cols">'
      + '<section><h3 class="sb-flow-title is-up">主力净流入 TOP8</h3>'
      + flowRows(pos, maxPos, 1) + '</section>'
      + '<section><h3 class="sb-flow-title is-down">主力净流出 TOP8</h3>'
      + flowRows(neg, maxNeg, -1) + '</section>'
      + '</div>'
      + '<div class="pred-muted sb-foot">资金流来源：' + esc(src) + '；左右两栏条形各自缩放，仅用于组内比较。缺失值不补零。</div>';
  }

  function shortName(name) {
    var s = String(name || '');
    return s.length > 10 ? s.slice(0, 10) + '…' : s;
  }

  function placeLabels(points, bounds) {
    var candidates = points.slice().sort(function (a, b) { return b.score - a.score; }).slice(0, 10);
    /* 标签避让：不仅彼此不重叠，也不压住其它气泡；放不下就留白，
       明细仍可悬停/聚焦气泡查看。 */
    var obstacles = points.map(function (p) {
      return { x: p.x - p.r - 2, y: p.y - p.r - 2, w: p.r * 2 + 4, h: p.r * 2 + 4 };
    });
    var placed = [];
    var out = [];
    function hits(box) {
      var all = placed.concat(obstacles);
      for (var i = 0; i < all.length; i++) {
        var p = all[i];
        if (box.x < p.x + p.w && box.x + box.w > p.x && box.y < p.y + p.h && box.y + box.h > p.y) return true;
      }
      return false;
    }
    candidates.forEach(function (p) {
      var text = shortName(p.item.sector);
      var w = text.length * 10 + 6;
      var h = 13;
      var tries = [
        [p.x + p.r + 5, p.y + 3],
        [p.x - p.r - 5 - w, p.y + 3],
        [p.x - w / 2, p.y - p.r - 9],
        [p.x - w / 2, p.y + p.r + 13],
        [p.x + p.r + 2, p.y - p.r + 2],
        [p.x + p.r + 2, p.y + p.r + 12],
        [p.x - p.r - 2 - w, p.y - p.r + 2],
        [p.x - p.r - 2 - w, p.y + p.r + 12]
      ];
      for (var i = 0; i < tries.length; i++) {
        var tx = tries[i][0];
        var ty = tries[i][1];
        if (tx < bounds.x || tx + w > bounds.x + bounds.w) continue;
        if (ty - 10 < bounds.y || ty > bounds.y + bounds.h) continue;
        var box = { x: tx, y: ty - 10, w: w, h: h };
        if (hits(box)) continue;
        placed.push(box);
        out.push('<text class="sb-bubble-label" x="' + tx.toFixed(1) + '" y="' + ty.toFixed(1) + '">' + esc(text) + '</text>');
        break;
      }
    });
    return out.join('');
  }

  function renderGraph() {
    var el = $('predSectorBoardBody');
    if (!el) return;
    var reason = flowUnavailableReason();
    if (reason) {
      el.innerHTML = '<div class="pred-empty"><b>强弱分布暂不可用</b><br>' + esc(reason)
        + '。该图同时需要涨跌幅与主力净流入，缺一不可；缺失字段不以 0 顶替。</div>';
      return;
    }
    var all = state.data || [];
    var points = all.filter(function (it) {
      return hasPct(it) && isNum(it.main_net_inflow);
    }).map(function (it) {
      return { item: it, pct: Number(it.change_pct), flow: Number(it.main_net_inflow) };
    });
    var missing = all.length - points.length;
    if (points.length < 3) {
      el.innerHTML = '<div class="pred-empty">可用于绘图的板块不足（' + points.length + ' 个），暂不绘制分布图。</div>';
      return;
    }

    var W = 920, H = 470, padL = 60, padR = 24, padT = 30, padB = 46;
    var chartW = W - padL - padR;
    var chartH = H - padT - padB;
    var midX = padL + chartW / 2;
    var midY = padT + chartH / 2;
    var maxPos = Math.max(1, Math.max.apply(null, points.filter(function (p) { return p.flow > 0; }).map(function (p) { return p.flow; }).concat([0])));
    var maxNeg = Math.max(1, Math.max.apply(null, points.filter(function (p) { return p.flow < 0; }).map(function (p) { return -p.flow; }).concat([0])));
    if (!points.some(function (p) { return p.flow > 0; })) maxPos = maxNeg;
    if (!points.some(function (p) { return p.flow < 0; })) maxNeg = maxPos;
    var maxFlow = Math.max(maxPos, maxNeg);
    var maxAbsPct = Math.max(0.5, Math.max.apply(null, points.map(function (p) { return Math.abs(p.pct); })));
    var halfW = chartW / 2;
    var halfH = chartH / 2;

    function xOf(flow) {
      /* 资金流分布高度右偏(多数板块 <1 亿、个别 60 亿+),线性轴会把大多数点
         挤在 0 附近;按 √|flow| 展开两侧,轴刻度仍标真实金额,并在脚注注明。 */
      return flow >= 0
        ? midX + Math.sqrt(flow / maxPos) * halfW
        : midX - Math.sqrt((-flow) / maxNeg) * halfW;
    }
    function yOf(pct) {
      var clipped = Math.max(-maxAbsPct, Math.min(maxAbsPct, pct));
      return midY - (clipped / maxAbsPct) * halfH;
    }

    var pctTicks = [maxAbsPct, maxAbsPct / 2, 0, -maxAbsPct / 2, -maxAbsPct];
    var yAxis = pctTicks.map(function (v) {
      var y = yOf(v);
      return '<line class="sb-grid-line' + (v === 0 ? ' is-zero' : '') + '" x1="' + padL + '" y1="' + y.toFixed(1)
        + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '"/>'
        + '<text class="sb-axis-text" x="' + (padL - 8) + '" y="' + (y + 3.5).toFixed(1) + '" text-anchor="end">'
        + esc((v > 0 ? '+' : '') + v.toFixed(1) + '%') + '</text>';
    }).join('');

    var xTicks = [
      { v: -maxNeg, label: '-' + (maxNeg / 1e8).toFixed(1) + ' 亿' },
      { v: -maxNeg / 2, label: '-' + (maxNeg / 2 / 1e8).toFixed(1) },
      { v: 0, label: '0' },
      { v: maxPos / 2, label: '+' + (maxPos / 2 / 1e8).toFixed(1) },
      { v: maxPos, label: '+' + (maxPos / 1e8).toFixed(1) + ' 亿' }
    ];
    var xAxis = xTicks.map(function (t) {
      var x = t.v === 0 ? midX : xOf(t.v);
      return '<line class="sb-grid-line' + (t.v === 0 ? ' is-zero' : '') + '" x1="' + x.toFixed(1) + '" y1="' + padT
        + '" x2="' + x.toFixed(1) + '" y2="' + (padT + chartH) + '"/>'
        + '<text class="sb-axis-text" x="' + x.toFixed(1) + '" y="' + (padT + chartH + 20) + '" text-anchor="middle">'
        + esc(t.label) + '</text>';
    }).join('');

    var bubbles = points.map(function (p) {
      var r = 3.5 + 12 * Math.sqrt(Math.abs(p.flow) / maxFlow);
      var cls = clsOf(p.flow);
      var title = (p.item.sector || '—') + '｜涨跌幅 ' + fmtPct(p.pct)
        + '｜主力净流入 ' + fmtYi(p.flow, true)
        + (isNum(p.item.large_order_ratio) ? '｜占比 ' + Number(p.item.large_order_ratio).toFixed(2) : '');
      p.x = xOf(p.flow);
      p.y = yOf(p.pct);
      p.r = r;
      p.score = (Math.abs(p.flow) / maxFlow) * 0.65 + (Math.abs(p.pct) / maxAbsPct) * 0.35;
      return '<g class="sb-bubble sb-row-link ' + cls + '" tabindex="0" role="button"'
        + ' data-sb-sector="' + esc(p.item.sector || '') + '" data-sb-code="' + esc(p.item.code || '') + '"'
        + ' aria-label="' + esc(title) + '，查看成分股">'
        + '<title>' + esc(title) + '</title>'
        + '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="' + r.toFixed(1) + '"/>'
        + '</g>';
    }).join('');

    var labels = placeLabels(points, { x: padL, y: padT, w: chartW, h: chartH });
    var quadrants = ''
      + '<rect class="sb-quad is-up" x="' + midX + '" y="' + padT + '" width="' + halfW + '" height="' + halfH + '"/>'
      + '<rect class="sb-quad is-warn" x="' + padL + '" y="' + padT + '" width="' + halfW + '" height="' + halfH + '"/>'
      + '<rect class="sb-quad is-down" x="' + padL + '" y="' + midY + '" width="' + halfW + '" height="' + halfH + '"/>'
      + '<rect class="sb-quad is-warn" x="' + midX + '" y="' + midY + '" width="' + halfW + '" height="' + halfH + '"/>'
      + '<text class="sb-quad-label is-warn" x="' + (padL + 10) + '" y="' + (padT + 17) + '">涨 · 资金流出</text>'
      + '<text class="sb-quad-label is-up" x="' + (W - padR - 10) + '" y="' + (padT + 17) + '" text-anchor="end">涨 · 资金流入</text>'
      + '<text class="sb-quad-label is-down" x="' + (padL + 10) + '" y="' + (padT + chartH - 10) + '">跌 · 资金流出</text>'
      + '<text class="sb-quad-label is-warn" x="' + (W - padR - 10) + '" y="' + (padT + chartH - 10) + '" text-anchor="end">跌 · 资金流入</text>';

    el.innerHTML = '<div class="sb-graph-wrap">'
      + '<svg class="sb-graph" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="板块主力净流入与涨跌幅四象限气泡图">'
      + quadrants + yAxis + xAxis
      + '<text class="sb-axis-title" x="' + padL + '" y="' + (padT - 12) + '">涨跌幅（%）</text>'
      + '<text class="sb-axis-title" x="' + (W - padR) + '" y="' + (H - 10) + '" text-anchor="end">主力净流入（左右分栏）</text>'
      + bubbles + labels
      + '</svg></div>'
      + '<div class="sb-legend">'
      + '<span><i class="sb-dot is-up"></i>主力净流入</span>'
      + '<span><i class="sb-dot is-down"></i>主力净流出</span>'
      + '<span>气泡越大 = |净流入| 越大</span>'
      + '<span>绘入 ' + points.length + ' 个板块' + (missing ? '；' + missing + ' 个字段缺失未入图' : '') + '</span>'
      + '</div>'
      + '<div class="pred-muted sb-foot">横轴以 0 为中线左右等宽分栏、按 √|净流入| 展开（两侧各自缩放），避免小额资金挤在 0 附近；悬停或聚焦气泡查看明细。缺失字段不按 0 入图。</div>';
  }

  function renderTab() {
    var tabs = document.querySelectorAll('#page-sector-board .sb-tab');
    for (var i = 0; i < tabs.length; i++) {
      var on = tabs[i].getAttribute('data-sb-tab') === state.tab;
      tabs[i].classList.toggle('is-active', on);
      tabs[i].setAttribute('aria-selected', on ? 'true' : 'false');
    }
    var filters = $('sbFilters');
    if (filters) filters.style.display = state.tab === 'board' ? '' : 'none';
    if (state.tab === 'flow') renderFlow();
    else if (state.tab === 'graph') renderGraph();
    else renderBoard();
  }

  function renderAll() {
    renderKpis();
    renderMeta();
    renderTab();
  }

  function renderLoadError() {
    setKpi('sbKpiUp', '—', '数据源暂不可用', '');
    setKpi('sbKpiDown', '—', '数据源暂不可用', '');
    setKpi('sbKpiIn', '—', '数据源暂不可用', '');
    setKpi('sbKpiOut', '—', '数据源暂不可用', '');
    var el = $('predSectorBoardBody');
    if (el) {
      el.innerHTML = '<div class="pred-empty"><b>板块数据加载失败</b><br>数据源暂不可用或请求超时，请重试。'
        + '<button class="btn btn-sm btn-outline" type="button" data-sb-retry style="margin-left:8px;">重试</button></div>';
    }
    var meta = $('sectorMeta');
    if (meta) meta.textContent = '数据源暂不可用';
    var badge = $('predSectorFreshness');
    if (badge) badge.innerHTML = '';
  }

  function ensureLoadingPlaceholder() {
    if (state.data) return;
    var el = $('predSectorBoardBody');
    if (el) el.innerHTML = '<div class="pred-empty">加载板块行情…</div>';
  }

  async function fetchJSON(url) {
    try {
      var headers = {};
      try {
        var t = localStorage.getItem('admin_token');
        if (t) headers.Authorization = 'Bearer ' + t;
      } catch (e) { /* ignore */ }
      var resp = await fetch(url, { headers: headers });
      var body = await resp.json();
      return { ok: resp.ok, body: body };
    } catch (e) {
      return { ok: false, body: null, error: e };
    }
  }

  async function load(force) {
    if (state.loading) return;
    if (!force && state.data && Date.now() - state.loadedAt < CACHE_MS) {
      renderAll();
      return;
    }
    state.loading = true;
    ensureLoadingPlaceholder();
    renderMeta();
    try {
      var r = await fetchJSON('/api/market/sector_flow' + (force ? '?_t=' + Date.now() : ''));
      var items = (r.ok && r.body && Array.isArray(r.body.data)) ? r.body.data : null;
      if (!items) {
        state.lastError = 'fetch-failed';
        if (!state.data) renderLoadError();
        return;
      }
      state.lastError = '';
      state.data = items;
      state.meta = (r.body && r.body._meta) || {};
      state.loadedAt = Date.now();
      if (!items.length) {
        var el = $('predSectorBoardBody');
        if (el) el.innerHTML = '<div class="pred-empty">数据源暂未返回板块数据，请稍后重试。</div>';
        renderKpis();
        renderMeta();
        return;
      }
      renderAll();
    } finally {
      state.loading = false;
      renderMeta();
    }
  }

  function setTab(tab) {
    if (['board', 'flow', 'graph'].indexOf(tab) < 0) return;
    state.tab = tab;
    if (!state.data) load(false);
    else renderTab();
  }

  function openDrawer(sector, code) {
    var d = state.drawer;
    var panel = $('sbDrawer');
    var backdrop = $('sbDrawerBackdrop');
    if (!panel || !backdrop) return;
    d.trigger = document.activeElement;
    d.sector = sector || '';
    d.code = code || '';
    d.items = null;
    d.meta = null;
    d.error = '';
    d.loading = true;
    panel.hidden = false;
    backdrop.hidden = false;
    panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('sb-drawer-open');
    renderDrawer();
    requestAnimationFrame(function () {
      panel.classList.add('is-open');
      backdrop.classList.add('is-open');
    });
    var closeBtn = $('sbDrawerClose');
    if (closeBtn) closeBtn.focus();
    loadStocks();
  }

  function closeDrawer() {
    var panel = $('sbDrawer');
    var backdrop = $('sbDrawerBackdrop');
    if (!panel || panel.hidden) return;
    panel.classList.remove('is-open');
    backdrop.classList.remove('is-open');
    panel.hidden = true;
    backdrop.hidden = true;
    panel.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('sb-drawer-open');
    var trigger = state.drawer.trigger;
    if (trigger && typeof trigger.focus === 'function') {
      try { trigger.focus(); } catch (e) { /* ignore */ }
    }
  }

  function findSector(sector, code) {
    var items = state.data || [];
    for (var i = 0; i < items.length; i++) {
      if (code && items[i].code === code) return items[i];
      if (!code && sector && items[i].sector === sector) return items[i];
    }
    return null;
  }

  function renderDrawer() {
    var d = state.drawer;
    var title = $('sbDrawerTitle');
    var sub = $('sbDrawerSub');
    var metaEl = $('sbDrawerMeta');
    var body = $('sbDrawerBody');
    if (!body) return;
    var current = findSector(d.sector, d.code);

    if (title) title.textContent = d.sector || '板块成分股';
    if (sub) {
      var bits = [];
      if (current) {
        bits.push('涨跌幅 ' + fmtPct(current.change_pct));
        bits.push('主力净流入 ' + fmtYi(current.main_net_inflow, true));
      }
      if (d.code) bits.push(d.code);
      sub.textContent = bits.length ? bits.join(' · ') : '成分股';
    }

    if (metaEl) {
      if (d.loading) {
        metaEl.textContent = '读取成分股…';
      } else if (d.error) {
        metaEl.textContent = '成分股数据源暂不可用';
      } else if (d.items && d.items.length) {
        var asOf = d.meta && d.meta.timestamp ? String(d.meta.timestamp).slice(11, 19) : '';
        metaEl.textContent = sourceText(d.meta) + (asOf ? ' · ' + asOf : '') + ' · ' + d.items.length + ' 只';
      } else {
        var unavailable = d.meta && d.meta.unavailable_fields ? d.meta.unavailable_fields : [];
        metaEl.textContent = unavailable.length ? '上游未返回成分股' : '暂无成分股';
      }
    }

    if (d.loading) {
      body.innerHTML = '<div class="pred-empty">加载成分股…</div>';
      return;
    }
    if (d.error) {
      body.innerHTML = '<div class="pred-empty"><b>成分股加载失败</b><br>数据源暂不可用或请求超时。'
        + '<button class="btn btn-sm btn-outline" type="button" data-sb-drawer-retry style="margin-left:8px;">重试</button></div>';
      return;
    }
    if (!d.items || !d.items.length) {
      body.innerHTML = '<div class="pred-empty"><b>该板块暂未返回成分股</b><br>'
        + '可能原因：板块代码与当前资金榜口径不一致（新浪资金榜无成分股接口），或上游暂时限流。'
        + '缺失数据不补零，也不生成示例个股。'
        + '<button class="btn btn-sm btn-outline" type="button" data-sb-drawer-retry style="margin-left:8px;">重试</button></div>';
      return;
    }

    var list = d.items.slice();
    var withPct = list.filter(hasPct);
    var noPct = list.filter(function (it) { return !hasPct(it); });
    withPct.sort(function (a, b) { return Number(b.change_pct) - Number(a.change_pct); });
    list = withPct.concat(noPct);
    var rows = list.map(function (it) {
      var code = String(it.code || '');
      return '<tr class="sb-stock-row" data-sb-stock="' + esc(code) + '" tabindex="0" role="button"'
        + ' aria-label="查看 ' + esc(it.name || code) + ' ' + esc(code) + ' 详情">'
        + '<td class="sb-stock-code">' + esc(code || '—') + '</td>'
        + '<td class="sb-stock-name">' + esc(it.name || '—') + '</td>'
        + '<td class="sb-num">' + (isNum(it.price) ? Number(it.price).toFixed(2) : '—') + '</td>'
        + '<td class="sb-num ' + clsOf(it.change_pct) + '">' + esc(fmtPct(it.change_pct)) + '</td>'
        + '<td class="sb-num">' + esc(fmtAmount(it.amount)) + '</td></tr>';
    }).join('');

    body.innerHTML = '<div class="sb-drawer-table-wrap" tabindex="0">'
      + '<table class="pred-sector-table sb-table sb-stock-table qf-table">'
      + '<thead><tr><th>代码</th><th>名称</th><th class="sb-num" data-sort-key="price" data-sort-type="num">最新价</th>'
      + '<th class="sb-num" data-sort-key="change_pct" data-sort-type="num">涨跌幅</th><th class="sb-num" data-sort-key="amount" data-sort-type="num">成交额</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table></div>'
      + '<div class="pred-muted sb-foot">共 ' + list.length + ' 只 · 按涨跌幅降序，缺失值排在最后 · 点击任意个股进入行情总览</div>';
  }

  async function loadStocks() {
    var d = state.drawer;
    if (!d.sector && !d.code) {
      d.loading = false;
      d.error = 'missing';
      renderDrawer();
      return;
    }
    var seq = ++d.seq;
    d.loading = true;
    d.error = '';
    renderDrawer();
    var params = ['limit=100', '_t=' + Date.now()];
    if (d.code) params.push('code=' + encodeURIComponent(d.code));
    if (d.sector) params.push('name=' + encodeURIComponent(d.sector));
    var r = await fetchJSON('/api/market/sector_stocks?' + params.join('&'));
    if (seq !== d.seq) return;
    var body = (r && r.body) || {};
    d.loading = false;
    if (r && r.ok && Array.isArray(body.data)) {
      d.items = body.data;
      d.meta = body._meta || {};
      d.error = '';
    } else {
      d.items = null;
      d.meta = body._meta || null;
      d.error = 'fetch-failed';
    }
    renderDrawer();
  }

  function jumpToStock(code) {
    if (!code) return;
    closeDrawer();
    if (typeof window.switchPage === 'function') window.switchPage('market');
    var input = $('stockSearchInput');
    if (input) input.value = code;
    if (typeof window.loadStockDetail === 'function') window.loadStockDetail(code);
    var card = $('stockSearchCard');
    if (card && card.scrollIntoView) {
      setTimeout(function () { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 60);
    }
  }

  function bindUI() {
    var page = $('page-sector-board');
    if (!page || page.__sbBound) return;
    page.__sbBound = true;

    page.addEventListener('click', function (e) {
      var t = e.target;
      var tabBtn = t.closest ? t.closest('[data-sb-tab]') : null;
      if (tabBtn) { setTab(tabBtn.getAttribute('data-sb-tab')); return; }
      var chip = t.closest ? t.closest('[data-sb-filter]') : null;
      if (chip) {
        state.filter = chip.getAttribute('data-sb-filter');
        var chips = page.querySelectorAll('.sb-chip');
        for (var i = 0; i < chips.length; i++) chips[i].classList.toggle('is-active', chips[i] === chip);
        renderTab();
        return;
      }
      if (t.closest && (t.closest('#sbDrawerClose') || t.closest('#sbDrawerBackdrop'))) {
        closeDrawer();
        return;
      }
      if (t.closest && t.closest('[data-sb-drawer-retry]')) { loadStocks(); return; }
      var stockRow = t.closest ? t.closest('[data-sb-stock]') : null;
      if (stockRow) {
        jumpToStock(stockRow.getAttribute('data-sb-stock'));
        return;
      }
      var sectorEl = t.closest ? t.closest('[data-sb-sector]') : null;
      if (sectorEl && !sectorEl.closest('#sbDrawer')) {
        openDrawer(sectorEl.getAttribute('data-sb-sector'), sectorEl.getAttribute('data-sb-code'));
        return;
      }
      if (t.closest && t.closest('[data-sb-retry]')) { load(true); return; }
      if (t.closest && t.closest('[data-sb-reset]')) {
        state.filter = 'all';
        state.query = '';
        var input = $('sbSearch');
        if (input) input.value = '';
        var allChip = page.querySelector('[data-sb-filter="all"]');
        if (allChip) {
          var all = page.querySelectorAll('.sb-chip');
          for (var j = 0; j < all.length; j++) all[j].classList.toggle('is-active', all[j] === allChip);
        }
        renderTab();
      }
    });

    var input = $('sbSearch');
    if (input) {
      input.addEventListener('input', function () {
        state.query = input.value;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
          if (state.tab === 'board') renderBoard();
        }, 120);
      });
    }

    page.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        var panel = $('sbDrawer');
        if (panel && !panel.hidden) { e.preventDefault(); closeDrawer(); }
        return;
      }
      if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
      var t = e.target;
      var stockRow = t.closest ? t.closest('[data-sb-stock]') : null;
      if (stockRow) {
        e.preventDefault();
        jumpToStock(stockRow.getAttribute('data-sb-stock'));
        return;
      }
      var sectorEl = t.closest ? t.closest('[data-sb-sector]') : null;
      if (sectorEl && !sectorEl.closest('#sbDrawer')) {
        e.preventDefault();
        openDrawer(sectorEl.getAttribute('data-sb-sector'), sectorEl.getAttribute('data-sb-code'));
      }
    });
  }

  function maybeAutoLoad() {
    var page = $('page-sector-board');
    if (page && page.classList.contains('active')) load(false);
  }

  function wrapSwitchPage() {
    if (typeof window.switchPage !== 'function' || window.__sectorBoardWrapped) return;
    window.__sectorBoardWrapped = true;
    var orig = window.switchPage;
    window.switchPage = function (name) {
      var out = orig.apply(this, arguments);
      if (name === 'sector-board') {
        try { load(false); } catch (e) { /* ignore */ }
      }
      return out;
    };
  }

  function init() {
    bindUI();
    wrapSwitchPage();
    maybeAutoLoad();
  }

  window.SectorBoard = { load: load, setTab: setTab, openSector: openDrawer, closeDrawer: closeDrawer };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  window.addEventListener('hashchange', maybeAutoLoad);
})();
