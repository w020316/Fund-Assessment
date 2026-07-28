# P1 实现计划:后端核心模块

> **设计文档**:`docs/superpowers/specs/2026-07-28-quantflow-pro-fund-advisor-design.md`
> **阶段**:P1(后端核心模块)
> **依赖**:P0已完成(LLM路由12 Provider+限流+236测试)

---

## 代码结构现状(基于探索)

| 模块 | 路径 | 现状 |
|------|------|------|
| 数据源 v2(生产用) | `src/core/data_source_v2.py` | 62个函数,函数式+TTL缓存+多源降级 |
| 基金建议引擎 | `src/analysis/fund_advisor.py` | 3信号(大盘/板块/盈亏),规则驱动 |
| 智能体框架 | `src/agents/` | 9角色/7类,4分析师+多空辩论+交易决策 |
| 市场路由 | `web/routes/market.py` | 25端点,`{"data":...,"_meta":...}`包装 |
| 基金路由 | `web/routes/fund.py` | 6端点,无_meta包装(待统一) |
| 话术库 | `src/analysis/script_library.py` | 24模板(12股+12基金),信号→场景映射 |

**关键约定**:延续"不造假"原则(数据不足返回明确提示,不编造信号)。

---

## P1-1:消息面聚合引擎(新增)

**目标**:聚合4类消息源,去重分类后输出结构化摘要。

### 新增文件

| 文件 | 职责 |
|------|------|
| `src/analysis/news_aggregator.py` | 消息面聚合核心引擎 |
| `web/routes/news.py` | 消息面API路由(4端点) |
| `tests/test_news_aggregator.py` | 单元测试 |

### 核心类设计

```python
# src/analysis/news_aggregator.py
class NewsAggregator:
    """消息面聚合引擎 - 4源并行抓取+去重+分类+LLM总结"""

    async def get_news_feed(self, sector=None, fund_code=None) -> dict:
        """消息面流(支持板块/基金过滤)"""

    async def get_hot_events(self, limit=10) -> list[dict]:
        """热点事件 Top N"""

    async def get_sentiment_index(self) -> dict:
        """情绪指数 0-100"""

    async def ai_search(self, query: str) -> dict:
        """AI检索(Tavily+LLM总结)"""

    # 内部方法
    def _fetch_finance_news(self) -> list[dict]:     # akshare stock_news_em
    def _fetch_announcements(self) -> list[dict]:     # akshare stock_notice_report
    def _fetch_research_reports(self) -> list[dict]:  # akshare stock_research_report_em
    def _fetch_sentiment_hot(self) -> list[dict]:     # akshare stock_hot_follow_em
    def _deduplicate(self, news_list) -> list[dict]:  # 标题相似度去重
    def _classify_sentiment(self, news) -> str:        # 利好/利空/中性
    def _llm_summarize(self, news_list) -> str:        # LLM总结
```

### API端点

```
GET  /api/news/feed          消息面流(支持sector/fund_code参数)
GET  /api/news/hot           热点事件(?limit=10)
GET  /api/news/sentiment     情绪指数
POST /api/news/search        AI检索(body: {query})
```

### 测试要点
- 4源抓取mock(akshare接口)
- 去重逻辑(相似标题合并)
- 分类逻辑(关键词匹配)
- LLM总结mock
- 空数据降级(返回"暂无消息")

---

## P1-2:重仓股板块分析(新增)

**目标**:从基金持仓反推板块机会,作为基金建议第4信号。

### 新增文件

| 文件 | 职责 |
|------|------|
| `src/analysis/fund_holdings.py` | 重仓股板块分析核心 |
| `tests/test_fund_holdings.py` | 单元测试 |

### 核心函数设计

```python
# src/analysis/fund_holdings.py
async def get_fund_holdings(fund_code: str) -> dict:
    """基金重仓股详情(前10大)"""

async def get_fund_sector_distribution(fund_code: str) -> dict:
    """板块分布与趋势"""

def _map_stock_to_sector(stock_code: str) -> str:
    """个股→行业板块映射"""

def _calculate_concentration(holdings: list) -> int:
    """集中度指数 0-100"""

def _estimate_nav_impact(holdings: list) -> float:
    """预估当日净值影响 %"""
```

### API端点(融入现有fund路由)

```
GET /api/fund/{fund_code}/holdings    基金重仓股详情
GET /api/fund/{fund_code}/sectors     板块分布与趋势
```

### 数据源
- akshare `fund_portfolio_hold_em(symbol=fund_code)` — 基金持仓
- akshare `stock_individual_info_em` — 个股行业
- data_source_v2 `get_sector_ranking()` — 板块涨跌

---

## P1-3:大盘研判增强(增强现有)

**目标**:新增大盘温度计,增强板块轮动分析。

### 新增文件

| 文件 | 职责 |
|------|------|
| `src/analysis/market_analyzer.py` | 大盘研判核心(温度计+板块轮动) |
| `tests/test_market_analyzer.py` | 单元测试 |

### 核心函数设计

```python
# src/analysis/market_analyzer.py
async def get_market_temperature() -> dict:
    """大盘温度计 0-100"""

async def get_sector_rotation() -> dict:
    """板块轮动排行"""

async def get_capital_flow_analysis() -> dict:
    """资金流向分析"""

def _calc_temperature(index_data, sector_data, capital_data, sentiment_data) -> int:
    """温度计算法(指数30%+板块25%+资金25%+情绪20%)"""
```

### API端点(融入现有market路由)

```
GET /api/market/temperature    大盘温度计
GET /api/market/sectors        板块轮动排行(增强现有)
GET /api/market/capital        资金流向
```

---

## P1-4:基金建议五信号(扩展现有)

**目标**:将fund_advisor从3信号扩展为5信号。

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/analysis/fund_advisor.py` | 扩展为五信号融合 |

### 改动要点

```python
# 现有 _build_market_signal / _build_sector_signal / _build_pnl_signal 保留
# 新增:
def _build_news_signal(fund_code, news_data) -> dict:
    """消息面信号(25%权重)"""

def _build_holdings_signal(fund_code, holdings_data) -> dict:
    """重仓股板块信号(20%权重)"""

def _build_market_temp_signal(temperature) -> dict:
    """大盘环境信号(15%权重)"""

# 修改 _merge_signals 支持五信号加权融合
def _merge_signals_weighted(signals: list[dict], weights: dict) -> dict:
    """加权融合(非简单优先级)"""
```

### API端点(扩展现有fund路由)

```
GET  /api/fund/{fund_code}/advice       基金买卖建议(五信号)
GET  /api/fund/advice/list              建议列表(多基金)
POST /api/fund/{fund_code}/deep-analysis 深度分析(触发智能体辩论)
```

---

## P1-5:智能体角色调整(调整现有)

**目标**:7智能体角色聚焦基金/板块/消息面。

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/agents/base.py` | AgentRole枚举新增3角色 |
| `src/agents/news_agent.py` | 改造为消息面分析师(聚焦基金影响) |
| `src/agents/fundamental_agent.py` | 调整为基金基本面+重仓股 |
| 新增 `src/agents/sector_agent.py` | 板块分析师 |
| 新增 `src/agents/fund_agent.py` | 基金分析师 |
| `src/agents/research_team.py` | 辩论流程调整(多空分派) |
| `src/agents/trading_manager.py` | 编排调整(去掉交易,输出建议) |

### 角色调整

| 智能体 | 立场 | 实现 |
|--------|------|------|
| 消息面分析师 | 多/空 | 改造news_agent,输入来自news_aggregator |
| 基金分析师 | 多/空 | 新增fund_agent,基金质地判断 |
| 板块分析师 | 多/空 | 新增sector_agent,重仓股板块趋势 |
| 技术分析师 | 中立 | 保留,聚焦净值技术指标 |
| 基本面分析师 | 中立 | 调整,聚焦重仓股基本面 |
| 风险分析师 | 空头 | 保留 |
| 宏观分析师 | 中立 | 保留,大盘+国际环境 |

### 输出调整
- 去掉 `TradingDecision`(交易决策),改为 `InvestmentAdvice`(投资建议)
- 输出:建议动作(加仓/减仓/持有/观望)+ 置信度 + 理由 + 风险

---

## 实施顺序与验收

| 序号 | 模块 | 依赖 | 验收标准 |
|------|------|------|---------|
| P1-1 | 消息面聚合引擎 | 数据源v2 | 4端点可用+4源聚合+去重+测试 |
| P1-2 | 重仓股板块分析 | akshare | 2端点可用+持仓关联+集中度+测试 |
| P1-3 | 大盘研判增强 | 数据源v2 | 3端点可用+温度计+测试 |
| P1-4 | 基金建议五信号 | P1-1/P1-2/P1-3 | 五信号融合+3端点+测试 |
| P1-5 | 智能体角色调整 | P1-1~P1-4 | 7角色+辩论+建议输出+测试 |
| P1-6 | 集成测试+Git提交 | 全部 | 全量测试通过+提交 |

---

## 约定

1. **响应格式统一**:所有新端点用 `{"data":..., "_meta":...}` 包装
2. **不造假原则**:数据不足返回明确提示,不编造信号
3. **缓存**:复用 `DataCache` 模块级单例,消息面60s/持仓300s/温度60s
4. **异步**:同步akshare调用用 `asyncio.to_thread` 包装
5. **品牌**:QuantFlow Pro,文案中性化(AI→智能)
6. **测试**:每个模块独立测试文件,mock外部API
