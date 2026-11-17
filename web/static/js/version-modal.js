/* ===== STAGE7: version-modal ===== 版本更新弹窗
   - /api/health.version(commit 短哈希) 与 localStorage.qf_seen_version 比对,不一致自动弹出
   - 更新日志: /static/changelog.json(最新一条置顶,历史 <details> 折叠)
   - window.openVersionModal() 手动打开;ESC / 遮罩点击 / 「知道了」关闭并写入 seen
   - /api/health 为轮询静默端点(api() 内置),失败不打扰用户、不自动弹窗 */
(function () {
  'use strict';

  const SEEN_KEY = 'qf_seen_version';

  function esc(s) {
    if (typeof window.escapeHtml === 'function') return window.escapeHtml(s);
    return String(s == null ? '' : s); // 兜底:主脚本异常时不注入未经转义的富文本
  }

  function readSeen() {
    try { return localStorage.getItem(SEEN_KEY); } catch (e) { return null; }
  }

  function markSeen(v) {
    try { localStorage.setItem(SEEN_KEY, v); } catch (e) { /* localStorage 禁用时忽略 */ }
  }

  function isOpen() {
    const m = document.getElementById('versionModal');
    return !!(m && m.style.display !== 'none' && m.style.display !== '');
  }

  function close() {
    const m = document.getElementById('versionModal');
    if (m) m.style.display = 'none';
  }

  function render(version, entries) {
    const body = document.getElementById('versionModalBody');
    const title = document.getElementById('versionModalTitle');
    if (!body) return;
    if (title) title.textContent = version ? `版本更新 · ${version}` : '版本更新';
    if (!entries || !entries.length) {
      body.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px 0;">暂无更新日志</div>';
      return;
    }
    const li = (t) => `<li style="margin:4px 0;">${esc(t)}</li>`;
    const latest = entries[0];
    const rest = entries.slice(1).map((e) => `
      <details class="gm-ver-details">
        <summary>${esc(e.version)} · ${esc(e.date || '')} ${esc(e.title || '')}</summary>
        <ul class="gm-ver-list">${(e.items || []).map(li).join('')}</ul>
      </details>`).join('');
    body.innerHTML = `
      <div class="gm-ver-latest">
        <div class="gm-ver-head">
          <b>${esc(latest.title || latest.version || '')}</b>
          <span>${esc(latest.date || '')} · ${esc(latest.version || '')}</span>
        </div>
        <ul class="gm-ver-list">${(latest.items || []).map(li).join('')}</ul>
      </div>
      ${rest ? `<div class="gm-ver-history"><div class="gm-ver-cap">历史版本</div>${rest}</div>` : ''}
      <div style="margin-top:12px;text-align:right;">
        <button type="button" class="btn btn-sm btn-primary" onclick="VersionModal.close()">知道了</button>
      </div>`;
  }

  async function open() {
    const m = document.getElementById('versionModal');
    if (!m) return;
    m.style.display = 'flex';
    const body = document.getElementById('versionModalBody');
    if (body) body.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px 0;">加载中...</div>';
    const [health, log] = await Promise.all([
      api('/api/health').catch(() => null),
      api('/static/changelog.json').catch(() => null),
    ]);
    if (!isOpen()) return; // 等待期间用户已关闭,不覆盖无关内容
    const version = health && typeof health.version === 'string' ? health.version : '';
    const entries = Array.isArray(log) ? log : [];
    render(version, entries);
    if (version && version !== 'unknown') markSeen(version);
  }

  async function autoCheck() {
    const health = await api('/api/health').catch(() => null);
    const version = health && typeof health.version === 'string' ? health.version : '';
    const cur = document.getElementById('versionCurrent');
    if (cur && version) cur.textContent = version;
    // 拿不到版本号(unknown/失败)不弹,避免每次访问都打扰
    if (!version || version === 'unknown') return;
    if (readSeen() !== version) open();
  }

  /* ESC 关闭(主脚本的 ESC 监听只覆盖存量三个 modal,不改动它,这里自行注册) */
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !isOpen()) return;
    close();
    e.preventDefault();
  });

  document.addEventListener('DOMContentLoaded', () => {
    const m = document.getElementById('versionModal');
    if (m) {
      // 遮罩点击关闭(只认遮罩本身,弹窗内部点击不冒泡误关)
      m.addEventListener('click', (e) => { if (e.target === m) close(); });
    }
    autoCheck();
  });

  window.VersionModal = { open, close };
  window.openVersionModal = open;
})();
