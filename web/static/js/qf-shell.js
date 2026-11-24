/* ============================================================================
 * STAGE8 · qf-shell.js —— QuantFlow Pro 终端化骨架（零框架 / 零构建 / 原生 ES5+DOM）
 * ----------------------------------------------------------------------------
 * 全局命名空间：window.QF
 *   QF.shell      boot / buildNavIndex / migrate / sync / wrapSwitchPage /
 *                 chromeHeight / pinToggle / enabled
 *   QF.flyout     侧栏 hover/focus 二级浮层（fixed，规避 overflow:hidden 裁剪）
 *   QF.indexbar   顶部指数情绪条（名称/点位/涨跌幅/成交额/箭头，不画迷你走势）
 *   QF.panel      面板三态（loading / empty / error / ready）+ QF.missing
 *   QF.table      .qf-table 增强（粘性表头样式在 CSS；排序 + MutationObserver 单次重放）
 *   QF.wb         工作台模板（localStorage qf.wb.layout.v1，坏 JSON fail-safe）
 *
 * 硬约束：
 *   ① 不引入任何依赖；不改后端；不改存量渲染函数业务逻辑（只在空/错态分支加 1~2 行）；
 *   ② 所有列表/面板渲染用 textContent / createElement，**全文件不拼接 HTML 字符串**；
 *   ③ 回滚开关：?shell=off 或 localStorage['qf.shell.off']='1' → 本文件首段直接 return，
 *      不加 body.qf-shell、不搬 DOM、不初始化任何模块（qf-cmdk.js 依据 QF.shell.enabled 跳过）。
 * ========================================================================== */
(function () {
  'use strict';

  /* ---- 0. 回滚开关（必须最先判定，早于任何 DOM 操作） ---- */
  function isShellOff() {
    try {
      if (/[?&]shell=off(&|$)/.test(window.location.search)) return true;
      if (window.localStorage && window.localStorage.getItem('qf.shell.off') === '1') return true;
    } catch (e) { /* 隐私模式/被禁用时按"未关闭"处理 */ }
    return false;
  }
  if (isShellOff()) {
    window.QF = window.QF || {};
    window.QF.shell = window.QF.shell || {};
    window.QF.shell.enabled = false;
    if (window.console && console.info) console.info('[QF] shell=off，骨架层未启用');
    return;
  }

  /* ---- 1. 基础工具 ---- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
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
  /** 只取直接子元素（避免 :scope 兼容性差异） */
  function kids(node, cls) {
    var out = [];
    if (!node || !node.children) return out;
    for (var i = 0; i < node.children.length; i++) {
      var c = node.children[i];
      if (!cls || (c.classList && c.classList.contains(cls))) out.push(c);
    }
    return out;
  }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function fmtNum(v, d) {
    if (!isNum(v)) return '—';
    return v.toFixed(typeof d === 'number' ? d : 2);
  }
  function nowClock() {
    var d = new Date();
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  /* 缺失值统一「—」+ title 说明（PRD P0-7 / US-9） */
  function missingText(reason, nextAt) {
    var r = reason || '原因：上游未返回该字段';
    var t = nextAt ? r + '（更新于 ' + nextAt + '）' : r + '（更新于 ' + nowClock() + '）';
    var span = mk('span', 'qf-missing', '—');
    span.title = t;
    span.setAttribute('aria-label', t);
    return span;
  }
  function pctCls(v) {
    if (!isNum(v)) return 'is-flat';
    if (v > 0) return 'is-up';
    if (v < 0) return 'is-down';
    return 'is-flat';
  }

  /* ---- 2. 导航数据模型 QF_NAV（20 个二级页；STAGE5_PAGE_MAP 缺 watchboard/risk/evolution） ---- */
  window.QF_NAV = window.QF_NAV || {
    version: 1,
    groups: [
      { id: 'hud-market', label: '行情', key: '1', defaultPage: 'workbench',
        items: [
          { id: 'workbench', label: '工作台' }, { id: 'market', label: '行情总览' },
          { id: 'review', label: '短线复盘' }, { id: 'global', label: '国际市场' },
          { id: 'data', label: '深度数据' }, { id: 'sector-board', label: '板块' },
          { id: 'market-style', label: '市场风格' }
        ] },
      { id: 'hud-holdings', label: '持仓', key: '2', defaultPage: 'position',
        items: [
          { id: 'position', label: '持仓管理' }, { id: 'fund', label: '基金管理' },
          { id: 'trade', label: '交易执行' }, { id: 'watchboard', label: '盯盘' },
          { id: 'risk', label: '风险' }
        ] },
      { id: 'hud-strategy', label: '策略', key: '3', defaultPage: 'strategy',
        items: [
          { id: 'strategy', label: '策略中心' }, { id: 'ai', label: '智能分析' },
          { id: 'gate', label: '出手三问' }, { id: 'dispatch', label: '一句话调度' },
          { id: 'evolution', label: '策略进化' }, { id: 'recommend', label: '今日推荐' }
        ] },
      { id: 'hud-monitor', label: '监控', key: '4', defaultPage: 'monitor',
        items: [{ id: 'monitor', label: '监控预警' }] },
      { id: 'hud-settings', label: '设置', key: '5', defaultPage: 'config',
        items: [{ id: 'config', label: '系统配置' }] }
    ],
    byId: {},
    byGroup: {}
  };

  window.QF = window.QF || {};
  var QF = window.QF;
  QF.version = 'stage8-v1';

  /* ---- 3. 模块骨架（先声明，boot 时填充） ---- */
  QF.shell = { enabled: true, booted: false, migrated: false, chromeHeight: function () { return 0; } };
  QF.flyout = {};
  QF.indexbar = {};
  QF.panel = {};
  QF.table = {};
  QF.wb = {};

  /* ==========================================================================
     4. QF.shell
     ========================================================================== */
  var NAV = window.QF_NAV;
  var pageTitles = {};   /* pageId → 页头标题（migrate 时从 .qf-page-title 读出） */
  var pageDescs = {};    /* pageId → 页头描述（作为 ctxbar 的 title 提示，信息零丢失） */

  function navGroupOf(pageId) {
    var info = NAV.byId[pageId];
    return info ? info.group : null;
  }
  function pageLabel(pageId) {
    if (pageTitles[pageId]) return pageTitles[pageId];
    var info = NAV.byId[pageId];
    return info ? info.label : pageId;
  }
  /** 一级聚合容器 id → 默认子页（与 STAGE5_GROUP_DEFAULT 同义，只读不改） */
  function resolvePageId(name) {
    var g = NAV.byGroup[name];
    return g ? g.defaultPage : name;
  }

  /**
   * 建索引：① 静态常量兜底（DOM 异常时命令面板仍可用）；
   * ② 用真实 sub-tab DOM 校准（顺序/文案以 DOM 为准，DOM 里有而常量没有的追加进来）。
   * 只读遍历，绝不写 STAGE5_PAGE_MAP、不改 DOM 结构。
   */
  function buildNavIndex() {
    NAV.byId = {};
    NAV.byGroup = {};
    NAV.groups.forEach(function (g) {
      NAV.byGroup[g.id] = g;
      NAV.byId[g.id] = { id: g.id, label: g.label, group: g.id, groupLabel: g.label, isGroup: true, defaultPage: g.defaultPage };
      g.items.forEach(function (it) {
        NAV.byId[it.id] = { id: it.id, label: it.label, group: g.id, groupLabel: g.label, isGroup: false };
      });
    });
    /* DOM 校准：以 .page > .hud-group-header .hud-subtab[data-page] 的实际顺序为准 */
    $$('.page > .hud-group-header .hud-subtab[data-page]').forEach(function (tab) {
      var pid = tab.getAttribute('data-page');
      if (!pid) return;
      var groupEl = tab.closest('.page');
      var gid = groupEl ? String(groupEl.id || '').replace(/^page-/, '') : '';
      var g = NAV.byGroup[gid];
      if (!g) return;
      var label = (tab.textContent || '').trim() || pid;
      var exists = null;
      g.items.forEach(function (it) { if (it.id === pid) exists = it; });
      if (exists) { exists.label = label; }
      else { g.items.push({ id: pid, label: label }); }
      if (!NAV.byId[pid]) {
        NAV.byId[pid] = { id: pid, label: label, group: gid, groupLabel: g.label, isGroup: false };
      } else {
        NAV.byId[pid].label = label;
      }
    });
  }

  /* ---- 4.1 上下文条（sticky 36px，二级 tab 常驻） ---- */
  function buildCtxbar() {
    var content = $('#mainContent') || $('.content');
    if (!content) return null;
    var bar = mk('div', 'qf-ctxbar');
    bar.id = 'qfCtxbar';
    bar.setAttribute('role', 'region');
    bar.setAttribute('aria-label', '页面上下文');

    var idBox = mk('div', 'qf-ctxbar__id');
    var groupEl = mk('span', 'qf-ctxbar__group', '');
    groupEl.id = 'qfCtxGroup';
    var sep = mk('span', 'qf-ctxbar__sep', '/');
    var pageEl = mk('span', 'qf-ctxbar__page', '');
    pageEl.id = 'qfCtxPage';
    idBox.appendChild(groupEl);
    idBox.appendChild(sep);
    idBox.appendChild(pageEl);

    var tabs = mk('div', 'qf-ctxbar__tabs');
    tabs.id = 'qfCtxTabs';
    var ops = mk('div', 'qf-ctxbar__ops');
    ops.id = 'qfCtxOps';

    var kbd = mk('button', 'qf-ctxbar__kbd', '⌘K');
    kbd.id = 'qfCtxCmdk';
    kbd.type = 'button';
    kbd.setAttribute('aria-label', '打开命令面板（Ctrl+K）');
    kbd.addEventListener('click', function () {
      if (QF.cmdk && typeof QF.cmdk.open === 'function') QF.cmdk.open();
    });

    bar.appendChild(idBox);
    bar.appendChild(tabs);
    bar.appendChild(ops);
    bar.appendChild(kbd);
    content.insertBefore(bar, content.firstChild);
    return bar;
  }

  function slotIn(container, attr, key) {
    if (!container || !key) return null;
    var found = container.querySelector('[' + attr + '="' + key + '"]');
    if (found) return found;
    var s = mk('div', 'qf-ctx-slot', '');
    s.setAttribute(attr, key);
    container.appendChild(s);
    return s;
  }

  /**
   * 一次性把二级 tab / 页面操作按钮搬进 ctxbar（move 不是 clone，id 不重复）。
   * 顺序：① 搬 actions/subtabs → ② 数量断言 → ③ 才 remove .qf-page-head / 空 .hud-group-header
   * 断言失败：整体放弃 remove（DOM 仍可用），console.error 记录。
   */
  function migrate() {
    var bar = $('#qfCtxbar');
    if (!bar) return false;
    var tabs = $('#qfCtxTabs');
    var ops = $('#qfCtxOps');
    var before = {
      actions: $$('.qf-page-actions').length,
      subtabs: $$('.hud-subtab').length,
      windowTabs: $$('.st6-window-tab').length
    };
    var plan = [];
    var heads = [];

    /* A) .page > .hud-group-header：tabs → ctxbar tabs，其余子节点 → ops */
    $$('.page > .hud-group-header').forEach(function (hdr) {
      var pageEl = hdr.parentElement;
      var pageId = String((pageEl && pageEl.id) || '').replace(/^page-/, '');
      if (!pageId) return;
      var isGroup = !!NAV.byGroup[pageId];
      var groupKey = isGroup ? pageId : null;
      var subtabs = hdr.querySelector('.hud-subtabs');
      var windowTabs = hdr.querySelector('.st6-window-tabs');
      if (subtabs) {
        plan.push({ node: subtabs, target: slotIn(tabs, groupKey ? 'data-qf-slot-group' : 'data-qf-slot-page', groupKey || pageId) });
      } else if (windowTabs) {
        plan.push({ node: windowTabs, target: slotIn(tabs, groupKey ? 'data-qf-slot-group' : 'data-qf-slot-page', groupKey || pageId) });
      }
      Array.prototype.slice.call(hdr.children || []).forEach(function (child) {
        if (child === subtabs || child === windowTabs) return;
        plan.push({ node: child, target: slotIn(ops, groupKey ? 'data-qf-slot-group' : 'data-qf-slot-page', groupKey || pageId) });
      });
    });

    /* B) .page > .qf-page-head：actions → ops；标题/描述 → ctxbar（文本保留，信息不丢失） */
    $$('.page > .qf-page-head').forEach(function (head) {
      var pageEl = head.parentElement;
      var pageId = String((pageEl && pageEl.id) || '').replace(/^page-/, '');
      if (!pageId) return;
      var actions = head.querySelector('.qf-page-actions');
      if (actions) plan.push({ node: actions, target: slotIn(ops, 'data-qf-slot-page', pageId) });
      var titleEl = head.querySelector('.qf-page-title');
      var subEl = head.querySelector('.qf-page-sub');
      if (titleEl) pageTitles[pageId] = (titleEl.textContent || '').trim();
      if (subEl) pageDescs[pageId] = (subEl.textContent || '').trim();
      heads.push(head);
    });

    /* 预校验（S9-06）：任何一步不可用就整体放弃——不得先搬后判留下"节点已离开
       原位、页头未删"的半搬态。全部可用才执行搬移；搬完的数量断言保留作兜底。 */
    for (var i = 0; i < plan.length; i++) {
      var step = plan[i];
      if (!step.node || !step.target || !document.contains(step.node)) {
        if (window.console && console.error) console.error('[QF] migrate 放弃：节点不可用', step);
        return false;
      }
    }
    for (var j = 0; j < plan.length; j++) {
      plan[j].target.appendChild(plan[j].node);
    }

    /* 数量断言：搬移不得让节点消失（move 语义下数量必须一致） */
    var after = {
      actions: $$('.qf-page-actions').length,
      subtabs: $$('.hud-subtab').length,
      windowTabs: $$('.st6-window-tab').length
    };
    if (after.actions !== before.actions || after.subtabs !== before.subtabs || after.windowTabs !== before.windowTabs) {
      if (window.console && console.error) {
        console.error('[QF] migrate 数量断言失败，已放弃 remove 页头（DOM 保持可用）', before, after);
      }
      return false;
    }

    /* 断言通过后才 remove：页头整块 + 搬空的 .hud-group-header */
    heads.forEach(function (h) { if (h && h.parentNode) h.parentNode.removeChild(h); });
    $$('.page > .hud-group-header').forEach(function (hdr) {
      if (!hdr.children.length) { hdr.parentNode.removeChild(hdr); }
      else { hdr.parentNode.removeChild(hdr); }   /* 子节点已全部搬走，只剩空白文本 */
    });
    /* 标题回填到导航索引（命令面板与 ctxbar 复用同一文案） */
    Object.keys(pageTitles).forEach(function (pid) {
      if (NAV.byId[pid]) NAV.byId[pid].label = pageTitles[pid];
    });
    return true;
  }

  /* ---- 4.2 sync：每次 switchPage 后同步 ctxbar ---- */
  function sync(pageId) {
    var bar = $('#qfCtxbar');
    if (!bar) return;
    var pid = resolvePageId(pageId);
    var groupId = navGroupOf(pid) || pid;
    $$('#qfCtxTabs > [data-qf-slot-group], #qfCtxTabs > [data-qf-slot-page], #qfCtxOps > [data-qf-slot-group], #qfCtxOps > [data-qf-slot-page]')
      .forEach(function (slot) {
        var g = slot.getAttribute('data-qf-slot-group');
        var p = slot.getAttribute('data-qf-slot-page');
        var on = (!!g && g === groupId) || (!!p && p === pid);
        slot.classList.toggle('is-active', on);
      });
    var groupEl = $('#qfCtxGroup');
    var pageEl = $('#qfCtxPage');
    if (groupEl) {
      var g2 = NAV.byGroup[groupId];
      groupEl.textContent = g2 ? g2.label : '';
    }
    if (pageEl) {
      pageEl.textContent = pageLabel(pid);
      pageEl.title = pageDescs[pid] || '';
    }
    /* 指数条：标出与当前上下文相关的项（不做网络请求） */
    if (QF.indexbar && QF.indexbar.markActive) QF.indexbar.markActive(pid);
  }

  /* ---- 4.3 包装 switchPage（必须是链上最外层：本文件最后加载） ---- */
  function wrapSwitchPage() {
    var orig = window.switchPage;
    if (typeof orig !== 'function') {
      if (window.console && console.error) console.error('[QF] 未找到 switchPage，跳过包装');
      return false;
    }
    if (orig.__qfWrapped) return true;
    var wrapped = function (name) {
      var r = orig.apply(this, arguments);
      try { sync(name); } catch (e) { if (window.console) console.warn('[QF] sync 失败', e); }
      return r;
    };
    wrapped.__qfWrapped = true;
    wrapped.__qfOrig = orig;  /* unboot 按此还原原生 switchPage（S9-05） */
    wrapped.__qfSync = sync;
    window.switchPage = wrapped;
    return true;
  }

  /* ---- 4.4 钉住 ---- */
  function readPinned() {
    try { return window.localStorage.getItem('qf.nav.pinned') === '1'; } catch (e) { return false; }
  }
  function applyPinned(on) {
    document.body.classList.toggle('qf-nav-pinned', !!on);
    var btn = $('#qfRailPin');
    if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
  function pinToggle(on) {
    applyPinned(on);
    try { window.localStorage.setItem('qf.nav.pinned', on ? '1' : '0'); } catch (e) { /* 忽略 */ }
  }
  function injectPinButton() {
    var sidebar = $('nav.sidebar') || $('.sidebar');
    if (!sidebar || $('#qfRailPin')) return;
    var btn = mk('button', 'qf-rail-pin', '');
    btn.id = 'qfRailPin';
    btn.type = 'button';
    btn.setAttribute('aria-pressed', 'false');
    btn.setAttribute('aria-label', '钉住侧栏');
    btn.title = '钉住侧栏（56 ↔ 168）';
    btn.appendChild(mk('span', 'qf-rail-pin__grip', ''));
    btn.appendChild(mk('span', 'qf-rail-pin__text', '钉住'));
    btn.addEventListener('click', function () {
      pinToggle(!document.body.classList.contains('qf-nav-pinned'));
    });
    sidebar.appendChild(btn);
  }

  /* ---- 4.5 chrome 高度（QA 唯一度量口径） ---- */
  function chromeHeight() {
    var top = $('.topbar');
    var bar = $('#qfCtxbar');
    var h = 0;
    if (top) h += top.getBoundingClientRect().height;
    if (bar && bar.offsetParent !== null) h += bar.getBoundingClientRect().height;
    return Math.round(h);
  }

  QF.shell.buildNavIndex = buildNavIndex;
  QF.shell.migrate = migrate;
  QF.shell.sync = sync;
  QF.shell.wrapSwitchPage = wrapSwitchPage;
  QF.shell.chromeHeight = chromeHeight;
  QF.shell.pinToggle = pinToggle;
  QF.shell.pageLabel = pageLabel;
  QF.shell.resolvePageId = resolvePageId;

  QF.shell.boot = function () {
    if (QF.shell.booted) return;
    document.body.classList.add('qf-shell');
    document.body.setAttribute('data-qf-shell', 'v1');
    buildNavIndex();
    buildCtxbar();
    QF.shell.migrated = migrate();
    QF.flyout.attach();
    injectPinButton();
    applyPinned(readPinned());
    wrapSwitchPage();
    QF.table.enhanceAll();
    QF.wb.init();
    QF.panel.stampAll();
    /* 首屏同步当前页（深链 #page 优先，否则默认工作台） */
    var startPage = 'workbench';
    try {
      var hash = (window.location.hash || '').replace(/^#/, '');
      if (hash && NAV.byId[resolvePageId(hash)]) startPage = hash;
    } catch (e) { /* 忽略 */ }
    sync(startPage);
    QF.shell.booted = true;
  };

  /* ==========================================================================
     5. QF.flyout —— 侧栏 hover / focus 浮层（220px，fixed 定位）
     ========================================================================== */
  var flyoutEl = null;
  var flyoutTimer = 0;
  var flyoutGroup = '';

  function ensureFlyout() {
    if (flyoutEl) return flyoutEl;
    flyoutEl = mk('div', 'qf-flyout');
    flyoutEl.id = 'qfFlyout';
    flyoutEl.hidden = true;
    flyoutEl.setAttribute('role', 'menu');
    flyoutEl.setAttribute('aria-label', '二级页面');
    flyoutEl.addEventListener('mouseenter', function () { clearTimeout(flyoutTimer); });
    flyoutEl.addEventListener('mouseleave', function () { QF.flyout.close(); });
    document.body.appendChild(flyoutEl);
    return flyoutEl;
  }

  function positionFlyout(anchorEl) {
    if (!flyoutEl || !anchorEl) return;
    var r = anchorEl.getBoundingClientRect();
    var w = 220;
    var left = r.right + 6;
    if (left + w > window.innerWidth - 8) left = Math.max(8, r.left - w - 6);
    var top = Math.max(8, Math.min(r.top, window.innerHeight - 240));
    flyoutEl.style.left = Math.round(left) + 'px';
    flyoutEl.style.top = Math.round(top) + 'px';
  }

  QF.flyout.attach = function () {
    var sidebar = $('nav.sidebar') || $('.sidebar');
    if (!sidebar) return;
    sidebar.addEventListener('mouseover', function (e) {
      var item = e.target && e.target.closest ? e.target.closest('.nav-item') : null;
      if (!item || !sidebar.contains(item)) return;
      QF.flyout.open(item.getAttribute('data-page'), item);
    });
    sidebar.addEventListener('mouseleave', function () { QF.flyout.close(); });
    sidebar.addEventListener('focusin', function (e) {
      var item = e.target && e.target.closest ? e.target.closest('.nav-item') : null;
      if (!item) return;
      QF.flyout.open(item.getAttribute('data-page'), item);
    });
    sidebar.addEventListener('focusout', function (e) {
      var to = e.relatedTarget;
      if (to && flyoutEl && (flyoutEl.contains(to) || sidebar.contains(to))) return;
      QF.flyout.close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && flyoutEl && !flyoutEl.hidden) QF.flyout.close();
    }, true);
    window.addEventListener('resize', function () { QF.flyout.close(); });
  };

  QF.flyout.open = function (groupId, anchorEl) {
    var g = NAV.byGroup[groupId];
    if (!g) return;
    var el = ensureFlyout();
    clearTimeout(flyoutTimer);
    flyoutTimer = setTimeout(function () {
      flyoutGroup = groupId;
      clear(el);
      el.appendChild(mk('span', 'qf-flyout__title', g.label + ' · 二级页'));
      var activePage = currentPageId();
      g.items.forEach(function (it) {
        var btn = mk('button', 'qf-flyout__item' + (it.id === activePage ? ' is-active' : ''), '');
        btn.type = 'button';
        btn.setAttribute('role', 'menuitem');
        btn.setAttribute('data-page', it.id);
        btn.appendChild(mk('span', '', it.label));
        btn.appendChild(mk('span', 'qf-flyout__kbd', ''));
        btn.addEventListener('click', function () {
          QF.flyout.close();
          if (typeof window.switchPage === 'function') window.switchPage(it.id);
        });
        el.appendChild(btn);
      });
      el.hidden = false;
      $$('.nav-item[aria-expanded="true"]').forEach(function (n) {
        if (n !== anchorEl) n.setAttribute('aria-expanded', 'false');
      });
      if (anchorEl) anchorEl.setAttribute('aria-expanded', 'true');
      positionFlyout(anchorEl);
    }, 150);
  };

  QF.flyout.close = function () {
    clearTimeout(flyoutTimer);
    if (!flyoutEl || flyoutEl.hidden) return;
    flyoutEl.hidden = true;
    flyoutGroup = '';
    $$('.nav-item[aria-expanded="true"]').forEach(function (n) { n.setAttribute('aria-expanded', 'false'); });
  };
  QF.flyout.position = positionFlyout;

  function currentPageId() {
    var active = $$('.page.active');
    for (var i = active.length - 1; i >= 0; i--) {
      var id = String(active[i].id || '').replace(/^page-/, '');
      if (id && !NAV.byGroup[id]) return id;
    }
    return resolvePageId('hud-market');
  }

  /* ==========================================================================
     6. QF.indexbar —— 顶部指数情绪条（数据诚实：不画任何生成的走势）
     ========================================================================== */
  QF.indexbar.render = function (barEl, items) {
    if (!barEl || !Array.isArray(items) || !items.length) return;
    var box = barEl.querySelector('#qfIndexBar');
    if (!box) {
      box = mk('div', 'qf-index-bar');
      box.id = 'qfIndexBar';
      barEl.appendChild(box);
    }
    clear(box);
    var activePage = currentPageId();
    items.forEach(function (it) {
      if (!it) return;
      var pct = it.change_pct;
      var cls = pctCls(pct);
      var card = mk('button', 'qf-idx ' + cls, '');
      card.type = 'button';
      card.setAttribute('data-index-code', String(it.code || ''));
      card.setAttribute('aria-label', (it.name || it.code || '指数') + ' ' +
        (isNum(it.price) ? fmtNum(it.price) : '点位缺失') + ' ' +
        (isNum(pct) ? (pct > 0 ? '+' : '') + fmtNum(pct) + '%' : '涨跌幅缺失'));
      if (QF.indexbar.activeCode && QF.indexbar.activeCode === String(it.code || '')) card.classList.add('is-active');
      card.appendChild(mk('span', 'qf-idx__name', it.name || it.code || '—'));

      if (isNum(it.price)) {
        card.appendChild(mk('span', 'qf-idx__price', fmtNum(it.price)));
      } else {
        card.appendChild(missingText('原因：上游未返回点位'));
      }
      if (isNum(pct)) {
        var arrow = pct > 0 ? '▲' : (pct < 0 ? '▼' : '·');
        card.appendChild(mk('span', 'qf-idx__arrow', arrow));
        card.appendChild(mk('span', 'qf-idx__pct', (pct > 0 ? '+' : '') + fmtNum(pct) + '%'));
      } else {
        card.appendChild(missingText('原因：上游未返回涨跌幅'));
      }
      if (isNum(it.amount)) {
        card.appendChild(mk('span', 'qf-idx__amt', '额 ' + fmtAmountYi(it.amount)));
      } else {
        card.appendChild(missingText('原因：上游未返回成交额'));
      }
      card.addEventListener('click', function () { QF.indexbar.jumpTo(it.code); });
      box.appendChild(card);
    });
    /* 存量节点（旧 .index-item / 占位文本）收起但不删除：loadGlobalIndexBar 会复用 #globalIndexBar */
    Array.prototype.slice.call(barEl.children).forEach(function (child) {
      if (child === box) return;
      if (child.id === 'globalIndexBar') { child.hidden = false; return; }
      if (child.classList && child.classList.contains('index-item')) { child.hidden = true; return; }
      if (child.nodeType === 1) child.hidden = true;
    });
  };

  function fmtAmountYi(v) {
    if (!isNum(v)) return '—';
    if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + '亿';
    if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + '万';
    return v.toFixed(0);
  }

  QF.indexbar.markActive = function () {
    var box = $('#qfIndexBar');
    if (!box) return;
    var code = QF.indexbar.activeCode || '';
    kids(box, 'qf-idx').forEach(function (c) {
      c.classList.toggle('is-active', !!code && c.getAttribute('data-index-code') === code);
    });
  };

  /** 整卡可点：切到工作台并高亮对应指数卡（不改变 location，不新增请求） */
  QF.indexbar.jumpTo = function (code) {
    var target = String(code || '');
    if (typeof window.switchPage === 'function') window.switchPage('workbench');
    if (!target) return;
    QF.indexbar.activeCode = target;
    var cards = $$('#wbIndexCards .wb-idx[data-index-code]');
    cards.forEach(function (c) { c.classList.remove('qf-idx-target', 'is-active'); });
    var hit = cards.filter(function (c) { return c.getAttribute('data-index-code') === target; })[0];
    if (hit) {
      hit.classList.add('qf-idx-target', 'is-active');
      try { hit.scrollIntoView({ block: 'nearest' }); } catch (e) { /* 老浏览器忽略 */ }
    }
    var bar = $('#qfIndexBar');
    if (bar) {
      kids(bar, 'qf-idx').forEach(function (c) {
        c.classList.toggle('is-active', c.getAttribute('data-index-code') === target);
      });
    }
  };

  /* ==========================================================================
     7. QF.panel —— 四态（loading / empty / error / ready）+ QF.missing
     ========================================================================== */
  function panelRoot(el) {
    if (!el) return null;
    if (el.nodeType !== 1) return null;
    return el.closest ? (el.closest('[data-qf-panel]') || el) : el;
  }
  function skeletonBlock(rows) {
    var wrap = mk('div', 'qf-panel__skel');
    for (var i = 0; i < rows; i++) wrap.appendChild(mk('span', 'qf-skel', ''));
    return wrap;
  }

  /* 三态渲染时隐藏面板原有内容：必须用内联 display 而不是 CSS 类，
     因为存量 HTML 里有大量 `style="display:flex"`，会压过任何 CSS 的 display:none。
     记录原值以便 ready 时精确还原（可逆、不破坏原结构）。 */
  var QF_PANEL_KEEP = ['qf-panel__state', 'card-title', 'qf-panel__head', 'kline-card-title', 'wb-ph'];
  function panelHideOthers(root) {
    kids(root).forEach(function (c) {
      var keep = false;
      for (var i = 0; i < QF_PANEL_KEEP.length; i++) {
        if (c.classList && c.classList.contains(QF_PANEL_KEEP[i])) { keep = true; break; }
      }
      if (keep) return;
      if (c.__qfHiddenByState == null) c.__qfHiddenByState = c.style.display || '';
      c.style.display = 'none';
    });
  }
  function panelShowAll(root) {
    kids(root).forEach(function (c) {
      if (c.__qfHiddenByState != null) {
        c.style.display = c.__qfHiddenByState;
        c.__qfHiddenByState = null;
      }
    });
  }

  QF.missing = function (value, reason, nextAt) {
    if (value !== null && value !== undefined && value !== '' && value !== '—') {
      return mk('span', '', String(value));
    }
    return missingText(reason, nextAt);
  };

  /**
   * @param {Element|string} el 面板根或其子节点（内部用 closest('[data-qf-panel]') 归一）
   * @param {'loading'|'empty'|'error'|'ready'} state
   * @param {{title?:string, detail?:string, code?:string, retry?:Function, reason?:string}} opts
   */
  QF.panel.state = function (el, state, opts) {
    var root = typeof el === 'string' ? $(el) : el;
    root = panelRoot(root);
    if (!root) return;
    var o = opts || {};
    var old = null;
    kids(root, 'qf-panel__state').forEach(function (n) { old = n; });
    if (old && old.parentNode) old.parentNode.removeChild(old);

    if (state === 'ready') {
      root.setAttribute('data-panel-state', 'ready');
      root.removeAttribute('data-empty-reason');
      var err = root.querySelector('[data-error-code]');
      if (err && !err.closest('.qf-panel__state')) err.removeAttribute('data-error-code');
      panelShowAll(root);
      try { root.__qfReadyH = Math.round(root.getBoundingClientRect().height); } catch (e) { /* 忽略 */ }
      return;
    }

    var block = mk('div', 'qf-panel__state');
    block.setAttribute('data-qf-panel-state', state);

    if (state === 'loading') {
      var prevH = root.__qfReadyH || 0;
      var headH = 32;
      var rows = root.getAttribute('data-qf-skel-rows');
      rows = rows ? parseInt(rows, 10) : Math.max(3, Math.min(12, Math.round((prevH - headH) / 24) || 3));
      block.appendChild(skeletonBlock(rows));
    } else if (state === 'empty') {
      var reason = o.reason || o.detail || '原因：上游未返回数据';
      block.appendChild(mk('span', 'qf-panel__state-title', '◻ ' + (o.title || '暂无数据')));
      var hint = mk('span', 'qf-panel__state-detail panel-empty-hint', reason);
      block.appendChild(hint);
      root.setAttribute('data-empty-reason', reason);
    } else if (state === 'error') {
      block.appendChild(mk('span', 'qf-panel__state-title', '⚠ ' + (o.title || '数据获取失败')));
      var code = mk('span', 'qf-panel__state-code', '错误码 ' + (o.code || 'UPSTREAM'));
      code.setAttribute('data-error-code', o.code || 'UPSTREAM');
      block.appendChild(code);
      if (o.detail) block.appendChild(mk('span', 'qf-panel__state-detail', o.detail));
      var actions = mk('div', 'qf-panel__state-actions');
      var retry = mk('button', 'panel-retry', '立即重试');
      retry.type = 'button';
      retry.addEventListener('click', function () {
        QF.panel.state(root, 'loading');
        try { if (typeof o.retry === 'function') o.retry(); } catch (e) { if (window.console) console.warn('[QF] retry 失败', e); }
      });
      actions.appendChild(retry);
      block.appendChild(actions);
    }
    if (state !== 'empty') root.removeAttribute('data-empty-reason');
    root.setAttribute('data-panel-state', state);
    panelHideOthers(root);
    root.appendChild(block);
  };

  /**
   * boot 时给所有已标记面板补一次初始态（M4 覆盖率 100%）：
   * 一律先标 ready（内容就是当前 DOM，不做任何隐藏/改写），
   * 之后由各页 render 函数在 空态/错态 分支调 QF.panel.state 改写 —— 这样
   * 首屏不会因为"骨架被 loading 态隐藏"而塌陷（避免 CLS 反而变大）。
   */
  QF.panel.stampAll = function () {
    $$('[data-qf-panel]').forEach(function (root) {
      if (root.getAttribute('data-panel-state')) return;
      root.setAttribute('data-panel-state', 'ready');
    });
  };

  /* ==========================================================================
     8. QF.table —— 排序 + MutationObserver 单次重放（不接管页面渲染）
     ========================================================================== */
  function cellSortValue(td) {
    if (!td) return '';
    var explicit = td.getAttribute('data-sort-value');
    if (explicit != null) return explicit;
    return (td.textContent || '').trim();
  }
  function toNumber(text) {
    if (text == null) return NaN;
    var s = String(text).replace(/,/g, '').replace(/%/g, '').replace(/\s/g, '');
    var mult = 1;
    if (/亿/.test(s)) { mult = 1e8; s = s.replace(/亿/, ''); }
    else if (/万/.test(s)) { mult = 1e4; s = s.replace(/万/, ''); }
    if (/^[+-]?(\d+(\.\d+)?|\.\d+)$/.test(s)) return parseFloat(s) * mult;
    var m = s.match(/[+-]?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) * mult : NaN;
  }

  /* 数值单元格识别：只认「纯数字 / 带正负 / 带 % / 带 亿|万|手|元 / 千分位」，
     且单元格内不能有子元素（避免把「操作」列的按钮文字判成数字）。
     用途：品牌页既有 render 不会加 .is-num，这里补一层样式标记（不改数据、不改文本）。 */
  var QF_NUM_RE = /^[+-]?[\d,]+(\.\d+)?%?$|^[+-]?[\d.]+(亿|万|手|元)$/;

  QF.table.markNumeric = function (tableEl) {
    if (!tableEl || !tableEl.tBodies || !tableEl.tBodies[0]) return;
    var rows = tableEl.tBodies[0].rows;
    for (var i = 0; i < rows.length; i++) {
      var cells = rows[i].cells;
      for (var j = 0; j < cells.length; j++) {
        var td = cells[j];
        if (td.children && td.children.length) continue;
        var txt = (td.textContent || '').trim();
        if (txt && QF_NUM_RE.test(txt)) td.classList.add('is-num');
      }
    }
  };

  QF.table.enhance = function (tableEl) {
    if (!tableEl || tableEl.nodeType !== 1) return;
    if (tableEl._qfEnhanced) {           /* 幂等：重复调用只补一次数值标记 */
      QF.table.markNumeric(tableEl);
      return;
    }
    tableEl._qfEnhanced = true;
    var ths = $$('th[data-sort-key]', tableEl);
    ths.forEach(function (th) {
      if (!th.hasAttribute('aria-sort')) th.setAttribute('aria-sort', 'none');
      th.addEventListener('click', function () {
        var key = th.getAttribute('data-sort-key');
        var cur = th.getAttribute('aria-sort') || 'none';
        var next = cur === 'none' ? 'descending' : (cur === 'descending' ? 'ascending' : 'none');
        QF.table.sort(tableEl, key, next);
      });
      th.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); th.click(); }
      });
      th.tabIndex = 0;
    });
    QF.table.markNumeric(tableEl);
    QF.table._observe(tableEl);
  };

  QF.table.enhanceAll = function () {
    $$('.qf-table').forEach(function (t) { QF.table.enhance(t); });
    /* 表格可能是后续渲染出来的：一次性再补一轮（不带轮询） */
    if (typeof MutationObserver === 'function' && document.body) {
      var mo = new MutationObserver(function () {
        $$('.qf-table').forEach(function (t) { QF.table.enhance(t); });
      });
      mo.observe(document.body, { childList: true, subtree: true });
    }
  };

  QF.table._observe = function (tableEl) {
    if (tableEl._qfObs) return;
    if (typeof MutationObserver !== 'function') return;
    var mo = new MutationObserver(function () {
      try { QF.table.markNumeric(tableEl); } catch (e) { /* 忽略 */ }
      if (tableEl._qfReapplying) return;            /* 自己改 DOM 时不重入 */
      var st = tableEl._qfSort;
      if (!st || st.dir === 'none') return;         /* 没排过序就不干预页面渲染 */
      tableEl._qfReapplying = true;
      try { QF.table.sort(tableEl, st.key, st.dir); } catch (e) { /* 忽略 */ }
      tableEl._qfReapplying = false;
    });
    mo.observe(tableEl.tBodies[0] || tableEl, { childList: true });
    tableEl._qfObs = mo;
  };

  QF.table.sort = function (tableEl, key, dir) {
    if (!tableEl || !key) return;
    var tbody = tableEl.tBodies[0];
    if (!tbody) return;
    var ths = $$('th[data-sort-key]', tableEl);
    var th = null;
    for (var i = 0; i < ths.length; i++) { if (ths[i].getAttribute('data-sort-key') === key) { th = ths[i]; break; } }
    if (!th) return;
    var col = Array.prototype.indexOf.call(th.parentNode.children, th);
    var isNumCol = (th.getAttribute('data-sort-type') === 'num');

    /* 原始顺序缓存（第三下恢复用） */
    if (!tableEl._qfOriginRows) {
      tableEl._qfOriginRows = Array.prototype.slice.call(tbody.rows);
    }
    var rows = Array.prototype.slice.call(tbody.rows);
    if (!rows.length) return;

    rows.forEach(function (tr, idx) { tr.__qfIdx = idx; });
    if (dir === 'none') {
      var origin = tableEl._qfOriginRows.filter(function (tr) { return tr && tr.isConnected; });
      origin.forEach(function (tr) { tbody.appendChild(tr); });
      tableEl._qfSort = null;
      ths.forEach(function (t) { t.setAttribute('aria-sort', 'none'); });
      return;
    }
    var sign = dir === 'ascending' ? 1 : -1;
    rows.sort(function (a, b) {
      var va = cellSortValue(a.cells[col]);
      var vb = cellSortValue(b.cells[col]);
      var na = toNumber(va), nb = toNumber(vb);
      var missA = isNaN(na), missB = isNaN(nb);
      if (missA && missB) return a.__qfIdx - b.__qfIdx;
      if (missA) return 1;                       /* 缺失值永远排最后 */
      if (missB) return -1;
      if (isNumCol || (!isNaN(na) && !isNaN(nb))) {
        if (na === nb) return a.__qfIdx - b.__qfIdx;
        return na < nb ? -sign : sign;
      }
      return sign * String(va).localeCompare(String(vb), 'zh-CN');
    });
    tableEl._qfReapplying = true;
    rows.forEach(function (tr) { tbody.appendChild(tr); });
    tableEl._qfReapplying = false;
    tableEl._qfSort = { key: key, dir: dir };
    ths.forEach(function (t) { t.setAttribute('aria-sort', t === th ? dir : 'none'); });
  };

  /* ==========================================================================
     9. QF.wb —— 工作台模板（按钮化上移/下移/折叠/隐藏 + 预设 + 重置）
     ========================================================================== */
  var WB_KEY = 'qf.wb.layout.v1';
  var WB_PANELS = [
    ['承接分析', 'hold'], ['情绪雷达', 'radar'], ['分歧转一致相位', 'phase'], ['涨停梯队', 'ladder'],
    ['行业热度', 'sector'], ['热门个股', 'hot'], ['资金连增', 'ddx'], ['开仓纪律', 'discipline'],
    ['资金水位', 'water'], ['持仓盘中估算', 'holdings']
  ];
  var WB_PRESETS = {
    intraday: ['hold', 'radar', 'phase', 'ddx', 'holdings', 'ladder', 'sector', 'hot', 'discipline', 'water'],
    review: ['ladder', 'sector', 'water', 'discipline', 'hot', 'radar', 'hold', 'phase', 'ddx', 'holdings']
  };
  function wbDefaultOrder() { return WB_PANELS.map(function (p) { return p[1]; }); }
  function wbDefault() {
    return { version: 1, order: wbDefaultOrder(), hidden: [], collapsed: [], preset: 'custom' };
  }
  function wbGrid() { return $('#page-workbench .wb-grid'); }
  function wbPanelMap() {
    var grid = wbGrid();
    var map = {};
    if (!grid) return map;
    kids(grid, 'wb-panel').forEach(function (sec) {
      var label = sec.getAttribute('aria-label') || '';
      var id = null;
      for (var i = 0; i < WB_PANELS.length; i++) {
        if (WB_PANELS[i][0] === label) { id = WB_PANELS[i][1]; break; }
      }
      if (!id) id = label || ('panel-' + Object.keys(map).length);
      map[id] = sec;
      sec.setAttribute('data-qf-wb-panel', id);
    });
    return map;
  }
  function sanitizeOrder(order, map) {
    var out = [];
    if (Array.isArray(order)) {
      order.forEach(function (id) { if (map[id] && out.indexOf(id) < 0) out.push(id); });
    }
    Object.keys(map).forEach(function (id) { if (out.indexOf(id) < 0) out.push(id); });
    return out;
  }
  function sanitizeList(list, map) {
    if (!Array.isArray(list)) return [];
    return list.filter(function (id) { return !!map[id]; });
  }

  QF.wb.KEY = WB_KEY;
  QF.wb.load = function () {
    var raw = null;
    try { raw = window.localStorage.getItem(WB_KEY); } catch (e) { return wbDefault(); }
    if (!raw) return wbDefault();
    var parsed = null;
    try { parsed = JSON.parse(raw); } catch (e) { parsed = null; }
    if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.order)) {
      try { window.localStorage.removeItem(WB_KEY); } catch (e2) { /* 忽略 */ }
      return wbDefault();
    }
    return {
      version: 1,
      order: parsed.order,
      hidden: Array.isArray(parsed.hidden) ? parsed.hidden : [],
      collapsed: Array.isArray(parsed.collapsed) ? parsed.collapsed : [],
      preset: parsed.preset || 'custom'
    };
  };
  QF.wb.save = function (layout) {
    try { window.localStorage.setItem(WB_KEY, JSON.stringify(layout)); } catch (e) { /* 隐私模式忽略 */ }
  };
  QF.wb.layout = wbDefault();

  QF.wb.apply = function () {
    try {
      var grid = wbGrid();
      if (!grid) return;
      var map = wbPanelMap();
      var layout = QF.wb.layout || wbDefault();
      var order = sanitizeOrder(layout.order, map);
      order.forEach(function (id) { grid.appendChild(map[id]); });
      var hidden = sanitizeList(layout.hidden, map);
      var collapsed = sanitizeList(layout.collapsed, map);
      Object.keys(map).forEach(function (id) {
        map[id].classList.toggle('qf-wb-hidden', hidden.indexOf(id) >= 0);
        map[id].classList.toggle('qf-wb-collapsed', collapsed.indexOf(id) >= 0);
      });
      layout.order = order;
      layout.hidden = hidden;
      layout.collapsed = collapsed;
      grid.classList.add('qf-wb-dense');
      QF.wb.syncControls();
    } catch (e) {
      if (window.console && console.warn) console.warn('[QF] 工作台布局应用失败，已回落默认', e);
    }
  };

  QF.wb.move = function (id, dir) {
    var layout = QF.wb.layout;
    var order = layout.order.slice();
    var i = order.indexOf(id);
    if (i < 0) return;
    var j = dir === 'up' ? i - 1 : i + 1;
    if (j < 0 || j >= order.length) return;
    order[i] = order[j];
    order[j] = id;
    layout.order = order;
    layout.preset = 'custom';
    QF.wb.save(layout);
    QF.wb.apply();
  };
  QF.wb.toggleHidden = function (id) {
    var layout = QF.wb.layout;
    var i = layout.hidden.indexOf(id);
    if (i >= 0) layout.hidden.splice(i, 1); else layout.hidden.push(id);
    layout.preset = 'custom';
    QF.wb.save(layout);
    QF.wb.apply();
  };
  QF.wb.toggleCollapsed = function (id) {
    var layout = QF.wb.layout;
    var i = layout.collapsed.indexOf(id);
    if (i >= 0) layout.collapsed.splice(i, 1); else layout.collapsed.push(id);
    layout.preset = 'custom';
    QF.wb.save(layout);
    QF.wb.apply();
  };
  QF.wb.preset = function (name) {
    var order = WB_PRESETS[name];
    if (!order) return;
    QF.wb.layout = { version: 1, order: order.slice(), hidden: [], collapsed: [], preset: name };
    QF.wb.save(QF.wb.layout);
    QF.wb.apply();
  };
  QF.wb.reset = function () {
    QF.wb.layout = wbDefault();
    try { window.localStorage.removeItem(WB_KEY); } catch (e) { /* 忽略 */ }
    QF.wb.apply();
  };
  QF.wb.setEdit = function (on) {
    document.body.classList.toggle('qf-wb-edit', !!on);
    var btn = $('#qfWbEditBtn');
    if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  };
  QF.wb.syncControls = function () {
    var layout = QF.wb.layout;
    $$('#page-workbench .wb-panel[data-qf-wb-panel]').forEach(function (sec) {
      var id = sec.getAttribute('data-qf-wb-panel');
      var hideBtn = sec.querySelector('[data-wb-act="hide"]');
      var colBtn = sec.querySelector('[data-wb-act="collapse"]');
      if (hideBtn) hideBtn.setAttribute('aria-pressed', layout.hidden.indexOf(id) >= 0 ? 'true' : 'false');
      if (colBtn) colBtn.setAttribute('aria-pressed', layout.collapsed.indexOf(id) >= 0 ? 'true' : 'false');
    });
    var presetTag = $('#qfWbPresetTag');
    if (presetTag) {
      presetTag.textContent = layout.preset === 'intraday' ? '预设：盘中'
        : (layout.preset === 'review' ? '预设：复盘' : '预设：自定义');
    }
  };

  function wbBuildControls() {
    var grid = wbGrid();
    if (!grid) return;
    kids(grid, 'wb-panel').forEach(function (sec) {
      var head = sec.querySelector('.wb-ph') || sec;
      if (head.querySelector('.qf-wb-ctl')) return;
      var ctl = mk('span', 'qf-wb-ctl', '');
      [['up', '▲', '上移'], ['down', '▼', '下移'], ['collapse', '–', '折叠'], ['hide', '×', '隐藏']].forEach(function (spec) {
        var b = mk('button', '', spec[1]);
        b.type = 'button';
        b.setAttribute('data-wb-act', spec[0]);
        b.setAttribute('aria-pressed', 'false');
        b.setAttribute('aria-label', spec[2] + '：' + (sec.getAttribute('aria-label') || ''));
        b.title = spec[2];
        ctl.appendChild(b);
      });
      head.appendChild(ctl);
    });
    if (!grid._qfWbBound) {
      grid.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('[data-wb-act]') : null;
        if (!btn) return;
        var sec = btn.closest('.wb-panel');
        if (!sec) return;
        var id = sec.getAttribute('data-qf-wb-panel');
        var act = btn.getAttribute('data-wb-act');
        if (act === 'up') QF.wb.move(id, 'up');
        else if (act === 'down') QF.wb.move(id, 'down');
        else if (act === 'hide') QF.wb.toggleHidden(id);
        else if (act === 'collapse') QF.wb.toggleCollapsed(id);
      });
      grid._qfWbBound = true;
    }
  }

  function wbBuildBar() {
    var page = $('#page-workbench');
    if (!page || $('#qfWbBar')) return;
    var shell = page.querySelector('.wb-shell') || page;
    var bar = mk('div', 'qf-wb-bar');
    bar.id = 'qfWbBar';
    var tag = mk('span', '', '预设：自定义');
    tag.id = 'qfWbPresetTag';
    bar.appendChild(tag);
    bar.appendChild(mk('span', 'qf-wb-bar__spacer', ''));
    [['intraday', '盘中预设'], ['review', '复盘预设']].forEach(function (spec) {
      var b = mk('button', 'qf-wb-toggle', spec[1]);
      b.type = 'button';
      b.setAttribute('data-wb-preset', spec[0]);
      b.addEventListener('click', function () { QF.wb.preset(spec[0]); });
      bar.appendChild(b);
    });
    var save = mk('button', 'qf-wb-toggle', '保存为模板');
    save.type = 'button';
    save.addEventListener('click', function () {
      QF.wb.save(QF.wb.layout);
      if (window.showToast) window.showToast('布局已保存到本机（qf.wb.layout.v1）', 'success');
    });
    bar.appendChild(save);
    var reset = mk('button', 'qf-wb-toggle', '恢复默认');
    reset.type = 'button';
    reset.addEventListener('click', function () {
      QF.wb.reset();
      if (window.showToast) window.showToast('已恢复默认布局', 'success');
    });
    bar.appendChild(reset);
    var done = mk('button', 'qf-wb-toggle', '完成');
    done.type = 'button';
    done.addEventListener('click', function () { QF.wb.setEdit(false); });
    bar.appendChild(done);
    shell.appendChild(bar);
  }

  function wbBuildToggle() {
    var head = $('#page-workbench .wb-head-right');
    if (!head || $('#qfWbEditBtn')) return;
    var btn = mk('button', 'qf-wb-toggle', '⋯ 编辑布局');
    btn.id = 'qfWbEditBtn';
    btn.type = 'button';
    btn.setAttribute('aria-pressed', 'false');
    btn.addEventListener('click', function () {
      QF.wb.setEdit(!document.body.classList.contains('qf-wb-edit'));
    });
    head.insertBefore(btn, head.firstChild);
  }

  QF.wb.init = function () {
    try {
      wbBuildToggle();
      wbBuildBar();
      wbBuildControls();
      QF.wb.layout = QF.wb.load();
      QF.wb.apply();
    } catch (e) {
      if (window.console && console.warn) console.warn('[QF] 工作台模板初始化失败', e);
    }
  };

  /* ---- 10. 启动：脚本在 </body> 前最后一个加载，DOM 已就绪 ---- */

  /**
   * 完整回退（S9-05, 2026-11-24）：boot 任何一步抛异常时, 只摘 body.qf-shell
   * 会留下无样式裸块（#qfCtxbar/#qfWbBar/#qfRailPin/#qfFlyout/#qfWbEditBtn）、
   * 包装版 switchPage 与面板初始态——半回滚态比不回滚更破。unboot 幂等地
   * 清掉全部注入节点与属性, 把应用还原到与 ?shell=off 一致的状态。
   */
  QF.shell.unboot = function () {
    ['qfCtxbar', 'qfFlyout', 'qfRailPin', 'qfWbBar', 'qfWbEditBtn', 'qfIndexBar']
      .forEach(function (id) {
        var n = document.getElementById(id);
        if (n && n.parentNode) n.parentNode.removeChild(n);
      });
    $$('.index-item').forEach(function (n) { n.hidden = false; });
    var gi = document.getElementById('globalIndexBar');
    if (gi) gi.hidden = false;
    $$('.qf-panel__state').forEach(function (n) {
      if (n.parentNode) n.parentNode.removeChild(n);
    });
    $$('[data-qf-panel]').forEach(function (n) {
      n.removeAttribute('data-panel-state');
      n.removeAttribute('data-empty-reason');
    });
    if (typeof window.switchPage === 'function' && window.switchPage.__qfWrapped &&
        typeof window.switchPage.__qfOrig === 'function') {
      window.switchPage = window.switchPage.__qfOrig;
    }
    document.body.classList.remove('qf-shell', 'qf-nav-pinned', 'qf-wb-edit');
    document.body.removeAttribute('data-qf-shell');
    QF.shell.booted = false;
    QF.shell.migrated = false;
    QF.shell.enabled = false;
  };

  function start() {
    try {
      QF.shell.boot();
    } catch (e) {
      if (window.console && console.error) console.error('[QF] boot 失败，骨架完整回退到存量布局', e);
      try { QF.shell.unboot(); } catch (e2) { /* 回退失败也要保证 enabled=false，cmdk 依赖它短路 */ }
      QF.shell.enabled = false;
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
