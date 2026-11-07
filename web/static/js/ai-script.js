/* ===== STAGE5: AI 定制建议(light 档真实入口) =====
 *
 * 背景(2026-11-07):
 *   LLM 任务分层路由上线后,light 档(15s 预算 / zhipu_glm → agnes-3.0-flash)
 *   的两个调用点都没有真实流量:
 *     1) news_aggregator 的 LLM 摘要 —— 依赖 Tavily,未配置时在调 LLM 前就 return
 *     2) /api/scripts/ai-generate   —— 端点存在但前端从未接线
 *   本文件把第 2 个接上:在「话术推荐」卡片里提供「生成 AI 定制建议」按钮,
 *   点击才发起请求,不影响页面加载耗时;模板话术仍然保留作为兜底。
 *
 * 契约:
 *   POST /api/scripts/ai-generate
 *   { target_type: "fund", target_data: {...}, scene: "" }
 *   -> { data: { source: "ai" | "fallback", content: "...", target_name, target_code } }
 *   source === "fallback" 表示 LLM 失败、已降级为模板话术,UI 需如实标注。
 */
(function () {
  'use strict';

  var _payload = null;
  var _inflight = false;

  function el(id) {
    return document.getElementById(id);
  }

  /** 把基金 advice 对象转换成后端 AIGenerateRequest.target_data */
  function buildTargetData(advice) {
    if (!advice) return null;
    var adv = advice.advice || {};
    return {
      fund_name: advice.fund_name || advice.name || '',
      fund_code: advice.fund_code || advice.code || '',
      current_nav: advice.nav || advice.current_nav || 0,
      change_pct: advice.change_pct || 0,
      pnl_pct: advice.pnl_pct || 0,
      decision: adv.action || advice.action || ''
    };
  }

  /**
   * 由 index.html 的 loadFundScripts() 调用。
   * 即使模板话术匹配失败,这个入口也要可用 —— 所以它在模板请求之前就被调用。
   */
  window.__setAiScriptPayload = function (advice) {
    var box = el('aiScriptBox');
    var result = el('aiScriptResult');
    var btn = el('aiScriptBtn');
    _payload = buildTargetData(advice);
    if (!box) return;
    if (!_payload || !_payload.fund_code) {
      box.style.display = 'none';
      return;
    }
    _inflight = false;
    if (result) result.style.display = 'none';
    if (btn) {
      btn.disabled = false;
      btn.textContent = '生成 AI 定制建议';
    }
    box.style.display = 'block';
  };

  function setLoading(on) {
    var btn = el('aiScriptBtn');
    var hint = el('aiScriptHint');
    _inflight = on;
    if (btn) {
      btn.disabled = on;
      btn.textContent = on ? '生成中…' : '重新生成';
    }
    if (hint) {
      hint.textContent = on ? '正在调用 AI(light 档),通常 5-15 秒' : '基于当前基金数据分析生成,约 5-15 秒';
    }
  }

  function showResult(source, content, metaText) {
    var box = el('aiScriptResult');
    var text = el('aiScriptText');
    var meta = el('aiScriptMeta');
    if (!box || !text) return;
    // 用 textContent 而非 innerHTML:LLM 输出属于不可信文本
    text.textContent = content;
    text.style.color = source === 'fallback' ? 'var(--text-muted)' : 'var(--text-primary)';
    if (meta) {
      meta.textContent = metaText;
      meta.style.color = source === 'fallback' ? 'var(--text-muted)' : 'var(--text-tertiary)';
    }
    box.style.display = 'block';
  }

  window.generateAiScript = function () {
    if (_inflight || !_payload) return;
    var hint = el('aiScriptHint');
    setLoading(true);
    if (hint) hint.textContent = '正在调用 AI(light 档),通常 5-15 秒';

    api('/api/scripts/ai-generate', {
      method: 'POST',
      body: JSON.stringify({ target_type: 'fund', target_data: _payload, scene: '' })
    }).then(function (resp) {
      var data = (resp && resp.data) || null;
      var content = data && data.content ? String(data.content) : '';
      if (!content) {
        setLoading(false);
        showResult('error', '未返回内容,请稍后再试', '生成失败');
        return;
      }
      setLoading(false);
      if (data.source === 'fallback') {
        showResult('fallback', content, 'AI 暂不可用,已降级为模板话术');
      } else {
        showResult('ai', content, 'AI 定制建议 · ' + (_payload.fund_name || _payload.fund_code));
      }
    }).catch(function (e) {
      setLoading(false);
      showResult('error', '生成失败:' + ((e && e.message) || '网络异常'), '可稍后重试,或使用上方模板话术');
    });
  };

  document.addEventListener('DOMContentLoaded', function () {
    var btn = el('aiScriptBtn');
    if (btn) btn.addEventListener('click', window.generateAiScript);
  });
})();
