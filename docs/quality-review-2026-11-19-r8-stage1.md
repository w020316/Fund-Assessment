# 第八轮质量评估 · 阶段一：增量代码审查与功能完整性排查

**执行日期**：2026-11-19｜**HEAD**：`6765107`｜**锚点**：`ab01cc9`
**范围**：7 个 DataGuard 绞杀迁移提交（13 文件 / +4,453 −1,605）+ 高危模块交叉抽查 + 安全专项 + 功能矩阵对标
**方法**：ruff / compileall / 全量 pytest / coverage 全量映射 + 6 个 provider 与门面逐函数对照（快照测试为证）+ 下游消费方（路由/前端）追踪
**基线**：[quality-baseline-2026-11-19-r8.md](file:///D:/xm/wz/Fund-Assessment/docs/quality-baseline-2026-11-19-r8.md)

---

## 一、静态工具面（实测）

| 项 | 结果 |
|---|---|
| 全量测试 | **3,337 passed / 0 failed / 2 skipped / 150.66s** |
| 覆盖率 | **89%**（18,797 语句 / 2,027 未覆盖）；`data_source_v2` 82%、`data_guard/*` 85%–100% |
| ruff | **0 errors** |
| compileall | **0 errors** |
| 迁移提交测试配套 | 7 个提交**逐一**附带 `tests/test_data_guard.py` 增量（77~365 行/提交），共 1,679 行快照测试 ✅ |

## 二、DataGuard 专项审查结论

逐文件核对「取数 + 解析」迁出后的行为等价性与契约兼容性：

| 文件 | 核对项 | 结论 |
|---|---|---|
| `providers/tencent.py` | 字段索引（3/4/5/31~38/44~48）、GBK 解码、基金 nav<=0 丢弃、涨跌幅 >20% 降级、A股指数复合币种置空、`_ds2` 属性调用 | ✅ 与迁移前逐位一致 |
| `providers/eastmoney.py` | K 线字段顺序、clist po 反转、资金流「klines 空→None 不 fallback」、融资融券单位、大宗溢价 ×100、lsjz 分页上限、m:100 缩放、f22 双方向 | ✅ 一致；空结果语义未错并入 fallback |
| `providers/sina.py` | K 线 jsonp 解析、板块资金 fenlei、newFLJK 正则、分钟跳空不插值、涨跌幅榜降级标注 | ✅ 一致 |
| `providers/mootdx.py` | bars 解析、财务快照全 None → 下一源、client 获取留在门面 | ✅ 一致 |
| `providers/ths.py` | 涨停池 filter 市场集合式、封板时间 unix/HH:MM:SS 双格式、北向停更语义 | ✅ 一致 |
| `contract.py` / `router.py` | 0 价结构性拒绝、extra=forbid、degraded 标记、全失败返回 None | ✅ 自身正确（接入状态见 Q1） |
| 门面 `data_source_v2.py` | 缓存键/TTL、健康度、来源标记、降级顺序、锁外 IO、`_ds2.xxx` patch 契约 | ✅ 未发现行为漂移 |
| 循环导入 | provider 顶层 import 门面 + 门面懒加载 provider | ✅ 无环（导入顺序实测通过） |

## 三、问题清单

### P0 / P1

**无。** 本轮基线与全量测试均绿，未发现数据错误、越权或线上不可用问题。

### P2（2 条，阶段 2 必须闭环）

**Q1. 契约层未接入生产路径，「缺失值语义已机制化」属过度声明**
- 位置：[src/core/data_guard/__init__.py:1-6](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/__init__.py#L1-L6)、[contract.py:90-95](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/contract.py#L90-L95)、[router.py:13](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/router.py#L13)
- 事实：`SourceRouter` / `QuoteContract` / `KlineContract` 仅被 `tests/test_data_guard.py` 引用；`data_source_v2` 全部门面仍直接调用 provider 的 `*_raw` 原始函数并返回 **dict**（0 兜底语义不变）。即：契约层目前是**未被生产调用的试点层**。
- 影响：r7 报告「缺失值语义已机制化」会让后续评估误以为红线已结构性生效；线上接口仍可能出现价格 0 兜底（由各自 `_sanitize`/`_opt_float` 局部兜住，不代表机制生效）。
- 修复建议（阶段 2）：不改业务语义的前提下，在契约层与门面文档中显式标注「接入状态：M1/M2 仅路由层可用，生产门面仍为原始 dict；M5 接入计划」，并把「M5 契约接入」列入 backlog；同步在 r8 报告更正口径。
- 判定：**文档/声明真实性问题，非功能回归**。

**Q2. 异动端点在「候选均取不到分钟序列」时仍按成功口径展示**
- 位置：[web/routes/market.py:1264-1265](file:///D:/xm/wz/Fund-Assessment/web/routes/market.py#L1264-L1265)（注释断言 `scanned>0` 即「确实没有异动」）、[market.py:1303-1304](file:///D:/xm/wz/Fund-Assessment/web/routes/market.py#L1303-L1304)（`items` 空仍给 85 分、无 reason）、[index.html:4845-4853](file:///D:/xm/wz/Fund-Assessment/web/static/index.html#L4845-L4853)（空态直接显示「无符合条件的异动」）、[data_source_v2.py:2575-2581](file:///D:/xm/wz/Fund-Assessment/src/core/data_source_v2.py#L2575-L2581)
- 复现路径（代码级确定）：候选池取到 16 只 → 16 只分钟序列全失败 → `items=[]`、`scanned=16`、`unavailable=16` → `_anomaly_meta` 走 `quality=85 / reason=""` → 前端渲染「已扫描 16 只 · 5 分钟内涨跌幅均未达阈值」。用户看到的是「无异常」，实际是「无法判定」。
- 修复建议（阶段 2）：`_anomaly_meta` 增加 `scanned>0 且 unavailable>=scanned 且 items 为空` 分支 → `quality=0`、`degraded_reason="分钟序列全部不可用…"`；前端该情形改为「数据暂不可用」空态；补后端单测 + 前端源码契约断言。

### P3（建议，入 backlog）

| # | 描述 | 位置 |
|---|---|---|
| Q3 | 公司资料请求字段未含 `f168`/`f50`，却读取它们 → `turnover_rate`/`volume_ratio` 恒 0（当前无前端消费） | [eastmoney.py:447](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/providers/eastmoney.py#L447)、[:464-465](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/providers/eastmoney.py#L464-L465) |
| Q4 | 腾讯板块占位 stub 先发一次 5s 超时请求再恒返回 `[]`（降级链末端的无效 IO） | [tencent.py:287-302](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/providers/tencent.py#L287-L302) |
| Q5 | 新闻发布时间用本机时区（Render 为 UTC，展示偏移 8h） | [sina.py:145](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/providers/sina.py#L145) |
| Q6 | 冗余导入：`except` 内 `import loguru`；`import json as _json`（顶层已导入） | [tencent.py:99](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/providers/tencent.py#L99)、[sina.py:59](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/providers/sina.py#L59) |
| Q7 | 未被引用的 `TencentQuoteListProvider` 与两个 Protocol（无 isinstance 消费） | [tencent.py:553-565](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/providers/tencent.py#L553-L565)、[provider_api.py](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/provider_api.py) |
| Q8 | 基金历史窗口首行缺前值时被填 `change_pct=0.00%`（与「缺失显示 —」红线有张力；已被既有测试锁定，改动需同步语义评审） | [data_source_v2.py:1537](file:///D:/xm/wz/Fund-Assessment/src/core/data_source_v2.py#L1537) |
| Q9 | 大宗交易 fallback 先请求行情再返回 `[]`（无效 IO） | [data_source_v2.py:1226-1236](file:///D:/xm/wz/Fund-Assessment/src/core/data_source_v2.py#L1226-L1236) |
| Q10 | mootdx 财务快照 docstring「异常向上抛出前先就地吞掉」自相矛盾 | [mootdx.py:39-40](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/providers/mootdx.py#L39-L40) |
| Q11 | 美股/港股实时查询 `codes` 未做格式与长度约束，直接作为缓存键与上游查询串 | [global_market.py:184](file:///D:/xm/wz/Fund-Assessment/web/routes/global_market.py#L184)、[:218](file:///D:/xm/wz/Fund-Assessment/web/routes/global_market.py#L218) |
| Q12 | 全球行情 Pydantic 模型价格字段非可选，缺失经 `_safe_float` 变 0.00（当前仅由「raw 非空」兜住） | [global_market.py:42-79](file:///D:/xm/wz/Fund-Assessment/web/routes/global_market.py#L42-L79) |

## 四、安全专项

| 检查项 | 结论 |
|---|---|
| 写端点鉴权 | `require_admin` 共 **105 处 / 15 个路由文件**；本轮 7 提交未新增/修改任何路由 |
| 危险调用 | 全仓无 `eval/exec/pickle/subprocess(shell=True)/verify=False`（仅 1 处注释命中） |
| 密钥硬编码 | `sk-*` / 硬编码 api_key 扫描 **0 命中** |
| SSRF | 出站主机均为常量；用户输入仅进入 query/param。敏感东财 `filter` 端点已有 `_is_stock_code` 422+纵深防御 |
| XSS | 本轮前端 **0 改动**；index.html `innerHTML` 254 处为存量 backlog（r7 已复扫新 JS 0 命中） |
| CORS | Render 白名单限定本域，未回退为 `*` |

## 五、功能完整性（矩阵对标结论）

| 检查项 | 结论 |
|---|---|
| 本轮新增能力 | 核心指数补 A 股（`c45cc67`）、A 股指数币种复合串修复（`cf2c226`）——均为**补全既有全球面板**，无新缺口 |
| 数据契约 | M1/M2 契约层与路由试点完成，但**生产未接入**（Q1） |
| 与 r7 backlog 对照 | 预测周报 N-1、monitor 前端入口、innerHTML 存量、留白 4px 均**未变化**，继续挂起 |
| 新增缺口 | **证据链缺口 1 项**：DataGuard 的「缺失值机制化」尚未落到生产路径（Q1） |

## 六、阶段 2 必办项

1. **Q1**（P2）：更正契约层/门面接入状态声明 + r8 文档口径，建立 M5 接入 backlog（不改业务语义）
2. **Q2**（P2）：异动「全部分钟不可用」降级态闭环（后端 meta + 前端空态 + 三条测试）
3. 回归：全量 pytest（3,337 基线不退化）+ ruff 0 + compileall 0
4. P3 共 10 条入 backlog，不在本阶段阻塞

---

> 本报告由 AI 协作生成，关键决策请由工程负责人复核。
