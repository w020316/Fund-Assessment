/**
 * 阶段5 T04 — 五信号评估分步进度(SignalProgress)
 *
 * 架构文档 2.6 拍板:后端本期不改(无按信号步骤边界),前端采用
 * "分阶段渲染真实子结果",进度步骤一一对应真实网络请求,禁止假延时(P1-3 / K8):
 *   步骤1 数据获取   — 真实调 GET /api/fund/positions(首步反馈 ≤1s,渲染持仓数与净值时间戳)
 *   步骤2 五信号计算 — 真实调 GET /api/fund/advice-v2(2734ms 的主体)
 *   步骤3 结论汇总   — 真实渲染 summary 与各基金信号卡片(标注数据时间戳,US-9)
 * 每步显示 完成/进行中/失败态(来自真实请求结果);advice-v2 中单只基金带
 * error 占位时标"缺失",不阻塞整体展示已得结论(P1-3 ③)。
 * 模块为普通 <script> 全局对象(与存量风格一致,无构建步骤)。
 */
/* global api, escapeHtml, fmtNum, signalLabel, Freshness */

(function () {
  'use strict';

  const STEP_TITLES = ['数据获取', '五信号计算', '结论汇总'];

  const SIGNAL_NAMES = {
    technical: '技术面(20%)',
    fundamental: '基本面(20%)',
    news: '消息面(25%)',
    holdings: '重仓股板块(20%)',
    market: '大盘环境(15%)',
  };

  function injectStyle() {
    if (document.getElementById('signal-progress-style')) return;
    const style = document.createElement('style');
    style.id = 'signal-progress-style';
    style.textContent = [
      '.sigp-steps{display:flex;flex-direction:column;gap:0;margin-bottom:12px;}',
      '.sigp-step{display:flex;align-items:center;gap:10px;min-height:44px;padding:4px 0;}',
      '.sigp-dot{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;',
      '  justify-content:center;font-size:12px;flex-shrink:0;',
      '  border:2px solid var(--border-light,#333b4d);color:var(--text-muted,#8b92a8);}',
      '.sigp-step.running .sigp-dot{border-color:var(--accent-blue,#3b82f6);color:var(--accent-blue,#3b82f6);}',
      '.sigp-step.done .sigp-dot{border-color:var(--hud-down,#34D399);color:var(--hud-down,#34D399);}',
      '.sigp-step.error .sigp-dot{border-color:#ef4444;color:#ef4444;}',
      '.sigp-step .sigp-title{font-size:13px;color:var(--text-secondary,#c9d1d9);}',
      '.sigp-step.running .sigp-title{color:var(--text-primary,#e6edf3);}',
      '.sigp-step .sigp-info{font-size:11px;color:var(--text-muted,#8b92a8);margin-left:auto;text-align:right;}',
      '.sigp-card{padding:10px 0;border-bottom:1px solid var(--border,#2a3142);}',
      '.sigp-missing{color:var(--color-warning,#f59e0b);font-size:12px;}',
    ].join('');
    document.head.appendChild(style);
  }

  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s) : String(s == null ? '' : s); }

  /** 步骤条 HTML(state: pending/running/done/error;info 为真实耗时/结果摘要) */
  function stepsHtml(states, infos) {
    return '<div class="sigp-steps">' + STEP_TITLES.map(function (title, i) {
      const s = states[i] || 'pending';
      const icon = s === 'done' ? '✓' : s === 'error' ? '✕' : (i + 1);
      return '<div class="sigp-step ' + s + '">' +
        '<span class="sigp-dot">' + icon + '</span>' +
        '<span class="sigp-title">' + title + '</span>' +
        '<span class="sigp-info">' + (infos[i] || '') + '</span>' +
        '</div>';
    }).join('') + '</div>';
  }

  /** 结论卡片(每只基金;标注数据时间戳 US-9;error 占位标缺失不阻塞) */
  function renderFundCard(a, navInfo) {
    const fs = a.five_signals || {};
    if (a.error || !fs.signals) {
      return '<div class="sigp-card">' +
        '<strong style="font-size:13px;">' + esc(a.fund_name || a.fund_code) + '</strong> ' +
        '<span class="sigp-missing">⚠ 信号缺失:' + esc(a.error || '无信号数据') + '(不阻塞整体结论)</span>' +
        '</div>';
    }
    const signals = fs.signals || {};
    const advice = a.advice || {};
    const act = advice.action || fs.action || '—';
    const actColor = act === '买入' ? 'var(--color-up,#34d399)' :
      act === '卖出' ? 'var(--color-down,#f87171)' : 'var(--text-secondary,#c9d1d9)';
    const score = fs.final_score || 50;
    const rows = Object.keys(SIGNAL_NAMES).map(function (key) {
      const s = signals[key] || {};
      const sc = s.score || 50;
      const sig = String(s.signal || 'neutral');
      const c = sig.indexOf('bull') >= 0 ? 'var(--color-up,#34d399)' :
        sig.indexOf('bear') >= 0 ? 'var(--color-down,#f87171)' : 'var(--text-muted,#8b92a8)';
      return '<div class="signal-row" style="font-size:12px;padding:2px 0;">' +
        '<span class="signal-label" style="display:inline-block;width:110px;color:var(--text-muted,#8b92a8);">' + SIGNAL_NAMES[key] + '</span>' +
        '<span style="color:' + c + ';font-weight:600;">' + Number(sc).toFixed(1) + '</span>' +
        '<span style="color:var(--text-muted,#8b92a8);margin-left:8px;font-size:11px;">' + signalLabel(sig.toUpperCase()) + '</span>' +
        '</div>';
    }).join('');
    return '<div class="sigp-card">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">' +
      '  <div><strong style="font-size:13px;">' + esc(a.fund_name || a.fund_code) + '</strong>' +
      '  <span style="font-size:12px;color:var(--text-muted,#8b92a8);margin-left:6px;">评分 ' + Number(score).toFixed(1) + '</span></div>' +
      '  <span style="color:' + actColor + ';font-weight:600;font-size:12px;padding:2px 8px;border:1px solid ' + actColor + ';border-radius:3px;">' + esc(String(act)) + '</span>' +
      '</div>' +
      (navInfo ? '<div style="margin:4px 0;">' + Freshness.html(navInfo) + '</div>' : '') +
      rows +
      '</div>';
  }

  /**
   * 运行五信号评估(三步全部真实请求驱动,无人为延时)。
   * 渲染到 #hudSignalBody(由 index.html STAGE5 卡片提供)。
   */
  async function run() {
    injectStyle();
    const body = document.getElementById('hudSignalBody');
    if (!body) return;
    const btn = document.getElementById('hudSignalRunBtn');
    if (btn) { btn.disabled = true; btn.textContent = '评估中...'; }

    const states = ['pending', 'pending', 'pending'];
    const infos = ['', '', ''];
    const t0 = performance.now();

    function paint() { body.innerHTML = stepsHtml(states, infos) + (states[2] === 'done' ? _conclusionHtml : ''); }
    let _conclusionHtml = '';

    try {
      // ── 步骤 1:数据获取(真实请求)──
      states[0] = 'running'; paint();
      const t1 = performance.now();
      const posResp = await api('/api/fund/positions');
      if (!posResp || !posResp.positions) throw new Error('持仓数据获取失败');
      const navByCode = {};
      posResp.positions.forEach(function (p) { navByCode[p.fund_code] = p; });
      const lowCount = posResp.positions.filter(function (p) { return p.confidence === 'low'; }).length;
      states[0] = 'done';
      infos[0] = '持仓 ' + posResp.positions.length + ' 只 · 耗时 ' + Math.round(performance.now() - t1) + 'ms' +
        (lowCount ? ' · 置信度低 ' + lowCount + ' 只' : '');
      paint();

      // ── 步骤 2:五信号计算(真实请求,2734ms 的主体)──
      states[1] = 'running'; paint();
      const t2 = performance.now();
      const advResp = await api('/api/fund/advice-v2');
      if (!advResp) throw new Error('五信号计算请求失败');
      const data = advResp && advResp.data ? advResp.data : advResp;
      const positions = (data && data.positions_advice) || [];
      states[1] = 'done';
      infos[1] = '耗时 ' + Math.round(performance.now() - t2) + 'ms';
      paint();

      // ── 步骤 3:结论汇总(真实渲染)──
      states[2] = 'running'; paint();
      const summary = (data && data.summary) || '—';
      const missing = positions.filter(function (a) { return a.error || !(a.five_signals || {}).signals; }).length;
      const cards = positions.map(function (a) {
        const p = navByCode[a.fund_code] || {};
        return renderFundCard(a, {
          asOf: p.nav_date || (p.update_time || '').slice(0, 10),
          estimated: p.confidence === 'low' && !p.nav_date,
          note: p.confidence === 'low' ? (p.confidence_reason || '数据置信度低') : '',
        });
      }).join('');
      _conclusionHtml =
        '<div style="font-size:13px;padding:6px 0;">组合结论:' + esc(String(summary)) +
        (missing ? ' <span class="sigp-missing">(缺失 ' + missing + ' 只)</span>' : '') + '</div>' + cards;
      states[2] = 'done';
      infos[2] = '全程 ' + Math.round(performance.now() - t0) + 'ms';
      paint();
    } catch (e) {
      // 失败态:展示已得结论(已完成步骤保留),失败步骤标错(P1-3 ③)
      const idx = states.indexOf('running');
      if (idx >= 0) { states[idx] = 'error'; infos[idx] = esc(String(e.message || '请求失败')); }
      else { states[2] = 'error'; }
      paint();
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '开始评估'; }
    }
  }

  window.SignalProgress = { run: run };
})();
