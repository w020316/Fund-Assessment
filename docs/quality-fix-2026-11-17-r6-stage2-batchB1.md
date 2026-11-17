# 第六轮质量评估 · 阶段二批次 B1 修复记录（数据诚实性 7 项）

**范围**：`d822bc3..44e3a07`｜**提交**：6 个 fix｜**主题**：把「没拿到」和「真的没有」分开
**回归**：**3001 passed / 1 skipped / 0 failed / 86.49s**（批次 A 2925 → **+76 例**，+40 个测试函数，0 用例消失，0 新增 skip）
**线上**：`version=44e3a07`（详见 §四 指纹复验）

---

## 一、逐项闭环

| # | 编号 | 提交 | 状态 |
|---|---|---|---|
| 1 | D-05 「无法判定」被写成「不变」 | `d817af1` | ✅ |
| 2 | D-01 HHI 分母静默丢弃缺失基金 | `7d70462` | ✅ |
| 3 | A-07 + M-03 空列表仍报 80 分健康 | `b390074` | ✅（有范围收敛，见 §五） |
| 4 | A-08 四维全缺仍输出「中性 50 分/观望」 | `10a8817` | ✅ |
| 5 | A-09 股票代码未校验拼进上游 filter | `ede01ba` | ✅ |
| 6 | C-03 降级时显示「¥0.00」 | `44e3a07` | ✅ |

### 1. D-05 持仓变化（`d817af1`）

- **根因**：东财改版后「持仓变化」不在文本列，而是「相关资讯」`<td class='xglj'>` 里的 `变动详情` 链接。代码遍历 cell 找「新进/增持/减持」，一个都匹配不到 → 无条件落默认 `"不变"`。实测 110022/161725 各 10 只全返回 `不变`。
- **方案**：`change` 三态化——默认 `None`；匹配到 新进/增持/减持/**不变** 才赋值；出现 `变动详情` 但解析不出关键词 → `"未披露"`。akshare 兜底表（无变化列）同样改 `None`。
- **验证**：4 条——只有链接无关键词 → 不得是「不变」；连相关资讯列都没有 → `None`；真「增持」仍解析；老版 7 列布局回归通过。

### 2. D-01 组合分散度覆盖度（`7d70462`）

- **根因**：`compute_concentration` 只在拿到 ≥2 点净值的基金上算市值，缺失基金**不进分母**且 `weights` 仍归一化到 1 → 3 只里 1 只缺失就按 2 只算，「有效持仓 2.0」看着像结论。分母缩小 → 集中度必然偏高。
- **方案**：新增 `weights_covered`／`uncovered_codes`／`covered_count`／`nominal_count`；`note` 追加「N 只中 M 只净值缺失（HHI 仅覆盖 M 只，覆盖 x%）」；前端 KPI 行显示覆盖度。
- **口径声明**：`weights_covered` 的分母需估算缺失净值的市值（缺失份额 × 已计价部分的加权平均单位净值），**只进覆盖度、绝不进 HHI/weights**，代码注释已写明。同时提供**只数口径** `covered_count/nominal_count`（无任何估算），两个口径并存。
- **验证**：4+1 条——3 只缺 1 只时 `weights_covered < 1` 且 note 含缺失数；全齐时 `== 1.0`；0 持仓仍 fail-closed 返回 None；接口层暴露覆盖度。

### 3. A-07 + M-03 空数据报 80 分（`b390074`）

- **根因**：`_build_meta` 的默认值是**乐观**的——未传 `quality_score` 就是 `100(cached) / 80(fresh)`，而 `degraded = quality < 70`。全文件 94 次调用里 44 次没传。`b870f3e` 只按端点打了 4 个补丁，`/hot_stocks`、`/hot_stocks_signal`、`/news`、`/global_news` 仍走默认。
- **方案**：① 新增 `_rows_quality(rows, base=80.0)`（空 → 0.0），4 个端点的新鲜分支与缓存分支都显式传分（`/hot_stocks` 三榜**全空**才算空）；② 把默认值抽成命名常量 `_DEFAULT_QUALITY = 80.0`，注释定性为「乐观默认」并写明适用边界。
- **验证**：`tests/test_market_meta_quality.py` 10 条——4 个端点空 payload → `degraded=True` + `quality=0.0`；非空 → 正常；`/hot_stocks` 单榜为空**不得**判空。

### 4. A-08 用缺失数据下结论（`10a8817`）

- **根因**：`_calc_*_score` 对缺失一律回 50 并只写 note，`_calc_thermometer` 固定权重加总 → 全缺时恰好 50.0 → `level=中性`、`action=观望`，路由恒 80 分。**这比空态更有害——它给出了一条投资建议。** 次生：北向缺失时 `nb_score` 固定 50 与板块各占一半，把「资金」维度系统性拉向 50。
- **方案**：① 各 component 加 `available: bool`，顶层加 `data_quality{available_dims, total_dims, missing_dims}` + `score_available`；② 全缺 → `level=数据不足`、文案含「不评分」；③ 路由 0 维 → quality 0.0、≤2 维 → 40.0 + `degraded_reason`；④ 北向缺失时 `capital_score == sector_score`（不再内插 50）；⑤ `/assessment` 与板块信号同样不再凭空下结论；⑥ 前端全缺时不再渲染「50 分 · 中性」表盘。
- **验证**：9 条——全缺 / 1-2 维 / 北向缺失（`capital == sector` 且 `available=false`）/ 四维齐（**数值与改动前完全一致**，回归保护）。

### 5. A-09 查询条件注入（`ede01ba`）

- **根因**：`code: Query("")` 原样进 `f'(SCODE="{code}")'`。可改写东财过滤条件；异常被 `except Exception` 吞成「无数据」，接口沦为任意报表探测器。
- **方案**：① 共享依赖 `_code_or_422`——空串放行（保住前端 `code ? code : '600519'` 兜底）、非空必须 `^\d{6}$` 否则 422 `INVALID_STOCK_CODE`；② `data_source_v2` 侧 `_is_stock_code` 纵深防御，三个函数入口直接返空**且不发请求**。
- **验证**：`tests/test_market_code_validation.py` 43 条（18 注入码 × 端点断言**上游调用次数为 0**、18 条数据源层、空串/合法码回归）。

### 6. C-03 降级显示 ¥0.00（`44e3a07`）

- **根因**：全降级时 `total_est_day_pnl` 强制 `0.0`，前端 `fmtMoney(0)` → `¥0.00`。0 是可被读走的真实数值（"今天不赚不亏"），而真实情况是"算不出来"。另单只降级项 `est_day_pnl=0.0` 而同项 `est_pct=null`，出现「— 涨跌幅 + ¥0.00 盈亏」的矛盾行。
- **方案**：`_estimate_day_pnl` 改 `float|None`（缺 pct / 缺 nav → None；`shares=0` 仍真实 0）；`_summarize_watchboard` **跳过 None 求和**并计 `pnl_missing_count`，全缺 → total None；前端两处显式判 null 显示「—」，全缺整块加 `is-degraded` 暖色边框 + 「估算不可用：当日盈亏无法计算，不是 0 元」。
- **比要求多做的一点（认可）**：部分降级不是简单置 None，而是「已知部分合计 + `pnl_missing_count`」并在 UI 注「其中 N 只无法估算」。这比"部分降级就整块置空"信息量更大且不误导。
- **验证**：4+1 条——全降级 `total_est_day_pnl is None`；正常数值不变；部分降级有值保留 + `pnl_missing_count>0`；前端机制测试断言 null 分支存在且**无 `|| 0` 兜底**。

---

## 二、回归

| 项 | 结果 |
|---|---|
| 本机全量（主理人独立复跑） | **3001 passed / 1 skipped / 0 failed / 86.49s** |
| 测试增量 | 2925 → 3001（**+76**），新增 4 个测试文件（`test_market_meta_quality.py`、`test_market_data_sufficiency.py`、`test_market_code_validation.py`、`test_market_routes_coverage.py` 扩充） |
| 用例消失 / 新增 skip | **0 / 0**（脚本比对测试函数数：2528 → 2568 之后继续增长，无消失） |

## 三、断言变更台账（3 条，全部为「断言锁定的正是缺陷本体」）

| 文件:行号 | 改前 | 改后 | 强度 |
|---|---|---|---|
| `tests/test_fund_holdings.py:431`（用例名 `test_change_unknown_falls_back_to_unchanged`） | `assert result[0]["change"] == "不变"` | `is None` | 精确相等 → 精确 None 判定，**不降**。函数名保留历史命名（不改名，避免被误读成增删用例），docstring 写明旧断言锁的是缺陷 |
| `tests/test_fund_holdings.py:542`（`TestFetchFundHoldingsAk::test_parses_dataframe_rows`） | `== "不变"` | `is None` | 同上（akshare 表本就没有变化列） |
| `tests/test_market_assessment.py:129-137`（`TestCapitalFlowScore::test_sector_main_inflow`） | `approx(55)` | `approx(60)` + 新增 4 条（`northbound_available=False`／`available=True`／`complete=False`／`missing==["northbound"]`） | 1 条 → 5 条，**只升不降** |

## 四、线上指纹复验

| 指纹 | 修复前（实测） | 修复后（实测） | 判定 |
|---|---|---|---|
| `GET /api/market/margin?code=abc123` | **HTTP 200** + `{"data":[]}` | **HTTP 422** `INVALID_STOCK_CODE` | ✅ |
| 同上 `code=60051` / `code=6005199` | 200 / 200 | 422 / 422 | ✅ |
| `GET /api/market/thermometer` 的 `data` 键 | `['action','components','level','score','update_time']`（无可用性契约） | 新增 **`data_quality{available_dims,total_dims,missing_dims}`、`score_available`**，components 各项带 `available` | ✅ |

> 注：`code=600519")+OR+(1=1` 这类注入串在**生产被 Render 边缘 WAF 拦成 403**（返回 `Blocked` 页），未能作为行为指纹。这属平台层防护，**不能替代应用层校验**——WAF 规则可变、且直连源站即绕过，所以 `ede01ba` 的代码级校验仍然必要。

## 五、遗留（如实，不假装根治）

1. **`_build_meta` 默认值仍是乐观的**：改后仍有 **53 处**调用未显式传 `quality_score`（`market.py` 36 / `global_market.py` 6 / `holdings.py` 5 / `news.py` 6），继续依赖 80 分默认。这是**有意的范围收敛**——把默认改成 fail-closed 会让 60+ 调用点一起翻成 `degraded`，把健康端点误标成降级。需后续「逐端点质量契约」专项收口，列入 backlog。
2. **A-08 全缺时 `score` 仍是数字 50**（只改 `level`/`action` + `score_available=false`）。理由：既有断言 `score==50` 未在批准清单内，且前端旧消费点会拿到 null。前端已改为全缺时不渲染表盘，所以不会显示「50 分 · 中性」。
3. **A-08 资金维度只对「北向缺失」重归一**：板块缺失但有北向时保留原口径（缺维按 50 加权），否则 `test_northbound_big_inflow/outflow`（75/25）也要改，不在此次批准范围。
4. **`/research_reports` 的 `code` 是查询参数值**（`requests` 会 URL 编码），不拼进 filter 语法，属不同风险面，未动。

---

> 本记录由 AI 协作生成，关键决策请由工程负责人复核。
