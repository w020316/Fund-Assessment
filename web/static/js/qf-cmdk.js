/* ============================================================================
 * STAGE8 · qf-cmdk.js —— 命令面板（Ctrl+K / ⌘K）
 * ----------------------------------------------------------------------------
 * 依赖：qf-shell.js 先加载（window.QF / window.QF_NAV）。
 * 数据源（**零新增后端接口，不依赖任何需鉴权接口**）：
 *   ① 页面：QF_NAV（本地常量 + DOM 校准，同步 0ms）
 *   ② 股票持仓：#positionBody tr
 *   ③ 基金持仓：#fundPositionBody tr
 *   ④ 自选股：#watchlistBody tr
 *   ⑤ 板块：   [data-sb-sector]（sector-board.js 已渲染的节点）
 *   ⑥ 最近访问：localStorage['qf.cmdk.recent']（≤5 条 pageId，空输入时展示）
 * 硬约束：
 *   - 结果行一律 createElement + textContent，**全文件不拼接 HTML 字符串**；
 *   - 捕获阶段拦 Ctrl+K 并 stopPropagation，避免触发存量 index.html 的
 *     「Ctrl+K → switchPage('ai') 且聚焦 #aiStockInput」；
 *   - 回滚：QF.shell.enabled === false（?shell=off）时本文件不初始化。
 * ========================================================================== */
(function () {
  'use strict';

  var QF = window.QF;
  if (!QF || !QF.shell || QF.shell.enabled === false) return;

  var RECENT_KEY = 'qf.cmdk.recent';
  var MAX_PER_GROUP = 5;
  var GROUP_ORDER = ['页面', '股票', '基金', '板块', '最近的'];

  var state = {
    open: false,
    items: [],
    activeIndex: -1,
    lastFocus: null,
    built: false,
    prefix: ''
  };

  var el = { root: null, input: null, list: null };

  /* ---- 基础工具 ---- */
  function mk(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function clear(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }
  function textOf(node) { return node ? (node.textContent || '').trim() : ''; }

  /* ---- 拼音首字母：本轮不实现 ----
     ★ 诚实标注（PRD P0-4 的"拼音首字母"匹配项未达成）：零依赖下要准确做拼音首字母
       需要一张 ~7000 字的映射表（≈300 行数据），或用 Intl 拼音排序（Node 小 ICU /
       部分浏览器行为不一致，实测不可靠）。为避免"看起来能搜、实际搜不到"的假功能，
       这里**不提供**该匹配；匹配只走 代码 / 名称 / 分组名 子串，已覆盖 3 键直达路径。
       已登记为后续增强项（见交付报告"偏差清单"）。 */

  /* ---- 数据源 ---- */
  function navPages() {
    var nav = window.QF_NAV;
    var out = [];
    if (!nav || !nav.groups) return out;
    nav.groups.forEach(function (g) {
      out.push({ kind: 'group', id: g.id, label: g.label, meta: '一级 · 默认 ' + g.defaultPage, pageId: g.defaultPage, group: '页面' });
      g.items.forEach(function (it) {
        var info = nav.byId && nav.byId[it.id];
        out.push({
          kind: 'page',
          id: it.id,
          label: it.label,
          meta: (info ? info.groupLabel : '') + (it.id === g.defaultPage ? ' · 默认落地' : ''),
          pageId: it.id,
          group: '页面'
        });
      });
    });
    return out;
  }

  function rowsFromTable(tbodyId, pageId, kind, metaPrefix) {
    var out = [];
    var body = document.getElementById(tbodyId);
    if (!body) return out;
    var rows = body.querySelectorAll('tr');
    for (var i = 0; i < rows.length; i++) {
      var tds = rows[i].children;
      if (!tds || tds.length < 2) continue;
      var code = textOf(tds[0]);
      var name = textOf(tds[1]);
      if (!code && !name) continue;
      out.push({
        kind: kind,
        id: code || name,
        label: (name || code) + (code && name ? '' : ''),
        code: code,
        name: name,
        meta: metaPrefix + ' · ' + code,
        pageId: pageId,
        group: kind === 'fund' ? '基金' : '股票'
      });
    }
    return out;
  }

  function sectorsFromDom() {
    var out = [];
    var nodes = document.querySelectorAll('[data-sb-sector]');
    var seen = {};
    for (var i = 0; i < nodes.length; i++) {
      var name = nodes[i].getAttribute('data-sb-sector') || '';
      var code = nodes[i].getAttribute('data-sb-code') || '';
      var key = name + '|' + code;
      if (!name || seen[key]) continue;
      seen[key] = 1;
      out.push({
        kind: 'sector',
        id: name,
        label: name,
        code: code,
        name: name,
        meta: '板块' + (code ? ' · ' + code : ''),
        pageId: 'sector-board',
        group: '板块'
      });
    }
    return out;
  }

  function recentItems() {
    var ids = [];
    try {
      var raw = window.localStorage.getItem(RECENT_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      if (Array.isArray(parsed)) ids = parsed;
    } catch (e) { ids = []; }
    var nav = window.QF_NAV;
    var out = [];
    ids.forEach(function (pid) {
      var info = nav && nav.byId ? nav.byId[pid] : null;
      if (!info) return;
      out.push({
        kind: 'page',
        id: info.id,
        label: info.label,
        meta: '最近访问 · ' + info.groupLabel,
        pageId: info.id,
        group: '最近的'
      });
    });
    return out;
  }

  function pushRecent(pageId) {
    if (!pageId) return;
    var ids = [];
    try {
      var raw = window.localStorage.getItem(RECENT_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      if (Array.isArray(parsed)) ids = parsed;
    } catch (e) { ids = []; }
    ids = ids.filter(function (x) { return x !== pageId; });
    ids.unshift(pageId);
    try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(ids.slice(0, MAX_PER_GROUP))); } catch (e2) { /* 忽略 */ }
  }

  function allSources() {
    var out = navPages();
    out = out.concat(rowsFromTable('positionBody', 'position', 'stock', '持仓'));
    out = out.concat(rowsFromTable('fundPositionBody', 'fund', 'fund', '基金持仓'));
    out = out.concat(rowsFromTable('watchlistBody', 'monitor', 'stock', '自选'));
    out = out.concat(sectorsFromDom());
    return out;
  }

  /* ---- 打分与过滤 ---- */
  function score(item, q) {
    if (!q) return 1;
    var needle = q.toLowerCase();
    var code = String(item.code || '').toLowerCase();
    var label = String(item.label || '').toLowerCase();
    var name = String(item.name || '').toLowerCase();
    if (code && code === needle) return 100;
    if (code && code.indexOf(needle) === 0) return 80;
    if (name && name === needle) return 70;
    if (label && label.indexOf(needle) >= 0) return 60;
    if (name && name.indexOf(needle) >= 0) return 60;
    if (String(item.group || '').toLowerCase().indexOf(needle) >= 0) return 20;
    return 0;
  }

  function query(text) {
    var raw = String(text || '').trim();
    var prefix = '';
    if (raw.charAt(0) === '/') { prefix = 'page'; raw = raw.slice(1); }
    else if (raw.charAt(0) === '>') { prefix = 'asset'; raw = raw.slice(1); }
    state.prefix = prefix;

    var pool = [];
    if (!raw) {
      pool = recentItems();
      return groupLimit(pool, raw);
    }
    pool = allSources();
    var scored = [];
    pool.forEach(function (it) {
      if (prefix === 'page' && it.group !== '页面') return;
      if (prefix === 'asset' && it.group === '页面') return;
      var s = score(it, raw);
      if (s > 0) scored.push({ item: it, s: s });
    });
    scored.sort(function (a, b) {
      if (b.s !== a.s) return b.s - a.s;
      return GROUP_ORDER.indexOf(a.item.group) - GROUP_ORDER.indexOf(b.item.group);
    });
    return groupLimit(scored.map(function (x) { return x.item; }), raw);
  }

  function groupLimit(items) {
    var counts = {};
    var out = [];
    items.forEach(function (it) {
      var g = it.group;
      counts[g] = counts[g] || 0;
      if (counts[g] >= MAX_PER_GROUP) return;
      counts[g] += 1;
      out.push(it);
    });
    /* 渲染顺序按固定分组序列 */
    out.sort(function (a, b) { return GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group); });
    return out;
  }

  /* ---- DOM 构建（打开时构建一次，之后复用） ---- */
  function build() {
    if (state.built) return;
    var root = mk('div', 'qf-cmdk');
    root.id = 'qfCmdk';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-label', '全局搜索');
    root.hidden = true;

    var scrim = mk('div', 'qf-cmdk__scrim', '');
    scrim.setAttribute('data-cmdk-close', '');
    scrim.addEventListener('click', function () { close(); });

    var box = mk('div', 'qf-cmdk__box', '');
    var inputRow = mk('div', 'qf-cmdk__input-row', '');

    var input = mk('input', 'qf-cmdk__input', '');
    input.id = 'qfCmdkInput';
    input.type = 'text';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-controls', 'qfCmdkList');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-activedescendant', '');
    input.placeholder = '搜索页面 / 基金 / 股票 / 板块…';
    input.addEventListener('input', function () { render(input.value); });

    var esc = mk('kbd', 'qf-cmdk__esc', 'Esc');
    inputRow.appendChild(input);
    inputRow.appendChild(esc);

    var list = mk('div', 'qf-cmdk__list', '');
    list.id = 'qfCmdkList';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', '搜索结果');

    var foot = mk('div', 'qf-cmdk__foot', '');
    [['↑', '↓', ' 移动'], ['Enter', '', ' 直达'], ['Esc', '', ' 关闭']].forEach(function (spec) {
      var span = mk('span', '', '');
      span.appendChild(mk('kbd', '', spec[0]));
      if (spec[1]) span.appendChild(mk('kbd', '', spec[1]));
      span.appendChild(document.createTextNode(spec[2]));
      foot.appendChild(span);
    });

    box.appendChild(inputRow);
    box.appendChild(list);
    box.appendChild(foot);
    root.appendChild(scrim);
    root.appendChild(box);
    document.body.appendChild(root);

    el.root = root;
    el.input = input;
    el.list = list;
    state.built = true;
  }

  /* ---- 渲染 ---- */
  function render(text) {
    if (!el.list) return;
    var items = query(text);
    state.items = items;
    state.activeIndex = items.length ? 0 : -1;
    clear(el.list);

    if (!items.length) {
      var empty = mk('div', 'qf-cmdk__empty', '没有匹配的页面或标的，试试输入 6 位股票代码');
      empty.setAttribute('role', 'status');
      el.list.appendChild(empty);
      if (el.input) el.input.setAttribute('aria-activedescendant', '');
      return;
    }

    var lastGroup = '';
    items.forEach(function (it, idx) {
      if (it.group !== lastGroup) {
        lastGroup = it.group;
        el.list.appendChild(mk('span', 'qf-cmdk__group', it.group));
      }
      var option = mk('button', 'qf-cmdk__item', '');
      option.type = 'button';
      option.id = 'qfCmdkItem-' + idx;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', idx === 0 ? 'true' : 'false');
      option.appendChild(mk('span', 'qf-cmdk__item-main', it.label));
      if (it.meta) option.appendChild(mk('span', 'qf-cmdk__item-meta', it.meta));
      option.addEventListener('click', function () {
        state.activeIndex = idx;
        commit();
      });
      option.addEventListener('mousemove', function () { setActive(idx); });
      el.list.appendChild(option);
    });
    setActive(0, true);
  }

  function setActive(idx, silent) {
    var nodes = el.list ? el.list.querySelectorAll('[role="option"]') : [];
    if (!nodes.length) return;
    if (idx < 0) idx = nodes.length - 1;
    if (idx >= nodes.length) idx = 0;
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].setAttribute('aria-selected', i === idx ? 'true' : 'false');
    }
    state.activeIndex = idx;
    if (el.input) el.input.setAttribute('aria-activedescendant', 'qfCmdkItem-' + idx);
    if (!silent && nodes[idx] && nodes[idx].scrollIntoView) {
      try { nodes[idx].scrollIntoView({ block: 'nearest' }); } catch (e) { /* 忽略 */ }
    }
  }

  function move(delta) {
    if (!state.items.length) return;
    setActive(state.activeIndex + delta);
  }

  function commit() {
    var it = state.items[state.activeIndex];
    if (!it) return;
    close();
    if (typeof window.switchPage !== 'function') return;
    window.switchPage(it.pageId || 'workbench');
    pushRecent(it.pageId);
    /* 板块/个股：复用既有抽屉/高亮逻辑（不新增代码路径），节点可能已随重渲染替换，重新查一次 */
    if (it.kind === 'sector' && it.name) {
      var row = document.querySelector('[data-sb-sector="' + cssEscape(it.name) + '"]');
      if (row && typeof row.click === 'function') {
        try { row.click(); } catch (e) { /* 忽略 */ }
      }
    }
  }

  function cssEscape(v) {
    var s = String(v == null ? '' : v);
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(s);
    return s.replace(/["\\]/g, '\\$&');
  }

  /* ---- 打开 / 关闭 ---- */
  function open(seed) {
    build();
    if (state.open) {
      if (el.input) { el.input.value = seed || ''; render(el.input.value); el.input.focus(); }
      return;
    }
    state.lastFocus = document.activeElement;
    state.open = true;
    document.body.classList.add('qf-cmdk-open');
    el.root.hidden = false;
    if (el.input) {
      el.input.value = seed || '';
      render(el.input.value);
      el.input.focus();
      try { el.input.select(); } catch (e) { /* 忽略 */ }
    }
  }

  function close() {
    if (!state.open) return;
    state.open = false;
    document.body.classList.remove('qf-cmdk-open');
    if (el.root) el.root.hidden = true;
    var back = state.lastFocus;
    state.lastFocus = null;
    if (back && typeof back.focus === 'function' && document.contains(back)) {
      try { back.focus(); } catch (e) { /* 忽略 */ }
    }
  }

  /* ---- 键盘 ---- */
  function isCmdK(e) {
    return (e.key === 'k' || e.key === 'K') && (e.ctrlKey || e.metaKey) && !e.altKey;
  }

  function onKeyDownCapture(e) {
    if (isCmdK(e)) {
      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
      /* 已打开时再按 Ctrl+K：清空输入并保持打开（US-4） */
      open('');
      return;
    }
    if (!state.open) return;
    var key = e.key;
    if (key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
      return;
    }
    if (key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); move(1); return; }
    if (key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); move(-1); return; }
    if (key === 'Home') { e.preventDefault(); e.stopPropagation(); setActive(0); return; }
    if (key === 'End') { e.preventDefault(); e.stopPropagation(); setActive(state.items.length - 1); return; }
    if (key === 'Enter') { e.preventDefault(); e.stopPropagation(); commit(); return; }
    if (key === 'Tab') {
      /* 对话框内只有一个可聚焦输入：Tab 留在 input，避免焦点逃到背景页面 */
      e.preventDefault();
      e.stopPropagation();
      if (el.input) el.input.focus();
    }
  }

  function onKeyUpCapture(e) {
    /* 双保险：部分浏览器/扩展在 keyup 阶段另有监听（R2） */
    if (isCmdK(e)) {
      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
    }
  }

  QF.cmdk = {
    init: function () {
      if (state.built && el.root) return;
      build();
      document.addEventListener('keydown', onKeyDownCapture, true);
      document.addEventListener('keyup', onKeyUpCapture, true);
    },
    open: open,
    close: close,
    query: query,
    commit: commit,
    isOpen: function () { return state.open; }
  };

  /* 自动初始化：ctxbar 的 ⌘K 按钮依赖它已就绪 */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { QF.cmdk.init(); }, { once: true });
  } else {
    QF.cmdk.init();
  }
})();
