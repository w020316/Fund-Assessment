# QuantFlow Pro 量化交易系统

基于多智能体辩论机制的 A 股量化分析系统,融合 7 位专业分析师角色、多空辩论、风险辩论与 A 股特有约束建模,新增国际股市数据、基金建议规则引擎、话术库三大模块。

## 核心亮点

- **7 角色多智能体辩论**:基本面/技术面/情绪面/新闻面/政策面/游资追踪/解禁监控,多空辩论 + 风险辩论 + 组合经理决策
- **多 LLM Provider 路由**:支持 OpenAI/Agnes/Gemini/Anthropic/Ollama,自动故障切换 + 熔断器保护
- **多数据源降级架构**:东方财富/腾讯/新浪/mootdx/akshare/tushare,6 源 3 级自动降级
- **国际股市数据**:腾讯财经接口,覆盖 5 大国际指数 + 美股/港股热门个股
- **基金建议规则引擎**:基于大盘/板块/盈亏三信号生成个性化建议(take_profit/add/dca/hold/watch)
- **话术库**:24 个预置模板 + 变量填充 + 智能匹配,股市+基金双覆盖
- **A 股特有约束建模**:T+1 交易制度、涨跌停限制(10%/20%/5%)、最小交易单位 100 股
- **数据质量校验**:多维度数据质量检查(完整性/时效性/合理性/一致性),质量评分 0-100
- **完整风控体系**:四级风险等级 + 系统回撤限制 + 单日亏损限制 + 紧急停止机制
- **鉴权安全**:静态 Token + 时序攻击防护,6 个写/交易端点保护
- **全栈可部署**:Docker/Fly.io/Railway/Render/Vercel,一键部署

## 技术架构

```
┌──────────────────────────────────────────────────┐
│         Web 前端 (index.html SPA,10 页面)         │
│  行情/智能分析/策略/交易/持仓/基金/国际/数据/监控/配置 │
├──────────────────────────────────────────────────┤
│          FastAPI REST API (10 大路由模块)          │
│  dashboard / strategy / trade / monitor          │
│  config / market / agent / fund                  │
│  global / scripts                                │
├──────────────────────────────────────────────────┤
│              核心业务层                            │
│  ┌──────────┐ ┌──────────┐ ┌───────────────┐    │
│  │LLM Router│ │  Agents  │ │  Strategies   │    │
│  │(多模型路由)│ │(7智能体) │ │  (6交易策略)  │    │
│  └──────────┘ └──────────┘ └───────────────┘    │
│  ┌──────────┐ ┌──────────┐ ┌───────────────┐    │
│  │Fund      │ │Script    │ │  Risk         │    │
│  │Advisor   │ │Library   │ │  Manager      │    │
│  │(基金建议) │ │(话术库)  │ │  (风控)       │    │
│  └──────────┘ └──────────┘ └───────────────┘    │
├──────────────────────────────────────────────────┤
│         数据源层 (多源降级 + 国际股市)              │
│  东方财富 / 腾讯 / 新浪 / mootdx / akshare        │
│  腾讯国际(美股/港股/国际指数)                      │
├──────────────────────────────────────────────────┤
│           基础设施层                               │
│  缓存(TTL+磁盘) / 鉴权(Token) / 日志(loguru)     │
│  配置(YAML) / 部署(Docker/Fly.io/Render/...)     │
└──────────────────────────────────────────────────┘
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Python 3.12 + FastAPI + Uvicorn |
| 数据处理 | Pandas + NumPy + pandas-ta + stockstats |
| 数据源 | akshare, tushare, mootdx(通达信), 东方财富API, 腾讯行情API, 新浪财经API, 腾讯国际股市API |
| AI/LLM | 多 Provider 路由(OpenAI/Agnes/Gemini/Anthropic/Ollama), Tavily 搜索 |
| 任务调度 | APScheduler |
| 数据验证 | Pydantic + 自研 DataValidator |
| 日志 | Loguru |
| 配置 | PyYAML + python-dotenv |
| 鉴权 | 静态 Token + secrets.compare_digest |
| 数据库 | SQLite (风控状态持久化) |
| 部署 | Render (主要), Fly.io, Railway, Vercel |
| 测试 | pytest + TestClient + mock, 56 文件 1394 用例 |

## 快速开始

### 1. 安装依赖

```bash
# 方式一:使用安装脚本(推荐,Windows)
install.bat

# 方式二:手动安装
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入 API 密钥
```

**必填项**(至少配置一个 LLM Provider):
- `TTAPI_API_KEY` 或 `AGNES_API_KEY` — AI 分析所需(推荐使用免费的 Agnes)

**可选项**:
- `TAVILY_API_KEY` — 新闻搜索增强
- `TINYFISH_API_KEY` — 备用 LLM
- `TUSHARE_TOKEN` — Tushare 数据源
- `ADMIN_TOKEN` — 写/交易端点鉴权(不配置则开发模式放行)

### 3. 启动服务

```bash
# 方式一:推荐启动入口(显式设置 PYTHONPATH)
python launch.py                    # 生产模式
python launch.py --reload           # 开发模式(热重载)
python launch.py --port 9000        # 自定义端口

# 方式二:直接 uvicorn
python -m uvicorn web.api:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000 即可使用。

### 4. 运行测试

```bash
# 全量测试(56 文件, 1394 用例)
python -m pytest tests/ -v

# 单个子项目测试
python -m pytest tests/test_cache.py -v
python -m pytest tests/test_fund_advisor.py -v
python -m pytest tests/test_script_library.py -v
python -m pytest tests/test_global_market.py -v
```

## 功能模块

### 行情总览(快捷键 1)
- 三大指数实时行情(上证/深证/创业板)
- 个股实时行情、K 线图、资金流向
- 板块排名、龙虎榜、北向资金
- 数据质量评分与来源标识

### 智能分析(快捷键 2)
- 7 位专业分析师多维度分析
- 多空辩论(Bull/Bear)+ 风险辩论(激进/保守/中性)
- 组合经理最终决策(BUY/HOLD/SELL)
- A 股特有约束(T+1/涨跌停/最小交易单位)
- 支持深度分析和快速分析两种模式
- 话术库浏览面板(分类 + 场景过滤)

### 策略中心(快捷键 3)
- TradingQuant:五维评分模型(技术 25%/资金 30%/基本面 10%/新闻 20%/情绪 15%)
- 涨停板分析:首板/二板/三板 + 分级、封板质量评分
- 可转债 T+0 狙击:正股涨停联动、转股溢价率计算
- 多因子量化 BSProQuant:22 个因子评分

### 交易执行(快捷键 4)
- 模拟券商(SimulatedBroker):佣金 + 印花税计算
- 实盘券商接口预留(LiveBroker)
- 交易执行器(TradeExecutor):信号 → 风控 → 执行
- 鉴权保护:买/卖/撤单需 ADMIN_TOKEN

### 持仓管理(快捷键 5)
- 持仓组合分析、集中度风险评估
- 板块暴露分析、再平衡建议
- 止损/止盈建议

### 基金管理(快捷键 6)
- 用户持仓 CRUD(代码/名称/份额/成本净值/买入日期)
- 基金建议规则引擎:大盘信号 + 板块信号 + 盈亏信号 → take_profit/add/dca/hold/watch
- 话术推荐:建议信号自动匹配话术模板
- 基金搜索、实时估值、历史净值

### 国际市场(快捷键 7)
- 5 大国际指数:道琼斯/纳斯达克/标普 500/恒生/国企
- 美股热门 10 只:AAPL/MSFT/GOOGL/AMZN/NVDA/META/TSLA/NFLX/AMD/INTC
- 港股热门 10 只:腾讯/阿里/美团/快手/小米/京东/金山/港交所/中移动/友邦
- 个股查询:支持美股/港股代码查询
- 并行总览:一键获取指数 + 美股 + 港股

### 数据中心(快捷键 8)
- 20+ API 端点,覆盖行情/资金/财务/研报/新闻
- 统一数据质量元数据(_meta:数据源/质量评分/缓存状态/时间戳)
- 数据质量检查端点 `/api/market/data-quality/{code}`

### 监控预警(快捷键 9)
- 四级风控(NORMAL/WARNING/DANGER/EMERGENCY)
- 告警规则引擎、5 分钟去重
- 钉钉/企业微信通知

### 系统配置(快捷键 0)
- YAML 配置 + 环境变量覆盖
- 敏感字段自动脱敏
- 分析引擎状态监控
- 系统健康面板

## API 端点

| 路径 | 说明 |
|------|------|
| `GET /api/health` | 健康检查(含 AI 密钥状态) |
| `GET /api/market/index_realtime` | 指数实时行情 |
| `GET /api/market/stock_realtime` | 个股实时行情 |
| `GET /api/market/stock_kline` | K 线数据 |
| `GET /api/market/data-quality/{code}` | 数据质量检查 |
| `POST /api/agent/analyze` | 多智能体分析 |
| `POST /api/agent/quick_analysis` | 快速分析 |
| `GET /api/dashboard/overview` | 仪表盘概览 |
| `GET /api/config/settings` | 系统配置 |
| `GET /api/strategy/list` | 策略列表 |
| `GET /api/fund/positions` | 基金持仓 |
| `POST /api/fund/advice` | 基金建议 |
| `GET /api/global/indices` | 国际指数 |
| `GET /api/global/us_hot` | 美股热门 |
| `GET /api/global/hk_hot` | 港股热门 |
| `GET /api/global/overview` | 国际市场总览 |
| `GET /api/scripts/list` | 话术列表 |
| `POST /api/scripts/generate` | 生成话术 |
| `POST /api/scripts/match/fund` | 基金话术匹配 |
| `POST /api/scripts/match/stock` | 股票话术匹配 |

完整 API 文档访问 `/docs`(Swagger UI)。

## 项目结构

```
Fund-Assessment/
├── config/              # 配置文件
│   ├── settings.yaml    # 主配置(AI/数据源/风控/通知/缓存 TTL)
│   └── strategies.yaml  # 策略配置
├── docs/                # 文档
│   ├── 2026-07-05-S1-backend-infra-fix.md      # S1 后端基建修复
│   ├── 2026-07-05-S2-fund-module.md            # S2 基金模块
│   ├── 2026-07-05-S3-global-market.md          # S3 国际股市
│   ├── 2026-07-06-S4-script-library.md         # S4 话术库
│   ├── 2026-07-06-S5-frontend-debrand.md       # S5 前端去 AI 化
│   └── 2026-07-06-S6-test-coverage.md          # S6 测试补全
├── scripts/             # 14 个实用脚本(日报/回测/监控等)
├── src/
│   ├── agents/          # 多智能体系统(7 Agent + 辩论 + 决策)
│   ├── analysis/        # 分析模块
│   │   ├── fund_advisor.py     # 基金建议规则引擎
│   │   ├── script_library.py   # 话术库(24 模板)
│   │   ├── capital_flow.py     # 资金流分析
│   │   ├── fundamental.py      # 基本面分析
│   │   ├── news.py             # 新闻分析
│   │   ├── sentiment.py        # 情绪分析
│   │   └── technical.py        # 技术分析
│   ├── core/            # 核心模块
│   │   ├── ai_service.py        # AI 分析服务(LLM 集成 + 并行优化)
│   │   ├── llm_router.py        # 多 LLM Provider 路由器
│   │   ├── data_source_v2.py    # 增强数据源(6 源降级 + 国际股市)
│   │   ├── data_validator.py    # 数据质量校验器
│   │   ├── cache.py             # 磁盘缓存(Windows 兼容)
│   │   ├── risk_manager.py      # 四级风控管理
│   │   ├── executor.py          # 交易执行器
│   │   ├── scheduler.py         # APScheduler 调度
│   │   └── backtest.py          # 回测引擎
│   ├── monitor/         # 监控模块(告警/日内资金流)
│   ├── strategies/      # 6 个交易策略
│   └── utils/           # 工具模块
│       ├── auth.py              # 鉴权(静态 Token)
│       ├── convert.py           # 类型转换(safe_float/safe_str)
│       ├── config.py            # 配置加载
│       ├── logger.py            # 日志
│       └── notify.py            # 通知
├── tests/               # 测试(56 文件, 1394 用例)
│   ├── conftest.py              # 测试配置 + fixtures
│   ├── test_api.py              # API 端点测试
│   ├── test_auth.py             # 鉴权测试
│   ├── test_cache.py            # DataCache 缓存测试
│   ├── test_convert.py          # 类型转换测试
│   ├── test_data_fabrication_fix.py  # 数据造假修复测试
│   ├── test_data_validator.py   # 数据校验测试
│   ├── test_fund_advisor.py     # 基金建议引擎测试
│   ├── test_global_market.py    # 国际股市数据源测试
│   ├── test_global_market_routes.py  # 国际市场路由测试
│   ├── test_llm_router.py       # LLM 路由测试
│   ├── test_script_library.py   # 话术库引擎测试
│   └── test_scripts_routes.py   # 话术库路由测试
├── web/
│   ├── api.py           # FastAPI 应用入口
│   ├── routes/          # 10 个 API 路由模块
│   │   ├── dashboard.py         # 仪表盘
│   │   ├── strategy.py          # 策略
│   │   ├── trade.py             # 交易(含鉴权)
│   │   ├── monitor.py           # 监控
│   │   ├── config.py            # 配置(含鉴权)
│   │   ├── market.py            # 行情
│   │   ├── agent.py             # AI Agent
│   │   ├── fund.py              # 基金
│   │   ├── global_market.py     # 国际市场
│   │   └── scripts.py           # 话术库
│   └── static/
│       └── index.html   # 前端 SPA(10 页面)
├── launch.py            # 启动入口(推荐)
├── .env.example         # 环境变量模板
├── requirements.txt     # Python 依赖
├── render.yaml          # Render 部署配置(主要)
└── fly.toml             # Fly.io 部署配置(备选)
```

## 测试

### 测试覆盖

| 子项目 | 测试文件数 | 用例数 | 覆盖范围 |
|--------|-----------|--------|---------|
| S1 后端基建 | 6 | 81 | 鉴权/转换/造假修复/数据校验/LLM 路由/行情路由/监控路由 |
| S2 基金模块 | 1 | 27 | 基金建议规则引擎 |
| S3 国际股市 | 1 | 15 | 腾讯接口解析/指数/美股/港股/总览 |
| S4 话术库 | 1 | 26 | 模板列表/填充/生成/基金匹配/股票匹配/分类 |
| S5 前端去 AI 化 | 0 | 0 | 纯前端改造(无单测) |
| S6 测试补全 | 3 | 55 | DataCache/话术路由/国际路由 |
| **合计** | **56** | **1394** | 全部通过 |

### 运行测试

```bash
# 全量
python -m pytest tests/ -v

# 带覆盖率
python -m pytest tests/ --cov=src --cov=web --cov-report=html
```

## 部署

### 云平台

主要部署在 Render 上,也支持 Fly.io / Railway / Vercel,详见对应配置文件:
- `fly.toml` — Fly.io
- `railway.json` — Railway
- `render.yaml` — Render
- `vercel.json` — Vercel

## 质量保障

- **1394 个单元测试**:覆盖核心模块、数据源、路由、引擎
- **数据真实性**:移除所有造假 fallback,返回空数据 + 日志说明
- **async 友好**:所有同步网络调用用 `asyncio.to_thread` 包装,不阻塞事件循环
- **鉴权安全**:6 个写/交易端点静态 Token 保护,`secrets.compare_digest` 防时序攻击
- **并行优化**:`_gather_stock_data` 11 次串行 → ThreadPoolExecutor 并行
- **代码去重**:`safe_float`/`safe_str` 4 文件去重到 `src/utils/convert.py`

## License

MIT
