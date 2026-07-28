# QuantFlow Pro 重写设计文档

> **日期**:2026-07-28
> **项目**:QuantFlow Pro 量化交易系统(推翻重做)
> **状态**:待用户审阅
> **前序项目**:S1-S7 七轮迭代(204 测试用例,3 大新功能),本次推翻重做

---

## 0. 决策记录

| 决策项 | 选择 | 决策时间 |
|--------|------|---------|
| 核心方向 | 推翻重做(全新项目) | 2026-07-28 |
| 项目定位 | 同定位重写(A股量化系统) | 2026-07-28 |
| 技术栈 | Python 后端(保留修复)+ Next.js 前端(重写) | 2026-07-28 |
| 重写范围 | 前端重写 + 后端保留修复 | 2026-07-28 |
| 功能范围 | 全功能复刻 + 扩展(回测可视化 + AI生成话术) | 2026-07-28 |
| 设计风格 | 金融专业终端风(深色) | 2026-07-28 |
| 子项目分解 | 按层分解(P1后端 → P2前端 → P3新功能) | 2026-07-28 |

**关键约束**(继承自项目记忆):
- 品牌名必须统一为 QuantFlow Pro,OpenClaw 不得出现在任何可见内容
- 前端 UI 保留深色终端风格,文案中性化(AI → 智能)
- API 路径和函数名在修改时尽量保留
- 部署使用 render.yaml(Python)+ Vercel(Next.js)

---

## 1. 项目背景与现状

### 1.1 前序成果(S1-S7)

项目已经历七轮迭代,当前状态:

| 子项目 | 交付 | 测试用例 | 状态 |
|--------|------|---------|------|
| S1 后端基建修复 | 鉴权/async/造假移除/并行/去重 | 81 | 完成 |
| S2 基金模块 | 基金建议规则引擎 + 8 API | 27 | 完成 |
| S3 国际股市 | 5 指数 + 美股/港股热门 | 15 | 完成 |
| S4 话术库 | 24 模板 + 变量填充 + 智能匹配 | 26 | 完成 |
| S5 前端去AI化 | 品牌统一 + AI文案中性化 | 0 | 完成 |
| S6 测试补全 | DataCache + 路由层覆盖 | 55 | 完成 |
| S7 交付文档 | README + 交付报告 | — | 完成 |
| **合计** | | **204** | |

### 1.2 推翻重做的理由

用户决策推翻重做,核心动机:
1. 前端为原生 HTML SPA,设计风格过于 AI 生成化,缺乏独特性
2. 前端技术栈(原生HTML)不匹配用户技能栈(Next.js/React/Vue)
3. 需接入更多免费 LLM API,降低运营成本
4. 需扩展新功能(回测可视化、AI生成话术)

### 1.3 保留与重写边界

| 模块 | 处理方式 | 理由 |
|------|---------|------|
| Python 后端核心 | **保留 + 修复** | S1-S7 修复成果有价值(鉴权/async/并行/造假移除) |
| 数据源封装(akshare/tushare/mootdx) | **保留** | Python 独有生态,重写成本高 |
| 7 智能体 + LLM 路由 | **保留 + 扩展** | 核心业务逻辑,扩展免费 Provider |
| 基金建议规则引擎 | **保留** | 三信号融合设计成熟 |
| 话术库 | **保留 + 扩展** | 新增 AI 生成模式 |
| 前端(原生 HTML) | **重写** | Next.js 从零构建 |
| 部署配置 | **调整** | 新增 Vercel 前端部署 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────┐
│  前端:Next.js 15 + React 19 + TS + Tailwind + shadcn/ui │
│  (金融终端深色风,11页面,App Router,SSR+CSR混合)        │
│  部署:Vercel                                            │
├─────────────────────────────────────────────────────────┤
│  后端:Python 3.12 + FastAPI(保留现有,修复+扩展)        │
│  部署:Render                                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐    │
│  │LLM Router│ │ Agents   │ │  Data Source         │    │
│  │(12个     │ │(7智能体  │ │  (6源降级+mootdx优先)│    │
│  │ Provider)│ │ 辩论)    │ │                      │    │
│  └──────────┘ └──────────┘ └──────────────────────┘    │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐    │
│  │Fund      │ │Script    │ │  Backtest            │    │
│  │Advisor   │ │Library   │ │  (可视化增强,新增)  │    │
│  │(基金建议)│ │(+AI生成) │ │                      │    │
│  └──────────┘ └──────────┘ └──────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  基础设施:SQLite(保留)+ Redis(仅预留配置,不实现)    │
│  鉴权:静态Token(后端保留,前端沿用)                   │
└─────────────────────────────────────────────────────────┘
```

### 关键架构决策

1. **后端保留 SQLite**:风控状态持久化够用,避免数据库迁移风险
2. **前后端分离部署**:Vercel(前端)+ Render(后端),CORS 跨域配置
3. **LLM Provider 全走 OpenAI 兼容**:14 个免费 API 中 13 个兼容,无需改 LLMRouter 核心代码
4. **不接实盘交易**:合规风险,仅做分析建议(参考 Vibe-Trading 合规立场)
5. **Redis 仅预留配置,不实现**:P1 不开发 Redis 集成,只在 `.env.example` 预留 `REDIS_URL` 配置项。开发环境继续用现有内存缓存,生产环境如需启用 Redis 可作为后续扩展(不在 P1/P2/P3 范围内)
6. **NextAuth.js 不实现**:P2 沿用后端现有静态 Token 鉴权,前端通过请求拦截器注入 `Authorization: Bearer <token>`。NextAuth.js 多用户登录系统是未来扩展,不在 P1/P2/P3 范围内

---

## 3. 子项目分解

按层分解,每层独立验证,风险最低。

| 阶段 | 名称 | 范围 | 依赖 | 验收 |
|------|------|------|------|------|
| **P1** | 后端修复 + 免费 API 扩展 | 修复遗留问题,接入 8 个免费 Provider | 无 | 8 Provider 故障切换 + 204测试无回归 |
| **P2** | 前端 Next.js 重写 | 从零构建金融终端深色风前端,11 页面 | P1(需后端 API) | 11 页面 + 响应式 + Vercel部署 |
| **P3** | 新功能开发 | 回测可视化 + AI 生成话术 | P1(后端)+ P2(前端) | 5 回测API + 5图表 + AI话术 |

---

## 4. P1 详细设计:后端修复 + 免费 API 扩展

### 4.1 免费 LLM Provider 接入

基于 14 个免费 API 调研(详见附录 B),接入以下 8 个 Provider,全部走 `provider_type: "openai"`:

| 优先级 | Provider | 模型 | Base URL | 角色 |
|--------|----------|------|----------|------|
| 100 | 智谱 GLM | glm-4-flash | `https://open.bigmodel.cn/api/paas/v4/` | Primary 1(永久免费+无token上限) |
| 95 | 硅基流动 | Qwen/Qwen2.5-7B-Instruct | `https://api.siliconflow.cn/v1` | Primary 2(9B以下永久免费) |
| 90 | Agnes AI | agnes-2.0-flash | `https://apihub.agnes-ai.com/v1` | Primary 3(全模态永久免费) |
| 85 | 阿里百炼 | qwen-turbo | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Primary 4(qwen-turbo永久免费) |
| 80 | Groq | llama-3.3-70b-versatile | `https://api.groq.com/openai/v1` | Fallback 1(国内可直连,极速) |
| 75 | OpenRouter | qwen/qwen-3-235b:free | `https://openrouter.ai/api/v1` | Fallback 2(聚合26+免费模型) |
| 70 | DeepSeek | deepseek-chat | `https://api.deepseek.com` | Fallback 3(低价兜底) |
| 65 | Cloudflare | @cf/meta/llama-3.3-70b-instruct-fp8-fast | `https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1` | Fallback 4(边缘节点) |

**保留现有 Provider**:TTAPI(priority 0)、Gemini(priority 2)、Anthropic(priority 3)、Ollama(priority 10)

**故障切换链路**:

```
Tier 1(国内直连+永久免费,主用):
  智谱GLM-4-Flash → 硅基流动Qwen2.5 → Agnes AI → 阿里qwen-turbo

Tier 2(国际+国内可直连,故障切换):
  Groq Llama70B → OpenRouter → Cloudflare Workers AI

Tier 3(付费兜底,深度分析):
  DeepSeek(价格极低,可视为"近免费")
```

### 4.2 LLMRouter 改造点

**文件**:`src/core/llm_router.py`

1. **`_load_from_env` 扩展**:新增 8 个 Provider 配置块,从环境变量读取
2. **令牌桶限流**:按 Provider 配置 RPM,超限自动等待(避免 429)
3. **指数退避重试**:失败时 1s → 2s → 4s 重试,最多 3 次
4. **Key 轮换**:支持 `ZHIPU_API_KEY_K1,K2` 多 Key 负载均衡
5. **健康检查**:新增 `_health_check` 后台任务(APScheduler),5 分钟 ping 各 Provider

### 4.3 修复项

基于 S7 报告"后续建议"章节:

| # | 问题 | 修复方案 | 涉及文件 |
|---|------|---------|---------|
| 1 | Git 未提交 S2-S7 | 提交全部累计变更 | - |
| 2 | fund/dashboard/trade 集成测试缺失 | 补充集成测试 | tests/ |
| 3 | 缓存未持久化跨进程 | 预留 REDIS_URL 配置,不实现 Redis 集成(后续扩展) | src/core/cache.py + .env.example |
| 4 | Provider 限流未实现 | 新增令牌桶限流 + 指数退避重试 | src/core/llm_router.py |
| 5 | API Key 轮换 | 支持多 Key 负载均衡 | src/core/llm_router.py |
| 6 | 健康检查缺失 | 新增 `/health/llm` 端点,5分钟 ping | web/routes/config.py |
| 7 | .env.example 未更新 | 补充 8 个新 Provider 的环境变量 | .env.example |

### 4.4 环境变量扩展

**文件**:`.env.example`

```bash
# === 现有 Provider ===
TTAPI_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OLLAMA_BASE_URL=

# === P1 新增:免费 LLM Provider ===
# 智谱 GLM(永久免费,推荐 Primary 1)
ZHIPU_API_KEY=
ZHIPU_MODEL=glm-4-flash

# 硅基流动(9B以下永久免费,推荐 Primary 2)
SILICONFLOW_API_KEY=
SILICONFLOW_MODEL=Qwen/Qwen2.5-7B-Instruct

# Agnes AI(全模态永久免费,推荐 Primary 3)
AGNES_API_KEY=
AGNES_MODEL=agnes-2.0-flash

# 阿里云百炼(qwen-turbo 永久免费,推荐 Primary 4)
DASHSCOPE_API_KEY=
DASHSCOPE_MODEL=qwen-turbo

# Groq(国内可直连,极速,推荐 Fallback 1)
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile

# OpenRouter(聚合免费模型,推荐 Fallback 2)
OPENROUTER_API_KEY=
OPENROUTER_MODEL=qwen/qwen-3-235b:free

# DeepSeek(低价兜底,推荐 Fallback 3)
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-chat

# Cloudflare Workers AI(边缘节点,推荐 Fallback 4)
CF_ACCOUNT_ID=
CF_API_TOKEN=
CF_MODEL=@cf/meta/llama-3.3-70b-instruct-fp8-fast

# === Redis(仅预留配置,P1/P2/P3 不实现,后续扩展) ===
REDIS_URL=
```

### 4.5 P1 验收标准

- [ ] 8 个新 Provider 配置可加载,故障切换链路验证通过
- [ ] 令牌桶限流 + 重试生效(Groq 30 RPM 不超限)
- [ ] `/health/llm` 端点返回各 Provider 状态
- [ ] 现有 204 测试无回归
- [ ] 新增 Provider 集成测试(每个 Provider 至少 1 个用例)
- [ ] .env.example 更新完整
- [ ] Git 提交 S2-S7 累计变更

---

## 5. P2 详细设计:前端 Next.js 重写

### 5.1 技术栈

| 层级 | 选型 | 理由 |
|------|------|------|
| 框架 | Next.js 15(App Router) | SSR+CSR 混合,SEO 友好,Vercel 原生部署 |
| 语言 | TypeScript | 类型安全 |
| 样式 | Tailwind CSS 4 | 原子化,深色主题易实现 |
| 组件库 | shadcn/ui | 可定制,不锁框架,符合终端风 |
| 图表 | Recharts + ECharts | ECharts 处理 K线/专业金融图,Recharts 处理普通图 |
| 状态 | Zustand + TanStack Query | Zustand 管理全局,TanStack Query 管理服务端态 |
| 动画 | Framer Motion | 微交互,数据跳动 |
| 表单 | react-hook-form + zod | 与后端 Pydantic 对齐 |
| 通知 | sonner | 轻量美观 |
| 粒子 | tsParticles | 终端背景效果 |

### 5.2 设计系统(金融终端深色风)

#### 色彩系统

```
背景层级:
  bg-base:     #0A0E1A  主背景(近黑深蓝)
  bg-surface:  #131826  卡片背景
  bg-elevated: #1C2333  悬浮元素
  bg-hover:    #242B3D  悬停态

文字层级:
  text-primary:   #E6EAF2  主文字
  text-secondary: #8B92A8  次要文字
  text-muted:     #5A6378  辅助文字

语义色:
  accent-cyan:   #00D9FF  主强调(青蓝,终端感)
  accent-purple: #B084F7  次强调(紫,科技感)
  profit-red:    #FF4757  涨(A股红涨)
  loss-green:    #00C896  跌(A股绿跌)
  warning:       #FFA502  警告
  danger:        #FF4757  危险
```

#### 排版规范

```
字体:JetBrains Mono(数字/代码)+ Inter(正文)+ Noto Sans SC(中文)
层级:
  H1: 32px / 700 / 1.2    页面标题
  H2: 24px / 600 / 1.3    区块标题
  H3: 18px / 600 / 1.4    卡片标题
  Body: 14px / 400 / 1.6  正文
  Caption: 12px / 400 / 1.5  辅助
数字:JetBrains Mono / tabular-nums / 等宽对齐
```

#### 核心组件

| 组件 | 设计要点 |
|------|---------|
| Button | 3 变体(primary 青蓝实心 / ghost 描边 / danger 红实心)+ 3 尺寸 |
| Card | surface 背景 + 1px border + 12px 圆角 + 微光晕 |
| Table | 紧凑行高(36px)+ 等宽数字 + 涨跌色 + sticky 表头 |
| Input | surface 背景 + 青蓝 focus 边框 |
| Badge | 语义色 + 10px 字号 + 圆角 |
| KLineChart | ECharts 深色主题 + 十字光标 + 缩放 |
| DataTicker | 顶部实时滚动数据条 |

### 5.3 页面规划(11 页面)

| # | 页面 | 路由 | 核心功能 |
|---|------|------|---------|
| 1 | 行情总览 | `/` | 大盘指数 + 涨跌榜 + 热门股 + 数据Ticker |
| 2 | 智能分析 | `/analysis` | 7智能体辩论可视化 + 多空对抗流程图 |
| 3 | 策略中心 | `/strategies` | 6 策略卡片 + 回测入口 |
| 4 | 交易模拟 | `/trade` | 模拟下单 + 持仓管理 |
| 5 | 持仓管理 | `/positions` | 持仓表 + 盈亏分析 |
| 6 | 基金建议 | `/fund` | 三信号雷达图 + 建议卡片 |
| 7 | 国际市场 | `/global` | 5指数 + 美股/港股热门 |
| 8 | 数据监控 | `/monitor` | 涨停监控 + 资金流 + 龙虎榜 |
| 9 | 话术库 | `/scripts` | 24模板浏览 + 智能匹配 + AI生成(P3) |
| 10 | 系统配置 | `/config` | LLM Provider状态 + 鉴权设置 |
| 11 | 回测中心 | `/backtest` | 策略选择 + 参数配置 + 结果可视化(P3) |

### 5.4 响应式适配

| 断点 | 宽度 | 布局策略 |
|------|------|---------|
| mobile | <768px | 单列 + 抽屉导航 + 卡片堆叠 |
| tablet | 768-1024px | 双列 + 折叠侧栏 |
| desktop | ≥1024px | 固定侧栏 + 多列网格 |
| wide | ≥1440px | 侧栏 + 主区 + 右侧详情面板 |

### 5.5 前后端联调

- **跨域**:Render 后端配置 CORS 允许 Vercel 域名
- **API 客户端**:TanStack Query 封装,baseURL 走环境变量 `NEXT_PUBLIC_API_URL`
- **鉴权**:请求拦截器注入 `Authorization: Bearer <token>`
- **错误处理**:统一 401 跳登录,5xx 显示 sonner 错误提示

### 5.6 P2 验收标准

- [ ] 11 页面全部实现,路由可访问
- [ ] 深色设计系统落地,色彩/排版/组件统一
- [ ] 响应式适配 mobile/tablet/desktop/wide
- [ ] API 联调通过,数据正常显示
- [ ] Vercel 部署成功,可访问
- [ ] Lighthouse 性能分 ≥80

---

## 6. P3 详细设计:新功能开发

### 6.1 功能一:回测可视化

**现状**:`scripts/backtest.py` 存在但无前端展示,`scripts/verify_backtest.py` 仅命令行验证。

**新增内容**:

| 层级 | 交付 | 说明 |
|------|------|------|
| 后端 | `web/routes/backtest.py` | 新增 5 个 API:启动/查询/列表/详情/对比 |
| 后端 | `src/core/backtest.py` 增强 | 复用现有,补充结果持久化(SQLite) |
| 前端 | `/backtest` 页面(第11页) | 策略选择 + 参数配置 + 结果可视化 |

#### 可视化图表组件(ECharts)

| 图表 | 用途 | 数据来源 |
|------|------|---------|
| 净值曲线 | 策略 vs 基准收益对比 | equity_curve |
| 回撤图 | 最大回撤时段标注 | drawdown |
| 交易点标记 | K线上买卖点叠加 | trades |
| 收益分布 | 月度/日度收益柱状图 | monthly_returns |
| 绩效雷达 | 5维评估(收益/夏普/回撤/胜率/换手) | metrics |

#### 关键指标

```python
{
  "total_return": 0.0,      # 总收益率
  "annual_return": 0.0,     # 年化收益率
  "max_drawdown": 0.0,      # 最大回撤
  "sharpe_ratio": 0.0,      # 夏普比率
  "win_rate": 0.0,          # 胜率
  "trade_count": 0,         # 交易次数
  "monthly_returns": [],    # 月度收益序列
  "equity_curve": [],       # 净值曲线
  "trades": []              # 交易明细
}
```

#### API 设计

```
POST /api/backtest/start    启动回测(异步,返回 task_id)
GET  /api/backtest/{task_id} 查询进度
GET  /api/backtest/list      回测历史列表
GET  /api/backtest/{id}      回测详情
POST /api/backtest/compare   多回测对比
```

### 6.2 功能二:AI 生成话术

**现状**:`src/analysis/script_library.py` 是 24 个静态模板 + 变量填充,响应快但缺乏个性化。

**改造方案**:保留静态模板(默认),新增 AI 生成模式(可选开关)。

| 模式 | 触发 | 延迟 | 成本 | 适用场景 |
|------|------|------|------|---------|
| 静态模板(默认) | 开关关闭 | <10ms | 0 | 快速浏览、批量生成 |
| AI 生成(可选) | 开关开启 | 2-5s | LLM 调用 | 个性化建议、深度解读 |

**新增内容**:

| 层级 | 交付 | 说明 |
|------|------|------|
| 后端 | `src/analysis/script_library.py` 增强 | 新增 `generate_ai_script` 函数 |
| 后端 | `web/routes/scripts.py` 扩展 | 新增 `POST /api/scripts/ai-generate` |
| 前端 | 话术库页增强 | 静态/AI 切换开关 + AI生成卡片 |

#### AI 生成 Prompt 设计

```python
SYSTEM_PROMPT = """你是专业的A股投资顾问,基于以下分析数据生成投资建议话术。
要求:
1. 语气专业但易懂,避免绝对化表述
2. 包含具体数据支撑(涨跌幅/估值/资金流向)
3. 给出明确的操作建议(加仓/减仓/持有/观望)
4. 提示风险点
5. 控制在150字以内

输入数据:
- 股票代码: {symbol}
- 当前价: {price}
- 涨跌幅: {change_pct}
- 智能体结论: {agent_result}
- 技术指标: {indicators}
- 基本面: {fundamentals}
"""
```

**故障降级**:AI 生成失败(Provider不可用/超时)→ 自动回退静态模板 + 日志告警。

### 6.3 P3 验收标准

**回测可视化**:
- [ ] 5 个 API 可用,异步回测不阻塞
- [ ] 5 类图表正确渲染,数据准确
- [ ] 回测历史可查询、可对比
- [ ] 回测结果持久化

**AI 生成话术**:
- [ ] 静态/AI 模式切换正常
- [ ] AI 生成话术符合 Prompt 规范
- [ ] Provider 故障时自动降级静态模板
- [ ] 响应时间 ≤5s

### 6.4 P3 测试要求

| 类型 | 覆盖 |
|------|------|
| 单元测试 | backtest 核心函数、AI话术生成函数 |
| 集成测试 | 5 个回测 API + AI话术 API |
| 边界测试 | 空数据/超时/Provider全挂 |

---

## 7. 风险与应对

| 风险 | 等级 | 应对策略 |
|------|------|---------|
| 免费 API 限流(智谱30并发/Groq 30RPM) | 中 | 令牌桶限流 + 多 Provider 故障切换 + Key 轮换 |
| 免费 API 政策变动(额度削减/停止免费) | 中 | 8 Provider 冗余,任一停服不影响整体;每月检查政策 |
| 前后端跨域问题 | 低 | Render 配置 CORS 允许 Vercel 域名 |
| 回测异步任务阻塞 | 中 | APScheduler 后台执行,前端轮询进度 |
| AI 话术 Provider 全挂 | 低 | 自动降级静态模板,保证可用性 |
| Next.js 学习成本 | 低 | 用户技能栈包含 Next.js 15 + React 19 |
| 数据源接口失效(akshare) | 中 | 6 源降级 + mootdx(TCP协议,永不封IP)兜底 |
| 实盘交易合规风险 | 高 | 不接实盘,仅做分析建议 |

---

## 8. 交付物清单

### 8.1 代码交付

| 阶段 | 交付物 |
|------|--------|
| P1 | 后端修复 + 8 Provider 接入 + 集成测试 + .env.example 更新 |
| P2 | Next.js 前端工程 + 11 页面 + 设计系统 + Vercel 部署配置 |
| P3 | 回测 API + 回测页面 + AI话术生成 + 测试 |

### 8.2 文档交付

- 本设计文档(已完成)
- 实现计划(writing-plans 生成)
- README 更新(反映新架构)
- API 文档(FastAPI 自动生成 + 前端 API 说明)
- 测试报告

### 8.3 部署交付

- Render 后端(render.yaml)
- Vercel 前端(vercel.json + 环境变量)
- 可访问的线上 URL

---

## 附录 A:GitHub 开源项目调研结论

基于 19 个开源量化项目调研,三大核心参考:

1. **TradingAgents**(TauricResearch,53k+ stars)— 多智能体辩论 + 多 LLM 路由的架构蓝本,与本项目核心特征 90% 契合。采用 LangGraph 状态图编排,`with_structured_output` 输出 Pydantic 实例。
2. **Vibe-Trading**(HKUDS,24k+ stars)— 工程化落地与安全设计的标杆,数据源降级策略(mootdx>Tencent>东方财富)、风控 Kill Switch、MCP 集成可直接借鉴。
3. **AStockAgents**(cliu-debug)— A 股本土化的多智能体实现,10 Agent + 博弈论辩论 + LangGraph + FastAPI 技术栈与本项目完全一致。

**推荐技术栈组合**(调研参考,实际采用见第2章架构决策):

```
LangGraph(编排) + FastAPI(后端) + Next.js(前端) +
AKShare + BaoStock + mootdx(三层数据源降级) +
LangChain init_chat_model(多 LLM 路由) +
Redis + PostgreSQL + DuckDB(存储) +
Celery(任务队列) + Docker Compose(部署)
```

> **注**:上述为调研推荐的重度技术栈。本项目实际采用轻量级方案:保留现有 FastAPI + SQLite + 自研 LLMRouter(不引入 LangGraph/LangChain/Celery/Docker),以降低重构风险和复杂度。LangGraph 多智能体编排作为未来架构演进方向,不在 P1/P2/P3 范围内。

**需要避免的坑**:
1. 单 Agent 直出结论导致幻觉 → 必须用 LangGraph 强制拆解为原子化节点
2. 数据源单点依赖 → 必须多源降级(mootdx 优先,TCP协议永不封IP)
3. 前瞻偏差(Lookahead Bias)→ 严格 point-in-time 安全
4. State 设计混乱 → 把 State 当作强契约协议,明确只读/读写权限
5. 多 LLM 结构化输出不一致 → Factory 模式自动选择

---

## 附录 B:免费 LLM API 调研结论

基于 14 个免费 LLM API 调研,Top 5 推荐:

| 排名 | 服务 | 免费额度 | 国内可访问 | 中文金融适配 |
|------|------|----------|------------|--------------|
| 1 | 智谱 GLM-4-Flash | 永久免费 + 无token上限 + 2000万新用户额度 | 直连 | 极高(中文原生,128K长文本) |
| 2 | 硅基流动 SiliconFlow | 9B以下模型永久免费 + 2000万新用户token + 1000RPM | 直连 | 极高(聚合Qwen/GLM/DeepSeek) |
| 3 | Agnes AI | 全模态永久免费,无token限制 | 直连 | 高(中文友好) |
| 4 | Groq | 14400 RPD(8B)+ 1000 RPD(70B) | 国内可直连 | 高(Llama 70B推理强,延迟极低) |
| 5 | 阿里云百炼 | 7000万新用户token + qwen-turbo永久免费 | 直连 | 极高(Qwen系列中文金融最强) |

**接入策略**:全部走 `provider_type: "openai"`,无需修改 LLMRouter 核心代码。

**故障切换链路**:
- Tier 1(国内直连+永久免费):智谱 → 硅基流动 → Agnes → 阿里
- Tier 2(国际+国内可直连):Groq → OpenRouter → Cloudflare
- Tier 3(付费兜底):DeepSeek(价格极低,近免费)

---

## 附录 C:设计灵感参考

前端设计参考以下 10 个优质 UI 设计案例(金融终端深色风):

1. **Bloomberg Terminal** — 专业金融终端标杆,信息密度高
2. **TradingView** — K线图与实时数据交互
3. **同花顺高端版** — A股本土化终端
4. **Stripe Dashboard** — 现代深色 SaaS 设计
5. **Linear** — 极简深色界面,克制配色
6. **Vercel Dashboard** — Next.js 原生深色风
7. **Raycast** — 终端式交互,快捷键驱动
8. **Framer Motion 官网** — 数据跳动动画
9. **Dribbble 金融仪表盘** — 视觉层次感
10. **shadcn/ui 官网** — 组件设计规范

---

**设计文档完成。等待用户审阅。**
