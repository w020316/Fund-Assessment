/**
 * STAGE7: workbench.js — 工作台（一屏判读）
 *
 * 数据来源：
 *   GET /api/market/index_realtime   指数
 *   GET /api/market/thermometer      大盘温度计（四维拆解）
 *   GET /api/review/board            涨停/炸板/连板/情绪温度
 *   GET /api/market/sector_flow      行业涨跌
 *   GET /api/market/hot_stocks       涨幅/成交额排行
 *   GET /api/market/ddx_streak       资金连增（连续主力净流入，异步补充，不阻塞首屏）
 *   GET /api/portfolio/watchboard    持仓盘中估值（需 ADMIN_TOKEN；开发模式放行）
 *
 * 红线：
 * - 缺失字段一律显示「—」并给出原因，不用 0 或占位数字顶替；
 * - 判读文案标注「规则引擎，非投资建议」；
 * - 所有上游字符串进入 innerHTML 前 escapeHtml。
 */
/* global escapeHtml, switchPage */
(function () {
  'use strict';

  var REFRESH_MS = 60000;
  var LS_AUTO = 'qf_workbench_auto_v1';
  var SESSION_TEXT = {
    morning: '盘中 · 早盘',
    afternoon: '盘中 · 午盘',
    lunch_break: '午间休市',
    closed: '已收盘',
    weekend: '周末休市'
  };
  /* 资金连增来源：候选池已接入东财排行 + 同花顺涨停池，资金流本身分历史/快照 */
  var SOURCE_TEXT = {
    eastmoney: '东财',
    akshare: 'akshare',
    sina_history: '新浪历史',
    sina: '新浪快照',
    unknown: '来源未标'
  };
  var state = { timer: null, auto: true, loading: false, loadedAt: 0, lastError: '', session: null, health: [], ddxSeq: 0 };

  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : ''; }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function num(v, d) {
    if (!isNum(v)) return '—';
    return Number(v).toFixed(d == null ? 2 : d);
  }
  function signed(v, d) {
    if (!isNum(v)) return '—';
    return (v > 0 ? '+' : '') + Number(v).toFixed(d == null ? 2 : d);
  }
  function pctText(v, d) {
    if (!isNum(v)) return '—';
    return signed(v, d == null ? 2 : d) + '%';
  }
  function clsOf(v) {
    if (!isNum(v) || v === 0) return '';
    return v > 0 ? 'is-up' : 'is-down';
  }
  /** 指数成交额单位：万元 → 亿/万亿 */
  function fmtAmountWan(v) {
    if (!isNum(v)) return '—';
    var yi = v / 1e4;
    if (yi >= 1e4) return (yi / 1e4).toFixed(2) + '万亿';
    if (yi >= 1) return yi.toFixed(0) + '亿';
    return (v * 1e4 / 1e8).toFixed(2) + '亿';
  }
  /** 个股成交额单位：元 */
  function fmtYuanAmount(v) {
    if (!isNum(v)) return '—';
    if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿';
    if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return v.toFixed(0);
  }
  /** 主力净流入：元 → 带符号的亿/万 */
  function fmtNetYuan(v) {
    if (!isNum(v)) return '—';
    var sign = v > 0 ? '+' : (v < 0 ? '-' : '');
    var abs = Math.abs(v);
    if (abs >= 1e8) return sign + (abs / 1e8).toFixed(2) + '亿';
    if (abs >= 1e4) return sign + (abs / 1e4).toFixed(0) + '万';
    return sign + abs.toFixed(0);
  }
  function fmtMoney(v) {
    if (!isNum(v)) return '—';
    var sign = v > 0 ? '+' : (v < 0 ? '-' : '');
    return sign + '¥' + Math.abs(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function timeText() {
    var d = new Date();
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') + ':' + String(d.getSeconds()).padStart(2, '0');
  }

  async function fetchJSON(url) {
    var headers = {};
    try {
      var t = localStorage.getItem('admin_token');
      if (t) headers.Authorization = 'Bearer ' + t;
    } catch (e) { /* localStorage 禁用时按匿名请求 */ }
    try {
      var resp = await fetch(url, { headers: headers });
      var body = null;
      try { body = await resp.json(); } catch (e2) { body = null; }
      return { ok: resp.ok, status: resp.status, body: body };
    } catch (e) {
      return { ok: false, status: 0, body: null, error: e };
    }
  }

  function $id(id) { return document.getElementById(id); }

  /* ================= 渲染：页头 ================= */
  function renderHead(board, thermo) {
    var dateEl = $id('wbDate');
    if (dateEl) dateEl.textContent = (board && board.date) ? board.date : '—';

    var phase = classifyPhase(board);
    var phaseTag = $id('wbPhaseTag');
    if (phaseTag) {
      phaseTag.textContent = phase.label;
      phaseTag.className = 'wb-tag ' + phase.cls;
    }

    var tempTag = $id('wbTempTag');
    if (tempTag) {
      if (thermo && isNum(thermo.score)) {
        tempTag.textContent = '大盘温度 ' + num(thermo.score, 1) + ' · ' + (thermo.level || '—');
        tempTag.className = 'wb-tag ' + (thermo.score >= 60 ? 'is-up' : thermo.score < 40 ? 'is-down' : 'is-warn');
      } else {
        tempTag.textContent = '大盘温度 —';
        tempTag.className = 'wb-tag';
      }
    }

    var live = $id('wbLiveDot');
    if (live && live.parentElement) {
      var stale = !board || board.data_quality !== 'normal' || (thermo && thermo.components && thermo.components.sentiment && String(thermo.components.sentiment.note || '').indexOf('不可用') >= 0);
      live.parentElement.classList.toggle('is-stale', !!stale);
      live.parentElement.title = stale ? '部分上游字段缺失，见各面板「—」说明' : '指数/复盘数据已刷新';
    }
  }

  /* ================= 渲染：交易时段 ================= */
  function renderSession(st) {
    state.session = st || null;
    var tag = $id('wbSessionTag');
    var hint = $id('wbRefreshHint');
    var wrap = $id('wbLiveWrap');
    var open = !!(st && st.is_open);
    if (tag) {
      var key = st ? String(st.session || '') : '';
      tag.textContent = SESSION_TEXT[key] || '交易时段未知';
      tag.className = 'wb-tag' + (open ? ' is-accent' : (key === 'lunch_break' ? ' is-warn' : ''));
      tag.title = st
        ? (open ? '连续竞价中，自动刷新 60s' : ('下一开市 ' + (st.next_open_time || '—')))
        : '交易状态接口暂不可用，仍按 60s 刷新';
    }
    if (hint) {
      hint.textContent = !st ? '刷新 60s（时段未知）'
        : open ? '盘中刷新 60s'
        : (state.auto ? '休市暂停刷新' : '自动刷新已关');
    }
    if (wrap) wrap.classList.toggle('is-closed', !open);
  }

  async function fetchStatus() {
    var r = await fetchJSON('/api/market/status');
    return (r.ok && r.body && r.body.data) ? r.body.data : null;
  }

  /* ================= 数据源健康 ================= */
  function renderHealth(items) {
    state.health = items.slice();
    var el = $id('wbSourceStrip');
    if (!el) return;
    var bad = [];
    el.innerHTML = '数据源 ' + items.map(function (x) {
      var cls = 'wb-src';
      var text = esc(x.k) + ' ✓';
      if (x.auth) {
        cls += ' is-dim';
        text = esc(x.k) + ' 需Token';
      } else if (!x.ok) {
        cls += ' is-bad';
        text = esc(x.k) + ' ✕';
        bad.push(x.k);
      }
      return '<span class="' + cls + '">' + text + '</span>';
    }).join('');
    el.title = bad.length ? ('不可用：' + bad.join('、')) : '全部数据源正常';
  }

  /** 异步补充的源（资金连增）返回后再并入健康条，不拖延首屏。 */
  function updateSourceHealth(k, ok, auth) {
    var items = (state.health || []).filter(function (x) { return x.k !== k; });
    items.push({ k: k, ok: !!ok, auth: !!auth });
    renderHealth(items);
  }

  function clockText(s) {
    var m = /(\d{2}):(\d{2})/.exec(String(s || ''));
    return m ? (m[1] + ':' + m[2]) : '—';
  }

  /* ================= 渲染：指数卡 ================= */
  function renderIndices(list) {
    var el = $id('wbIndexCards');
    if (!el) return;
    if (!Array.isArray(list) || !list.length) {
      el.innerHTML = '<div class="wb-empty" style="grid-column:1/-1;"><b>指数数据暂不可用</b><br>上游未返回行情，请稍后点击「刷新」。</div>';
      return;
    }
    var wanted = ['000001', '399001', '399006', '000688'];
    var picked = [];
    wanted.forEach(function (code) {
      for (var i = 0; i < list.length; i++) {
        if (String(list[i].code) === code) { picked.push(list[i]); break; }
      }
    });
    if (picked.length < 4) {
      list.forEach(function (it) {
        if (picked.length < 4 && picked.indexOf(it) < 0) picked.push(it);
      });
    }
    el.innerHTML = picked.map(function (it) {
      var pct = Number(it.change_pct);
      var c = clsOf(pct);
      return '<div class="wb-idx ' + c + '">'
        + '<div class="wb-idx-top"><span class="wb-idx-name">' + esc(it.name || '—') + '</span></div>'
        + '<div class="wb-idx-pct ' + c + '">' + esc(pctText(pct)) + '</div>'
        + '<div class="wb-idx-sub"><span>' + esc(num(it.price, 2)) + '</span><b>成交 ' + esc(fmtAmountWan(it.amount)) + '</b></div>'
        + '</div>';
    }).join('');
  }

  /* ================= 渲染：统计条 ================= */
  function renderStats(board, thermo) {
    var el = $id('wbStatStrip');
    if (!el) return;
    var m = (board && board.market) || {};
    var lim = (board && board.limit) || {};
    var temp = (board && board.temperature) || {};
    var sr = (thermo && thermo.components && thermo.components.sector_rotation) || {};
    var countsOk = m.counts_available !== false && isNum(m.rise_count);
    var sealRate = isNum(lim.broken_rate_pct) ? 100 - lim.broken_rate_pct : null;

    var cells = [
      {
        k: '个股涨 / 平 / 跌',
        v: countsOk ? (num(m.rise_count, 0) + ' / ' + num(m.flat_count, 0) + ' / ' + num(m.fall_count, 0)) : '— / — / —',
        s: countsOk ? ('上涨占比 ' + num((m.rise_ratio || 0) * 100, 1) + '%') : '上游未返回计数（指数代理）',
        cls: countsOk ? 'is-up' : 'is-dim'
      },
      {
        k: '涨停 / 跌停',
        v: (isNum(lim.limit_up_count) ? lim.limit_up_count : '—') + ' / ' + (isNum(lim.limit_down_count) ? lim.limit_down_count : '—'),
        s: '封板率 ' + (isNum(sealRate) ? num(sealRate, 1) + '%' : '—') + ' · 炸板 ' + (isNum(lim.broken_count) ? lim.broken_count : '—'),
        cls: 'is-up'
      },
      {
        k: '最高连板',
        v: isNum(lim.max_streak) ? lim.max_streak + ' 板' : '—',
        s: ladderSummary(lim.ladder),
        cls: ''
      },
      {
        k: '两市成交额',
        v: fmtAmountWan(m.total_amount),
        s: '指数成交额合计',
        cls: ''
      },
      {
        k: '板块涨 / 跌',
        v: (isNum(sr.gain_count) ? sr.gain_count : '—') + ' / ' + (isNum(sr.fall_count) ? sr.fall_count : '—'),
        s: '东方财富行业板块',
        cls: isNum(sr.gain_count) && isNum(sr.fall_count) ? clsOf(sr.gain_count - sr.fall_count) : ''
      },
      {
        k: '打板温度',
        v: isNum(temp.score) ? num(temp.score, 1) : '—',
        s: (temp.level || '—') + ' · 涨停/连板/封板/广度/指数',
        cls: isNum(temp.score) ? (temp.score >= 60 ? 'is-up' : temp.score < 40 ? 'is-down' : 'is-warn') : ''
      }
    ];

    el.innerHTML = cells.map(function (c) {
      return '<div class="wb-stat">'
        + '<div class="wb-stat-k">' + esc(c.k) + '</div>'
        + '<div class="wb-stat-v ' + c.cls + '">' + esc(String(c.v)) + '</div>'
        + '<div class="wb-stat-s">' + esc(c.s) + '</div>'
        + '</div>';
    }).join('');
  }

  function ladderSummary(ladder) {
    if (!ladder || typeof ladder !== 'object') return '梯队 —';
    var keys = Object.keys(ladder);
    if (!keys.length) return '梯队 —';
    return keys.slice(0, 4).map(function (k) { return k + ' ' + ladder[k]; }).join(' · ');
  }

  /* ================= 渲染：承接分析 ================= */
  function renderHold(board, thermo) {
    var el = $id('wbHoldBody');
    if (!el) return;
    if (!board) {
      el.innerHTML = '<div class="wb-empty"><b>复盘数据暂不可用</b><br>涨停/炸板池未返回，承接状态无法计算。</div>';
      return;
    }
    var m = board.market || {};
    var lim = board.limit || {};
    var temp = board.temperature || {};
    var sr = (thermo && thermo.components && thermo.components.sector_rotation) || {};
    var avg = isNum(m.index_avg_change_pct) ? m.index_avg_change_pct : null;
    var broken = isNum(lim.broken_rate_pct) ? lim.broken_rate_pct : null;
    var seal = isNum(broken) ? 100 - broken : null;
    var gain = isNum(sr.gain_count) ? sr.gain_count : null;
    var fall = isNum(sr.fall_count) ? sr.fall_count : null;

    var verdict = '承接待确认';
    var vCls = 'is-warn';
    var note = '指数与板块方向不一致，等待封板率与连板高度确认。';
    if (avg != null && avg <= -0.3 && ((broken != null && broken >= 30) || (gain != null && fall != null && fall > gain * 2))) {
      verdict = '承接减弱';
      vCls = 'is-down';
      note = '指数走弱且封板质量下滑，资金承接不足，反弹需先看指数止跌。';
    } else if (avg != null && avg >= 0.3 && (broken == null || broken <= 30)) {
      verdict = '承接增强';
      vCls = 'is-up';
      note = '指数与封板质量同步改善，短线资金承接意愿增强。';
    } else if (avg == null) {
      verdict = '数据不足';
      vCls = '';
      note = '指数均值与涨停池未同时返回，暂不给出承接结论。';
    }

    var reasons = [];
    if (avg != null) {
      reasons.push({ cls: clsOf(avg), html: '指数均值 <b>' + esc(pctText(avg)) + '</b>，' + esc(avg < 0 ? '主要指数整体走弱' : '主要指数整体走强') + '。' });
    }
    if (seal != null) {
      reasons.push({ cls: seal < 70 ? 'is-warn' : 'is-up', html: '封板率 <b>' + esc(num(seal, 1)) + '%</b>，炸板 <b>' + esc(String(lim.broken_count == null ? '—' : lim.broken_count)) + '</b> 家。' });
    }
    if (isNum(lim.limit_up_count)) {
      reasons.push({ cls: '', html: '涨停 <b>' + esc(String(lim.limit_up_count)) + '</b> / 跌停 <b>' + esc(String(lim.limit_down_count == null ? '—' : lim.limit_down_count)) + '</b>，最高连板 <b>' + esc(String(lim.max_streak == null ? '—' : lim.max_streak)) + '</b> 板。' });
    }
    if (gain != null && fall != null) {
      reasons.push({ cls: clsOf(gain - fall), html: '板块 <b>' + esc(String(gain)) + ' 涨 / ' + esc(String(fall)) + ' 跌</b>，' + esc(gain < fall ? '板块整体承压' : '板块整体偏暖') + '。' });
    }
    if (m.counts_available === false) {
      reasons.push({ cls: 'is-warn', html: '个股涨跌家数<b>上游未返回</b>，当前以指数与涨停池代理判断。' });
    }

    el.innerHTML = '<div class="wb-hold-top">'
      + '<div><div class="wb-hold-label">当前承接状态（规则引擎）</div>'
      + '<div class="wb-hold-state ' + vCls + '">' + esc(verdict) + '</div>'
      + '<div class="wb-hold-note">' + esc(note) + '</div></div>'
      + '<div class="wb-tag ' + (vCls === 'is-up' ? 'is-up' : (vCls === 'is-down' ? 'is-down' : 'is-warn')) + '">打板温度 ' + esc(isNum(temp.score) ? num(temp.score, 1) : '—') + '</div>'
      + '</div>'
      + '<div class="wb-mini-row">'
      + mini('指数均值', pctText(avg), clsOf(avg), avg == null ? '数据缺失' : '8 大指数等权')
      + mini('封板率', seal == null ? '—' : num(seal, 1) + '%', seal == null ? '' : (seal < 70 ? 'is-down' : 'is-up'), seal == null ? '涨停池缺失' : ('炸板率 ' + num(broken, 1) + '%'))
      + mini('打板温度', isNum(temp.score) ? num(temp.score, 1) : '—', isNum(temp.score) ? (temp.score >= 60 ? 'is-up' : temp.score < 40 ? 'is-down' : '') : '', temp.level || '数据缺失')
      + '</div>'
      + '<div class="wb-reasons">' + reasons.map(function (r) {
        return '<div class="wb-reason ' + r.cls + '">' + r.html + '</div>';
      }).join('') + '</div>';
  }

  function mini(k, v, cls, s) {
    return '<div class="wb-mini"><div class="wb-mini-k">' + esc(k) + '</div>'
      + '<div class="wb-mini-v ' + (cls || '') + '">' + esc(String(v)) + '</div>'
      + '<div class="wb-mini-s">' + esc(String(s)) + '</div></div>';
  }

  /* ================= 渲染：情绪雷达 ================= */
  function radarDims(board, thermo) {
    var comp = (thermo && thermo.components) || {};
    var lim = (board && board.limit) || {};
    var dims = [
      { label: '指数', value: comp.index_trend ? comp.index_trend.score : null, missing: !comp.index_trend },
      { label: '板块', value: comp.sector_rotation ? comp.sector_rotation.score : null, missing: !comp.sector_rotation },
      { label: '资金', value: comp.capital_flow ? comp.capital_flow.score : null, missing: capitalMissing(comp.capital_flow) },
      { label: '情绪', value: comp.sentiment ? comp.sentiment.score : null, missing: !comp.sentiment || String(comp.sentiment.note || '').indexOf('不可用') >= 0 },
      { label: '封板', value: isNum(lim.broken_rate_pct) ? Math.max(0, Math.min(100, 100 - lim.broken_rate_pct)) : null, missing: !isNum(lim.broken_rate_pct) },
      { label: '连板', value: isNum(lim.max_streak) ? Math.max(0, Math.min(100, lim.max_streak * 20)) : null, missing: !isNum(lim.max_streak) }
    ];
    return dims;
  }

  function capitalMissing(c) {
    if (!c) return true;
    return Number(c.northbound_net || 0) === 0 && Number(c.sector_main_net || 0) === 0;
  }

  function renderRadar(board, thermo) {
    var el = $id('wbRadarBody');
    if (!el) return;
    var dims = radarDims(board, thermo);
    var cx = 165, cy = 132, r = 94;
    var rings = [0.25, 0.5, 0.75, 1];
    var parts = [];
    parts.push('<svg class="wb-radar-svg" viewBox="0 0 330 264" role="img" aria-label="情绪雷达：指数、板块、资金、情绪、封板、连板六维评分">');
    // 网格环
    rings.forEach(function (k) {
      var pts = [];
      for (var i = 0; i < 6; i++) {
        var a = -Math.PI / 2 + i * Math.PI / 3;
        pts.push((cx + Math.cos(a) * r * k).toFixed(1) + ',' + (cy + Math.sin(a) * r * k).toFixed(1));
      }
      parts.push('<polygon points="' + pts.join(' ') + '" fill="none" stroke="#1B2637" stroke-width="1"/>');
    });
    // 轴线 + 标签
    for (var i = 0; i < 6; i++) {
      var ang = -Math.PI / 2 + i * Math.PI / 3;
      var x2 = cx + Math.cos(ang) * r;
      var y2 = cy + Math.sin(ang) * r;
      parts.push('<line x1="' + cx + '" y1="' + cy + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1) + '" stroke="#1B2637" stroke-width="1"/>');
      var lx = cx + Math.cos(ang) * (r + 22);
      var ly = cy + Math.sin(ang) * (r + 22);
      var anchor = Math.abs(lx - cx) < 8 ? 'middle' : (lx > cx ? 'start' : 'end');
      parts.push('<text class="wb-radar-label' + (dims[i].missing ? ' is-missing' : '') + '" x="' + lx.toFixed(1) + '" y="' + (ly + 3).toFixed(1) + '" text-anchor="' + anchor + '">'
        + esc(dims[i].label) + (dims[i].missing ? '·缺' : '') + '</text>');
    }
    // 数据多边形
    var pts2 = [];
    dims.forEach(function (d, idx) {
      var a = -Math.PI / 2 + idx * Math.PI / 3;
      var v = isNum(d.value) ? Math.max(0, Math.min(100, d.value)) : 50;
      var rr = r * v / 100;
      pts2.push((cx + Math.cos(a) * rr).toFixed(1) + ',' + (cy + Math.sin(a) * rr).toFixed(1));
    });
    parts.push('<polygon points="' + pts2.join(' ') + '" fill="rgba(124,134,255,.16)" stroke="#7C86FF" stroke-width="1.6" stroke-linejoin="round"/>');
    dims.forEach(function (d, idx) {
      var a = -Math.PI / 2 + idx * Math.PI / 3;
      var v = isNum(d.value) ? Math.max(0, Math.min(100, d.value)) : 50;
      var rr = r * v / 100;
      parts.push('<circle cx="' + (cx + Math.cos(a) * rr).toFixed(1) + '" cy="' + (cy + Math.sin(a) * rr).toFixed(1) + '" r="2.6" fill="#A9B0FF"/>');
    });
    // 中心综合分
    var score = (thermo && isNum(thermo.score)) ? thermo.score : null;
    parts.push('<text class="wb-radar-center" x="' + cx + '" y="' + (cy - 6) + '">' + esc(score == null ? '—' : num(score, 1)) + '</text>');
    parts.push('<text class="wb-radar-center-sub" x="' + cx + '" y="' + (cy + 14) + '">综合</text>');
    parts.push('</svg>');

    var legend = dims.map(function (d) {
      return '<div class="wb-legend-item' + (d.missing ? ' is-missing' : '') + '"><span>' + esc(d.label) + '</span><b>'
        + esc(d.missing ? '缺失' : num(d.value, 0)) + '</b></div>';
    }).join('');

    el.innerHTML = '<div class="wb-radar-wrap">' + parts.join('') + '</div>'
      + '<div class="wb-legend">' + legend + '</div>'
      + '<div class="wb-phase-explain" style="margin-top:7px;">封板 = 100 − 炸板率；连板 = 最高板数 × 20（上限 100）。「缺失」维度不计入结论，仅作占位。</div>';
  }

  /* ================= 渲染：分歧 → 一致 相位 ================= */
  function classifyPhase(board) {
    var lim = (board && board.limit) || {};
    var temp = (board && board.temperature) || {};
    var y = (board && board.yesterday) || {};
    var broken = isNum(lim.broken_rate_pct) ? lim.broken_rate_pct : null;
    var yBroken = isNum(y.broken_rate_pct) ? y.broken_rate_pct : null;
    var score = isNum(temp.score) ? temp.score : null;
    if (broken == null || score == null) return { key: 'unknown', label: '相位数据不足', cls: '' };
    if (broken >= 40 && score < 45) return { key: 'ebb', label: '退潮', cls: 'is-down' };
    if (broken <= 25 && score >= 55) return { key: 'consensus', label: '一致', cls: 'is-up' };
    if (yBroken != null && yBroken >= 40 && broken <= yBroken - 8) return { key: 'repair', label: '分歧转一致', cls: 'is-accent' };
    if (broken >= 30) return { key: 'divergence', label: '分歧', cls: 'is-warn' };
    return { key: 'pending', label: '分歧待确认', cls: 'is-warn' };
  }

  var PHASE_TEXT = {
    consensus: '封板率回升、连板梯队不断层，资金从分歧转向一致。',
    divergence: '炸板率偏高、晋级率不足，资金意见不统一，追高容错低。',
    ebb: '高位分歧且温度走低，连板高度下移，注意补跌与退潮风险。',
    repair: '昨日高炸板后今日封板率明显修复，分歧转向一致的第一步。',
    pending: '分歧与一致之间，等待封板率与连板高度给出方向。',
    unknown: '涨停池与温度数据未同时返回，暂不判读相位。'
  };

  function renderPhase(board) {
    var el = $id('wbPhaseBody');
    if (!el) return;
    if (!board) {
      el.innerHTML = '<div class="wb-empty"><b>相位数据暂不可用</b><br>需要涨停池与炸板池同时返回。</div>';
      return;
    }
    var lim = board.limit || {};
    var y = board.yesterday || {};
    var temp = board.temperature || {};
    var phase = classifyPhase(board);
    var broken = isNum(lim.broken_rate_pct) ? lim.broken_rate_pct : null;
    var yBroken = isNum(y.broken_rate_pct) ? y.broken_rate_pct : null;
    var delta = (broken != null && yBroken != null) ? broken - yBroken : null;
    var limitDelta = isNum(y.limit_up_delta) ? y.limit_up_delta : null;

    el.innerHTML = '<div class="wb-phase-head">'
      + '<span class="wb-phase-badge ' + phase.cls + '">' + esc(phase.label) + '</span>'
      + '<span class="wb-phase-explain">' + esc(PHASE_TEXT[phase.key] || '') + '</span>'
      + '</div>'
      + '<div class="wb-phase-row">'
      + '<div class="wb-phase-cell"><div class="wb-phase-cell-k">昨日 封板率 / 涨停</div><div class="wb-phase-cell-v">'
      + esc(yBroken == null ? '—' : num(100 - yBroken, 1) + '%') + ' <span style="font-size:11px;color:var(--wb-faint);">/ ' + esc(isNum(y.limit_up_count) ? String(y.limit_up_count) : '—') + '</span></div></div>'
      + '<div class="wb-phase-arrow">→</div>'
      + '<div class="wb-phase-cell"><div class="wb-phase-cell-k">今日 封板率 / 涨停</div><div class="wb-phase-cell-v">'
      + esc(broken == null ? '—' : num(100 - broken, 1) + '%') + ' <span style="font-size:11px;color:var(--wb-faint);">/ ' + esc(isNum(lim.limit_up_count) ? String(lim.limit_up_count) : '—') + '</span></div></div>'
      + '</div>'
      + '<div class="wb-phase-metrics">'
      + mini('炸板率变化', delta == null ? '—' : signed(delta, 1) + ' pct', delta == null ? '' : (delta === 0 ? '' : (delta < 0 ? 'is-up' : 'is-down')), delta == null ? '昨日数据缺失' : (delta === 0 ? '较昨日持平' : (delta < 0 ? '封板质量改善' : '封板质量走弱')))
      + mini('涨停数变化', limitDelta == null ? '—' : signed(limitDelta, 0), limitDelta == null ? '' : clsOf(limitDelta), '较昨日')
      + mini('最高连板', isNum(lim.max_streak) ? lim.max_streak + ' 板' : '—', '', '连板梯队高度')
      + '</div>'
      + '<div class="wb-phase-note">判读规则（非投资建议）：封板率 ≥ 75% 且温度 ≥ 55 → 一致；炸板率 ≥ 40% 且温度 &lt; 45 → 退潮；昨日炸板率 ≥ 40% 且今日回落 ≥ 8pct → 分歧转一致。昨日数据不可用时不做跨日判读。</div>';
  }

  /* ================= 渲染：开仓纪律 ================= */
  function disciplineChecks(board, thermo) {
    var lim = (board && board.limit) || {};
    var m = (board && board.market) || {};
    var temp = (board && board.temperature) || {};
    var y = (board && board.yesterday) || {};
    /* 优先用温度计的「大盘温度」，缺失时退回复盘的打板温度 */
    var score = (thermo && isNum(thermo.score)) ? thermo.score : (isNum(temp.score) ? temp.score : null);
    var broken = isNum(lim.broken_rate_pct) ? lim.broken_rate_pct : null;
    var maxStreak = isNum(lim.max_streak) ? lim.max_streak : null;
    var avg = isNum(m.index_avg_change_pct) ? m.index_avg_change_pct : null;
    var yBroken = isNum(y.broken_rate_pct) ? y.broken_rate_pct : null;
    return [
      { name: '温度 ≥ 50', val: score == null ? null : num(score, 1), pass: score == null ? null : score >= 50, hint: '优先取大盘温度；缺失时退回打板温度' },
      { name: '炸板率 ≤ 30%', val: broken == null ? null : num(broken, 1) + '%', pass: broken == null ? null : broken <= 30, hint: '封板质量' },
      { name: '最高连板 ≥ 3', val: maxStreak == null ? null : maxStreak + ' 板', pass: maxStreak == null ? null : maxStreak >= 3, hint: '赚钱效应高度' },
      { name: '指数均值 ≥ 0%', val: avg == null ? null : pctText(avg), pass: avg == null ? null : avg >= 0, hint: '主要指数方向' },
      {
        name: '封板质量较昨日改善',
        val: (broken == null || yBroken == null) ? null : signed(broken - yBroken, 1) + ' pct',
        pass: (broken == null || yBroken == null) ? null : broken <= yBroken,
        hint: '炸板率环比回落'
      }
    ];
  }

  function disciplineVerdict(checks) {
    var available = checks.filter(function (c) { return c.pass !== null; });
    var passed = available.filter(function (c) { return c.pass; }).length;
    var verdict, cls, tone;
    if (available.length < 3) {
      verdict = '数据不足';
      cls = 'is-dim';
      tone = 'muted';
    } else if (passed / available.length >= 0.75) {
      verdict = '可正常参与';
      cls = 'is-accent';
      tone = 'accent';
    } else if (passed / available.length >= 0.4) {
      verdict = '降级参与';
      cls = 'is-warn';
      tone = 'warn';
    } else {
      verdict = '空仓等待';
      cls = 'is-dim';
      tone = 'muted';
    }
    return { verdict: verdict, cls: cls, tone: tone, passed: passed, available: available.length };
  }

  function renderDiscipline(board, thermo) {
    var el = $id('wbDisciplineBody');
    if (!el) return;
    if (!board) {
      el.innerHTML = '<div class="wb-empty"><b>纪律数据暂不可用</b><br>复盘与温度数据未返回，无法做开仓检查。</div>';
      return;
    }
    var checks = disciplineChecks(board, thermo);
    var verdict = disciplineVerdict(checks);
    var passed = verdict.passed;
    var available = verdict.available;

    el.innerHTML = '<div class="wb-disc-top">'
      + '<div><div class="wb-disc-label">今日开仓纪律（规则引擎）</div>'
      + '<div class="wb-disc-verdict ' + verdict.cls + '">' + esc(verdict.verdict) + '</div></div>'
      + '<div class="wb-disc-score">命中 ' + passed + ' / ' + available
      + '<br>缺失 ' + (checks.length - available) + ' 项</div>'
      + '</div>'
      + '<div class="wb-checks" role="list">' + checks.map(function (c) {
        var stateCls = c.pass === null ? 'is-miss' : (c.pass ? 'is-pass' : 'is-fail');
        var ico = c.pass === null ? '—' : (c.pass ? '✓' : '!');
        var stateText = c.pass === null ? '缺失' : (c.pass ? '通过' : '未通过');
        var aria = c.name + '：' + stateText + '，当前 ' + (c.val == null ? '无数据' : c.val);
        return '<div class="wb-check ' + stateCls + '" role="listitem" aria-label="' + esc(aria) + '" title="' + esc(c.hint) + '">'
          + '<span class="wb-check-ico" aria-hidden="true">' + ico + '</span>'
          + '<span class="wb-check-name">' + esc(c.name) + '</span>'
          + '<span class="wb-check-val">' + esc(c.val == null ? '缺失' : c.val) + '</span>'
          + '</div>';
      }).join('') + '</div>'
      + '<div class="wb-disc-note">命中 ≥ 75% → 正常参与；40%~74% → 降级参与（降低仓位、只做低吸）；&lt; 40% → 空仓等待。缺失项只标注、不计入判定。规则引擎，非投资建议。</div>';
  }

  /* ================= 渲染：涨停梯队 ================= */
  function renderLadder(board) {
    var el = $id('wbLadderBody');
    if (!el) return;
    if (!board || !board.limit) {
      el.innerHTML = '<div class="wb-empty"><b>涨停池暂不可用</b><br>上游未返回涨停/炸板数据。</div>';
      return;
    }
    var lim = board.limit;
    var ladder = lim.ladder || {};
    var keys = Object.keys(ladder);
    var meta = $id('wbLadderMeta');
    if (meta) meta.textContent = '封板率 ' + (isNum(lim.broken_rate_pct) ? num(100 - lim.broken_rate_pct, 1) + '%' : '—') + ' · 炸板 ' + (isNum(lim.broken_count) ? lim.broken_count : '—');
    if (!keys.length) {
      el.innerHTML = '<div class="wb-empty"><b>今日无涨停梯队</b><br>非交易日或涨停池为空。</div>';
      return;
    }
    var max = 1;
    keys.forEach(function (k) { max = Math.max(max, Number(ladder[k]) || 0); });
    var rows = keys.sort(function (a, b) { return (Number(ladder[b]) || 0) - (Number(ladder[a]) || 0); }).map(function (k) {
      var v = Number(ladder[k]) || 0;
      return '<div class="wb-row is-compact">'
        + '<span class="wb-row-name"><b>' + esc(k) + '</b></span>'
        + '<span class="wb-bar is-up"><i style="width:' + Math.max(4, Math.round(v / max * 100)) + '%"></i></span>'
        + '<span class="wb-row-val is-up">' + esc(String(v)) + '</span>'
        + '</div>';
    }).join('');
    var chips = (lim.top_sectors || []).slice(0, 6).map(function (s) {
      return '<span class="wb-chip">' + esc(s.name) + ' <b>' + esc(String(s.limit_up_count)) + '</b></span>';
    }).join('');
    el.innerHTML = '<div class="wb-rows">' + rows + '</div>'
      + (chips ? '<div class="wb-chips"><span class="wb-chip" style="border-color:transparent;background:transparent;padding-left:0;color:var(--wb-faint);">涨停集中</span>' + chips + '</div>' : '');
  }

  /* ================= 渲染：行业热度 ================= */
  function renderSectors(items, meta) {
    var el = $id('wbSectorBody');
    if (!el) return;
    var metaEl = $id('wbSectorMeta');
    var unavailable = (meta && meta.unavailable_fields) || [];
    if (metaEl) {
      metaEl.textContent = unavailable.length ? '资金流字段缺失（仅涨跌幅可用）' : '主力资金字段可用';
    }
    if (!Array.isArray(items) || !items.length) {
      el.innerHTML = '<div class="wb-empty"><b>板块数据暂不可用</b><br>上游未返回行业板块行情，请稍后刷新。</div>';
      return;
    }
    var sorted = items.slice().sort(function (a, b) { return Number(b.change_pct || 0) - Number(a.change_pct || 0); });
    var up = sorted.slice(0, 6);
    var down = sorted.slice(-6).reverse();
    var maxAbs = 1;
    sorted.forEach(function (s) { maxAbs = Math.max(maxAbs, Math.abs(Number(s.change_pct) || 0)); });
    el.innerHTML = '<div class="wb-heat-cols">'
      + '<div><div class="wb-heat-title is-up"><span>领涨</span><span>涨跌幅</span></div>' + sectorRows(up, maxAbs) + '</div>'
      + '<div><div class="wb-heat-title is-down"><span>领跌</span><span>涨跌幅</span></div>' + sectorRows(down, maxAbs) + '</div>'
      + '</div>';
  }

  function sectorRows(list, maxAbs) {
    return list.map(function (s) {
      var v = Number(s.change_pct);
      var c = clsOf(v);
      var w = Math.max(3, Math.round(Math.abs(v) / maxAbs * 100));
      return '<div class="wb-row">'
        + '<span class="wb-row-name">' + esc(s.sector || '—') + '</span>'
        + '<span class="wb-bar ' + c + '"><i style="width:' + w + '%"></i></span>'
        + '<span class="wb-row-val ' + c + '">' + esc(pctText(v)) + '</span>'
        + '</div>';
    }).join('');
  }

  /* ================= 渲染：热门股 ================= */
  function renderHot(hot) {
    var el = $id('wbHotBody');
    if (!el) return;
    if (!hot || (!Array.isArray(hot.top_gainers) && !Array.isArray(hot.top_volume))) {
      el.innerHTML = '<div class="wb-empty"><b>热门股暂不可用</b><br>上游未返回排行数据。</div>';
      return;
    }
    var gain = (hot.top_gainers || []).slice(0, 5);
    var vol = (hot.top_volume || []).slice(0, 5);
    el.innerHTML = '<div class="wb-hot-cols">'
      + '<div><div class="wb-heat-title is-up"><span>涨幅 TOP5</span><span>涨幅</span></div>'
      + (gain.length ? gain.map(hotGainRow).join('') : '<div class="wb-empty">暂无数据</div>') + '</div>'
      + '<div><div class="wb-heat-title"><span>成交额 TOP5</span><span>成交额</span></div>'
      + (vol.length ? vol.map(hotVolRow).join('') : '<div class="wb-empty">暂无数据</div>') + '</div>'
      + '</div>';
  }

  function hotGainRow(s) {
    var v = Number(s.change_pct);
    var c = clsOf(v);
    var code = s.code || '';
    return '<button type="button" class="wb-hot-row" data-code="' + esc(code) + '" title="查看 ' + esc(s.name || code) + ' 详情">'
      + '<span class="wb-hot-name">' + esc(s.name || '—') + '<span>' + esc(code) + '</span></span>'
      + '<span class="wb-hot-num ' + c + '">' + esc(pctText(v)) + '</span>'
      + '<span class="wb-hot-num">' + esc(num(s.price, 2)) + '</span>'
      + '</button>';
  }

  function hotVolRow(s) {
    var v = Number(s.change_pct);
    var c = clsOf(v);
    var code = s.code || '';
    return '<button type="button" class="wb-hot-row" data-code="' + esc(code) + '" title="查看 ' + esc(s.name || code) + ' 详情">'
      + '<span class="wb-hot-name">' + esc(s.name || '—') + '<span>' + esc(code) + '</span></span>'
      + '<span class="wb-hot-num ' + c + '">' + esc(pctText(v)) + '</span>'
      + '<span class="wb-hot-num">' + esc(fmtYuanAmount(Number(s.amount))) + '</span>'
      + '</button>';
  }

  /* ================= 渲染：资金连增 ================= */
  function renderDdx(r) {
    var el = $id('wbDdxBody');
    if (!el) return;
    var metaEl = $id('wbDdxMeta');
    var data = (r && r.ok && r.body && r.body.data) ? r.body.data : null;
    if (!data) {
      if (metaEl) metaEl.textContent = '不可用';
      el.innerHTML = '<div class="wb-empty"><b>资金流数据暂不可用</b><br>个股资金流上游取数失败，本面板不影响其他判读；稍后「刷新」重试。</div>';
      return;
    }
    var sampled = isNum(data.sampled) ? data.sampled : 0;
    var failed = isNum(data.failed) ? data.failed : 0;
    var items = Array.isArray(data.items) ? data.items : [];
    /* 冷却 / 全失败 / 无候选 都不是"筛不出连增"，必须分开说，避免把限流说成筛选结果 */
    if (data.cooldown || sampled === 0 || failed >= sampled) {
      if (metaEl) metaEl.textContent = data.cooldown ? '冷却中' : '取数失败';
      el.innerHTML = '<div class="wb-empty"><b>资金流取数失败</b><br>'
        + (data.cooldown
          ? '上游限流冷却中（约 2 分钟），冷却结束后自动重试；不影响其他判读。'
          : '候选股逐股资金流均未取到（上游限流或断连），不影响其他判读；稍后「刷新」重试。')
        + '</div>';
      return;
    }
    if (metaEl) {
      var srcCounts = data.sources || {};
      var srcText = Object.keys(srcCounts).map(function (k) {
        return (SOURCE_TEXT[k] || k) + ' ' + srcCounts[k];
      }).join(' / ');
      metaEl.textContent = '候选 ' + sampled
        + ' · 连增 ' + items.length
        + (failed > 0 ? (' · 失败 ' + failed) : '')
        + (srcText ? (' · ' + srcText) : '');
      metaEl.title = srcText ? ('资金流来源：' + srcText) : '资金流来源未标注';
    }
    if (!items.length) {
      el.innerHTML = '<div class="wb-empty"><b>候选池内暂无连增标的</b><br>涨幅 / 成交额前 10 中，没有连续 '
        + esc(String(data.min_days || 2)) + ' 个交易日主力净流入为正的个股（这是筛选结果，不是数据缺失）。</div>';
      return;
    }
    el.innerHTML = '<div class="wb-ddx-head"><span>名称</span><span>连增</span><span>今日净流入</span><span>区间合计</span></div>'
      + items.map(function (it) {
        var streak = Number(it.streak) || 0;
        var today = Number(it.net_today);
        var sum = Number(it.net_sum);
        var ratio = Number(it.ratio_today);
        var tip = '查看 ' + (it.name || it.code) + ' 详情'
          + (it.end_date ? (' · 截至 ' + it.end_date) : '')
          + (isNum(ratio) ? (' · 今日净占比 ' + num(ratio, 2) + '%') : '')
          + ' · 来源 ' + (SOURCE_TEXT[it.source] || it.source || '未标注');
        return '<button type="button" class="wb-hot-row wb-ddx-row" data-code="' + esc(it.code) + '" title="' + esc(tip) + '">'
          + '<span class="wb-hot-name">' + esc(it.name || '—') + '<span>' + esc(it.code) + '</span></span>'
          + '<span class="wb-ddx-streak">' + streak + '日</span>'
          + '<span class="wb-hot-num ' + clsOf(today) + '">' + esc(fmtNetYuan(today)) + '</span>'
          + '<span class="wb-hot-num">' + esc(fmtNetYuan(sum)) + '</span>'
          + '</button>';
      }).join('')
      + '<div class="wb-phase-explain" style="margin-top:8px;">连增 = 从最近交易日起主力净流入额连续为正的天数（历史资金口径，非通达信 DDX 大单净量）；仅资金面观察，非买入信号。</div>';
  }

  function loadDdx(suffix) {
    var seq = ++state.ddxSeq;
    fetchJSON('/api/market/ddx_streak' + suffix).then(function (r) {
      if (seq !== state.ddxSeq) return; /* 期间又刷新过，旧响应丢弃 */
      renderDdx(r);
      var d = (r.ok && r.body && r.body.data) ? r.body.data : null;
      var ok = !!d && !d.cooldown && isNum(d.sampled) && d.sampled > 0
        && !(isNum(d.failed) && d.failed >= d.sampled);
      updateSourceHealth('资金', ok);
    });
  }

  /* ================= 渲染：持仓快照 ================= */
  function renderPortfolio(r) {
    var el = $id('wbPortfolioBody');
    if (!el) return;
    var metaEl = $id('wbPortfolioMeta');
    if (!r || !r.ok) {
      var hint = (r && r.status === 401)
        ? '持仓估值需要管理员 Token：请到「设置」填入后可在此查看。'
        : '持仓估值暂不可用，请在「持仓管理」页查看。';
      if (metaEl) metaEl.textContent = '未授权';
      el.innerHTML = '<div class="wb-empty"><b>持仓快照未加载</b><br>' + esc(hint) + '</div>';
      return;
    }
    var d = r.body || {};
    var positions = Array.isArray(d.positions) ? d.positions : [];
    var summary = d.summary || {};
    var total = summary.total_est_day_pnl;
    if (!positions.length) {
      el.innerHTML = '<div class="wb-empty"><b>暂无持仓</b><br>在「持仓管理」录入后，这里显示盘中估算盈亏。</div>';
      if (metaEl) metaEl.textContent = '持仓 0';
      return;
    }
    var sorted = positions.slice().sort(function (a, b) { return Number(b.est_pct || 0) - Number(a.est_pct || 0); }).slice(0, 5);
    if (metaEl) {
      var conf = summary.confidence_counts || {};
      metaEl.textContent = '持仓 ' + positions.length + ' · 高置信 ' + (conf.high || 0) + ' · 滞后 ' + (summary.stale_count || 0);
    }
    el.innerHTML = '<div class="wb-portfolio">'
      + '<div class="wb-pf-total">'
      + '<div class="wb-pf-total-k">组合估算当日盈亏</div>'
      + '<div class="wb-pf-total-v ' + clsOf(total) + '">' + esc(fmtMoney(total)) + '</div>'
      + '<div class="wb-pf-total-s">估算≠净值 · ' + esc(d.degraded ? '已降级：' + String(d.degraded_reason || '') : '主源实时') + '</div>'
      + '</div>'
      + '<div class="wb-pf-list">' + sorted.map(function (p) {
        var pct = Number(p.est_pct);
        var c = clsOf(pct);
        return '<div class="wb-pf-item" title="' + esc(p.fund_name || '') + '">'
          + '<div class="wb-pf-name">' + esc(p.fund_name || '—') + '</div>'
          + '<div class="wb-pf-code">' + esc(p.fund_code || '') + '</div>'
          + '<div class="wb-pf-pct ' + c + '">' + esc(isNum(pct) ? pctText(pct) : '—') + '</div>'
          + '<div class="wb-pf-pnl">' + esc(fmtMoney(Number(p.est_day_pnl))) + '</div>'
          + '</div>';
      }).join('') + '</div>'
      + '</div>';
  }

  /* ================= 加载编排 ================= */
  async function load(force, statusIn) {
    if (state.loading) return;
    state.loading = true;
    var btn = $id('wbRefreshBtn');
    if (btn) { btn.disabled = true; btn.textContent = '刷新中'; }
    var suffix = force ? ('?_t=' + Date.now()) : '';
    try {
      var results = await Promise.all([
        fetchJSON('/api/market/index_realtime' + suffix),
        fetchJSON('/api/market/thermometer' + suffix),
        fetchJSON('/api/review/board' + suffix),
        fetchJSON('/api/market/sector_flow' + suffix),
        fetchJSON('/api/market/hot_stocks' + suffix),
        fetchJSON('/api/portfolio/watchboard' + suffix),
        statusIn ? Promise.resolve(statusIn) : fetchStatus()
      ]);
      var idx = results[0], thermoR = results[1], boardR = results[2], sectorR = results[3], hotR = results[4], pfR = results[5];
      var indices = (idx.ok && idx.body && Array.isArray(idx.body.data)) ? idx.body.data : null;
      var thermo = (thermoR.ok && thermoR.body && thermoR.body.data) ? thermoR.body.data : null;
      var board = (boardR.ok && boardR.body && boardR.body.data) ? boardR.body.data : null;
      var sectors = (sectorR.ok && sectorR.body && Array.isArray(sectorR.body.data)) ? sectorR.body.data : null;
      var sectorMeta = (sectorR.ok && sectorR.body && sectorR.body._meta) ? sectorR.body._meta : null;
      var hot = (hotR.ok && hotR.body && hotR.body.data) ? hotR.body.data : null;

      renderSession(results[6]);
      renderHealth([
        { k: '指数', ok: idx.ok && !!indices },
        { k: '温度', ok: thermoR.ok && !!thermo },
        { k: '复盘', ok: boardR.ok && !!board },
        { k: '板块', ok: sectorR.ok && !!sectors },
        { k: '热门', ok: hotR.ok && !!hot },
        { k: '持仓', ok: !!(pfR && pfR.ok), auth: !!(pfR && pfR.status === 401) }
      ]);
      renderHead(board, thermo);
      renderIndices(indices);
      renderStats(board, thermo);
      renderHold(board, thermo);
      renderRadar(board, thermo);
      renderPhase(board);
      renderDiscipline(board, thermo);
      renderLadder(board);
      renderSectors(sectors, sectorMeta);
      renderHot(hot);
      renderPortfolio(pfR);
      /* 资金连增要逐股拉资金流（1~3s），异步补拉，不拖慢首屏其他面板 */
      loadDdx(suffix);

      state.loadedAt = Date.now();
      var foot = $id('wbFootTime');
      if (foot) foot.textContent = timeText();
      var up = $id('wbUpstreamTime');
      if (up) up.textContent = clockText((thermo && thermo.update_time) || (board && board.update_time) || (sectorMeta && sectorMeta.timestamp));
      var live = $id('wbLiveDot');
      if (live && live.parentElement) live.parentElement.title = '最近刷新 ' + timeText();
    } catch (e) {
      state.lastError = String(e && e.message ? e.message : e);
      var fallback = $id('wbFootTime');
      if (fallback) fallback.textContent = '刷新失败，保留上次数据';
    } finally {
      state.loading = false;
      if (btn) { btn.disabled = false; btn.textContent = '刷新'; }
    }
  }

  function startTimer() {
    stopTimer();
    if (!state.auto) return;
    state.timer = setInterval(tick, REFRESH_MS);
  }

  function tick() {
    var page = $id('page-workbench');
    if (!page || !page.classList.contains('active') || document.visibilityState === 'hidden') return;
    if (!state.auto) return;
    fetchStatus().then(function (st) {
      renderSession(st);
      /* 休市只更新时段徽标，不再打上游；状态不可用时维持原刷新行为 */
      if (!st || st.is_open) load(false, st);
    });
  }

  function stopTimer() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  function toggleAuto() {
    state.auto = !state.auto;
    try { localStorage.setItem(LS_AUTO, state.auto ? '1' : '0'); } catch (e) { /* 隐私模式忽略 */ }
    var btn = $id('wbAutoBtn');
    if (btn) {
      btn.textContent = state.auto ? '自动刷新:开' : '自动刷新:关';
      btn.classList.toggle('is-on', state.auto);
      btn.setAttribute('aria-pressed', state.auto ? 'true' : 'false');
    }
    renderSession(state.session);
    if (state.auto) { startTimer(); } else { stopTimer(); }
  }

  /** 行点击 → 行情总览的个股详情面板（复用存量 loadStockDetail）。
      用事件委托而非内联 onclick：容器每次刷新重建，委托监听不受影响。 */
  function bindRowClicks(containerId) {
    var el = $id(containerId);
    if (!el || el.__wbBound) return;
    el.__wbBound = true;
    el.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('.wb-hot-row') : null;
      if (btn && btn.dataset && btn.dataset.code) openStock(btn.dataset.code);
    });
  }

  function bindHotRows() {
    bindRowClicks('wbHotBody');
    bindRowClicks('wbDdxBody');
  }

  function openStock(code) {
    if (!code || typeof switchPage !== 'function') return;
    switchPage('market');
    if (typeof loadStockDetail === 'function') {
      try { loadStockDetail(String(code)); } catch (e) { /* 详情失败不阻塞切页 */ }
    }
    setTimeout(function () {
      var panel = $id('stockDetailPanel');
      if (panel && typeof panel.scrollIntoView === 'function') {
        panel.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
    }, 80);
  }

  function onSwitch() {
    var page = $id('page-workbench');
    if (!page) return;
    /* 以页面实际 active 态为准：一级导航「行情」传的是 hud-market，
       switchPage 内部才映射到 workbench，不能按入参 name 判断。 */
    if (page.classList.contains('active')) {
      if (!state.loadedAt || Date.now() - state.loadedAt > 30000) load(false);
      startTimer();
    } else {
      stopTimer();
    }
  }

  function wrapSwitchPage() {
    if (typeof window.switchPage !== 'function' || window.__workbenchWrapped) return;
    window.__workbenchWrapped = true;
    var orig = window.switchPage;
    window.switchPage = function (name) {
      var out = orig.apply(this, arguments);
      try { onSwitch(name); } catch (e) { /* 渲染失败不阻塞切页 */ }
      return out;
    };
  }

  function init() {
    try { if (localStorage.getItem(LS_AUTO) === '0') state.auto = false; } catch (e) { /* 隐私模式忽略 */ }
    var autoBtn = $id('wbAutoBtn');
    if (autoBtn) {
      autoBtn.textContent = state.auto ? '自动刷新:开' : '自动刷新:关';
      autoBtn.classList.toggle('is-on', state.auto);
      autoBtn.setAttribute('aria-pressed', state.auto ? 'true' : 'false');
    }
    wrapSwitchPage();
    bindHotRows();
    var page = $id('page-workbench');
    if (page && page.classList.contains('active')) {
      load(false);
      startTimer();
    }
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState !== 'visible' || !page || !page.classList.contains('active')) return;
      if (!state.auto) return;
      if (!state.loadedAt || Date.now() - state.loadedAt > 30000) tick();
    });
  }

  window.Workbench = {
    load: load,
    toggleAuto: toggleAuto,
    stopTimer: stopTimer,
    openStock: openStock,
    /* 其他页面（短线复盘 / 出手三问）复用同一套开仓纪律规则 */
    disciplineChecks: disciplineChecks,
    disciplineVerdict: disciplineVerdict
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
