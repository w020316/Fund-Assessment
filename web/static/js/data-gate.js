/**
 * 数据质量门禁（三道防线）
 *
 * 参考「数据错了还显示成功？」的思路，把数据流拆成三段做可见检查：
 *   采集：指数 / 全市场涨跌家数 / 涨跌停 / 板块覆盖
 *   数仓：主力净流入字段完整度 / 复盘数据质量
 *   投研：资金面可用性 / 数据源健康
 *
 * 原则：缺失即失败或降级，不补 0；门禁结论只说明“这屏数据能不能当结论看”，
 * 不预测行情、不构成投资建议。
 */
(function () {
  'use strict';

  var CACHE_MS = 30000;

  var state = {
    loading: false,
    loadedAt: 0,
    checks: null,
    error: ''
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : String(s == null ? '' : s); }
  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(Number(v)); }

  function settled(res) {
    if (res && res.status === 'fulfilled') return { ok: true, body: res.value || null, error: '' };
    var msg = (res && res.reason && res.reason.message) ? res.reason.message : '请求失败';
    return { ok: false, body: null, error: msg };
  }

  function check(key, group, name, status, value, desc) {
    return { key: key, group: group, name: name, status: status, value: value, desc: desc || '' };
  }

  function buildChecks(idx, board, sector, capital, dh) {
    var checks = [];

    /* ---- 采集 ---- */
    var indices = (idx.ok && idx.body && Array.isArray(idx.body.data)) ? idx.body.data : null;
    var idxN = indices ? indices.length : 0;
    checks.push(check('indices', '采集', '指数行情',
      idxN >= 4 ? 'pass' : (idxN > 0 ? 'warn' : 'fail'),
      idxN + ' 个指数',
      idxN >= 4 ? '上证 / 深证 / 创业板 / 科创等核心指数可用' : (idxN > 0 ? '指数返回不完整，部分指数卡会显示「—」' : (idx.error || '上游未返回指数行情'))));

    var bd = (board.ok && board.body && board.body.data) ? board.body.data : null;
    var m = (bd && bd.market) || {};
    var lim = (bd && bd.limit) || {};
    var countsOk = !!bd && m.counts_available !== false && isNum(m.rise_count) && isNum(m.fall_count);
    checks.push(check('breadth', '采集', '全市场涨跌家数',
      countsOk ? 'pass' : 'fail',
      countsOk ? ('涨 ' + m.rise_count + ' / 平 ' + (m.flat_count == null ? '—' : m.flat_count) + ' / 跌 ' + m.fall_count) : '未获取',
      countsOk ? '情绪与广度按真实家数计算' : '情绪温度降级为指数代理，不能当作真实广度'));

    var limitOk = isNum(lim.limit_up_count) && isNum(lim.limit_down_count);
    var limitPartial = isNum(lim.limit_up_count) || isNum(lim.limit_down_count);
    checks.push(check('limit', '采集', '涨停 / 跌停家数',
      limitOk ? 'pass' : (limitPartial ? 'warn' : 'fail'),
      limitOk ? ('涨停 ' + lim.limit_up_count + ' / 跌停 ' + lim.limit_down_count) : (limitPartial ? '仅一侧可用' : '未获取'),
      limitOk ? '封板率与梯队判断可用' : '涨跌停结构缺失，次日规则只作参考'));

    var sectors = (sector.ok && sector.body && Array.isArray(sector.body.data)) ? sector.body.data : null;
    var secN = sectors ? sectors.length : 0;
    checks.push(check('sectors', '采集', '板块行情覆盖',
      secN >= 20 ? 'pass' : (secN > 0 ? 'warn' : 'fail'),
      secN + ' 个板块',
      secN >= 20 ? '行业板块覆盖足够做涨跌与风格归组' : (secN > 0 ? '板块数量偏少，排名与风格页可能不完整' : (sector.error || '上游未返回板块行情'))));

    /* ---- 数仓 ---- */
    var meta = (sector.ok && sector.body && sector.body._meta) ? sector.body._meta : null;
    var fc = (meta && meta.field_completeness && meta.field_completeness.main_net_inflow);
    var ratio = isNum(fc) ? Number(fc) : null;
    var unavailable = (meta && Array.isArray(meta.unavailable_fields)) ? meta.unavailable_fields : [];
    if (unavailable.indexOf('main_net_inflow') >= 0) ratio = 0;
    checks.push(check('flow_field', '数仓', '主力净流入字段',
      ratio === null ? 'warn' : (ratio >= 0.9 ? 'pass' : (ratio >= 0.5 ? 'warn' : 'fail')),
      ratio === null ? '无完整度元信息' : (Math.round(ratio * 100) + '% 有值'),
      ratio !== null && ratio >= 0.9 ? '资金排名与资金水位口径可用' : '资金字段缺失较多，资金流结论只作参考，不按 0 补齐'));

    var quality = (bd && bd.data_quality) || '';
    var boardOk = !!bd && bd.available !== false && quality !== 'empty';
    checks.push(check('review_quality', '数仓', '复盘数据质量',
      boardOk ? (quality === 'normal' ? 'pass' : 'warn') : 'fail',
      quality === 'normal' ? '正常' : (quality === 'partial' ? '部分缺失' : (quality || '未获取')),
      boardOk ? '复盘看板可作为当日盘面底稿' : '复盘数据为空或不可用，情绪结论不成立'));

    /* ---- 投研 / 健康 ---- */
    var cap = (capital.ok && capital.body && capital.body.data) ? capital.body.data : null;
    var nb = (cap && cap.northbound) || {};
    var topIn = (cap && cap.sector_main_flow && Array.isArray(cap.sector_main_flow.top_inflow)) ? cap.sector_main_flow.top_inflow : [];
    var capOk = !!(cap && (nb.available || topIn.length));
    checks.push(check('capital', '投研', '资金面可用性',
      capOk ? (nb.available ? 'pass' : 'warn') : 'fail',
      capOk ? (nb.available ? '北向 + 板块资金' : '仅板块资金') : '未获取',
      capOk ? (nb.available ? '北向与板块主力资金可用' : '北向自 2024-08 停披，缺失不影响板块资金判断') : (capital.error || '资金面接口未返回数据')));

    var dhBody = (dh.ok && dh.body) ? dh.body : null;
    var overall = (dhBody && dhBody.overall) || 'unknown';
    var srcs = (dhBody && Array.isArray(dhBody.sources)) ? dhBody.sources : [];
    var okN = srcs.filter(function (s) { return s.status === 'ok'; }).length;
    var downN = srcs.filter(function (s) { return s.status === 'down'; }).length;
    checks.push(check('source_health', '投研', '数据源健康',
      overall === 'ok' ? 'pass' : (overall === 'down' ? 'fail' : 'warn'),
      overall === 'ok' ? (okN + ' 路正常') : (overall === 'down' ? (downN + ' 路已中断') : '部分未探测'),
      overall === 'ok' ? '本进程内各数据源最近抓取正常' : '存在降级或未探测的数据源，详见「系统配置 → 数据源健康」'));

    return checks;
  }

  function summarize(checks) {
    var passed = checks.filter(function (c) { return c.status === 'pass'; }).length;
    var failed = checks.filter(function (c) { return c.status === 'fail'; }).length;
    var hardGate = checks.filter(function (c) {
      return c.key === 'breadth' || c.key === 'limit';
    }).every(function (c) { return c.status === 'pass'; });
    var overall = failed === 0 ? 'ready' : (failed >= 3 ? 'blocked' : 'degraded');
    return { passed: passed, total: checks.length, failed: failed, hardGate: hardGate, overall: overall };
  }

  function render() {
    var body = $('dqcBody');
    var badge = $('dqcSummary');
    if (!body) return;
    if (!state.checks) {
      body.innerHTML = '<div class="dqc-empty">切换到本页自动检查，或点击「运行检查」。</div>';
      if (badge) { badge.className = 'dqc-badge is-unknown'; badge.textContent = '待检查'; }
      return;
    }
    var checks = state.checks;
    var sum = summarize(checks);

    if (badge) {
      badge.className = 'dqc-badge is-' + sum.overall;
      badge.textContent = sum.overall === 'ready' ? ('数据就绪 · ' + sum.passed + '/' + sum.total)
        : (sum.overall === 'degraded' ? ('数据降级 · ' + sum.passed + '/' + sum.total)
          : ('数据未就绪 · ' + sum.passed + '/' + sum.total));
    }

    var items = checks.map(function (c) {
      return '<div class="dqc-item is-' + c.status + '" data-dqc="' + esc(c.key) + '">'
        + '<span class="dqc-dot" aria-hidden="true"></span>'
        + '<div class="dqc-main">'
        + '<div class="dqc-name"><span>' + esc(c.name) + '</span><b>' + esc(c.value) + '</b></div>'
        + '<div class="dqc-desc">' + esc(c.desc) + '</div>'
        + '</div></div>';
    }).join('');

    var gate = sum.hardGate
      ? '<div class="dqc-gate is-pass"><b>硬门禁通过</b> 核心行情与涨跌停家数齐备，可据此做复盘与策略判断；结论仍不构成投资建议。</div>'
      : '<div class="dqc-gate is-fail"><b>硬门禁未过</b> 涨跌家数或涨跌停缺失：工作台情绪温度、次日判断只作参考，缺失项不按 0 补齐。</div>';

    body.innerHTML = '<div class="dqc-grid">' + items + '</div>' + gate
      + '<div class="dqc-foot">检查于 ' + esc(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
      + ' · 共 ' + checks.length + ' 项 · 规则引擎输出，不预测行情</div>';
  }

  async function load(force) {
    if (state.loading) return;
    if (!force && state.checks && Date.now() - state.loadedAt < CACHE_MS) {
      render();
      return;
    }
    state.loading = true;
    var body = $('dqcBody');
    if (body && !state.checks) body.innerHTML = '<div class="dqc-empty">正在并行检查 5 路数据…</div>';
    var suffix = force ? ('?_t=' + Date.now()) : '';
    try {
      var settledRes = await Promise.allSettled([
        api('/api/market/index_realtime' + suffix),
        api('/api/review/board' + suffix),
        api('/api/market/sector_flow' + suffix),
        api('/api/market/capital-flow' + suffix),
        api('/api/data/health' + suffix)
      ]);
      state.checks = buildChecks(
        settled(settledRes[0]),
        settled(settledRes[1]),
        settled(settledRes[2]),
        settled(settledRes[3]),
        settled(settledRes[4])
      );
      state.error = '';
      state.loadedAt = Date.now();
      render();
    } catch (e) {
      state.error = String(e && e.message ? e.message : e);
      state.checks = null;
      if (body) body.innerHTML = '<div class="dqc-empty is-err">检查失败：' + esc(state.error) + '</div>';
    } finally {
      state.loading = false;
    }
  }

  function maybeAutoLoad() {
    var page = $('page-data');
    if (page && page.classList.contains('active')) load(false);
  }

  function wrapSwitchPage() {
    if (typeof window.switchPage !== 'function' || window.__dataGateWrapped) return;
    window.__dataGateWrapped = true;
    var orig = window.switchPage;
    window.switchPage = function (name) {
      var out = orig.apply(this, arguments);
      if (name === 'data') {
        try { load(false); } catch (e) { /* ignore */ }
      }
      return out;
    };
  }

  function init() {
    wrapSwitchPage();
    maybeAutoLoad();
  }

  window.DataGate = { load: load };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  window.addEventListener('hashchange', maybeAutoLoad);
})();
