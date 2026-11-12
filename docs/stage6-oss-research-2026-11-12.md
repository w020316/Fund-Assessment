# 阶段六 · 同类开源项目调研立项（2026-11-12）

**定位**：第五轮质量评估的阶段六（原定跳过，用户后续要求补充执行）。
**目的**：对照同类开源项目校准 QuantFlow Pro 量化链路（数据 → 指标 → 信号 → 回测 → 参数优化 → 因子检验）的成熟度，识别值得借鉴的能力与应避免的依赖，产出可执行的立项建议。
**方法**：候选项目活跃度经 GitHub API 实测（2026-11-12，零构造）；能力对比基于各项目官方文档与社区评测检索交叉验证。本项目侧事实全部来自本轮阶段 0-5 实测。

## 一、候选项目活跃度（GitHub API 实测 2026-11-12）

| 项目 | Stars | 最近推送 | 状态 | 定位 |
|---|---|---|---|---|
| [microsoft/qlib](https://github.com/microsoft/qlib) | 50,233 | 2026-11-04 | 活跃 | AI 导向量化研究平台（数据层 PIT / 因子库 / ML 工作流） |
| [vnpy/vnpy](https://github.com/vnpy/vnpy) | 46,348 | 2026-11-11 | 活跃 | 国内事件驱动交易系统（实盘网关 + CTA 回测） |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 23,721 | **2024-08-19** | **停滞** | 经典事件驱动回测框架（社区维护分叉续命） |
| [akfamily/akshare](https://github.com/akfamily/akshare) | 23,312 | 2026-11-12 | 活跃 | A 股数据源聚合（本项目已在用） |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 9,824 | 2026-11-11 | 活跃 | 向量化回测/参数搜索（pandas+NumPy 全矩阵化） |
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | 9,228 | 2026-08-05 | 低频维护 | 轻量单资产回测 |
| [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | 7,856 | 2026-11-02 | 活跃 | 组合绩效分析 + HTML tear sheet |
| [stefan-jansen/zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) | 2,015 | 2026-11-02 | 活跃 | Zipline 社区续命版（事件驱动 + pyfolio 配套） |

**依赖风险结论**：backtrader 已两年无推送，**不引入**——也反证本项目自研回测引擎（`src/core/backtest.py`，覆盖率 97%）的路线正确；同类事件驱动框架（zipline-reloaded/vnpy）面向实盘网关与订单生命周期，与本项目的"研究诊断"定位不匹配，引入即背负无关复杂度。

## 二、能力矩阵对比（对照本项目六环节链路）

| 能力 | 本项目（实测） | qlib | vectorbt | quantstats | 差距判定 |
|---|---|---|---|---|---|
| A 股数据源 | akshare+EM 双上游、双保险 | 自建 PIT 数据层 | 不含 | 不含 | **持平**（研究场景够用；PIT 治理缺位见下） |
| 指标/信号 | MA 金叉死叉 + Alpha144 | 158 个 alpha 库 | 信号矩阵广播 | 不含 | 差距为"广度"，链路已通 |
| 事件回测 | 自研引擎，逐笔成交/费率/滑点 | 有 | 向量化（快但近似） | 不含 | 持平（向量化的价值见立项 2） |
| 参数优化 | 63 组合网格 + IR 热力图（循环式） | 网格+ML 调参 | **数千组合秒级**（广播） | 不含 | **立项 2** |
| 因子检验 | IC/分组/purge+holdout/市值暴露 | 完整因子流（训练→归因） | 不含 | 不含 | 定位匹配，深度足够研究诊断用 |
| 绩效报告 | 指标卡 + JSON 导出 | 报告生成 | 图表丰富 | **一站式 HTML tear sheet** | **立项 1** |
| 数据时点治理（PIT） | 无（notes 已自曝幸存者偏差限制） | **核心优势** | 不含 | 不含 | backlog（见立项 3） |
| 开箱 Web 工作台 | **有**（SPA + 管理鉴权 + 诚实降级） | 无（Jupyter 为主） | 无 | 无 | **本项目独有优势** |

## 三、立项建议（按优先级）

### 立项 1（P1）：回测绩效 Tear Sheet —— 借鉴 quantstats
- **依据**：本项目回测/网格输出只有 4-6 个指标卡（收益/回撤/夏普/IR/交易次数），quantstats 生态已验证"月度收益热力表 + 尾部比率（Sortino/Calmar/尾部损失/偏度峰度）+ 滚动指标"是判读策略质量的标准形态；与 roadmap「网格高原识别」天然互补（高原判读需要逐组合完整指标）。
- **范围**：`BacktestEngine` 结果增加 monthly-returns 表与 tail-ratios 计算（纯 pandas，无新依赖），前端复用热力图组件渲染月度表。
- **成本**：低-中；与既有"缺失显示缺失"红线兼容（样本不足的指标显示 —）。

### 立项 2（P2）：参数网格向量化 —— 借鉴 vectorbt
- **依据**：当前 63 组合为逐组合循环回测（单飞锁内秒级）；vectorbt 证明信号/持仓矩阵化 + 广播后参数搜索可扩到数千组合。立项目标是支撑 roadmap「多标的对比」与「高原识别」需要的高密度网格（如 MA 2-60 步长 1 = 800+ 组合）。
- **范围**：为内置只读策略（MA cross）增加向量化快路径，事件引擎保留为外部策略的精确路径；两路径结果用现有测试交叉校验。
- **成本**：中；**风险点**：向量化近似与逐笔精度的边界必须写进 notes（不夸大精度）。

### 立项 3（P3/backlog）：PIT 数据快照层 —— 借鉴 qlib（远期）
- **依据**：factor-research 的研究边界 notes 已诚实自曝"不根据测试段自动选择方向/幸存者偏差"等限制；qlib 的 point-in-time 数据层是从根上治理幸存者偏差/未来函数的方案。
- **范围**：本地 K 线快照按拉取日归档（复用既有数据缓存目录约定），因子检验支持"当时可见宇宙"回放。
- **成本**：高，依赖数据源能力；先入 backlog，待因子检验使用频次验证后再立项。

## 四、结论

1. 本项目"轻量自研 + A 股研究诊断 + Web 工作台"的定位在同类开源中**没有重叠竞品**：qlib/vnpy 重于平台与实盘，vectorbt/quantstats 是纯库无 UI，backtrader 已停滞。
2. 真正值得吸收的是三个已被验证的形态：**tear sheet 报告**（quantstats）、**向量化参数搜索**（vectorbt）、**PIT 数据治理**（qlib）——分别对应立项 1/2/3，成本与优先级已排定。
3. 引入外部框架（backtrader/zipline/vnpy）经活跃度与定位双重评估后**全部否决**。

## 数据来源

- GitHub API 实测：上表 8 仓库 stars/pushed_at/open_issues（2026-11-12 由 `gh api repos/{repo}` 拉取）
- 框架对比检索：[Backtrader vs VnPy vs Qlib (dev.to, 2026-04)](https://dev.to/linou518/backtrader-vs-vnpy-vs-qlib-a-deep-comparison-of-python-quant-backtesting-frameworks-2026-3gjl) · [Quant Open-Source Frameworks Compared](https://waylandz.com/quant-book-en/Quant-Framework-Comparison/) · [Best Python Backtesting Libraries 2026 (hasanjaved.me)](https://hasanjaved.me/blog/best-python-backtesting-libraries-2026/) · [vectorbt 官方文档](https://vectorbt.dev/) · [quantstats (PyPI)](https://pypi.org/project/quantstats/) · [qis 论文（含 pyfolio-reloaded/quantstats/empyrical-reloaded 生态对照, JOSS 2026-10)](https://joss.theoj.org/papers/10.21105/joss.11400.pdf)
