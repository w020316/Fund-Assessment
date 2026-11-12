(() => {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const form = byId('optimizerForm');
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
    status('输入已更新，请重新运行优化。');
  }

  function setBusy(value) {
    busy = value;
    form.querySelectorAll('input, button').forEach((element) => { element.disabled = value; });
    byId('runOptimizer').textContent = value ? '正在回测网格…' : '运行参数优化';
    form.setAttribute('aria-busy', String(value));
  }

  function irColor(ir, maxAbs) {
    if (!finite(ir) || maxAbs <= 0) return '';
    const alpha = Math.min(Math.abs(ir) / maxAbs, 1) * 0.8 + 0.06;
    return ir >= 0 ? `rgba(248,113,113,${alpha.toFixed(3)})` : `rgba(52,211,153,${alpha.toFixed(3)})`;
  }

  function cellTitle(row) {
    return `MA${row.short_window}/MA${row.long_window}\n年化收益 ${percent(row.annual_return)}\n最大回撤 ${percent(row.max_drawdown)}\n夏普 ${number(row.sharpe_ratio)}\nIR ${number(row.information_ratio)}\n交易 ${row.trade_count} 次`;
  }

  function metric(label, value, description) {
    const container = document.createElement('div');
    container.className = 'metric';
    const heading = document.createElement('span');
    heading.textContent = label;
    const main = document.createElement('strong');
    main.textContent = value;
    const caption = document.createElement('span');
    caption.textContent = description;
    container.append(heading, main, caption);
    return container;
  }

  function render() {
    if (!result) return;
    const data = result.data;
    const grid = Array.isArray(data.grid) ? data.grid : [];
    byId('gridInfo').textContent = `${data.stock_code} · ${data.start_date} ~ ${data.end_date} · ${grid.length} 个有效组合`;
    if (!grid.length) {
      status('网格为空：未取得该区间的历史 K 线，或参数范围内没有有效组合。', 'error');
      return;
    }

    const best = data.best || grid[0];
    byId('bestMetrics').replaceChildren(
      metric('最优组合', `MA${best.short_window}/MA${best.long_window}`, '按信息比率排序'),
      metric('最优 IR', number(best.information_ratio), '正 = 跑赢买入持有'),
      metric('最优年化收益', percent(best.annual_return), `回撤 ${percent(best.max_drawdown)}`),
      metric('最优夏普', number(best.sharpe_ratio), `${best.trade_count} 次交易`)
    );
    const warning = byId('warning');
    warning.textContent = result.warning || '';
    warning.hidden = !result.warning;

    const shorts = [...new Set(grid.map((row) => row.short_window))].sort((a, b) => a - b);
    const longs = [...new Set(grid.map((row) => row.long_window))].sort((a, b) => a - b);
    const lookup = new Map(grid.map((row) => [`${row.short_window}:${row.long_window}`, row]));
    const maxAbs = Math.max(...grid.map((row) => Math.abs(row.information_ratio)).filter(finite), 0.0001);

    const table = byId('heatmap');
    table.replaceChildren();
    const head = document.createElement('tr');
    head.append(Object.assign(document.createElement('th'), { className: 'axis', textContent: 'long \\ short' }));
    for (const s of shorts) head.append(Object.assign(document.createElement('th'), { className: 'axis', textContent: `MA${s}` }));
    table.append(head);
    for (const l of longs) {
      const tr = document.createElement('tr');
      tr.append(Object.assign(document.createElement('td'), { className: 'axis', textContent: `MA${l}` }));
      for (const s of shorts) {
        const td = document.createElement('td');
        const row = lookup.get(`${s}:${l}`);
        if (!row) { td.className = 'cell empty'; td.textContent = '—'; }
        else {
          td.className = 'cell';
          if (best && row.short_window === best.short_window && row.long_window === best.long_window) td.classList.add('best');
          td.style.background = irColor(row.information_ratio, maxAbs);
          td.textContent = number(row.information_ratio, 2);
          td.title = cellTitle(row);
        }
        tr.append(td);
      }
      table.append(tr);
    }
    byId('report').hidden = false;
    status('参数优化完成；请优先选择成片的高原区域，而不是孤立的最高点。', 'success');
  }

  form.addEventListener('input', invalidate);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    invalidate();
    const payload = {
      stock_code: byId('stockCode').value.trim(),
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
    status('正在拉取 K 线并回测参数网格；首次服务启动可能较慢…');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetch('/api/strategy/optimize-ma-cross', { method: 'POST', headers, body: JSON.stringify(payload), signal: controller.signal });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        if (response.status === 401) throw new Error('鉴权失败：请填写管理令牌，或返回工作台检查管理鉴权设置。');
        if (response.status === 429) throw new Error('计算请求过于频繁，请稍后重试（每分钟最多 3 次）。');
        if (response.status === 503 && /单飞|仍在进行/.test(body?.detail || '')) throw new Error(body.detail);
        let detail = body?.detail;
        if (Array.isArray(detail)) detail = detail.map((item) => `${item.loc?.slice(1).join('.') || '输入'}：${item.msg}`).join('；');
        if (detail && typeof detail === 'object') detail = detail.message;
        throw new Error(typeof detail === 'string' ? detail : `服务返回 HTTP ${response.status}，未取得网格结果。`);
      }
      if (!body?.data || !Array.isArray(body.data.grid)) throw new Error('服务返回的结果格式不完整，未展示热力图。');
      result = body;
      render();
    } catch (error) {
      status(error.name === 'AbortError' ? '等待服务超过 120 秒，未取得结果；请缩小日期范围后重试。' : `优化未完成：${error.message}`, 'error');
    } finally {
      clearTimeout(timeout);
      setBusy(false);
    }
  });
})();
