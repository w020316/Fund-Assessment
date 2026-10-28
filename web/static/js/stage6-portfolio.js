/**
 * stage6-portfolio.js — 阶段六批次A 前端(盯盘 / 风险 / X-Ray / 账本导入导出)
 *
 * 设计约定(架构文档 §5):
 * - 独立文件净增,存量页面与函数零搬动;index.html 只挂 Tab 与 script/css 引用
 * - 二级 Tab「盯盘 / 风险」经 monkey-patch switchPage 接入 STAGE5 父子联动,
 *   不改写 index.html 内的 STAGE5_PAGE_MAP(存量函数零修改)
 * - 复用全局组件: api()(自动注入 admin_token)、showToast、renderStatus、
 *   Freshness.html(新鲜度/估算标识统一组件)
 * - 红涨绿跌: .st6-up(#F87171) / .st6-down(#34D399),hud-theme 变量兼容
 * - 44px 触摸目标(.st6-window-tab 等)
 */
/* global Freshness, renderStatus, showToast, api */
(function () {
  'use strict';

  // ===== STAGE6: utils ===== 通用工具

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtMoney(v) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtPct(v, digits) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toFixed(digits == null ? 2 : digits) + '%';
  }

  /** 涨跌语义色(A股红涨绿跌) */
  function pctCls(v) {
    if (v == null || isNaN(v) || v === 0) return 'st6-flat';
    return v > 0 ? 'st6-up' : 'st6-down';
  }

  function pctRatio(v, digits) {
    if (v == null || isNaN(v)) return '—';
    return (Number(v) * 100).toFixed(digits == null ? 2 : digits) + '%';
  }

  /** api() 不可用时兜底(自动注入 admin_token) */
  function apiFallback(url, options) {
    options = options || {};
    const headers = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
    const token = localStorage.getItem('admin_token');
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return fetch(url, Object.assign({}, options, { headers: headers }))
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
  }

  function callApi(url, options) {
    const fn = (typeof api === 'function') ? api : apiFallback;
    return Promise.resolve(fn(url, options));
  }

  /** 置信度徽标(必须展示,含人话原因) */
  function confBadge(confidence, reason) {
    const cls = { high: 'st6-conf-high', medium: 'st6-conf-medium', low: 'st6-conf-low' }[confidence] || 'st6-conf-low';
    const label = { high: '置信度高', medium: '置信度中', low: '置信度低' }[confidence] || '置信度低';
    return '<span class="st6-conf ' + cls + '" title="' + esc(reason || '') + '">' +
      '<span class="st6-conf-dot"></span>' + label + '</span>';
  }

  // ===== STAGE6: nav-patch ===== 二级 Tab 接入 STAGE5 父子联动
  // 原理:包装全局 switchPage,新子页自行完成"兄弟同时 active"联动,
  // 其余页面透传原实现(存量函数零搬动)。

  function activateStage6Page(name, loader) {
    document.querySelectorAll('.page').forEach(function (p) { p.classList.remove('active'); });
    document.querySelectorAll('.nav-item').forEach(function (n) { n.classList.remove('active'); });
    document.querySelectorAll('.bottom-tab-item').forEach(function (n) { n.classList.remove('active'); });
    const page = document.getElementById('page-' + name);
    const parent = document.getElementById('page-hud-holdings');
    if (page) page.classList.add('active');
    if (parent) parent.classList.add('active');
    document.querySelectorAll('.nav-item[data-page="hud-holdings"]').forEach(function (n) { n.classList.add('active'); });
    document.querySelectorAll('.bottom-tab-item[data-page="hud-holdings"]').forEach(function (n) { n.classList.add('active'); });
    document.querySelectorAll('.hud-subtab').forEach(function (t) { t.classList.remove('active'); });
    const sub = document.querySelector('.hud-subtab[data-page="' + name + '"]');
    if (sub) sub.classList.add('active');
    if (typeof loader === 'function') loader();
  }

  const _origSwitchPage = window.switchPage;
  window.switchPage = function (name) {
    if (name === 'watchboard') { activateStage6Page('watchboard', loadWatchboard); return; }
    if (name === 'risk') { activateStage6Page('risk', loadRiskTab); return; }
    if (typeof _origSwitchPage === 'function') return _origSwitchPage.apply(this, arguments);
  };

  // ===== STAGE6: watchboard ===== 盯盘页

  const wbState = { loaded: false };

  function loadWatchboard(force) {
    const body = document.getElementById('st6WbBody');
    if (!body) return;
    // QA Bug 修复(批次A 低危):推送设置卡 DOM 在 page-watchboard 内,
    // 直接进盯盘页也必须加载;此前只在 loadRiskTab 触发,导致"永远加载中"。
    loadPushSettings();
    renderStatus(body, 'loading');
    callApi('/api/portfolio/watchboard' + (force ? '?force=true' : ''))
      .then(function (data) {
        if (!data) throw new Error('接口无响应');
        wbState.loaded = true;
        renderWatchboard(data);
      })
      .catch(function (e) {
        renderStatus(body, 'error', '盯盘数据加载失败', String(e && e.message || e));
      });
  }

  function renderWatchboard(data) {
    const summary = data.summary || {};
    const items = data.positions || [];
    const big = document.getElementById('st6WbSummaryBig');
    const meta = document.getElementById('st6WbSummaryMeta');
    if (big) {
      big.textContent = (summary.total_est_day_pnl > 0 ? '+' : '') + fmtMoney(summary.total_est_day_pnl) + ' 元';
      big.className = 'st6-summary-big ' + pctCls(summary.total_est_day_pnl);
    }
    if (meta) {
      const counts = summary.confidence_counts || {};
      const freshness = Freshness.html({
        asOf: (data.generated_at || '').slice(11, 16),
        estimated: true,
        note: data.degraded ? ('降级:' + (data.degraded_reason || '数据源异常')) : ''
      });
      meta.innerHTML =
        '<span>持仓 ' + (summary.count || 0) + ' 只</span>' +
        '<span>置信度高 ' + (counts.high || 0) + ' / 中 ' + (counts.medium || 0) + ' / 低 ' + (counts.low || 0) + '</span>' +
        freshness +
        '<span style="color:#f59e0b;">估算≠净值,以官方净值为准</span>';
    }
    const body = document.getElementById('st6WbBody');
    if (!body) return;
    if (!items.length) {
      renderStatus(body, 'empty', '暂无持仓', '请先在「基金管理」添加基金持仓');
      return;
    }
    body.innerHTML = '<div class="st6-estimate-list">' + items.map(function (it) {
      const stale = it.stale ? '<span class="st6-stale-mark">数据滞后</span>' : '';
      const estPct = it.est_pct == null ? '—' : fmtPct(it.est_pct);
      const estNav = it.est_nav == null ? '—' : Number(it.est_nav).toFixed(4);
      return '<div class="st6-estimate-card">' +
        '<div class="st6-estimate-top">' +
          '<span><span class="st6-estimate-name">' + esc(it.fund_name || it.fund_code) + '</span>' +
          '<span class="st6-estimate-code">' + esc(it.fund_code) + '</span></span>' +
          '<span class="st6-estimate-pct ' + pctCls(it.est_pct) + '">' + estPct + '</span>' +
        '</div>' +
        '<div class="st6-estimate-grid">' +
          '<span class="st6-kv"><span class="st6-k">估算净值</span><span class="st6-v">' + esc(estNav) + '</span></span>' +
          '<span class="st6-kv"><span class="st6-k">官方净值</span><span class="st6-v">' + (it.nav ? Number(it.nav).toFixed(4) : '—') + (it.nav_date ? ' (' + esc(it.nav_date) + ')' : '') + '</span></span>' +
          '<span class="st6-kv"><span class="st6-k">估算当日盈亏</span><span class="st6-v ' + pctCls(it.est_day_pnl) + '">' + (it.est_day_pnl > 0 ? '+' : '') + fmtMoney(it.est_day_pnl) + '</span></span>' +
          '<span class="st6-kv"><span class="st6-k">数据来源</span><span class="st6-v">' + esc(it.source || 'none') + '</span></span>' +
        '</div>' +
        '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;">' +
          confBadge(it.confidence, it.confidence_reason) + stale +
        '</div>' +
      '</div>';
    }).join('') + '</div>';
  }

  // ===== STAGE6: webhook-config ===== 推送设置(盯盘页内卡片)

  // 盯盘页与风险页都会触发加载:复用同一 Promise + loaded 标记,避免来回切页重复请求
  const pushSettingsState = { loaded: false, promise: null };

  function loadPushSettings(force) {
    if (force) pushSettingsState.loaded = false;
    if (pushSettingsState.loaded) return Promise.resolve();
    if (pushSettingsState.promise) return pushSettingsState.promise;
    pushSettingsState.promise = callApi('/api/portfolio/webhook/config')
      .then(function (data) {
        if (!data || !data.config) return;
        const cfg = data.config;
        const box = document.getElementById('st6PushBody');
        if (!box) return;
        box.innerHTML =
          '<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;">' +
            '<label style="display:flex;align-items:center;gap:6px;font-size:13px;white-space:nowrap;flex-shrink:0;">' +
              '<input type="checkbox" id="st6PushEnabled" ' + (cfg.enabled ? 'checked' : '') + ' style="width:18px;height:18px;"> 启用推送' +
            '</label>' +
            '<label style="display:flex;align-items:center;gap:6px;font-size:13px;white-space:nowrap;flex-shrink:0;">阈值 ±' +
              '<input class="input" type="number" id="st6PushThreshold" min="0.1" max="100" step="0.5" value="' + esc(cfg.thresholds && cfg.thresholds.est_pct_abs != null ? cfg.thresholds.est_pct_abs : 2.0) + '" style="width:80px;">%' +
            '</label>' +
            '<span style="font-size:12px;color:var(--text-muted,#8b92a8);">渠道: ' + esc(cfg.channel) +
              (cfg.sendkey_configured ? ' · 凭据已配置' : ' · <span style="color:#f59e0b;">凭据未配置(env PUSH_SERVERCHAN_SENDKEY)</span>') + '</span>' +
          '</div>' +
          '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">' +
            '<button class="btn btn-sm btn-primary" id="st6PushSaveBtn" onclick="St6Portfolio.savePushConfig()">保存配置</button>' +
            '<button class="btn btn-sm" id="st6PushTestBtn" onclick="St6Portfolio.testPush()">发送测试推送</button>' +
          '</div>';
        pushSettingsState.loaded = true;
      })
      .catch(function () { /* 静默:设置卡片非关键路径 */ })
      .finally(function () { pushSettingsState.promise = null; });
    return pushSettingsState.promise;
  }

  /** 阈值校验:有限数字且 0 < v <= 100,非法返回 null(后端同规则返回 422) */
  function parseThreshold(raw) {
    const v = Number(String(raw == null ? '' : raw).trim());
    if (!isFinite(v) || v <= 0 || v > 100) return null;
    return v;
  }

  function savePushConfig() {
    const enabled = !!(document.getElementById('st6PushEnabled') || {}).checked;
    const thRaw = (document.getElementById('st6PushThreshold') || {}).value;
    const threshold = parseThreshold(thRaw);
    if (threshold == null) {
      if (typeof showToast === 'function') showToast('阈值需为大于 0 且不超过 100 的数字', 'error');
      return;
    }
    callApi('/api/portfolio/webhook/config', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: enabled,
        thresholds: { est_pct_abs: threshold },
      }),
    }).then(function (data) {
      if (data && data.success) { if (typeof showToast === 'function') showToast('推送配置已保存', 'success'); }
      else if (typeof showToast === 'function') showToast((data && data.message) || '保存失败', 'error');
    }).catch(function (e) {
      if (typeof showToast === 'function') showToast('保存失败: ' + (e && e.message || e), 'error');
    });
  }

  function testPush() {
    const btn = document.getElementById('st6PushTestBtn');
    if (btn) { btn.disabled = true; btn.textContent = '发送中...'; }
    callApi('/api/portfolio/webhook/test', { method: 'POST', body: JSON.stringify({}) })
      .then(function (data) {
        if (typeof showToast === 'function') {
          showToast(data && data.success ? '测试推送已发送,请到 Server酱查看' : ('推送失败: ' + (data && (data.error || data.message))),
            data && data.success ? 'success' : 'error');
        }
      })
      .catch(function (e) {
        if (typeof showToast === 'function') showToast('发送失败: ' + (e && e.message || e), 'error');
      })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = '发送测试推送'; }
      });
  }

  // ===== STAGE6: risk-panel ===== 风险页(指标 + X-Ray + 账本)

  const riskState = { window: '3m', loaded: false };

  function loadRiskTab() {
    if (!riskState.loaded) {
      loadRisk('3m');
      loadPushSettings();
    }
    // 归因卡在风险页内,首次进入即加载(与风险窗口独立)
    if (!attrState.loaded) loadAttribution(attrState.window);
    loadRebalanceTab();
  }

  function loadRisk(window) {
    riskState.window = window || riskState.window || '3m';
    const body = document.getElementById('st6RiskBody');
    if (!body) return;
    renderStatus(body, 'loading');
    // 窗口 Tab 高亮
    document.querySelectorAll('.st6-window-tab[data-window]').forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-window') === riskState.window);
    });
    callApi('/api/portfolio/risk?window=' + encodeURIComponent(riskState.window))
      .then(function (data) {
        if (!data) throw new Error('接口无响应');
        riskState.loaded = true;
        renderRisk(data);
      })
      .catch(function (e) {
        renderStatus(body, 'error', '风险指标加载失败', String(e && e.message || e));
      });
  }

  function metricCard(label, value, hint, cls) {
    return '<div class="st6-metric-card">' +
      '<div class="st6-metric-label">' + esc(label) + '</div>' +
      '<div class="st6-metric-value ' + (cls || '') + '">' + esc(value) + '</div>' +
      (hint ? '<div class="st6-metric-hint">' + esc(hint) + '</div>' : '') +
      '</div>';
  }

  function fmtMetric(v, mode) {
    if (v == null) return '样本不足';
    if (mode === 'ratio') return pctRatio(v);
    if (mode === 'num') return String(v);
    return fmtMoney(v);
  }

  function renderRisk(data) {
    const p = data.portfolio || {};
    const summary = document.getElementById('st6RiskSummary');
    if (summary) {
      // 每指标一句话解读(MDD>30% 标红"深回撤品种")
      const mddDeep = p.max_drawdown != null && p.max_drawdown > 0.30;
      summary.innerHTML =
        metricCard('区间收益(' + esc(data.window) + ')', fmtMetric(p.total_return, 'ratio')) +
        metricCard('年化收益', fmtMetric(p.annual_return, 'ratio')) +
        metricCard('最大回撤', fmtMetric(p.max_drawdown, 'ratio'), mddDeep ? '深回撤品种' : '回撤可控', mddDeep ? 'st6-risk-deep' : '') +
        metricCard('年化波动', fmtMetric(p.annual_vol, 'ratio')) +
        metricCard('夏普比率', fmtMetric(p.sharpe, 'num'), 'rf=' + (data.rf != null ? (data.rf * 100).toFixed(1) : '2.0') + '%') +
        metricCard('Sortino', fmtMetric(p.sortino, 'num')) +
        metricCard('卡玛比率', fmtMetric(p.calmar, 'num')) +
        metricCard('样本数', String(p.nav_points != null ? p.nav_points : 0), '交易日');
    }
    const body = document.getElementById('st6RiskBody');
    if (!body) return;
    const funds = data.funds || [];
    if (!funds.length) {
      renderStatus(body, 'empty', '暂无持仓', '添加持仓后可查看单基金风险指标');
      return;
    }
    const rows = funds.map(function (f) {
      return '<tr>' +
        '<td>' + esc(f.fund_code) + '</td>' +
        '<td>' + esc(f.fund_name || '') + '</td>' +
        '<td class="' + (f.max_drawdown != null && f.max_drawdown > 0.3 ? 'st6-risk-deep' : '') + '">' + fmtMetric(f.max_drawdown, 'ratio') + '</td>' +
        '<td>' + fmtMetric(f.annual_vol, 'ratio') + '</td>' +
        '<td>' + fmtMetric(f.sharpe, 'num') + '</td>' +
        '<td>' + fmtMetric(f.sortino, 'num') + '</td>' +
        '<td>' + fmtMetric(f.calmar, 'num') + '</td>' +
        '<td>' + (f.nav_points || 0) + '</td>' +
      '</tr>';
    }).join('');
    body.innerHTML =
      '<div class="table-wrap"><table>' +
      '<thead><tr><th>代码</th><th>名称</th><th>最大回撤</th><th>年化波动</th><th>夏普</th><th>Sortino</th><th>卡玛</th><th>样本</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>' +
      '<div class="st6-note">' + esc(data.note || '') +
      (data.degraded ? ' · <span style="color:#f59e0b;">部分基金历史数据缺失,已标注降级</span>' : '') +
      ' · 口径: rf=2.0%、年化 244 交易日、当前份额静态合成</div>';
  }

  // ===== STAGE6: xray-report ===== 持仓重叠 X-Ray

  function loadXray() {
    const body = document.getElementById('st6XrayBody');
    if (!body) return;
    renderStatus(body, 'loading');
    callApi('/api/portfolio/xray')
      .then(function (data) {
        if (!data) throw new Error('接口无响应');
        renderXray(data);
      })
      .catch(function (e) {
        renderStatus(body, 'error', 'X-Ray 加载失败', String(e && e.message || e));
      });
  }

  function heatClass(overlap) {
    if (overlap == null) return 'st6-cell-heat-0';
    if (overlap > 0.20) return 'st6-cell-heat-3';
    if (overlap > 0.10) return 'st6-cell-heat-2';
    if (overlap > 0.03) return 'st6-cell-heat-1';
    return 'st6-cell-heat-0';
  }

  function renderXray(data) {
    const body = document.getElementById('st6XrayBody');
    if (!body) return;
    if (data.status === 'insufficient_data') {
      // 数据可得性降级:显式"数据不足",不显示空表
      renderStatus(body, 'empty', '数据不足', data.message || '披露持仓不可得,请稍后重试');
      return;
    }
    const funds = data.funds || [];
    const matrix = data.overlap_matrix || [];
    // 桌面矩阵;移动端 CSS 横向滚动兜底
    let html = '';
    if (data.degraded && (data.missing_funds || []).length) {
      html += '<div class="st6-note" style="color:#f59e0b;">以下基金披露持仓缺失(已从矩阵剔除): ' +
        esc((data.missing_funds || []).join(', ')) + '</div>';
    }
    (data.concentration_warnings || []).forEach(function (w) {
      html += '<div class="st6-xray-warn">⚠ ' + esc(w) + '</div>';
    });
    if (funds.length >= 2) {
      html += '<div class="st6-xray-table-wrap"><table class="st6-xray-table"><thead><tr><th></th>' +
        funds.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>';
      funds.forEach(function (r, i) {
        html += '<tr><td><b>' + esc(r) + '</b></td>' + matrix[i].map(function (v, j) {
          return i === j
            ? '<td class="st6-cell-heat-0" style="color:var(--text-muted,#8b92a8);">—</td>'
            : '<td class="' + heatClass(v) + '">' + pctRatio(v, 1) + '</td>';
        }).join('') + '</tr>';
      });
      html += '</tbody></table></div>';
    }
    const exposure = (data.stock_exposure || []).slice(0, 5);
    if (exposure.length) {
      html += '<div class="st6-diff-title">单股跨基金暴露 Top5</div><div class="st6-xray-table-wrap"><table class="st6-xray-table">' +
        '<thead><tr><th>个股</th><th>持有基金数</th><th>合计隐含权重</th><th>明细</th></tr></thead><tbody>' +
        exposure.map(function (e) {
          return '<tr><td>' + esc(e.stock_name || e.stock_code) + '</td>' +
            '<td>' + e.fund_count + '</td>' +
            '<td class="' + (e.total_weight > 0.15 ? 'st6-risk-deep' : '') + '">' + pctRatio(e.total_weight, 1) + '</td>' +
            '<td>' + esc((e.funds || []).map(function (f) { return f.fund_code + '(' + pctRatio(f.weight, 1) + ')'; }).join(' ')) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }
    html += '<div class="st6-note">数据截止: ' +
      esc(Object.keys(data.data_as_of || {}).map(function (c) { return c + ' ' + data.data_as_of[c]; }).join(' / ') || '—') +
      ' · 重叠度 = min-weight(共同重仓合计权重取小者之和) · 季报披露有 ~15 个工作日滞后</div>';
    body.innerHTML = html;
  }

  // ===== STAGE6: ledger-io ===== 账本导入导出

  function downloadExport() {
    const token = localStorage.getItem('admin_token') || '';
    fetch('/api/portfolio/export', { headers: token ? { Authorization: 'Bearer ' + token } : {} })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'quantflow_ledger_' + (data.exported_at || '').replace(/[: ]/g, '-').slice(0, 16) + '.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
        if (typeof showToast === 'function') showToast('账本已导出', 'success');
      })
      .catch(function (e) {
        if (typeof showToast === 'function') showToast('导出失败: ' + (e && e.message || e), 'error');
      });
  }

  const ledgerState = { payload: null, diff: null };

  function ledgerFileChosen(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function () {
      try {
        ledgerState.payload = JSON.parse(String(reader.result));
        ledgerState.diff = null;
        ledgerPreview();
      } catch (e) {
        if (typeof showToast === 'function') showToast('文件不是合法 JSON: ' + e.message, 'error');
      }
    };
    reader.readAsText(file, 'utf-8');
  }

  function ledgerPreview() {
    if (!ledgerState.payload) {
      if (typeof showToast === 'function') showToast('请先选择导出的账本 JSON 文件', 'error');
      return;
    }
    const body = document.getElementById('st6LedgerDiff');
    if (body) renderStatus(body, 'loading');
    callApi('/api/portfolio/import', {
      method: 'POST',
      body: JSON.stringify({ payload: ledgerState.payload, dry_run: true }),
    }).then(function (data) {
      if (!data) throw new Error('接口无响应');
      if (!data.success) {
        if (body) body.innerHTML = '<div class="st6-xray-warn">校验失败(整包拒绝): ' +
          esc((data.errors || []).join('; ')) + '</div>';
        return;
      }
      ledgerState.diff = data.diff;
      renderLedgerDiff(data.diff, data.message);
    }).catch(function (e) {
      if (typeof showToast === 'function') showToast('预览失败: ' + (e && e.message || e), 'error');
    });
  }

  function renderLedgerDiff(diff, message) {
    const body = document.getElementById('st6LedgerDiff');
    if (!body) return;
    let html = '<div class="st6-diff-block">';
    html += '<div class="st6-diff-title">导入影响预览(尚未写入)</div>';
    const fp = diff && diff.fund_positions;
    if (fp) {
      html += '<div class="st6-diff-item">基金持仓: 新增 ' + (fp.added || []).length +
        ' · 变更 ' + (fp.updated || []).length + ' · 删除 ' + (fp.deleted || []).length + '</div>';
      (fp.added || []).forEach(function (p) {
        html += '<div class="st6-diff-item" style="color:var(--hud-up,#F87171);">+ 新增 ' + esc(p.fund_code) + ' ' + esc(p.fund_name || '') + '</div>';
      });
      (fp.updated || []).forEach(function (p) {
        html += '<div class="st6-diff-item">~ 变更 ' + esc(p.fund_code) + ' ' + Object.keys(p.changes || {}).map(function (k) {
          return k + ': ' + esc(p.changes[k].old) + ' → ' + esc(p.changes[k].new);
        }).join(', ') + '</div>';
      });
      (fp.deleted || []).forEach(function (p) {
        html += '<div class="st6-diff-item" style="color:var(--hud-down,#34D399);">- 删除 ' + esc(p.fund_code) + ' ' + esc(p.fund_name || '') + '</div>';
      });
    }
    if (diff && diff.portfolio_config) {
      const pc = diff.portfolio_config;
      if (pc.enabled) {
        html += '<div class="st6-diff-item">推送配置: enabled ' + String(pc.enabled.current) +
          ' → ' + String(pc.enabled.incoming) + '</div>';
      }
      if (pc.groups) {
        html += '<div class="st6-diff-item">持仓分组: ' + pc.groups.current_groups + ' → ' +
          pc.groups.incoming_groups + ' 组 · 归组映射 ' + pc.groups.current_mappings + ' → ' +
          pc.groups.incoming_mappings + ' 条</div>';
      }
    }
    html += '<div class="st6-note">' + esc(message || '') + '</div>';
    html += '<div class="st6-confirm-row">' +
      '<input type="checkbox" id="st6ImportConfirm"><label for="st6ImportConfirm" style="font-size:12px;">我已逐项核对上方 diff,确认覆盖现有账本</label>' +
      '<button class="btn btn-sm" onclick="St6Portfolio.ledgerExecute()">执行导入</button>' +
      '</div></div>';
    body.innerHTML = html;
  }

  function ledgerExecute() {
    const checkbox = document.getElementById('st6ImportConfirm');
    if (!checkbox || !checkbox.checked) {
      if (typeof showToast === 'function') showToast('请先勾选确认,再执行导入', 'error');
      return;
    }
    callApi('/api/portfolio/import', {
      method: 'POST',
      body: JSON.stringify({ payload: ledgerState.payload, dry_run: false, confirm: true }),
    }).then(function (data) {
      if (data && data.success) {
        if (typeof showToast === 'function') showToast('导入完成: ' + (data.message || ''), 'success');
        const body = document.getElementById('st6LedgerDiff');
        if (body) body.innerHTML = '<div class="st6-note">导入完成。备份目录: ' + esc(data.backup_dir || '(无需备份)') + '</div>';
      } else {
        if (typeof showToast === 'function') showToast('导入失败: ' + ((data && (data.errors || [data.message]).join('; ')) || ''), 'error');
      }
    }).catch(function (e) {
      if (typeof showToast === 'function') showToast('导入失败: ' + (e && e.message || e), 'error');
    });
  }

  // ===== STAGE6: groups ===== 持仓分组(批次 B 功能 #8)
  // 设计:表格行/卡片仍由 index.html 原渲染器生成,本模块只**重排 DOM +
  // 插入分组头/小计**,不改写原渲染逻辑(存量零搬动;接口失败静默平铺)。
  // 归组真相在 data/portfolio/groups.json;后端 effective 对未归组持仓兜底 default。

  const groupsState = {
    config: null, effective: {}, positions: [], loaded: false,
    filter: '__all__', collapsed: null, draft: null, draftMap: null,
  };

  function _readCollapsed() {
    if (!groupsState.collapsed) {
      let ids = [];
      try { ids = JSON.parse(localStorage.getItem('st6_group_collapsed') || '[]'); } catch (e) { ids = []; }
      groupsState.collapsed = {};
      (Array.isArray(ids) ? ids : []).forEach(function (id) { groupsState.collapsed[id] = true; });
    }
    return groupsState.collapsed;
  }

  function _persistCollapsed() {
    try {
      const ids = Object.keys(groupsState.collapsed || {}).filter(function (k) { return groupsState.collapsed[k]; });
      localStorage.setItem('st6_group_collapsed', JSON.stringify(ids));
    } catch (e) { /* 隐私模式:仅内存态 */ }
  }

  function _isCollapsed(gid) { return !!_readCollapsed()[gid]; }

  function _groups() {
    const cfg = groupsState.config || {};
    const list = Array.isArray(cfg.groups) && cfg.groups.length
      ? cfg.groups : [{ id: 'default', name: '我的持仓' }];
    return list.map(function (g) {
      return { id: String(g.id), name: String(g.name || g.id) };
    });
  }

  function _groupOf(code) {
    const gid = String(groupsState.effective[String(code)] || 'default');
    const exists = _groups().some(function (g) { return g.id === gid; });
    return exists ? gid : 'default';
  }

  /** 分组小计:市值 + 当日盈亏(口径与卡片一致:市值−市值/(1+涨幅)) */
  function _subtotal(list) {
    let mv = 0, day = 0;
    list.forEach(function (p) {
      const m = Number(p.market_value) || 0;
      const chg = Number(p.change_pct) || 0;
      mv += m;
      if (p.current_nav > 0 && chg !== 0) day += m - m / (1 + chg / 100);
    });
    return { mv: mv, day: day, count: list.length };
  }

  /** 行显隐:内联 display 保存/恢复(走势图行自身也是 display:none,不可覆盖) */
  function _setRowHidden(row, hide) {
    if (hide) {
      if (!row.hasAttribute('data-st6-prev-display')) {
        row.setAttribute('data-st6-prev-display', row.style.display || '');
      }
      row.style.display = 'none';
    } else if (row.hasAttribute('data-st6-prev-display')) {
      row.style.display = row.getAttribute('data-st6-prev-display');
      row.removeAttribute('data-st6-prev-display');
    }
  }

  function _headInnerHtml(gid, name, list, totals) {
    const collapsed = _isCollapsed(gid);
    return '<span class="st6-group-caret" aria-hidden="true">' + (collapsed ? '▸' : '▾') + '</span>' +
      '<span class="st6-group-name">' + esc(name) + '</span>' +
      '<span class="st6-group-count">' + list.length + ' 只</span>' +
      '<span class="st6-group-sub">市值 ' + fmtMoney(totals.mv) + ' · 当日 ' +
        '<span class="' + pctCls(totals.day) + '">' + (totals.day > 0 ? '+' : '') + fmtMoney(totals.day) + '</span></span>';
  }

  function _regroupTable() {
    const body = document.getElementById('fundPositionBody');
    if (!body) return;
    body.querySelectorAll('tr.st6-group-row').forEach(function (n) { n.remove(); });
    const rowMap = {}, chartMap = {};
    body.querySelectorAll('tr[data-fund-code]').forEach(function (n) {
      rowMap[n.getAttribute('data-fund-code')] = n;
    });
    body.querySelectorAll('tr[data-chart-for]').forEach(function (n) {
      chartMap[n.getAttribute('data-chart-for')] = n;
    });
    if (!Object.keys(rowMap).length) return;

    const buckets = {};
    _groups().forEach(function (g) { buckets[g.id] = { list: [], rows: [] }; });
    (groupsState.positions || []).forEach(function (p) {
      const code = String(p.fund_code);
      const gid = _groupOf(code);
      if (!buckets[gid]) return;
      buckets[gid].list.push(p);
      if (rowMap[code]) {
        buckets[gid].rows.push(rowMap[code]);
        delete rowMap[code];
      }
      if (chartMap[code]) {
        buckets[gid].rows.push(chartMap[code]);
        delete chartMap[code];
      }
    });
    // 理论不可达:持仓接口与行渲染同源;残留行归 default,绝不丢行
    const leftovers = Object.keys(rowMap).map(function (k) { return rowMap[k]; })
      .concat(Object.keys(chartMap).map(function (k) { return chartMap[k]; }));

    const frag = document.createDocumentFragment();
    _groups().forEach(function (g) {
      const b = buckets[g.id];
      if (!b.list.length) return;
      const head = document.createElement('tr');
      head.className = 'st6-group-row';
      head.setAttribute('data-group-id', g.id);
      const td = document.createElement('td');
      td.colSpan = 8;
      td.innerHTML = '<div class="st6-group-head" role="button" tabindex="0" aria-expanded="' +
        (!_isCollapsed(g.id)) + '" onclick="St6Groups.toggleGroup(\'' + g.id + '\')" ' +
        'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();St6Groups.toggleGroup(\'' + g.id + '\');}">' +
        _headInnerHtml(g.id, g.name, b.list, _subtotal(b.list)) + '</div>';
      head.appendChild(td);
      frag.appendChild(head);
      b.rows.forEach(function (r) {
        r.setAttribute('data-group-id', g.id);
        frag.appendChild(r);
      });
    });
    if (leftovers.length) {
      leftovers.forEach(function (r) {
        r.setAttribute('data-group-id', 'default');
        frag.appendChild(r);
      });
    }
    body.appendChild(frag);
  }

  function _regroupCards() {
    const cardsEl = document.getElementById('fundPositionCards');
    if (!cardsEl) return;
    const cards = Array.prototype.slice.call(cardsEl.querySelectorAll('.fund-card[data-fund-code]'));
    if (!cards.length) return;
    const cardMap = {};
    cards.forEach(function (c) { cardMap[c.getAttribute('data-fund-code')] = c; });
    cardsEl.innerHTML = '';

    const frag = document.createDocumentFragment();
    _groups().forEach(function (g) {
      const list = (groupsState.positions || []).filter(function (p) {
        return _groupOf(p.fund_code) === g.id;
      });
      if (!list.length) return;
      const block = document.createElement('section');
      block.className = 'st6-group-block';
      block.setAttribute('data-group-id', g.id);
      block.innerHTML = '<div class="st6-group-head st6-group-head-card" role="button" tabindex="0" aria-expanded="' +
        (!_isCollapsed(g.id)) + '" onclick="St6Groups.toggleGroup(\'' + g.id + '\')" ' +
        'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();St6Groups.toggleGroup(\'' + g.id + '\');}">' +
        _headInnerHtml(g.id, g.name, list, _subtotal(list)) + '</div>' +
        '<div class="st6-group-cards"></div>';
      const holder = block.querySelector('.st6-group-cards');
      list.forEach(function (p) {
        const card = cardMap[String(p.fund_code)];
        if (card) holder.appendChild(card);
      });
      frag.appendChild(block);
    });
    cardsEl.appendChild(frag);
  }

  function _applyVisibility() {
    const collapsed = _readCollapsed();
    const filter = groupsState.filter || '__all__';
    _groups().forEach(function (g) {
      const visible = filter === '__all__' || filter === g.id;
      const hideRows = !visible || !!collapsed[g.id];
      document.querySelectorAll('#fundPositionBody tr.st6-group-row[data-group-id="' + g.id + '"]')
        .forEach(function (n) { _setRowHidden(n, !visible); });
      document.querySelectorAll('#fundPositionBody tr[data-group-id="' + g.id + '"]:not(.st6-group-row)')
        .forEach(function (n) { _setRowHidden(n, hideRows); });
      document.querySelectorAll('#fundPositionCards .st6-group-block[data-group-id="' + g.id + '"]')
        .forEach(function (b) {
          b.style.display = visible ? '' : 'none';
          const holder = b.querySelector('.st6-group-cards');
          if (holder) holder.style.display = collapsed[g.id] ? 'none' : '';
        });
    });
  }

  function _regroup() {
    _regroupTable();
    _regroupCards();
    _applyVisibility();
    renderGroupBar();
  }

  function renderGroupBar() {
    const bar = document.getElementById('st6GroupBar');
    if (!bar) return;
    const list = _groups();
    const counts = {};
    list.forEach(function (g) { counts[g.id] = 0; });
    (groupsState.positions || []).forEach(function (p) {
      const gid = _groupOf(p.fund_code);
      counts[gid] = (counts[gid] || 0) + 1;
    });
    const chip = function (id, label, count, active) {
      return '<span class="st6-group-chip' + (active ? ' active' : '') + '" role="button" tabindex="0" ' +
        'onclick="St6Groups.setFilter(\'' + id + '\')" ' +
        'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();St6Groups.setFilter(\'' + id + '\');}">' +
        esc(label) + ' <b>' + count + '</b></span>';
    };
    let html = chip('__all__', '全部', (groupsState.positions || []).length, groupsState.filter === '__all__');
    list.forEach(function (g) {
      html += chip(g.id, g.name, counts[g.id] || 0, groupsState.filter === g.id);
    });
    html += '<button class="btn btn-sm st6-group-manage-btn" onclick="St6Groups.toggleManage()">分组管理</button>';
    bar.innerHTML = html;
  }

  function setFilter(id) {
    groupsState.filter = id || '__all__';
    _regroup();
  }

  function toggleGroup(gid) {
    const c = _readCollapsed();
    if (c[gid]) delete c[gid]; else c[gid] = true;
    _persistCollapsed();
    _regroup();
  }

  function loadGroups(force) {
    if (groupsState.loaded && !force) return Promise.resolve(groupsState.config);
    return callApi('/api/portfolio/groups')
      .then(function (data) {
        if (data && Array.isArray(data.groups)) {
          groupsState.config = data;
          groupsState.effective = data.effective || {};
          groupsState.loaded = true;
          renderGroupBar();
          _applyVisibility();
        }
        return groupsState.config;
      })
      .catch(function () {
        // 接口不可用:全员归默认组,持仓展示不受影响
        renderGroupBar();
        return null;
      });
  }

  /** loadFundData 渲染完成后调用:按分组重排 + 小计(幂等,可重复调用) */
  function applyGroups(positions) {
    groupsState.positions = Array.isArray(positions) ? positions : [];
    if (!groupsState.loaded) {
      loadGroups().then(function () { _regroup(); });
    }
    _regroup();
  }

  // --- 分组管理面板 ---

  function _manageOpen() {
    const box = document.getElementById('st6GroupManage');
    return !!box && !box.hasAttribute('hidden');
  }

  function toggleManage(force) {
    const box = document.getElementById('st6GroupManage');
    if (!box) return;
    const open = typeof force === 'boolean' ? force : box.hasAttribute('hidden');
    if (!open) {
      box.setAttribute('hidden', '');
      box.innerHTML = '';
      groupsState.draft = null;
      groupsState.draftMap = null;
      return;
    }
    groupsState.draft = _groups().map(function (g) { return { id: g.id, name: g.name }; });
    const cfgMap = (groupsState.config && groupsState.config.fund_groups) || {};
    groupsState.draftMap = Object.assign({}, cfgMap, groupsState.effective || {});
    box.removeAttribute('hidden');
    renderManagePanel();
  }

  function renderManagePanel() {
    const box = document.getElementById('st6GroupManage');
    if (!box || !groupsState.draft) return;
    const draft = groupsState.draft;
    let html = '<div class="st6-group-manage-title">分组设置 <span>保存后立即生效;未归组的旧持仓自动进入默认分组</span></div>';
    html += '<div class="st6-group-manage-list">';
    draft.forEach(function (g, i) {
      html += '<div class="st6-group-manage-row">' +
        '<span class="st6-group-manage-id">#' + esc(g.id) + '</span>' +
        '<input class="input st6-group-name-input" maxlength="20" value="' + esc(g.name) +
          '" data-group-index="' + i + '" oninput="St6Groups.renameGroup(' + i + ', this.value)" aria-label="分组名称">' +
        (g.id === 'default'
          ? '<span class="st6-group-lock">默认组不可删</span>'
          : '<button class="btn btn-sm" onclick="St6Groups.deleteGroup(' + i + ')">删除</button>') +
        '</div>';
    });
    html += '</div><div class="st6-group-add-row">' +
      '<input class="input" id="st6NewGroupName" maxlength="20" placeholder="新分组名称(≤20 字)">' +
      '<button class="btn btn-sm" onclick="St6Groups.addGroup()">添加分组</button></div>';

    html += '<div class="st6-group-manage-title" style="margin-top:12px;">持仓归组</div>';
    const positions = groupsState.positions || [];
    if (!positions.length) {
      html += '<div class="st6-note">暂无持仓;添加持仓后可在此归组。</div>';
    } else {
      html += '<div class="st6-group-assign-list">';
      positions.forEach(function (p) {
        const code = String(p.fund_code);
        const cur = groupsState.draftMap[code] || 'default';
        html += '<div class="st6-group-assign-row">' +
          '<span class="st6-group-assign-name">' + esc(p.fund_name || code) +
            ' <span class="st6-group-count">' + esc(code) + '</span></span>' +
          '<select class="input st6-group-select" aria-label="' + esc(code) + ' 所属分组" ' +
            'onchange="St6Groups.assignFund(\'' + esc(code) + '\', this.value)">' +
            draft.map(function (g) {
              return '<option value="' + esc(g.id) + '"' + (g.id === cur ? ' selected' : '') + '>' + esc(g.name) + '</option>';
            }).join('') +
          '</select></div>';
      });
      html += '</div>';
    }
    html += '<div class="st6-group-manage-actions">' +
      '<button class="btn btn-sm btn-primary" onclick="St6Groups.saveGroups()">保存分组</button>' +
      '<button class="btn btn-sm" onclick="St6Groups.toggleManage(false)">取消</button></div>';
    box.innerHTML = html;
  }

  function renameGroup(index, value) {
    if (!groupsState.draft || !groupsState.draft[index]) return;
    groupsState.draft[index].name = String(value || '');
  }

  function addGroup() {
    if (!groupsState.draft) return;
    const input = document.getElementById('st6NewGroupName');
    const name = String((input && input.value) || '').trim();
    if (!name) {
      if (typeof showToast === 'function') showToast('请先填写分组名称', 'error');
      return;
    }
    let id = 'g' + Date.now().toString(36);
    while (groupsState.draft.some(function (g) { return g.id === id; })) id += 'x';
    groupsState.draft.push({ id: id, name: name.slice(0, 20) });
    renderManagePanel();
  }

  function deleteGroup(index) {
    if (!groupsState.draft) return;
    const g = groupsState.draft[index];
    if (!g || g.id === 'default') return;
    groupsState.draft.splice(index, 1);
    Object.keys(groupsState.draftMap || {}).forEach(function (code) {
      if (groupsState.draftMap[code] === g.id) delete groupsState.draftMap[code];
    });
    renderManagePanel();
  }

  function assignFund(code, gid) {
    if (!groupsState.draftMap) return;
    groupsState.draftMap[String(code)] = String(gid);
  }

  function saveGroups() {
    if (!groupsState.draft) return;
    const names = groupsState.draft.map(function (g) { return String(g.name || '').trim(); });
    if (names.some(function (n) { return !n || n.length > 20; })) {
      if (typeof showToast === 'function') showToast('分组名称需为 1-20 字符', 'error');
      return;
    }
    const groups = groupsState.draft.map(function (g, i) { return { id: g.id, name: names[i] }; });
    callApi('/api/portfolio/groups', {
      method: 'PUT',
      body: JSON.stringify({ groups: groups, fund_groups: groupsState.draftMap || {} }),
    }).then(function (data) {
      if (!data || !data.success) {
        if (typeof showToast === 'function') {
          showToast('分组保存失败: ' + ((data && (data.errors || [data.message])) || []).join('; '), 'error');
        }
        return;
      }
      groupsState.config = data;
      groupsState.effective = data.effective || {};
      groupsState.loaded = true;
      groupsState.filter = '__all__';
      toggleManage(false);
      _regroup();
      if (typeof showToast === 'function') showToast('分组配置已保存', 'success');
    }).catch(function (e) {
      if (typeof showToast === 'function') showToast('分组保存失败: ' + (e && e.message || e), 'error');
    });
  }

  // ===== STAGE6: attribution ===== 收益归因(批次 B 功能 #5)

  const attrState = { window: 'mtd', loaded: false };

  function loadAttribution(window) {
    attrState.window = window || attrState.window || 'mtd';
    const body = document.getElementById('st6AttrBody');
    if (!body) return;
    renderStatus(body, 'loading');
    document.querySelectorAll('.st6-window-tab[data-attr-window]').forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-attr-window') === attrState.window);
    });
    callApi('/api/portfolio/attribution?window=' + encodeURIComponent(attrState.window))
      .then(function (data) {
        if (!data) throw new Error('接口无响应');
        attrState.loaded = true;
        renderAttribution(data);
      })
      .catch(function (e) {
        renderStatus(body, 'error', '收益归因加载失败', String(e && e.message || e));
      });
  }

  function renderAttribution(data) {
    const p = data.portfolio || {};
    const summaryEl = document.getElementById('st6AttrSummary');
    if (summaryEl) {
      if (!p.start_value) {
        summaryEl.innerHTML = '';
      } else {
        summaryEl.innerHTML =
          metricCard('区间盈亏', (p.pnl > 0 ? '+' : '') + fmtMoney(p.pnl) + ' 元',
            (data.window_start || '—') + ' → ' + (data.window_end || '—'), pctCls(p.pnl)) +
          metricCard('区间收益率', p.total_return == null ? '样本不足' : pctRatio(p.total_return),
            '期末市值 ' + fmtMoney(p.end_value), pctCls(p.total_return)) +
          metricCard('期初市值', fmtMoney(p.start_value), '按当前份额静态回推', '') +
          metricCard('归因基金数', String(p.fund_count || 0) + ' 只', '静态份额法', '');
      }
    }
    const body = document.getElementById('st6AttrBody');
    if (!body) return;
    const funds = data.funds || [];
    if (!funds.length) {
      renderStatus(body, 'empty', '暂无归因数据', data.note || '持仓或历史净值不足,请稍后重试');
      return;
    }
    const maxAbs = funds.reduce(function (m, f) {
      return Math.max(m, Math.abs(Number(f.contribution) || 0));
    }, 0) || 1;
    const totalPnl = Number(p.pnl) || 0;
    let html = '<div class="st6-attr-total">组合区间盈亏 <b class="' + pctCls(totalPnl) + '">' +
      (totalPnl > 0 ? '+' : '') + fmtMoney(totalPnl) + ' 元</b>' +
      '<span class="st6-note">各基金贡献额之和 = 组合盈亏(忽略区间申赎现金流)</span></div>';
    funds.forEach(function (f) {
      const name = esc(f.fund_name || f.fund_code);
      const code = esc(f.fund_code);
      if (f.contribution == null) {
        html += '<div class="st6-attr-row st6-attr-missing">' +
          '<div class="st6-attr-head"><span class="st6-attr-name">' + name +
            ' <span class="st6-group-count">' + code + '</span></span>' +
            '<span class="st6-attr-val st6-flat">历史不足</span></div></div>';
        return;
      }
      const contrib = Number(f.contribution) || 0;
      const width = Math.max(1.5, Math.abs(contrib) / maxAbs * 100);
      const share = f.contribution_rate == null ? '—' : pctRatio(f.contribution_rate, 1);
      const partial = f.partial
        ? '<span class="st6-attr-partial" title="基金历史起点晚于窗口起点,按可得区间计算">区间不完整</span>' : '';
      html += '<div class="st6-attr-row">' +
        '<div class="st6-attr-head">' +
          '<span class="st6-attr-name">' + name + ' <span class="st6-group-count">' + code + '</span>' + partial + '</span>' +
          '<span class="st6-attr-val ' + pctCls(contrib) + '">' + (contrib > 0 ? '+' : '') + fmtMoney(contrib) + ' 元</span>' +
        '</div>' +
        '<div class="st6-attr-track"><div class="st6-attr-bar ' +
          (contrib >= 0 ? 'st6-attr-bar-up' : 'st6-attr-bar-down') +
          '" style="width:' + width.toFixed(1) + '%;"></div></div>' +
        '<div class="st6-attr-meta">权重 ' + (f.weight == null ? '—' : pctRatio(f.weight, 1)) +
          ' · 期初 ' + fmtMoney(f.start_value) + ' 元' +
          ' · 净值 ' + Number(f.start_nav).toFixed(4) + ' → ' + Number(f.end_nav).toFixed(4) +
          ' (' + esc(f.start_date || '—') + ' → ' + esc(f.end_date || '—') + ')' +
          ' · 贡献占比 ' + share + '</div></div>';
    });
    if ((data.missing_funds || []).length) {
      html += '<div class="st6-xray-warn">历史净值不足,未纳入归因: ' + esc((data.missing_funds || []).join(', ')) + '</div>';
    }
    html += '<div class="st6-note">' + esc(data.note || '') +
      ' · 累计净值口径(含分红再投) · 窗口 ' + esc(data.window || attrState.window) + '</div>';
    body.innerHTML = html;
  }

  // ===== STAGE7: rebalance ===== 再平衡建议单(目标权重 + 偏离阈值)

  const RB_MAX_WEIGHT_TOTAL = 100;
  const rbState = { loaded: false };

  function loadRebalanceTab() {
    const box = document.getElementById('st6RbTargets');
    if (!box || rbState.loaded) return;
    renderStatus(box, 'loading');
    Promise.all([
      callApi('/api/portfolio/rebalance/config'),
      callApi('/api/fund/positions'),
    ]).then(function (res) {
      rbState.loaded = true;
      renderRebalanceEditor(res[0] || {}, res[1] || {});
    }).catch(function (e) {
      renderStatus(box, 'error', '再平衡配置加载失败', String(e && e.message || e));
    });
  }

  function renderRebalanceEditor(cfg, payload) {
    const box = document.getElementById('st6RbTargets');
    if (!box) return;
    const threshold = document.getElementById('st6RbThreshold');
    if (threshold && cfg.drift_threshold_pct != null) threshold.value = cfg.drift_threshold_pct;
    const positions = Array.isArray(payload) ? payload : (payload.positions || []);
    const targets = cfg.targets || {};
    const codes = [];
    positions.forEach(function (p) {
      if (p.fund_code && codes.indexOf(p.fund_code) < 0) codes.push(p.fund_code);
    });
    Object.keys(targets).forEach(function (c) {
      if (codes.indexOf(c) < 0) codes.push(c);
    });
    if (!codes.length) {
      box.innerHTML = '<div class="st6-note">暂无持仓;先到「持仓」添加基金后再配置目标权重。</div>';
      return;
    }
    box.innerHTML = '<div class="st6-rb-grid">' + codes.map(function (code) {
      const p = positions.filter(function (x) { return x.fund_code === code; })[0] || {};
      const val = targets[code] != null ? targets[code] : '';
      return '<label class="st6-rb-row" data-code="' + esc(code) + '">' +
        '<span class="st6-rb-name">' + esc(code) + ' ' + esc(p.fund_name || '') + '</span>' +
        '<input type="number" class="st6-rb-input st6-rb-weight" min="0" max="100" step="0.1" ' +
        'value="' + esc(val) + '" inputmode="decimal" aria-label="' + esc(code) + ' 目标权重(百分比)">' +
        '</label>';
    }).join('') + '</div>' +
      '<div class="st6-note">权重合计需 ≤100%(其余视为现金);留空的持仓不参与本次再平衡。</div>';
  }

  function collectRebalanceTargets() {
    const targets = {};
    let total = 0;
    const rows = document.querySelectorAll('#st6RbTargets .st6-rb-row');
    for (let i = 0; i < rows.length; i++) {
      const code = rows[i].getAttribute('data-code');
      const input = rows[i].querySelector('.st6-rb-weight');
      const raw = input ? String(input.value || '').trim() : '';
      if (!raw) continue;
      const w = Number(raw);
      if (!isFinite(w) || w <= 0 || w > 100) {
        return { error: code + ' 的目标权重需在 0~100 之间(留空表示不参与)' };
      }
      targets[code] = w;
      total += w;
    }
    if (total > RB_MAX_WEIGHT_TOTAL + 0.001) {
      return { error: '目标权重合计 ' + total.toFixed(1) + '% 超过 100%' };
    }
    return { targets: targets };
  }

  function saveRebalanceConfig() {
    const picked = collectRebalanceTargets();
    if (picked.error) { showToast(picked.error, 'error'); return; }
    const thresholdEl = document.getElementById('st6RbThreshold');
    const threshold = Number(thresholdEl ? thresholdEl.value : 5);
    if (!isFinite(threshold) || threshold <= 0 || threshold > 100) {
      showToast('偏离阈值需在 0~100 之间', 'error');
      return;
    }
    callApi('/api/portfolio/rebalance/config', {
      method: 'PUT',
      body: JSON.stringify({ drift_threshold_pct: threshold, targets: picked.targets }),
    }).then(function (data) {
      if (!data || data.success === false) {
        showToast((data && data.errors && data.errors[0]) || '再平衡目标保存失败', 'error');
        return;
      }
      showToast('再平衡目标已保存', 'success');
      loadRebalancePreview();
    });
  }

  function loadRebalancePreview() {
    const body = document.getElementById('st6RbBody');
    if (!body) return;
    renderStatus(body, 'loading');
    callApi('/api/portfolio/rebalance/preview').then(function (data) {
      if (!data) throw new Error('接口无响应');
      renderRebalancePreview(data);
    }).catch(function (e) {
      renderStatus(body, 'error', '再平衡建议生成失败', String(e && e.message || e));
    });
  }

  function renderRebalancePreview(data) {
    const body = document.getElementById('st6RbBody');
    if (!body) return;
    const targets = data.targets || {};
    if (!Object.keys(targets).length) {
      renderStatus(body, 'empty', '未配置目标权重', '在上方为目标基金填写权重并保存');
      return;
    }
    const positions = data.positions || [];
    const suggestions = data.suggestions || [];
    let html = '';
    if (positions.length) {
      html += '<div class="table-wrap"><table><thead><tr>' +
        '<th>代码</th><th>名称</th><th>份额</th><th>净值</th><th>市值</th>' +
        '<th>当前%</th><th>目标%</th><th>偏离pp</th></tr></thead><tbody>' +
        positions.map(function (p) {
          return '<tr>' +
            '<td>' + esc(p.code) + '</td>' +
            '<td>' + esc(p.name || '') + '</td>' +
            '<td>' + fmtMoney(p.shares) + '</td>' +
            '<td>' + (p.nav != null ? Number(p.nav).toFixed(4) : '—') + '</td>' +
            '<td>' + fmtMoney(p.value) + '</td>' +
            '<td>' + fmtPct(p.current_pct) + '</td>' +
            '<td>' + fmtPct(p.target_pct) + '</td>' +
            '<td class="' + pctCls(p.drift_pct) + '">' +
            (p.drift_pct > 0 ? '+' : '') + fmtPct(p.drift_pct) + '</td>' +
            '</tr>';
        }).join('') + '</tbody></table></div>' +
        '<div class="st6-note">参与口径市值 ' + fmtMoney(data.managed_value) +
        ' 元 · 建议买入 ' + fmtMoney(data.total_buy) + ' 元 · 建议卖出 ' +
        fmtMoney(data.total_sell) + ' 元</div>';
    }
    if (suggestions.length) {
      html += '<div class="st6-rb-suggests">' + suggestions.map(function (s) {
        const isBuy = s.action === 'buy';
        return '<div class="st6-rb-suggest">' +
          '<span class="st6-rb-action ' + (isBuy ? 'st6-up' : 'st6-down') + '">' +
          (isBuy ? '买入' : '卖出') + '</span>' +
          '<span class="st6-rb-suggest-name">' + esc(s.code) + ' ' + esc(s.name || '') + '</span>' +
          '<span>' + (isBuy ? '买入 ' : '卖出 ') + fmtMoney(s.shares) + ' 份 · ' +
          fmtMoney(s.amount) + ' 元</span>' +
          '<span class="st6-note" style="margin:0;">' + esc(s.reason) + '</span>' +
          '</div>';
      }).join('') + '</div>';
    } else {
      html += '<div class="st6-note">当前偏离均在阈值 ' + fmtPct(data.threshold_pct) +
        ' 以内,无需调仓。</div>';
    }
    if (data.degraded) {
      html += '<div class="st6-note" style="color:#f59e0b;">部分基金缺少最新净值,已从参与口径剔除</div>';
    }
    html += '<div class="st6-note">' + esc(data.note || '') + '</div>';
    body.innerHTML = html;
  }

  // ===== STAGE6: expose ===== 全局暴露(inline onclick 绑定)

  window.St6Portfolio = {
    loadWatchboard: loadWatchboard,
    loadRisk: loadRisk,
    loadXray: loadXray,
    loadAttribution: loadAttribution,
    saveRebalanceConfig: saveRebalanceConfig,
    loadRebalancePreview: loadRebalancePreview,
    savePushConfig: savePushConfig,
    testPush: testPush,
    downloadExport: downloadExport,
    ledgerFileChosen: ledgerFileChosen,
    ledgerPreview: ledgerPreview,
    ledgerExecute: ledgerExecute,
  };

  window.St6Groups = {
    applyGroups: applyGroups,
    setFilter: setFilter,
    toggleGroup: toggleGroup,
    toggleManage: toggleManage,
    renameGroup: renameGroup,
    addGroup: addGroup,
    deleteGroup: deleteGroup,
    assignFund: assignFund,
    saveGroups: saveGroups,
  };
})();
