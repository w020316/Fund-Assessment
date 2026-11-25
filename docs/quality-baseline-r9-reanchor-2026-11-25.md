# r9 轮 · 基线重锚（2026-11-25，增量记录）

**重锚原因**：阶段 0 基线锚点 `4245af1`（2026-11-24）后新增 11 个 commit（S9-16~S9-22 UI 收敛 + 汇金归类修复 + S9 系列后端修复），阶段一审查与阶段五复测需以当前 HEAD 为唯一参照。

## 一、重锚基线（当前 HEAD `d117288` 实测）

| 项 | 值 | 对比阶段 0 基线（`4245af1`） |
|---|---|---|
| 测试收集 | 3,429 | 3,406（+23，S9 系列配套测试） |
| 全量回归 | **3,427 passed / 2 skipped（194.08s）**，全绿 | 3,405 passed / 1 skipped（89.51s）；耗时翻倍源于新增测试与覆盖统计，无失败项 |
| skipped 说明 | 均为命名含 `_skipped` 的设计内跳过（如 test_pcf_skipped_when_net_cash_zero），非环境性跳过 | — |
| 覆盖率 | **88%**（19,593 语句 / 2,258 未覆盖） | 88%（19,576 / 2,283），语句 +17、未覆盖 −25，门禁 78% 达标 |
| ruff | **1 error**：`web/api.py:13` F811（`Request` 重复导入） | 5 errors（全在 scripts/）——scripts 5 项已由 9c9c205 治理清零，但 f674090 在 web/api.py 引入 1 个新 F811 |
| compileall | 0 errors（src + web + tests） | 0 errors，一致 |
| SPA 页面容器 | 27 个唯一 `data-page`（index.html 原始计数 35 含重复模板 `${name}`/`${st5Entry.parent}`） | 基线 35 为原始计数，本轮补唯一值口径 |
| 路由装饰器 | 154 | 159（差异为统计脚本对 mcp/重定向路由的口径，非功能删减，`/openapi.json` 精确值阶段四补测） |

## 二、线上指纹（2026-11-25 实测）

- `https://fund-assessment.onrender.com/api/health` → **200**，`version: d117288`，`status: ok`，`checks: {data_source: true, ai: true}`
- 结论：当前 HEAD 已部署上线，本地工作区 = 线上版本，零漂移。

## 三、解释器环境说明（如实记录）

- 原登记 `.venv` 已损坏：其 `pyvenv.cfg` 指向已卸载的 `Python310`，venv Python 无法启动。
- 实际可用解释器：`D:\dev-tools\Python312\python.exe`（本轮全部实测均基于它）；`start.ps1` 的探测列表已优先使用该路径，启动链路不受影响。
- 处置：`.venv` 清理列入阶段一 P3 清单，不在基线重锚阶段执行。

## 四、承阶段 0 的待基线项（不变）

Firefox / Safari 兼容、pip-audit 依赖 CVE、生产按 IP 限流分桶端到端——原因不变（本机仅 Chromium 内核 / pip 出网受限 / 需双公网出口），本轮阶段三再尝试 Firefox。
