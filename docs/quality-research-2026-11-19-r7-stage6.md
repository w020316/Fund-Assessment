# 第七轮质量评估 · 阶段六：新模块调研与立项——多源行情数据治理薄契约层（QF-DataGuard）

**执行日期**：2026-11-19｜**性质**：正式调研文档（技术评审通过后方可启动编码）
**选题依据**：承接 r6 阶段六结论（多源数据治理层，建议自建薄契约层）+ r7 实证痛点——`src/core/data_source_v2.py` 已达 **2,030 语句（全项目最大文件）**、覆盖率 72%、6 源 3 级降级逻辑与业务解析耦合在单文件内，r6~r7 连续两轮在该文件修「缺失值伪造 0」类缺陷（d8b4f3d 等），结构治理收益明确。

---

## 目录

1. 引言（背景与问题定义）
2. 竞品调研（5 项目对比 + SWOT）
3. 方案详情（模块划分 / 接口定义 / 架构 / 技术栈 / 核心实现思路）
4. 风险评估与应对
5. 里程碑计划

## 1. 引言

### 1.1 问题定义
当前 6 个数据源（东方财富/腾讯/新浪/mootdx/akshare/tushare）的「取数→解析→校验→降级」全部内联在 `data_source_v2.py`（2,030 语句）。三个结构性问题：
- **契约分散**：每个源的解析函数各自为政，字段缺失语义不统一（r6/r7 连续修复「缺失伪造 0」缺陷均源于此）
- **测试成本高**：降级矩阵组合爆炸，单文件 565 条未覆盖语句 ≈ 全项目 22%
- **变更风险集中**：任何单源改动都在全项目最大文件内进行，review 面大

### 1.2 目标
在不改变既有 API 路径与函数签名（项目硬约束）的前提下，把「源适配 + 统一契约 + 降级编排」拆为可独立测试的薄层，**预期 data_source_v2.py 收敛至 <800 语句、新层覆盖率 ≥90%、全局覆盖率 ≥89%**。

## 2. 竞品调研

### 2.1 对比表（star 数为 2026-11-19 实测核实，附来源）

| 项目 | Stars（核实口径） | 许可证 | 活跃度（近 3 月） | 数据层设计 | 部署复杂度 | 与本项目匹配度 |
|---|---|---|---|---|---|---|
| [microsoft/qlib](https://github.com/microsoft/qlib) | **50,387**（GitHub API 实测） | MIT | 高（2026-11-04 push） | 重数据层：自有二进制 bin 格式 + Expression 引擎，离线为中心 | 高（需数据转存管道） | ★★☆ 理念可借鉴（字段 schema 严格），实现过重 |
| [vnpy/vnpy](https://github.com/vnpy/vnpy) | **≈46,226**（repo.uno 2026-11-04 快照；社区口径 44.4k@2026-08，取整区间 44k~46k） | MIT | 高（2026-10-27 commit） | Gateway 抽象（每源一个适配器 + 统一事件类型） | 高（完整交易框架，组件耦合多） | ★★☆ Gateway 分层思路契合，但框架级引入成本高 |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | **36,050**（GitHub API 快照；注：快照时间戳显示 2025-02，活跃度待复核） | 自定义（NOASSERTION） | 待复核 | Provider 路由 + Pydantic 结果模型 + @venv 命令注册，**契约层最完整** | 中（Platform 可独立部署） | ★★★★ Pydantic 契约 + Provider 路由与本方案最接近，但体量大、自定义许可证 |
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | **31,620**（GitHub API 实测） | LGPL-3.0 | 极高（2026-11-17 push） | Rust 内核 + Python 适配器，DataEngine 统一消息类型 | 高（Rust 工具链） | ★★☆ 架构优秀但 Rust 内核不符合本项目纯 Python + Render 约束 |
| [zvtvz/zvt](https://github.com/zvtvz/zvt) | **4,338**（GitHub API 实测） | MIT | 中（2026-07-01 push，近 3 月无提交） | 统一 ORM Schema + 每源 recorder 落库 | 中（需数据库） | ★★★ Schema 统一思想好，但强依赖落库（本项目 Render Free 磁盘非持久） |

> 核实方法：GitHub REST API `/repos/{owner}/{repo}` 直取（OpenBB/qlib/nautilus/zvt）；vnpy 因 API 限流采用 repo.uno 聚合快照 + 社区口径交叉，已如实标注差异区间。

### 2.2 SWOT（自建薄契约层方案）

| | 内容 |
|---|---|
| **S 优势** | ① 契约集中后「缺失值标 null」红线可在层内机制化（Pydantic 校验 + 拒绝 0 值兜底），把历轮人工修缺陷变成结构性防御；② 单源适配器独立测试，降级矩阵可穷举；③ API 路径/签名零变更，部署零风险 |
| **W 劣势** | ① 一次性重构 ~1,200 行，回归窗口长；② 适配器样板代码增多（每源一个文件） |
| **O 机会** | qlib 的严格 schema、OpenBB 的 Provider 路由、vnpy 的 Gateway 分层均有成熟范式可低风险借鉴（仅借思想不引依赖）；新源（如国际市场扩展）接入成本从改 2,030 行文件降为新增 1 个适配器 |
| **T 威胁** | ① akshare 上游接口频变，适配器需要持续维护（现状已如此，不新增风险）；② 重构期间双路径并存易产生漂移——用「快照测试锁行为」缓解 |

### 2.3 量化评分（1-5）

| 维度 | qlib | vnpy | OpenBB | nautilus | zvt | 自建薄层 |
|---|---|---|---|---|---|---|
| 功能匹配 | 2 | 2 | 4 | 3 | 3 | **5** |
| 部署复杂度（越低越好） | 2 | 2 | 3 | 2 | 3 | **5** |
| 许可证友好 | 5 | 5 | 2 | 3 | 5 | **5** |
| 活跃度 | 5 | 5 | 3 | 5 | 2 | **5**（自有仓库） |
| 引入成本（越低越好） | 1 | 1 | 2 | 1 | 2 | **5** |
| **合计** | 11 | 11 | 14 | 14 | 15 | **25** |

**结论**：不引框架，自建薄契约层（qf-dataguard），借鉴 OpenBB 的「Provider 路由 + 结果模型」与 vnpy 的「每源一适配器」分层。

## 3. 方案详情

### 3.1 模块划分与接口定义

```
src/core/data_guard/
├── contract.py      # 统一契约: Pydantic 模型(QuoteResult/KlineResult/NewsResult... 字段级 None 语义)
├── provider_api.py  # Provider 协议: fetch_xxx(code, *, timeout) -> 原始 dict | None(明确缺失)
├── providers/       # 每源一适配器(纯解析, 无降级逻辑): eastmoney.py / tencent.py / sina.py / mootdx.py / akshare_p.py / tushare_p.py
├── router.py        # 降级编排: 源优先级 + 熔断(复用 llm_router 模式) + 缺失传播
└── validator.py     # 质量校验: 复用 data_validator, 输出 _meta(来源/质量分/degraded)
```

关键接口（示意）：
```python
class QuoteContract(BaseModel):
    code: str; price: float | None; prev_close: float | None  # 缺失=None, 禁 0 兜底
    volume: float | None; degraded: bool = False; source: str; fetched_at: datetime

class Provider(Protocol):
    name: str
    priority: int
    def fetch_quote(self, code: str) -> QuoteContract | None: ...
```

### 3.2 架构图（含部署形态）

```
[web/routes] ──> [data_guard.router] ──> [providers/*] ──> [上游 6 源]
                      │                        │
                      ├─ 熔断/优先级            └─ 原始 dict
                      └─> [contract 校验] ──> [validator + _meta] ──> [TTL 缓存] ──> 路由响应
部署形态: 单进程 Render Free (512MB) 内同层模块, 无新进程/新依赖, 冷启动增量 <50ms(懒加载)
```

### 3.3 技术栈选型理由
纯 Python 3.12 + Pydantic v2（已有依赖）；**零新依赖**（Render Python 3.14 + 预编译 wheel 约束，不引入 Rust/重框架）；复用既有 cache/auth/熔断模式。

### 3.4 核心实现思路
1. **绞杀者模式迁移**：router 先作为 `data_source_v2` 的门面（内部转发），路由/签名不变；逐函数把解析体搬入 provider，快照测试锁行为（输入样本 → 输出逐位一致）
2. **缺失语义机制化**：contract 层 `model_validator` 拒绝「关键字段 0 值兜底」，`_opt_float` 模式（r6 修复产物）下沉为层内规范
3. **降级矩阵测试**：6 源 × 3 级组合用参数化测试穷举，目标把 data_source_v2 现有 565 条未覆盖语句转移为薄层 ≥90% 覆盖

### 3.5 风险与应对

| 风险 | 等级 | 应对 |
|---|---|---|
| 迁移期行为漂移 | 高 | 每迁移一个函数跑快照对比 + 全量回归；CI 覆盖率门禁 78% 不降 |
| 上游接口变更（akshare） | 中 | 适配器单文件隔离 + 熔断降级既有机制兜底 |
| Render Free 内存 | 中 | 无新依赖/新进程，预估增量 <10MB；回归阶段实测 RSS |
| 大文件搬迁引入回归 | 中 | 绞杀者模式分 6 个 PR 串行（每源一 PR），任意一步可回退 |

### 3.6 里程碑计划（不含日期，按验收标准）

| 里程碑 | 交付内容 | 验收标准 |
|---|---|---|
| M1 契约层 | contract.py + validator 接入 + 快照测试基建 | 快照测试全绿；现有 API 输出逐位一致 |
| M2 双源试点 | eastmoney/tencent 两适配器 + router 门面 | 这两源相关用例覆盖率 ≥90%；全量回归绿 |
| M3 全源迁移 | 其余 4 源适配器 + data_source_v2 收敛为门面 | data_source_v2 <800 语句；全局覆盖率 ≥89% |
| M4 机制化收口 | 缺失值 0 兜底在 contract 层结构性拒绝 | 新增专项测试证明 0 值兜底不可达；线上指纹验证 |

### 3.7 执行进展与后续路径（2026-11-19 补记）

- **已完成**：M1 契约层、M2 双源试点、M3 五处迁移、M4-A~M4-F（榜单/研报/涨停池/北向、交易日/龙虎榜、资金流/融资融券/大宗/股东户数/双源新闻、财务快照/公司资料/基金实时与历史、板块群 10 函数、市场情绪与广度/分钟线、全球面板 8 处、异动扫描候选池）。
- **实绩**：`data_source_v2.py` 2,030 → **1,397 语句**（−31%），累计迁出 **47 处**解析体；快照测试全程锁行为，无线上漂移事故。
- **目标修正说明**：`<800 语句` 的验收口径**无法通过继续迁 provider 达成**——剩余语句集中在门面编排、缓存、健康度判定与指标计算，已不属于「源适配」范畴。
- **后续路径**：若要继续压降，须按职责拆为独立模块（指标计算 / 异动判定 / 全球门面），会触及模块边界与既有 patch 契约，风险与收益均高于 provider 迁移，**建议单独开一轮评估（先出拆分方案与影响面清单）后再决定是否执行**。

> 本文档由 AI 协作生成，经技术评审通过后方可启动编码。
