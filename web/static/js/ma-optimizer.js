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

  // 立项 1(借鉴 quantstats): 最优组合 tear sheet —— 月度收益表 + 偏度/峰度。
  // 红线: 缺失显示缺失(—), 不填 0 冒充。
  function renderTearSheet(best) {
    const wrap = byId('tearSheet');
    const months = Array.isArray(best.monthly_returns) ? best.monthly_returns : [];
    const shapeNote = [
      best.skewness == null ? null : `偏度 ${number(best.skewness, 2)}`,
      best.kurtosis == null ? null : `峰度 ${number(best.kurtosis, 2)}（超额，正态=0）`,
    ].filter(Boolean).join(' · ');
    if (!months.length && !shapeNote) { wrap.hidden = true; return; }
    wrap.hidden = false;
    byId('tearNote').textContent = shapeNote
      ? `日收益分布形态：${shapeNote}。月度收益为自然月区间收益，跨月缺口的月份为区间观测值。`
      : '月度收益为自然月区间收益，跨月缺口的月份为区间观测值。';
    const table = byId('monthlyTable');
    table.replaceChildren();
    const head = document.createElement('tr');
    head.append(
      Object.assign(document.createElement('th'), { className: 'axis', textContent: '月份' }),
      Object.assign(document.createElement('th'), { className: 'axis', textContent: '收益' }),
    );
    table.append(head);
    for (const m of months) {
      const tr = document.createElement('tr');
      const tdMonth = document.createElement('td');
      tdMonth.className = 'axis';
      tdMonth.textContent = m.month;
      const tdRet = document.createElement('td');
      tdRet.className = 'cell';
      if (m.return_pct == null || !finite(m.return_pct)) {
        tdRet.textContent = '—';
      } else {
        tdRet.textContent = `${m.return_pct > 0 ? '+' : ''}${m.return_pct.toFixed(2)}%`;
        tdRet.style.color = m.return_pct >= 0 ? 'var(--qf-sem-up)' : 'var(--qf-sem-down)';
      }
      tr.append(tdMonth, tdRet);
      table.append(tr);
    }
  }

  // 高原识别(启发式): 最优组合所在正 IR 连通域的规模与平均 IR。
  // 红线: 服务端无 plateau 或最优 IR 非正时显示缺失(隐藏注记), 不伪造判读。
  function renderPlateauNote(plateau) {
    const note = byId('plateauNote');
    const size = plateau && typeof plateau.component_size === 'number'
      && Number.isFinite(plateau.component_size) ? plateau.component_size : 0;
    if (!size) { note.hidden = true; note.textContent = ''; return; }
    const verdict = size >= 3
      ? '连片高原，可信度相对较高'
      : (size === 1 ? '孤立尖峰，大概率是过拟合幸存者，谨慎采信' : '小片区域，可信度有限');
    note.textContent = `高原识别（启发式）：最优 MA${plateau.best_short}/MA${plateau.best_long} 所在的正 IR 连通域含 `
      + `${size} 个组合、平均 IR ${plateau.component_ir_mean}（网格内正 IR 组合共 ${plateau.grid_positive_count} 个）—— ${verdict}。`;
    note.hidden = false;
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
    renderTearSheet(best);
    renderPlateauNote(data.plateau);
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

  // 立项 2 收口: 自定义网格范围 → 显式窗口数组(空 = 服务端默认)。
  // 返回: {} = 未填写走默认; false = 填写非法(行内报错); 其余为窗口对象。
  function buildRange(minText, maxText, step) {
    if (!minText && !maxText) return null;
    if (!minText || !maxText) return false;
    const min = Number(minText);
    const max = Number(maxText);
    if (!Number.isInteger(min) || !Number.isInteger(max) || min < 2 || max > 120 || min > max) return false;
    const values = [];
    for (let v = min; v <= max; v += step) values.push(v);
    return values;
  }

  function customWindows() {
    const stepRaw = byId('longStep').value.trim();
    const step = stepRaw ? Number(stepRaw) : 5;
    if (!Number.isInteger(step) || step < 1 || step > 50) return false;
    const shorts = buildRange(byId('shortMin').value.trim(), byId('shortMax').value.trim(), 1);
    const longs = buildRange(byId('longMin').value.trim(), byId('longMax').value.trim(), step);
    if (shorts === false || longs === false) return false;
    if (shorts === null && longs === null) return {};
    const s = shorts || [];
    const l = longs || [];
    if (s.length > 60 || l.length > 60) return false;
    const pairs = s.length && l.length ? s.reduce((acc, v) => acc + l.filter((x) => x > v).length, 0) : (s.length || l.length);
    if (pairs > 2000) return false;
    const windows = {};
    if (shorts) windows.short_windows = shorts;
    if (longs) windows.long_windows = longs;
    return windows;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    if (!form.reportValidity()) {
      status('请检查标红的输入：股票代码必须是 6 位数字，日期需为有效区间（开始早于结束）。', 'error');
      return;
    }
    invalidate();
    const windows = customWindows();
    if (windows === false) {
      status('自定义网格范围有误：窗口需为 2–120 的整数、起 ≤ 止、步长 1–50，且有效组合数 ≤ 2000。', 'error');
      return;
    }
    const payload = {
      stock_code: byId('stockCode').value.trim(),
      start_date: byId('startDate').value,
      end_date: byId('endDate').value,
      ...windows,
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
