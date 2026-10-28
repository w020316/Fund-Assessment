/**
 * 阶段5 T04 — 数据新鲜度徽章全局组件(Freshness)
 *
 * 架构文档 Shared Knowledge #3:所有数值卡的"截至/估算/滞后"标识唯一入口,
 * 禁止各页自行拼接时间文案(P1-4 / K6 100% 覆盖)。
 *
 * 统一接口:
 *   Freshness.html({ asOf: "14:32" | "2026-10-24", estimated: false, lagDays: 0, note: "" })
 *     → 返回 HTML 字符串(用于 innerHTML 模板拼接;无信息时返回空串不渲染)
 *   Freshness.render({...同上})
 *     → 返回 DOM 节点
 *
 * 语义:
 *   - estimated=true → 渲染"估算"标签 + 虚线边框视觉区分(P1-4 ②)
 *   - lagDays>=1    → 渲染"滞后 N 天"
 *   - asOf          → 渲染"截至 HH:mm / YYYY-MM-DD"
 *   - note          → 附加人话说明(如后端 confidence_reason)
 * 模块为普通 <script> 全局对象(与存量风格一致,无构建步骤)。
 */
/* global escapeHtml */

(function () {
  'use strict';

  function injectStyle() {
    if (document.getElementById('freshness-style')) return;
    const style = document.createElement('style');
    style.id = 'freshness-style';
    style.textContent = [
      '.freshness-badge{display:inline-flex;align-items:center;gap:4px;',
      '  font-size:11px;color:var(--text-muted,#8b92a8);padding:1px 6px;',
      '  border-radius:4px;line-height:1.6;vertical-align:middle;}',
      /* 估算值视觉区分:虚线边框 + 琥珀色标签(P1-4 ②) */
      '.freshness-badge.is-estimated{color:var(--color-warning,#f59e0b);',
      '  border:1px dashed var(--color-warning,#f59e0b);}',
      '.freshness-badge.is-lagged{color:var(--color-warning,#f59e0b);}',
      '.freshness-badge .freshness-note{color:var(--text-muted,#8b92a8);}',
    ].join('');
    document.head.appendChild(style);
  }

  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s) : String(s == null ? '' : s); }

  /**
   * 生成徽章 HTML。
   * @param {{asOf?:string, estimated?:boolean, lagDays?:number, note?:string}} opts
   * @returns {string} 无任何信息时返回 ''(调用方模板可直接拼接)
   */
  function html(opts) {
    // 全站只用 html() 拼模板,若样式只在 render() 注入则 .freshness-badge 永不生效
    injectStyle();
    const o = opts || {};
    const hasAsOf = !!(o.asOf && String(o.asOf).trim());
    const estimated = !!o.estimated;
    const lagDays = Number(o.lagDays) || 0;
    const note = o.note ? String(o.note) : '';
    if (!hasAsOf && !estimated && lagDays < 1 && !note) return '';

    const parts = [];
    if (estimated) parts.push('估算');
    if (lagDays >= 1) parts.push('滞后 ' + lagDays + ' 天');
    if (hasAsOf) parts.push('截至 ' + esc(String(o.asOf).trim()));
    if (note) parts.push('<span class="freshness-note">' + esc(note) + '</span>');

    const cls = ['freshness-badge'];
    if (estimated) cls.push('is-estimated');
    if (lagDays >= 1) cls.push('is-lagged');
    return '<span class="' + cls.join(' ') + '">' + parts.join(' · ') + '</span>';
  }

  /** 返回 DOM 节点(架构文档统一接口形态) */
  function render(opts) {
    injectStyle();
    const htmlStr = html(opts);
    const span = document.createElement('span');
    if (htmlStr) span.innerHTML = htmlStr;
    return span;
  }

  window.Freshness = { html: html, render: render };
})();
