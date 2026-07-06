# S5 前端去 AI 化重设计记录

**日期**: 2026-07-06
**子项目**: S5(前端去 AI 化重设计)
**前置上下文**: 前端品牌混乱(title/logo 显示 "OpenClaw",后端 FastAPI 为 "QuantFlow Pro"),UI 大量暴露 AI 品牌标识(导航"AI分析"、按钮"AI深度分析"、状态条显示 TTAPI/Tavily/Tinyfish/OpenAI 等 LLM 提供商名称)。

---

## 1. 模块概览

| # | 改造类别 | 涉及文件 | 状态 |
|---|---------|---------|------|
| 1 | 品牌统一(OpenClaw → QuantFlow Pro) | `web/static/index.html` | ✅ |
| 2 | AI 文案中性化 | `web/static/index.html` | ✅ |
| 3 | LLM 提供商细节隐藏 | `web/static/index.html` | ✅ |
| 4 | 验证 + 文档 | `docs/2026-07-06-S5-frontend-debrand.md` | ✅ |

**设计原则**:不改变原设计风格(配色/布局/字体/组件样式),只改文案与品牌;保留所有功能与 API 路径。

---

## 2. 详细改造记录

### 2.1 品牌统一(OpenClaw → QuantFlow Pro)

| 位置 | 改前 | 改后 |
|------|------|------|
| `<title>` | OpenClaw 量化交易系统 | QuantFlow Pro 量化交易系统 |
| 顶部 logo 图标 | `OC` | `QF` |
| 顶部 logo 文字 | OpenClaw 量化交易系统 | QuantFlow Pro 量化交易系统 |

### 2.2 AI 文案中性化

| 位置 | 改前 | 改后 |
|------|------|------|
| 左侧导航(快捷键 2) | 🤖 AI分析 | 🧠 智能分析 |
| 底部 tab(移动端) | 🤖 AI | 🧠 分析 |
| AI 页注释 | `<!-- 2. AI智能分析 -->` | `<!-- 2. 智能分析 -->` |
| JS 注释 | `/* 2. AI智能分析 */` | `/* 2. 智能分析 */` |
| 分析引擎状态条 | 🤖 AI引擎: | 🧠 分析引擎: |
| 主操作按钮 | 🧠 AI深度分析 | 🧠 深度分析 |
| Toast 提示 | 正在进行AI深度分析... | 正在进行深度分析... |
| JS 注释 | `// 调用AI分析API` | `// 调用分析API` |
| 系统健康默认项 | AI引擎 | 分析引擎 |
| 系统健康默认详情 | GPU利用率 85% | 未配置 |
| 话术库注释 | 话术库浏览(AI 页) | 话术库浏览(智能分析页) |

### 2.3 LLM 提供商细节隐藏

**改前**:
- 系统健康面板显示具体提供商:`TTAPI+Tavily+Tinyfish 已配置`
- LLM Provider 状态条显示具体名称:`OpenAI` / `keys.provider`(可能是 DeepSeek/Agnes 等)

**改后**:
- 系统健康面板:`已就绪` / `未配置`(不暴露具体提供商)
- LLM Provider 状态条:`已就绪` / `未配置` / `内置引擎` / `检测失败`

**判断逻辑**(统一聚合,不再分支显示):
```javascript
const hasKey = keys.provider || keys.ttapi || keys.tavily || keys.tinyfish 
            || keys.agnes || keys.openai_key || keys.api_key;
if (hasKey) {
  nameEl.textContent = '已就绪';
  statusEl.innerHTML = '...在线';
} else {
  nameEl.textContent = '未配置';
  statusEl.innerHTML = '...未配置';
}
```

### 2.4 保留项(不动)

- **函数名**:`runAiAnalysis` / `loadLlmProvider` / `loadAiHistory` / `renderAgentCards` 等(内部实现,不影响 UI,避免大改动)
- **API 路径**:`/api/agent/analyze` / `/api/agent/opinions` 等(后端契约,不变)
- **数据字段**:`data.ai_keys.ttapi` 等 JS 逻辑判断(必须读取 API 返回字段)
- **分析师卡片标题**:"基本面分析师" / "技术分析师" / "情绪分析师" / "新闻分析师"(中性描述,合理保留)
- **"分析历史"** / **"话术库"** 等卡片标题(本就中性)

---

## 3. 验证结果

### 3.1 静态验证

服务运行中(`python launch.py --reload`),curl 抓取首页 HTML:

| 检查项 | 结果 |
|--------|------|
| `<title>QuantFlow Pro 量化交易系统</title>` | ✅ |
| logo 文字:`QuantFlow Pro 量化交易系统` | ✅ |
| logo 图标:`QF` | ✅ |
| 无 "OpenClaw" 残留 | ✅ |
| 无 UI 可见 "AI引擎" / "AI分析" / "AI深度分析" 文案 | ✅ |
| 无 UI 可见 "TTAPI" / "Tavily" / "Tinyfish" / "OpenAI" / "DeepSeek" 提供商名称 | ✅ |

### 3.2 功能验证

- 服务无重启,--reload 模式自动热加载
- 浏览器访问 http://localhost:8000 正常
- API 端点全部正常(`/api/health` / `/api/scripts/*` 等)

---

## 4. 设计决策与权衡

1. **保留函数名与 API 路径**:避免大范围重构,降低引入 bug 的风险;只改 UI 可见文案
2. **隐藏 LLM 提供商细节**:用户不需要知道底层用 TTAPI 还是 Agnes,统一显示"已就绪"更专业
3. **保留"分析师"卡片标题**:这是功能角色描述(基本面/技术/情绪/新闻),不暴露 AI 品牌,合理保留
4. **品牌统一为 QuantFlow Pro**:与后端 FastAPI 应用名(`QuantFlow Pro 量化交易系统`)保持一致
5. **不改设计风格**:严格遵守用户偏好"不能改变原设计风格;在不影响原样式的前提下进行操作"

---

## 5. 文件清单

**修改**:
- `web/static/index.html`— 13 处文案/品牌改造

**新增**:
- `docs/2026-07-06-S5-frontend-debrand.md`— 本文档

---

## 6. 后续计划

S5 完成。下一子项目:
- **S6 测试补全**:为前端改造补充回归测试(若可行)
- **S7 交付文档**:整理 S1-S7 全部成果,生成最终交付报告
