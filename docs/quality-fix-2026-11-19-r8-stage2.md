# 第八轮质量评估 · 阶段二：问题闭环修复与缺失功能补全

**执行日期**：2026-11-19｜**基线**：[quality-baseline-2026-11-19-r8.md](file:///D:/xm/wz/Fund-Assessment/docs/quality-baseline-2026-11-19-r8.md)（3,337P / 2S / 0F，覆盖率 89%，ruff 0）
**阶段一清单**：[quality-review-2026-11-19-r8-stage1.md](file:///D:/xm/wz/Fund-Assessment/docs/quality-review-2026-11-19-r8-stage1.md)（P0=0 / P1=0 / P2=2 / P3=10）

## 修复记录

### Q1（P2）契约层未接入生产路径，声明过度 → 已修复 ✅
- **原始描述**：`SourceRouter` / `QuoteContract` / `KlineContract` 仅被测试引用；`data_source_v2` 门面仍返回 provider 原始 dict，线上接口不受契约层约束，但文档称「缺失值语义已机制化」。
- **技术方案**：在 [data_guard/__init__.py](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/__init__.py) 与 [contract.py](file:///D:/xm/wz/Fund-Assessment/src/core/data_guard/contract.py) 增加「接入状态」说明：M1/M2 仅路由层可用；生产门面仍返回原始 dict；M5 出口统一 normalize 需行为变更评审。**零业务语义改动**。
- **提交**：`07e46aa`
- **验证**：全仓检索确认生产引用为 0；ruff 0 + compileall 0；全量测试与基线一致。

### Q2（P2）异动「全部分钟序列不可用」被展示成「无异动」→ 已修复 ✅
- **原始描述**：候选池成功（scanned=16）但 16 只分钟序列全失败时，`_anomaly_meta` 因 `items=[]` 仍给 quality=85 且无 `degraded_reason`；前端据此渲染「已扫描 16 只 · 5 分钟内涨跌幅均未达阈值」——把数据故障表述成结论。
- **技术方案**：
  - 后端 [market.py](file:///D:/xm/wz/Fund-Assessment/web/routes/market.py) 新增分支：`scanned>0 && unavailable>=scanned && items 为空` → `quality=0`、`degraded_reason="分钟序列全部不可用（上游行情接口异常），本次无法判定异动"`，`degraded=true`；
  - 前端 [index.html](file:///D:/xm/wz/Fund-Assessment/web/static/index.html) `loadStockAnomaly` 同条件空态改为「异动数据暂不可用」，保留「部分缺失仍可判定无异动」的原语义；
  - 路由 docstring 同步收紧「scanned>0 即无异动」的过宽表述。
- **提交**：`b4d7091`
- **验证**：新增 3 条用例——全不可用降级、部分缺失维持原结论、前端分支顺序契约；定向 160 passed / 1 skipped；全量回归见下表。

## 环境假失败声明（诚实记录）

首次全量回归出现 **1 例失败**：`tests/test_strategy_routes.py::TestAuth::test_validate_passes_with_correct_token` → `RuntimeError: can't start new thread`。该用例与本次改动无交集（不涉及异动/契约层），**隔离复测 1 passed**；判定为 Windows 线程资源瞬时耗尽的环境假失败，已复跑确认（结果见下表）。按红线**未修改任何断言或跳过该用例**。

## 收口指标（实测）

| 指标 | 阶段 0 基线 | 阶段 2 收口 | 变化 |
|---|---|---|---|
| 全量测试 | 3,337P / 2S / 0F | **3,340P / 2S / 0F**（192.13s，复跑） | +3 用例 |
| ruff | 0 | **0** | 持平 |
| compileall | 0 | **0** | 持平 |
| P0/P1 修复率 | — | **100%**（P0=0，P1=0） | 达标 |
| P2 修复率 | — | **100%**（2/2） | 达标 |
| P3 | 10 条 | 10 条入 backlog（Q3–Q12） | 不阻塞 |

**多场景验证**：每项修复执行「定向测试 + 全量回归 + ruff + compileall」；Q2 另覆盖全不可用/部分缺失两个分支与前端源码契约。

> 本报告由 AI 协作生成，关键决策请由工程负责人复核。
