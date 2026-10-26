/**
 * 阶段5 T02 — 持仓补录向导(FundWizard)
 *
 * 4 步状态机:① 持仓列表 → ② 逐只核对表单 → ③ diff 预览 → ④ 确认提交/回执
 *
 * 设计要点(架构文档 1.4 / PRD P0-1~P0-3):
 * - K7 前端 100% 拦截:份额≤0、成本≤0、代码非 6 位数字、非法日期
 * - 防覆盖保存语义:全量覆盖仅允许在 diff 预览 + 逐项勾选删除后提交
 * - 提交失败不丢已录入内容:所有草稿保存在内存 state,失败停留在预览步可重试
 * - 置信度低(fail-closed)条目一键直达该基金复核表单(预填现有值)
 * - 全部交互元素 ≥44×44px(触摸目标硬指标)
 * - 模块为普通 <script> 全局对象(与存量风格一致,无构建步骤)
 */
/* global api, showToast, escapeHtml, fmtNum */

(function () {
  'use strict';

  /** 向导状态(提交失败不丢内容的载体) */
  const state = {
    step: 1,            // 1 列表 / 2 表单 / 3 预览 / 4 确认
    draft: [],          // [{fund_code,fund_name,shares,cost_nav,buy_date,note,_deleted,_isNew}]
    editingCode: null,  // 当前表单步编辑的基金代码(null=新增)
    lastDiff: null,     // 最近一次 validate 返回(失败重试时复用)
    lastWarnings: [],
    submitting: false,  // 防重复提交
  };

  const STEPS = ['持仓列表', '逐只核对', 'diff 预览', '确认提交'];

  /* ============ 样式(自包含,一次注入;主题色最终由 hud.css 覆盖) ============ */
  function injectStyle() {
    if (document.getElementById('fw-style')) return;
    const style = document.createElement('style');
    style.id = 'fw-style';
    style.textContent = [
      '.fw-mask{position:fixed;inset:0;background:rgba(0,0,0,0.65);z-index:9999;',
      '  display:flex;align-items:center;justify-content:center;padding:12px;}',
      '.fw-modal{background:var(--bg-card,#161b22);border:1px solid var(--border,#2a3142);',
      '  border-radius:12px;width:100%;max-width:760px;max-height:92vh;display:flex;flex-direction:column;}',
      '.fw-header{display:flex;align-items:center;gap:10px;padding:12px 16px;',
      '  border-bottom:1px solid var(--border,#2a3142);font-size:15px;font-weight:600;}',
      '.fw-step-label{font-size:12px;color:var(--text-muted,#8b92a8);margin-left:auto;}',
      '.fw-close{min-width:44px;min-height:44px;display:flex;align-items:center;justify-content:center;',
      '  font-size:22px;cursor:pointer;color:var(--text-muted,#8b92a8);}',
      '.fw-steps{display:flex;gap:4px;padding:10px 16px 0;font-size:11px;flex-wrap:wrap;}',
      '.fw-steps .fw-step{padding:4px 10px;border-radius:12px;color:var(--text-muted,#8b92a8);',
      '  border:1px solid transparent;}',
      '.fw-steps .fw-step.on{color:var(--accent,#f59e0b);border-color:var(--accent,#f59e0b);}',
      '.fw-steps .fw-step.done{color:var(--color-up,#34d399);}',
      '.fw-body{padding:16px;overflow-y:auto;}',
      /* 44px 触摸目标硬指标 */
      '.fw-btn{min-height:44px;min-width:44px;padding:8px 16px;border-radius:8px;cursor:pointer;',
      '  font-size:13px;font-weight:500;border:1px solid var(--border-light,#333b4d);',
      '  background:transparent;color:var(--text-secondary,#c9d1d9);}',
      '.fw-btn:disabled{opacity:0.45;cursor:not-allowed;}',
      '.fw-btn-primary{background:var(--accent,#f59e0b);color:#0d1117;border-color:transparent;}',
      '.fw-btn-danger{background:var(--color-up,#ef4444);color:#fff;border-color:transparent;}',
      '.fw-input{min-height:44px;padding:8px 10px;border-radius:8px;width:100%;',
      '  border:1px solid var(--border-light,#333b4d);background:rgba(255,255,255,0.03);',
      '  color:var(--text-primary,#e6edf3);font-size:14px;box-sizing:border-box;}',
      '.fw-label{display:block;font-size:12px;color:var(--text-muted,#8b92a8);margin:10px 0 4px;}',
      '.fw-err{color:var(--color-up,#f87171);font-size:12px;margin-top:4px;min-height:16px;}',
      '.fw-table{width:100%;border-collapse:collapse;font-size:13px;}',
      '.fw-table th,.fw-table td{padding:8px 6px;text-align:left;border-bottom:1px solid var(--border,#2a3142);}',
      '.fw-row-del td{opacity:0.55;text-decoration:line-through;background:rgba(239,68,68,0.08);}',
      '.fw-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;}',
      '.fw-badge-low{color:#f59e0b;border:1px dashed #f59e0b;}',
      '.fw-badge-high{color:#34d399;border:1px solid rgba(52,211,153,0.4);}',
      '.fw-check{appearance:none;width:24px;height:24px;border:2px solid var(--border-light,#555);',
      '  border-radius:6px;cursor:pointer;position:relative;vertical-align:middle;flex-shrink:0;}',
      '.fw-check:checked{background:var(--color-up,#ef4444);border-color:var(--color-up,#ef4444);}',
      '.fw-check:checked::after{content:"✓";position:absolute;inset:0;display:flex;',
      '  align-items:center;justify-content:center;color:#fff;font-size:15px;}',
      '.fw-del-banner{display:flex;align-items:center;gap:10px;padding:10px 12px;margin:10px 0;',
      '  border:1px solid #ef4444;border-radius:8px;background:rgba(239,68,68,0.12);',
      '  color:#f87171;font-weight:600;font-size:13px;}',
      '.fw-del-item{display:flex;align-items:center;gap:10px;min-height:44px;padding:6px 8px;',
      '  border-bottom:1px solid var(--border,#2a3142);cursor:pointer;}',
      '.fw-section-title{font-size:13px;font-weight:600;margin:14px 0 6px;}',
      '.fw-change{font-size:12px;color:var(--text-muted,#8b92a8);margin-top:2px;}',
      '.fw-footer{display:flex;gap:10px;padding:12px 16px;border-top:1px solid var(--border,#2a3142);',
      '  flex-wrap:wrap;justify-content:flex-end;}',
      '.fw-receipt{text-align:center;padding:24px 8px;}',
      '.fw-receipt .fw-receipt-num{font-size:34px;font-weight:700;margin:8px 0;}',
      '@media (max-width:640px){.fw-modal{max-height:96vh;}.fw-hide-sm{display:none;}}',
    ].join('');
    document.head.appendChild(style);
  }

  /* ============ 工具 ============ */

  function ensureRoot() {
    let root = document.getElementById('fundWizardRoot');
    if (!root) {
      root = document.createElement('div');
      root.id = 'fundWizardRoot';
      document.body.appendChild(root);
    }
    return root;
  }

  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s) : String(s == null ? '' : s); }

  function num(v) { const n = Number(v); return isNaN(n) ? 0 : n; }

  /** K7 校验(与后端 FundPosition pydantic 规则对等,见架构文档 Shared Knowledge #6) */
  function validateRecord(rec) {
    const errors = {};
    if (!/^[0-9]{6}$/.test(String(rec.fund_code || ''))) {
      errors.fund_code = '基金代码须为 6 位数字';
    }
    if (!(num(rec.shares) > 0)) {
      errors.shares = '份额必须大于 0';
    }
    if (!(num(rec.cost_nav) > 0)) {
      errors.cost_nav = '成本净值必须大于 0';
    }
    if (rec.buy_date) {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(rec.buy_date));
      let ok = false;
      if (m) {
        const y = +m[1], mo = +m[2], d = +m[3];
        ok = mo >= 1 && mo <= 12 && d >= 1 && d <= new Date(y, mo, 0).getDate();
      }
      if (!ok) errors.buy_date = '日期须为合法的 YYYY-MM-DD';
    }
    return errors;
  }

  /** 去掉内部标记字段,得到可提交的持仓记录 */
  function toPayload(rec) {
    return {
      fund_code: rec.fund_code,
      fund_name: rec.fund_name || '',
      shares: num(rec.shares),
      cost_nav: num(rec.cost_nav),
      buy_date: rec.buy_date || '',
      note: rec.note || '',
    };
  }

  function activeDraft() { return state.draft.filter(function (r) { return !r._deleted; }); }

  function findDraft(code) {
    for (let i = 0; i < state.draft.length; i++) {
      if (state.draft[i].fund_code === code) return state.draft[i];
    }
    return null;
  }

  function renderStepLabel() {
    const el = document.getElementById('fwStepLabel');
    if (el) el.textContent = '步骤 ' + state.step + '/4 · ' + STEPS[state.step - 1];
    const steps = document.querySelectorAll('#fwSteps .fw-step');
    steps.forEach(function (s, i) {
      s.classList.toggle('on', i + 1 === state.step);
      s.classList.toggle('done', i + 1 < state.step);
    });
  }

  /* ============ 模态框骨架 ============ */

  function ensureModal() {
    const root = ensureRoot();
    if (document.getElementById('fwMask')) return;
    root.innerHTML =
      '<div class="fw-mask" id="fwMask" style="display:none;">' +
      '  <div class="fw-modal" role="dialog" aria-label="持仓补录向导">' +
      '    <div class="fw-header">' +
      '      <span>持仓补录向导</span>' +
      '      <span class="fw-step-label" id="fwStepLabel"></span>' +
      '      <span class="fw-close" role="button" tabindex="0" aria-label="关闭向导" onclick="FundWizard.close()">×</span>' +
      '    </div>' +
      '    <div class="fw-steps" id="fwSteps">' +
      STEPS.map(function (s, i) { return '<span class="fw-step">' + (i + 1) + ' ' + s + '</span>'; }).join('') +
      '    </div>' +
      '    <div class="fw-body" id="fwBody"></div>' +
      '  </div>' +
      '</div>';
  }

  /* ============ 打开/关闭 ============ */

  async function open(reviewCode) {
    injectStyle();
    ensureModal();
    const mask = document.getElementById('fwMask');
    mask.style.display = 'flex';
    // 重置状态并加载现有持仓(GET /api/fund/positions,含 confidence)
    state.step = 1;
    state.draft = [];
    state.editingCode = null;
    state.lastDiff = null;
    state.submitting = false;
    renderStep1Loading();
    const data = await api('/api/fund/positions');
    if (!data || !data.positions) {
      renderBody('<div class="fw-receipt">持仓加载失败,请关闭后重试</div>');
      return;
    }
    state.draft = data.positions.map(function (p) {
      return {
        fund_code: p.fund_code, fund_name: p.fund_name || '',
        shares: p.shares, cost_nav: p.cost_nav,
        buy_date: p.buy_date || '', note: p.note || '',
        nav_date: p.nav_date || '', confidence: p.confidence || 'high',
        confidence_reason: p.confidence_reason || '',
        _deleted: false, _isNew: false,
      };
    });
    renderStep1();
    // 支持从持仓页"⚠ 置信度低 → 复核"一键直达该基金表单(预填现有值)
    if (reviewCode) goToForm(reviewCode);
  }

  function close() {
    const mask = document.getElementById('fwMask');
    if (mask) mask.style.display = 'none';
  }

  function renderBody(html) {
    const body = document.getElementById('fwBody');
    if (body) body.innerHTML = html;
    renderStepLabel();
  }

  /* ============ 步骤 1:持仓列表 ============ */

  function renderStep1Loading() {
    renderBody('<div style="padding:32px;text-align:center;color:var(--text-muted,#8b92a8);">加载持仓中...</div>');
  }

  function renderStep1() {
    state.step = 1;
    const rows = state.draft.map(function (r) {
      const low = r.confidence === 'low';
      const delBtn = r._deleted
        ? '<button class="fw-btn" onclick="FundWizard.markDelete(\'' + r.fund_code + '\')">取消删除</button>'
        : '<button class="fw-btn" onclick="FundWizard.markDelete(\'' + r.fund_code + '\')">标记删除</button>';
      return '<tr class="' + (r._deleted ? 'fw-row-del' : '') + '">' +
        '  <td>' + esc(r.fund_code) + '</td>' +
        '  <td>' + esc(r.fund_name || '—') + '</td>' +
        '  <td>' + fmtNum(r.shares) + '</td>' +
        '  <td class="fw-hide-sm">' + fmtNum(r.cost_nav, 4) + '</td>' +
        '  <td>' + (low
          ? '<span class="fw-badge fw-badge-low">⚠ 置信度低</span><div class="fw-change">' + esc(r.confidence_reason) + '</div>'
          : '<span class="fw-badge fw-badge-high">截至 ' + esc(r.nav_date || '—') + '</span>') + '</td>' +
        '  <td style="white-space:nowrap;">' +
        (low && !r._deleted ? '<button class="fw-btn" onclick="FundWizard.goToForm(\'' + r.fund_code + '\')">复核</button> ' : '') +
        (!r._deleted ? '<button class="fw-btn" onclick="FundWizard.goToForm(\'' + r.fund_code + '\')">编辑</button> ' : '') +
        delBtn +
        '  </td>' +
        '</tr>';
    }).join('');
    const delCount = state.draft.filter(function (r) { return r._deleted; }).length;
    renderBody(
      (delCount ? '<div class="fw-del-banner">已标记删除 ' + delCount + ' 条(预览步将逐项确认)</div>' : '') +
      '<div style="overflow-x:auto;"><table class="fw-table">' +
      '<thead><tr><th>代码</th><th>名称</th><th>份额</th><th class="fw-hide-sm">成本净值</th><th>净值/置信度</th><th>操作</th></tr></thead>' +
      '<tbody>' + (rows || '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted);">暂无持仓,点击"新增基金"开始录入</td></tr>') + '</tbody>' +
      '</table></div>' +
      '<div class="fw-footer">' +
      '  <button class="fw-btn" onclick="FundWizard.goToForm(null)">+ 新增基金</button>' +
      '  <button class="fw-btn fw-btn-primary" onclick="FundWizard.goPreview()">下一步:diff 预览</button>' +
      '</div>'
    );
  }

  function markDelete(code) {
    const rec = findDraft(code);
    if (rec) rec._deleted = !rec._deleted;
    renderStep1();
  }

  /* ============ 步骤 2:逐只核对表单 ============ */

  function goToForm(code) {
    state.editingCode = code;
    const rec = code ? findDraft(code) : null;
    state.step = 2;
    renderBody(
      '<div class="fw-change" style="margin-bottom:4px;">' +
      (rec ? '正在核对 ' + esc(code) + '(预填现有值)' : '新增基金持仓') + '</div>' +
      '<label class="fw-label" for="fwCode">基金代码(6 位数字)</label>' +
      '<input class="fw-input" id="fwCode" inputmode="numeric" maxlength="6" placeholder="如 110022" value="' + esc(rec ? rec.fund_code : '') + '" oninput="FundWizard.onFieldInput()">' +
      '<div class="fw-err" id="fwErrCode"></div>' +
      '<label class="fw-label" for="fwName">基金名称</label>' +
      '<input class="fw-input" id="fwName" placeholder="如 易方达消费行业" value="' + esc(rec ? rec.fund_name : '') + '">' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">' +
      '  <div>' +
      '    <label class="fw-label" for="fwShares">持有份额(必须大于 0)</label>' +
      '    <input class="fw-input" id="fwShares" type="number" inputmode="decimal" step="0.01" min="0" placeholder="1000" value="' + (rec ? rec.shares : '') + '" oninput="FundWizard.onFieldInput()">' +
      '    <div class="fw-err" id="fwErrShares"></div>' +
      '  </div>' +
      '  <div>' +
      '    <label class="fw-label" for="fwCost">成本净值(必须大于 0)</label>' +
      '    <input class="fw-input" id="fwCost" type="number" inputmode="decimal" step="0.0001" min="0" placeholder="2.5000" value="' + (rec ? rec.cost_nav : '') + '" oninput="FundWizard.onFieldInput()">' +
      '    <div class="fw-err" id="fwErrCost"></div>' +
      '  </div>' +
      '</div>' +
      '<label class="fw-label" for="fwDate">买入日期(可选,YYYY-MM-DD)</label>' +
      '<input class="fw-input" id="fwDate" type="date" value="' + esc(rec ? rec.buy_date : '') + '" onchange="FundWizard.onFieldInput()">' +
      '<div class="fw-err" id="fwErrDate"></div>' +
      '<label class="fw-label" for="fwNote">备注</label>' +
      '<input class="fw-input" id="fwNote" maxlength="200" placeholder="可选" value="' + esc(rec ? rec.note : '') + '">' +
      '<div class="fw-footer">' +
      '  <button class="fw-btn" onclick="FundWizard.renderStep1()">返回列表</button>' +
      '  <button class="fw-btn fw-btn-primary" id="fwSaveBtn" onclick="FundWizard.saveFormRecord()">保存并下一步</button>' +
      '</div>'
    );
  }

  /** 表单即时内联校验(不依赖提交后报错,PRD 移动端优先章节) */
  function collectForm() {
    return {
      fund_code: (document.getElementById('fwCode').value || '').trim(),
      fund_name: (document.getElementById('fwName').value || '').trim(),
      shares: document.getElementById('fwShares').value,
      cost_nav: document.getElementById('fwCost').value,
      buy_date: document.getElementById('fwDate').value || '',
      note: (document.getElementById('fwNote').value || '').trim(),
    };
  }

  function showFormErrors(errors) {
    const map = { fund_code: 'fwErrCode', shares: 'fwErrShares', cost_nav: 'fwErrCost', buy_date: 'fwErrDate' };
    Object.keys(map).forEach(function (k) {
      const el = document.getElementById(map[k]);
      if (el) el.textContent = errors[k] || '';
    });
  }

  function onFieldInput() {
    // 即时校验:用户边输边提示,提交按钮在全部通过前置灰(K7 100% 拦截)
    const rec = collectForm();
    const errors = validateRecord(rec);
    showFormErrors(errors);
    const btn = document.getElementById('fwSaveBtn');
    if (btn) btn.disabled = Object.keys(errors).length > 0;
  }

  async function saveFormRecord() {
    const rec = collectForm();
    const errors = validateRecord(rec);
    if (Object.keys(errors).length) { showFormErrors(errors); return; }  // K7 双保险
    const btn = document.getElementById('fwSaveBtn');
    if (btn) { btn.disabled = true; btn.textContent = '保存中...'; }
    const resp = await api('/api/fund/positions/add', {
      method: 'POST',
      body: JSON.stringify(toPayload(rec)),
    });
    if (btn) { btn.disabled = false; btn.textContent = '保存并下一步'; }
    if (!resp || resp.success !== true) {
      showToast('单条保存失败,已录入内容保留,请重试', 'error');
      return;
    }
    // 更新本地草稿(upsert,保序)
    const existing = findDraft(rec.fund_code);
    if (existing) {
      Object.assign(existing, toPayload(rec));
    } else {
      const entry = Object.assign({}, toPayload(rec), { nav_date: '', confidence: 'high', confidence_reason: '', _deleted: false, _isNew: true });
      state.draft.push(entry);
    }
    showToast((resp.changes ? '已更新 ' : '已新增 ') + rec.fund_code, 'success');
    renderStep1();
  }

  /* ============ 步骤 3:diff 预览 ============ */

  async function goPreview() {
    state.step = 3;
    renderBody('<div style="padding:32px;text-align:center;color:var(--text-muted,#8b92a8);">正在计算变更预览...</div>');
    const payload = { positions: activeDraft().map(toPayload) };
    const resp = await api('/api/fund/positions/validate', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    if (!resp || resp.success !== true || !resp.summary) {
      // 失败:停留在预览入口,草稿完整保留,可重试
      renderBody(
        '<div class="fw-del-banner">预览计算失败,已录入内容已保留,可重试</div>' +
        '<div class="fw-footer"><button class="fw-btn" onclick="FundWizard.renderStep1()">返回列表</button>' +
        '<button class="fw-btn fw-btn-primary" onclick="FundWizard.goPreview()">重试</button></div>'
      );
      return;
    }
    state.lastDiff = resp.summary;
    state.lastWarnings = resp.warnings || [];
    renderStep3();
  }

  function renderStep3() {
    state.step = 3;
    const diff = state.lastDiff;
    if (!diff) { goPreview(); return; }
    const added = diff.added || [];
    const updated = diff.updated || [];
    const deleted = diff.deleted || [];
    const warnings = state.lastWarnings || [];
    const delChecked = deletedCodesConfirmed();

    let html = '';

    // 新增
    html += '<div class="fw-section-title">新增(<span style="color:var(--color-up,#34d399);">' + added.length + '</span> 条)</div>';
    html += added.length
      ? added.map(function (a) {
        return '<div class="fw-del-item" style="cursor:default;">+ ' + esc(a.fund_code) + ' ' + esc(a.fund_name || '') + '</div>';
      }).join('')
      : '<div class="fw-change">无新增</div>';

    // 更新(字段级 old→new)
    html += '<div class="fw-section-title">更新(<span style="color:var(--accent,#f59e0b);">' + updated.length + '</span> 条)</div>';
    html += updated.length
      ? updated.map(function (u) {
        const changes = Object.keys(u.changes || {}).map(function (f) {
          const c = u.changes[f];
          const oldV = (typeof c.old === 'number') ? fmtNum(c.old) : (c.old || '空');
          const newV = (typeof c.new === 'number') ? fmtNum(c.new) : (c.new || '空');
          return '<div class="fw-change">' + esc(f) + ': ' + esc(String(oldV)) + ' → <b>' + esc(String(newV)) + '</b></div>';
        }).join('');
        return '<div class="fw-del-item" style="cursor:default;flex-wrap:wrap;">~ ' + esc(u.fund_code) + ' ' + esc(u.fund_name || '') + changes + '</div>';
      }).join('')
      : '<div class="fw-change">无更新</div>';

    // 删除(逐项勾选 + 红色高亮"将删除 N 条")
    html += '<div class="fw-del-banner">⚠ 将删除 <span style="font-size:18px;">' + deleted.length + '</span> 条持仓 — 每条须逐项勾选确认</div>';
    if (deleted.length) {
      html += deleted.map(function (d) {
        const checked = state.lastDiff._confirmedDeletes && state.lastDiff._confirmedDeletes.indexOf(d.fund_code) >= 0;
        return '<label class="fw-del-item">' +
          '<input type="checkbox" class="fw-check" data-del-code="' + esc(d.fund_code) + '" ' + (checked ? 'checked' : '') + ' onchange="FundWizard.onDeleteCheck(this)">' +
          '<span>确认删除 ' + esc(d.fund_code) + ' ' + esc(d.fund_name || '') + '</span></label>';
      }).join('');
    } else {
      html += '<div class="fw-change">无删除</div>';
    }

    // 置信度警告
    if (warnings.length) {
      html += '<div class="fw-section-title" style="color:#f59e0b;">置信度提示</div>';
      html += warnings.map(function (w) {
        return '<div class="fw-change">⚠ ' + esc(w.fund_code) + ':' + esc(w.message) + '</div>';
      }).join('');
    }

    // 确认文案写明影响范围(P0-3)
    html += '<div style="margin-top:14px;padding:10px 12px;border:1px solid var(--border,#2a3142);border-radius:8px;font-size:13px;">' +
      '本次提交影响范围:新增 <b>' + added.length + '</b> 条、更新 <b>' + updated.length + '</b> 条、删除 <b>' + deleted.length + '</b> 条(全量覆盖保存)' +
      '</div>';

    html += '<div class="fw-footer">' +
      '<button class="fw-btn" onclick="FundWizard.renderStep1()">返回列表</button>' +
      '<button class="fw-btn fw-btn-primary" id="fwConfirmBtn" onclick="FundWizard.goConfirm()"' +
      (deleted.length && delChecked < deleted.length ? ' disabled' : '') + '>下一步:确认提交</button>' +
      '</div>';

    renderBody(html);
  }

  /** 已勾选确认的删除项代码列表(存于 lastDiff._confirmedDeletes,内存态) */
  function deletedCodesConfirmed() {
    const boxes = document.querySelectorAll('#fwBody input[data-del-code]');
    const codes = [];
    boxes.forEach(function (b) { if (b.checked) codes.push(b.getAttribute('data-del-code')); });
    if (state.lastDiff) state.lastDiff._confirmedDeletes = codes;
    return codes.length;
  }

  function onDeleteCheck() {
    const diff = state.lastDiff;
    const total = (diff && diff.deleted ? diff.deleted.length : 0);
    const checked = deletedCodesConfirmed();
    const btn = document.getElementById('fwConfirmBtn');
    if (btn) btn.disabled = checked < total;
  }

  /* ============ 步骤 4:确认提交 + 回执 ============ */

  function goConfirm() {
    const diff = state.lastDiff;
    if (!diff) return;
    state.step = 4;
    const deletedCodes = (diff.deleted || []).map(function (d) { return d.fund_code; }).join('、') || '无';
    renderBody(
      '<div class="fw-section-title" style="font-size:15px;">二次确认</div>' +
      '<div style="font-size:13px;line-height:1.8;">' +
      '保存方式为<b style="color:#f87171;">全量覆盖</b>,请确认影响范围:<br>' +
      '· 新增 ' + diff.added.length + ' 条<br>' +
      '· 更新 ' + diff.updated.length + ' 条<br>' +
      '· 删除 ' + diff.deleted.length + ' 条(代码:' + esc(deletedCodes) + ')<br>' +
      '保存后可通过"重新补录"修正,但删除的持仓需手动重新录入。' +
      '</div>' +
      '<div class="fw-footer">' +
      '<button class="fw-btn" onclick="FundWizard.renderStep3()">返回修改</button>' +
      '<button class="fw-btn fw-btn-danger" id="fwSubmitBtn" onclick="FundWizard.doSubmit()">确认提交</button>' +
      '</div>'
    );
  }

  async function doSubmit() {
    if (state.submitting) return;
    state.submitting = true;
    const btn = document.getElementById('fwSubmitBtn');
    if (btn) { btn.disabled = true; btn.textContent = '提交中...'; }
    const payload = { positions: activeDraft().map(toPayload) };
    const resp = await api('/api/fund/positions', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    state.submitting = false;
    if (!resp || resp.success !== true) {
      // 提交失败:模态框不销毁,草稿保留在 state,回到预览步可重试(P0-1 ④)
      showToast('提交失败,已录入内容未丢失,请重试', 'error');
      renderStep3();
      return;
    }
    // 成功:回执(影响范围摘要),之后关闭
    const s = resp.summary || { added: 0, updated: 0, deleted: 0, deleted_codes: [] };
    renderBody(
      '<div class="fw-receipt">' +
      '  <div style="font-size:15px;">✅ 保存成功</div>' +
      '  <div class="fw-receipt-num" style="color:var(--color-up,#34d399);">+' + s.added + '</div>' +
      '  <div class="fw-change">新增 ' + s.added + ' 条 · 更新 ' + s.updated + ' 条 · 删除 ' + s.deleted + ' 条</div>' +
      (s.deleted_codes && s.deleted_codes.length
        ? '<div class="fw-change" style="color:#f87171;">已删除:' + esc(s.deleted_codes.join('、')) + '</div>' : '') +
      '</div>' +
      '<div class="fw-footer"><button class="fw-btn fw-btn-primary" onclick="FundWizard.close()">关闭向导</button></div>'
    );
    // 刷新底层页面数据(若函数已就绪)
    if (typeof loadFundData === 'function') { try { loadFundData(); } catch (e) { /* 忽略 */ } }
  }

  /* ============ 导出(全局,与存量风格一致) ============ */
  window.FundWizard = {
    open: open,
    close: close,
    renderStep1: renderStep1,
    renderStep3: renderStep3,
    goToForm: goToForm,
    markDelete: markDelete,
    saveFormRecord: saveFormRecord,
    onFieldInput: onFieldInput,
    goPreview: goPreview,
    goConfirm: goConfirm,
    doSubmit: doSubmit,
    onDeleteCheck: onDeleteCheck,
  };
})();
