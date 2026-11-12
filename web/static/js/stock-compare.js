(() => {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const form = byId('compareForm');
  let busy = false;
  let result = null;
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const number = (value, digits = 4) => finite(value) ? value.toFixed(digits) : '—';
  const percent = (value) => finite(value) ? `${(value * 100).toFixed(1)}%` : '—';

  function status(text, kind = '') {
    byId('status').textContent = text;
    byId('status').className = kind;
  }

  function invalidate() {
    result = null;
    byId('report').hidden = true;
    status('输入已更新，请重新运行对比。');
  }

  function setBusy(value) {
    busy = value;
    form.querySelectorAll('input, button').forEach((element) => { element.disabled = value; });
    byId('runCompare').textContent = value ? '正在逐票回测…' : '运行对比';
    form.setAttribute('aria-busy', String(value));
  }

  // 客户端预校验: 2–5 只、各自 6 位数字、去重后仍 ≥2。服务端仍有 422 兜底。
  function parseCodes() {
    const raw = byId('stockCodes').value.trim();
    if (!raw) return null;
    const codes = [...new Set(raw.split(/[,，;\s;]+/).filter(Boolean))];
    if (codes.length < 2 || codes.length > 5) return null;
    if (codes.some((code) => !/^[0-9]{6}$/.test(code))) return null;
    return codes;
  }

  function cell(label, value, color) {
    const td = document.createElement('td');
    td.className = 'cell';
    td.textContent = value;
    if (color) td.style.color = color;
    td.setAttribute('data-label', label);
    return td;
  }

  function render() {
    if (!result) return;
    const data = result.data;
    const results = Array.isArray(data.results) ? data.results : [];
    byId('compareInfo').textContent = `${data.start_date} ~ ${data.end_date} · ${results.length} 只标的`;
    const warning = byId('warning');
    warning.textContent = result.warning || '';
    warning.hidden = !result.warning;

    const table = byId('rankTable');
    table.replaceChildren();
    const head = document.createElement('tr');
    for (const text of ['排名', '代码', '最优组合', '区间收益', '年化收益', '信息比率', '夏普', '最大回撤', '交易次数', '备注']) {
      head.append(Object.assign(document.createElement('th'), { textContent: text }));
    }
    table.append(head);

    results.forEach((row, index) => {
      const tr = document.createElement('tr');
      tr.append(Object.assign(document.createElement('td'), {
        className: 'axis',
        textContent: row.error ? '—' : String(index + 1),
      }));
      tr.append(Object.assign(document.createElement('td'), {
        className: 'axis',
        textContent: row.stock_code,
      }));
      const best = row.best;
      if (!best) {
        for (let i = 0; i < 7; i += 1) tr.append(cell('', '—'));
        tr.append(cell('备注', row.error || '无有效组合', 'var(--qf-sem-critical)'));
      } else {
        tr.title = `MA${best.short_window}/MA${best.long_window} · 偏差仅在该网格内相对`;
        tr.append(
          cell('最优组合', `MA${best.short_window}/MA${best.long_window}`),
          cell('区间收益', percent(best.total_return),
            finite(best.total_return) ? (best.total_return >= 0 ? 'var(--qf-sem-up)' : 'var(--qf-sem-down)') : ''),
          cell('年化收益', percent(best.annual_return),
            finite(best.annual_return) ? (best.annual_return >= 0 ? 'var(--qf-sem-up)' : 'var(--qf-sem-down)') : ''),
          cell('信息比率', number(best.information_ratio),
            finite(best.information_ratio) ? (best.information_ratio >= 0 ? 'var(--qf-sem-up)' : 'var(--qf-sem-down)') : ''),
          cell('夏普', number(best.sharpe_ratio)),
          cell('最大回撤', percent(best.max_drawdown)),
          cell('交易次数', finite(best.trade_count) ? String(best.trade_count) : '—'),
          cell('备注', '—'),
        );
      }
      table.append(tr);
    });
    byId('report').hidden = false;
    status('对比完成；排名仅代表同一区间内策略表现的相对差异。', 'success');
  }

  form.addEventListener('input', invalidate);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    const codes = parseCodes();
    if (!codes) {
      status('股票代码有误：需要 2–5 个互不相同的 6 位数字 A 股代码，用逗号或空格分隔。', 'error');
      return;
    }
    const payload = {
      stock_codes: codes,
      start_date: byId('startDate').value,
      end_date: byId('endDate').value,
    };
    const headers = { 'Content-Type': 'application/json' };
    let token = byId('adminToken').value.trim();
    if (!token) {
      try { token = localStorage.getItem('admin_token') || ''; } catch { token = ''; }
    }
    if (token) headers.Authorization = `Bearer ${token}`;
    setBusy(true);
    try {
      const resp = await fetch('/api/strategy/compare-ma-cross', { method: 'POST', headers, body: JSON.stringify(payload) });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { detail = (await resp.json()).detail || detail; } catch { /* 保留状态码 */ }
        status(`对比失败：${detail}`, 'error');
        return;
      }
      result = await resp.json();
      render();
    } catch (err) {
      status(`网络错误：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusy(false);
    }
  });
})();
