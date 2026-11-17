# 第六轮质量评估 · 阶段二批次 B2 修复记录（8 项：数值出口 / 前端 / LLM / 测试有效性）

**范围**：`78de0ff..ba5deb7`｜**提交**：8 个 fix/test｜**回归**：**3034 passed / 1 skipped / 0 failed / 87.55s**（B1 3001 → **+33 例**，0 用例消失）
**既有断言改动**：**0 条**

---

## 一、逐项闭环

| # | 编号 | 提交 | 关键点 |
|---|---|---|---|
| 1 | D-03 高原识别 `inf` 污染 → 500 | `7c2899c` | 函数自己排序 + `_finite_ir()` 过滤 |
| 2 | D-04 多标的排名无消毒 | `87af155` | `_sanitize_floats()` + 稳定排序键 |
| 3 | C-04 移动端触摸目标 <48px | `3fadfc2` | 真浏览器实测：改前 5~18 个小目标 → 改后 **0** |
| 4 | C-05 `diversification` 未进超时白名单 | `dd59546` | 并入 30s 档 |
| 5 | A-15 板块兜底串行 17s+ | `8cb54ba` | 去掉第二次串行 + 进程内单飞；实测 32s→**26s** |
| 6 | B-01 Gemini system 退化成 user | `fa2e713` | 改走顶层 `systemInstruction` |
| 7 | B-02 协议开关拼错静默回落 | `a0dc1ef` | WARN + `protocol` 进健康检查 |
| 8 | F-01 14 例全是源码字符串断言 | `ba5deb7` | 模板插值审计 + `node --check` 护栏 |

### 1. D-03（`7c2899c`）

- **根因**：`_analyze_plateau` 以 `grid[0]` 取最优，**隐式依赖调用方已按 IR 降序排好**；`component_ir_mean` 无 `isfinite`；入选条件 `ir > 0` 对 `inf` 成立 → `inf` 一路到 `JSONResponse(allow_nan=False)` 抛 `ValueError` → 500。
- **方案**：入口自保排序（IR 缺失归 0）；`_finite_ir()` 统一过滤 inf/nan；连通域只收有限正 IR；均值只对有限值求，全过滤 → None。
- **验证**：6 条——**乱序输入与有序输入结果全等**（证明不再依赖调用方）；`inf` 不入域且均值有限；`nan` 不选中不计数；全 inf/nan → None；IR=None 不抛；空/全负 → None。

### 2. D-04（`87af155`）

- **根因**：排名按 `information_ratio` 排序无消毒，`NaN` 比较恒 False → 排名不稳定 + 响应 500；且与第五轮在 `dashboard.py` 建立的纪律不一致（同一类比率两套规矩）。
- **方案**：`_sanitize_floats()` 递归把 inf/nan → None（与 dashboard 同纪律）；`_rank_key` 让缺失/非有限稳定排末尾；`compare` 每票先消毒再排序，`optimize` 出口整体消毒。
- **验证**：4 条——含 inf/nan 的 best → 200 且响应体无 `Infinity`/`NaN`；`best=None` 全空稳定；**连调两次排名完全一致**；`optimize` 的 grid + plateau 均消毒。

### 3. C-04（`3fadfc2`）— 真浏览器实测

- **根因**：`.wb-btn` 移动端只有 40px（低于 48 门槛）；`.wb-hot-row` ≈29~33px、`.wb-link` ≈19px **完全没有移动端放大规则**。工作台是默认落地页。
- **方案**：`@media (max-width:640px)` 内 `.wb-btn{min-height:48px}`、`.wb-hot-row{padding:14px 6px;min-height:48px}`、`.wb-link{inline-flex;min-height:48px}`。
- **验证（本机 Edge + Playwright 实测，非推演）**：

| 视角 | 改前 `.wb-*` 小目标 | 改后 | hscroll |
|---|---|---|---|
| 375×667 | 18 个（2×`wb-btn` 40px、2×`wb-link` 19px、13×`wb-hot-row` 30~33px） | **0 个** | 0 |
| 414×896 | 5 个 | **0 个** | 0 |

渲染高度实测均 48px。剩余非工作台小目标（未动，遗留）：`span#connStatus` 7×7（状态点，非控件）、5 个 `span.hud-subtab` 高 44px。

### 4. C-05（`dd59546`）

- **方案**：`diversification` 并入 risk/attribution 同一条 `startsWith` → 30000。
- **验证**：用 **node 真执行** `API_TIMEOUT_MS` 函数体断言 `diversification=30000`（非 15000）、`risk/attribution=30000`、`scan=25000`、行情 `15000`。本机 node 存在，**实际执行非 skip**。

### 5. A-15（`8cb54ba`）— 24s 的更正

- **根因**：东财 2 跳 → 新浪板块资金 `fenlei=2`(6s) → `fenlei=0`(6s) → 旧新浪(5s) 全串行；`get_sector_ranking` 被 6 个端点共用且无 single-flight。
- **方案**：`fenlei` 只试 `2`（失败本次即放弃 + 300s 冷却）；`get_sector_ranking` 加进程内单飞（Lock + 双检缓存）。
- **实测最坏耗时（wall clock，每个上游挂到自身 timeout 再失败）**：改前 **32.00s** → 改后 **26.00s**。
- **对阶段一结论的更正**：报告写的 17s **少算了两个跳**（东财概念兜底 5s、新浪 v2 5s）。**真实基线是 32s**。要压到 15s 内必须动「兜底跳数 / 单跳超时」，会改变数据源覆盖面 → 单独立项，不作为本次范围。

### 6. B-01（`fa2e713`）

- **根因**：`_call_gemini` 角色映射只有 `assistant → model`，**`system` 全落 `user`**。本仓库大量模板用 `{"role":"system"}`。Gemini 原生要求走顶层 `systemInstruction`，否则人格/JSON 约束弱化**且不报错**，表现为「偶尔解析失败」。
- **方案**：system 聚合进 `payload["systemInstruction"]`，`contents` 只留 user/model；**无 system 不下发该键**（避免空 systemInstruction）。
- **验证**：5 条——system→顶层且 contents=1；多条 system 换行合并；assistant→model 顺序不变；无 system 无该键；**空 system 也不下发**。既有 2 条 gemini 用例未改仍绿。

### 7. B-02（`a0dc1ef`）

- **方案**：非空且 ≠`gemini` → WARN（写出原值 + 后果 + 正确写法）；构造后 INFO 记生效协议 + base_url（**不打 key**）；`LLMProvider` 加 `protocol` 字段并进健康检查。
- **一处对指令的合理修正**：我在指令里写的 `provider_status()` **在本仓库并不存在**，实际出口是 `health_check()`（即 `/api/health/llm` 的数据源）。工程师改用 `health_check()`，等价出口、未新建接口 —— 正确。
- **验证**：5 条——`gemini-pro`→openai + WARN；`gemini`→v1beta 且无 WARN；大小写/空格容错；未设→openai 无 WARN（避免噪声）；**告警行不含 key 及其任何片段**。

### 8. F-01（`ba5deb7`）— 测试有效性

- **改法**：① `fund-head` 全局子串断言 → 分页作用域断言；② 新增**模板插值审计**（扫描 `workbench.js` 全文 + 本轮新增的两块 `index.html` 函数），白名单分四层且**每条注明依据**（`_SAFE_WRAPPERS` / `_NUMERIC_CALLS` / `_NUMERIC_VARS` / `_SCOPED_SAFE_EXPRS`）；顺手给该块内 `level`/`action`/`d.effective_holdings` 补了 `escapeHtml`；③ 新增 `node --check` 语法护栏（3 个 js 文件）。
- **有效性实证（关键）**：把 `renderHot` 里的 `esc(it.name || '—')` 临时改成 `` `${it.name || '—'}` `` → 审计测试**立刻变红**，报 `["it.name || '—'"]`；`git checkout` 还原后恢复绿。**这是"测试真的有效"的唯一证据，已实测非推演。**

---

## 二、回归

| 项 | 结果 |
|---|---|
| 本机全量（主理人独立复跑） | **3034 passed / 1 skipped / 0 failed / 87.55s** |
| 测试增量 | 3001 → 3034（+33），新增 2 个文件（`test_frontend_api_timeout.py`、`test_sector_ranking_resilience.py`） |
| 用例消失 / 新增 skip | **0 / 2**（2 处均为 `skipif(node is None)`，本机 node 存在 → 实际执行，非跳过） |
| 既有断言改动 | **0 条** |

## 三、线上指纹复验

| 指纹 | 修复前（实测） | 修复后（实测） | 判定 |
|---|---|---|---|
| `GET /api/health/llm` 的 `providers.ly77625` 键集 | `[available, base_url, circuit_open, fail_count, free_tier, has_multi_keys, model, priority, provider_type, rpm, same_upstream_as]`（**无 protocol**） | 新增 **`protocol`** | ✅ |

> B-01（Gemini `systemInstruction`）无法用匿名指纹验证——需线上 `LY77625_PROTOCOL=gemini` 且实际调用该 provider。已有 5 条单测锁死请求体结构，**线上端到端维持待基线**（与阶段四"仅匿名面"决策一致）。

## 四、遗留（如实）

1. **index.html 的转义面远大于历轮记录**。我独立实测：全文 **997 处 `${...}` 插值，仅 218 处被 `esc/escapeHtml` 包裹**；未包裹的 779 处里约 130 处是数值格式化器（`fmtNum` 48 / `color` 35 / `changeColor` 16 / `_numOrDash` 13 / `fmtMoney` 8 / `changeSign` 9），其余需逐处判定。
   —— 历轮说的「152 处 innerHTML 仅 21 处转义」**是低估**。工程师报的「539 处」与我的 779 口径不同（统计范围/去重方式差异），**两个数字都不宜单独引用**，以本节口径为准。
   **本轮作用域**：`workbench.js` 全文 + 本轮新增的 `index.html` 两块函数。其余**冻结为遗留**，建议单开「前端 innerHTML 转义专项」按页推进（修完一页纳入审计作用域）。
2. **审计规则有一处宽松点**：字面量三元规则（`expr` 含引号且不含 `.` 即放行）会漏掉「无点号裸变量 + 字符串字面量」这类写法。我实测**当前零实际命中**（`workbench.js` 该规则放行 0 条），属潜伏面；专项里应收紧为显式白名单。
3. **A-15 只从 32s 降到 26s**，未达「15s 内」。再压必须动兜底跳数/单跳超时，会改数据源覆盖面 → 单独立项。
4. **B2-7 的 `protocol` 字段只对 `health_check()` 出口可见**；`/api/health` 顶层未加（避免动主健康检查契约）。

---

> 本记录由 AI 协作生成，关键决策请由工程负责人复核。
