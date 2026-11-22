/**
 * 国家队 / 机构持仓（十大流通股东）前端。
 *
 * 数据: GET /api/market/institution_holdings?code=
 * 口径: 分类是规则近似，同时展示原始股东类型；比例口径为占流通股本。
 */
(function () {
  'use strict';

  var CACHE_MS = 60000;
  var state = { key: '', loadedAt: 0, data: null, loading: false, seq: 0 };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : String(s == null ? '' : s); }
  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(Number(v)); }

  function codeValue() {
    var el = $('ihCode');
    return (el && el.value.trim()) || '600519';
  }

  function fmtShares(v) {
    if (!isNum(v)) return '—';
    var n = Number(v);
    var abs = Math.abs(n);
    var sign = n > 0 ? '+' : (n < 0 ? '-' : '');
    if (abs >= 1e8) return sign + (abs / 1e8).toFixed(2) + ' 亿股';
    if (abs >= 1e4) return sign + (abs / 1e4).toFixed(1) + ' 万股';
    return sign + abs.toFixed(0) + ' 股';
  }

  function fmtRatio(v) {
    return isNum(v) ? Number(v).toFixed(2) + '%' : '—';
  }

  function catClass(c) {
    if (c === '国家队') return 'is-nation';
    if (c === '机构') return 'is-inst';
    if (c === '外资') return 'is-foreign';
    if (c === '产业资本') return 'is-industry';
    if (c === '个人') return 'is-person';
    return 'is-other';
  }

  function render(data) {
    var body = $('ihBody');
    if (!body) return;
    if (!data) {
      body.innerHTML = '<div class="ih-empty">持仓数据暂不可用，请稍后重试。</div>';
      return;
    }
    if (!data.available) {
      body.innerHTML = '<div class="ih-empty is-warn"><b>暂无持仓结构数据</b><br>'
        + esc(data.reason || '可能尚未披露或上游未返回') + '<br>'
        + '<span class="ih-note">' + esc(data.note || '') + '</span></div>';
      return;
    }

    var s = data.summary || {};
    var cats = s.categories || {};
    var ch = s.changes || {};
    var order = ['国家队', '机构', '外资', '产业资本', '个人', '其他'];

    var head = '<div class="ih-head">'
      + '<span class="ih-date">报告期 ' + esc(data.end_date || '—') + '</span>'
      + '<span class="ih-total">十大合计 <b>' + fmtRatio(s.total_ratio) + '</b></span>'
      + '<span class="ih-chg">新进 <b>' + (ch.new || 0) + '</b> · 增持 <b class="is-up">' + (ch.increase || 0) + '</b>'
      + ' · 减持 <b class="is-down">' + (ch.decrease || 0) + '</b> · 不变 <b>' + (ch.flat || 0) + '</b></span>'
      + '</div>';

    var catCards = order.map(function (c) {
      var v = cats[c] || {};
      return '<div class="ih-cat ' + catClass(c) + '">'
        + '<div class="ih-cat-k">' + esc(c) + '</div>'
        + '<div class="ih-cat-v">' + fmtRatio(v.ratio) + '</div>'
        + '<div class="ih-cat-n">' + (v.count || 0) + ' 家</div>'
        + '</div>';
    }).join('');

    var rows = (data.holders || []).map(function (h) {
      var chgCls = h.change_kind === 'increase' ? 'is-up' : (h.change_kind === 'decrease' ? 'is-down' : '');
      var chg = h.change_shares != null
        ? (h.change_label + ' ' + fmtShares(h.change_shares))
        : h.change_label;
      return '<tr>'
        + '<td class="ih-rank">' + esc(String(h.rank || '—')) + '</td>'
        + '<td class="ih-name" title="' + esc(h.name) + '">' + esc(h.name) + '</td>'
        + '<td><span class="ih-badge ' + catClass(h.category) + '">' + esc(h.category) + '</span></td>'
        + '<td class="ih-type">' + esc(h.holder_type || '—') + '</td>'
        + '<td class="ih-num">' + (h.hold_num != null ? esc(fmtShares(h.hold_num).replace('+', '')) : '—') + '</td>'
        + '<td class="ih-num">' + fmtRatio(h.free_ratio) + '</td>'
        + '<td class="ih-num ' + chgCls + '">' + esc(chg) + '</td>'
        + '</tr>';
    }).join('');

    var top = (s.top_changes || []).length
      ? '<div class="ih-top">变动最大：' + s.top_changes.map(function (x) {
        return esc(x.name) + '（' + esc(x.category) + '，' + esc(x.change_label + ' ' + fmtShares(x.change_shares)) + '）';
      }).join('；') + '</div>'
      : '';

    body.innerHTML = head
      + '<div class="ih-cats">' + catCards + '</div>'
      + top
      + '<div class="table-wrap" tabindex="0"><table class="ih-table"><thead><tr><th>#</th><th>股东名称</th><th>归类</th><th>原始类型</th><th>持股数</th><th>占流通</th><th>变动</th></tr></thead><tbody>'
      + rows + '</tbody></table></div>'
      + '<div class="ih-foot">' + esc(data.note || '') + '</div>';
  }

  async function load(force) {
    if (state.loading) return;
    var code = codeValue();
    if (!/^\d{6}$/.test(code)) {
      var b0 = $('ihBody');
      if (b0) b0.innerHTML = '<div class="ih-empty is-warn">请输入 6 位股票代码。</div>';
      return;
    }
    if (!force && state.data && state.key === code && Date.now() - state.loadedAt < CACHE_MS) {
      render(state.data);
      return;
    }
    state.loading = true;
    var seq = ++state.seq;
    var body = $('ihBody');
    if (body) body.innerHTML = '<div class="ih-empty">正在拉取十大流通股东…</div>';
    try {
      var resp = await api('/api/market/institution_holdings?code=' + encodeURIComponent(code));
      if (seq !== state.seq) return;
      state.data = (resp && resp.data) ? resp.data : null;
      state.key = code;
      state.loadedAt = Date.now();
      render(state.data);
    } catch (e) {
      if (seq !== state.seq) return;
      if (body) {
        body.innerHTML = '<div class="ih-empty is-warn"><b>查询失败</b><br>'
          + esc(e && e.message ? e.message : '请求失败')
          + '<br><span class="ih-note">缺失时不补 0、不伪造股东。</span></div>';
      }
    } finally {
      if (seq === state.seq) state.loading = false;
    }
  }

  function init() {
    var body = $('ihBody');
    if (!body) return;
    var codeEl = $('ihCode');
    if (codeEl && !codeEl.dataset.ihBound) {
      codeEl.dataset.ihBound = '1';
      codeEl.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') load(true); });
    }
  }

  window.InstitutionHoldings = { load: load };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
