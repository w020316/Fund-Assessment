/**
 * STAGE6-PRED: recommendations.js — 策略容器"今日推荐"区块(R4.2/R4.3)
 *   + 持仓份额估算角标与行内编辑(R7.1, 经 A10 端点)
 *   + 北向/QDII 口径说明文案(R7.3/R7.4, 挂载点 STAGE6-PRED: freshness-coverage)。
 *
 * 铁律:
 * - 每张推荐卡片底部必现免责文案"仅供参考, 不构成投资建议"(R4.2)
 * - 无信号显示"今日无满足条件的推荐"(R4.3)
 * - 持仓表格行内编辑经 MutationObserver 注入, 不改 index.html 既有渲染函数
 */
/* global escapeHtml, Freshness, showToast, loadFundData */
(function () {
  'use strict';

  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : ''; }

  function cstDateStr() {
    return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date());
  }

  async function fetchJSON(url, options) {
    const opts = options || {};
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    try {
      const t = localStorage.getItem('admin_token');
      if (t) headers['Authorization'] = 'Bearer ' + t;
    } catch (e) { /* ignore */ }
    try {
      const resp = await fetch(url, Object.assign({}, opts, { headers }));
      const body = await resp.json();
      return { ok: resp.ok && body && body.code === 0, status: resp.status, body: body };
    } catch (e) {
      return { ok: false, status: 0, body: null, error: e };
    }
  }

  // ===== 今日推荐(R4.3) =====

  const DISCLAIMER = '<div class="pred-disclaimer">仅供参考, 不构成投资建议。标的由规则信号生成并经 AI 润色理由, 请独立判断并自担风险。</div>';

  function itemHTML(it) {
    const cls = it.direction === 'up' ? 'up' : 'down';
    const dirLabel = it.direction === 'up' ? '看涨' : '看跌';
    return '<div class="pred-recommend-item">'
      + '<div class="pred-rec-head">'
      + '<span class="pred-rec-name">' + esc(it.name || it.symbol) + '</span>'
      + '<span class="pred-muted">' + esc(it.symbol) + ' · ' + (it.kind === 'fund' ? '基金' : '股票/ETF') + '</span>'
      + '<span class="pred-dir-badge ' + cls + '">' + dirLabel + '</span>'
      + '<span class="pred-conf">置信度 ' + esc(it.confidence) + '</span>'
      + Freshness.html({ asOf: esc(String(it.created_at || '').slice(11, 16)), note: '信号: ' + esc(it.source_signal) })
      + '</div>'
      + '<div class="pred-rec-reason">' + esc(it.reason || '—') + '</div>'
      + DISCLAIMER
      + '</div>';
  }

  async function load() {
    const el = document.getElementById('predRecommendBody');
    if (!el) return;
    el.innerHTML = '<div class="pred-empty">加载中…</div>';
    const r = await fetchJSON('/api/prediction/recommendations?days=2');
    // QF3-27: 先判 ok 再判空。此前失败时提示"今日无满足条件的推荐(信号未触发)",
    // 把"取不到"解释成"没有", 用户会以为策略没触发而不再关注。
    if (!r.ok) {
      el.innerHTML = r.status
        ? '<div class="pred-empty">数据加载失败（HTTP ' + r.status + '），请稍后重试</div>'
        : '<div class="pred-empty">数据加载失败，请检查网络后重试</div>';
      return;
    }
    const rows = (r.body && r.body.data && r.body.data.recommendations) || [];
    const today = cstDateStr();
    const todays = rows.filter(function (it) { return it.trade_date === today; });
    if (!todays.length) {
      el.innerHTML = '<div class="pred-empty">今日无满足条件的推荐(信号未触发或盘后未生成, 生成时点 15:40)</div>';
      return;
    }
    el.innerHTML = todays.map(itemHTML).join('')
      + '<div class="pred-muted" style="margin-top:4px;">共 ' + todays.length
      + ' 条 · 每条标的均可通过"信号"角标追溯到具体规则信号; 无法追溯的条目已被系统丢弃。</div>';
  }

  // ===== 持仓份额估算角标 + 行内编辑(R7.1) =====

  let estimatedCodes = null; // Set<string> | null(未加载)

  async function loadEstimatedCodes() {
    try {
      const t = localStorage.getItem('admin_token');
      const headers = t ? { 'Authorization': 'Bearer ' + t } : {};
      const resp = await fetch('/api/fund/positions', { headers: headers });
      if (!resp.ok) return null;
      const data = await resp.json();
      const rows = (data && data.positions) || [];
      const set = new Set();
      rows.forEach(function (p) { if (p.shares_estimated) set.add(String(p.fund_code)); });
      return set;
    } catch (e) {
      return null;
    }
  }

  function editShareRow(code) {
    const row = document.querySelector('#fundPositionBody tr[data-fund-code="' + code + '"]');
    if (!row) return;
    const cell = row.children[2]; // 第 3 列 = 份额
    if (!cell || cell.querySelector('.pred-share-edit-input')) return;
    const oldText = (cell.textContent || '').replace('份额为估算值,请复核', '').trim();
    cell.innerHTML = '<input class="pred-share-edit-input" type="number" min="0.01" step="0.01" value="'
      + esc(parseFloat(oldText.replace(/,/g, '')) || 0) + '" aria-label="编辑 ' + esc(code) + ' 的份额">'
      + '<button class="btn btn-sm btn-primary" style="padding:4px 8px;min-height:32px;" onclick="RecommendPage.saveShare(\'' + code + '\')">存</button> '
      + '<button class="btn btn-sm" style="padding:4px 8px;min-height:32px;" onclick="RecommendPage.cancelShareEdit()">取消</button>';
    const input = cell.querySelector('input');
    if (input) { input.focus(); input.select(); }
  }

  async function saveShare(code) {
    const row = document.querySelector('#fundPositionBody tr[data-fund-code="' + code + '"]');
    if (!row) return;
    const input = row.children[2].querySelector('input');
    if (!input) return;
    const val = parseFloat(input.value);
    if (!isFinite(val) || val <= 0) {
      if (typeof showToast === 'function') showToast('份额必须为正有限数', 'error');
      return;
    }
    const r = await fetchJSON('/api/fund/positions/' + code + '/shares', {
      method: 'PUT',
      body: JSON.stringify({ shares: val }),
    });
    if (r.ok) {
      if (typeof showToast === 'function') showToast('份额已更新', 'success');
      if (estimatedCodes) estimatedCodes.delete(code);
      enhanceFundTable(true); // 本地先刷新角标
      if (typeof loadFundData === 'function') loadFundData(); // 全量刷新持仓
    } else if (typeof showToast === 'function') {
      showToast('保存失败: ' + ((r.body && r.body.message) || '请稍后重试'), 'error');
    }
  }

  function cancelShareEdit() {
    if (typeof loadFundData === 'function') loadFundData();
  }

  function enhanceFundTable(force) {
    const body = document.getElementById('fundPositionBody');
    if (!body || estimatedCodes === null) return;
    body.querySelectorAll('tr[data-fund-code]').forEach(function (tr) {
      const code = tr.getAttribute('data-fund-code');
      const cell = tr.children[2];
      if (!cell) return;
      const hasBadge = cell.querySelector('.pred-share-estimated');
      const isEditing = cell.querySelector('.pred-share-edit-input');
      if (isEditing) return;
      if (estimatedCodes.has(code) && !hasBadge) {
        const badge = document.createElement('span');
        badge.className = 'pred-share-estimated';
        badge.title = '该份额为近似值(±5%), 请向券商核对后在此行内修正';
        badge.textContent = '份额为估算值,请复核';
        cell.appendChild(badge);
        const btn = document.createElement('button');
        btn.className = 'btn btn-sm';
        btn.style.cssText = 'padding:2px 8px;min-height:28px;margin-left:4px;';
        btn.setAttribute('aria-label', '行内编辑 ' + code + ' 的份额');
        btn.textContent = '改';
        btn.addEventListener('click', function () { editShareRow(code); });
        cell.appendChild(btn);
      } else if (!estimatedCodes.has(code) && hasBadge && force) {
        hasBadge.remove();
      }
    });
  }

  function observeFundTable() {
    const body = document.getElementById('fundPositionBody');
    if (!body) return;
    const mo = new MutationObserver(function () { enhanceFundTable(false); });
    mo.observe(body, { childList: true, subtree: true });
  }

  // ===== 北向/QDII 口径说明(R7.3/R7.4) =====

  const NORTHBOUND_NOTE = '<div class="pred-source-note" id="predNorthboundNote">'
    + '· 实时北向资金披露已于 2024-08 停止, 卡片展示的是最新可得 T+1 持股变动口径;'
    + '若上游不可用则显示"数据源未覆盖", 不再显示无意义的 0。<br>'
    + '· QDII 基金净值 T+1/T+2 披露, 相关卡片显示滞后 1~2 天属正常现象, 非数据故障。</div>';

  function patchNorthboundPanel() {
    const panel = document.getElementById('northboundPanel');
    if (!panel || document.getElementById('predNorthboundNote')) return;
    if (!panel.innerHTML) return; // 尚未渲染, 等下次 mutation
    panel.insertAdjacentHTML('afterbegin', NORTHBOUND_NOTE);
  }

  function observeNorthbound() {
    const panel = document.getElementById('northboundPanel');
    if (!panel) return;
    const mo = new MutationObserver(function () { patchNorthboundPanel(); });
    mo.observe(panel, { childList: true, subtree: true });
    // 全局新鲜度口径说明挂载点(STAGE6-PRED: freshness-coverage)
    const mount = document.getElementById('predFreshnessCoverage');
    if (mount && !mount.innerHTML) mount.innerHTML = NORTHBOUND_NOTE;
  }

  // ===== switchPage 包装接入 =====
  function wrapSwitchPage() {
    if (typeof window.switchPage !== 'function' || window.__recommendWrapped) return;
    window.__recommendWrapped = true;
    const orig = window.switchPage;
    window.switchPage = function (name) {
      const out = orig.apply(this, arguments);
      if (name === 'recommend') { try { window.RecommendPage.load(); } catch (e) { /* ignore */ } }
      if (name === 'fund') {
        // R7.1: 持仓页加载后拉取估算标识并注入角标
        (async function () {
          estimatedCodes = await loadEstimatedCodes();
          enhanceFundTable(false);
        })();
      }
      return out;
    };
  }

  window.RecommendPage = {
    load: load,
    saveShare: saveShare,
    cancelShareEdit: cancelShareEdit,
  };

  function init() {
    wrapSwitchPage();
    observeFundTable();
    observeNorthbound();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
