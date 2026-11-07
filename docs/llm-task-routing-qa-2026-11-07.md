# QA 复验报告：按任务难度分层调度 LLM（Task-Tier Routing）

- **Language**: 中文
- **文档类型**: QA 复验报告（T05）
- **日期**: 2026-11-07
- **作者**: 严过关（QA 工程师）
- **上游输入**:
  - `docs/llm-task-routing-design-2026-11-07.md`（**§8 为验收口径修正，本报告按 §8 判分，不按 PRD AC4**）
  - `docs/llm-task-routing-prd-2026-11-07.md`（AC4 已作废）
  - 实现：`src/core/llm_task_tier.py`、`src/core/llm_router.py`、`web/api.py`、4 个调用点
- **复验原则**: 不相信自报告，用**独立用例 + 变异测试**验证，而非重跑同一条 `pytest tests/`。

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| 全量回归 | **2286 passed / 0 failed**（基线 2261 + QA 独立新增 25），无 `skip`/`xfail` 新增 |
| 新增 `def test` | 全仓 2204（≥ AC1 基线 2076）；QA 新增 18 个 `def test`（参数化展开 25 条） |
| 四个高风险点 | AC5 / AC6 / AC7 / model 不串档：**全部通过**（独立用例验证） |
| 变异测试 | 2 个人为破坏**全部被抓到**（见 §4） |
| 零回归 | 既有 5 个测试文件删除行数 = 0；`priority` 真实改动行 = 0；Provider 数不变 |
| **发现的问题** | **① `zhipu_glm_45` 未置 `enabled=False`（与 §8 AC4-2 / D10 不符）；② `LLM_TIER_HOP_TIMEOUT_<TIER>` 环境变量旋钮未实现（B5 能力缺失）** |
| 路由判定 | ②③ 属"实现与设计不符"→ 报 `software-engineer`；但 ① 涉及"零回归 vs D10"口径冲突，**需主理人裁定**（详见 §5） |

---

## 1. 复验方法（独立视角）

1. **独立环境**：`.venv` 解释器已损坏（指向不存在的 Python310），改用 `D:/dev-tools/Python312/python.exe` + `PYTHONPATH=<repo>`。核实全量 2286 passed 与主理人 2261 + QA 25 自洽。
2. **独立用例**：新建 `tests/test_llm_tier_routing_regression.py`（25 条），**不复用**工程师用例。AC5 特意断言"第二次是否真的再次调用 provider"，避免"只断言返回了对象、没查 `cached`/调用次数"的假过。
3. **变异测试**：对 `llm_router.py` 与 `llm_task_tier.py` 各做 1 处人为破坏，验证用例真的能抓到（改完已按字节级 `diff -q` 校验还原）。
4. **实证脚本**：直接实例化 Router 打印 `enabled/priority/model` 与三档链，核对线上等价 env 行为。

---

## 2. 测试结果数字

| 命令 | 结果 |
|---|---|
| `pytest tests/ -q`（全量，Python312） | **2286 passed**, 1 warning, 52.34s |
| `pytest tests/test_llm_task_tier.py tests/test_llm_tier_routing_regression.py tests/test_llm_router.py tests/test_llm_budget.py tests/test_llm_probe_api.py tests/test_ai_service.py tests/test_multi_agent_fund.py -q` | **294 passed**, 11.53s |
| QA 独立文件单独跑 | **25 passed** |

**零回归复核**（工程师未改既有断言）：

```
git diff tests/test_llm_router.py     | grep -c '^-[^-]'  -> 0
git diff tests/test_ai_service.py     | grep -c '^-[^-]'  -> 0
git diff tests/test_llm_budget.py     | grep -c '^-[^-]'  -> 0
git diff tests/test_llm_probe_api.py  | grep -c '^-[^-]'  -> 0
git diff tests/test_multi_agent_fund.py | grep -c '^-[^-]' -> 0
```

> **关于 `priority=` 检查的口径澄清（重要）**：`git diff | grep -c "priority="` 返回 **2**，但这两行是 diff 的**上下文行**（ttapi 块、agnes 块周围未改动的 `priority=0,` / `priority=90,`）。用严格口径 `git diff | grep -E "^[+-].*priority="` 返回 **0**，证明 **priority 数值与排序逻辑零改动（AC3 满足）**。请勿把上下文行的 2 误读为失败。

---

## 3. AC 逐条判定表（按设计 §8 口径）

| AC | 判据 | 结论 | 证据 / 原因 |
|---|---|---|---|
| **AC1** | 全量全绿、用例数 ≥ 2076、无新增 skip/xfail | ✅ 通过 | 2286 passed；`def test` 2204；无 skip/xfail |
| **AC2** | `_providers_for_tier("standard") == list(_providers)`，`None` 等价 | ✅ 通过 | 独立用例 `test_standard_view_is_new_list_same_objects`、`test_three_tiers_in_online_env` |
| **AC3** | priority 数值/排序零改动、无删 Provider | ✅ 通过 | `^[+-].*priority=` = 0；Provider 仍 4 个（线上等价） |
| **AC4'** | deep 链 = `zhipu_glm → agnes(3.0) → ly77625`，caps 20/25/20，不含 glm_45/2.5-flash/pro/ttapi | ✅ 通过 | 独立用例 + 工程师常量用例；变异 A 抓 18 条 |
| **AC4-2** | standard 全链含 disabled 的 glm_45；**chat 有效跳序 = zhipu_glm→agnes→ly77625** | ❌ **不通过（部分）** | 列表成员含 glm_45 ✓；但 `zhipu_glm_45.enabled == True`，`chat` 有效跳序为 **4 跳（含同源 glm_45）**，与 §8 声明不符（详见 §5-问题①） |
| **AC5** | 同 prompt 先 light 后 deep，第二次 `cached == False` 且 provider ∈ deep 链 | ✅ 通过 | 独立端到端：断言 `cached is False` **且** provider 被再次调用；变异 B 抓 3 条 |
| **AC6** | deep 首跳 429 → 落第 2 跳 agnes（非 ly77625），fallback==1，熔断计数正确 | ✅ 通过 | `test_deep_first_hop_429_falls_to_agnes`：visited==[zhipu_glm, agnes]，`_fail_count==1` |
| **AC7** | deep `timeout=120` → 预算 60、caps 20/25/20；`total_timeout=5` 最高优先；第 3 跳可达 | ✅ 通过 | 三条独立用例；caps 实测 `[20,25,20]`、budget 60.0；第 3 跳拿到 20s |
| **AC8** | 未知 tier 回落 standard + 恰好 1 WARN，不抛异常 | ✅ 通过 | 参数化 `None/""/"   "/"bogus"/123/[]/{} /3.5` 全部回落全链且不抛 |
| **model 不串档** | agnes 条目拿 `agnes-3.0-flash`，zhipu_glm/ly77625 拿不到 | ✅ 通过 | 逐跳捕获 model 参数：`zhipu_glm=None, agnes=agnes-3.0-flash, ly77625=None` |
| **D11 模型纠正** | `AGNES_MODEL=agnes-2.0-flash` → `agnes-3.0-flash` + 1 WARN；第二道保险 route.model | ✅ 通过 | 实证 + 独立用例（WARN 恰好 1 条）；deep 第 2 跳 route.model 恒为 agnes-3.0-flash |
| **旁路开关** | `LLM_TIER_ROUTING_ENABLED=0` → 三档全链 | ✅ 通过 | 独立用例三档全部 == 全链，且 route 全 None |
| **安全** | routing/health 不含 `api_key`/`sk-`/真实密钥 | ✅ 通过 | 独立用例：`routing_health()` 与 `health_check()` 的 JSON 序列化均无泄露 |
| **AC9/AC11** | 匿名 `GET /api/health/llm` 200，`routing.tiers` 三键有序 | ✅ 通过 | 工程师 HTTP 用例 + QA router 级；endpoint 匿名（无 `Depends`） |
| **AC13** | deep P95 ≤ 60s（线上） | ⚠️ 未覆盖 | 需线上真实 key/流量；单测等价证据：预算 60 + caps 保证整链 ≤ ~62s（<90s） |
| **AC14** | `POST /probe?tier=deep`（带 token）3 候选、agnes ok | ⚠️ 未覆盖 | 需线上 `ADMIN_TOKEN`（主理人无）；单测 `probe(tier=deep).total==3` 通过 |
| **AC15** | 5 次 deep 样本 `max(wall_clock) < 90s` | ⚠️ 未覆盖 | 线上实测项（附 curl 见 §6） |
| **D10** | `zhipu_glm_45.enabled is False` 且 priority==1 且仍在 _providers | ❌ **不通过** | 见问题① |
| **B5** | standard "各 15s" 能力以 `LLM_TIER_HOP_TIMEOUT_STANDARD` env 提供 | ⚠️ **未实现** | 见问题② |

---

## 4. 变异测试结论（关键）

| 变异 | 破坏内容 | 结果 | 被抓到的用例（示例） |
|---|---|---|---|
| **A** | `TIER_SPECS["deep"].routes` 顺序改为 `zhipu_glm → ly77625 → agnes` | ✅ **被抓到（18 failed）** | `test_deep_chain_order_is_compliance_first`、`test_deep_chain_actual_providers`、`test_deep_429_falls_back_to_second_hop`、`test_deep_per_hop_timeouts_follow_cap`、`test_deep_third_hop_is_really_attempted` 等 |
| **B** | `_make_key` 中 `"tier": tier` 改为 `"tier": ""`（去掉 tier） | ✅ **被抓到（3 failed）** | QA `test_light_then_deep_does_not_share_cache`、`test_deep_then_light_does_not_share_cache`、工程师 `test_cross_tier_cache_isolation` |

**结论**：AC5 的缓存隔离用例与 deep 链顺序/逐跳 cap 用例**均具备真实齿力**（非假过）。`test_same_tier_cache_still_hits` 在变异 B 下仍通过（正确地只测同档命中），说明测试能区分"隔离"与"缓存失效"。

**还原校验**：两文件已从备份还原，`grep "QA MUTATION"` 为空，`diff -q` 与备份逐字节一致；还原后全量 2286 passed。

---

## 5. 发现的问题与去向

### 问题①（需裁定）`zhipu_glm_45` 未置 `enabled=False` —— 与 §8 AC4-2 / D10 不符

- **现状（实证）**：线上等价 env 下 `zhipu_glm_45: enabled=True, priority=1, model=glm-4.5-flash`。
- **与设计冲突**：设计 §8 备注 2 明确写"`zhipu_glm_45` **现在是 enabled=False**（D10）"；§8 AC4-2 声明"chat() 实际跳序因 is_available 跳过 = `zhipu_glm → agnes → ly77625`"。当前实现 standard `chat()` 有效跳序为 **4 跳：`zhipu_glm → zhipu_glm_45 → agnes → ly77625`**。
- **影响**：`zhipu_glm` 触发 429 时会先落到同源 `zhipu_glm_45`（同 Key、同 30 rpm，几乎必然再次 429），白跑一跳、浪费令牌与一次退避；D9/D13 的"standard 有效链 == 深链声明"自洽性被破坏。
- **最小复现**：
  ```bash
  PYTHONPATH=. python -c "
  import os; os.environ.update({'ZHIPU_API_KEY':'z','AGNES_API_KEY':'a','LY77625_API_KEY':'l'})
  from src.core.llm_router import LLMRouter
  r=LLMRouter()
  print([(p.name,p.enabled) for p in r._providers])
  # 期望 glm_45 enabled=False；实际 True
  "
  ```
- **口径冲突（为何不直接判死）**：保持 `enabled=True` 恰恰与"今日行为逐字节一致"（今天 glm_45 确实会被尝试）一致；工程师新增用例 `test_standard_chain_includes_zhipu_glm_45` 也按 4 跳断言。即 **D10（禁用同源）与"零回归"在这条上互斥**。
- **建议**：由主理人裁定——
  - 选 A（按 §8/D10）：把 `zhipu_glm_45` 构造处加 `enabled=False`（不动 `priority=1`），并把新增用例 `test_standard_chain_includes_zhipu_glm_45` 的期望改为 3 跳；D10 的零回归核验已在 §1.2 表逐条做过（probe/名字列表/priority 均不受影响）。
  - 选 B（保零回归）：保留 `enabled=True`，则 §8 备注 2 与 AC4-2 需改写口径。
- **去向**：报 `software-engineer`（若选 A 附上改动点）+ 报 `team-lead` 裁定。

### 问题②（能力缺失，低优先）`LLM_TIER_HOP_TIMEOUT_<TIER>` 环境变量旋钮未实现

- **现状**：全仓 `grep LLM_TIER_HOP_TIMEOUT` 为空；`chat()` 内联为 `min(caller_cap, hop_cap, remaining)`，未委托设计要求的 `resolve_hop_timeout()`（§6.1 规则 5 明文"禁止在 chat() 里手写 min(...)"）。
- **影响**：B5 里"standard 各 15s"的落地机制（设计说"线上加一条 env 即可，无需改代码"）**实际不生效**——设置 `LLM_TIER_HOP_TIMEOUT_STANDARD=15` 对单跳无任何影响。默认部署不受影响（零回归保持）。
- **另**：设计 D11 的 `correct_model_id` 被实现为 `_remap_retired_model`，且纠正表只有 `agnes-2.0-flash` 一项（缺 `agnes-2.5-flash` / `agnes-2.5-pro-*`）。仅当有人把 `AGNES_MODEL` 设为这些旧值才会命中缺失项，风险低。
- **建议**：作为 P2 记录或补实现（读取 `LLM_TIER_HOP_TIMEOUT_<TIER>` 参与 `min`）。不阻塞本期。
- **去向**：报 `software-engineer`（记录），`team-lead` 知悉。

---

## 6. 线上复验可执行清单（curl）

> 线上地址 `https://fund-assessment.onrender.com`。**匿名**命令可直接跑；**带 token** 命令需 `ADMIN_TOKEN`（主理人没有，写成占位符 `$ADMIN_TOKEN`，由运维/你本地注入）。

### 6.1 匿名（无需 token）

```bash
BASE=https://fund-assessment.onrender.com

# AC9/AC11：routing.tiers 三档有序链
curl -s "$BASE/api/health/llm" | python -m json.tool | sed -n '/"routing"/,/}/p'

# 精确取三档链
curl -s "$BASE/api/health/llm" | python -c "import sys,json;d=json.load(sys.stdin);print(json.dumps(d['routing']['tiers'],ensure_ascii=False,indent=2))"
# 期望：{"deep":["zhipu_glm","agnes","ly77625"],"standard":[全链(含 zhipu_glm_45)],"light":["zhipu_glm","agnes"]}

# AC10：无密钥泄露（应为空输出）
curl -s "$BASE/api/health/llm" | grep -iE "sk-|api_key|bearer" || echo "OK: 无密钥片段"

# 逐跳明细（关注 routes.deep[1].model == agnes-3.0-flash、label == agnes_30）
curl -s "$BASE/api/health/llm" | python -c "import sys,json;d=json.load(sys.stdin);print(json.dumps(d['routing']['routes'],ensure_ascii=False,indent=2))"

# 预算（期望 deep=60, standard=25, light=15）
curl -s "$BASE/api/health/llm" | python -c "import sys,json;d=json.load(sys.stdin);print(d['routing']['budgets_seconds'])"

# AC13（PRD 版）：agnes.model 已纠正 + available
curl -s "$BASE/api/health/llm" | python -c "import sys,json;d=json.load(sys.stdin);p=d['providers']['agnes'];print({k:p[k] for k in ('model','available') if k in p})"
```

### 6.2 带 token（需 ADMIN_TOKEN）

```bash
BASE=https://fund-assessment.onrender.com
ADMIN_TOKEN='<在此注入运维提供的 ADMIN_TOKEN>'

# AC11/AC14：按档位探活（deep 返回 3 候选，agnes 该项应 ok==true 且 model==agnes-3.0-flash）
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$BASE/api/health/llm/probe?tier=deep" | python -m json.tool

# light 探活（2 候选）
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$BASE/api/health/llm/probe?tier=light" | python -m json.tool

# 未带 token 必须 401/403（安全回归）
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE/api/health/llm/probe?tier=deep"
```

### 6.3 AC12/AC15 业务触发后读 health（建议连跑 5 次看 last_routing / 时延余量）

```bash
# 触发一次 deep 业务（端点名以线上实际为准，例如 POST /api/fund/analyze；此处占位）
# 连续 5 次后读：
curl -s "$BASE/api/health/llm" | python -c "import sys,json;d=json.load(sys.stdin);r=d['routing'];print('last_routing=',r['last_routing']);print('counters=',r['counters'])"
# 期望：last_routing.tier==deep 且 provider ∈ {zhipu_glm,agnes,ly77625}；
#      counters.deep.calls≈5；AC15 观察 deep 时延是否 max<90s（可结合 APM/日志 latency_ms）
```

> **注意**：线上 `zhipu_glm_45` 若维持 `enabled=True`（问题①未裁定），则 standard/happy-path 观测里它可能出现在 `counters` 或 probe 结果中，属预期现状，不是新 bug。

---

## 7. 附录：QA 独立用例清单（`tests/test_llm_tier_routing_regression.py`，25 条）

- **TestAC5CacheIsolation**：`test_light_then_deep_does_not_share_cache`、`test_deep_then_light_does_not_share_cache`、`test_same_tier_cache_still_hits`
- **TestAC6429108Fallback**：`test_deep_first_hop_429_falls_to_agnes`
- **TestAC7BudgetAndHopCaps**：`test_deep_timeout_120_clamped_to_60_with_per_hop_caps`、`test_explicit_total_timeout_has_highest_priority`、`test_third_hop_reachable_when_first_two_fail_fast`
- **TestModelNotCrossHop**：`test_deep_chain_model_params_per_hop`、`test_explicit_model_overrides_all_hops`
- **TestRoutingMatrix**：`test_three_tiers_in_online_env`、`test_unknown_tier_never_raises_and_falls_back`（8 参数）、`test_standard_view_is_new_list_same_objects`
- **TestRetiredModelCorrection**：`test_old_agnes_model_corrected_with_warn`、`test_tier_chain_second_hop_uses_current_model_regardless`
- **TestGlobalBypass**：`test_bypass_degrades_all_tiers_to_full_chain`
- **TestNoSecretLeak**：`test_routing_health_has_no_secret`、`test_health_check_snapshot_has_no_secret`
- **TestKnownDeviationSnapshot**：`test_zhipu_glm_45_enabled_state_snapshot`（锁定问题①现状，防静默漂移）

> 说明：`test_known_deviation` 系列**锁定现状**而非期望失败，以保证交付套件全绿；问题①的"期望 D10"已在 §5 与 AC4-2 判定中标注为不通过。
