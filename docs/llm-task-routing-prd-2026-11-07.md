# 增量 PRD：按任务难度分层调度 LLM（Task-Tier Routing）

- **Language**: 中文
- **Programming Language**: Python（后端 `src/core/llm_router.py`），无前端改动
- **Project Name**: `fund_assessment_llm_task_routing`
- **文档类型**: 简单 PRD（增量，不含竞品分析/市场调研）
- **版本**: v2（2026-11-07 并入 deep prompt 实测证据，覆盖 v1 中与新数据冲突的结论）
- **作者**: 许清楚（产品经理）
- **状态**: 待架构师评审

---

## 0. 原始需求复述

> 用户原话：「查看一下已有的api平台各模型的能力，根据任务的难易分配模型」

翻译为产品需求：**当前所有 LLM 调用共用一条 priority 链（谁先命中用谁），7 智能体深度决策与 100 字新闻摘要抢同一个模型。需要按「任务难度」把调用分流到能力/额度匹配的模型上，同时不让线上已有的 2136 条回归用例失败。**

---

## 1. 项目信息

| 项 | 内容 |
|---|---|
| 线上地址 | https://fund-assessment.onrender.com |
| 线上实际加载 Provider | 4 个：`zhipu_glm` / `zhipu_glm_45` / `agnes` / `ly77625` |
| **线上实际可用异源模型** | **仅 2 个**（见 F5）：GLM-4.7-Flash 与 gemini-3.8-flash |
| 涉及核心文件 | `src/core/llm_router.py`（993 行） |
| 涉及调用点 | 9 处，经 `_call_llm` / `router.chat` 收敛为 3 个入口 |
| 本地 Key 状态 | 本地 `.env` **无** `ZHIPU_API_KEY` / `LY77625_API_KEY` → GLM 与 gemini-3.8-flash 本地无法实测，只能引用线上/主理人实测 |

### 1.1 证据等级定义（全文引用）

| 编号 | 含义 | 可信度 | 冲突时优先级 |
|---|---|---|---|
| **E5** | 2026-11-07 **deep prompt 实测**（1800 字中文 7 分析师 prompt，要求严格 JSON + 完整 schema：`action/position_size/confidence/analysts[7]/debate/astock_constraints`） | **最高** | **1（覆盖 E1）** |
| **E1** | 2026-11-07 **短 prompt 实测**（5 日净值序列，严格 JSON + 5 日涨跌幅 + 趋势判断；正确答案 9.21% / 加速上涨） | 高 | 2 |
| **E2** | 厂商官方文档/规格 | 中 | 3 |
| **E3** | 代码静态阅读（本地仓库） | 高（对事实本身） | 2 |
| **E4** | 未验证（无实测也无文档） | 低 | 4 |

> **规则**：E5 与 E1 冲突时以 E5 为准（deep prompt 才是 deep 档的真实工况）。E1 只覆盖「中等难度 + 强 JSON」，**不能用于判定 deep 档能力**。

---

## 2. 产品定义

### 2.1 Product Goals（3 个，正交）

| # | 目标 | 衡量方式 |
|---|---|---|
| **G1 正确性** | 最高难度任务（7 智能体基金决策）由「E5 实测完整输出 schema + 趋势判断正确」的模型承载；**任何 E5 实测失败（500/403/502/404）的模型不得留在生产链上** | deep 链 TOP2 均为 E5 实测「全字段齐 + 7 分析师 + JSON 严格」 |
| **G2 额度经济** | 轻任务（100~200 字话术/新闻摘要）不占用稀缺额度；免费额度按业务价值分配 | light 档首选不进入 deep 链；同 prompt 跨 tier 不共享缓存 |
| **G3 可控可回滚** | 分层是**叠加层**，不改变现有默认行为与控制面 | 不传 tier 时链路顺序与今日完全一致；2136 条回归全绿；`/api/health/llm` 可读出 tier 矩阵 |

### 2.2 User Stories

| # | As a | I want | So that |
|---|---|---|---|
| US1 | 基金投资者 | 我的 7 智能体深度决策走最可靠的模型 | 不会因为模型把「加速上涨」判成「震荡」而给出错误加仓建议 |
| US2 | 运维/开发者 | 在 `/api/health/llm` 看到每个 tier 的实际模型链和命中分布 | 出问题能立刻定位是哪个档位在降级，而不是翻日志 |
| US3 | 产品/成本负责人 | 廉价高频任务不消耗主力模型额度 | 高峰期主力模型不被话术生成挤爆（429 一起挂） |
| US4 | 架构师/QA | 引入分层后旧行为 100% 可复现 | 回归失败时能断定「不是分层改的」 |

### 2.3 Non-Goals（本期明确不做）

- 不做按 token 成本计价的动态出价/拍卖式调度。
- 不做多模型并行投票（ensemble）。
- 不做前端 UI 改造（仅后端 + health JSON）。
- 不新增收费 Provider 接入（除非 Q1 决策为「愿意付费」）。

---

## 3. 模型能力矩阵

### 3.1 可用模型矩阵（含不可用项，用于决策排除）

| # | provider | model | 建议档位 | 上下文 | 延迟档 | JSON 可靠性 | 推理强度 | 免费额度 | 证据 | 置信度 |
|---|---|---|---|---|---|---|---|---|---|---|
| M1 | `ly77625` | gemini-3.8-flash | **deep 首选 / standard 备选** | E4 未验证 | **9.70s（E5，deep prompt）**；9.30s（E1） | **E5 严格无 fence，schema 全字段齐，7 分析师** | 高（E1：282 reasoning tokens，reason 质量最佳；E5：436 completion tokens，比 M3 省 25%） | 免费中转，额度 E4 | **E5+E1** | **高** |
| M3 | `agnes`（槽位复用，改 model） | agnes-3.0-flash | **deep 次选 / standard 备选 / light 降级** | E4 未验证 | **16.45s（E5）**；8.92s（E1 短任务） | **E5 严格无 fence，schema 全字段齐，7 分析师** | 中高（E5：582 completion tokens；E1 短任务仅 50 tokens） | 免费 | **E5+E1** | **高** |
| M2 | `zhipu_glm` | glm-4.7-flash | **standard 首选 / light 首选 / deep 末位** | **200K**（E2） | **E4**（本地无 Key） | **E4／E2 均未报告支持 json mode** | 中（30B 总参 / 3B 激活 MoE） | 免费，30 并发（E2） | E2 | **中**（能力）／**低**（JSON） |
| M8 | `zhipu_glm_45` | glm-4.5-flash | **禁用（同源重试位）** | 128K（E2） | E4 | E4 | E4 | 免费，共享同一 Key 与 30 rpm（E2） | E2：**2026-01-30 已下线，请求自动路由到 GLM-4.7-Flash** | 中 |
| M4 | `agnes`（现网配置） | agnes-2.0-flash | **禁用（生产 500）** | E4 | 1.66s | — | — | — | **E5：HTTP 500 `Model agnes-2.0-flash is temporarily unavailable`**；且 Agnes `/v1/models` **已不含该模型**（E5）→ 属**过期模型 ID** | 高 |
| M5 | — | agnes-2.5-flash | **全档排除** | E4 | **22.36s**（E1） | 是（E1） | **2123 reasoning tokens 且把趋势判成「震荡」**（E1） | 免费 | E1 | 高（对 E1 场景）／未做 E5 |
| M6 | — | agnes-2.5-pro-alpha | **不可用** | — | 1.28s | — | — | **403 `Insufficient user quota, remaining: $0`**（E1+E5） | E1+E5 | 高 |
| M7 | — | agnes-2.5-pro-beta | **不可用** | — | 1.17s | — | — | 同上 403（E1+E5） | E1+E5 | 高 |
| M9 | `ly77625` | qwen3.8-27b | **不可用** | E4 | 3.00s | — | — | — | **E1：HTTP 502（Cloudflare 错误页）**；E5 复测仍 502 | 高 |
| M10 | `ly77625` | x-deepseek-v4.1-flash | 未评估 | E4 | E4 | E4 | E4 | E4 | **E4：仅在 `/v1/models` 列表出现，从未调用** | **低** |
| M11 | `ttapi` | gpt-4o | **不可用/删除候选** | E4 | 4.83s | — | — | — | **E1+E5：HTTP 404 返回 HTML（平台已失效）** | 高 |
| M12 | Agnes 平台 | agnes-2.5-video / image 系列 | 不适用（非文本） | — | — | — | — | — | E1（`/v1/models` 列表） | 高 |

### 3.2 五个必须写进设计的关键事实

| # | 事实 | 证据 | 对设计的影响 |
|---|---|---|---|
| **F1** | `zhipu_glm`(priority=-1) 与 `zhipu_glm_45`(priority=1) **是同一上游模型**（GLM-4.5-Flash 已下线自动转发到 4.7-Flash），且共享同一 API Key 与 30 rpm 配额 | E2 | 现有「双 Provider 兜底」是**伪冗余**：高峰期一起 429。不得把二者当两条独立链路做可用性承诺 |
| **F2** | `multi_agent_fund.py:542` 传 `timeout=120`，但 `chat()` 内 `budget = LLM_TOTAL_TIMEOUT`（默认 **25s**），且 `call_timeout = min(timeout, remaining)` | E3 | **单次首选失败就能吃光全部预算**：`ly77625` 的 provider timeout 默认 30 → `call_timeout = min(30, 25) = 25s` → 一旦它慢/超时，25s 预算 100% 耗尽，`remaining <= 0` → **降级链第 2、3 位从未被尝试**。分层必须配套抬 `total_timeout` |
| **F3** | `_LLMResponseCache._make_key()` 仅含 `messages / model / temperature / json_mode`，而 9 处调用中 `model` 基本为 `None` | E3 | 若只加 tier 不改缓存 key，**light 档的低质答案会以缓存命中形式喂给 deep 档**（TTL 300s） |
| **F4** | 现网 `agnes` provider 配的 `AGNES_MODEL=agnes-2.0-flash` 是**过期模型 ID**：平台返回 500 `temporarily unavailable`，且 `/v1/models` 已不再列出 | E5 | 这是**生产缺陷**：`agnes` 在 priority 90，是 `ly77625`(95) 之前的兜底位，实际打不通，等于兜底位是空的 |
| **F5** | 线上 4 个 Provider 去重后**真实只有 2 个异源可用模型**（GLM-4.7-Flash、gemini-3.8-flash）：`zhipu_glm`+`zhipu_glm_45` 同源（F1）、`agnes`=500（F4） | E1+E2+E5 | 3 档分层只有 2 个真实异源 → **档位之间的差异必须靠「链的顺序 + 预算 + 缓存策略」体现，不可能靠「每档一个专属模型」**；这也是必须把 `agnes` 修成 agnes-3.0-flash 的原因（否则 3 档只剩 2 个模型可分配） |

### 3.3 JSON 容错边界（降低对不可靠模型的容忍度）

`multi_agent_fund._parse_fund_analysis_response` 已具备容错：剥 markdown fence → 正则提取首个 JSON 对象 → 失败则 `_fallback_fund_result`（E3 复核一致）。

**产品含义**：模型不支持 `response_format`/JSON 不严格时**不会抛异常崩掉，而是静默退化为 fallback 结果 = 分析失败但用户看到的是「分析失败，请稍后重试」**。因此：

> **"JSON 不严格"在 deep 档等价于"业务失败"，不是"降级体验"。**
> 这也是为什么 deep 链必须把 **E5 已验证 JSON 严格**的 M1/M3 排在仅 E2 支持的 M2 **之前** —— 即使 M2 有 200K 上下文优势。

---

## 4. 任务难度分层定义

### 4.1 档位数量：3 档（重审后仍为 3 档，但理由变了）

| 方案 | 论证 | 结论 |
|---|---|---|
| 3 档（deep / standard / light） | 真实异源只有 **2 个**（F5）→ 档位差异只能靠顺序/预算/缓存体现；9 个调用点天然聚成 1+6+2 三簇 | **采纳** |
| 4 档（+ critical / + bulk） | 无第 3 个高质量候选可区分 `critical` 与 `deep`（pro 档 $0 全 403） | 否决 |
| 2 档（heavy / light） | standard 的 6 个调用点（JSON 抽取、中等分析）与 deep 的预算/JSON 要求差异明显，合并会让 deep 预算被 standard 拉爆 | 否决 |

**扩展约定**：`task_tier` 用字符串枚举（非 int），未知值按 `standard` 处理并 WARN，便于将来无损加档（例如 agnes pro 充值后可加 `critical`）。

### 4.2 三档定义

| 维度 | `deep` | `standard` | `light` |
|---|---|---|---|
| **业务语义判定规则** | 多角色/多步骤推理 + 需在约束下裁决 + 输出直接影响用户资金决策 | 单轮结构化抽取/中等分析，有明确字段 schema | 短文本生成/摘要，无 schema 约束 |
| **命中调用点** | `multi_agent_fund.py:542`；`ai_service.py:1106/1173` 且 `mode=="deep"` | `ai_service.py:1457 / 1470 / 1139 / 1334`；`1106/1173` 且 `mode!="deep"` | `script_library.py:487`；`news_aggregator.py:358` |
| **典型输入 token 量级** | 8K~40K（E5 实测 prompt 1800 字中文 ≈ 3K tokens + 持仓/新闻/宏观快照）；输出 2K~6K JSON | 2K~8K；输出 0.5K~2K | 0.5K~2K；输出 100~300 字 |
| **必须 JSON 严格** | **是**（解析失败 = 分析失败，见 §3.3） | **是**（1457/1139/1334）；1470 为否 | **否** |
| **单次模型延迟（实测口径）** | **10~17s 可接受**（E5：M1 9.70s / M3 16.45s） | 目标 ≤ 8s，硬上限 15s | 目标 ≤ 8s，硬上限 15s |
| **端到端预算（`total_timeout`）** | **60s**（硬上限 90s）——算式见 §4.3 | **25s**（= 现状默认，保持不变） | **15s** |
| **失败代价** | 高：用户拿到错误操作建议 | 中：局部功能降级 | 低：文案缺失，可模板兜底 |
| **额度敏感度** | 低（一天几十次） | 中 | **高**（频次最高） |
| **故障策略** | 逐个降级；单 provider 超时**必须收紧**防止吃光预算 | 链内降级，超预算即报错 | 快速失败 → 模板兜底（P2） |

### 4.3 deep 预算算式（这是 F2 的定量修复方案）

| 项 | 值 | 依据 |
|---|---|---|
| 首选 M1 单 provider 超时 | **20s** | E5 实测 9.70s × 2 倍余量；**禁止沿用 30s**（30s > 25s 现状预算，会单点吃光） |
| 次选 M3 单 provider 超时 | **25s** | E5 实测 16.45s × 1.5 倍余量 |
| 末位 M2（GLM）预留 | **15s** | E4 延迟未知，按最保守值 |
| **deep `total_timeout`** | **20 + 25 + 15 = 60s** | 保证「首选失败 → 次选 → 末位」**三次尝试都能真正跑到**，而不是只跑一次 |

> **为什么不是 90s/120s**：E5 证明 happy path 只需 9.7~16.5s。60s 已经能在两次连续失败后仍拿到结果，同时把最坏等待控制在 1 分钟内；拉到 120s 只会让失败场景白等 2 分钟，且需要额外确认 Render/网关侧超时（E4 未验证）。

---

## 5. tier × provider 映射表

### 5.1 映射（首选链 → 降级链）

| tier | 顺位 | provider | model | 选型理由 | 风险 |
|---|---|---|---|---|---|
| **deep** | 1 | `ly77625` | gemini-3.8-flash | **E5 最强证据**：9.70s、JSON 严格无 fence、schema 全字段齐、7 分析师、436 tokens（比 M3 快 41%、省 25% token） | 第三方免费中转，**额度与数据合规 E4**；单点 |
| **deep** | 2 | `agnes`（改 model） | agnes-3.0-flash | E5：16.45s、JSON 严格、schema 全字段齐、7 分析师 | 延迟为首选 1.7 倍；E1 显示短任务仅 50 tokens（长输出已由 E5 证伪风险） |
| **deep** | 3 | `zhipu_glm` | glm-4.7-flash | 200K 上下文可容纳全量上下文；官方网关、免费 30 并发、唯一非第三方中转 | **JSON mode 未验证（E2）**，因 §3.3 规则排末位而非次选 |
| **deep** | — | ~~`zhipu_glm_45`~~ | — | **排除**：同源（F1），会一起 429 | — |
| **deep** | — | ~~`agnes-2.0-flash`~~ | — | **排除**：E5 HTTP 500，模型 ID 已下架（F4） | — |
| **deep** | — | ~~`agnes-2.5-flash`~~ | — | **排除**：22.36s + 2123 reasoning tokens 且趋势判断错（E1）→ deep 场景**明确有害** | — |
| **standard** | 1 | `zhipu_glm` | glm-4.7-flash | **与今日默认链首位一致 → 零行为变更**，最大化回归安全；200K 适合中等抽取 | JSON mode 未验证 → P1-6 必须补测 |
| **standard** | 2 | `agnes`（改 model） | agnes-3.0-flash | E5 已验证 JSON 严格 + 全 schema，是 standard 的强兜底 | 16.45s 对 standard 偏慢（预算 25s 仍可容纳） |
| **standard** | 3 | `ly77625` | gemini-3.8-flash | 质量兜底，但**消耗未知额度故排最后** | 额度不明 |
| **standard** | — | ~~`zhipu_glm_45`~~ / ~~`agnes-2.0`~~ / ~~`agnes-2.5-flash`~~ | — | 同 F1 / F4 / E1 判断错误 | — |
| **light** | 1 | `zhipu_glm` | glm-4.7-flash | 见 §5.2 决策 | 与 standard 抢同一 30 rpm |
| **light** | 2 | `agnes`（改 model） | agnes-3.0-flash | GLM 限流时接管短文本生成 | 8.92s 对 light 偏慢 |
| **light** | — | ~~`ly77625`~~ | — | **排除**：不给廉价高频任务消耗未知额度的高质量模型 | — |
| **light** | — | ~~`agnes-2.0-flash`~~ | — | **排除**：500（F4） | — |

### 5.2 决策记录一：light 档首选 = `zhipu_glm`（glm-4.7-flash），不是 agnes-3.0-flash

| 维度 | glm-4.7-flash | agnes-3.0-flash | 判给谁 |
|---|---|---|---|
| light 是否需要 JSON | 否 | 否 | 平 |
| GLM 唯一已知短板（JSON mode 未验证） | **在 light 档不构成风险**——light 不要求 JSON | — | **GLM**（把模型放在能用其长处的档位） |
| 限流保护 | **已有 30 RPM 令牌桶**（E3：`rpm=30` → `_token_buckets` 生效） | `rpm=0`（无令牌桶，无节流保护） | **GLM** |
| 额度可持续性 | 官方网关、免费、30 并发（E2） | 第三方免费平台，额度 E4 未知 | **GLM** |
| 网关距离/依赖面 | 与 standard/deep 主力同域，出问题只排查一个上游 | 多一个第三方依赖 | **GLM** |
| 短任务延迟 | E4 未实测 | 8.92s（E1） | agnes（唯一劣势项） |
| 输出长度上限 | 200K 上下文 | E1 短任务仅 50 completion tokens | **GLM** |

**结论**：**light 首选 `zhipu_glm`，次选 `agnes-3.0-flash`**。核心理由：light 是**最高频**档位，对「限流保护 + 额度可持续」的权重远高于「延迟」，而 light 的输出质量要求最低、对 JSON 零要求 —— 正好避开 GLM 唯一的已知短板。agnes-3.0-flash 延迟更快（8.92s）但无 RPM 保护且额度未知，**适合做 light 的溢出承接位，不适合做主位**。

**遗留风险与缓解**：light 与 standard 首位同为 GLM，共享 30 RPM。缓解 = (a) 令牌桶限流已就位；(b) light 限流时自动落 agnes-3.0-flash；(c) P2-1 模板化直接降低 light 绝对调用量。

### 5.3 决策记录二：两个「占位 provider」的处置

| 对象 | 现状 | 处置 | 理由（含测试依据） |
|---|---|---|---|
| `agnes` provider，model=`agnes-2.0-flash` | E5 **HTTP 500**，且平台 `/v1/models` **已下架该 ID** | **「替换 model」而非删除 provider**：改默认值为 `agnes-3.0-flash`（保留 provider 名 `agnes`、保留 priority=90、保留 `AGNES_MODEL` 环境变量覆盖能力） | ① provider 骨架（key/base_url/槽位）本身有效，坏的是过期模型 ID（F4）；② 已核验 **无任何测试断言 agnes 的 model 字符串**（`grep AGNES tests/` 命中的均为 key 相关），改 model 零测试风险；③ 删除 provider 会减少线上 provider 数量、影响 health 计数类断言；④ 改 model 后它立刻从「空兜底位」升级为 deep 次选，是**唯一能用最小 diff 换来一个新异源模型**的方案（F5 下这条价值极高） |
| `zhipu_glm_45`，model=`glm-4.5-flash` | 与 `zhipu_glm` **同源**（GLM-4.5 已下线自动转发 4.7-Flash），共享同一 Key 与 30 rpm | **保留配置 + `enabled=False` + 标注 `same_upstream_as="zhipu_glm"`**；不进任何 tier 链 | **绝不能删除**，删除会直接打挂 4 条用例：`test_free_providers_sorted_by_priority`（名字列表含 `zhipu_glm_45`）、`test_zhipu_glm_45_loading`（断言 `model=="glm-4.5-flash"`、`priority==1`、`rpm==30`、`free_tier is True`）、`test_zhipu_fallback_model_env_override`、`test_probe_all_success`（断言 `total==3` 且结果顺序含 `zhipu_glm_45`）。<br>而 `enabled=False` 是安全的：`probe()` 遍历 `self._providers` **不按 enabled 过滤**（E3：`probe()` 只做 `targets = list(self._providers)`），`chat()` 则通过 `is_available` 首行 `if not self.enabled: return False` **跳过它** → 恰好实现「不再被 chat 尝试、但仍出现在 probe/health 中」的语义，4 条用例全绿 |

### 5.4 决策记录三：deep 档是否接受 10~17s 延迟 —— 接受，且必须同时修好 25s 预算矛盾

| 问题 | 结论 |
|---|---|
| happy path 延迟 10~17s 可接受吗？ | **可接受**。这是 deep 档的真实成本，且**用户当前感知不到变化**——现状 25s 预算下正常请求本来就要等这个量级。E5 显示 M1 只需 9.70s，比现网 GLM 的典型耗时更短。 |
| 25s 总预算的矛盾要不要纳入需求？ | **必须纳入，且是 P0**。F2 的机制不是「deep 慢一点」，而是**首选一旦慢/超时就把 25s 全部吃光，第 2、3 顺位永远不会被尝试**（`ly77625` provider timeout 默认 30s → `call_timeout=min(30,25)=25s`）。此时「3 个候选」退化为「1 个候选」，与分层的初衷完全相反。 |
| 目标预算 | **60s**（不是 90/120s），逐 provider 超时 20s / 25s / 15s。理由见 §4.3。 |
| 前端影响 | **正常路径无变化**（9.7~16.5s < 现 25s 预算）；仅**降级路径**最坏从 25s 变为 60s，需要前端 loading 文案容忍（不改代码，仅确认超时设置）。 |

### 5.5 全局排除清单（所有档位均不进入）

| 对象 | 原因 | 处置 |
|---|---|---|
| `ttapi` / gpt-4o | E1+E5：HTTP 404 返回 HTML，平台已失效 | `enabled=False`（保留配置，见 Q4） |
| `qwen3.8-27b` | E1+E5：HTTP 502 Cloudflare 错误页 | 不接入 |
| `agnes-2.5-pro-alpha` / `beta` | E1+E5：403，账户 remaining **$0** | 不接入；保留配置，充值后可评估（Q1） |
| `agnes-2.0-flash` | E5：HTTP 500 + 模型已下架 | **改 model 为 agnes-3.0-flash**（§5.3） |
| `agnes-2.5-flash` | 慢 + 推理 token 浪费 + **判断错误** | 从所有 tier 链移除（Q5） |
| `x-deepseek-v4.1-flash` | E4 未调用过 | 补一次 probe 后再定档 |

### 5.6 默认行为等值约束（G3 的核心）

| 约束 | 内容 |
|---|---|
| C1 | `chat()` 的 `task_tier` 默认值必须为 `"standard"` |
| C2 | `standard` 链的顺序必须**严格等于** `[p.name for p in self._providers]`（按 priority 升序的现有顺序，含已 `enabled=False` 的项也不改其位次） |
| C3 | 禁止修改 `_providers` 的排序逻辑与任何 `priority` 数值；禁止删除任何 Provider 对象 |
| C4 | tier 链必须是**独立计算的有序视图**（`_providers_for_tier(tier)`），不得原地重排 `_providers` |

> C3/C4 硬依据：`tests/test_llm_router.py:509-516` 直接断言 `_providers[0]=="zhipu_glm"`、`[1]=="zhipu_glm_45"`、`[2]=="cloudflare_ai"`、`[-1]=="siliconflow"`；`:357` 断言 `zhipu[0].priority == -1`；`tests/test_llm_free_providers.py:124-127` 断言完整名字列表。**任何原地重排或删除都会打挂 214 条 LLM 相关用例**（46+21+3+4+76+49+15）。

---

## 6. 需求池

### P0（Must have）

| ID | 需求 | 验收要点 |
|---|---|---|
| **P0-1** | `LLMRouter.chat()` 增加 `task_tier: str = "standard"`；`_call_llm()` 透传；未知值回落 `standard` + WARN | 不传 tier 时行为与今日一致（C1/C2） |
| **P0-2** | 新增 `_providers_for_tier(tier) -> list[LLMProvider]` 返回独立有序视图，`chat()` 遍历该视图；`_providers` 原地不动 | `_providers_for_tier("standard") == list(self._providers)` 单测断言 |
| **P0-3** | **7 智能体深度决策（`multi_agent_fund.py:542`）走 `deep` 档**：`ly77625/gemini-3.8-flash → agnes/agnes-3.0-flash → zhipu_glm/glm-4.7-flash`；排除 `zhipu_glm_45`、`agnes-2.0-flash`、`agnes-2.5-flash` | health/日志可读出 tier 与实际命中 provider |
| **P0-4** | **修复 deep 预算矛盾（F2）**：deep 档 `total_timeout=60s`；deep 链单 provider 超时 = M1 20s / M3 25s / M2 15s。`standard` 保持 25s、`light` 15s | 首选超时时，第 2 顺位**仍被实际尝试**（单测可断言 `_call_provider` 被调用 2 次） |
| **P0-5** | **修复 `agnes` 过期模型 ID（F4）**：默认 `AGNES_MODEL` 由 `agnes-2.0-flash` 改为 `agnes-3.0-flash`（保留环境变量覆盖） | 线上 `/api/health/llm` 中 `agnes.model == "agnes-3.0-flash"`；probe agnes 返回 200 |
| **P0-6** | **缓存 key 纳入 tier（F3）** | 同 prompt 以 light 调用后，deep 调用**不得**命中该缓存 |
| **P0-7** | **`zhipu_glm_45` 处置（§5.3）**：保留 Provider、保留 model/priority/rpm、置 `enabled=False`、health 增 `same_upstream_as`；禁止进 deep 链 | 4 条相关用例全绿；deep 链不含它 |
| **P0-8** | **不破坏现有回归** | 全套 `pytest tests/` 全绿，用例数不减（基线 2076 个 `def test` / 2136 条含参数化，见 AC1） |

### P1（Should have）

| ID | 需求 | 理由 |
|---|---|---|
| P1-1 | `/api/health/llm` 增 `routing` 段：各 tier 链顺序、可用性、`last_routing`（tier/provider/latency/fallback_count） | US2；匿名可读且不含 api_key |
| P1-2 | `/api/health/llm/probe` 支持 `tier` 参数，对某档链做端到端探活 | 支撑 AC4/AC11 |
| P1-3 | 失效 Provider 置 `enabled=False`（ttapi / agnes-2.5-pro-*） | 避免每次调用白跑超时（ttapi 404 尤其浪费） |
| P1-4 | `_call_llm()` 末尾 `_call_ttapi_direct` 兜底（E3：ttapi 已 404，必然失败）改为「tier 链末位重试」或直接抛错 | 现状是死代码，掩盖真实故障 |
| P1-5 | **补测 `glm-4.7-flash` 的 JSON mode**（线上 probe + 一次 deep prompt 全 schema 验证） | 决定 M2 能否从 deep 末位提升为 deep 次选；当前仅 E2 |
| P1-6 | Provider 级 `max_output_tokens` 字段 | 应对 E1 中 agnes 短任务仅 50 completion tokens 的异常 |

### P2（Nice to have）

| ID | 需求 | 理由 |
|---|---|---|
| P2-1 | light 档「模板化优先」：话术/新闻摘要先走规则模板，LLM 仅润色 | 最高频调用省额度最彻底，同时缓解 GLM 30 RPM 争抢 |
| P2-2 | tier 命中率/延迟/降级次数写入 monitor 指标 | 长期配额规划 |
| P2-3 | 补测 `x-deepseek-v4.1-flash`，评估加入 standard 备选 | F5 下多一个异源兜底价值很高 |
| P2-4 | 评估「gemini-3.8-flash 与 agnes-3.0-flash 双写对比」（记录两者 action/confidence 差异） | 为 Q3 合规切换积累数据 |

---

## 7. 验收标准（可量化、可验证）

### 7.1 回归类

| ID | 标准 | 验证方法 |
|---|---|---|
| **AC1** | `pytest tests/ -q` 全绿；用例总数 ≥ 基线（2076 `def test` / 2136 条），不新增 `skip`/`xfail` | `python -m pytest tests/ -q` 并比对收集数 |
| **AC2** | 不传 `task_tier` 时 Provider 顺序与今日一致 | 单测：`_providers_for_tier("standard") == list(router._providers)`；`test_llm_router.py:509-516` 原断言不改仍绿 |
| **AC3** | `priority` 数值与排序逻辑零改动；无 Provider 被删除 | `git diff` 中 `priority=` 变更行为 0；provider 名字列表断言全绿 |
| **AC4** | §5.3 涉及的 4 条用例（`test_free_providers_sorted_by_priority` / `test_zhipu_glm_45_loading` / `test_zhipu_fallback_model_env_override` / `test_probe_all_success`）全绿 | 单文件执行 `pytest tests/test_llm_free_providers.py -q` |

### 7.2 功能类

| ID | 标准 | 验证方法 |
|---|---|---|
| **AC5** | `deep` 链 = `["ly77625", "agnes", "zhipu_glm"]`；不含 `zhipu_glm_45` / `agnes-2.5-flash` / `ttapi` | 单测断言链顺序 + 排除集 |
| **AC6** | deep 预算生效：mock 首选 provider 卡住 25s，**次选仍被调用**（`_call_provider` 调用次数 ≥ 2）且总耗时 < 60s | 单测（缩短常量版） |
| **AC7** | deep 单 provider 超时参数 = 20s / 25s / 15s，且 `total_timeout == 60` | 单测断言调用参数 |
| **AC8** | 缓存隔离：先 light 后 deep 同 prompt，第二次 `cached == False` 且 provider ∈ deep 链 | 单测 |
| **AC9** | 降级：mock deep 首位返回 429，自动落第 2 顺位并成功 | 单测 |
| **AC10** | 未知 tier 输入不抛异常，回落 standard 并产生 WARN | 单测 |

### 7.3 线上复验（用户可自己跑）

| ID | 标准 | 验证方法 |
|---|---|---|
| **AC11** | `GET /api/health/llm` 匿名 200，`routing.tiers` 含 `deep`/`standard`/`light` 三键，每键为有序 provider 名数组 | `curl -s https://fund-assessment.onrender.com/api/health/llm \| jq '.routing.tiers'` |
| **AC12** | 响应体不含任何 `api_key` / `sk-` 片段 | `curl -s .../api/health/llm \| grep -i "sk-\|api_key"` 无输出 |
| **AC13** | **`agnes.model` 已变为 `agnes-3.0-flash`** 且 `available == true` | `curl -s .../api/health/llm \| jq '.providers.agnes'` |
| **AC14** | `POST /api/health/llm/probe?tier=deep`（带 `ADMIN_TOKEN`）返回 200，列出 3 个候选可连通性与延迟；`agnes` 该项 `ok==true` | `curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" ".../probe?tier=deep"` |
| **AC15** | 触发 deep 业务后 `routing.last_routing` 显示 `tier=deep` 且 `provider ∈ {ly77625, agnes, zhipu_glm}`；**连续 5 次 ≥4 次首选命中** | 连续请求 5 次后读 health |
| **AC16** | 时延：deep 5 次样本 P95 ≤ 30s（happy path 预期 10~17s）；standard P95 ≤ 25s；light P95 ≤ 15s | health latency 统计或日志打点 |
| **AC17** | deep 端到端 JSON 解析成功率 = 100%（5/5），且 `analysts` 数组长度 == 7 | 观察 `_parse_fund_analysis_response` 无异常、`analyst_count == 7` |

> 复验前提：E5 的 9.70s / 16.45s 是**单次模型调用**口径；AC16 测的是**端到端**（含数据抓取、prompt 组装）。若端到端 P95 超 30s，应优化上下文裁剪而非放宽预算。

---

## 8. 待确认问题（仅列需要用户决策的）

| # | 问题 | 选项 | 默认建议 | 状态 |
|---|---|---|---|---|
| **Q1** | Agnes 账户 remaining **$0**，`agnes-2.5-pro-alpha/beta` 全 403。是否充值把 pro 档纳入 deep 首选？ | A. 不付费／B. 付费 | **A**（现有 M1/M3 已满足 deep） | 待定 |
| **Q2** | deep 档延迟接受度：happy path 10~17s 已确认可接受；**降级路径最坏等待**是否接受 60s？ | A. 60s／B. 保持 25s（等于放弃降级链）／C. 90s+ | **A（60s）** | 待定 |
| **Q3** | `ly77625` 是免费第三方中转，**数据合规 E4**。deep 首选意味着持仓/宏观上下文发往该中转。是否接受？ | A. 接受（9.70s + 最少 token）／B. **agnes-3.0-flash 优先**（16.45s，同样 E5 全 schema 通过），gemini 降为第 2 | **B**——两者 E5 证据等价（均全字段齐、7 分析师、JSON 严格），既然能力打平，就应选依赖面更可控的一方；代价仅 +6.75s（仍在 60s 预算内） | **需你拍板（唯一影响映射表顺序的决策）** |
| **Q4** | 失效 Provider（ttapi / qwen3.8-27b / agnes-pro）删除还是 `enabled=False` 保留？ | A. 删除／B. 保留并禁用 | **B**（删除会触发名字列表类断言；且额度恢复可一键启用） | 待定 |
| **Q5** | `agnes-2.5-flash` 是否从所有档位移除？ | A. 移除／B. 仅 light 兜底 | **A**（22.36s + 2123 tokens 且趋势答错） | 待定 |
| **Q6** | 是否需要把 deep 与 standard 的 **GLM 30 RPM 配额**做隔离（例如 standard 限 20 RPM、light 限 10 RPM）？ | A. 不分（共享）／B. 按档分配 | **A 先观测**：P1-1 的 `last_routing` 上线后按实测数据再决定 | 待定 |

---

## 9. 交给架构师的 6 条硬约束（不可协商）

1. `chat()` 新增参数必须**默认 `standard` 且 standard 链 == 现有 priority 顺序**；否则 2136 条回归必然失败。
2. **禁止改动 `_providers` 排序、`priority` 数值、以及删除任何 Provider 对象**（`test_llm_router.py:509-516`/`:357` + `test_llm_free_providers.py:124-127` 硬断言）。
3. deep 档必须同时抬高 `total_timeout`（60s）并**收紧单 provider 超时**（20/25/15s），否则 `ly77625` 的 30s 默认超时会把 25s 预算一次吃光（F2），3 个候选退化为 1 个。
4. 缓存 key 必须加 tier 维度，否则 light 的低质答案会污染 deep（F3）。
5. `zhipu_glm` 与 `zhipu_glm_45` **共享同一上游与同一 30 rpm 配额**（F1），不得当作两条独立冗余链路做可用性承诺；后者只能 `enabled=False`，**不能删**（§5.3）。
6. `agnes` 的 `model` 必须从 `agnes-2.0-flash` 改为 `agnes-3.0-flash`（F4，现网 500）；这是本期唯一能用「1 行配置」换来一个新异源模型的改动，优先级等同 P0。
