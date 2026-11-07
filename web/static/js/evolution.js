/* ===== STAGE5: evolution ===== 策略自进化前端(B6 六端点;谱系/详情/对比/操作流) */
/**
 * evolution.js — 策略自进化前端（B6 六端点;F1-F4）
 *
 * 设计约定(docs/strategy-evolution-design-2026-11-07.md §2/§6 阶段 B):
 * - 独立文件净增;index.html 只挂子 Tab/页面容器/script 引用(仅新增节点,不动 10→5 IA)
 * - 「策略进化」子页经包装 switchPage 接入 STAGE5 父子联动(同 stage6-portfolio.js 模式)
 * - 复用全局组件: api()(自动注入 admin_token)、showToast、renderStatus、
 *   tokenGateHTML(Token 门控)、customConfirm(二次确认,ESC/遮罩可关)
 * - 红涨绿跌: 收益/年化用 .st6-up/.st6-down(A股语义);回撤/夏普/胜率中性色
 * - 触摸目标 ≥48px(主操作按钮内联 min-height:48px,移动端由 hud-theme 基线兜底)
 * - 503 展示后端结构化 code+message, 不弹"未知错误"
 */
/* global api, renderStatus, showToast, tokenGateHTML, customConfirm, _hasAdminToken */
(function () {
  'use strict';

  const BASE = '/api/strategy/evolution';
  const EVO_PAGE = 'evolution';
  const PARENT_PAGE = 'hud-strategy';
  const RUN_POLL_INTERVAL_MS = 10000; // F4: run 进行中每 10s 轮询 status

  const state = {
    lineage: null,        // 最近一次 /lineage 响应
    activeId: null,       // 当前激活代
    detailId: null,       // 详情区当前展示的代
    runInFlight: false,   // run 请求在途
    pollTimer: null,
  };

  // ===== utils =====

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function num(v, digits) {
    if (v == null || isNaN(Number(v))) return '—';
    return Number(v).toFixed(digits == null ? 2 : digits);
  }

  /** 分数(0-1)→百分比字符串 */
  function pct(v, digits) {
    if (v == null || isNaN(Number(v))) return '—';
    return (Number(v) * 100).toFixed(digits == null ? 2 : digits) + '%';
  }

  /** 红涨绿跌(A股语义): 仅用于收益/年化类指标 */
  function upDownCls(v) {
    if (v == null || isNaN(Number(v)) || Number(v) === 0) return 'st6-flat';
    return Number(v) > 0 ? 'st6-up' : 'st6-down';
  }

  function $(id) { return document.getElementById(id); }

  function hasToken() {
    if (typeof _hasAdminToken === 'function') { try { return !!_hasAdminToken(); } catch (e) { /* fallthrough */ } }
    try { return !!localStorage.getItem('admin_token'); } catch (e) { return false; }
  }

  function callApi(url, options) {
    if (typeof api !== 'function') {
      return Promise.reject(new Error('api() 组件不可用'));
    }
    return Promise.resolve(api(url, options));
  }

  /** 503/4xx 结构化错误 → 可读一行(code: message), 不弹"未知错误" */
  function errText(e) {
    const code = (e && e.code) || '';
    const msg = (e && (e.detail || e.message)) || String(e);
    return code ? `${code}: ${msg}` : String(msg);
  }

  function showStatusLine(text, isError) {
    const line = $('evoStatusLine');
    if (!line) return;
    if (!text) { line.style.display = 'none'; line.textContent = ''; return; }
    line.style.display = 'block';
    line.innerHTML = `<span style="color:${isError ? 'var(--color-up,#F87171)' : 'var(--color-down,#34D399)'};">${esc(text)}</span>`;
  }

  function gateOr(elId, what) {
    const body = $(elId);
    if (!body) return false;
    if (!hasToken()) {
      body.innerHTML = typeof tokenGateHTML === 'function' ? tokenGateHTML(what) : '未配置管理员 Token';
      return false;
    }
    return true;
  }

  // ===== F1/F2: switchPage 父子联动(包装, 不改 STAGE5_PAGE_MAP) =====

  function activateEvolutionPage(loader) {
    document.querySelectorAll('.page').forEach(function (p) { p.classList.remove('active'); });
    document.querySelectorAll('.nav-item').forEach(function (n) { n.classList.remove('active'); });
    document.querySelectorAll('.bottom-tab-item').forEach(function (n) { n.classList.remove('active'); });
    const page = $('page-' + EVO_PAGE);
    const parent = $('page-' + PARENT_PAGE);
    if (page) page.classList.add('active');
    if (parent) parent.classList.add('active');
    document.querySelectorAll(`.nav-item[data-page="${PARENT_PAGE}"]`).forEach(function (n) { n.classList.add('active'); });
    document.querySelectorAll(`.bottom-tab-item[data-page="${PARENT_PAGE}"]`).forEach(function (n) { n.classList.add('active'); });
    document.querySelectorAll('.hud-subtab').forEach(function (t) { t.classList.remove('active'); });
    const sub = document.querySelector(`.hud-subtab[data-page="${EVO_PAGE}"]`);
    if (sub) sub.classList.add('active');
    if (typeof loader === 'function') loader();
  }

  const _origSwitchPage = window.switchPage;
  window.switchPage = function (name) {
    if (name === EVO_PAGE) { activateEvolutionPage(Evolution.loadAll); return; }
    if (typeof _origSwitchPage === 'function') return _origSwitchPage.apply(this, arguments);
  };

  // ===== F2: 谱系列表 + 单代详情 =====

  function renderLineage(data) {
    const body = $('evoBody');
    if (!body) return;
    state.lineage = data;
    state.activeId = data.active_generation_id;

    const gens = data.generations || [];
    if (!gens.length) {
      renderStatus(body, 'empty', '暂无进化代',
        '点击「开始进化」产生第一代(单次预算 25s, 与回测互斥, 无优于父代者不写盘)');
      return;
    }

    const activeBadge = '<span style="font-size:11px;color:var(--qf-neon-cyan,#22E3FF);border:1px solid rgba(34,227,255,.34);border-radius:4px;padding:0 6px;margin-left:6px;">当前激活</span>';
    const rows = gens.map(function (g) {
      const isActive = g.generation_id === data.active_generation_id;
      const isShown = g.generation_id === state.detailId;
      return `<div role="button" tabindex="0" data-gen="${g.generation_id}"
        style="display:flex;align-items:center;gap:10px;min-height:48px;padding:6px 10px;margin-bottom:4px;cursor:pointer;
               border:1px solid ${isShown ? 'var(--accent-blue)' : 'var(--border)'};border-radius:8px;
               background:${isActive ? 'rgba(34,227,255,.06)' : 'transparent'};"
        onclick="Evolution.showDetail(${g.generation_id})"
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();Evolution.showDetail(${g.generation_id});}">
        <strong style="min-width:52px;">第 ${g.generation_id} 代</strong>
        <span style="font-size:12px;">fitness <b>${num(g.fitness, 4)}</b></span>
        <span style="font-size:11px;color:var(--text-muted);">${esc(g.created_at || '')}</span>
        <span style="font-size:11px;color:var(--text-muted);margin-left:auto;">digest ${esc(g.params_digest || '')}</span>
        ${isActive ? activeBadge : ''}
      </div>`;
    }).join('');

    body.innerHTML =
      `<div style="font-size:12px;color:var(--text-muted);margin-bottom:6px;">
         谱系共 ${data.count} 代(按代号倒序); ${data.active_generation_id ? `当前激活第 ${data.active_generation_id} 代` : '当前为出厂参数(从未激活)'}
       </div>` + rows + `<div id="evoDetailBox" style="margin-top:8px;"></div>`;

    // 默认展开最新一代详情
    if (!state.detailId) showDetail(gens[0].generation_id);
  }

  function renderDetail(record) {
    const box = $('evoDetailBox');
    if (!box) return;
    const p = record.params || {};
    const w = p.weights || {};
    const s = p.signals || {};
    const m = record.metrics || {};

    const paramRow = (label, val) =>
      `<div style="display:flex;justify-content:space-between;gap:8px;padding:2px 0;font-size:12px;">
         <span style="color:var(--text-muted);">${esc(label)}</span><span>${esc(val)}</span>
       </div>`;

    const metricRow = (label, val, cls) =>
      `<div style="display:flex;justify-content:space-between;gap:8px;padding:2px 0;font-size:12px;">
         <span style="color:var(--text-muted);">${esc(label)}</span><span class="${cls || ''}">${esc(val)}</span>
       </div>`;

    box.innerHTML = `
      <div style="border:1px solid var(--border);border-radius:8px;padding:12px;">
        <div class="flex-between" style="flex-wrap:wrap;gap:8px;">
          <div style="font-size:13px;font-weight:600;">第 ${record.generation_id} 代详情
            <span style="font-size:11px;color:var(--text-muted);font-weight:400;">${esc(record.created_at || '')}</span>
          </div>
          <div style="display:flex;gap:8px;" id="evoDetailActions"></div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:8px;">
          <div>
            <div style="font-size:12px;color:var(--text-secondary);font-weight:600;margin-bottom:4px;">指标(净值模拟口径)</div>
            ${metricRow('年化收益率', pct(m.annual_return), upDownCls(m.annual_return))}
            ${metricRow('最大回撤', pct(m.max_drawdown), 'st6-flat')}
            ${metricRow('夏普比率', num(m.sharpe), 'st6-flat')}
            ${metricRow('胜率', pct(m.win_rate), 'st6-flat')}
            ${metricRow('适应度 fitness', num(m.fitness, 4), 'st6-flat')}
            <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">
              评估窗 ${m.eval_window_days == null ? '—' : m.eval_window_days} 日 · ${m.fund_count == null ? '—' : m.fund_count} 只基金 · 候选 ${m.evaluated_candidates == null ? '—' : m.evaluated_candidates} 个
            </div>
          </div>
          <div>
            <div style="font-size:12px;color:var(--text-secondary);font-weight:600;margin-bottom:4px;">参数(激活后写入 strategies.yaml)</div>
            ${Object.keys(w).map(function (k) { return paramRow('权重·' + k, num(w[k], 4)); }).join('')}
            ${Object.keys(s).map(function (k) { return paramRow('阈值·' + k, num(s[k], 1)); }).join('')}
          </div>
        </div>
        <div style="font-size:11px;color:var(--text-tertiary);margin-top:6px;">
          父代: ${record.parent_id == null ? '无(初始代)' : '第 ' + record.parent_id + ' 代'} · 适应度 ${num(record.parent_fitness, 4)} · ${record.improved_over_parent ? '优于父代' : '未优于父代'}
        </div>
      </div>`;
  }

  function loadDetail(genId) {
    const box = $('evoDetailBox');
    if (!box) return;
    renderStatus(box, 'loading');
    callApi(`${BASE}/generations/${genId}`)
      .then(function (record) {
        renderDetail(record);
        if (typeof Evolution.onDetailLoaded === 'function') Evolution.onDetailLoaded(record);
      })
      .catch(function (e) {
        if (e && e.status === 401) { box.innerHTML = tokenGateHTML('策略进化详情'); return; }
        renderStatus(box, 'error', '代际详情加载失败', errText(e));
      });
  }

  // ===== 对外 API =====

  const Evolution = {
    /** 进入页面/手动刷新: 拉谱系(空态/加载/错误走 hud-status) */
    loadAll: function () {
      if (!gateOr('evoBody', '策略进化')) return;
      renderStatus($('evoBody'), 'loading');
      callApi(`${BASE}/lineage`)
        .then(function (data) { renderLineage(data); })
        .catch(function (e) {
          if (e && e.status === 401) { $('evoBody').innerHTML = tokenGateHTML('策略进化'); return; }
          renderStatus($('evoBody'), 'error', '谱系加载失败', errText(e));
        });
    },

    showDetail: function (genId) {
      state.detailId = genId;
      // 高亮当前详情行(重画列表保持状态)
      if (state.lineage) renderLineage(state.lineage);
      else loadAll();
      loadDetail(genId);
    },

    /** 供后续 F3/F4 挂载: 对比面板与操作流在 detail 渲染后注入动作按钮 */
    onDetailLoaded: null,

    _state: state,
  };

  window.Evolution = Evolution;
})();
