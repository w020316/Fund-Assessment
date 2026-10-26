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

  function loadPushSettings() {
    callApi('/api/portfolio/webhook/config')
      .then(function (data) {
        if (!data || !data.config) return;
        const cfg = data.config;
        const box = document.getElementById('st6PushBody');
        if (!box) return;
        box.innerHTML =
          '<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;">' +
            '<label style="display:flex;align-items:center;gap:6px;font-size:13px;">' +
              '<input type="checkbox" id="st6PushEnabled" ' + (cfg.enabled ? 'checked' : '') + ' style="width:18px;height:18px;"> 启用推送' +
            '</label>' +
            '<label style="display:flex;align-items:center;gap:6px;font-size:13px;">阈值 ±' +
              '<input class="input" type="number" id="st6PushThreshold" value="' + esc(cfg.thresholds && cfg.thresholds.est_pct_abs != null ? cfg.thresholds.est_pct_abs : 2.0) + '" step="0.5" style="width:80px;">%' +
            '</label>' +
            '<span style="font-size:12px;color:var(--text-muted,#8b92a8);">渠道: ' + esc(cfg.channel) +
              (cfg.sendkey_configured ? ' · 凭据已配置' : ' · <span style="color:#f59e0b;">凭据未配置(env PUSH_SERVERCHAN_SENDKEY)</span>') + '</span>' +
          '</div>' +
          '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">' +
            '<button class="btn btn-sm btn-primary" id="st6PushSaveBtn" onclick="St6Portfolio.savePushConfig()">保存配置</button>' +
            '<button class="btn btn-sm" id="st6PushTestBtn" onclick="St6Portfolio.testPush()">发送测试推送</button>' +
          '</div>';
      })
      .catch(function () { /* 静默:设置卡片非关键路径 */ });
  }

  function savePushConfig() {
    const enabled = !!(document.getElementById('st6PushEnabled') || {}).checked;
    const thRaw = (document.getElementById('st6PushThreshold') || {}).value;
    callApi('/api/portfolio/webhook/config', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: enabled,
        thresholds: { est_pct_abs: Number(thRaw) || 2.0 },
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
      html += '<div class="st6-diff-item">推送配置: enabled ' + String(diff.portfolio_config.enabled.current) +
        ' → ' + String(diff.portfolio_config.enabled.incoming) + '</div>';
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

  // ===== STAGE6: expose ===== 全局暴露(inline onclick 绑定)

  window.St6Portfolio = {
    loadWatchboard: loadWatchboard,
    loadRisk: loadRisk,
    loadXray: loadXray,
    savePushConfig: savePushConfig,
    testPush: testPush,
    downloadExport: downloadExport,
    ledgerFileChosen: ledgerFileChosen,
    ledgerPreview: ledgerPreview,
    ledgerExecute: ledgerExecute,
  };
})();
