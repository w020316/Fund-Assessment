# 第七轮质量评估 · 阶段一：全量代码审查与功能完整性排查

**执行日期**：2026-11-19｜**HEAD**：`c4b0f7d`（基线 `docs/quality-baseline-2026-11-19-r7.md`）
**审查方法**：静态分析（pytest 全量 / coverage / ruff / XSS 重扫描探针）+ 增量 diff 交叉审查（`561583b..c4b0f7d`，57 提交 / 74 文件）+ 功能矩阵对标。只列可证实问题，不做猜测性报忧。

---

## 一、问题清单

### P1（严重，1 条）

**Q1. 测试套件红灯：`test_guard_script_behaviour_matrix` 在本机必然失败（测试基建缺陷）**
- 位置：[tests/test_prediction_workflow.py:111-147](file:///D:/xm/wz/Fund-Assessment/tests/test_prediction_workflow.py#L111-L147)
- 描述：该用例真的执行 `.github/workflows/prediction.yml` 的 `Guard secrets` shell 脚本，用 `shutil.which("bash")` 选解释器。本机解析到 `C:\Windows\system32\bash.EXE`（**WSL 启动器且本机无 WSL 发行版**）：脚本根本未执行，stderr 为 GBK 编码的本地化错误（触发 `text=True` 的 UTF-8 解码线程 `UnicodeDecodeError`，输出全部丢失），rc=1 恰好撞上「secrets 缺失应 exit 1」的断言，随后「输出文件含 `secrets_ready=false`」断言失败。`skipif(not BASH)` 守卫被 WSL stub 绕过（BASH 非空但不可用）。
- 佐证：探针 `.tmp/probe_guard_bash.py` 最小复现——rc=1、stdout/stderr 空、`GITHUB_OUTPUT` 文件空、解码线程崩溃，与全量套件中失败输出逐项一致。
- 影响面：基线 3125P+**1F**；该用例由增量提交 `2fdf8d2` 新增（r6 收官全绿之后），故与 r6 记录不矛盾；GitHub Actions（ubuntu 真 bash）不受影响，属本机可复现问题。
- 修复建议：skipif 改为**行为级 bash 可用性探针**（`bash -c "exit 7"` 校验 rc==7，失败则跳过并说明原因），同时子进程捕获加 `errors="replace"` 消除编码崩溃、保留可诊断输出。**不削弱任何断言**（在真 bash 环境下断言完全保留）。

### P2（一般，2 条）

**Q2. ruff 171 errors（r6 169 → +2），需按文件分类治理而非盲改**
- 分布（`--statistics` 实测）：F401×126（其中 `src/core/__init__.py` 39 + `src/agents/__init__.py` 16 为**有意 re-export**）、E402×23（`web/api.py` 20——`sys.path`/环境准备先于 import 的结构性必然）、F841×18（`data_source_v2.py` 6 处真实死变量 + 测试 mock 参数 7 处）、F811×2、E731×1、F403×1。
- 约束提醒：历轮教训「ruff 自动修复会误删 `__init__.py` 公共 re-export」→ 两条 `__init__.py` 与 `web/api.py` 的 E402 应走 `pyproject.toml` per-file-ignores，其余 F401/F841 逐条人工判断后修。
- 修复建议：新增 per-file-ignores 规则（预期消掉 ~62 条），真实死变量手工修（预期再消 ~20 条），目标 ≤90 并回归全量测试。

**Q3. 覆盖率洼地延续：`src/strategies/*` 三文件 <62%**
- 位置：`stock_monitor.py` 50%（216/107）、`trading_quant.py` 55%（298/135）、`limit_up.py` 61%（194/76）——历轮遗留，未恶化也未改善；`data_source_v2.py` 66%→72% 有收窄但 565 条未覆盖仍占全项目 22%。
- 修复建议：阶段 2 补策略层纯函数分支测试（数据不足/边界档位/涨跌停常量路径），目标全局 ≥86% 且三个洼地 ≥70%。

### P3（建议，4 条，列入 backlog 不阻塞）

| # | 描述 | 位置 |
|---|---|---|
| Q4 | `innerHTML` 存量 254 处（r6 253），新 JS 已全部合规；存量属 index.html 历史欠账，需分片治理 | web/static/index.html |
| Q5 | 色彩 hex 硬编码 340 处、留白 4px 严格 66.1%——承 r6 阶段五遗留，归阶段五分片推进 | index.html / workbench.css |
| Q6 | F403/E731/F811 共 4 条零散 lint | 见 ruff 输出 |
| Q7 | 依赖 CVE、Firefox/Safari 兼容性维持「待基线」（r6 已定位根因，不重复展开） | — |

---

## 二、功能完整性排查（功能矩阵对标结论）

| 检查项 | 结论 |
|---|---|
| r6 backlog「预测模块」「全球市场面板」 | ✅ **已落地闭环**：`prediction.js` / `global-market.js` / `version-modal.js` 外置上线，页面容器 20→24 |
| prediction 路由鉴权面 | ✅ 9 端点逐显式挂载（4 读公开 / 5 写+export 全 `require_admin`），文件头有验收核对表（[web/routes/prediction.py:3-12](file:///D:/xm/wz/Fund-Assessment/web/routes/prediction.py#L3-L12)） |
| prediction 数据真实性 | ✅ 三信号缺失不伪造 `flat/50` 兜底（test_prediction_insufficient 锁定）；`llm_confidence` 缺失返回 null |
| 预测定时闭环失败语义 | ✅ guard 单一判定点 + 执行步全 gating + Summary `always()`（`prediction.yml` M-01 统一）——仅本机测试基建需修（Q1） |
| XSS 面 | ✅ 8 个新增/改动 JS 裸插值重扫描 0 命中；`test_frontend_xss.py` 等前端契约 77P/1S 全绿 |
| 仍仅 API 可用（维持 backlog） | monitor/*、heatmap、multi_analyze 等承 r6 结论，本轮无新增无前端入口的能力 |
| workflow 守卫 | ✅ daily-report 提交守卫 fail-closed + `core.quotepath=false` 修复到位 |

**整体质量印象**：本轮增量（预测闭环 + 全球面板 + 失败语义统一）遵循了既有硬约束（鉴权/数据真实性/escapeHtml/外置瘦身），架构一致性好；唯一红灯是新增测试的 Windows 环境兼容性欠考虑。

---

## 三、阶段 2 必办项（按优先级）

1. Q1：修 guard 测试的 bash 探针 + 编码健壮性 → 全量回归转绿（独立 commit）
2. Q2：ruff 治理——per-file-ignores + 人工修死变量 → 统计前后对比（独立 commit）
3. Q3：补 `stock_monitor` / `trading_quant` / `limit_up` 分支测试 → 洼地 ≥70%（独立 commit）

> 本报告由 AI 协作生成，关键决策请由工程负责人复核。
