# QuantFlow Pro API 文档

> 版本: 1.0
> 更新日期: 2026-07-29
> 基础 URL: `https://fund-assessment.onrender.com`
> 本地开发: `http://localhost:8000`

---

## 1. 概览

### 1.1 统计

| 项 | 数量 |
|---|---|
| 路由文件 | 13 个(`web/api.py` + `web/routes/` 12 个) |
| API 端点 | 94 个 |
| GET 端点 | 71 |
| POST 端点 | 19 |
| PUT 端点 | 2 |
| DELETE 端点 | 2 |

### 1.2 鉴权

| 类型 | 端点 | 鉴权方式 |
|------|------|---------|
| 公开 | 大部分 GET 端点 | 无 |
| 管理员 | 8 个写端点(交易/持仓/配置) | `Authorization: Bearer <ADMIN_TOKEN>` |

**鉴权 Header**:
```
Authorization: Bearer <ADMIN_TOKEN>
```

### 1.3 统一响应格式

```json
{
  "data": { ... },        // 数据载荷
  "_meta": {              // 元信息
    "timestamp": "2026-07-29T10:00:00",
    "data_source": "tencent",
    "cached": true
  }
}
```

错误响应:
```json
{
  "detail": "错误描述"
}
```

### 1.4 端点模块分布

| 模块 | 路由前缀 | 端点数 |
|------|---------|--------|
| health | - | 3 |
| market | `/api/market` | 27 |
| fund | `/api/fund` | 9 |
| agent | `/api/agent` | 9 |
| config | `/api/config` | 7 |
| monitor | `/api/monitor` | 6 |
| global | `/api/global` | 6 |
| scripts | `/api/scripts` | 6 |
| strategy | `/api/strategy` | 6 |
| trade | `/api/trade` | 5 |
| news | `/api/news` | 4 |
| dashboard | `/api/dashboard` | 4 |
| holdings | `/api/holdings` | 2 |

---

## 2. health 模块(健康检查)

### 2.1 GET / 系统首页

重定向到静态首页。

**响应**: 302 Redirect → `/static/index.html`

---

### 2.2 GET /api/health 系统健康检查

返回系统依赖与 AI Key 状态。

**响应**:
```json
{
  "status": "ok",
  "akshare": true,
  "core_modules": true,
  "ai_ready": true,
  "ai_keys": {
    "zhipu_glm": true,
    "agnes": true,
    "ttapi": false
  }
}
```

---

### 2.3 GET /api/health/llm LLM Provider 健康

返回所有 LLM Provider 的状态、优先级、限流信息。

**响应**:
```json
{
  "status": "ok",
  "total_providers": 2,
  "available_providers": ["zhipu_glm", "agnes"],
  "providers": {
    "zhipu_glm": {
      "priority": 100,
      "available": true,
      "circuit_breaker": "closed"
    },
    "agnes": {
      "priority": 90,
      "available": true,
      "circuit_breaker": "closed"
    }
  }
}
```

---

## 3. market 模块(行情总览,27 端点)

### 3.1 GET /api/market/index_realtime 指数实时行情

**响应**: `data: list[IndexRealtimeItem]`

```json
{
  "data": [
    {
      "code": "000001.SH",
      "name": "上证指数",
      "price": 3245.68,
      "change": 12.35,
      "change_pct": 0.38,
      "volume": 12345678,
      "amount": 987654321
    }
  ],
  "_meta": { "timestamp": "...", "data_source": "tencent" }
}
```

---

### 3.2 GET /api/market/stock_realtime 股票实时行情

**查询参数**:
- `codes` (str, 必填): 股票代码,逗号分隔,如 `600519,000001`

**响应**: `data: list[StockRealtimeItem]`

---

### 3.3 GET /api/market/stock_kline 股票 K 线

**查询参数**:
- `code` (str, 必填): 股票代码
- `period` (str, 默认 `daily`): K 线周期(daily/weekly/monthly)
- `count` (int, 默认 120): 返回数量

**响应**: `data: list[KlineItem]`

---

### 3.4 GET /api/market/stock_detail 股票详情

**查询参数**:
- `code` (str, 必填): 股票代码

**响应**: `data: StockDetailResponse`

包含 `quote` / `financial` / `capital_flow` / `kline_summary` 四个部分。

---

### 3.5 GET /api/market/hot_stocks 热门股票

返回涨幅/跌幅/成交额三榜。

**响应**: `data: HotStocksResponse`

```json
{
  "data": {
    "top_gainers": [...],
    "top_losers": [...],
    "top_volume": [...]
  }
}
```

---

### 3.6 GET /api/market/sector_ranking 板块排行

含主力/超大/大/中/小单净流入。

**响应**: `data: list[SectorRankingItem]`

---

### 3.7 GET /api/market/sector_flow 板块资金流向

**响应**: `data: list[SectorFlowItem]`

---

### 3.8 GET /api/market/northbound 北向资金实时

**响应**: `data: NorthboundFlowItem`

---

### 3.9 GET /api/market/capital-flow 资金流向总览

含北向+板块主力 Top5。

**响应**: `data: dict`

---

### 3.10 GET /api/market/dragon_tiger 龙虎榜

**响应**: `data: list[DragonTigerItem]`

---

### 3.11 GET /api/market/margin 融资融券

**查询参数**:
- `code` (str, 可选): 股票代码

**响应**: `data: MarginTradingItem`

---

### 3.12 GET /api/market/block_trades 大宗交易

**查询参数**:
- `code` (str, 可选): 股票代码

**响应**: `data: list[BlockTradeItem]`

---

### 3.13 GET /api/market/shareholder 股东户数变化

**查询参数**:
- `code` (str, 可选): 股票代码

**响应**: `data: ShareholderItem`

---

### 3.14 GET /api/market/news 股票/全局新闻

**查询参数**:
- `code` (str, 可选): 股票代码
- `page` (int, 默认 1): 页码
- `page_size` (int, 默认 20): 每页数量

**响应**: `data: list[NewsItem]`

---

### 3.15 GET /api/market/global_news 全局新闻

**响应**: `data: list[NewsItem]`

---

### 3.16 GET /api/market/research_reports 研究报告

**查询参数**:
- `code` (str, 可选): 股票代码
- `page` (int, 默认 1)
- `page_size` (int, 默认 20)

**响应**: `data: list[ResearchReportItem]`

---

### 3.17 GET /api/market/hot_stocks_signal 涨停板信号

同花顺优先,东财兜底。

**响应**: `data: list[HotStockSignalItem]`

---

### 3.18 GET /api/market/heatmap 市场热力图

板块涨跌着色。

**响应**: `data: list[HeatmapItem]`

---

### 3.19 GET /api/market/search 股票搜索

**查询参数**:
- `q` (str, 必填): 搜索关键词

**响应**: `data: list[SearchResultItem]`

---

### 3.20 GET /api/market/status 市场开闭盘状态

**响应**: `data: MarketStatusResponse`

```json
{
  "data": {
    "is_open": true,
    "session": "morning",
    "next_open_time": "2026-07-30 09:30:00",
    "current_time": "2026-07-29 10:00:00"
  }
}
```

---

### 3.21 GET /api/market/market_wide_stats 全市场统计

融资余额/大宗/股东变化。

**响应**: `data: MarketWideStatsResponse`

---

### 3.22 GET /api/market/market_sentiment 市场情绪指标

**响应**: `data: dict`

---

### 3.23 GET /api/market/thermometer 大盘温度计

0-100 量化指标。

**响应**: `data: dict`

```json
{
  "data": {
    "score": 65.5,
    "label": "震荡偏多",
    "signals": {
      "index_trend": 70,
      "sector_strength": 65,
      "capital_flow": 60,
      "sentiment": 68
    }
  }
}
```

---

### 3.24 GET /api/market/fund_realtime 基金实时行情

akshare 优先,腾讯兜底。

**查询参数**:
- `codes` (str, 必填): 基金代码,逗号分隔

**响应**: `data: list[FundRealtimeItem]`

---

### 3.25 GET /api/market/fund_history 基金历史净值

**查询参数**:
- `code` (str, 必填): 基金代码
- `period` (str, 默认 `1y`): 周期(1m/3m/6m/1y/3y/all)

**响应**: `data: list[FundHistoryItem]`

---

### 3.26 GET /api/market/assessment 大盘综合研判

温度计+资金+板块轮动。

**响应**: `data: dict`

---

### 3.27 GET /api/market/data-quality/{stock_code} 数据质量检查

**路径参数**:
- `stock_code` (str, 必填): 股票代码

**响应**:
```json
{
  "stock_code": "600519",
  "quality_score": 0.85,
  "is_valid": true,
  "warnings": [],
  "criticals": [],
  "issues_count": 0,
  "data_dimensions": {
    "completeness": 1.0,
    "accuracy": 0.9,
    "timeliness": 0.95,
    "consistency": 0.85
  }
}
```

---

## 4. fund 模块(基金建议与持仓,9 端点)

### 4.1 GET /api/fund/positions 获取基金持仓

含实时净值与盈亏。

**响应**:
```json
{
  "positions": [
    {
      "fund_code": "110022",
      "fund_name": "易方达消费",
      "shares": 1000,
      "cost_nav": 2.5,
      "current_nav": 2.8,
      "market_value": 2800,
      "pnl": 300,
      "pnl_pct": 12.0,
      "today_pnl": 50,
      "today_pnl_pct": 1.8
    }
  ],
  "summary": {
    "total_market_value": 2800,
    "total_cost": 2500,
    "total_pnl": 300,
    "total_pnl_pct": 12.0,
    "today_pnl": 50,
    "today_pnl_pct": 1.8,
    "count": 1
  }
}
```

---

### 4.2 POST /api/fund/positions 保存基金持仓(全量覆盖,管理员)

**请求体**:
```json
{
  "positions": [
    {
      "fund_code": "110022",
      "fund_name": "易方达消费",
      "shares": 1000,
      "cost_nav": 2.5,
      "buy_date": "2026-01-15",
      "note": "定投"
    }
  ]
}
```

**响应**: `{ "success": true, "message": "已保存 1 个持仓" }`

---

### 4.3 POST /api/fund/positions/add 添加单只基金持仓(管理员)

**请求体**:
```json
{
  "fund_code": "110022",
  "fund_name": "易方达消费",
  "shares": 1000,
  "cost_nav": 2.5,
  "buy_date": "2026-01-15",
  "note": "定投"
}
```

**响应**: `{ "success": true, "message": "添加成功" }`

---

### 4.4 DELETE /api/fund/positions/{fund_code} 删除基金持仓(管理员)

**路径参数**:
- `fund_code` (str, 必填): 基金代码

**响应**: `{ "success": true, "message": "已删除" }`

---

### 4.5 GET /api/fund/advice 基金建议 v1(规则引擎)

**响应**: 规则引擎结果(三信号: 市场/板块/盈亏)

---

### 4.6 GET /api/fund/advice-v2 基金建议 v2(五信号融合,P1-4)

**响应**:
```json
{
  "positions_advice": [
    {
      "fund_code": "110022",
      "fund_name": "易方达消费",
      "action": "加仓",
      "confidence": 72,
      "timing": "短期(1-2周)",
      "signals": {
        "technical": { "score": 65, "reason": "..." },
        "fundamental": { "score": 80, "reason": "..." },
        "news": { "score": 75, "reason": "..." },
        "holdings": { "score": 70, "reason": "..." },
        "market": { "score": 55, "reason": "..." }
      },
      "key_reasons": ["..."],
      "risks": ["..."]
    }
  ]
}
```

---

### 4.7 GET /api/fund/search 基金搜索

**查询参数**:
- `q` (str, 必填, min_length=1): 关键词

**响应**:
```json
{
  "data": [
    { "code": "110022", "name": "易方达消费", "type": "股票型" }
  ],
  "query": "消费",
  "count": 1
}
```

---

### 4.8 GET /api/fund/realtime 基金实时行情聚合

**查询参数**:
- `codes` (str, 必填): 基金代码,逗号分隔

**响应**: `data: list[dict]`

---

### 4.9 GET /api/fund/history 基金历史净值(腾讯)

**查询参数**:
- `code` (str, 必填): 基金代码
- `period` (str, 默认 `1y`)

**响应**: `data: list`, `code: str`, `period: str`

---

## 5. news 模块(消息面,4 端点,P1-1)

### 5.1 GET /api/news/feed 消息面流

支持板块/基金过滤。

**查询参数**:
- `sector` (str, 可选): 板块名
- `fund_code` (str, 可选): 基金代码

**响应**:
```json
{
  "data": {
    "hot_events": [...],
    "sentiment_index": 65,
    "key_news": [...]
  }
}
```

---

### 5.2 GET /api/news/hot 热点事件 Top N

**查询参数**:
- `limit` (int, 1-50, 默认 10)

**响应**: `data: dict`

---

### 5.3 GET /api/news/sentiment 情绪指数

0-100 量化。

**响应**: `data: dict`

---

### 5.4 POST /api/news/search 智能检索(Tavily+LLM,不缓存)

**请求体**:
```json
{
  "query": "白酒板块"
}
```

**响应**:
```json
{
  "data": {
    "query": "白酒板块",
    "summary": "白酒板块近期受...",
    "sentiment": "利好",
    "results": [
      {
        "title": "白酒板块大涨",
        "source": "东方财富",
        "publish_time": "2026-07-29 10:00:00",
        "sentiment": "利好",
        "url": "..."
      }
    ]
  }
}
```

---

## 6. agent 模块(智能体,9 端点,P1-5)

### 6.1 POST /api/agent/analyze 股票深度分析

结果记入历史。

**请求体**:
```json
{
  "stock_code": "600519"
}
```

**响应**: 深度分析结果(含 agent_opinions、bull_bear_debate、final_decision)

---

### 6.2 POST /api/agent/fund_analyze 基金多智能体分析(P1-5 核心)

**请求体**:
```json
{
  "fund_code": "110022",
  "fund_name": "易方达消费",
  "cost_nav": 2.5,
  "shares": 1000,
  "mode": "deep"
}
```

**响应**: 7 角色分析结果
```json
{
  "agent_opinions": [...],
  "bull_bear_debate": {
    "bull_arguments": [...],
    "bear_arguments": [...]
  },
  "risk_debate": { "risks": [...] },
  "portfolio_manager_decision": { "action": "BUY", "confidence": 0.72, ... },
  "final_recommendation": {
    "action": "BUY",
    "target_price": 3.0,
    "stop_loss": 2.3,
    "position_advice": "加仓 30%",
    "reasoning": "..."
  },
  "analysis_mode": "deep",
  "analyst_count": 7,
  "timestamp": "..."
}
```

**超时**: 90s(前端),120s(后端 LLM)

---

### 6.3 POST /api/agent/multi_analyze 多智能体分析

**请求体**:
```json
{
  "stock_code": "600519",
  "mode": "deep",
  "agents": ["fundamental", "technical"]
}
```

**响应**: 含 `selected_agents: list`

---

### 6.4 POST /api/agent/quick_analysis 快速分析

**请求体**: `{ "stock_code": "600519" }`

---

### 6.5 POST /api/agent/portfolio_advice 投资组合建议

**请求体**:
```json
{
  "positions": [{"code": "600519", "shares": 100, ...}]
}
```

---

### 6.6 GET /api/agent/opinions 个股智能体观点

**查询参数**:
- `code` (str, 必填): 股票代码

**响应**: `{ "stock_code": "600519", "opinions": [...] }`

---

### 6.7 GET /api/agent/debate 多空辩论结果

**查询参数**:
- `code` (str, 必填): 股票代码

**响应**:
```json
{
  "topic": "600519",
  "bull_arguments": [...],
  "bear_arguments": [...],
  "bull_score": 75,
  "bear_score": 60,
  "consensus": "偏多",
  "confidence": 0.65
}
```

---

### 6.8 GET /api/agent/history 决策历史(最近 20 条)

**响应**: `{ "count": 20, "history": [...] }`

---

### 6.9 GET /api/agent/market_outlook 市场展望

**响应**: LLM 生成的市场展望文本

---

## 7. global 模块(国际市场,6 端点)

### 7.1 GET /api/global/overview 国际市场总览

指数+美股热门+港股热门并行获取(P1 新增)。

**响应**:
```json
{
  "data": {
    "indices": [...],
    "us_hot": [...],
    "hk_hot": [...]
  }
}
```

---

### 7.2 GET /api/global/indices 国际指数实时

道指/纳指/标普/恒生/国企。

**响应**: `data: list[GlobalIndexItem]`

---

### 7.3 GET /api/global/us_hot 美股热门(10 只科技龙头)

**响应**: `data: list[UsStockItem]`

---

### 7.4 GET /api/global/hk_hot 港股热门(10 只蓝筹)

**响应**: `data: list[HkStockItem]`

---

### 7.5 GET /api/global/us_realtime 美股实时行情(按代码)

**查询参数**:
- `codes` (str, 必填): 如 `AAPL,TSLA`

**响应**: `data: list[UsStockItem]`

---

### 7.6 GET /api/global/hk_realtime 港股实时行情(按代码)

**查询参数**:
- `codes` (str, 必填): 如 `00700,09988`

**响应**: `data: list[HkStockItem]`

---

## 8. holdings 模块(重仓股,2 端点,P1-2)

### 8.1 GET /api/holdings/{fund_code} 基金重仓股板块分析

**路径参数**:
- `fund_code` (str, 必填): 基金代码

**查询参数**:
- `refresh` (bool, 默认 false): 强制刷新缓存

**响应**:
```json
{
  "data": {
    "holdings": [
      {"code": "600519", "name": "贵州茅台", "weight": 9.8, "sector": "白酒", "change_pct": 1.2}
    ],
    "sector_exposure": [...],
    "concentration": 65,
    "nav_impact": 0.35,
    "sector_rotation": [...]
  }
}
```

---

### 8.2 GET /api/holdings/sector-rotation/overview 板块轮动总览(全局)

**响应**: `data: dict`

---

## 9. strategy 模块(策略,6 端点)

### 9.1 GET /api/strategy/list 策略列表

**响应**: `list[StrategyInfo]`

```json
[
  {
    "name": "trading_quant",
    "display_name": "TradingQuant 五维评分",
    "description": "技术/基本面/资金/消息/情绪",
    "enabled": true
  }
]
```

---

### 9.2 POST /api/strategy/analyze 策略分析

**请求体**:
```json
{
  "stock_code": "600519",
  "strategy_type": "comprehensive"
}
```

`strategy_type` 可选: `comprehensive` / `trading_quant` / `bspro_quant`

**响应**: `AnalyzeResponse`

---

### 9.3 POST /api/strategy/backtest 策略回测

**请求体**:
```json
{
  "strategy": "bspro_quant",
  "stock_code": "600519",
  "start_date": "2025-01-01",
  "end_date": "2026-01-01"
}
```

**响应**:
```json
{
  "strategy": "bspro_quant",
  "stock_code": "600519",
  "total_return": 0.15,
  "annualized_return": 0.15,
  "max_drawdown": 0.08,
  "sharpe_ratio": 1.2,
  "win_rate": 0.6,
  "trades": [...]
}
```

---

### 9.4 GET /api/strategy/scan/new_high 创业板新高突破扫描

**响应**: `list[NewHighItem]`

---

### 9.5 GET /api/strategy/scan/limit_up 涨停板扫描

**响应**: `list[LimitUpItem]`

---

### 9.6 GET /api/strategy/scan/cb 可转债 T+0 机会扫描

**响应**: `list[CBItem]`

---

## 10. trade 模块(交易执行,5 端点,管理员鉴权)

### 10.1 POST /api/trade/buy 买入下单(管理员)

**请求体**:
```json
{
  "stock_code": "600519",
  "amount": 100,
  "price": 1800.0,
  "strategy": "manual"
}
```

**响应**: `OrderResponse`

---

### 10.2 POST /api/trade/sell 卖出下单(管理员)

**请求体**: 同 buy

**响应**: `OrderResponse`

---

### 10.3 POST /api/trade/cancel 撤单(管理员)

**请求体**: `{ "order_id": "..." }`

**响应**: `{ "success": true, "message": "撤单成功" }`

---

### 10.4 GET /api/trade/orders 订单列表

**响应**: `list[OrderResponse]`

---

### 10.5 GET /api/trade/history 交易历史

**查询参数**:
- `symbol` (str, 可选)
- `limit` (int, 默认 50)

**响应**: `list[TradeHistoryItem]`

---

## 11. dashboard 模块(账户总览,4 端点)

### 11.1 GET /api/dashboard/overview 账户总览

**响应**:
```json
{
  "available_cash": 800000,
  "total_assets": 850000,
  "market_value": 50000,
  "daily_pnl": 1500,
  "daily_pnl_pct": 0.3,
  "position_count": 3,
  "risk_level": "normal",
  "risk_message": ""
}
```

---

### 11.2 GET /api/dashboard/positions 持仓列表

**响应**: `list[PositionItem]`

---

### 11.3 GET /api/dashboard/risk 风控状态

**响应**: `RiskResponse`

```json
{
  "level": "normal",
  "total_assets": 850000,
  "peak_assets": 855000,
  "drawdown_pct": 0.5,
  "daily_pnl": 1500,
  "daily_pnl_pct": 0.3,
  "consecutive_stop_losses": 0,
  "is_paused": false,
  "pause_until": null,
  "is_emergency_stopped": false,
  "no_new_positions": false,
  "position_reduction": 0.5,
  "message": ""
}
```

---

### 11.4 GET /api/dashboard/trades 交易历史

**查询参数**:
- `limit` (int, 默认 20)

**响应**: `list[TradeItem]`

---

## 12. monitor 模块(数据监控,6 端点)

### 12.1 GET /api/monitor/alerts 告警列表

无 code 时遍历自选股生成默认告警。

**查询参数**:
- `stock_code` (str, 可选)

**响应**: `list[AlertItem]`

---

### 12.2 GET /api/monitor/capital_flow 资金流向

含北向变化。

**查询参数**:
- `stock_code` (str, 可选)

**响应**: `CapitalFlowResponse`

---

### 12.3 GET /api/monitor/northbound 北向资金实时

含 Top 个股。

**响应**: `NorthboundResponse`

---

### 12.4 GET /api/monitor/watchlist 自选股列表

**响应**: `list[WatchlistItem]`

---

### 12.5 POST /api/monitor/watchlist 添加自选股

**请求体**:
```json
{
  "stock_code": "600519",
  "rules": ["price_surge", "volume_spike"]
}
```

**响应**: `{ "success": true, "message": "已添加" }`

---

### 12.6 DELETE /api/monitor/watchlist/{stock_code} 移除自选股

**路径参数**:
- `stock_code` (str, 必填)

**响应**: `{ "success": true, "message": "已移除" }`

---

## 13. config 模块(系统配置,7 端点)

### 13.1 GET /api/config/settings 获取配置

敏感字段脱敏。

**响应**: `{ "settings": { ... } }`

---

### 13.2 PUT /api/config/settings 更新配置(管理员)

脱敏值会被剥离。

**请求体**: `{ "settings": { ... } }`

**响应**: `{ "settings": { ... } }`

---

### 13.3 GET /api/config/strategies 获取策略配置

**响应**: `{ "strategies": { ... } }`

---

### 13.4 PUT /api/config/strategies 更新策略配置(管理员)

**请求体**: `{ "strategies": { ... } }`

---

### 13.5 POST /api/config/test_notify 测试通知

钉钉/企业微信。

**响应**: `{ "success": true, "message": "通知发送成功" }`

---

### 13.6 GET /api/config/user_positions 获取用户股票持仓

**响应**: `{ "positions": [...], "available_cash": 800000 }`

---

### 13.7 POST /api/config/user_positions 保存用户股票持仓(管理员)

**请求体**:
```json
{
  "positions": [...],
  "available_cash": 800000.0
}
```

**响应**: `{ "success": true, "message": "已保存" }`

---

## 14. scripts 模块(话术库,6 端点)

### 14.1 GET /api/scripts/categories 获取话术库分类结构

**响应**: `data: dict`

---

### 14.2 GET /api/scripts/list 列出话术模板

**查询参数**:
- `category` (str, 可选)
- `scene` (str, 可选)

**响应**: `data: list`

---

### 14.3 GET /api/scripts/{script_id} 按 ID 获取单个话术

**路径参数**:
- `script_id` (str, 必填)

**响应**: `data: dict | None`

---

### 14.4 POST /api/scripts/generate 根据模板生成话术

**请求体**:
```json
{
  "script_id": "fund_buy_strong",
  "variables": {
    "fund_name": "易方达消费",
    "action": "加仓",
    "confidence": 72
  }
}
```

**响应**: `data: dict`

---

### 14.5 POST /api/scripts/match/fund 根据基金建议自动匹配话术

**请求体**: `{ "fund_advice": { ... } }`

**响应**: `data: list`, `count: int`

---

### 14.6 POST /api/scripts/match/stock 根据个股数据自动匹配话术

**请求体**: `{ "stock_data": { ... } }`

**响应**: `data: list`, `count: int`

---

## 15. 错误码与状态码

| 状态码 | 含义 | 处理建议 |
|--------|------|---------|
| 200 | 成功 | - |
| 400 | 请求参数错误 | 检查请求体格式 |
| 401 | 未授权 | 检查 ADMIN_TOKEN |
| 404 | 资源不存在 | 检查路径参数 |
| 422 | Pydantic 校验失败 | 检查字段类型 |
| 500 | 服务器内部错误 | 查看服务日志 |
| 503 | 服务不可用 | LLM/数据源全部降级 |

---

## 16. 速率限制

- **LLM Provider**: 30 RPM(zhipu_glm/agnes)
- **数据源**: 无显式限制,依赖外部 API
- **缓存**: 15-300s TTL,减轻数据源压力

---

## 17. 变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-07-29 | 初始版本,覆盖 94 个端点 |
