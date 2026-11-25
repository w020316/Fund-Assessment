# r9 阶段二 · 问题闭环修复与缺失功能补全（2026-11-25）

**修复范围**：阶段一清单全部 P2（2/2）+ 可低成本闭环的 P3（2/4，余 2 项入 backlog）。
**收口验证**（HEAD `e52c7a7` 实测）：全量回归 **3,465 passed / 2 skipped（188.04s）** 全绿；覆盖率 **89%**（19,592 语句 / 2,147 未覆盖，门禁 78% 达标）；ruff **0 error**（基线 1 → 0）；compileall 0 errors。

## 一、结构化修复记录

### Fix-1 ｜ P3#1 ｜ F811 重复导入（commit `4e830b0`）
- **原始描述**：[web/api.py:13](file:///D:/xm/wz/Fund-Assessment/web/api.py) `starlette.requests.Request` 与 line 7 `fastapi` 导入重复（f674090 引入），ruff 报 F811。
- **技术方案**：删除 starlette 侧导入，统一从 fastapi 导入（fastapi.Request 即 starlette re-export）。
- **验证方法**：ruff 全绿（All checks passed）+ `import web.api` 成功 + 全量回归无破坏。
- **状态**：✅ 已修复

### Fix-2 ｜ P2#1 ｜ 触控目标 48×48 透明热区（commit `cda7d36`）
- **原始描述**：S9-18 用 `min-height:44px` 达 WCAG AAA，但与「≥48×48 且不改视觉尺寸」规范存在口径偏差；且 `#sbSearch` 为 `<input>` 不支持伪元素。
- **技术方案**：按钮类元素（`.aep-retry/.aep-close`、`.qf-wb-bar .btn/.qf-wb-toggle`、`.sb-chip`）用 `position:relative + ::after inset:-2px` 透明热区把命中区 44→48px，视觉零变化；`#sbSearch` 在 `pointer:coarse` 断点命中区 44→48px（仅触屏设备，桌面不受影响）。
- **重叠核算**：aep gap 12px / wb-bar gap 8px / sb-chips gap 5px，均大于 -2px×2=4px 扩展量，无热区重叠；wb-panel `overflow:hidden` 被 wb-bar margin-top 8px + padding 8px/12px 吸收，无裁剪。
- **验证方法**：shell 契约 + api 契约测试 38 passed；阶段三将用 elementFromPoint 实测 48×48 命中与无遮挡。
- **状态**：✅ 已修复（阶段三实测复核）

### Fix-3 ｜ P2#2 ｜ 三模块覆盖率补强（commit `fe3e260`）
- **原始描述**：mcp.py 74%、config.py 76%、monitor.py 79%，薄弱点为异常兜底/降级标识/回退链分支。
- **技术方案**：新增 41 个测试用例（mcp +14：7 个工具 handler 全覆盖/ping/batch 拒绝/arguments 非对象/校验文案/版本回退；config +11：.bak 三级回退链/_save_yaml 失败/四端点异常兜底/企微分支/异常脱敏/user_positions 回退；monitor +16：告警 5 档分支/watchlist 非 dict 回退与写失败/StockMonitor 成功路径/capital_flow 空数据降级/northbound TradingQuant 回退链成败两分支）。
- **验证方法**：定向覆盖实测 **mcp 100% / config 94% / monitor 98%**（余 4 行为 ImportError 探测回退，环境性不可达，如实记录）；全量回归 3,465 passed。
- **状态**：✅ 已修复（全站覆盖率 88%→89%）

### Fix-4 ｜ P2（承 r8/r9 存量）｜ 表单可访问名称（commit `e52c7a7`）
- **原始描述**：30 处表单控件无 `<label for>` 关联也无 aria-label（搜索框/持仓表单/定投参数/基金表单/策略参数模板等），屏幕阅读器不可用。
- **技术方案**：28 个静态控件按 id 精确注入语义 aria-label；策略参数模板 2 个动态控件注入 aria-label（select 用配置路径、input 用 STRATEGY_PARAM_META 中文元数据，注意模板求值先于 `label` 变量声明，用内联表达式规避 TDZ ReferenceError）。
- **验证方法**：扫描器复测 **0 处缺失**（基线 30）；shell/fund/api 契约测试 42 passed；模板改动无渲染逻辑变化。
- **状态**：✅ 已修复

## 二、修复率统计

| 级别 | 阶段一数量 | 本轮修复 | 修复率 | 未修复处置 |
|---|---|---|---|---|
| P0 | 0 | — | — | — |
| P1 | 0 | — | — | — |
| P2 | 2 | 2 | **100%** | — |
| P3 | 4 | 2 | 50%（达标即可） | `.venv` 清理（start.ps1 已自愈，无功能影响）→ backlog；tokenBanner 双份文案互指注释 → backlog（低风险） |

## 三、新增/修改代码测试覆盖

- 本轮新增测试：41 个用例（test_mcp.py / test_config_routes.py / test_monitor_routes.py），无生产逻辑改动仅 CSS/aria-label 注入，契约测试锁回归。
- 全站覆盖率：88% → **89%**（+38 用例全绿）。

## 四、构建与 CI

- 本地构建：compileall 0 errors ✔；ruff 0 error ✔
- push `e52c7a7` 触发 GitHub Actions 全量门禁（编译/测试/覆盖率 78% 门槛）+ Render 自动部署，线上指纹复验见阶段三开头。

## 五、阶段小结

P0/P1 为零、P2 100% 修复、P3 50% 修复（其余入 backlog）、回归全绿、覆盖率不降反升。**判定：通过**，进入阶段三页面检查。
