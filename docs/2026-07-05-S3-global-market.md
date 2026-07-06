# S3 国际股市数据新增记录

**日期**: 2026-07-05
**子项目**: S3(国际股市数据)
**前置上下文**: S1/S2 已完成。项目原有数据全部聚焦 A 股(沪深),无任何美股/港股/国际指数实现。

---

## 1. 模块概览

| # | 功能 | 类别 | 涉及文件 | 状态 |
|---|------|------|---------|------|
| 1 | 国际股市数据源函数(腾讯接口) | 新增 | `src/core/data_source_v2.py` | ✅ |
| 2 | 国际市场路由(6 端点) | 新增 | `web/routes/global_market.py` | ✅ |
| 3 | api.py 注册路由 | 修改 | `web/api.py` | ✅ |
| 4 | 前端 page-global 页面(快捷键 7) | 新增 | `web/static/index.html` | ✅ |
| 5 | 顶部栏追加国际指数 | 新增 | `web/static/index.html` | ✅ |
| 6 | 单元测试(15 用例) | 新增 | `tests/test_global_market.py` | ✅ |

---

## 2. 详细记录

### 2.1 数据源函数 `data_source_v2.py`

**数据源**:腾讯财经接口 `https://qt.gtimg.cn/q=<codes>`,GBK 编码,`~` 分隔字段。

**代码前缀**(实测确认):
- 美股:`us<AAPL>`(如 `usAAPL`, `usTSLA`)
- 港股:`hk<00700>`(5 位数字)
- 国际指数:`usDJI`(道琼斯)、`usIXIC`(纳斯达克)、`usINX`(标普500)、`hkHSI`(恒生)、`hkHSCEI`(国企)

**字段索引**(与 A 股略有不同):
| 索引 | 字段 |
|------|------|
| parts[1] | 名称 |
| parts[2] | 代码(可能带交易所后缀如 AAPL.OQ) |
| parts[3] | 最新价 |
| parts[4] | 昨收 |
| parts[5] | 开盘 |
| parts[6] | 成交量 |
| parts[31] | 涨跌额 |
| parts[32] | 涨跌幅 |
| parts[33] | 最高 |
| parts[34] | 最低 |
| parts[35] | 币种(美股是 USD;港股指数此处是价格,需特殊处理) |

**新增函数**:
- `_tencent_global_quote(codes)` — 通用腾讯国际行情解析
- `get_global_indices()` — 5 个国际指数(含 market 标记 + 币种补全)
- `get_us_stock_realtime(symbols)` — 美股实时(代码清洗去交易所后缀)
- `get_hk_stock_realtime(codes)` — 港股实时(代码补齐 5 位)
- `get_us_hot_stocks()` — 美股热门 10 只科技龙头
- `get_hk_hot_stocks()` — 港股热门 10 只蓝筹
- `get_global_market_overview()` — 并行总览(指数+美股+港股)

**关键修复**:
1. **代码前缀错误**:初版用 `gb_aapl`(小写),实测无返回。正确为 `usAAPL`(us+大写)。
2. **字段索引错误**:初版 `change=parts[30]`,实测 `parts[30]` 是时间字符串。正确为 `parts[31]`。
3. **币种字段错误**:初版 `currency=parts[37]`,实测港股指数 `parts[35]` 是价格(23350.030)而非 HKD。修复:数字则置空,在 `get_global_indices` 中按市场类型补全。

### 2.2 国际市场路由 `global_market.py`

**6 个端点**(前缀 `/api/global`):
- `GET /api/global/indices` — 国际指数(道琼斯/纳斯达克/标普500/恒生/国企)
- `GET /api/global/us_hot` — 美股热门 10 只
- `GET /api/global/hk_hot` — 港股热门 10 只
- `GET /api/global/us_realtime?codes=AAPL,TSLA` — 美股按代码查询
- `GET /api/global/hk_realtime?codes=00700,09988` — 港股按代码查询
- `GET /api/global/overview` — 并行总览(15s 缓存)

**文件命名注意**:Python 中 `global` 是关键字,不能用作模块名,因此文件命名为 `global_market.py`,导入为 `from web.routes import global_market as global_route`。

### 2.3 前端 page-global 页面

**导航**:侧边栏新增"🌍 国际市场"(快捷键 7),原 data/monitor/config 快捷键 7→8→9 改为 8→9→0。

**页面结构**:
1. 国际指数卡片(grid-3,5 个指数,含国旗 emoji + 名称 + 价格 + 涨跌幅)
2. 美股热门 + 港股热门(grid-2 并列表格)
3. 国际个股查询(市场选择 + 代码输入 + 查询结果表格)

**顶部栏增强**:`loadGlobalIndexBar()` 在 A 股指数后追加国际指数(带国旗)。

**新增 JS 函数**:`loadGlobalData` / `loadGlobalIndices` / `loadUsHot` / `loadHkHot` / `queryGlobalStock` / `loadGlobalIndexBar`。

---

## 3. 测试结果

### 3.1 国际市场单测

```
tests/test_global_market.py: 15 passed in 1.70s
```

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|---------|
| TestTencentGlobalQuote | 5 | 腾讯接口解析(美股/港股/空代码/涨跌额兜底/币种数字清除) |
| TestGetGlobalIndices | 2 | 市场标记 + 币种补全 |
| TestGetUsStockRealtime | 3 | 代码清洗/空输入/币种默认 |
| TestGetHkStockRealtime | 3 | 代码补齐/空输入/币种默认 |
| TestGetGlobalMarketOverview | 2 | 并行聚合 + 空数据处理 |

### 3.2 全量回归测试

```
tests/: 123 passed, 2 warnings in 6.07s
```

无回归。S1(81)+ S2(27)+ S3(15)= 123 全部通过。

---

## 4. 端到端验证

服务运行于 `http://localhost:8000`,以下端点验证通过:
- `GET /api/global/indices` — 5 个指数(道琼斯/纳斯达克/标普500/恒生/国企)
- `GET /api/global/us_hot` — 10 只美股(AAPL/MSFT/GOOGL/AMZN/NVDA/META/TSLA/NFLX/AMD/INTC)
- `GET /api/global/hk_hot` — 10 只港股(腾讯/阿里/美团/快手/小米/京东/金山/港交所/中移动/友邦)
- `GET /api/global/us_realtime?codes=AAPL,TSLA` — 返回 2 只美股
- `GET /api/global/hk_realtime?codes=00700,09988` — 返回 2 只港股
- `GET /api/global/overview` — indices=5 us_hot=10 hk_hot=10

---

## 5. 遗留与后续

- 腾讯接口不支持日经225/富时100/DAX/CAC/韩国综合/印度孟买等指数,仅支持美股和港股相关指数。后续如需更多国际指数,可引入 yfinance 或其他数据源。
- 美股/港股历史 K 线暂未接入(腾讯接口的国际 K 线需要额外接口)。如需要,可作为后续增强项。
- 后续 S4 将接入股市/基金话术库(借鉴 git 项目)。
