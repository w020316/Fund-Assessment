# 第六轮质量评估 · 阶段二批次 A 修复记录（8 个 P1 全关闭）

**范围**：`561583b..HEAD`｜**提交**：8 个 fix + 1 个 docs｜**规模**：22 文件 / +2,119 −65
**回归**：**2925 passed / 1 skipped / 0 failed / 88.39s**（基线 2885，**+40 例**）
**CI**：`CI` job 绿（2m21s）｜**线上**：`version=449e781`，三项行为指纹全部命中

---

## 一、逐项闭环（四段式）

| # | 编号 | 提交 | 状态 |
|---|---|---|---|
| 1 | C-01 工作台假死 | `f768db1` | ✅ 已上线 |
| 2 | E-01 超时雪崩 | `57c2434` | ✅ 已上线 |
| 3 | A-06 融券卖出股当元 | `8f140d1` | ✅ 已上线 |
| 4 | A-16 溢价率差 100 倍 | `ef47905` | ✅ 已上线 |
| 5 | A-01 北向拆分伪造 0 | `8d5949e` | ✅ 已上线 |
| 6 | A-02 冷启动首败即 down | `5fa61e9` | ✅ 已上线 |
| 7 | A-05 重成本端点无边界（含 A-04 的 P2 部分） | `9f423e3` | ✅ 已上线 |
| 8 | M-01 定时循环失败语义矛盾 | `2fdf8d2` | ✅ 已合并（见 §四 待用户配置） |

### 1. C-01 · 工作台假死（`f768db1`）

- **原始描述**：`fetchJSON` 裸 fetch 无 AbortController/无超时；`load()` 用 `Promise.all([7 请求])`，上游任一 TCP 半挂起 → Promise 永不 settle → `state.loading` 在 `finally` 永不复位 → 刷新按钮永久置灰、60s 定时器每次静默 return。
- **技术方案**：① 每请求 `AbortController + setTimeout`，预算分级（`/api/review/board` 30s，其余 15s），超时 abort 归一为 `{ok:false,status:0}`，`finally` 清计时器防泄漏；② `Promise.all` → `Promise.allSettled`，各面板独立结算 + 失败占位；③ 页脚汇总「部分面板取数失败：X，其余数据已更新」；④ `finally` 仍复位 loading 与按钮态。
- **修复状态**：完成。
- **多场景验证**：5 条机制测试（按花括号配平截取 `fetchJSON`/`timeoutFor`/`load` 的**函数体**断言，非全文子串——直接针对 F-01 的缺陷形态）；`node --check` 通过；手工把 URL 改不可达域名 → 15s 后面板显示失败态、按钮恢复、其余面板照常刷新。

### 2. E-01 · 超时反而制造雪崩（`57c2434`）

- **原始描述**：`wait_for(to_thread(fn), 15)` 只取消 await 不 kill 线程；akshare 无 socket 超时 → 挂起线程堆积占满**默认** executor → 全站 `to_thread` 排队。加超时反而把局部慢放大成全站雪崩。
- **技术方案**：① 扫描迁到模块级 `ThreadPoolExecutor(max_workers=2, thread_name_prefix="scan")`，用 `run_in_executor`，爆炸半径钉死 2 线程；② 半开熔断 `_scan_last_timeout` + 60s 冷却，期间直接 503（`cooldown=True` + `retry_after_s`），到期自动放行一次重试；③ 三端点补 `except HTTPException: raise`，否则冷却 503 会被通用兜底改写、标记丢失；④ 注释写明 `wait_for` 无法 kill 线程的现实约束。
- **修复状态**：完成。
- **多场景验证**：`tests/test_scan_concurrency.py` 7 条行为测试——线程名带 `scan` 前缀（确实走专用池）／4 并发峰值 ≤2／超时后第二请求直接 503 且上游 `calls==[1]`（没起新任务）／冷却到期放行（半开）／三端点各跑一次。既有 4 条 503 断言（`test_scan_failure.py`）未改动，通过。

### 3. A-06 · 融券卖出「股」当「元」（`8f140d1`）

- **原始描述**：`short_sell = RQMCL`，与同排 `RZYE/RQYE/RZRQYE`（元）混排。
- **技术方案**：`short_sell → None`（金额语义上游不可得，诚实空值不填 0）；新增 `short_sell_volume = _opt_float(RQMCL)`（缺失即 None）；响应模型两字段均 `float | None`；前端表头改「融券卖出量(股)」，缺失渲染「—」。
- **修复状态**：完成，已上线。
- **多场景验证**：① 改写既有断言（见 §三）+ 断言 `short_sell is None`、`short_sell_volume == 2900`；② 单位自洽等式 `short_balance / 221550 ≈ 1453.9`（= 收盘价）；③ 字段缺失时两者皆 None 不是 0。

### 4. A-16 · 溢价率差 100 倍（`ef47905`）

- **原始描述**：`premium_pct = PREMIUM_RATIO`（小数 −0.1747）直接当百分数。
- **技术方案**：`round(PREMIUM_RATIO * 100, 2)`，缺失仍 None；注释写清「基准=当日收盘价，上游为小数」及与 `DISCOUNT_RATIO`（基准昨收、已是百分数）的差异；顺手标明 `DEAL_VOLUME` 单位是股并给出自洽等式。
- **修复状态**：完成，已上线。
- **多场景验证**：① 真实 fixture 断言 `premium_pct ≈ -17.47`；② 断言它等于 `(9.92/12.02-1)*100`；③ **反向断言**与按昨收算的 −17.95 相差 >0.4（防两字段互换）；④ `DEAL_AMT/DEAL_PRICE == DEAL_VOLUME` 自洽；⑤ 缺失时为 None 不落 0%。

### 5. A-01 · 北向沪深拆分伪造 0（`8d5949e`）

- **原始描述**：`sh/sz_net_inflow` 写死 `0.0` 且 `sh_sz_split_available` 在聚合层被丢弃；`NorthboundFlowItem` 又用 `_safe_float(None)` 把 None 回落成 0.0（**同链路第二处伪造**）。
- **技术方案**：三处同改——`data_source_v2` total-only 分支 sh/sz → None；`market_assessment` 新增 `nb_split_ok` 判空并透传 flag；`market.py` 模型改 `float | None`。前端已有 `nbSplitOk ? ... : '—'`，flag 到位即自动显示「—」。
- **修复状态**：完成，已上线。
- **多场景验证**：新增 3 条（total-only → sh/sz None + flag False；flag=True → 数值照旧，防「加了 flag 反而丢数据」；数据源层 total-only 保 None）。既有 `sh_net_inflow == 50000000.0` 用例**未改**，继续通过。

### 6. A-02 · 冷启动首败即判 down（`5fa61e9`）

- **原始描述**：`is_down()` 复用 `_status()` 的「从未成功即 down」老规则，不看 `consecutive_failures` → 进程重启后抖一次就整轮放弃源 600s。
- **技术方案**：`is_down()` 直接判 `consecutive_failures >= DOWN_THRESHOLD`；**展示语义不动**（前端徽标仍走 `_status()`），两套口径的差异写进 docstring 防后人合并回去。
- **修复状态**：完成。
- **多场景验证**：4 条——1 次失败 `is_down()` False 但 `snapshot().status` 仍 down（证明两口径已解耦）／3 次 True／`record_success` 后 False（能自愈，不永久熔断）／未上报源 False。既有 `test_first_failure_without_history_is_down` 未改。

### 7. A-05（+A-04 的 P2 部分）· 重成本端点无成本边界（`9f423e3`）

- **原始描述**：三个 `scan/*` 与 `/ddx_streak` 无限流；`limit` 直接进缓存键（30 档 × 10 档）。
- **决策记录**：**不加 `require_admin`**——前端 `runScan()` 匿名直调是匿名面核心功能，加鉴权会 401 打断主路径。资源风险在资源层解决。
- **技术方案**：① 四端点 `@limiter.limit("6/minute")`；② 扫描进程内单飞（`_scan_inflight`，同键在飞直接 503 `SCAN_IN_PROGRESS` + `busy=True`，不排队不起第二个上游）；③ `_normalize_ddx_limit()` 归一到 {5,10,12} 再进缓存键（>12 封顶，因候选池上限 12）。
- **修复状态**：完成，已上线。
- **多场景验证**：① 连打 8 次 → `200×6 + 429×2`（**线上实测**）；② 扫描仍匿名可用（回归保护）；③ 同键在飞第二个请求 503 且上游只调 1 次；④ 单飞键释放不永久 busy。

### 8. M-01 · 定时循环失败语义矛盾（`2fdf8d2`）

- **原始描述**：`Guard secrets` 用 `::warning::` + 写 pred_failures 让 run 变红，7 个执行步各自 `[ -z ... ] && exit 0` 静默成功 → 同一次运行「一步说失败、其余说成功」，红绿取决于谁先跑完。
- **技术方案**：单一判定点——guard 输出 job 级 `secrets_ready=false/true` 并 `exit 1`（`::error::` 明确写「这是配置缺失信号，不是代码故障」+ 修复入口）；7 个执行步 `if:` 全部加 `secrets_ready == 'true'`（缺失即 `skipped`，**skipped ≠ success**）；删除各步的静默 `exit 0`。
- **修复状态**：**代码侧完成；配置侧待用户操作**（见 §四）。
- **多场景验证**：新增 `tests/test_prediction_workflow.py` 6 条——YAML 可解析／guard 是唯一判定点／**每个**执行步 `if` 都含 secrets_ready（≥7 步，防新加步骤漏网）／全文件不再出现静默 `exit 0`。

---

## 二、回归与线上验证

| 项 | 结果 |
|---|---|
| 本机全量回归（主理人独立复跑，不采信自报） | **2925 passed / 1 skipped / 0 failed / 88.39s** |
| 测试增量 | 2885 → 2925（**+40**），新增 2 个测试文件（`test_scan_concurrency.py`、`test_prediction_workflow.py`） |
| CI | `CI` job success（2m21s） |
| 线上 `version` | **449e781**（= HEAD，指纹一致） |
| 线上 `status/degraded` | `ok` / `false` |

### 行为指纹复验（不用版本号接口，全部用「新代码引入的确定性行为」）

| 指纹 | 修复前（实测留档） | 修复后（实测） | 判定 |
|---|---|---|---|
| `GET /api/market/margin?code=600519` | `short_sell: 2900.0`（无 volume 字段） | **`short_sell: null` + `short_sell_volume: 2900.0`** | ✅ |
| `GET /api/market/block_trades?code=300003` | `premium_pct: -0.174708818636` | **`premium_pct: -17.47`** | ✅ |
| `GET /api/market/ddx_streak?limit=5` ×8 | 全 200（无限流） | **200×6 → 429, 429** | ✅ |

---

## 三、断言变更台账（唯一批准的两处，均**增强**非削弱）

| 文件:用例 | 改前 | 改后 | 强度判定 |
|---|---|---|---|
| `tests/test_data_source_v2_eastmoney_reports.py::test_margin_trading_uses_current_report_and_fields` | `assert result == {..., "short_sell": 10400, ...}`（把「股」当「元」锁死） | 仍是**精确 dict 相等**，改为 `"short_sell": None, "short_sell_volume": 2900` | **不降**（仍全等比较）+ 新增 2 条用例 |
| 同上 `::test_block_trades_uses_current_report_and_fields` | `assert result[0]["premium_pct"] == -1.5`（把「差 100 倍」锁死） | `pytest.approx(-17.47, abs=0.01)` + 等于 `(9.92/12.02-1)*100` + **反向断言**与 −17.95 相差 >0.4 + `amount/price == volume` | **增强**（三重断言 + 反向排除） |

- **无任何用例被删除或跳过**。
- `tests/conftest.py` 新增 autouse fixture `_reset_scan_cooldown`：只重置 E-01 引入的模块级 `_scan_last_timeout`，**不改任何断言**（否则前一个超时用例会让后续扫描用例命中冷却分支，表现为随机失败）。
- 其余 18 个被修改的测试文件均为**纯新增用例**，未改动既有断言。

---

## 四、遗留与待用户操作

1. **M-01 的配置侧**：`gh secret list` 仍为空。请在仓库 `Settings → Secrets and variables → Actions` 添加 `PREDICT_API_BASE`（如 `https://fund-assessment.onrender.com`）与 `ADMIN_TOKEN`（与 Render 控制台一致）。配好后下一次交易日 16:3x CST 的 run 应从「红」转「绿」，且不再是 skip。
2. **`_DDX_FAIL_COOLDOWN` / `_scan_pool` 均为进程内状态**：Render 每次部署/休眠唤醒都会重置，属预期行为（本项目单 worker 约束）。
3. **批次 B / C 未开始**：25 项 P2 中已随批次 A 顺手关闭 1 项（A-04 的 `limit` 归一化部分），剩余 24 项按计划分两批推进。

---

> 本记录由 AI 协作生成，关键决策请由工程负责人复核。
