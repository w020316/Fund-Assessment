/* ===== STAGE7: global-dashboard ===== 全球市场面板前端
   数据源: GET /api/global/dashboard(匿名端点;后端聚合缓存 30s)
   组成: 全球指数全景(核心 5 条 + 美洲/欧洲/亚太/宏观 18 条) + 全球行业板块(11 条 SPDR ETF + VIX)
         + 行情总览页全球速览条(#globalStrip,点击 chip 跳国际市场页)
   接入方式: 包装 switchPage(与 stage6-portfolio/evolution 同款机制),
         market/global 页首次激活才拉数据,不自创全局轮询。
   数据约定: 缺失(null)一律渲染「—」,严禁 Number(x)||0 归零;红涨绿跌用既有 token。 */
(function () {
  'use strict';

  /* ---------- 速览条白名单(行情总览页): code 归一化匹配 + 名称兜底 ---------- */
  const STRIP_WHITELIST = [
    { code: '.DJI', alts: ['DJIA', 'USDJI'], names: ['道琼斯'] },
    { code: '.IXIC', alts: ['NDX', 'USIXIC'], names: ['纳斯达克'] },
    { code: '.INX', alts: ['SPX', 'USINX'], names: ['标普500'] },
    { code: '.HSI', alts: ['HSI', 'HKHSI'], names: ['恒生指数', '恒生'] },
    { code: 'N225', alts: [], names: ['日经225'] },
    { code: 'GDAXI', alts: [], names: ['德国DAX', 'DAX30'] },
    { code: 'FTSE', alts: [], names: ['富时100', '英国富时'] },
    { code: 'UDI', alts: [], names: ['美元指数'] },
  ];

  const REGION_ORDER = ['americas', 'europe', 'asia', 'macro'];
  const REGION_LABEL_FALLBACK = { americas: '美洲', europe: '欧洲', asia: '亚太', macro: '宏观' };

  const CACHE_TTL_MS = 60000; // 前端侧轻量缓存 60s;后端本身还有 30s 聚合缓存
  let _cache = null;
  let _promise = null;
  let _cacheAt = 0;

  /* ---------- 工具 ---------- */
  function normCode(c) {
    return String(c || '').replace(/^\./, '').split('.')[0].toUpperCase();
  }

  function isNum(v) {
    return typeof v === 'number' && isFinite(v);
  }

  /** 涨跌幅文本:缺失「—」,正数补 + 号(复用 index.html 的 changeSign/fmtNum) */
  function pctText(v) {
    return isNum(v) ? changeSign(v) + fmtNum(v) + '%' : '—';
  }

  /** 涨跌色:缺失用 muted,不归零(红涨绿跌) */
  function pctColor(v) {
    return isNum(v) ? changeColor(v) : 'var(--text-muted)';
  }

  /** 依赖 index.html 的全局助手,缺失时给出可诊断的空渲染而非抛错 */
  function helpersReady() {
    return typeof changeSign === 'function' && typeof fmtNum === 'function'
      && typeof changeColor === 'function' && typeof escapeHtml === 'function';
  }

  function emptyHtml(msg) {
    return `<div class="gm-empty">${escapeHtml(msg)}</div>`;
  }

  /* ---------- 数据获取(单飞 + TTL) ---------- */
  function fetchDashboard(force) {
    const now = Date.now();
    if (!force && _promise && now - _cacheAt < CACHE_TTL_MS) return _promise;
    if (!force && _cache && now - _cacheAt < CACHE_TTL_MS) return Promise.resolve(_cache);
    _cacheAt = now;
    _promise = api('/api/global/dashboard')
      .then((resp) => { _cache = resp && resp.data ? resp : null; return _cache; })
      .catch(() => { _cache = null; return null; });
    return _promise;
  }

  /* ---------- 全球指数全景 ---------- */
  function groupAvg(rows) {
    const vals = rows.map((r) => r.change_pct).filter(isNum);
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  }

  function renderGroup(label, rows) {
    const avg = groupAvg(rows);
    const avgHtml = avg == null
      ? '<span class="gm-group-avg" style="color:var(--color-flat);">均值 —</span>'
      : `<span class="gm-group-avg" style="color:${changeColor(avg)};">均值 ${changeSign(avg)}${avg.toFixed(2)}%</span>`;
    const cells = rows.map((r) => `
      <div class="gm-idx-cell">
        <div class="gm-idx-name" title="${escapeHtml(r.code || '')}">${escapeHtml(r.name || r.code || '—')}</div>
        <div class="gm-idx-val">
          <span class="gm-idx-price">${fmtNum(r.price)}</span>
          <span class="gm-idx-pct" style="color:${pctColor(r.change_pct)};">${pctText(r.change_pct)}</span>
        </div>
      </div>`).join('');
    return `<div class="gm-group">
      <div class="gm-group-head"><span class="gm-group-label">${escapeHtml(label)}</span>${avgHtml}</div>
      <div class="gm-idx-grid">${cells}</div>
    </div>`;
  }

  function renderPanorama(d) {
    const body = document.getElementById('gmPanoramaBody');
    if (!body) return;
    const core = (d && d.core_indices) || [];
    const world = (d && d.world_indices) || [];
    if (!core.length && !world.length) {
      body.innerHTML = emptyHtml('全球指数数据暂不可用，请稍后刷新');
      return;
    }
    /* 核心指数(腾讯源 5 条)独立成「核心」组置顶;world_indices(东财 18 条)按 region 分四组。
       两组对道指/纳指等重叠代码各自展示,口径不同(源不同),不去重。 */
    let html = core.length ? renderGroup('核心', core) : '';
    REGION_ORDER.forEach((rg) => {
      const rows = world.filter((r) => r.region === rg);
      if (rows.length) html += renderGroup(REGION_LABEL_FALLBACK[rg] || rg, rows);
    });
    body.innerHTML = html;
  }

  /* ---------- 全球行业板块 + VIX ---------- */
  function vixBadge(vix) {
    if (!vix) return '';
    const high = isNum(vix.price) && vix.price > 20;
    return `<span class="gm-vix-badge${high ? ' is-high' : ''}" title="${escapeHtml(vix.name || 'VIX')}">
      VIX <b>${fmtNum(vix.price)}</b>
      <i style="color:${pctColor(vix.change_pct)};">${pctText(vix.change_pct)}</i>
      <em>${high ? '恐慌偏高' : '平稳'}</em>
    </span>`;
  }

  function renderSectors(d) {
    const body = document.getElementById('gmSectorsBody');
    if (!body) return;
    const rows = (d && d.sectors) || [];
    if (!rows.length) {
      body.innerHTML = emptyHtml('行业板块数据暂不可用，请稍后刷新');
      return;
    }
    const maxAbs = rows.reduce((m, r) => (isNum(r.change_pct) ? Math.max(m, Math.abs(r.change_pct)) : m), 0);
    const list = rows.map((r) => {
      const pct = isNum(r.change_pct) ? r.change_pct : null;
      /* 比例条宽度按 |涨跌幅| 相对组内最大值归一;缺失不给宽度(不造假数据) */
      const w = (pct != null && maxAbs > 0) ? Math.max(2, Math.round((Math.abs(pct) / maxAbs) * 100)) : 0;
      const barColor = pct == null ? 'var(--text-muted)'
        : (pct > 0 ? 'var(--color-up)' : (pct < 0 ? 'var(--color-down)' : 'var(--color-flat)'));
      return `<div class="gm-sector-row">
        <span class="gm-sector-name" title="${escapeHtml(r.name || '')}">${escapeHtml(r.sector || r.code || '—')}</span>
        <span class="gm-sector-bar"><i style="width:${w}%;background:${barColor};"></i></span>
        <span class="gm-sector-pct" style="color:${pctColor(pct)};">${pctText(pct)}</span>
      </div>`;
    }).join('');
    body.innerHTML = `<div class="gm-sector-head">
        <span class="gm-sector-cap">GICS 11 行业 · SPDR ETF 代理</span>
        ${vixBadge(d && d.vix)}
      </div>
      <div class="gm-sector-list">${list}</div>`;
  }

  /* ---------- 行情总览页速览条 ---------- */
  function matchStripItem(pool, w) {
    return pool.find((r) => normCode(r.code) === normCode(w.code)
      || (w.alts || []).some((a) => normCode(r.code) === normCode(a))
      || (w.names || []).some((n) => r.name && (r.name === n || r.name.includes(n) || n.includes(r.name))));
  }

  function renderStrip(d) {
    const el = document.getElementById('globalStrip');
    if (!el) return;
    const pool = [].concat((d && d.core_indices) || [], (d && d.world_indices) || []);
    if (!pool.length) {
      el.innerHTML = '<span class="gm-strip-empty">全球速览暂不可用</span>';
      return;
    }
    const chips = [];
    STRIP_WHITELIST.forEach((w) => {
      const hit = matchStripItem(pool, w);
      if (hit && chips.indexOf(hit) === -1) chips.push(hit);
    });
    if (!chips.length) {
      el.innerHTML = '<span class="gm-strip-empty">全球速览暂不可用</span>';
      return;
    }
    el.innerHTML = chips.map((r) => `
      <button type="button" class="gm-chip" onclick="switchPage('global')" aria-label="查看国际市场详情">
        <span class="gm-chip-name">${escapeHtml(r.name || r.code || '—')}</span>
        <span class="gm-chip-pct" style="color:${pctColor(r.change_pct)};">${pctText(r.change_pct)}</span>
      </button>`).join('');
  }

  /* ---------- 入口 ---------- */
  function renderAll(d) {
    renderPanorama(d);
    renderSectors(d);
    renderStrip(d);
  }

  async function load(force) {
    if (!helpersReady()) return;
    const d = await fetchDashboard(force);
    renderAll(d);
  }

  window.GlobalMarket = {
    load,
    refresh() { return load(true); },
  };

  /* 包装 switchPage(既有挂钩机制):market=速览条 / global=全景面板,首次激活才拉数据 */
  const _origSwitch = window.switchPage;
  if (typeof _origSwitch === 'function') {
    window.switchPage = function (name) {
      if (name === 'global' || name === 'market') load();
      return _origSwitch.apply(this, arguments);
    };
  } else {
    // 兜底:index.html 主脚本异常未定义 switchPage 时,等 DOM ready 直接拉一次
    document.addEventListener('DOMContentLoaded', () => load());
  }
})();
