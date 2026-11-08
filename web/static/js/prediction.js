/**
 * STAGE6-PRED: prediction.js — 监控容器三区块(时段卡片 / 次日预测 / AI 准确率看板)。
 *
 * 设计文档 §三 / T04:
 * - 时段卡片: rule/llm 摘要 + 采集时间 Freshness 角标 + 立即采集按钮(5 分钟幂等) + "未采集"占位
 * - 次日预测卡片: 方向/置信度/理由 + 近 30 日历史展开
 * - AI 准确率看板: 三窗口命中率 + 30 日走势条 + 样本量 + "样本不足"明确态
 * 独立 JS(无构建步骤), 通过包装 window.switchPage 接入('monitor' 页加载), 不改 index.html 既有函数。
 */
/* global escapeHtml, Freshness, showToast */
(function () {
  'use strict';

  const SESSIONS = [
    { key: 'morning', label: '早盘' },
    { key: 'noon', label: '午盘' },
    { key: 'close', label: '盘后' },
  ];
  const DIR_LABEL = { up: '看涨', down: '看跌', flat: '震荡' };
  const PAGES_TO_LOAD = { monitor: 'loadAll' };

  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : ''; }

  /** UTC ISO → 北京时间 HH:mm(Freshness asOf 用)。 */
  function cstTime(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleTimeString('zh-CN', {
        timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false,
      });
    } catch (e) { return ''; }
  }

  function cstDateStr() {
    // 北京时间今天 YYYY-MM-DD(与后端 trade_date 口径一致)
    const parts = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' })
      .format(new Date());
    return parts; // sv-SE locale 输出 YYYY-MM-DD
  }

  async function fetchJSON(url, options) {
    const opts = options || {};
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    try {
      const t = localStorage.getItem('admin_token');
      if (t) headers['Authorization'] = 'Bearer ' + t;
    } catch (e) { /* 隐私模式忽略 */ }
    try {
      const resp = await fetch(url, Object.assign({}, opts, { headers }));
      if (resp.status === 401) return { ok: false, status: 401, body: null };
      const body = await resp.json();
      return { ok: resp.ok && body && body.code === 0, status: resp.status, body: body };
    } catch (e) {
      return { ok: false, status: 0, body: null, error: e };
    }
  }

  // ===== 时段卡片(R1.3/R1.4) =====

  function sessionCardHTML(sess, row) {
    if (!row) {
      return '<div class="pred-card"><div class="pred-card-head">'
        + '<span class="pred-card-title">' + esc(sess.label) + '</span></div>'
        + '<div class="pred-empty">未采集</div>'
        + '<div style="margin-top:8px;text-align:center;">'
        + '<button class="btn btn-sm btn-outline" style="min-height:36px;" onclick="PredictionPage.collect(\'' + sess.key + '\')">立即采集</button>'
        + '</div></div>';
    }
    const missing = (row.missing_fields && row.missing_fields.length)
      ? '<span class="pred-insufficient">部分字段缺失: ' + esc(row.missing_fields.join(',')) + '</span>'
      : '';
    const llm = row.llm_summary
      ? '<div class="pred-llm">AI: ' + esc(row.llm_summary) + '</div>'
      : '<div class="pred-llm">AI 一句话未生成(LLM 降级, 规则摘要完整)</div>';
    return '<div class="pred-card"><div class="pred-card-head">'
      + '<span class="pred-card-title">' + esc(sess.label) + '</span>'
      + Freshness.html({ asOf: cstTime(row.collected_at) })
      + '</div>'
      + '<div class="pred-summary">' + esc(row.rule_summary || '暂无规则摘要') + '</div>'
      + llm + missing
      + '<div style="margin-top:8px;">'
      + '<button class="btn btn-sm btn-outline" style="min-height:36px;" onclick="PredictionPage.collect(\'' + sess.key + '\')">立即采集</button>'
      + '</div></div>';
  }

  async function loadSessions() {
    const el = document.getElementById('predSessionCards');
    if (!el) return;
    el.innerHTML = '<div class="pred-empty">加载中…</div>';
    const r = await fetchJSON('/api/prediction/snapshots?days=2');
    // QF3-27: 先判 ok 再判空。此前 `(r.ok && ...) || []` 把请求失败归一成空数组,
    // 四张时段卡片会渲染成"未采集", 与"今天还没到采集时点"完全无法区分。
    if (!r.ok) {
      el.innerHTML = r.status
        ? '<div class="pred-empty">数据加载失败（HTTP ' + r.status + '），请稍后重试</div>'
        : '<div class="pred-empty">数据加载失败，请检查网络后重试</div>';
      return;
    }
    const today = cstDateStr();
    const rows = (r.body && r.body.data && r.body.data.snapshots) || [];
    const bySession = {};
    rows.forEach(function (row) { if (row.trade_date === today) bySession[row.session] = row; });
    el.innerHTML = SESSIONS.map(function (s) { return sessionCardHTML(s, bySession[s.key]); }).join('');
  }

  async function collect(session) {
    const r = await fetchJSON('/api/prediction/snapshots/collect', {
      method: 'POST',
      body: JSON.stringify({ session: session }),
    });
    if (typeof showToast === 'function') {
      if (r.ok) {
        const d = (r.body && r.body.data) || {};
        showToast(d.skipped ? '非交易日, 已跳过采集' : (d.idempotent ? '5 分钟内已采集, 返回既有快照' : '采集完成'), 'success');
      } else {
        showToast('采集失败, 请稍后重试', 'error');
      }
    }
    await loadSessions();
  }

  // ===== 次日预测卡片(R3.2) =====

  function predictionCardHTML(p) {
    const cls = p.direction === 'up' ? 'up' : (p.direction === 'down' ? 'down' : 'flat');
    const conflict = p.conflict_flag
      ? '<span class="pred-insufficient">规则与 AI 结论冲突, 已按规则为准</span>' : '';
    const reasons = (p.rule_reasons || []).map(function (r) {
      const v = (r.value === null || r.value === undefined) ? '缺数据' : JSON.stringify(r.value);
      return '<li><span class="pred-muted">' + esc(r.signal) + '</span> = ' + esc(v)
        + ' → ' + esc(r.verdict || '') + '</li>';
    }).join('');
    const llm = p.llm_direction
      ? '<div class="pred-llm">AI(deep): ' + esc(DIR_LABEL[p.llm_direction] || p.llm_direction)
        + (p.llm_confidence != null ? ' · 置信度 ' + esc(p.llm_confidence) : '')
        + (p.llm_reason ? ' · ' + esc(p.llm_reason) : '') + '</div>'
      : '<div class="pred-llm">AI 辅助判断未生成(降级, 规则结论完整)</div>';
    return '<div class="pred-card-head">'
      + '<span class="pred-card-title">预测目标日 ' + esc(p.target_date) + ' · 沪深300</span>'
      + Freshness.html({ asOf: cstTime(p.created_at) })
      + '</div>'
      + '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:6px 0;">'
      + '<span class="pred-dir-badge ' + cls + '">' + esc(DIR_LABEL[p.direction] || p.direction) + '</span>'
      + '<span class="pred-conf">置信度 ' + esc(p.confidence) + '</span>'
      + conflict + '</div>'
      + '<details><summary class="pred-muted" style="cursor:pointer;">规则依据(可追溯)</summary>'
      + '<ul style="font-size:12px;line-height:1.9;margin:6px 0 0;padding-left:18px;">' + reasons + '</ul></details>'
      + llm;
  }

  async function loadPrediction() {
    const el = document.getElementById('predNextDayBody');
    if (!el) return;
    el.innerHTML = '<div class="pred-empty">加载中…</div>';
    const r = await fetchJSON('/api/prediction/predictions?days=30');
    // QF3-27: 先判 ok 再判空。此前失败时提示"暂无预测(每交易日 15:40 自动生成)",
    // 用户会等到 15:40 再来, 而实际是请求失败。
    if (!r.ok) {
      el.innerHTML = r.status
        ? '<div class="pred-empty">数据加载失败（HTTP ' + r.status + '），请稍后重试</div>'
        : '<div class="pred-empty">数据加载失败，请检查网络后重试</div>';
      return;
    }
    const rows = (r.body && r.body.data && r.body.data.predictions) || [];
    if (!rows.length) {
      el.innerHTML = '<div class="pred-empty">暂无预测(每交易日 15:40 盘后自动生成, 也可由管理员手动触发)</div>';
      return;
    }
    const history = rows.map(function (p) {
      const cls = p.direction === 'up' ? 'pred-up' : (p.direction === 'down' ? 'pred-down' : 'pred-flat');
      return '<tr><td>' + esc(p.trade_date) + '</td><td>' + esc(p.target_date) + '</td>'
        + '<td class="' + cls + '">' + esc(DIR_LABEL[p.direction] || p.direction) + '</td>'
        + '<td>' + esc(p.confidence) + '</td>'
        + '<td>' + (p.conflict_flag ? '<span class="pred-insufficient">冲突</span>' : '') + '</td></tr>';
    }).join('');
    el.innerHTML = predictionCardHTML(rows[0])
      + '<details style="margin-top:10px;"><summary class="pred-muted" style="cursor:pointer;">近 30 日历史(' + rows.length + ')</summary>'
      + '<div class="table-wrap" style="max-height:260px;overflow-y:auto;margin-top:6px;"><table class="pred-sector-table">'
      + '<thead><tr><th>预测日</th><th>目标日</th><th>方向</th><th>置信度</th><th>冲突</th></tr></thead>'
      + '<tbody>' + history + '</tbody></table></div></details>';
  }

  // ===== AI 准确率看板(R6.3) =====

  const WINDOWS = [['day', '日'], ['7d', '近 7 日'], ['30d', '近 30 日']];
  const KINDS = [['prediction', '预测'], ['recommendation', '推荐']];

  function fmtRate(rate) {
    return rate == null ? '样本不足' : (rate * 100).toFixed(1) + '%';
  }

  function accCellHTML(kindLabel, kindData) {
    const rule = kindData.rule || {};
    const llm = kindData.llm || {};
    const insufficient = (rule.total < 5) ? '<span class="pred-insufficient">样本不足</span>' : '';
    const rateCls = rule.hit_rate == null ? 'pred-flat' : (rule.hit_rate >= 0.5 ? 'pred-up' : 'pred-down');
    return '<div class="pred-acc-cell">'
      + '<div class="pred-acc-title">' + esc(kindLabel) + ' · 规则口径</div>'
      + '<div class="pred-acc-rate ' + rateCls + '">' + esc(fmtRate(rule.hit_rate)) + '</div>'
      + '<div class="pred-acc-meta">命中 ' + esc(rule.hits) + ' / ' + esc(rule.total) + ' 条 ' + insufficient + '</div>'
      + '<div class="pred-acc-meta">AI 口径: ' + esc(fmtRate(llm.hit_rate))
      + ' (' + esc(llm.hits) + '/' + esc(llm.total) + ')</div>'
      + '</div>';
  }

  function trendHTML(trend) {
    if (!trend || !trend.length) return '<div class="pred-muted" style="margin-top:8px;">暂无走势数据(验证闭环运行后展示)</div>';
    const max = Math.max.apply(null, trend.map(function (t) { return t.hit_rate || 0; })) || 1;
    const bars = trend.map(function (t) {
      const h = Math.max(4, Math.round((t.hit_rate || 0) / max * 60));
      const low = (t.hit_rate || 0) < 0.5 ? ' is-low' : '';
      return '<div class="pred-trend-bar' + low + '" style="height:' + h + 'px;" title="'
        + esc(t.stat_date) + ' 命中率 ' + esc(fmtRate(t.hit_rate)) + ' (样本 ' + esc(t.total) + ')"></div>';
    }).join('');
    return '<div class="pred-trend">' + bars + '</div>'
      + '<div class="pred-muted" style="margin-top:6px;">近 ' + trend.length + ' 个验证日 · 条形越高命中率越高(橙=低于50%)</div>';
  }

  async function loadAccuracy() {
    const el = document.getElementById('predAccuracyBody');
    if (!el) return;
    el.innerHTML = '<div class="pred-empty">加载中…</div>';
    const r = await fetchJSON('/api/prediction/accuracy');
    if (!r.ok || !r.body || !r.body.data) {
      el.innerHTML = '<div class="pred-empty">准确率数据加载失败, 请稍后重试</div>';
      return;
    }
    const d = r.body.data;
    const cells = [];
    WINDOWS.forEach(function (w) {
      const wd = d.windows[w[0]] || {};
      KINDS.forEach(function (k) {
        if (wd[k[0]]) cells.push(accCellHTML(w[1] + ' · ' + k[1], wd[k[0]]));
      });
    });
    const insufficient = d.insufficient_sample
      ? '<span class="pred-insufficient">部分窗口样本不足(<5 条), 对应命中率不展示数字</span>' : '';
    el.innerHTML = '<div style="margin-bottom:8px;">' + insufficient
      + Freshness.html({ asOf: cstTime(new Date().toISOString()), note: '实时统计' }) + '</div>'
      + '<div class="pred-acc-grid">' + cells.join('') + '</div>'
      + trendHTML(d.trend_30d);
  }

  function loadAll() {
    loadSessions();
    loadPrediction();
    loadAccuracy();
  }

  // ===== switchPage 包装接入(不改 index.html 既有函数; 与 stage6 同模式) =====
  function wrapSwitchPage() {
    if (typeof window.switchPage !== 'function') return;
    if (window.__predSwitchWrapped) return;
    window.__predSwitchWrapped = true;
    const orig = window.switchPage;
    window.switchPage = function (name) {
      const out = orig.apply(this, arguments);
      const hook = PAGES_TO_LOAD[name];
      if (hook && typeof window.PredictionPage[hook] === 'function') {
        try { window.PredictionPage[hook](); } catch (e) { /* 加载失败不阻塞切页 */ }
      }
      return out;
    };
  }

  window.PredictionPage = {
    loadAll: loadAll,
    loadSessions: loadSessions,
    loadPrediction: loadPrediction,
    loadAccuracy: loadAccuracy,
    collect: collect,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wrapSwitchPage);
  } else {
    wrapSwitchPage();
  }
})();
