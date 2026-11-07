/**
 * STAGE6-PRED: sector-board.js — 行情容器"板块"二级页(R2.1)。
 *
 * - 数据源: 既有公开端点 GET /api/market/sector_flow(零新端点)
 * - 板块涨跌榜: 名称/涨跌幅/主力净流入/大单占比, 红涨绿跌, ≥30 行
 * - 数据源失败显示明确错误态; 右上角 Freshness 角标(统一组件)
 * 注: 上游 sector_flow 不提供领涨股字段(PRD R2.1 的"领涨股"列上游不支持),
 *     以主力净流入/大单占比替代呈现 —— 已在汇报中登记为偏离点。
 */
/* global escapeHtml, Freshness */
(function () {
  'use strict';

  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : ''; }

  function fmtYi(v) {
    const n = Number(v);
    if (!isFinite(n)) return '—';
    return (n / 1e8).toFixed(2) + ' 亿';
  }

  function pctCls(v) {
    const n = Number(v);
    if (!isFinite(n) || n === 0) return 'pred-flat';
    return n > 0 ? 'pred-up' : 'pred-down';
  }

  function rowHTML(item, idx) {
    const pct = Number(item.change_pct);
    const sign = pct > 0 ? '+' : '';
    return '<tr><td>' + esc(idx + 1) + '</td>'
      + '<td>' + esc(item.sector) + '</td>'
      + '<td class="' + pctCls(pct) + '">' + esc(sign + (isFinite(pct) ? pct.toFixed(2) : '—')) + '%</td>'
      + '<td class="' + pctCls(Number(item.main_net_inflow)) + '">' + esc(fmtYi(item.main_net_inflow)) + '</td>'
      + '<td>' + esc(isFinite(Number(item.large_order_ratio)) ? Number(item.large_order_ratio).toFixed(2) : '—') + '</td></tr>';
  }

  async function fetchJSON(url) {
    try {
      const headers = {};
      try {
        const t = localStorage.getItem('admin_token');
        if (t) headers['Authorization'] = 'Bearer ' + t;
      } catch (e) { /* ignore */ }
      const resp = await fetch(url, { headers: headers });
      const body = await resp.json();
      return { ok: resp.ok, body: body };
    } catch (e) {
      return { ok: false, body: null, error: e };
    }
  }

  async function load(force) {
    const el = document.getElementById('predSectorBoardBody');
    const badge = document.getElementById('predSectorFreshness');
    if (!el) return;
    el.innerHTML = '<div class="pred-empty">加载中…</div>';
    const r = await fetchJSON('/api/market/sector_flow' + (force ? '?_t=' + Date.now() : ''));
    const items = (r.ok && r.body && Array.isArray(r.body.data)) ? r.body.data : null;
    if (!items) {
      el.innerHTML = '<div class="pred-empty">板块数据加载失败: 数据源暂不可用, 请点击「刷新」重试</div>';
      if (badge) badge.innerHTML = '';
      return;
    }
    if (!items.length) {
      el.innerHTML = '<div class="pred-empty">数据源暂未返回板块数据, 请稍后重试</div>';
      if (badge) badge.innerHTML = '';
      return;
    }
    const sorted = items.slice().sort(function (a, b) { return Number(b.change_pct || 0) - Number(a.change_pct || 0); });
    const asOf = (r.body && r.body._meta && r.body._meta.timestamp) ? String(r.body._meta.timestamp).slice(11, 19) : '';
    if (badge) {
      badge.innerHTML = Freshness.html({ asOf: asOf, note: '东财板块行情' });
    }
    const rows = sorted.map(function (it, i) { return rowHTML(it, i); }).join('');
    el.innerHTML = '<div class="table-wrap" style="max-height:520px;overflow-y:auto;">'
      + '<table class="pred-sector-table">'
      + '<thead><tr><th>#</th><th>板块名称</th><th>涨跌幅</th><th>主力净流入</th><th>大单占比</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table></div>'
      + '<div class="pred-muted" style="margin-top:6px;">共 ' + sorted.length + ' 个板块 · 红涨绿跌 · 点击左侧「行情总览」返回</div>';
  }

  window.SectorBoard = { load: load };

  // switchPage 包装接入('sector-board' 页加载), 不改 index.html 既有函数
  function wrapSwitchPage() {
    if (typeof window.switchPage !== 'function' || window.__sectorBoardWrapped) return;
    window.__sectorBoardWrapped = true;
    const orig = window.switchPage;
    window.switchPage = function (name) {
      const out = orig.apply(this, arguments);
      if (name === 'sector-board') {
        try { window.SectorBoard.load(false); } catch (e) { /* ignore */ }
      }
      return out;
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wrapSwitchPage);
  } else {
    wrapSwitchPage();
  }
})();
