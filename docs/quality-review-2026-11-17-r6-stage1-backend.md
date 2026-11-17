# 第六轮质量评估 · 阶段一增量代码审查（后端 / 数据面）

- 审查人：高见远（Architect）
- 审查日期：2026-11-17
- 基线锚定：第五轮 HEAD `0ae5b7a`（2026-11-12）
- 被审提交区间：`0ae5b7a..561583b`（当前 HEAD `561583b`，工作树干净）
- 审查方式：**只读**。未修改任何源码、未提交、未运行会写文件的脚本。

---

## 1. 审查范围与方法

### 1.1 覆盖的提交（后端/数据面相关 11 个）

| 提交 | 主题 | 归属组 |
|------|------|--------|
| `561583b` | 两融余额改交易所官方源 + 北向停更如实标注 | A |
| `2fa4fa0` | 龙虎榜改单次请求 | A / E |
| `891a4b1` | 东财报表下线/改名恢复（融资融券/大宗/股东/龙虎榜） | A |
| `b870f3e` | 空数据不再谎报高质量 | A |
| `ea5a9fc` | 资金连增冷启动跳过 down 源 + 新浪历史兜底 | A |
| `30abf5c` | 宽表缺失值不伪造 0 | A |
| `04fd6d9` | 数据源可用性与来源标注 | A |
| `e5cd416` | 资金连增多源回退 + 个股连增端点 | A |
| `3f16263` | ly77625 原生 Gemini 协议开关 | B |
| `0576bb8` | 扫描端点加 15s 上限 → 503 | E |
| `ce448dc` | 东财重仓股表格改版（`fund_holdings.py`，另一组范围，本轮只做交叉确认） | — |

### 1.2 覆盖的文件

| 文件 | 现状行数 | 增量行数 |
|------|---------|---------|
| `src/core/data_source_v2.py` | 3361 | +533/-... |
| `src/core/data_health.py` | 203 | +22 |
| `src/analysis/market_assessment.py` | 348 | +35 |
| `src/analysis/ddx_streak.py` | 381 | 新增 |
| `web/routes/market.py` | 1607 | +407 |
| `web/routes/strategy.py` | 678 | +141 |
| `src/core/llm_router.py` | 1266 | +20 |
| `src/core/ai_service.py` | 1531 | +7 |
| `.env.example` | — | +6 |

### 1.3 手法

1. `git diff 0ae5b7a..HEAD -- <file>` 逐 hunk 定位增量，再用 `Read` 打开 **当前 HEAD 完整上下文**（不是只看 diff）——报告里所有行号均为当前 HEAD 真实行号，逐条用 `grep -n` 复核过。
2. 关键路径做**上游语义推断与实测证据交叉**：用 `tests/test_data_source_v2_eastmoney_reports.py` 的 fixture 判断字段语义变化（如 `RQMCL`），用 `git show 0ae5b7a:<file>` 判断某问题是否为**存量**（避免把老问题记到本轮提交上）。
3. 数据诚实性红线专项：从 `data_source_v2`（返回 None/0）→ `market_assessment`（聚合）→ `routes/market.py`（序列化 + `_meta.quality`）→ 前端（`_numOrDash` / `available` 判断）**逐环跟读**，找"哪一环把 None 变回 0"。
4. LLM 五条硬约束专项：核对本次 diff 是否触碰 `_providers` 排序 / `priority` / `standard` 全链视图 / 路由缓存 key / `route is None` 超时公式 / deep 预算，并核对 `tests/test_llm_router.py:505-517` 的硬断言未被改动。
5. 未联网、未跑测试（避免写 `.pytest_cache`）。测试基线沿用主理人实测：**2885 passed / 1 skipped**。凡需线上响应才能定论的，一律写「待确认」而非推断。

---

## 2. 问题清单

> 级别定义：**P0 阻断**（线上功能错误 / 数据错到会误导决策）｜**P1 严重**（明确可复现的错误或不可用风险）｜**P2 一般**（契约/口径/性能偏差，有明确触发条件）｜**P3 建议**（坏味道、可维护性）。
> 「存量」标记表示该问题在基线 `0ae5b7a` 已存在，本轮**同类改动未覆盖到它**（本轮修复不完整，仍需处理，但不算回归）。
>
> **统计（v2，已合入主理人实测证据）：P0 阻断 0 ｜ P1 严重 5 ｜ P2 一般 10 ｜ P3 建议 10，合计 25 条。**
> v2 变更：A-06 由「待确认」升级为**实测确认**；A-10 措辞按实测行数修正；**新增 A-16（大宗交易溢价率差 100 倍，P2）**；A-05 修复方案按产品裁决改为「限流 + 单飞，不动鉴权」；§5 待确认 1~4 已实测闭环。

### 2.1 A 组 · 数据源治理

| 编号 | 文件:行号 | 组 | 级别 | 问题描述（触发条件 → 可观察后果） | 修复建议（可直接落地） |
|------|-----------|----|------|-----------------------------------|------------------------|
| A-01 | `src/core/data_source_v2.py:2501-2502` + `src/analysis/market_assessment.py:258-265` | A | **P1** | **沪深拆分位被伪造成 0 并原样透传给前端**。`get_northbound_flow_realtime()` 在只拿到合计时写死 `"sh_net_inflow": 0.0, "sz_net_inflow": 0.0`（用 `sh_sz_split_available=False` 兜底标注）；但 `get_capital_flow_overview()` 只判 `nb_ok = bool(northbound)`，**丢掉了这个 flag**，把 0.0 原样输出成 `sh_net_inflow: 0.0 / sz_net_inflow: 0.0`。前端 `web/static/index.html:4636-4637` 用 `_numOrDash()` 渲染，0.0 → 显示「0.00」。触发条件：上游 `RPT_MUTUAL_DEAL_HISTORY` 恢复给出净额（正是本轮刻意保留的"自愈探针"路径）。后果：用户看到「沪股通 0.00 亿 / 深股通 0.00 亿」——把"无拆分数据"读成"沪深各自恰好 0 净流入"，与本项目最核心的数据诚实性红线冲突。（当前因上游停更、合计为 None 而处于潜伏态） | 两侧同时改：① `data_source_v2.py:2501-2502` 改成 `None`；② `market_assessment.py:252-267` 增加 `nb_split_ok = bool(northbound) and northbound.get("sh_sz_split_available") is not False`，`sh/sz` 改为 `... if nb_split_ok else None`，并在 `northbound` 字典里透传 `"sh_sz_split_available": nb_split_ok`；③ 同步在 `tests/test_market_assessment.py` 的 `TestCapitalFlowOverview` 加一条 `sh_net_inflow is None` 的断言锁死。 |
| A-02 | `src/core/data_health.py:102-109`（`_status`）被 `:112-116`（`is_down`）复用 | A | **P1** | **冷启动首败即判 down，导致新源被整轮放弃 10 分钟**。`_status()` 有一条老规则：`last_success_ts is None → "down"`（不看 `consecutive_failures`）。本轮新增的 `is_down()` 直接复用它，而 `ddx_streak._paused()` 就靠它跳过源。触发条件：进程重启（Render Free 频繁休眠/唤醒）后第一次请求 `ddx` 时，东财抖一次 → `consecutive_failures=1` 但 `last_success_ts is None` → `is_down()` 立刻 True → `_paused("ddx_em")` True → **之后 600s 内所有候选股都不再试 ddx_em**。与本文件 `DOWN_THRESHOLD=3`、注释「前 1~2 次视为瞬时抖动 → degraded」的设计意图正相反。后果：冷启动窗口内资金连增主力口径被永久切到 akshare/新浪（口径不同，`sources` 里能看到但用户看不出差别）；`data_health.snapshot()` 同时把该源报 `down`，监控噪声。 | `is_down()` 不要走 `_status()`，直接判阈值：`return entry is not None and entry["consecutive_failures"] >= DOWN_THRESHOLD`（`consecutive_failures` 只在 `record_failure` 递增、`record_success` 清零，语义正好）。补一条测试：`record_failure` 一次后 `is_down(...) is False`，连续 3 次后为 True。 |
| A-03 | `src/analysis/ddx_streak.py:271-277`、`:306` | A | P2 | **冷却机制非对称，且抖动一次就再锁 10 分钟**。① `_paused()` 的 600s 是固定常量，冷却到期后只给**一次**探测机会，该次失败（哪怕上游瞬时 502）→ `last_attempt_ts` 刷新 → 再锁 600s；② `_fetch_sina_history`（第 306 行）**完全没有 `_paused` 保护**，与 em/akshare 不对称——它是历史兜底主力，反而最不受保护。触发条件：上游间歇性抖动。后果：一个可用源被"过度惩罚"（可用却 10 分钟不用），另一个源被"过度使用"（不可用却每轮都打），12 只候选的最坏耗时不可控。 | ① 冷却期改为指数退避（600 → 1200 → 2400 封顶）或冷却到期后连续 2 次成功才解除；② `_fetch_sina_history` 也走 `_paused("ddx_sina_hist")`，但要保证"三源全 paused"时抛 `None`（不要把快照当连增）；③ 在 `build_ddx_streak` 返回值里加 `"skipped": {"ddx_em": n}`，前端/日志可看出"是被跳过而不是没数据"。 |
| A-04 | `web/routes/market.py:640` | A | P2 | **公开端点 + 参数空间 × 上游放大 ≈ 36 倍，无任何限流**。`cache_key = f"market:ddx_streak:{limit}:{min_days}"`，`limit∈[1,30]`、`min_days∈[1,10]` → 300 个互不命中的缓存键；每个 miss 触发「候选池抓取（含 3 次东财排行，不足时再打同花顺）+ 12 只股票 × 最多 3 个上游请求」。触发条件：遍历 `?limit=1..30&min_days=1..10`。后果：短时间内数百次东财/新浪请求，`em_get` 的 0.3s 全局限速会让**全站所有东财接口**排队；被上游按 IP 限流后，行情/板块/龙虎榜一起降级。`_DDX_FAIL_COOLDOWN` 只在"全失败"时生效，帮不上忙。 | ① 挂 `@limiter.limit("10/minute")`；② `limit` 归一化到固定档（如 `min(limit, 12)` 且只保留 5/10/12 三档）或直接不把 `limit` 放进缓存键（拉满 30 条后切片，成本一样）；③ `_DDX_TTL=300` 保持，但把 `min_days` 约束到 `{2,3,5}`。 |
| A-05 | `web/routes/strategy.py:338`、`:392`、`:411` + `web/routes/market.py:629` | A/E | **P1** | **重型端点无限流（成本放大面）**。三个 `scan/*` 是 `@router.get` 裸挂，既无 `require_admin` 也无 `@limiter.limit`，每次请求触发 akshare 全市场抓取；`/api/market/ddx_streak` 同样公开。触发条件：任何人循环请求（无需 token）。后果：与 E-01 叠加 → 默认线程池被占满 → 全站 `asyncio.to_thread` 排队，Render Free 单 worker 直接假死。**注意：这不是"漏挂鉴权"**——产品裁决扫描为**刻意保留的匿名功能**（前端 `index.html:5428-5446` 的 `runScan()` 匿名直调，82 个端点已鉴权、扫描是唯一刻意公开的重成本面），因此**不加 `require_admin`**，资源风险在资源层解决。 | **按产品裁决（2026-11-17）：不改鉴权，改为资源约束**。① 三个 `scan/*` 加 `@limiter.limit("6/minute")`；② 加进程内单飞：模块级 `_scan_inflight: dict[str, float]`，同一 operation 在 15s 内重复请求直接返回 503 + `detail={"code":"SCAN_DATA_UNAVAILABLE","cooldown":true,"retry_after_s":<剩余>}`（复用 `_scan_unavailable()` 并加 `cooldown` 字段，前端已有失败态渲染）；③ `/api/market/ddx_streak` 按 A-04 处理（`@limiter.limit("10/minute")` + `limit` 档位归一化），它是只读且有 300s 缓存，不需要单飞。 |
| A-06 | `src/core/data_source_v2.py:1523-1548` | A | **P1**（已实测确认） | **两融「融券卖出」把"股数"当"金额"输出，前端同排混排**。本轮把 `short_sell` 从 `RQYE`（融券余额，元）改成 `RQMCL`。**已用真实响应闭环（600519，2026-11-16）**：`RZYE=14,788,586,340`(融资余额/元)、`RZMRE=272,139,009`(融资买入/元)、`RQYE=322,111,545`(融券余额/元)、`RQYL=221,550`(**股**)、`RQMCL=2,900`(**股**)、`RQCHL=2,700`(融券偿还/**股**)、`RZRQYE=15,110,697,885`(总余额/元)、`SPJ=1453.9`；**算术闭环 `RQYE/RQYL = 322,111,545/221,550 = 1453.9 = SPJ`** 证明 `RQYL/RQMCL` 是股、`RQYE` 是元；且该报表**没有任何融券"额"字段**（无 `RQJE/RQMJE`）。触发条件：任意个股两融面板。后果：`web/static/index.html:8202-8204` 表头「融券卖出」与同行「融资余额 147.9 亿 / 融券余额 3.2 亿」并列，实际输出的是 `2900`（股）→ 用户读成 2900 元，与真值 2900×1453.9≈421.6 万元差 3 个数量级，据此误判做空压力。 | 采用方案②（报表无金额字段，方案①不可行）：① `short_sell` 保留"金额"语义但无数据 → 输出 `None`；② 新增 `"short_sell_volume": _safe_float(item.get("RQMCL", 0))`（股）、可选 `"short_repay_volume": RQCHL`；③ `MarginTradingItem`（`market.py:785-792`）加 `short_sell: float | None` 与 `short_sell_volume: float | None`；④ 前端 `index.html:8199-8209` 表头改「融券卖出量(股)」并改 `mapItem` 取 `short_sell_volume`，`default` 示例数据同步改成股数；⑤ 测试 `tests/test_data_source_v2_eastmoney_reports.py:45-53` 的 `result ==` 断言需同步加 `short_sell_volume` 并确认 `short_sell is None`。 |
| A-07 | `web/routes/market.py:481-482`、`:1013`、`:972`、`:991` | A | P2（存量） | **空数据仍报 80 分、`degraded=false`（`b870f3e` 同类路径未覆盖完）**。`_build_meta()` 默认 `quality=80`（`:45`）且 `degraded = quality < 70`（`:54`）。以下路径"取到空列表也走默认 80 分"：`/hot_stocks`（东财+新浪三榜全空 → 三个空数组、80 分）、`/hot_stocks_signal`（同花顺 + 东财兜底都空 → 空数组、80 分）、`/news`、`/global_news`（空列表、80 分）。对照 `b870f3e` 已修的 `/margin`、`/dragon_tiger`、`/block_trades`、`/shareholder`（都显式 `quality_score=0.0`），说明本轮修复是按端点逐个打的补丁，没有统一口径。触发条件：对应上游临时不可用。后果：前端拿到 `degraded=false` + 空列表，只能渲染「暂无数据」，无法区分"确实没有"与"抓取失败"——正是本轮要消灭的模式。 | 在 `web/routes/market.py` 增加统一工具：`def _rows_quality(rows, base=80.0): return base if rows else 0.0`，上述 4 处改为 `_build_meta(src, cached=False, quality_score=_rows_quality(result))`；再补一条参数化测试覆盖"空列表 ⇒ degraded 为真"。 |
| A-08 | `web/routes/market.py:1514-1550` + `src/analysis/market_assessment.py:100-122`、`:160-172` | A | P2 | **四维全缺时温度计仍输出「中性 50 分」且 `quality=80`（非 degraded）**。`_calc_*_score` 对缺失一律回 50 并只写 `note`；`_calc_thermometer` 用固定权重加总 → 全缺时恰好 50.0 → `level=中性`、`action=观望,等待方向`；路由侧 `_build_meta("aggregator", cached=False)` 恒 80 分。触发条件：指数/板块/情绪/北向同时不可达（冷启动 + 上游抖动，工作中常见）。后果：用户看到"市场震荡，观望为主"的投资建议——这不是"缺数据"，是**用缺失数据生成了结论**，比空态更有害。次生问题：`_calc_capital_flow_score` 在北向缺失时把 nb_score 固定 50 再与 sector_score 各占一半（`:112`），使「资金」维度被系统性拉向 50，而前端 `web/static/index.html:6157` 直接显示 `components.capital_flow`。 | ① `market_assessment` 顶层返回 `"data_quality": {"available_dims": n, "total_dims": 4}`，各 component 增加 `"available": bool`；② 路由按可用维度数降分（`n==0 → quality 0.0`、`n<=2 → 40.0`），并在 `extra` 里给 `degraded_reason`；③ 北向缺失时 `capital_score` 改为 `score = sector_score`（或权重归一化为 nb 0 / sector 1），**同时**改 `tests/test_market_assessment.py:133-139`（现断言 `score==55`，"板块 20 亿 + 北向缺失"会变成 60）与 `:109-114`（现断言全缺时 `score==50`，改后仍为 50 可保留）。 |
| A-09 | `web/routes/market.py:890/910/930` + `src/core/data_source_v2.py:1532`、`:1577`、`:1629` | A | P2 | **`code` 未校验即 f-string 拼进上游 filter（查询注入）**。`@router.get("/margin") code: str = Query("")` → `f'(SECURITY_CODE="{stock_code}")'` / `f'(SCODE="{stock_code}")'`。同文件 `:687` 的 `/stock_flow_streak` 已用 `pattern=r"^\d{6}$"`，说明项目里有正确写法，只是这三处没对齐。触发条件：`GET /api/market/margin?code=600519")+OR+(1=1`（URL 编码后）。后果：可改写东财报表的过滤条件（越权读取不属于该 code 的行），异常被 `except Exception` 吞成"无数据"，同时把接口变成"任意东财报表探测器"。属 SSRF 之外的第二类注入面。 | ① 三个路由的 `code` 统一改 `Query(..., min_length=6, max_length=6, pattern=r"^\d{6}$")`（空串语义改为 422，前端需确认不会传空——当前前端有 `code ? ... : '600519'` 兜底）；② `data_source_v2` 侧再加一道 `if not re.fullmatch(r"\d{6}", stock_code): return {}`，纵深防御。 |
| A-10 | `src/core/data_source_v2.py:1145`、`:1155-1156` | A/E | P3 | 龙虎榜单次请求后**只取 `pageSize=100` 再按最新交易日过滤 + 截 30 条**。**实测（近 8 个交易日单日明细行数：11-16=70、11-13=69、11-12=69、11-11=85、11-10=58、11-09=67、11-06=79、11-05=3）→ 最大 85 行，`pageSize=100` 当前不会截断，但余量只剩 15%**。次要问题仍在：多上榜原因会把同一只股票拆成多行（`EXPLANATION` 不同），列表里会出现重复股票；涨停 100+ 家的极端日（如指数调仓、题材爆发）到 100 行上界就会被截断且截断位置不可控。触发条件：极端活跃日。后果：龙虎榜"少几只票"或同一股票重复出现，用户以为刷屏。非造假，属覆盖度不足。 | 把 `pageSize` 提到 200（**仍是单次请求**，不引入本轮刚消除的第二次往返）；按 `SECURITY_CODE` 去重后取前 30（优先保留 `TOTAL_NET` 绝对值最大的一条原因）；`_meta` 里给 `raw_count` 与 `dedup_count` 便于排查是否触顶。 |
| A-11 | `src/core/data_source_v2.py:1582` | A | P3 | `data.get("result", {}).get("data", [])`：上游返回 `"result": null` 时 `.get` 落在 `None` 上抛 `AttributeError`，被 `except` 吞掉后走空兜底——**静默**且日志里是"NoneType has no attribute get"这种对排障无信息量的报错。同文件 `:2626` 对 `res is None` 已做显式处理，风格不一致。 | 统一写 `((data.get("result") or {}).get("data")) or []`（本文件 `:1126`、`:2490` 已是这种写法，照抄即可）。 |
| A-12 | `src/core/data_source_v2.py:1603-1617` | A | P3 | `_get_block_trades_fallback()` 先 `get_realtime_quote_tencent([code])` 取行情、判 `price<=0`，然后无条件 `return []`——一整段死逻辑。触发条件：每次大宗交易失败。后果：白白多打一次腾讯请求（增加延迟与外部依赖），代码读者会以为兜底有内容。 | 整个函数体改为 `logger.info(...)` + `return []`，或直接删除并在调用处 `return []`。 |
| A-13 | `src/core/data_source_v2.py:1643-1646` + `web/routes/market.py:811` | A | P3 | 股东户数环比：`TOTAL_NUM_RATIO` 为 None 且只有 1 期数据时 `prev_count=0` → `change_pct = 0.0` → 输出「环比 0.00%」。触发条件：次新股 / 上游只回 1 期。后果：把"算不出环比"显示成"环比无变化"。`ShareholderItem.change_pct: float` 也没有 null 位。 | `change_pct` 改为 `float | None`，无上期时输出 `None`；前端 `_pctOrDash` 已支持 `—`。 |
| A-14 | `src/core/data_source_v2.py:795-821` | A | P3 | 同花顺版 `get_northbound_flow()` 已成为**无调用方的死代码**（`grep` 确认生产代码零引用，仅测试间接覆盖），且内部仍是 `_safe_float(..., 0)` 老口径——一旦有人复用它，"只有合计"会立刻变成沪深 0（重演 A-01）。 | 删除，或改名 `_legacy_northbound_flow_ths` 并在函数头写明"输出可能为伪造 0，勿在生产调用"，同时加一条 `ast`/`grep` 式测试禁止引用。 |
| A-15 | `src/core/data_source_v2.py:1268-1336`（`_get_sector_ranking_sina_fund`） | A | P2 | **新增兜底把板块最坏路径从 ~11s 拉到 ~17s，且被多个端点共享**。东财 2 跳（探针为 False 时 `em_get` 立即抛，代价小）→ 新浪板块资金 `fenlei=2`(6s) → `fenlei=0`(6s) → 旧新浪 `newFLJK`(5s)，全部串行。`get_sector_ranking` 被 `/sector_ranking`、`/sector_flow`、`/heatmap`、`/thermometer`、`/capital-flow`、`/assessment` 六处共用，`ds2` 只给了 30s 缓存、无 single-flight。触发条件：东财不可用 + 新浪板块资金首次失败（如 fenlei 参数再变）。后果：工作台 `load()` 用 `Promise.all` 等 7 个接口且 `fetchJSON` **无超时**（`web/static/js/workbench.js:93-107`），首屏被单个板块接口拖到 17s 以上；并发两个请求还会把上游请求数翻倍。 | ① `fenlei` 两个候选改成"先试 2，失败记录到 data_health，本次不再试 0"（用 `seconds_since_attempt` 做同类冷却）；② 给 `get_sector_ranking` 加进程内 single-flight（`threading.Lock` + 最近结果），避免多端点并发重复抓；③ 路由侧对 `/sector_ranking` 加 `asyncio.wait_for(..., timeout=8)`，超时返回旧缓存 + `stale=True`（SWR 机制已有）。 |
| A-16 | `src/core/data_source_v2.py:1592` | A | P2 | **大宗交易折溢率把小数比率当百分数显示，数值差 100 倍**。`"premium_pct": _safe_float(item.get("PREMIUM_RATIO", 0))` 直接透传。**实测（300003 乐普医疗，2026-11-16）**：`DEAL_PRICE=9.92`、`PRE_CLOSE_PRICE=12.09`、`CLOSE_PRICE=12.02`；`PREMIUM_RATIO=-0.174708818636` = `9.92/12.02-1` → **是小数比率，基准为收盘价**；`DISCOUNT_RATIO=-17.948717948718` = `9.92/12.09-1` → **是百分数，基准为昨收**。触发条件：任意大宗交易记录（100% 命中，不是边界）。后果：前端 `index.html:8214-8216`「折溢率」列显示「-0.17%」，真值「-17.47%」——**差 100 倍**，用户会把深度折价的大宗交易读成几乎平价成交，直接影响对减持压力的判断。测试 `tests/test_data_source_v2_eastmoney_reports.py:64`（fixture `PREMIUM_RATIO=-1.5`）与 `:74`（断言 `== -1.5`）用百分数口味做 fixture，正好掩盖了这个 bug，属于"fixture 与真实响应口径不一致"。 | ① `"premium_pct": round(_safe_float(item.get("PREMIUM_RATIO", 0)) * 100, 2)`，并在代码注释写明「基准 = 收盘价 CLOSE_PRICE，不是昨收」；② **不要**顺手换成 `DISCOUNT_RATIO`——它与 `PREMIUM_RATIO` 口径不同（一个比收盘价、一个比昨收），前端如需「相对昨收折价率」应另开字段 `discount_pct` 并在表头标注基准；③ 把测试 fixture 改成真实值（`PREMIUM_RATIO=-0.1747` → 断言 `premium_pct == -17.47`），并在 fixture 里保留 `PRE_CLOSE_PRICE/CLOSE_PRICE`，为 §6 建议的「单位/口径自洽断言」留好抓手；④ 前端表头「折溢率」建议加脚注「基准：收盘价」；**同一张表顺带修单位**：`DEAL_VOLUME` 实测为**股**（`DEAL_AMT/DEAL_PRICE = 13,137,100/9.92 = 1,324,300 = DEAL_VOLUME`），表头「成交量」加「(股)」或 `Math.round(v/100)` 转手并标「(手)」，见 §5.1-3。 |

### 2.2 B 组 · LLM 协议层

| 编号 | 文件:行号 | 组 | 级别 | 问题描述（触发条件 → 可观察后果） | 修复建议（可直接落地） |
|------|-----------|----|------|-----------------------------------|------------------------|
| B-01 | `src/core/llm_router.py:945-951` | B | P2 | **system 提示词在 Gemini 协议下退化为 user 段**。`_call_gemini` 的角色映射只有两条分支：`assistant → model`，其余（含 `system`）→ `user`。本仓库大量模板用 `{"role":"system"}`（`src/core/ai_service.py:1052/1128/1166/1201/1366/1490`，另 `news_aggregator.py:360`、`multi_agent_fund.py:533`）。Gemini 原生协议要求这类内容走顶层 `systemInstruction`，否则变成"用户先说了两句"。触发条件：`LY77625_PROTOCOL=gemini` 且模板带 system。后果：**不报错**，只是人格/输出格式约束弱化——`json_mode=true` 时更容易跑出非 JSON，解析失败会表现为"偶尔解析失败"，很难归因到协议开关。 | 在 `_call_gemini` 前置分流：把 `role=="system"` 的 content 聚合成 `payload["systemInstruction"] = {"parts":[{"text": "\n".join(systems)}]}`，`contents` 只保留 user/model；补一条测试：传 system+user 时断言 `systemInstruction` 存在且 `contents` 长度为 1。 |
| B-02 | `src/core/llm_router.py:568` | B | P2 | **协议开关拼错时静默走 OpenAI 兼容层**。判定式为 `os.getenv("LY77625_PROTOCOL","openai").strip().lower() == "gemini"`，其余任何取值（`gemini-pro`、`Gemini2`、`gemini ` 之类）都静默落到 OpenAI 层——而按本次提交说明，OpenAI 兼容层"频繁 502"，即**这个开关的全部意义就是绕开它**。且构造 provider 时无任何日志记录生效协议。触发条件：Render 控制台手工填错一个字符（`render.yaml` 的 env 不生效，只能手工填，误填概率不低）。后果：表现为"ly77625 能用但一直报错"，日志里只有 OpenAI 层 502，没有任何线索指向"协议没生效"。 | ① 非法值打 WARN：`v = os.getenv(...); if v and v.strip().lower() != "gemini": logger.warning("[llm] LY77625_PROTOCOL=%r 不是 gemini，按 openai 兼容层处理", v)`；② provider 构造后 `logger.info(f"[llm] ly77625 protocol={'gemini' if ly_gemini else 'openai'} base={base_url}")`，并把 `protocol` 写进 `provider_status()` 输出，运维页可直接看到。 |
| B-03 | `src/core/llm_router.py:965` | B | P3 | Gemini 分支用 `provider.api_key` 而非 `provider.current_api_key`（`:105-112` 的多 key 轮换）。ly77625 目前只传单 key（`:576-578` 无 `api_keys`），**当前无影响**；但原生 gemini provider（`:356-363`）一旦改为多 key，就会静默只用第一个 key，轮换/限额分摊失效。 | 改成 `provider.current_api_key`（与 `:900` 的 OpenAI 分支保持一致，一行改动）。 |
| B-04 | `src/core/llm_router.py:970-977` | B | P3 | 安全拦截与真空响应无法区分：`candidates` 为空或首项无 `content`（`finishReason=SAFETY/RECITATION`）时走到 `raise ValueError("返回空内容")`，在 `chat()` 里按普通失败计入 `record_failure()` → 3 次即熔断 30s。触发条件：某个用户输入（或某段新闻原文）触发上游安全策略。后果：**因为一个用户的输入把整条 Provider 熔断**，其他用户请求被迫降级/变慢。 | 读 `data.get("promptFeedback", {}).get("blockReason")` 与 `candidates[0].get("finishReason")`，命中则抛专用异常（如 `LLMContentBlocked`）并在 `chat()` 里**不**调用 `record_failure()`、不计入 `fallback` 熔断计数，只记 info 日志。 |
| B-05 | `src/core/llm_router.py:979-984` | B | P3 | Gemini 分支返回的 `LLMResponse` 不带 `usage`（OpenAI 分支 `:918` 有），`usage={}`。后果：用量/成本统计对走 Gemini 协议的 provider 恒为 0，`_recorder` 的统计页看不出这类调用。 | 映射 `usageMetadata`：`usage={"prompt_tokens": u.get("promptTokenCount",0), "completion_tokens": u.get("candidatesTokenCount",0), "total_tokens": u.get("totalTokenCount",0)}`。 |

### 2.3 E 组 · 超时与失败语义

| 编号 | 文件:行号 | 组 | 级别 | 问题描述（触发条件 → 可观察后果） | 修复建议（可直接落地） |
|------|-----------|----|------|-----------------------------------|------------------------|
| E-01 | `web/routes/strategy.py:330-335` | E | **P1** | **`asyncio.wait_for` 只取消 await，不取消线程里正在跑的阻塞请求**。`await asyncio.wait_for(asyncio.to_thread(fn), timeout=15)` 超时后协程返回 503，但 akshare 的那次 HTTP 仍在**默认 executor**（`ThreadPoolExecutor(min(32, cpu+4))`）的线程里继续跑到自己的 socket 超时（akshare 东财接口**不设超时**，可能无限）。触发条件：上游挂起 + 用户重试（前端扫描按钮可反复点；`test_scan_failure.py:48-62` 自己就演示了"返回 503 但线程还在跑"）。后果：每次超时泄漏一个长期占用线程；累计若干次后默认 executor 打满，**全站所有 `asyncio.to_thread`（行情、详情、回测、LLM 之外的数据接口）一起排队**，Render Free 单 worker 无法自愈，只能重启。这是"超时保护"反而制造雪崩的典型。 | ① 扫描类用**专用受限 executor**：模块级 `_scan_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scan")`，`loop.run_in_executor(_scan_pool, fn)` + `wait_for`，把爆炸半径限制在 2 个线程；② 加"半开熔断"：模块级 `_scan_last_timeout`，超时后 60s 内直接返回 503（`detail` 加 `cooldown: true`），不再起新线程；③ 无法真正 kill 线程的现实约束写进注释，避免后来人误以为 `wait_for` 能释放上游连接。 |
| E-02 | `web/routes/strategy.py:386-389`、`:405-408`、`:426-429` | E | P2 | `_run_scan` 抛出的 `HTTPException(503)` 被外层 `except Exception` 二次捕获并 `logger.exception(...)` 重新包装。语义最终没变（都是 503 + 同一 `code`），但**每次正常超时都会打一条完整堆栈的 "exception" 日志**。触发条件：任何一次 15s 超时。后果：线上日志里超时看起来像代码缺陷（`logger.exception` 的语义是"未预期异常"），排障时会把"上游挂起"误判成"扫描逻辑写错了"，也会稀释真实异常的告警。 | 三个 `except Exception:` 前各插一行 `except HTTPException: raise`（或用 `except HTTPException: raise` / `except Exception:` 双分支），让超时 503 原样透传、不进 `logger.exception`。 |
| E-03 | `src/core/data_source_v2.py:1141-1149`、`:1150-1156` | E | P3 | 龙虎榜"单次请求"改动的**超时预算**：`em_get` 固定 `timeout=5`（`:332`），单次请求 + 全局限速等待（`_EM_MIN_INTERVAL=0.3s` + `random(0,0.5)`）最大约 5.8s，相比原两次往返（~11.6s）确实减半，**改造方向正确**；但这条 5s 是硬编码在 `em_get` 里的，路由侧 `/dragon_tiger` 无 `wait_for`，`_cache_set(..., 60)` 只缓存成功结果。后端最坏 5.8s，前端 `api()` 默认超时需 > 6s（待核实前端 `api()` 的 timeout 常量）。 | 建议把 `em_get` 的 `timeout` 参数化（默认 5s），龙虎榜用 6~8s 并显式注释预算来源；或在 `/dragon_tiger` 外层加 `wait_for(6.5)` 返回 200 + 降级 meta（保持既有"200 + degraded"契约，避免新增 503 面）。 |
| E-04 | 全 `web/routes/market.py` vs `web/routes/strategy.py` | E | P3 | **"上游不可用"有两套契约**：market 侧一律 `200 + quality=0/degraded` 空 body（`/margin:901`、`/dragon_tiger:882`、`/block_trades:922`、`/shareholder:941`、`/research_reports:864`），strategy 侧一律 `503 + detail.code`（`/search:1231` 是 market 内唯一的 503）。触发条件：前端要判断"缺数据"。后果：每个面板都得写两套判断，容易漏（`workbench.js` 只消费了 `sector_flow` 的 `_meta`）。 | 在 `docs/` 固化契约：**"用户主动触发的重计算 = 503（可区分重试）；页面展示型只读数据 = 200 + degraded"**。market 侧四个报表端点的空态补 `extra={"degraded_reason": "上游报表不可用"}`，前端统一按 `_meta.degraded` 渲染。 |

#### 关于"某端点该 503 却 500、且有测试锁死 500"

**明确结论：本轮范围内不存在锁死 500 的断言，可以自由调整 5xx 语义，无阻塞性测试。**
依据：全库 `grep -rn "status_code == 500\|assert.*500"` 命中项均为无关断言（`tests/test_metrics.py:75` 是 metrics 计数桶、`tests/test_risk_manager.py:231` 是金额）。扫描三端点的断言全部是 **503**：`tests/test_scan_failure.py:20`、`:32`、`:44`、`:61`。market 侧 `tests/test_market_routes.py:247` 断言的是数据值（`sh_net_inflow == 50000000.0`），改 A-01 时不受影响（该用例的 mock 里 `sh_sz_split_available` 缺省、走 `True` 分支）。

---

## 3. 已验证合规项（查过且确认没问题）

### 3.1 数据诚实性（红线）

1. **`None` 语义在宽表链路已打通**：`get_market_wide_stats`（`data_source_v2.py:2594-2674`）四项默认 `None`、只有真取到才写值；`MarketWideStatsResponse`（`market.py:1426-1432`）四个字段全 `| None`；`get_market_wide_stats` 端点（`:1497`、`:1508`）在四项全 None 时降为 `quality=0.0` → `degraded=True`。前端 `_numOrDash` 能识别 null。
2. **四个报表端点的空态已改为不造假**：`_get_margin_trading_fallback`（`:1555-1563`）、`_get_shareholder_count_fallback`（`:1658-1665`）、`_get_block_trades_fallback`（`:1603-1617`）、`_get_dragon_tiger_sina`（`:1180-1187`）全部返回空并写明"no fabrication"；历史上"用涨幅榜冒充龙虎榜""按市值分档估股东户数""按 1.2% 比例估融资余额"三处造假已彻底移除（用 `git show 0ae5b7a:` 对照确认）。
3. **板块资金全 0 的"假健康"已被拦住**：`_flow_field_quality` / `_flow_fields_quality`（`market.py:74-119`）在字段全 0 或完整度 < 60% 时把质量分压到 0/60 → `degraded=True`；`/sector_flow`、`/sector_ranking` 都接了。
4. **新浪板块兜底新增了 `unavailable_fields` 说明**：`_sina_missing_flow_fields`（`market.py:1021-1036`）把新浪不提供的超大/大/中/小单 8 个字段显式列进 `unavailable_fields` 并给 `source_note`，而不是留 0 让前端误读。
5. **来源标注不再谎报东财**：`_mark_source` / `get_last_source`（`data_source_v2.py:99-107`）覆盖了 `stock_ranking`（含分档键）、`sector_ranking`、`news`、`global_news`；`/sector_flow`、`/sector_ranking`、`/heatmap`、`/news`、`/global_news`、`/hot_stocks` 都改读实际来源；三个榜来源不一致时标 `mixed`、三源全失败标 `unavailable`（`market.py:459-464`），**没有硬编码 "eastmoney" 的残留**（该路径已核）。
6. **北向停更在四个消费点都如实标注**：`data_source_v2.NORTHBOUND_NET_NOTE`（`:2464-2467`）→ `/northbound` 空态与异常态都带 `extra.unavailable_reason`（`market.py:1458`、`:1476`）→ `market_assessment` 的 `capital_signal="北向净流入已停止披露"`（`:331`）→ AI Prompt「缺失(北向净流入自 2024-08 起停止披露)」（`ai_service.py:301`）。`ai_service.py:303-306` 对 `sh_sz_split_available is False` 有独立分支，不会把 0.0 当沪深净额写进 Prompt。
7. **情绪维度 counts 缺失不再崩也不再伪造**：`_calc_sentiment_score`（`market_assessment.py:138-139`）先判 `counts_available is False` 返回 50 + note；`get_market_sentiment` 指数代理路径三个 count 显式 `None`（`:2448-2452`）。

### 3.2 安全

8. **SSRF 面清白**：`data_source_v2.py` / `ddx_streak.py` / `market.py` 内所有外部 URL 都是**模块级硬编码常量**（含新增的 `_SSE_MARGIN_URL`、`_SZSE_MARGIN_URL`、`_EM_FLOW_URL`、`_SINA_FLOW_URL`、`_SINA_HIST_URL`），没有任何用户可控字符串进入 scheme/host 位置；用户参数只进 query（`code`/`page`/`limit`），未见 URL 拼接注入（`em_get` 用 `params=` 而非手拼）。
9. **密钥不落日志**：`_sanitize_error`（`llm_router.py:1081-1087`）会剔除 `api_key` 与所有 `api_keys` 并截断 200 字符；Gemini 分支把 key 放 header（`:963-966`）而非 URL query，注释也写明了原因（access log 泄漏）。
10. **`require_admin` 无遗漏**（本轮新增的重计算端点）：`/strategy/compare-ma-cross`（`:586`）、`/factor-research`（`:479`）、`/optimize-ma-cross`（`:514`）都挂了 `Depends(require_admin)`；`main.py` 未见全局开关被改动。**注意**：三个 `scan/*` 与 `/market/ddx_streak` **按产品裁决是刻意保留的匿名面**（不是漏挂），但成本与放大效应过高，须在资源层补限流 + 单飞（见 A-04 / A-05）。
11. **无 SQL/命令注入面**：本模块无 SQL；`requests` 无 shell 调用。

### 3.3 LLM 五条硬约束（B 组专项）

12. **`_providers` 排序与 `priority` 一个字都没动**：diff 仅替换 `provider_type` 与 `base_url` 的取值表达式，`priority=95` 原样保留；`tests/test_llm_router.py:505-517` 的硬断言（`[0]=zhipu_glm(-1)`、`[1]=zhipu_glm_45(1)`、`[2]=cloudflare_ai(65)`、`[-1]=siliconflow(95)`）不涉及 ly77625，仍然成立。新增测试 `test_gemini_protocol_loading` 也显式断言 `ly.priority == 95`。
13. **没有新增 Provider**：只是给同一个 `ly77625` 换协议，链长度与名字集合不变。
14. **`standard` 档仍是运行时全链**：`_routes_for_tier` 的 `if spec.is_dynamic: return [(p, None) for p in self._providers]`（`:633-635`）不在 diff 内，未被触碰。
15. **路由缓存 key 含 tier**：`_make_key`（`:208-228`）里 `"tier": tier` 仍在，未被触碰。
16. **`route is None` 分支与旧超时公式保留**：`_hop_timeout` → `resolve_hop_timeout`（`:674-683`）不在 diff 内；`_resolve_budget` 的旁路分支（`:667-671`）也未改。
17. **deep 预算 60s ≤ 前端 90s**：`llm_task_tier.py:71` `budget_seconds=60.0`；ly77625 在 deep 档的 `timeout_cap=20`（`llm_task_tier.py:75`），未被本轮改动。
18. **协议分支不影响"流式/非流式"**：全库 LLM 调用均为非流式（`llm_router.py` 内唯一 `"stream": False` 在 `:1059` 的 OpenAI 兼容路径；`ai_service.py` 无 stream/SSE），因此不存在"Gemini 分支缺少流式实现"的隐患。`json_mode` 在 Gemini 分支映射为 `generationMimeType`（`:959-960`），有测试 `test_gemini_protocol_chat_uses_native_endpoint` 锁死。
19. **`thought` part 过滤正确**：`content = "".join(p.get("text","") for p in parts if not p.get("thought"))`（`:974`）——原实现只取 `parts[0]`，思考型模型会把思维链当答案；现在既排除 thought 段，又把多段正文拼全。有测试 `test_gemini_thought_parts_excluded` 覆盖。

### 3.4 失败语义与降级可识别性

20. **扫描 15s 上限已落地且前后端预算匹配**：`_SCAN_TIMEOUT_S=15`（`strategy.py:327`）< 前端 25s（`test_scan_failure.py:76` 锁死 `return 25000`）→ 用户能看到后端明确的 503 文案而不是笼统的客户端超时；三端点都有 `SCAN_DATA_UNAVAILABLE` + `retryable: true`，错误文案不含内部细节（`:314-321`）。
21. **`_run_scan` 的 503 不会被改成 500**：外层 `except Exception` 重新抛的仍是 `_scan_unavailable()`，`detail.code` 一致（实测断言 `test_scan_failure.py:32/44/61` 覆盖三种路径）。
22. **`_parallel_fetch` 有界超时语义正确**：`deadline=None` 时用 `wait(set(futures))` 等全部完成（有注释说明为何不能直接遍历 `futures.keys()`），超时分支会 `cancel()` 并记 None（`data_source_v2.py:110-146`）。
23. **东财探针失败不再永久生效**：`_EM_AVAILABLE=False` 只在 60s 内生效（`data_source_v2.py:160-168`），`_ensure_em_checked()` 现在真的返回结果（`:197-201`），修掉了"启动瞬间网络未就绪 → push2 一票否决到进程结束"的老 P0。
24. **`data_health` 状态机线程安全**：全部读写都在 `_lock` 内；`snapshot()` 会把未上报的源铺成 `unknown` 并单列 `unverified`，`unknown` 不计入 `overall`（`data_health.py:145-197`）——"没验过"没有被算成"正常"。
25. **`_cache` 有上限、`_thread_pool` 有 `atexit` 关闭**（`data_source_v2.py:55-65`），未发现无界缓存增长。

---

## 4. 功能缺口清单（代码/文档侧，不做联网标杆对标）

| # | 缺口 | 说明（现状 → 缺什么） | 用户价值 | 实现成本 | 优先级 |
|---|------|----------------------|---------|---------|--------|
| G-1 | **温度计/研判的"数据可用性"契约** | 现在只有各 component 的 `note` 自由文本；缺 `available: bool` 与顶层 `data_quality.available_dims`，前端只能靠"note 里是否包含'不可用'"这种字符串匹配（`workbench.js:136`、`:389` 正是这么写的），一改文案就失效 | 高 | 低（后端加字段 + 前端改判断） | **P1** |
| G-2 | **数据源"暂停/冷却"状态不可见** | `data_health.snapshot()` 有 `status/last_error`，但没有"因 down 被跳过到何时"；运维看到 down 无法判断"是否已停止重试、何时自愈"（`_paused` 的 600s 只存在于 `ddx_streak` 内部） | 中高（排障） | 低 | P2 |
| G-3 | **上游源开关 / 强制指定源** | 无法在运行时"只用新浪""禁用东财"来做故障隔离与 A/B；`_EM_AVAILABLE` 只有自动探针，无人工控制位 | 中（运维） | 低（env → 源白名单） | P2 |
| G-4 | **龙虎榜/两融/大宗的"数据时点"提示** | 三个报表都返回 `trade_date`，但 `_meta` 里没有"距今几个交易日"；周末/节假日用户看到 3 天前的龙虎榜没有提示（`market_wide_stats` 已有 `margin_trade_date`，其它没有） | 中 | 低 | P2 |
| G-5 | **资金连增候选池覆盖度** | 候选仅"涨幅 Top10 + 成交额 Top10 + 同花顺涨停池"，上限 12 只，接口不输出"候选池来源/大小上限"；用户会以为是全市场连增榜 | 中 | 低（`_meta` 加 `candidate_pool` 与 `sampled` 已在 payload 里，只需前端展示） | P2 |
| G-6 | **冷启动预热** | Render Free 频繁休眠；重启后首个工作台请求要等板块 8~17s、资金连增 15s，`Promise.all` 无超时 → 首屏要么很慢要么空。缺启动后后台 warm-up（thermometer/sector/wide_stats） | 高 | 中（`lifespan` 里起 1 个低优线程 + 失败静默） | P2 |
| G-7 | **`_meta` 契约文档** | 本轮新增 `available` / `sh_sz_split_available` / `unavailable_reason` / `sources` / `cooldown` / `field_completeness` 等 6 个字段，散落在各端点，没有一处集中说明"哪些字段是可依赖的降级信号" | 中（防后续误删） | 低 | P3 |

---

## 5. 待确认事项（不猜测，需实测/产品确认）

### 5.1 已实测闭环（v2，由主理人拉真实上游响应完成，证据可复核）

1. **`RQMCL` 的单位与语义** → **已闭环，见 A-06**。`RPTA_WEB_RZRQ_GGMX` / 600519 / 2026-11-16：`RZYE=14,788,586,340`、`RZMRE=272,139,009`、`RQYE=322,111,545`、`RQYL=221,550`、`RQMCL=2,900`、`RQCHL=2,700`、`RZRQYE=15,110,697,885`、`SPJ=1453.9`。**算术自洽：`RQYE/RQYL = 322,111,545/221,550 = 1453.9 = SPJ`** → `RQYL/RQMCL/RQCHL` 为**股**、`RQYE` 为**元**；报表**无任何融券"额"字段**，故只能走方案②（`short_sell=None` + `short_sell_volume`）。
2. **`PREMIUM_RATIO` 是否为百分数** → **已闭环，已单列为 A-16**。300003 / 2026-11-16：`PREMIUM_RATIO=-0.174708818636` = `9.92/12.02-1`（**小数、比收盘价**）、`DISCOUNT_RATIO=-17.948717948718` = `9.92/12.09-1`（**百分数、比昨收**）。当前代码直接透传 → 差 100 倍。
3. **`DEAL_VOLUME` 单位** → **已闭环：股**。`DEAL_AMT/DEAL_PRICE = 13,137,100/9.92 = 1,324,300 = DEAL_VOLUME`。前端表头「成交量」需加单位（股）或除 100 转手，建议与 A-16 一起改（同一张表）。
4. **`RPT_BILLBOARD_DAILYDETAILS` 单日行数** → **已闭环，见 A-10**。近 8 个交易日每日明细行数 58/67/69/69/70/79/85/3（截断），最大 85 < 100 → **`pageSize=100` 当前不截断，余量仅 15%**；措辞已从"可能被截断"改为"极端日风险"，修复建议保留提到 200（仍是单次请求）。
5. **扫描端点鉴权** → **已有产品裁决：不加 `require_admin`**（扫描是刻意保留的匿名核心功能，`index.html:5428-5446` 匿名直调；82 个端点已鉴权）。A-05 修复方案已改为「limiter + 进程内单飞」，资源风险在资源层解决。

### 5.2 仍待处理

6. **`LY77625_PROTOCOL=gemini` 的线上真实稳定性**：提交说明称"原生 Gemini 协议可稳定路由"，我未联网验证；建议在任何默认值调整前先 `probe(name="ly77625")` 打一发留档。
7. **前端 `api()` 的默认超时常量**（E-03 依赖）：`index.html` 的 `api()` 与 `workbench.js` 的 `fetchJSON` 策略不同（后者**无**超时），需前端组确认 `/dragon_tiger`、`/sector_ranking` 的实际容忍时间。

---

## 6. 结论

- 本轮增量的**主方向是对的**：北向停更如实标注、四张东财报表恢复真实数据、宽表缺失值改 None、来源标注不再谎报东财、扫描加 15s 上限、Gemini 协议开关未触碰任何 LLM 硬约束——这些都在代码里落实了，不是只写在提交信息里。
- 主要风险集中在两处**跨模块的"最后一公里"**：① 数据诚实性的修补是**逐端点打补丁**，聚合层（`market_assessment` → `/thermometer`、`/capital-flow`、`/assessment`）和若干空列表端点仍是"缺失也报 80 分"，并且 `sh_sz_split_available` 这个新 flag 在 `get_capital_flow_overview` 处被丢弃（A-01）；② 成本型端点的**资源边界**只在语义层（503）做了，没在资源层（线程/限流）做，`wait_for` 无法释放上游连接（E-01 + A-05 组合会放大成全站排队）。
- 未发现 P0 阻断级缺陷；本轮无回归性破坏（`_providers` 排序、tier 路由、缓存键、超时公式、扫描 503 断言全部保持一致）。
- **本轮 3 个"数据语义类"问题（A-06 融券卖出量当额、A-16 折溢率小数当百分数、以及 A-10/A-13 同族的"单位口径"问题）根因是同一类**：把上游字段的单位/口径当成理所当然，只按字段名猜含义，**没有做算术自洽校验**。而这三个案例恰恰都留有可直接验证的自洽等式——`RQYE / RQYL = SPJ` 能证明谁是元谁是股、`DEAL_AMT / DEAL_VOLUME = DEAL_PRICE` 能证明成交量是股还是手、`9.92 / 12.02 - 1 = PREMIUM_RATIO` 能当场区分小数与百分数——说明"自洽校验"在本项目不是奢侈品，是低成本高收益的必做项。
- **建议把「单位/口径自洽断言」作为后续新增/更换报表解析的准入要求**：① 每个新报表字段上线时，必须在注释或测试 fixture 里写出"用哪两个字段算出第三个"的等式（如 `净额 = 余额差`、`金额 = 价 × 量`）；② 在 `tests/test_data_source_v2_eastmoney_reports.py` 用真实响应样本（不是手写口味值）做 fixture，并断言等式成立；③ 前端表头一律带单位与基准（「融券卖出量(股)」「折溢率(基准:收盘价)」），避免单位错误被 UI 隐藏。
