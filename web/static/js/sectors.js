/**
 * 阶段5 T04 — 板块聚合视图(SectorView)
 *
 * PRD P2-2 / US-6:按 CPO/PCB/存储/半导体(映射来自 GET /api/config/sector-map,
 * 可配置)聚合基金持仓的市值集中度(占比条形)与当日收益合计;未映射基金归
 * unmapped_label("其他")。聚合计算在前端(持仓数量级 <50,无后端聚合端点)。
 * 视图内可切换"板块/列表"(列表含 Freshness 新鲜度徽章,四件套规范)。
 * 当日收益口径与后端一致:市值 - 市值/(1+涨幅%)。
 * 模块为普通 <script> 全局对象(与存量风格一致,无构建步骤)。
 */
/* global api, escapeHtml, fmtMoney, fmtNum, changeColor, Freshness */

(function () {
  'use strict';

  function injectStyle() {
    if (document.getElementById('sector-view-style')) return;
    const style = document.createElement('style');
    style.id = 'sector-view-style';
    style.textContent = [
      '.sector-row{padding:8px 0;border-bottom:1px solid var(--border,#2a3142);}',
      '.sector-bar-wrap{height:14px;border-radius:7px;background:rgba(255,255,255,0.05);',
      '  overflow:hidden;margin-top:4px;}',
      '.sector-bar{height:100%;border-radius:7px;background:var(--accent-blue,#3b82f6);',
      '  transition:width 0.3s ease;}',
      '.sector-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;',
      '  flex-wrap:wrap;font-size:13px;}',
      '.sv-table{width:100%;border-collapse:collapse;font-size:13px;}',
      '.sv-table th,.sv-table td{padding:8px 6px;text-align:left;border-bottom:1px solid var(--border,#2a3142);}',
    ].join('');
    document.head.appendChild(style);
  }

  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s) : String(s == null ? '' : s); }

  const state = { view: 'sector', sectors: {}, unmappedLabel: '其他', loaded: false };

  /** 当日收益(与后端 GET /positions 口径一致) */
  function todayPnl(p) {
    if (!p.market_value || !p.change_pct) return 0;
    return p.market_value - p.market_value / (1 + p.change_pct / 100);
  }

  async function load() {
    injectStyle();
    const body = document.getElementById('hudSectorBody');
    if (!body) return;
    body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);">板块聚合加载中...</div>';
    // 并行拉取:板块映射(可配置)+ 持仓(含市值/涨跌幅/新鲜度字段)
    const [mapResp, posResp] = await Promise.all([
      api('/api/config/sector-map'),
      api('/api/fund/positions'),
    ]);
    state.sectors = (mapResp && mapResp.sectors) || {};
    state.unmappedLabel = (mapResp && mapResp.unmapped_label) || '其他';
    state.positions = (posResp && posResp.positions) || [];
    state.loaded = true;
    render();
  }

  /** 按板块归组:代码→板块 反查表;未映射归 unmapped_label */
  function groupBySector() {
    const codeToSector = {};
    Object.keys(state.sectors).forEach(function (sector) {
      (state.sectors[sector] || []).forEach(function (code) { codeToSector[code] = sector; });
    });
    const groups = {};
    Object.keys(state.sectors).forEach(function (s) { groups[s] = []; });
    groups[state.unmappedLabel] = groups[state.unmappedLabel] || [];
    state.positions.forEach(function (p) {
      const s = codeToSector[p.fund_code] || state.unmappedLabel;
      (groups[s] = groups[s] || []).push(p);
    });
    return groups;
  }

  function renderSectorView() {
    const groups = groupBySector();
    const totalMv = state.positions.reduce(function (s, p) { return s + (p.market_value || 0); }, 0);
    const names = Object.keys(groups).filter(function (s) { return groups[s].length; });
    if (!names.length) {
      return '<div style="padding:24px;text-align:center;color:var(--text-muted);">暂无持仓数据</div>';
    }
    return names.map(function (sector) {
      const list = groups[sector];
      const mv = list.reduce(function (s, p) { return s + (p.market_value || 0); }, 0);
      const pnl = list.reduce(function (s, p) { return s + todayPnl(p); }, 0);
      const pct = totalMv > 0 ? (mv / totalMv * 100) : 0;
      return '<div class="sector-row">' +
        '<div class="sector-head">' +
        '  <span><b>' + esc(sector) + '</b>' +
        '  <span style="font-size:11px;color:var(--text-muted);margin-left:6px;">' + list.length + ' 只</span></span>' +
        '  <span style="font-size:12px;color:var(--text-muted);">市值 ' + fmtMoney(mv) + ' · ' + pct.toFixed(1) + '%' +
        '  <span style="color:' + changeColor(pnl) + ';margin-left:8px;">当日 ' + (pnl >= 0 ? '+' : '') + fmtMoney(pnl) + '</span></span>' +
        '</div>' +
        '<div class="sector-bar-wrap"><div class="sector-bar" style="width:' + Math.min(pct, 100).toFixed(1) + '%;"></div></div>' +
        '</div>';
    }).join('') +
      '<div style="font-size:11px;color:var(--text-muted);margin-top:8px;">集中度 = 板块市值 / 组合总市值(' + fmtMoney(totalMv) + ');未映射基金归"' + esc(state.unmappedLabel) + '"</div>';
  }

  function renderListView() {
    if (!state.positions.length) {
      return '<div style="padding:24px;text-align:center;color:var(--text-muted);">暂无持仓数据</div>';
    }
    const rows = state.positions.map(function (p) {
      const pnl = todayPnl(p);
      return '<tr>' +
        '<td>' + esc(p.fund_code) + '</td>' +
        '<td>' + esc(p.fund_name || '—') + '</td>' +
        '<td>' + fmtNum(p.shares) + '</td>' +
        '<td>' + fmtMoney(p.market_value || 0) +
        '  <div>' + Freshness.html({
          asOf: p.nav_date || '', estimated: p.confidence === 'low' && !p.nav_date,
          note: p.confidence === 'low' ? (p.confidence_reason || '') : '',
        }) + '</div></td>' +
        '<td style="color:' + changeColor(pnl) + ';">' + (pnl >= 0 ? '+' : '') + fmtMoney(pnl) + '</td>' +
        '</tr>';
    }).join('');
    return '<div style="overflow-x:auto;"><table class="sv-table">' +
      '<thead><tr><th>代码</th><th>名称</th><th>份额</th><th>市值</th><th>当日收益</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>';
  }

  function render() {
    const body = document.getElementById('hudSectorBody');
    if (!body) return;
    body.innerHTML = state.view === 'sector' ? renderSectorView() : renderListView();
    const tabs = document.querySelectorAll('#hudSectorTabs .hud-subtab');
    tabs.forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-view') === state.view);
    });
  }

  function switchView(view) {
    state.view = view === 'list' ? 'list' : 'sector';
    if (!state.loaded) { load(); return; }
    render();
  }

  window.SectorView = { load: load, switchView: switchView };
})();
