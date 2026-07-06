# S1 后端基建修复记录

**日期**: 2026-07-05
**子项目**: S1(后端基建修复)
**前置上下文**: 全面质量评估发现 4 类系统性问题——无鉴权、async 同步阻塞、数据造假 fallback、代码重复

---

## 1. 修复概览

| # | 类别 | 严重度 | 涉及文件数 | 状态 |
|---|------|--------|-----------|------|
| 1 | 敏感端点无鉴权 | 高 | 4 | ✅ 完成 |
| 2 | async 端点同步阻塞 | 高 | 3 | ✅ 完成 |
| 3 | trade.py 重复代码 + 模拟撤单造假 | 中 | 1 | ✅ 完成 |
| 4 | 融资融券硬编码比例造假 | 高 | 1 | ✅ 完成 |
| 5 | 股东户数单位 bug + 硬编码造假 | 高 | 1 | ✅ 完成 |
| 6 | 龙虎榜用涨幅榜冒充 + 买卖额填 0 | 高 | 1 | ✅ 完成 |
| 7 | 基金 change_pct 除零 + 字段语义错误 | 高 | 1 | ✅ 完成 |
| 8 | `_gather_stock_data` 11 次串行请求 | 中 | 1 | ✅ 完成 |
| 9 | `_safe_float`/`_safe_str` 4+ 文件重复 | 低 | 4 | ✅ 完成 |

---

## 2. 详细修复记录

### 2.1 敏感端点鉴权(无 → 静态 Token)

**问题**: 6 个写/交易端点完全无鉴权,任意客户端可调用。

**方案**: 静态 Token + 环境变量(用户确认)。

**新增**:
- `src/utils/auth.py` —— `require_admin` FastAPI 依赖
  - 通过 `Authorization: Bearer <token>` 校验
  - `ADMIN_TOKEN` 未配置时放行但记警告(开发模式)
  - 用 `secrets.compare_digest` 防时序攻击

**应用**:
- `web/routes/config.py`: `update_settings`、`update_strategies`、`save_user_positions`
- `web/routes/trade.py`: `buy`、`sell`、`cancel`

**配置**:
- `.env` / `.env.example`: 新增 `ADMIN_TOKEN=`(含生成说明)

### 2.2 async 端点同步阻塞修复

**问题**: `market.py` / `dashboard.py` / `monitor.py` 大量 `async def` 端点直接调用同步网络函数,阻塞事件循环。

**修复**:
- 全部用 `await asyncio.to_thread(...)` 包装同步调用
- `market.py`: 30+ 处编辑,覆盖 stock_realtime / stock_kline / fund_* / index_realtime / hot_stocks / sector_ranking / research_reports / dragon_tiger / margin / block_trades / shareholder / news / global_news / hot_stocks_signal / search / stock_detail / northbound / market_sentiment / market_wide_stats
- `dashboard.py`: `_enrich_positions_with_realtime` 改 `async def` + 4 处 `await`; `broker.get_balance` / `get_positions` / `risk_manager.get_risk_status` 用 `asyncio.gather` 并行
- `monitor.py`: `_generate_default_alerts` 改 `async def`; `monitor.check_alerts` / `ds2.get_realtime_quote_tencent` / `get_index_realtime` / `get_northbound_flow_realtime` / `get_capital_flow_detail` 全部包装

### 2.3 trade.py 重构

**问题**: `buy` / `sell` 重复代码;`cancel` 在交易未启用时返回"模拟撤单成功"(造假)。

**修复**:
- 抽出 `_execute_order(req, side, request)` 公共方法
- `cancel` 的"模拟撤单成功"改为 503 "交易功能未启用, 无法撤单"

### 2.4 数据造假 fallback 移除(3 处)

**统一策略**: 移除造假估算,返回空数据 + 日志说明 "no fabrication"。假数据比无数据更糟。

| 函数 | 历史造假 | 修复后 |
|------|---------|--------|
| `_get_margin_trading_fallback` | `margin_ratio=0.012` 硬编码,`margin_balance=总市值*0.012` | 返回 `{}` |
| `_get_shareholder_count_fallback` | `total_market_value`(元)按亿比较导致分档全错,`base_holders` 硬编码 500000/150000/30000/6000 | 返回 `{}` |
| `_get_dragon_tiger_sina` / `_get_dragon_tiger_from_ranking` | 用涨幅榜 `|change_pct|>=5%` 冒充龙虎榜,`buy_amount=0` / `sell_amount=0` / `net_amount=0` 全填 0 | 返回 `[]` |

### 2.5 基金 change_pct 双重 bug

**Bug 1(除零)**: `change / (nav - change) * 100 if (nav - change) != 0 else 0.0`
- 当 `nav == change` 时 `prev_nav = 0`,数据无效但被静默吞掉

**Bug 2(字段语义错误,更严重)**:
- 腾讯基金接口 `parts[7]` 是**涨跌幅百分比**(如 `1.566` 表示 1.566%),不是绝对涨跌额
- 原代码误当绝对值再除以 `(nav-change)` 反推百分比
- 实测 `110022 易方达消费行业股票`: nav=2.724, parts[7]=1.566 → 算出 **135.23%**(荒谬,基金单日不可能涨 135%)

**修复**(双保险):
```python
change_pct_raw = _safe_float(parts[7])
if abs(change_pct_raw) <= 20:
    # 合理范围(基金单日涨跌幅不会超过 20%),parts[7] 即百分比
    change_pct = change_pct_raw
    if change_pct != 0:
        prev_nav = nav / (1 + change_pct / 100)
        change = round(nav - prev_nav, 4)
    else:
        change = 0.0
else:
    # 异常值(>20%): 字段格式可能变更,降级为绝对值再算百分比
    change = change_pct_raw
    prev_nav = nav - change
    if prev_nav > 0:
        change_pct = change / prev_nav * 100
    else:
        change_pct = 0.0
```

**验证**: 110022 change_pct 从 135.23% → 1.57%;161725 从 44.03% → 0.16%(均合理)。

### 2.6 `_gather_stock_data` 串行 → 并行

**问题**: `src/core/ai_service.py` 的 `_gather_stock_data` 串行调用 11 个网络函数(quote / kline / capital_flow / financial / company / margin / shareholder / research_reports / news / dragon_tiger / northbound / global_news),总耗时 = ∑ 各请求耗时。

**修复**: 用 `data_source_v2._parallel_fetch`(基于 `ThreadPoolExecutor`)并行,总耗时 ≈ max(各请求耗时)。

### 2.7 `_safe_float` / `_safe_str` 去重

**问题**: `market.py` / `monitor.py` / `dashboard.py` / `data_source_v2.py` 各自重复定义,行为略有差异(monitor/dashboard 不处理 NaN/Inf)。

**修复**:
- `src/utils/convert.py` 提供 `safe_float` / `safe_str` / `safe_int`(完整 NaN/Inf 处理)
- 3 个路由文件改为 `from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str`
- `data_source_v2.py` 保留内部定义(下划线私有,使用最广,改动风险大)

---

## 3. 测试结果

### 3.1 新增单元测试

| 测试文件 | 用例数 | 覆盖范围 |
|---------|--------|---------|
| `tests/test_auth.py` | 8 | 鉴权依赖(未配置放行 / 缺头 401 / 错令牌 401 / Bearer / 裸 token / 空令牌 / 生成 / 防时序) |
| `tests/test_convert.py` | 18 | `safe_float` / `safe_str` / `safe_int`(None/NaN/Inf/字符串/空串) |
| `tests/test_data_fabrication_fix.py` | 16 | 3 处造假 fallback 返回空 + 基金 change_pct 4 种场景 + `_parallel_fetch` 3 项 |

### 3.2 全量测试

```
$ pytest tests/test_auth.py tests/test_convert.py tests/test_data_fabrication_fix.py \
         tests/test_data_validator.py tests/test_llm_router.py
============================= 68 passed in 4.13s ==============================
```

**注**: `tests/test_api.py` 涉及真实网络调用,易因外部 API 限流/超时 hang,本次未纳入离线测试集,留待手动验证。

### 3.3 端到端验证(服务在 http://localhost:8000 运行)

| 端点 | 修复前 | 修复后 |
|------|--------|--------|
| `/api/health` | `{"agnes":true,...}` | `{"agnes":true,...}` ✓ |
| `/api/market/index_realtime` | 真实数据(上证 4043.64) | 真实数据 ✓ |
| `/api/market/dragon_tiger` | 涨幅榜冒充,`buy_amount=0` | `{"data":[]}` ✓ |
| `/api/market/shareholder?stock_code=600519` | `holder_num=6000`(造假) | `holder_num=0`(空) ✓ |
| `/api/market/margin?stock_code=600519` | `margin_balance=总市值*0.012`(造假) | `margin_balance=0`(空) ✓ |
| `/api/market/fund_realtime?codes=110022,161725` | `change_pct=135.23`(荒谬) | `change_pct=1.57` ✓ |

---

## 4. 涉及文件清单

### 新增(4)
- `src/utils/auth.py`
- `src/utils/convert.py`
- `tests/test_auth.py`
- `tests/test_convert.py`
- `tests/test_data_fabrication_fix.py`
- `docs/2026-07-05-S1-backend-infra-fix.md`(本文档)

### 修改(8)
- `src/core/data_source_v2.py` —— 3 处造假 fallback + 基金 change_pct 双 bug
- `src/core/ai_service.py` —— `_gather_stock_data` 并行化
- `web/routes/market.py` —— 全文异步化 + 去重 `_safe_float`
- `web/routes/dashboard.py` —— 全文异步化 + 去重 `_safe_float`
- `web/routes/monitor.py` —— 全文异步化 + 去重 `_safe_float`
- `web/routes/config.py` —— 加鉴权
- `web/routes/trade.py` —— 加鉴权 + 抽公共方法 + 移除造假撤单
- `.env` / `.env.example` —— 新增 `ADMIN_TOKEN`
- `tests/test_llm_router.py` —— 修复 `test_chat_all_providers_fail` 的 env 隔离

---

## 5. 后续工作(留待 S2-S7)

S1 仅覆盖后端基建。剩余子项目:

- **S2**: 基金模块(用户主玩基金,需根据大盘/板块波动给建议)
- **S3**: 国际股市数据(美股 / 港股 / 日股等)
- **S4**: 股市/基金话术库(借鉴 git 项目)
- **S5**: 前端去 AI 化重设计(蓝紫渐变 + Emoji + 卡片同构 → 专业金融风)
- **S6**: 测试补全(核心模块零覆盖 → 关键路径覆盖)
- **S7**: 交付文档(用户手册 / 验收报告)
