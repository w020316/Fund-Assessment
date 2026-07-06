# S2 基金模块修复与新增记录

**日期**: 2026-07-05
**子项目**: S2(基金模块)
**前置上下文**: 用户主玩基金,需根据大盘/板块波动给出个性化建议。S1 已完成后端基建修复。

---

## 1. 模块概览

| # | 功能 | 类别 | 涉及文件 | 状态 |
|---|------|------|---------|------|
| 1 | 基金规则引擎(大盘/板块/盈亏三维信号) | 新增 | `src/analysis/fund_advisor.py` | ✅ |
| 2 | 基金路由(持仓 CRUD + 建议 + 搜索 + 行情) | 新增 | `web/routes/fund.py` | ✅ |
| 3 | 前端基金页面(快捷键 6) | 新增 | `web/static/index.html` | ✅ |
| 4 | 板块 API 参数修正(行业 vs 概念) | 修复 | `src/core/data_source_v2.py` | ✅ |
| 5 | 基金搜索 NoneType 修复 | 修复 | `web/routes/fund.py` | ✅ |
| 6 | 信号函数 action 键缺失修复 | 修复 | `src/analysis/fund_advisor.py` | ✅ |
| 7 | 单元测试(27 用例) | 新增 | `tests/test_fund_advisor.py` | ✅ |
| 8 | conftest.py 补 pylibs 路径 | 修复 | `tests/conftest.py` | ✅ |

---

## 2. 详细记录

### 2.1 基金规则引擎 `fund_advisor.py`

**设计**:规则可解释 + 不造假 + 保守优先。

**三维信号体系**:
1. **大盘信号** `_build_market_signal`:取绝对值最大涨跌幅的指数(上证/深证/创业板)
   - 跌 > 2% → `dca`(定投逢低加仓)
   - 跌 1-2% → `watch`(关注)
   - 涨 > 2% → `take_profit`(止盈)
   - 涨 1-2% / 平稳 → `hold`(持有)

2. **板块信号** `_build_sector_signal`:基金名称关键词 → 板块映射(45 组)
   - 板块跌 > 2% → `add`(加仓)
   - 板块涨 > 3% → `take_profit`(止盈)

3. **盈亏信号** `_build_pnl_signal`:成本净值 vs 当前净值
   - 浮盈 > 20% → `take_profit`
   - 浮亏 > 15% → `watch`

**信号合并** `_merge_signals`:优先级 `take_profit(4) > add/dca(3) > hold(2) > watch(1) > none(0)`,reason 用 `|` 连接。

**入口** `generate_fund_advice`:并行拉取 index + sector + fund_quotes,为每只基金生成建议 + 整体摘要。

### 2.2 基金路由 `fund.py`

**8 个端点**:
- `GET /api/fund/positions` — 持仓列表(含实时净值与盈亏 enrich)
- `POST /api/fund/positions` — 全量保存(需鉴权)
- `POST /api/fund/positions/add` — 单只添加(需鉴权)
- `DELETE /api/fund/positions/{fund_code}` — 删除(需鉴权)
- `GET /api/fund/advice` — 基金建议
- `GET /api/fund/search?q=xxx` — 基金搜索(东方财富)
- `GET /api/fund/realtime?codes=xxx` — 实时行情(腾讯)
- `GET /api/fund/history?code=xxx&period=1y` — 历史净值

**存储**:`web/user_fund_positions.json`,默认示例持仓 2 只(110022 易方达消费 + 161725 招商白酒)。

### 2.3 前端基金页面

- 导航栏新增"💰 基金管理"(快捷键 6),原项目快捷键 6→7→8→9
- `pageKeys` 数组新增 `'fund'`,快捷键范围 `'1'-'9'`
- 4 概览卡片(总持仓/总市值/总盈亏/总收益率)+ 建议卡片 + 持仓列表表格 + 添加基金表单(含搜索)

### 2.4 板块 API 参数修正

**问题**:`get_sector_ranking` 的 `fs` 参数 `m:90+t:3` 实际是**概念板块**(华为概念/CRO概念),原注释写反了。

**修复**:主请求改为 `m:90+t:2`(行业板块:白酒/食品饮料/医药),降级分支改为 `m:90+t:3`。

### 2.5 基金搜索 NoneType 修复

**问题**:东财搜索 API 返回的 `item.get("FundBaseInfo")` 可能为 None,直接 `.get()` 报错。

**修复**:`base_info = item.get("FundBaseInfo") or {}` 再取值。

### 2.6 信号函数 action 键缺失修复

**问题**:`_build_market_signal` / `_build_sector_signal` / `_build_pnl_signal` 的 "none" 分支返回的 dict 缺少 `action` 键,导致 `generate_fund_advice` 中 `market_signal['action']` 抛 `KeyError`。

**修复**:所有 "none" 分支补充 `"action": "暂无"`。

### 2.7 conftest.py 补 pylibs 路径

**问题**:`tests/conftest.py` 只添加项目根目录到 `sys.path`,未添加 `pylibs`(本地依赖目录),导致不依赖 `PYTHONPATH` 环境变量时 pytest 找不到 `loguru` 等依赖。

**修复**:conftest.py 同时添加 `pylibs` 目录到 `sys.path`。

---

## 3. 测试结果

### 3.1 基金模块单测

```
tests/test_fund_advisor.py: 27 passed in 2.88s
```

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|---------|
| TestInferSectors | 6 | 基金名称→板块关键词推断 |
| TestBuildMarketSignal | 7 | 大盘涨跌幅阈值信号 |
| TestBuildPnlSignal | 5 | 盈亏信号 |
| TestMergeSignals | 5 | 信号合并优先级 |
| TestGenerateFundAdvice | 4 | 端到端建议生成(mock) |

### 3.2 全量回归测试

```
tests/: 108 passed, 2 warnings in 7.79s
```

无回归。S1 的 81 个用例 + S2 的 27 个用例全部通过。

---

## 4. 端到端验证

服务运行于 `http://localhost:8000`,以下端点验证通过:
- `GET /api/fund/positions` — 返回真实持仓 + 实时净值 + 盈亏
- `GET /api/fund/advice` — 返回大盘信号 + 每只基金建议(含 reason)
- `GET /api/fund/search?q=白酒` — 返回匹配结果
- `GET /api/fund/realtime?codes=110022,161725` — 返回真实净值

---

## 5. 遗留与后续

- LLM 摘要部分暂未接入(规则引擎已可独立工作,LLM 摘要可作为 S5 增强项)
- 板块 API 在 push2 不可用时走 sina 降级返回概念板块,属可接受降级
- 后续 S3 将接入国际股市数据(美股/港股/日股)
