/**
 * 行情容器「市场风格」二级页。
 *
 * 数据: GET /api/market/sector_flow(板块涨跌幅 + 主力净流入),与「板块」页同源。
 * 口径:
 * - 风格分类来自固定行业标签(防守/进攻),只做归组展示,不做预测、不构成投资建议;
 * - 板块涨跌幅 / 主力净流入原样展示,缺失字段显示「—」,不按 0 计入;
 * - 风格占比条按"归入该组的板块个数"计算,不是涨跌家数,避免把两种口径混读。
 */
(function () {
  'use strict';

  var CACHE_MS = 15000;

  /* 防守型:现金流稳定、分红属性强、需求刚性
     关键词同时覆盖东财短名与证监会行业长名(如「货币金融服务」「公共设施管理业」) */
  var DEFENSIVE = [
    '银行', '货币金融', '保险', '公用事业', '电力', '燃气', '煤气', '水务', '水的生产', '自来水',
    '环保', '环境治理', '公共设施', '水利', '卫生',
    '食品', '白酒', '饮料', '农牧', '农、林、牧、渔', '种植业', '养殖业', '渔业', '林业',
    '中药', '医药商业', '化学制药', '煤炭', '石油', '钢铁', '黑色金属',
    '交通运输', '铁路', '公路', '港口', '航运', '水上运输', '航空运输', '管道运输', '仓储', '物流',
    '家用电器', '家电', '家居', '家具', '纺织', '服装',
    '商业百货', '商贸零售', '零售', '批发', '餐饮', '住宿', '旅游',
    '美容护理', '造纸', '水泥', '建材', '工程建设', '建筑', '土木', '房屋', '装修装饰',
    '房地产开发', '房地产服务', '房地产中介', '房地产', '旅馆'
  ];

  /* 进攻型:高景气成长、题材弹性大、对流动性与风险偏好敏感 */
  var OFFENSIVE = [
    '半导体', '芯片', '电子', '元件', '消费电子', '光学', '光电子', '电子化学品',
    '通信', '电信', '计算机', '软件', '互联网', 'IT服务', '信息传播',
    '游戏', '传媒', '广告包装', '文化', '广播', '影视', '新闻和出版',
    '航天', '航空', '船舶', '军工', '电池', '光伏', '风电', '电源设备', '电网设备', '电机', '电气机械',
    '能源金属', '小金属', '有色金属', '贵金属', '稀土', '金属制品',
    '汽车', '机器人', '专用设备', '通用设备', '工程机械', '自动化设备', '仪器仪表', '机械',
    '化学', '化纤', '橡胶', '非金属矿物', '金属', '生物制品', '医药', '医疗服务', '医疗器械',
    '证券', '资本市场', '多元金融', '金融', '科技', '研究', '科研'
  ];

  var state = {
    loading: false,
    loadedAt: 0,
    data: null,
    meta: null,
    error: ''
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : ''; }
  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(Number(v)); }

  function pctText(v) {
    if (!isNum(v)) return '—';
    var n = Number(v);
    return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  function flowText(v) {
    if (!isNum(v)) return '—';
    var n = Number(v);
    var sign = n > 0 ? '+' : '';
    var abs = Math.abs(n);
    if (abs >= 1e8) return sign + (n / 1e8).toFixed(2) + ' 亿';
    if (abs >= 1e4) return sign + (n / 1e4).toFixed(1) + ' 万';
    return sign + n.toFixed(0);
  }

  function clsOf(v) {
    if (!isNum(v) || Number(v) === 0) return '';
    return Number(v) > 0 ? 'is-up' : 'is-down';
  }

  function styleOf(name) {
    var s = String(name || '');
    var i;
    for (i = 0; i < DEFENSIVE.length; i++) {
      if (s.indexOf(DEFENSIVE[i]) >= 0) return 'defensive';
    }
    for (i = 0; i < OFFENSIVE.length; i++) {
      if (s.indexOf(OFFENSIVE[i]) >= 0) return 'offensive';
    }
    return 'neutral';
  }

  function sourceText(meta) {
    if (!meta) return '板块行情';
    if (meta.data_source === 'sina') return '新浪板块';
    if (meta.data_source === 'eastmoney') return '东财板块';
    return meta.data_source || '板块行情';
  }

  function renderFreshness(meta) {
    var el = $('msFreshness');
    if (!el) return;
    var asOf = meta && meta.timestamp ? String(meta.timestamp).slice(11, 19) : '';
    el.innerHTML = (typeof Freshness !== 'undefined')
      ? Freshness.html({ asOf: asOf, note: sourceText(meta) })
      : '';
  }

  function groupStats(items) {
    var up = 0, down = 0, flat = 0, unknown = 0;
    items.forEach(function (it) {
      if (!isNum(it.change_pct)) { unknown++; return; }
      var v = Number(it.change_pct);
      if (v > 0) up++;
      else if (v < 0) down++;
      else flat++;
    });
    return { up: up, down: down, flat: flat, unknown: unknown, total: items.length };
  }

  function renderRows(items, maxAbs) {
    if (!items.length) {
      return '<div class="ms-empty">该组暂无板块数据</div>';
    }
    return items.map(function (it, idx) {
      var pct = it.change_pct;
      var c = clsOf(pct);
      var width = (isNum(pct) && maxAbs > 0) ? Math.max(3, Math.min(100, Math.abs(Number(pct)) / maxAbs * 100)) : 0;
      var barCls = isNum(pct) ? c : 'is-na';
      var flow = it.main_net_inflow;
      return '<div class="ms-row sb-row-link" role="button" tabindex="0"'
        + ' data-ms-sector="' + esc(it.sector || '') + '" data-ms-code="' + esc(it.code || '') + '"'
        + ' aria-label="查看 ' + esc(it.sector || '') + ' 板块成分股">'
        + '<span class="ms-rank">' + (idx + 1) + '</span>'
        + '<span class="ms-name" title="' + esc(it.sector || '') + '">' + esc(it.sector || '—') + '</span>'
        + '<span class="ms-bar"><i class="' + barCls + '" style="width:' + width.toFixed(1) + '%"></i></span>'
        + '<span class="ms-pct ' + c + '">' + esc(pctText(pct)) + '</span>'
        + '<span class="ms-flow ' + clsOf(flow) + '">' + esc(flowText(flow)) + '</span>'
        + '</div>';
    }).join('');
  }

  function render() {
    var body = $('msBody');
    if (!body) return;
    var data = state.data;
    if (!Array.isArray(data) || !data.length) {
      body.innerHTML = '<div class="ms-empty ms-empty-lg">'
        + '<b>板块行情暂不可用</b><br>上游未返回板块涨跌与资金数据，请稍后点击「刷新」重试。'
        + (state.error ? '<br><span class="ms-err">原因：' + esc(state.error) + '</span>' : '')
        + '</div>';
      return;
    }

    var def = [], off = [], neu = [];
    data.forEach(function (it) {
      var g = styleOf(it.sector);
      if (g === 'defensive') def.push(it);
      else if (g === 'offensive') off.push(it);
      else neu.push(it);
    });

    var byPct = function (a, b) {
      if (!isNum(a.change_pct)) return 1;
      if (!isNum(b.change_pct)) return -1;
      return Number(b.change_pct) - Number(a.change_pct);
    };
    def.sort(byPct);
    off.sort(byPct);

    var maxAbs = 0;
    data.forEach(function (it) {
      if (isNum(it.change_pct)) maxAbs = Math.max(maxAbs, Math.abs(Number(it.change_pct)));
    });

    var ds = groupStats(def), os = groupStats(off), ns = groupStats(neu);
    var totalClassified = def.length + off.length;
    var defPct = totalClassified ? def.length / totalClassified * 100 : 0;

    var summary = '<div class="ms-summary">'
      + '<div class="ms-ratio" role="img" aria-label="防守型 ' + def.length + ' 个板块，进攻型 ' + off.length + ' 个板块">'
      + '<span class="ms-ratio-def" style="flex:' + (def.length || 0.0001) + ';"></span>'
      + '<span class="ms-ratio-off" style="flex:' + (off.length || 0.0001) + ';"></span>'
      + '</div>'
      + '<div class="ms-ratio-legend">'
      + '<span><i class="ms-dot is-def"></i>防守型 <b>' + def.length + '</b> 个 · ' + defPct.toFixed(0) + '%</span>'
      + '<span><i class="ms-dot is-off"></i>进攻型 <b>' + off.length + '</b> 个 · ' + (100 - defPct).toFixed(0) + '%</span>'
      + '<span class="ms-neutral-note">未分类 ' + neu.length + ' 个（不参与占比）</span>'
      + '</div>'
      + '</div>';

    var defCol = '<section class="ms-col" aria-label="防守型板块">'
      + '<header class="ms-col-head"><span class="ms-col-title is-def">防守型</span>'
      + '<span class="ms-col-sub">上涨 ' + ds.up + ' · 下跌 ' + ds.down + (ds.unknown ? ' · 缺失 ' + ds.unknown : '') + '</span></header>'
      + '<div class="ms-list">' + renderRows(def, maxAbs) + '</div>'
      + '</section>';

    var offCol = '<section class="ms-col" aria-label="进攻型板块">'
      + '<header class="ms-col-head"><span class="ms-col-title is-off">进攻型</span>'
      + '<span class="ms-col-sub">上涨 ' + os.up + ' · 下跌 ' + os.down + (os.unknown ? ' · 缺失 ' + os.unknown : '') + '</span></header>'
      + '<div class="ms-list">' + renderRows(off, maxAbs) + '</div>'
      + '</section>';

    var neutral = '';
    if (neu.length) {
      var names = neu.map(function (it) { return esc(it.sector || '—'); }).join('、');
      neutral = '<div class="ms-neutral">未分类板块（' + neu.length + '）：' + names
        + (ns.unknown ? '；其中 ' + ns.unknown + ' 个涨跌幅缺失' : '') + '</div>';
    }

    body.innerHTML = summary
      + '<div class="ms-cols">' + defCol + offCol + '</div>'
      + neutral
      + '<div class="ms-foot">口径：风格分类为固定行业标签，仅做当日归组对照；涨跌幅与主力净流入为板块口径，'
      + '缺失显示「—」，不按 0 计入；点击任一板块可查看成分股。数据 ' + esc(sourceText(state.meta))
      + (state.meta && state.meta.timestamp ? ' · 截至 ' + esc(String(state.meta.timestamp).slice(11, 19)) : '')
      + '。规则展示，不构成投资建议。</div>';

    renderFreshness(state.meta);
  }

  async function load(force) {
    if (state.loading) return;
    if (!force && state.data && Date.now() - state.loadedAt < CACHE_MS) {
      render();
      return;
    }
    state.loading = true;
    var body = $('msBody');
    if (body && !state.data) body.innerHTML = '<div class="ms-empty ms-empty-lg">加载板块行情…</div>';
    try {
      var resp = await api('/api/market/sector_flow' + (force ? '?_t=' + Date.now() : ''));
      state.data = (resp && Array.isArray(resp.data)) ? resp.data : null;
      state.meta = (resp && resp._meta) ? resp._meta : null;
      state.error = '';
      state.loadedAt = Date.now();
      render();
    } catch (e) {
      state.error = String(e && e.message ? e.message : e);
      render();
    } finally {
      state.loading = false;
    }
  }

  function onRowActivate(ev) {
    var row = ev && ev.target ? ev.target.closest('.ms-row') : null;
    if (!row) return;
    if (ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
    if (ev.type === 'keydown') ev.preventDefault();
    if (typeof SectorBoard !== 'undefined' && typeof SectorBoard.openSector === 'function') {
      SectorBoard.openSector(row.getAttribute('data-ms-sector') || '', row.getAttribute('data-ms-code') || '');
    }
  }

  function bind() {
    var body = $('msBody');
    if (!body || body.dataset.msBound === '1') return;
    body.dataset.msBound = '1';
    body.addEventListener('click', onRowActivate);
    body.addEventListener('keydown', onRowActivate);
  }

  function maybeAutoLoad() {
    var page = $('page-market-style');
    if (page && page.classList.contains('active')) {
      bind();
      load(false);
    }
  }

  function wrapSwitchPage() {
    if (typeof window.switchPage !== 'function' || window.__marketStyleWrapped) return;
    window.__marketStyleWrapped = true;
    var orig = window.switchPage;
    window.switchPage = function (name) {
      var out = orig.apply(this, arguments);
      if (name === 'market-style') {
        try { bind(); load(false); } catch (e) { /* ignore */ }
      }
      return out;
    };
  }

  function init() {
    wrapSwitchPage();
    maybeAutoLoad();
  }

  window.MarketStyle = { load: load };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  window.addEventListener('hashchange', maybeAutoLoad);
})();
