# r9 阶段一 · 全量代码审查与功能完整性排查（2026-11-25）

**审查范围**：重锚基线 `d117288`；增量重点 `4245af1..d117288` 共 11 个 commit（±681/−95 行，25 文件）逐行交叉审查 + 全量静态检查 + 遗留清单核对 + 功能矩阵对标。
**执行方法**：增量 diff 人工交叉审查（后端 8 文件 + 前端 6 文件）+ ruff/compileall/覆盖率实测 + 27 页面容器 × 三场景旅程覆盖核对。
**执行模型**：GLM-5.3-Flash（如实标注）。

## 一、增量审查结论（总体）

S9-01~S9-22 系列修复质量整体优秀，全部满足项目硬约束，亮点核对如下（均有配套测试）：

| 修复项 | 核对结论 |
|---|---|
| S9-14 量能缺失显式"不可判" | `_val`→`_num` 过滤 NaN→None，语义链路正确；补 0 假象消除 ✔ |
| S9-04 缠论/信号端点 6 位代码强校验 | `_CODE_RE.fullmatch` 收口，detail 结构与 `_code_or_422` 一致 ✔ |
| S9-01 mootdx 专用 2 线程池 + 15s 显式超时 | 挂起面封顶于池内，符合「显式超时禁止拉长等待」约束 ✔ |
| S9-10 机构持仓失败短缓存（1800s→60s） | 瞬时故障不再被缓存放大 ✔ |
| S9-11 MCP 异常不泄露内部信息 | ValueError（自产文案）与未知异常（固定文案）分流 ✔ |
| S9-03 `/mcp` 写限流豁免声明 | 注释明确豁免理由（只读+自挂 60/min），与模块声明一致 ✔ |
| S9-19 首帧预上色 + 4s 失效保险 + boot-done | boot 成败均标记完成，无悬挂态；`?shell=off` 根路径透传 ✔ |
| S9-06 migrate 预校验 / S9-05 unboot 还原 / S9-07 cmdk 短路 | 回退路径闭环，半搬态已消除 ✔ |
| S9-15 表格观察器防抖 + 异常隔离 / S9-16 localStorage 64KB 防御 / S9-17 浮层实测定位 | 性能放大点已收敛 ✔ |
| 7a27a6f 移除裸「汇金」关键词 | 归类负例测试配套 ✔ |
| 9c9c205 scripts ruff 治理（5→0） | 基线 5 errors 清零 ✔ |

## 二、问题清单（P0~P3 分级）

> P0（阻断）：0 个 ｜ P1（严重）：0 个 ｜ P2（一般）：2 个 ｜ P3（建议）：4 个

| # | 级别 | 位置 | 描述 | 修复建议 |
|---|---|---|---|---|
| 1 | P2 | [web/static/css/qf-shell.css:640](file:///D:/xm/wz/Fund-Assessment/web/static/css/qf-shell.css)、[web/static/index.html:434](file:///D:/xm/wz/Fund-Assessment/web/static/index.html)、[web/static/css/terminal-ui.css:139-151](file:///D:/xm/wz/Fund-Assessment/web/static/css/terminal-ui.css) | **触控目标 44px 与 48px 规范口径偏差**：S9-18 采用 `min-height:44px`（WCAG 2.5.5 AAA），但评估总规则与项目硬约束为「透明热区 ≥48×48 且不改视觉尺寸」。涉及 `.aep-retry/.aep-close`、`.qf-wb-bar .btn/.qf-wb-toggle`、`#sbSearch/.sb-chip` | 阶段三实测各触点实际命中区后，阶段五用透明热区（`position:relative; ::after` 扩展命中区）统一收敛至 ≥48×48，不改变视觉尺寸 |
| 2 | P2 | [tests 缺口](file:///D:/xm/wz/Fund-Assessment/web/routes/mcp.py)、[config.py](file:///D:/xm/wz/Fund-Assessment/web/routes/config.py)、[monitor.py](file:///D:/xm/wz/Fund-Assessment/web/routes/monitor.py) | **覆盖率薄弱模块**：mcp.py 74%、config.py 76%、monitor.py 79%（全站 88%） | 阶段二为三模块关键分支补测（MCP 工具入参校验分支、config 读写失败分支、monitor 定时器分支），目标 ≥85%；未达部分列入分阶段计划 |
| 3 | P3 | [web/api.py:13](file:///D:/xm/wz/Fund-Assessment/web/api.py) | F811：`Request` 重复导入（line 7 fastapi 已导入，f674090 追加时未删 line 13 starlette 原导入；fastapi 的 Request 即 starlette re-export，无功能影响） | 删除 line 13 `from starlette.requests import Request`，配 ruff 全绿验证 |
| 4 | P3 | `.venv/`（pyvenv.cfg） | venv 损坏：指向已卸载的 Python310，venv Python 无法启动；start.ps1 已优先 `D:\dev-tools\Python312` 不受影响 | 删除损坏的 `.venv` 目录并重建（或从文档移除引用），避免误导后续维护 |
| 5 | P3 | [web/static/index.html:1083-1086](file:///D:/xm/wz/Fund-Assessment/web/static/index.html) 与 `refreshTokenBanner()` | 静态预上色 tokenBanner 与动态模板为**双份同文案**（S9-19 引入），文案修改时易漂移 | 在两处加互指注释（「文案与 refreshTokenBanner 保持同步」），或提取 JS 常量在 boot 时回填静态节点 |
| 6 | P3 | 全局 | skipped 测试均为设计内跳过（命名含 `_skipped`），无环境性跳过——**核对项，非缺陷** | 无需处理，仅记录口径 |

## 三、遗留项核对（承 r8/r9 基线）

| 项 | 状态 | 处置 |
|---|---|---|
| a11y：表单无 label 60 处、字段无 id/name 40 处 | 存量未修复 | P2：阶段二优先修复高频入口（设置/搜索/持仓表单），其余列分阶段计划 |
| `innerHTML` 直接拼接 254 处 | 承 r8 backlog | 增量代码已核：S9 系列 0 处新增直接拼接；存量列 backlog |
| `#connStatus` 语义误标 / monitor 子能力无前端入口 / 策略进度反馈 | 承 r8 backlog | 入本轮 roadmap，不阻塞 |
| Firefox / Safari 兼容、pip-audit CVE、生产 IP 限流分桶 | 待基线 | 阶段三尝试 Firefox 安装，失败如实标注 |
| 色彩 hex 304 处、留白 2px 子网格 | 承 r8 backlog | 阶段五按维度处理 |

## 四、功能缺口专项报告

**功能矩阵**：27 个唯一页面容器全部有对应实现与路由；r8 收口 24 页 → 本轮新增 3（market-style、dispatch、hud-shell 三态页面族），API 面无删减。三大核心旅程（工作台看盘 / 个股缠论+多信号 / 复盘三问）的页面入口、路由、API、测试四层齐备。

| 缺口 | 优先级 | 用户价值 | 实现成本 | 落地建议 |
|---|---|---|---|---|
| F-1 monitor 子能力无前端入口（告警规则查看/触发历史） | P3 | 用户无法在页面看到已配置告警的触发记录，能力感知缺失 | 中（新增只读页面 + 1 个只读 API 已存在） | 列入 roadmap 本轮不实施，避免改动面扩大 |
| F-2 预测准确率周报（r8 roadmap N-1） | P2 | 预测闭环可验证，提升信任度 | 中（prediction_store 已有数据，聚合 + 展示） | roadmap 首位，下轮功能迭代候选 |
| F-3 持仓快照曲线（N-2）/ 三问通过率统计（N-3） | P3 | 复盘纪律量化 | 低-中 | backlog |

**结论**：无 P0/P1 功能缺口；本轮增量功能（缠论结构、多信号扫描、MCP 只读、国家队/机构持仓、市场风格页）完整落地且测试配套。功能完整度维持约 95%（缺 monitor 前端入口等承前项）。

## 五、阶段小结

- 静态检查：ruff 1 error（P3#3）、compileall 0、覆盖率 88%（门禁 78% 达标）✔
- 回归：3,427 passed / 2 skipped 全绿 ✔
- 线上：`d117288` 已部署，指纹一致 ✔
- 判定：**通过**，可进入阶段二。P0/P1 为零，P2 两项（触控 48px 收敛、覆盖率补强）在阶段二处理；触控项的实测部分依赖阶段三数据，阶段二先落地「透明热区方案」代码改造。
