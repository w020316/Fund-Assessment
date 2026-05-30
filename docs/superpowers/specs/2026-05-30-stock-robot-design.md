# OpenClaw 量化 AI 炒股机器人 — 设计文档

> 版本: v1.0 | 日期: 2026-05-30 | 平台: OpenClaw AI Agent

## 1. 产品定位

基于 OpenClaw AI Agent 平台的 A 股量化自动交易系统，覆盖数据获取、多维分析、策略执行、风险控制全链路。

## 2. 架构设计：分层模块化

```
┌─────────────────────────────────────────────────────┐
│                    scripts/ 入口层                    │
│  quant.py | cb_monitor.py | limit_up_monitor.py ... │
├─────────────────────────────────────────────────────┤
│                  strategies/ 策略层                   │
│  trading_quant | cb_t0_sniper | limit_up_analyzer   │
│  stock_monitor | a_stock_analyst | bspro_quant      │
├─────────────────────────────────────────────────────┤
│                  analysis/ 分析层                     │
│  technical | capital_flow | fundamental | sentiment  │
├─────────────────────────────────────────────────────┤
│                    core/ 核心层                       │
│  data_source | risk_manager | executor | scheduler  │
├─────────────────────────────────────────────────────┤
│                    utils/ 工具层                      │
│            logger | config | notify                  │
└─────────────────────────────────────────────────────┘
```

## 3. 数据流

```
数据源(AkShare/Tushare/东方财富/新浪/同花顺)
  → DataSource(4层降级) → 缓存
  → Analysis(技术面25%+资金面30%+基本面10%+消息面20%+情绪面15%)
  → Strategy(评分信号: STRONG_BUY/BUY/WATCH/HOLD/SELL/STRONG_SELL)
  → RiskManager(4级风控拦截)
  → Executor(券商API实盘执行)
  → Notify(钉钉/微信推送)
```

## 4. 核心模块

### 4.1 DataSource — 数据获取层
- 4 层降级: 主源→备用源1→备用源2→缓存→无数据提示
- 统一 DataSource 抽象接口
- 支持实时行情、历史K线、资金流向、北向资金、新闻

### 4.2 RiskManager — 风控引擎
- 系统级: 资产回撤>15% → 清仓+暂停5日
- 日级: 单日亏损>5% → 次日不开新仓
- 策略级: 连续3次止损 → 降低仓位50%
- 异常级: 系统异常 → 立即停止自动交易
- 所有交易指令必须经过风控检查

### 4.3 Executor — 交易执行器
- 对接券商API
- 支持买入/卖出/撤单
- 交易记录持久化
- 滑点控制

### 4.4 Scheduler — 任务调度器
- 基于 APScheduler
- 每日工作流: 盘前/盘中/尾盘/盘后
- 任务超时机制
- 异常告警

## 5. 策略模块

### 5.1 trading_quant — 量化评分系统
- 多维度评分: 技术面25% + 资金面30% + 基本面10% + 消息面20% + 情绪面15%
- 信号: ≥80 STRONG_BUY | 65-79 BUY | 50-64 WATCH | 35-49 HOLD | 20-34 SELL | <20 STRONG_SELL

### 5.2 cb_t0_sniper — 可转债T+0
- 正股涨停→资金转向可转债→T+0日内套利
- 止损-3%，仓位100%

### 5.3 limit_up_analyzer — 涨停板分析
- 连板层级/涨停原因/封板质量评分

### 5.4 stock_monitor — 股票监控
- 7大预警规则: 涨幅异常/EPS超预期/量价背离/大宗交易/RSI/北向资金/行业轮动

### 5.5 a_stock_analyst — A股分析师
- 基本面+技术面+行业分析

### 5.6 bspro_quant — BitSoul量化
- 20+因子库/Alpha因子挖掘/自定义回测

## 6. 四大交易策略参数

| 策略 | 仓位上限 | 持仓数 | 总仓位 | 止损 | 执行方式 |
|------|---------|--------|--------|------|---------|
| 创业板新高 | 20% | 5只 | 80% | ATR动态/-8% | 即时/收盘 |
| 涨停板 | 10% | 3只 | 30% | -5% | 立即/收盘 |
| 可转债T+0 | 2只/5只 | - | 100% | -3% | 立即 |
| 长线价值 | 30% | 3只 | 90% | -15% | 收盘 |

## 7. 技术选型

- 语言: Python 3.12
- 数据处理: Pandas + NumPy
- 技术指标: pandas-ta
- 数据源: AkShare(主) + Tushare(备)
- 消息推送: 钉钉/企业微信 Webhook
- 任务调度: APScheduler
- 数据存储: SQLite
- 日志: Loguru
- 交易接口: 券商API(实盘)
