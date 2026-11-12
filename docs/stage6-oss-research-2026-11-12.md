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
> **状态：已实施（2026-11-12，commit `ab4afd1`）**。BacktestResult 新增 `skewness`/`kurtosis`/`monthly_returns`，`optimize_ma_cross` 的 best 附带 tear sheet 字段，ma-optimizer 页渲染「最优组合月度收益 · Tear Sheet」（红=正/绿=负/缺失=—）；7 个行为级测试，全量回归 2743 passed，已上线指纹复验。立项 4 因本机 pip 出网被拦（moviepy/edge-tts 不可安装）维持 backlog。
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

## 五、抖音 / 短视频链路补充调研（2026-11-12 追加）

**背景**：本项目服务「量化对垒」科普视频链路（数据 → 指标 → 信号 → 回测 → 参数优化），网页端产出判读素材。阶段 3/4 的 Playwright 截图探针已证明"图表素材自动采集"环节可程序化；抖音相关开源可补齐 **旁白 → 字幕 → 合成 → 分发** 后四环。

### 候选项目活跃度（GitHub API 实测 2026-11-12）

| 项目 | Stars | 最近推送 | 定位（对本视频链路的意义） |
|---|---|---|---|
| [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 142,739 | 2026-11-12 | 全自动短视频生成（文案→素材→TTS→字幕→合成），**体型过大**：依赖多、产物风格与本链路不符 |
| [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | 64,845 | 2026-11-11 | 音色克隆 TTS，需 GPU |
| [fishaudio/fish-speech](https://github.com/fishaudio/fish-speech) | 33,693 | 2026-11-12 | 高质量 TTS，需 GPU |
| [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) | 21,360 | 2026-11-09 | 抖音解析/下载 —— **不纳入**：与生产链路无关且有平台条款风险 |
| [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) | 17,456 | 2026-11-04 | 下载工具 —— **不纳入**（同上） |
| [WEIFENG2333/VideoCaptioner](https://github.com/WEIFENG2333/VideoCaptioner) | 16,507 | 2026-09-12 | 字幕生成/对齐/样式 |
| [dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload) | 16,449 | 2026-10-22 | 多平台自动上传（抖音/B站/小红书/视频号等），cookie + 浏览器自动化方案，已提供 Agent Skill 形态 |
| [Zulko/moviepy](https://github.com/Zulko/moviepy) | 15,139 | 2026-08-26 | Python 视频合成基础库，成熟稳定 |
| [rany2/edge-tts](https://github.com/rany2/edge-tts) | 12,521 | 2026-10-14 | 轻量 TTS（零 GPU 依赖），旁白最省成本路线 |
| [linyqh/NarratoAI](https://github.com/linyqh/NarratoAI) | 11,798 | 2026-10-24 | 文案→解说视频一站式，"文案分镜"结构值得借鉴 |
| [modelscope/FunClip](https://github.com/modelscope/FunClip) | 6,543 | 2026-11-01 | 语音识别驱动的片段剪辑 |

全部 11 项均活跃（近 2 月内有推送），无停滞依赖风险；两个下载类工具因合规与定位原因主动排除。

### 立项建议（按优先级）

**立项 4（P1）：图表素材 → 科普短视频片段合成 PoC —— 借鉴 moviepy + edge-tts**
- **依据**：素材采集已具备（截图探针），缺合成与旁白。moviepy（成熟）+ edge-tts（零 GPU）是成本最低的合规组合：把 IR 热力图/因子报告的关键判读画面 + 固定脚本旁白合成为 30-60s 竖屏片段。NarratoAI 的"文案分镜"结构（一句旁白对应一个画面）可直接套用。
- **范围**：`scripts/` 下新增离线合成脚本（不进 Web 端）；旁白文案与画面清单走版本化 YAML，与视频链路 STEP 顺序对应。
- **成本**：低-中。红线：旁白不含任何收益承诺数字（与页面免责文案同口径）。

**立项 5（P2）：抖音发布自动化 PoC —— 借鉴 social-auto-upload**
- **依据**：social-auto-upload 验证了 cookie + 浏览器自动化上传抖音的可行性，且其 Agent Skill 形态与本项目的 Playwright 基础设施契合。
- **前置评估（必须先做）**：① 优先走**抖音开放平台官方视频发布 API**（需资质申请）；② 模拟登录上传方案存在平台条款风险，仅作 fallback 并显式标注风险；③ 账号 cookie 属敏感凭据，存储方案需复用本项目管理鉴权体系的边界约定。
- **成本**：中；风险在合规而非技术。

**立项 6（P3/backlog）：统一音色旁白 —— GPT-SoVITS / fish-speech**
- 需 GPU 推理环境，待立项 4 的 PoC 验证旁白需求真实存在后再升级。

### 本节结论

抖音侧没有"量化科普"重叠竞品（MoneyPrinterTurbo/NarratoAI 是通用短视频生成，无金融数据判读能力），本项目"网页端判读素材 → 合成 → 分发"的拼装路线成立；推荐组合为 **moviepy + edge-tts（合成）+ 官方开放平台 API（分发）**，全部为活跃维护且无重型依赖。



1. 本项目"轻量自研 + A 股研究诊断 + Web 工作台"的定位在同类开源中**没有重叠竞品**：qlib/vnpy 重于平台与实盘，vectorbt/quantstats 是纯库无 UI，backtrader 已停滞。
2. 真正值得吸收的是三个已被验证的形态：**tear sheet 报告**（quantstats）、**向量化参数搜索**（vectorbt）、**PIT 数据治理**（qlib）——分别对应立项 1/2/3，成本与优先级已排定。
3. 引入外部框架（backtrader/zipline/vnpy）经活跃度与定位双重评估后**全部否决**。

## 六、结论

1. 本项目"轻量自研 + A 股研究诊断 + Web 工作台"的定位在同类开源中**没有重叠竞品**：qlib/vnpy 重于平台与实盘，vectorbt/quantstats 是纯库无 UI，backtrader 已停滞。
2. 量化侧值得吸收的是三个已被验证的形态：**tear sheet 报告**（quantstats）、**向量化参数搜索**（vectorbt）、**PIT 数据治理**（qlib）——对应立项 1/2/3。
3. 抖音/短视频侧同样无"量化科普"重叠竞品（MoneyPrinterTurbo/NarratoAI 为通用短视频生成，无金融判读能力），值得吸收的是**轻量合成**（moviepy + edge-tts）与**发布自动化**（social-auto-upload 方案，官方 API 优先）——对应立项 4/5/6。
4. 引入外部框架（backtrader/zipline/vnpy/MoneyPrinterTurbo）经活跃度、定位与依赖重量三重评估后**全部否决**；下载解析类工具（TikTokDownloader / Douyin_TikTok_Download_API）因平台条款风险主动排除。
5. 六项立项汇总排序：**P1 = 立项 1（tear sheet）+ 立项 4（合成 PoC）**；P2 = 立项 2（向量化）+ 立项 5（发布自动化，先过合规评估）；P3 = 立项 3（PIT）+ 立项 6（音色 TTS）。

## 数据来源

- GitHub API 实测：上两表 19 仓库 stars/pushed_at/open_issues（2026-11-12 由 `gh api repos/{repo}` 拉取；FunClip 经 `gh api search/repositories` 定位至 modelscope 组织）
- 框架对比检索：[Backtrader vs VnPy vs Qlib (dev.to, 2026-04)](https://dev.to/linou518/backtrader-vs-vnpy-vs-qlib-a-deep-comparison-of-python-quant-backtesting-frameworks-2026-3gjl) · [Quant Open-Source Frameworks Compared](https://waylandz.com/quant-book-en/Quant-Framework-Comparison/) · [Best Python Backtesting Libraries 2026 (hasanjaved.me)](https://hasanjaved.me/blog/best-python-backtesting-libraries-2026/) · [vectorbt 官方文档](https://vectorbt.dev/) · [quantstats (PyPI)](https://pypi.org/project/quantstats/) · [qis 论文（含 pyfolio-reloaded/quantstats/empyrical-reloaded 生态对照, JOSS 2026-10)](https://joss.theoj.org/papers/10.21105/joss.11400.pdf)
- 抖音/短视频链路检索：[social-auto-upload 仓库](https://github.com/dreammis/social-auto-upload) · [social-auto-upload 完整教程 (CSDN, 2026-09)](https://blog.csdn.net/gitblog_00804/article/details/153455455) · [一键批量上传八大平台 (腾讯云开发者, 2026-09)](https://cloud.tencent.com/developer/article/2739365) · [Social Auto Upload 介绍 (掘金)](https://juejin.cn/post/7490734152502558770)
