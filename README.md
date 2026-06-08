# OpenClaw 量化AI炒股机器人

基于多智能体辩论机制的A股AI量化分析系统，融合7位专业分析师角色、多空辩论、风险辩论与A股特有约束建模。

## 核心亮点

- **7角色多智能体辩论**：基本面/技术面/情绪面/新闻面/政策面/游资追踪/解禁监控，多空辩论+风险辩论+组合经理决策
- **多LLM Provider路由**：支持OpenAI/DeepSeek/Gemini/Anthropic/Ollama，自动故障切换+熔断器保护
- **多数据源降级架构**：东方财富/腾讯/新浪/mootdx/akshare/tushare，6源3级自动降级
- **A股特有约束建模**：T+1交易制度、涨跌停限制(10%/20%/5%)、最小交易单位100股
- **数据质量校验**：多维度数据质量检查(完整性/时效性/合理性/一致性)，质量评分0-100
- **完整风控体系**：四级风险等级+系统回撤限制+单日亏损限制+紧急停止机制
- **全栈可部署**：Docker/Fly.io/Railway/Render/Vercel，一键部署

## 技术架构

```
┌──────────────────────────────────────────────┐
│           Web 前端 (index.html SPA)           │
│  8页面: 行情/AI分析/策略/交易/持仓/数据/监控/配置 │
├──────────────────────────────────────────────┤
│          FastAPI REST API (7大路由模块)        │
│  dashboard / strategy / trade / monitor       │
│  config / market / agent                      │
├──────────────────────────────────────────────┤
│              核心业务层                        │
│  ┌──────────┐ ┌──────────┐ ┌───────────────┐ │
│  │LLM Router│ │  Agents  │ │  Strategies   │ │
│  │(多模型路由)│ │(7智能体) │ │  (6交易策略)  │ │
│  └──────────┘ └──────────┘ └───────────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌───────────────┐ │
│  │  Risk    │ │Scheduler │ │ DataValidator │ │
│  │ Manager  │ │ (调度器)  │ │ (数据校验)    │ │
│  └──────────┘ └──────────┘ └───────────────┘ │
├──────────────────────────────────────────────┤
│         数据源层 (多源降级架构)                 │
│  东方财富 / 腾讯 / 新浪 / mootdx / akshare   │
├──────────────────────────────────────────────┤
│           基础设施层                           │
│  缓存(TTL+磁盘) / 日志(loguru) / 配置(YAML)  │
│  部署(Docker/Fly.io/Render/Railway/Vercel)   │
└──────────────────────────────────────────────┘
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Python 3.12 + FastAPI + Uvicorn |
| 数据处理 | Pandas + NumPy + pandas-ta + stockstats |
| 数据源 | akshare, tushare, mootdx(通达信), 东方财富API, 腾讯行情API, 新浪财经API |
| AI/LLM | 多Provider路由(OpenAI/DeepSeek/Gemini/Anthropic/Ollama), Tavily搜索 |
| 任务调度 | APScheduler |
| 数据验证 | Pydantic + 自研DataValidator |
| 日志 | Loguru |
| 配置 | PyYAML + python-dotenv |
| 数据库 | SQLite (风控状态持久化) |
| 部署 | Docker, Fly.io, Render, Railway, Vercel |

## 快速开始

### 1. 安装依赖

```bash
# 方式一：使用安装脚本（推荐）
install.bat

# 方式二：手动安装
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入API密钥
```

必填项：
- `TTAPI_API_KEY` 或 `DEEPSEEK_API_KEY` — AI分析所需（至少配置一个LLM Provider）

可选项：
- `TAVILY_API_KEY` — 新闻搜索增强
- `TUSHARE_TOKEN` — Tushare数据源

### 3. 启动服务

```bash
# 方式一：使用启动脚本
start.bat

# 方式二：直接启动
python -m uvicorn web.api:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000 即可使用。

### 4. 运行测试

```bash
python -m pytest tests/ -v
```

## 功能模块

### 行情总览
- 三大指数实时行情（上证/深证/创业板）
- 个股实时行情、K线图、资金流向
- 板块排名、龙虎榜、北向资金
- 数据质量评分与来源标识

### AI分析
- 7位专业分析师多维度分析
- 多空辩论（Bull/Bear）+ 风险辩论（激进/保守/中性）
- 组合经理最终决策（BUY/HOLD/SELL）
- A股特有约束（T+1/涨跌停/最小交易单位）
- 支持深度分析和快速分析两种模式

### 策略中心
- TradingQuant：五维评分模型（技术25%/资金30%/基本面10%/新闻20%/情绪15%）
- 涨停板分析：首板/二板/三板+分级、封板质量评分
- 可转债T+0狙击：正股涨停联动、转股溢价率计算
- 多因子量化BSProQuant：22个因子评分

### 交易执行
- 模拟券商（SimulatedBroker）：佣金+印花税计算
- 实盘券商接口预留（LiveBroker）
- 交易执行器（TradeExecutor）：信号→风控→执行

### 持仓管理
- 持仓组合分析、集中度风险评估
- 板块暴露分析、再平衡建议
- 止损/止盈建议

### 数据中心
- 20+ API端点，覆盖行情/资金/财务/研报/新闻
- 统一数据质量元数据（_meta: 数据源/质量评分/缓存状态/时间戳）
- 数据质量检查端点 `/api/market/data-quality/{code}`

### 监控预警
- 四级风控（NORMAL/WARNING/DANGER/EMERGENCY）
- 告警规则引擎、5分钟去重
- 钉钉/企业微信通知

### 系统配置
- YAML配置 + 环境变量覆盖
- 敏感字段自动脱敏
- LLM Provider状态监控

## API端点

| 路径 | 说明 |
|------|------|
| `GET /api/health` | 健康检查（含AI密钥状态） |
| `GET /api/market/index_realtime` | 指数实时行情 |
| `GET /api/market/stock_realtime` | 个股实时行情 |
| `GET /api/market/stock_kline` | K线数据 |
| `GET /api/market/data-quality/{code}` | 数据质量检查 |
| `POST /api/agent/analyze` | AI多智能体分析 |
| `POST /api/agent/quick_analysis` | AI快速分析 |
| `GET /api/dashboard/overview` | 仪表盘概览 |
| `GET /api/config/settings` | 系统配置 |
| `GET /api/strategy/list` | 策略列表 |

## 项目结构

```
Fund-Assessment/
├── config/              # 配置文件
│   ├── settings.yaml    # 主配置（AI/数据源/风控/通知/缓存TTL）
│   └── strategies.yaml  # 策略配置
├── scripts/             # 14个实用脚本（日报/回测/监控等）
├── src/
│   ├── agents/          # 多智能体系统（7 Agent + 辩论 + 决策）
│   ├── analysis/        # 分析模块（资金流/基本面/新闻/情绪/技术）
│   ├── core/            # 核心模块
│   │   ├── ai_service.py        # AI分析服务（LLM集成）
│   │   ├── llm_router.py        # 多LLM Provider路由器
│   │   ├── data_source_v2.py    # 增强数据源（6源降级）
│   │   ├── data_validator.py    # 数据质量校验器
│   │   ├── cache.py             # 磁盘缓存（Windows兼容）
│   │   ├── risk_manager.py      # 四级风控管理
│   │   ├── executor.py          # 交易执行器
│   │   ├── scheduler.py         # APScheduler调度
│   │   └── backtest.py          # 回测引擎
│   ├── monitor/         # 监控模块（告警/日内资金流）
│   ├── strategies/      # 6个交易策略
│   └── utils/           # 工具模块（配置/日志/通知）
├── tests/               # 39个测试用例
│   ├── test_data_validator.py
│   ├── test_llm_router.py
│   └── test_api.py
├── web/
│   ├── api.py           # FastAPI应用入口
│   ├── routes/          # 7个API路由模块
│   └── static/
│       └── index.html   # 前端SPA（8页面）
├── .env.example         # 环境变量模板
├── requirements.txt     # Python依赖
├── Dockerfile           # Docker部署
└── docker-compose.yml   # Docker Compose
```

## 部署

### Docker

```bash
docker-compose up -d
```

### 云平台

支持一键部署到 Fly.io / Railway / Render / Vercel，详见对应配置文件。

## License

MIT
