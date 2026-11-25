# r9+ 功能迭代交付记录（2026-11-25，roadmap N-1 落地）

**范围**：承 `qa-final-2026-11-25-r9.md` roadmap 首选项 N-1（预测准确率周报）+ 遗留风险 R-1（CI 依赖 CVE 扫描）。
**代码终点**：`ba69c23`（已 push 上线，指纹验证见文末）。

## 一、N-1 预测准确率周报

### 交付内容
| 层 | 内容 |
|---|---|
| 服务层 | [prediction_service.get_accuracy_weekly](file:///D:/xm/wz/Fund-Assessment/src/core/prediction_service.py)：近 N 个自然周（周一起始）聚合 `verification_results`；口径与 `compute_accuracy` 完全一致（同一验证行双通道；prediction 的 llm 用同一实际涨跌按 LLM 方向重判）；样本 <5 → `hit_rate=None`；空周如实 `total=0` 不掩盖断档；**纯只读不落库**（承 QF3-07 约定） |
| 路由 | `GET /api/prediction/accuracy/weekly?weeks=1..12`（公开读，越界 422） |
| 前端 | monitor 页「准确率周报」卡片（[prediction.js](file:///D:/xm/wz/Fund-Assessment/web/static/js/prediction.js)）：每周一行 = 周标签 + 预测规则命中率条形（≥50% 青 / <50% 橙）+ AI 口径 / 推荐双口径数字；样本不足显式标注；640px 断点两行降级布局 |
| 测试 | [test_prediction_accuracy_weekly.py](file:///D:/xm/wz/Fund-Assessment/tests/test_prediction_accuracy_weekly.py) 6 用例：空库零副作用 / 跨周分组与命中率 / llm 重判与 rule 分化 / 路由公开读 / 只读二次断言 / weeks 边界 |

### 验证（全部实测）
- 预测族测试 27 passed；**全量回归 3,471 passed / 2 skipped**；覆盖率 **89%**（19,645 语句）不降；ruff 0 error；compileall 0
- 本地浏览器冒烟：8 周渲染正常、空周如实"样本不足"、0 JS 错误（截图 `.screenshots/weekly-card-smoke.png`）
- 端点实测：`weeks=8` → 8 周、周末界正确截至今日（`10-05~10-11 … 11-23~11-25`）

### Git 记录偏差（如实说明）
`ba69c23` 因 PowerShell 多行 commit 消息引号断裂，后端（服务层+路由+6 测试）与前端+CI 合并为**单个 commit**，且落库消息为前端+CI 版本，后端实现描述未入消息体。代码完整、消息偏差在此文档如实补记（main 已推送，不做历史改写）。

## 二、R-1 CI 依赖 CVE 扫描

[ci.yml](file:///D:/xm/wz/Fund-Assessment/.github/workflows/ci.yml) 新增 pip-audit 步骤（`pip-audit -r requirements.txt`，**report-only 不阻断**——先观察一轮白名单噪声，数据稳定后再决定是否升级为 blocking 门禁）。承 r9 遗留风险 R-1，"pip-audit 待基线"自本轮起由 CI 每次推送自动产出。

## 三、roadmap 更新

| 项 | 状态 |
|---|---|
| N-1 预测准确率周报 | ✅ **本轮交付** |
| R-1 pip-audit 入 CI | ✅ 本轮交付（report-only） |
| N-4 缠论+信号联合研判卡 | 下轮候选 |
| N-2 持仓快照曲线 / N-3 三问通过率统计 | backlog |
