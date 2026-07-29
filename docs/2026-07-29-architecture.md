# QuantFlow Pro 技术架构文档

> 版本: 1.0
> 更新日期: 2026-07-29
> 项目根目录: `d:\xm\wz\Fund-Assessment`
> 部署地址: https://fund-assessment.onrender.com

---

## 1. 项目概览

### 1.1 产品定位

QuantFlow Pro 是一款**基金投资决策辅助工具**,基于消息面+大盘研判+基金重仓分析,为偏专业的基金投资者提供买卖建议的智能分析平台。

**核心价值**:
1. **穿透式分析**: 不只看基金净值,而是穿透到重仓股、所在板块、大盘趋势
2. **消息面驱动**: 聚合新闻+公告+研报+舆情,用多智能体辩论提炼影响
3. **建议而非操作**: 给明确的加仓/减仓/持有/观望建议,但不代用户执行买卖

### 1.2 技术栈

| 层级 | 技术选型 |
|------|---------|
| 后端框架 | FastAPI + Pydantic + Uvicorn(ASGI) |
| 核心语言 | Python 3.12.11(Render 部署版本) |
| 数据处理 | Pandas + NumPy(纯实现,无 pandas_ta/numba) |
| 数据存储 | SQLite(原生 sqlite3,无 ORM) |
| 日志 | Loguru |
| 定时任务 | APScheduler |
| 前端 | 单页 HTML + Vanilla JS + 原生 CSS(GitHub 深色风) |
| 部署 | Render Web Service(Free 实例) |

### 1.3 关键指标

| 指标 | 数值 |
|------|------|
| Python 源文件 | 50 个(43 业务模块) |
| 测试文件 | 48 个(866 用例,80%+ 覆盖率) |
| API 端点 | 94 个(13 个路由文件) |
| LLM Provider | 11 个(2 个启用:zhipu_glm priority=100 + agnes priority=90) |
| 前端代码行数 | 4325 行(单文件 SPA) |
| 异常处理 | except:pass 数量: 0(批次 1 已清理) |

---

## 2. 整体架构

### 2.1 架构图

```
┌────────────────────────────────────────────────────────────────┐
│ 前端 SPA (web/static/index.html, 4325 行)                      │
│ - 10 个核心页面(行情/分析/基金/消息面/国际/监控/持仓/交易/策略/配置) │
│ - 响应式断点: 360px / 768px / 1024px / 1200px                  │
│ - 4325 行 Vanilla JS,内置 fetch 封装+toast+模态框              │
├────────────────────────────────────────────────────────────────┤
│ FastAPI 应用层 (web/api.py)                                    │
│ - lifespan 初始化: data_source / executor / risk_manager       │
│ - CORS + 请求日志中间件                                         │
│ - 12 个路由模块注册(94 个端点)                                  │
├────────────────────────────────────────────────────────────────┤
│ 业务逻辑层                                                     │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐  │
│ │ LLM Router  │ │ 7 智能体    │ │ 消息面聚合引擎 (P1-1)  │  │
│ │ 11 Provider │ │ 辩论框架    │ │ 4 源: 新闻/公告/舆情/AI│  │
│ │ 令牌桶+熔断 │ │             │ │                         │  │
│ └─────────────┘ └─────────────┘ └─────────────────────────┘  │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐  │
│ │ 基金建议    │ │ 重仓股分析   │ │ 大盘研判 (P1-3)         │  │
│ │ 五信号融合  │ │ (P1-2)      │ │ 温度计 0-100            │  │
│ │ (P1-4)      │ │             │ │                         │  │
│ └─────────────┘ └─────────────┘ └─────────────────────────┘  │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐  │
│ │ 6 策略引擎  │ │ 风控管理器  │ │ 交易执行器              │  │
│ │ 含回测      │ │ 4 级限制     │ │ 模拟/实盘 Broker        │  │
│ └─────────────┘ └─────────────┘ └─────────────────────────┘  │
├────────────────────────────────────────────────────────────────┤
│ 数据源层 (6 源 3 级降级)                                        │
│ ┌──────────┬──────────┬──────────┬──────────┬──────────┐      │
│ │ 东方财富 │  腾讯    │  新浪    │  mootdx  │ akshare  │      │
│ │ (主源)   │ (基金主) │ (二级)   │ (三级)   │ (旧版)   │      │
│ └──────────┴──────────┴──────────┴──────────┴──────────┘      │
├────────────────────────────────────────────────────────────────┤
│ 基础设施                                                       │
│ - SQLite: risk_state / trade_records / trades (3 张表)         │
│ - JSON 文件: user_fund_positions.json / user_positions.json    │
│ - 磁盘缓存: DataCache (TTL 15-300s)                            │
│ - 鉴权: 静态 Token (hmac.compare_digest, 时序攻击保护)         │
└────────────────────────────────────────────────────────────────┘
```

### 2.2 关键架构决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 数据源 | 6 源 3 级自动降级 | 单一数据源故障时自动切换,提升可用性 |
| LLM | 11 Provider 路由+令牌桶+熔断 | 多 Provider 故障切换,免费模型优先 |
| 数据库 | SQLite(无 ORM) | 状态持久化够用,避免迁移风险 |
| 前端 | 单页 HTML(Vanilla JS) | 硬约束保留原 CSS/配色/布局/字体 |
| 部署 | Render Free(非 Blueprint) | Blueprint 需付款,Web Service 免费实例够用 |
| 鉴权 | 静态 Token(非 OAuth) | 多用户系统是未来扩展,当前够用 |
| Redis | 仅预留配置 | P1/P2/P3 不实现,后续扩展 |

---

## 3. 目录结构

```
Fund-Assessment/
├── config/                     # 配置文件
│   ├── settings.yaml           # 主配置(AI/数据源/缓存/风控/通知/日志)
│   └── strategies.yaml         # 策略参数配置
├── docs/                       # 设计文档与交付报告
│   ├── superpowers/specs/      # 关键设计规格
│   ├── 2026-07-29-architecture.md       # 本文档
│   ├── 2026-07-29-api-reference.md      # API 文档
│   ├── 2026-07-29-user-manual.md        # 用户手册
│   ├── 2026-07-29-test-report.md        # 测试报告
│   ├── 2026-07-29-delivery-report.md    # 交付报告
│   ├── 2026-07-29-ux-assessment.md      # UX 评估
│   ├── 2026-07-29-opensource-research.md# 开源调研
│   └── 2026-07-29-design-system.md      # 设计系统
├── scripts/                    # 命令行脚本(独立运行)
│   ├── backtest.py             # 回测入口
│   ├── cb_monitor.py           # 可转债监控
│   ├── generate_daily_report.py# 每日报告生成
│   └── limit_up_monitor.py     # 涨停监控
├── src/                        # 核心业务代码
│   ├── agents/                 # 多智能体(7 文件)
│   ├── analysis/               # 分析模块(11 文件)
│   ├── core/                   # 核心基础设施(11 文件)
│   ├── monitor/                # 监控告警(2 文件)
│   ├── strategies/             # 交易策略(6 文件)
│   └── utils/                  # 工具(auth/config/convert/logger/notify)
├── tests/                      # pytest 测试用例(48 文件,866 用例)
├── web/                        # Web 层
│   ├── api.py                  # FastAPI 应用入口
│   ├── routes/                 # 路由模块(12 文件,94 个端点)
│   └── static/index.html       # 前端单页应用(4325 行)
├── pyproject.toml              # 项目元数据与依赖
├── requirements.txt            # Render 部署依赖
├── render.yaml                 # Render 部署配置
└── README.md
```

---

## 4. 核心模块说明

### 4.1 `src/core/` 核心基础设施(11 文件)

| 文件 | 职责 | 关键类/函数 |
|------|------|------------|
| `ai_service.py` | AI 分析服务(股票多智能体分析) | `analyze_stock`、`quick_analysis`、`multi_analyze`、`analyze_portfolio`、`get_market_outlook`、`search_news` |
| `llm_router.py` | 多 LLM Provider 路由 | `LLMProvider`、`TokenBucket`、`LLMRouter`、`LLMResponse`、`get_llm_router()` |
| `data_source_v2.py` | **增强数据源(6 源 3 级降级+国际市场)** | 80+ 函数: `get_kline_mootdx`、`get_realtime_quote_tencent`、`get_global_market_overview`、`_parallel_fetch` |
| `data_source.py` | 旧版数据源(akshare/tushare,启动时尝试 import) | `AkShareSource`、`TushareSource`、`DataSourceManager` |
| `cache.py` | 磁盘缓存(TTL) | `DataCache`、`_serialize` |
| `risk_manager.py` | 风险管理器(4 级限制) | `RiskLevel`、`RiskStatus`、`RiskManager`(持久化 SQLite) |
| `executor.py` | 交易执行器(模拟/实盘) | `Order`、`Trade`、`SimulatedBroker`、`LiveBroker`、`TradeExecutor` |
| `backtest.py` | 回测引擎 | `BacktestEngine`、`new_high_strategy`、`limit_up_strategy`、`cb_t0_strategy` |
| `scheduler.py` | APScheduler 任务调度 | `Scheduler` |
| `data_validator.py` | 数据质量校验(4 维度评分) | `DataValidator`、`get_data_validator()` |
| `response.py` | API 响应模型 | `APIResponse`、`success()`、`error()` |

### 4.2 `src/agents/` 智能体(7 文件)

| 文件 | 角色 | 立场 |
|------|------|------|
| `base.py` | 基类 | - |
| `fundamental_agent.py` | 基本面分析师 | 中立 |
| `technical_agent.py` | 技术分析师 | 中立 |
| `sentiment_agent.py` | 情绪分析师 | 中立 |
| `news_agent.py` | 新闻分析师 | 中立 |
| `research_team.py` | 多空研究团队 | Bull(多)/Bear(空) |
| `trading_manager.py` | 交易管理器 | Trader/RiskManager/PortfolioManager |

### 4.3 `src/analysis/` 分析模块(11 文件)

| 文件 | 功能 | P1 关键 |
|------|------|---------|
| `fundamental.py` | 基本面(估值/盈利/成长评分) | - |
| `technical.py` | 技术面(纯 pandas/numpy) | - |
| `news.py` | 新闻情绪分析 | - |
| `news_aggregator.py` | **消息面聚合(P1-1)** | ✓ |
| `sentiment.py` | 市场情绪分析 | - |
| `capital_flow.py` | 资金流分析 | - |
| `fund_holdings.py` | **重仓股分析(P1-2)** | ✓ |
| `market_assessment.py` | **大盘研判(P1-3)** | ✓ |
| `fund_advisor.py` | 基金建议规则引擎 v1 | - |
| `fund_advisor_v2.py` | **基金建议五信号融合 v2(P1-4)** | ✓ |
| `multi_agent_fund.py` | **基金多智能体分析主入口(P1-5)** | ✓ |
| `script_library.py` | 话术库(24 模板) | - |

### 4.4 `src/strategies/` 策略(6 文件)

| 文件 | 用途 |
|------|------|
| `trading_quant.py` | TradingQuant 五维评分 |
| `bspro_quant.py` | BSProQuant 22 因子量化 |
| `cb_t0_sniper.py` | 可转债 T+0 狙击 |
| `limit_up.py` | 涨停板分析 |
| `stock_monitor.py` | 股票监控告警 |
| `a_stock_analyst.py` | A 股综合分析师 |

### 4.5 `web/routes/` 路由(12 文件,94 端点)

| 路由文件 | 路由前缀 | 端点数 |
|---------|---------|--------|
| `market.py` | `/api/market` | 27 |
| `fund.py` | `/api/fund` | 9 |
| `agent.py` | `/api/agent` | 9 |
| `config.py` | `/api/config` | 7 |
| `monitor.py` | `/api/monitor` | 6 |
| `global_market.py` | `/api/global` | 6 |
| `scripts.py` | `/api/scripts` | 6 |
| `strategy.py` | `/api/strategy` | 6 |
| `trade.py` | `/api/trade` | 5 |
| `news.py` | `/api/news` | 4 |
| `dashboard.py` | `/api/dashboard` | 4 |
| `holdings.py` | `/api/holdings` | 2 |
| **总计** | - | **91**(含根路径+健康检查共 3) |

---

## 5. 数据流:基金多智能体分析(P1-5)

以 `POST /api/agent/fund_analyze` 为例,完整数据流:

```
1. 用户请求 POST /api/agent/fund_analyze
   Body: { fund_code, fund_name, cost_nav, shares, mode }
   ↓
2. web/routes/agent.py: fund_multi_analyze(req)
   Pydantic 校验 FundMultiAnalyzeRequest
   ↓
3. src/analysis/multi_agent_fund.py:
   analyze_fund_with_agents(fund_code, fund_name, cost_nav, shares, mode="deep")
   ↓
4. 并行抓取 5 源数据 (asyncio.gather, return_exceptions=True):
   ├─ _fetch_nav_history  → ds2.get_fund_history_tencent(1y)
   ├─ _fetch_realtime     → ds2.get_fund_realtime_tencent([code])
   ├─ _fetch_holdings     → analyze_fund_holdings(fund_code)
   ├─ _fetch_news         → get_news_feed(fund_code=...)
   └─ _fetch_market       → get_market_thermometer()
   ↓
5. 串行补抓重仓股实时行情:
   stock_quotes = ds2.get_realtime_quote_tencent(stock_codes)
   ↓
6. 计算五信号融合(fund_advisor_v2.analyze_fund_five_signals):
   technical(20%) + fundamental(20%) + news(25%)
   + holdings(20%) + market(15%) → final_score/direction/action
   ↓
7. 构建 context = { fund_quote, nav_history, news_data,
                    holdings_data, stock_quotes, thermometer,
                    five_signals }
   ↓
8. _build_fund_analysis_prompt(fund_code, fund_name, context, cost_nav, shares, mode)
   生成 7 角色分析指令 + A股约束 + JSON 输出模板
   ↓
9. messages = [system(研究总监人设), user(prompt)]
   ↓
10. get_llm_router().chat(messages, temperature=0.5, json_mode=True, timeout=120)
    ├─ 按优先级遍历 Providers
    ├─ 令牌桶限流 (rpm)
    ├─ 指数退避重试 (1s→2s→4s, 最多 3 次)
    ├─ 熔断器保护 (连续 3 次失败熔断 30s)
    └─ 故障切换到下一个 Provider
    ↓
11. _parse_fund_analysis_response(response.content, fund_code)
    ├─ 剥离 markdown 代码块
    ├─ json.loads + 正则兜底
    └─ 标准化为 { agent_opinions, bull_bear_debate,
                  risk_debate, portfolio_manager_decision,
                  final_recommendation, action, confidence,
                  analysis_mode, analyst_count, timestamp }
    ↓
12. 异常路径: _fallback_fund_result(fund_code, reason)
    返回 action="HOLD", confidence=0.0, analyst_count=0
    ↓
13. 响应返回 web/routes/agent.py → JSON 响应给客户端
```

### 5.1 7 位分析师角色

| 角色 | 名称 | 数据依赖 |
|------|------|---------|
| `news` | 消息面分析师 | `news_data` |
| `fund` | 基金分析师 | `fund_quote`, `nav_history` |
| `sector` | 板块分析师 | `holdings_data` |
| `technical` | 技术分析师 | `nav_history` |
| `fundamental` | 基本面分析师 | `holdings_data`, `stock_quotes` |
| `risk` | 风险分析师 | `holdings_data` |
| `macro` | 宏观分析师 | `thermometer` |

### 5.2 五信号融合权重

| 信号 | 权重 | 评分来源 |
|------|------|---------|
| 消息面 | 25% | `news_aggregator.get_news_feed` 的 `sentiment_index` |
| 技术面 | 20% | 基金净值 MA5/MA20 趋势 |
| 基本面 | 20% | 重仓股 PE/PB + 净值分位数 |
| 重仓股板块 | 20% | `fund_holdings.analyze_fund_holdings` 的 `nav_impact` + `sector_rotation` |
| 大盘环境 | 15% | `market_assessment.get_market_thermometer` 的 `score` |

---

## 6. 外部依赖

### 6.1 数据源(6 源 3 级降级)

| 数据类型 | 主源 | 二级 | 三级 |
|---------|------|------|------|
| K线 | mootdx | 东方财富 | 新浪 |
| A 股实时行情 | 腾讯 | 东方财富 | - |
| 板块排名 | 东方财富 | 新浪 v1/v2 | 腾讯 |
| 龙虎榜 | 东方财富 | 新浪 | ranking 兜底 |
| 财务快照 | mootdx | 东方财富 | 腾讯 |
| 基金净值 | 腾讯 | - | - |

### 6.2 LLM Provider 配置(2 个启用)

| Provider | 模型 | Base URL | Priority | Timeout | 环境变量 | 状态 |
|----------|------|----------|----------|---------|---------|------|
| `zhipu_glm` | `glm-4-flash` | `https://open.bigmodel.cn/api/paas/v4/` | 100 | 30s | `ZHIPU_API_KEY` | 启用 |
| `agnes` | `agnes-2.0-flash` | `https://apihub.agnes-ai.com/v1` | 90 | 30s | `AGNES_API_KEY` | 启用 |

**核心机制**:
- 多 Key 轮换:优先 `XXX_API_KEYS`(逗号分隔),回退 `XXX_API_KEY`
- 令牌桶限流:`TokenBucket` 按 RPM 限制
- 熔断器:连续 3 次失败熔断 30 秒
- 指数退避:1s → 2s → 4s,最多 3 次重试

### 6.3 第三方服务

| 服务 | 用途 | 环境变量 |
|------|------|---------|
| Tavily | AI 检索(实时新闻) | `TAVILY_API_KEY` |
| TinyFish | AI 检索备用 | `TINYFISH_API_KEY` |
| 东方财富基金搜索 | 基金代码/名称模糊搜索 | 无(直调) |

---

## 7. 数据库设计

项目使用原生 `sqlite3`(无 ORM),共 3 个 SQLite 数据库,3 张表:

### 7.1 `risk_state` 表(风控状态持久化)

数据库路径: `data/risk_state.db`

```sql
CREATE TABLE IF NOT EXISTS risk_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),  -- 单行约束
    total_assets            REAL NOT NULL,
    peak_assets             REAL NOT NULL,
    daily_start_assets      REAL NOT NULL,
    daily_pnl               REAL NOT NULL DEFAULT 0,
    consecutive_stop_losses INTEGER NOT NULL DEFAULT 0,
    is_paused               INTEGER NOT NULL DEFAULT 0,
    pause_until             TEXT,
    is_emergency_stopped    INTEGER NOT NULL DEFAULT 0,
    no_new_positions        INTEGER NOT NULL DEFAULT 0,
    position_reduction      REAL NOT NULL DEFAULT 0.5,
    last_trade_date         TEXT,
    updated_at              TEXT NOT NULL
);
```

### 7.2 `trade_records` 表(风控关联交易记录)

同上数据库 `data/risk_state.db`

```sql
CREATE TABLE IF NOT EXISTS trade_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL,
    price         REAL NOT NULL,
    quantity      REAL NOT NULL,
    amount        REAL NOT NULL,
    profit        REAL,
    is_stop_loss  INTEGER NOT NULL DEFAULT 0,
    timestamp     TEXT NOT NULL
);
```

### 7.3 `trades` 表(交易执行器持久化)

数据库路径: `data/trades.db`

```sql
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id      TEXT NOT NULL UNIQUE,
    order_id      TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL,
    price         REAL NOT NULL,
    quantity      REAL NOT NULL,
    amount        REAL NOT NULL,
    commission    REAL NOT NULL,
    stamp_tax     REAL NOT NULL,
    net_amount    REAL NOT NULL,
    strategy      TEXT,
    reason        TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 7.4 JSON 文件持久化

- `web/user_fund_positions.json`: 基金持仓(JSON,非 SQLite)
- `web/user_positions.json`: 用户股票持仓(JSON)
- `web/watchlist.json`: 自选股列表(JSON)

---

## 8. 部署配置

### 8.1 Render 部署(`render.yaml`)

```yaml
services:
  - type: web
    name: fund-assessment
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn web.api:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /api/health
    envVars:
      - key: PYTHONPATH
        value: /opt/render/project/src
      - key: PYTHONUNBUFFERED
        value: "1"
      - key: PYTHON_VERSION
        value: "3.12.11"
      - key: CORS_ORIGINS
        value: "*"
      # AI Provider(至少配置一个)
      - key: ZHIPU_API_KEY    # 智谱 GLM (priority=100,免费)
        sync: false
      - key: AGNES_API_KEY    # Agnes (priority=90,免费)
        sync: false
      - key: TAVILY_API_KEY   # AI 检索
        sync: false
      # 鉴权
      - key: ADMIN_TOKEN
        sync: false
```

### 8.2 部署约束

| 约束 | 值 | 原因 |
|------|------|------|
| 实例类型 | Free(512MB RAM) | 免费方案 |
| 请求超时 | 100s | Render 限制 |
| AI Provider 超时 | ≤30s | 避免 100s 超时 |
| Python 版本 | 3.12.11 | 规避 numba 在 3.14 的 Rust 编译失败 |
| 依赖 | 须有预编译 wheel | 避免 Render 构建失败 |

### 8.3 其他部署平台

- `fly.toml`: Fly.io 配置
- `railway.json`: Railway 配置
- `vercel.json`: Vercel 配置
- `Procfile`: Heroku 风格

---

## 9. 鉴权与安全

### 9.1 鉴权机制

- **静态 Token**: 通过 `ADMIN_TOKEN` 环境变量配置
- **时序攻击保护**: `hmac.compare_digest` 常数时间比较
- **保护端点**(共 8 个写端点):
  - `fund/positions` POST/DELETE
  - `fund/positions/add` POST
  - `trade/buy` / `trade/sell` / `trade/cancel` POST
  - `config/settings` PUT
  - `config/strategies` PUT
  - `config/user_positions` POST

### 9.2 安全最佳实践

- 密钥不硬编码,全部通过环境变量注入
- 健康检查端点不泄露密钥详情(仅返回布尔值)
- CORS 在生产环境应收紧(当前为 `*`)
- 所有 SQL 使用参数化查询,防 SQL 注入

### 9.3 风控 4 级限制

| 限制项 | 配置值 | 触发动作 |
|--------|--------|---------|
| 系统最大回撤 | 15% | 暂停 5 天 |
| 单日最大亏损 | 5% | 当日停止交易 |
| 策略连续止损 | 3 次 | 仓位缩减 50% |
| 紧急停止 | - | `is_emergency_stopped=1`,需人工解除 |

---

## 10. 扩展性设计

### 10.1 数据源热插拔

新增数据源只需:
1. 在 `data_source_v2.py` 中实现 fetch 函数
2. 在降级链中插入合适位置
3. 无需修改上层调用方

### 10.2 LLM Provider 扩展

新增 Provider 只需:
1. 在 `llm_router.py` 的 `_load_from_env()` 添加配置
2. 设置环境变量
3. 自动按 priority 加入路由

### 10.3 智能体扩展

新增智能体只需:
1. 继承 `BaseAgent`
2. 在 `ANALYST_ROLES` 中添加角色
3. 修改 prompt 模板

### 10.4 策略扩展

新增策略只需:
1. 在 `src/strategies/` 中实现
2. 在 `config/strategies.yaml` 中配置
3. 自动出现在 `/api/strategy/list`

---

## 11. 监控与可观测性

### 11.1 健康检查

- `GET /api/health`: 系统健康(依赖状态+AI Key 状态)
- `GET /api/health/llm`: LLM Provider 健康(状态/优先级/限流)

### 11.2 日志

- Loguru 结构化日志
- 请求日志中间件记录所有 HTTP 请求
- 异常日志: 批次 1 已消除 78 处 except:pass,全部改为 `logger.warning(f"{操作} failed: {e}")`

### 11.3 数据质量

- `GET /api/market/data-quality/{stock_code}`: 4 维度评分(完整性/准确性/及时性/一致性)
- 前端"数据质量徽章"实时展示(realtime/delayed/mock)

---

## 12. 已知限制与未来扩展

### 12.1 当前限制

1. **Render Free 实例**: 512MB RAM,并发 AI Agent 调用会 OOM(已优化为串行)
2. **AI 模型**: 仅使用免费模型(glm-4-flash、agnes-2.0-flash),响应速度受限
3. **前端**: 单页 HTML 4325 行,未来考虑迁移 Next.js
4. **数据库**: SQLite 单写并发受限,不适合高并发场景
5. **多用户**: 当前为单用户系统,无 OAuth

### 12.2 未来扩展方向

| 方向 | 优先级 | 描述 |
|------|--------|------|
| Redis 缓存 | P2 | 替换磁盘缓存,提升性能 |
| 多用户系统 | P2 | OAuth + 用户隔离 |
| 独立消息面页 | P1 | 当前消息面集成在基金页 |
| 决策回测 | P2 | 验证系统建议的历史准确性 |
| 基金对比 | P2 | 多基金横向对比 |
| 个性化预警 | P3 | 关键事件推送 |

---

## 附录: 文档引用

- [API 文档](2026-07-29-api-reference.md)
- [用户手册](2026-07-29-user-manual.md)
- [测试报告](2026-07-29-test-report.md)
- [交付报告](2026-07-29-delivery-report.md)
- [UX 评估](2026-07-29-ux-assessment.md)
- [开源调研](2026-07-29-opensource-research.md)
- [设计系统](2026-07-29-design-system.md)
