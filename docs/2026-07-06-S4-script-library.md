# S4 话术库新增记录

**日期**: 2026-07-06
**子项目**: S4(话术库)
**前置上下文**: S1/S2/S3 已完成。项目缺少向用户解释行情/基金建议的话术能力,AI 分析结果偏技术化,普通用户难以理解。

---

## 1. 模块概览

| # | 功能 | 类别 | 涉及文件 | 状态 |
|---|------|------|---------|------|
| 1 | 话术模板引擎(24 模板 + 变量填充 + 智能匹配) | 新增 | `src/analysis/script_library.py` | ✅ |
| 2 | 话术库路由(6 端点) | 新增 | `web/routes/scripts.py` | ✅ |
| 3 | api.py 注册路由 | 修改 | `web/api.py` | ✅ |
| 4 | 前端基金页话术推荐卡片 | 新增 | `web/static/index.html` | ✅ |
| 5 | 前端 AI 页话术库浏览面板 | 新增 | `web/static/index.html` | ✅ |
| 6 | 单元测试(26 用例) | 新增 | `tests/test_script_library.py` | ✅ |

---

## 2. 详细记录

### 2.1 话术模板引擎 `script_library.py`

**预置模板**:共 24 条,覆盖股市与基金主要场景。

| 分类 | 数量 | 场景 | 维度 |
|------|------|------|------|
| 股市话术 | 12 | buy(4) / sell(3) / hold(2) / wait(2) / stop_loss(1) | 技术面 / 基本面 / 资金面 / 情绪面 |
| 基金话术 | 12 | dca(3) / take_profit(3) / add(2) / hold(2) / wait(2) | 择时 |

**模板格式**:`{变量名}` 占位,如:
```
{stock_name}突破关键阻力位 {resistance_price},成交量放大至 {volume_ratio} 倍,
MACD 金叉,短期动能强劲,可考虑分批建仓,止损位 {stop_loss_price}。
```

**变量填充规则** `_fill_template(template, variables)`:
- 正则 `\{(\w+)\}` 匹配占位符
- 缺失变量用「—」占位(不报错)
- 浮点数智能格式化:整数值(如 `1800.0`)显示为 `1800`,非整数显示 2 位小数(如 `2.50`)
- 字符串直接替换

**智能匹配**:
- `match_fund_scripts(fund_advice)`:基金建议信号(take_profit/add/dca/hold/watch/none)→ 话术场景(take_profit/add/dca/hold/wait/wait),自动填充 fund_name/fund_code/current_nav/cost_nav/pnl_pct/index_name/index_change_pct
- `match_stock_scripts(stock_data)`:根据涨跌幅 + 浮盈决定场景
  - `pnl_pct >= 20` 或 `change_pct >= 5` → sell
  - `change_pct <= -5` → wait
  - 其他 → hold

### 2.2 路由端点 `scripts.py`

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/scripts/list?category=&scene=` | 列出话术模板(可过滤) |
| GET | `/api/scripts/categories` | 获取分类结构 |
| GET | `/api/scripts/{script_id}` | 按 ID 获取单个模板 |
| POST | `/api/scripts/generate` | 根据模板 ID + 变量生成话术 |
| POST | `/api/scripts/match/fund` | 根据基金建议自动匹配话术 |
| POST | `/api/scripts/match/stock` | 根据个股数据自动匹配话术 |

**请求体结构**:
- `GenerateRequest`: `{"script_id": "stock_buy_breakthrough", "variables": {"stock_name": "贵州茅台", ...}}`
- `MatchFundRequest`: `{"fund_advice": {fund_advisor.generate_fund_advice 返回的单只 advice 项}}`
- `MatchStockRequest`: `{"stock_data": {"code", "name", "price", "change_pct", "pnl_pct", ...}}`

### 2.3 前端集成

**基金页**:
- 基金建议卡片后新增 `fundScriptCard` 话术推荐卡片
- `loadFundAdvice` 完成后调用 `loadFundScripts(advices, market)` → POST `/api/scripts/match/fund`
- 按建议信号分组展示匹配的话术

**AI 页**:
- 分析历史前新增话术库浏览面板
- 支持按分类(stock/fund)+ 场景(buy/sell/hold/...)下拉过滤
- `switchPage('ai')` 时同时加载 `loadScriptLibrary()` 与 `loadLlmProvider()`

---

## 3. 测试结果

### 3.1 单元测试 `tests/test_script_library.py`

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|---------|
| TestListScripts | 5 | 全部/按 category/按 scene/组合过滤/空结果 |
| TestGetScript | 2 | 存在/不存在 |
| TestFillTemplate | 5 | 完整填充/缺失变量「—」占位/浮点格式化/整数浮点/空值 |
| TestGenerateScript | 3 | 完整生成/不存在模板/变量缺失兜底 |
| TestMatchFundScripts | 4 | take_profit/add/hold/watch 信号映射 |
| TestMatchStockScripts | 4 | 大涨卖出/大跌观望/正常持有/浮盈止盈 |
| TestGetScriptCategories | 3 | 分类结构/场景列表/维度列表 |

**结果**:26 passed, 2 warnings in 1.68s

### 3.2 全量回归测试

```
tests/
├── test_data_source_v2.py    (S1)  42 passed
├── test_fund_advisor.py      (S2)  27 passed
├── test_global_market.py     (S3)  15 passed
├── test_script_library.py    (S4)  26 passed
├── test_llm_router.py        (S1)  17 passed
├── test_market_routes.py     (S1)  14 passed
├── test_monitor_routes.py    (S1)   8 passed
└── ...
============================== 149 passed, 2 warnings in 5.94s ===============================
```

S1 + S2 + S3 + S4 累计 149 用例全部通过,无回归。

### 3.3 端到端验证

服务重启后(`python launch.py --reload`),6 个端点全部返回正常:

| 端点 | 验证结果 |
|------|---------|
| GET /api/scripts/categories | ✅ 返回 stock(12)+fund(12)=24,5 个场景 |
| GET /api/scripts/list?category=stock&scene=buy | ✅ 返回 4 个买入话术 |
| GET /api/scripts/stock_buy_breakthrough | ✅ 返回单条话术详情 |
| POST /api/scripts/generate | ✅ "贵州茅台突破关键阻力位 1800,成交量放大至 2.50 倍,MACD 金叉,短期动能强劲,可考虑分批建仓,止损位 1700。" |
| POST /api/scripts/match/fund | ✅ take_profit 信号匹配 3 条止盈话术,变量正确填充(浮盈 8.96%、上证指数涨 0.50%) |
| POST /api/scripts/match/stock | ✅ change_pct=3.5 匹配 2 条持有话术 |

---

## 4. 关键设计决策

1. **模板 + 变量填充**(非 AI 生成):响应快(<10ms)、可预测、无 API 成本
2. **场景驱动**:每条话术标注 scene,匹配时按信号 → 场景映射,避免误推荐
3. **缺失变量兜底**:用「—」占位而非报错,保证输出完整可读
4. **基金建议复用**:match/fund 直接接收 `fund_advisor.generate_fund_advice` 返回结构,前端无需额外转换
5. **双入口设计**:基金页(情景化推荐)+ AI 页(浏览查询),覆盖使用与探索两种场景

---

## 5. 文件清单

**新增**:
- `src/analysis/script_library.py`(405 行)— 24 模板 + 引擎
- `web/routes/scripts.py`(98 行)— 6 端点
- `tests/test_script_library.py`— 26 用例
- `docs/2026-07-06-S4-script-library.md`— 本文档

**修改**:
- `web/api.py`— 注册 scripts 路由
- `web/static/index.html`— 基金页话术卡片 + AI 页话术库面板

---

## 6. 后续计划

S4 完成。下一子项目:**S5 前端去 AI 化重设计**(移除 UI 上的 AI 品牌标识,统一为 QuantFlow Pro 风格)。
