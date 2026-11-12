(() => {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const form = byId('researchForm');
  const maxBytes = 2_000_000;
  let report = null;
  let sourceName = '粘贴的历史数据';
  let busy = false;
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const number = (value, digits = 4) => finite(value) ? value.toFixed(digits) : '—';
  const percent = (value) => finite(value) ? `${(value * 100).toFixed(1)}%` : '—';
  const percentagePoints = (value) => finite(value) ? `${value.toFixed(3)}%` : '—';
  const segmentNames = { train: '研究段', purged: '隔离标签', test: '测试段', unlabelled: '标签未到期' };

  function status(text, kind = '') {
    byId('status').textContent = text;
    byId('status').className = kind;
  }

  function invalidate() {
    report = null;
    byId('report').hidden = true;
    byId('downloadReport').disabled = true;
    status('输入已更新，请重新运行检验。');
  }

  function setBusy(value) {
    busy = value;
    form.querySelectorAll('input, select, textarea, button').forEach((element) => {
      element.disabled = value;
    });
    byId('downloadReport').disabled = value || !report;
    byId('runResearch').textContent = value ? '正在计算…' : '运行因子检验';
    form.setAttribute('aria-busy', String(value));
  }

  function download(content, filename, mime) {
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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

  function svgElement(tag, attributes, text) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function drawIC(rows) {
    const svg = byId('icChart');
    svg.replaceChildren();
    for (const value of [-1, 0, 1]) {
      const y = 105 - value * 80;
      svg.append(svgElement('line', { x1: 36, x2: 585, y1: y, y2: y, stroke: 'var(--qf-line-2)', 'stroke-dasharray': value === 0 ? '0' : '3 4' }));
      svg.append(svgElement('text', { x: 7, y: y + 4, fill: 'var(--qf-text-secondary)', 'font-size': 12 }, String(value)));
    }
    let path = '';
    let connected = false;
    let count = 0;
    rows.forEach((row, index) => {
      if (!finite(row.rank_ic)) { connected = false; return; }
      const x = rows.length === 1 ? 310 : 36 + index / (rows.length - 1) * 549;
      const y = 105 - row.rank_ic * 80;
      path += `${connected ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `;
      connected = true;
      count += 1;
      const point = svgElement('circle', { cx: x, cy: y, r: 2, fill: 'var(--qf-neon-cyan)' });
      point.append(svgElement('title', {}, `${row.date} · Rank IC ${number(row.rank_ic)}`));
      svg.append(point);
    });
    svg.append(svgElement('path', { d: path, fill: 'none', stroke: 'var(--qf-neon-cyan)', 'stroke-width': 1.5 }));
    if (rows.length) {
      svg.append(svgElement('text', { x: 36, y: 215, fill: 'var(--qf-text-secondary)', 'font-size': 12 }, rows[0].date));
      svg.append(svgElement('text', { x: 585, y: 215, 'text-anchor': 'end', fill: 'var(--qf-text-secondary)', 'font-size': 12 }, rows[rows.length - 1].date));
    }
    byId('chartInfo').textContent = count ? `${count} 个有效 IC 日期；缺失或常数截面不连线。` : '本段没有有效 IC：需足够股票、完整标签且横截面存在差异。';
  }

  function drawGroups(summary) {
    const chart = byId('groupChart');
    chart.replaceChildren();
    const maximum = Math.max(...summary.group_returns_pct.filter(finite).map(Math.abs), 0.0001);
    summary.group_returns_pct.forEach((value, index) => {
      const row = document.createElement('div');
      row.className = 'group-row';
      row.title = `${summary.group_counts[index]} 个有效股票日观测`;
      const label = document.createElement('span');
      label.textContent = `G${index + 1}`;
      const track = document.createElement('div');
      track.className = 'group-track';
      const bar = document.createElement('div');
      bar.className = 'group-bar';
      bar.style.width = finite(value) ? `${Math.abs(value) / maximum * 100}%` : '0%';
      if (value < 0) bar.style.background = 'var(--qf-sem-down)';
      track.append(bar);
      const output = document.createElement('output');
      output.textContent = percentagePoints(value);
      row.append(label, track, output);
      chart.append(row);
    });
  }

  function render() {
    if (!report) return;
    const data = report.data;
    const segment = byId('segment').value;
    const summary = data.summary[segment];
    const rows = data.daily.filter((row) => segment === 'all' || row.segment === segment);
    const coverage = data.coverage;
    byId('coverage').textContent = `${report.source} · ${coverage.rows} 行 / ${coverage.dates} 个日期 / ${coverage.stocks} 只股票；面板缺格 ${coverage.missing_cells}。候选观测 ${coverage.eligible_observations}，收益可配对 ${coverage.paired_observations}，远期收益缺失 ${coverage.missing_forward_observations}。`;
    const split = data.split;
    const unlabelled = data.daily.filter((row) => row.segment === 'unlabelled').length;
    byId('splitInfo').textContent = `测试分界 ${split.cutoff_date || '—'} · 研究段 ${split.train_days} 日 / 隔离 ${split.purged_days} 日 / 测试段 ${split.test_days} 日。跨越测试分界的前段标签已剔除；末尾 ${unlabelled} 日标签未到期。分段天数包含预热日期。`;
    byId('metrics').replaceChildren(
      metric('平均 Rank IC', number(summary.rank_ic_mean), `${summary.days} 个有效 IC 日期`),
      metric('Rank ICIR', number(summary.rank_ic_ir), '均值 / 样本标准差，未年化'),
      metric('Rank IC 正值占比', percent(summary.rank_ic_positive_ratio), '分母仅含有效 IC 日期'),
      metric('高分组 − 低分组', number(summary.top_bottom_spread_pct, 3), '远期收益差（百分点），非交易利润')
    );
    drawIC(rows);
    drawGroups(summary);
    const size = data.size_exposure;
    byId('sizeInfo').textContent = size.available
      ? `有效市值观测 ${size.observations}，候选覆盖率 ${percent(size.coverage_ratio)}；分数与市值的平均 Rank 相关 ${number(size.rank_correlation)}；高分组中，小市值底部 20% 股票占 ${percent(size.top_group_small_cap_share)}。这是暴露诊断，不是剔除市值后的独立收益。`
      : '未获得足够的历史市值数据，无法判断小盘股暴露；不能把缺失当作无暴露。';
    byId('notes').replaceChildren(...data.notes.map((note) => {
      const item = document.createElement('li');
      item.textContent = note;
      return item;
    }));
    byId('dailyRows').replaceChildren(...rows.slice(-80).map((row) => {
      const tr = document.createElement('tr');
      for (const text of [row.date, segmentNames[row.segment], row.samples, number(row.ic), number(row.rank_ic)]) {
        const td = document.createElement('td');
        td.textContent = String(text);
        tr.append(td);
      }
      return tr;
    }));
    byId('report').hidden = false;
  }

  byId('csvFile').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    invalidate();
    byId('csvData').value = '';
    if (file.size > maxBytes) { status('CSV 文件超过 2 MB，请缩小日期范围或股票池。', 'error'); return; }
    try {
      byId('csvData').value = await file.text();
      sourceName = file.name;
      byId('fileInfo').textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
      status('CSV 已载入；请核对参数后运行。');
    } catch {
      status('无法读取文件，请选择 UTF-8 CSV 或直接粘贴内容。', 'error');
    }
  });

  form.addEventListener('input', (event) => {
    if (event.target.id === 'adminToken' || event.target.id === 'csvFile') return;
    invalidate();
    if (event.target.id === 'csvData') {
      sourceName = '粘贴或编辑的历史数据';
      byId('fileInfo').textContent = 'CSV 内容已手动编辑。';
    }
  });
  byId('segment').addEventListener('change', render);
  byId('downloadTemplate').addEventListener('click', () => download('date,stock_code,open,close,amount,adj_factor,is_st,listing_days,market_cap\n', 'alpha144-columns.csv', 'text/csv;charset=utf-8'));
  byId('downloadReport').addEventListener('click', () => {
    if (report) download(JSON.stringify(report, null, 2), 'alpha144-research.json', 'application/json');
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    if (!form.reportValidity()) {
      status('请检查标红的输入：研究参数需在规定范围内（如因子窗口 2~120、分位组数 2~10）。', 'error');
      return;
    }
    const csv = byId('csvData').value.trim();
    if (!csv) { status('请先导入历史 CSV 数据。', 'error'); return; }
    if (new TextEncoder().encode(csv).length > maxBytes) { status('CSV 内容超过 2 MB，请缩小数据范围。', 'error'); return; }
    invalidate();
    const payload = {
      csv_data: csv,
      window: Number(byId('window').value), horizon: Number(byId('horizon').value),
      groups: Number(byId('groups').value), direction: Number(byId('direction').value),
      min_listing_days: Number(byId('minListing').value), min_average_amount: Number(byId('minAmount').value),
      train_ratio: Number(byId('trainRatio').value)
    };
    const headers = { 'Content-Type': 'application/json' };
    let token = byId('adminToken').value.trim();
    if (!token) {
      try { token = localStorage.getItem('admin_token') || ''; } catch { token = ''; }
    }
    if (token) headers.Authorization = `Bearer ${token}`;
    setBusy(true);
    status('正在对齐历史面板并检验因子；首次服务启动可能较慢…');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetch('/api/strategy/factor-research', { method: 'POST', headers, body: JSON.stringify(payload), signal: controller.signal });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        if (response.status === 401) throw new Error('鉴权失败：请填写管理令牌，或返回工作台检查管理鉴权设置。');
        if (response.status === 429) throw new Error('计算请求过于频繁，请稍后重试（每分钟最多 3 次）。');
        let detail = body?.detail;
        if (Array.isArray(detail)) detail = detail.map((item) => `${item.loc?.slice(1).join('.') || '输入'}：${item.msg}`).join('；');
        if (detail && typeof detail === 'object') detail = detail.message;
        throw new Error(typeof detail === 'string' ? detail : `服务返回 HTTP ${response.status}，未生成报告。`);
      }
      if (!body?.data?.summary || !Array.isArray(body.data.daily)) throw new Error('服务返回的报告格式不完整，未展示结果。');
      report = { source: sourceName, generated_at: new Date().toISOString(), data: body.data };
      byId('segment').value = 'test';
      render();
      status(body.data.available ? '因子检验完成。默认展示时间留出测试段；请同时检查覆盖率与研究边界。' : body.data.message || '数据不足，未形成有效横截面检验。', body.data.available ? 'success' : 'error');
    } catch (error) {
      status(error.name === 'AbortError' ? '等待服务超过 120 秒，未取得报告；请稍后重试。' : `检验未完成：${error.message}`, 'error');
    } finally {
      clearTimeout(timeout);
      setBusy(false);
    }
  });
})();
