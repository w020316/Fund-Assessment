# 综合质量评估与功能完善执行规格

> 日期: 2026-07-29
> 项目: QuantFlow Pro (Fund-Assessment)
> PRD 基线: docs/superpowers/specs/2026-07-28-quantflow-pro-fund-advisor-design.md
> 范围: 代码审查、问题修复、功能添加、功能完善、页面检查、功能测试、前端文案优化、UX 评估、开源调研、交付准备

---

## 1. 现状基线

| 维度 | 当前状态 | 目标 |
|------|---------|------|
| Python 源文件 | 50 个 (43 业务模块) | - |
| 测试文件 | 17 个 (覆盖率约 23%) | 全量覆盖 33 个无测试模块, 80%+ |
| 异常处理 | 189 处 except Exception, 78 处 except:pass | 消除静默吞异常, 引入结构化日志 |
| 前端 | 4191 行单文件 SPA (Vanilla JS, 深色 GitHub 风格) | 文案+交互优化(不改 CSS/配色/布局/字体) |
| 文档 | 12 篇阶段性文档 + 4 篇设计规格 | 补全技术/API/用户手册/测试报告/交付报告 |
| 部署 | Render Free (agnes priority=90 + zhipu_glm priority=100 双 Provider) | - |
| LLM Provider | 2 个可用 | - |

## 2. 硬约束(不可违反)

1. 品牌名统一为 QuantFlow Pro, OpenClaw 不得出现
2. 前端 UI 保留原始 CSS/配色/布局/字体, 仅允许文案改动与交互逻辑优化
3. API 路径与函数名在修改中保持不变
4. Render 部署使用 Python 3.12.11 运行时
5. 测试文件命名: test_<module>_routes.py (路由), test_<component>.py (组件)
6. 测试 fixture 使用 tmp_path 目录隔离, patch(time.time) 测 TTL
7. 不引入需要 Rust 编译的依赖(避免 Render 构建失败)

## 3. PRD 功能对照表

对照 PRD(qa-design §3-§5)检查功能完成度:

| PRD 模块 | API 端点 | 前端集成 | 状态 |
|---------|---------|---------|------|
| P1-1 消息面聚合 | /api/news/feed, /api/news/search, /api/news/hot-events, /api/news/sentiment | 消息面卡片+AI检索 | 已完成 |
| P1-2 重仓股板块 | /api/holdings/{fund_code}, /api/holdings/sector-rotation/overview | viewHoldings弹窗+板块轮动卡片 | 已完成 |
| P1-3 大盘研判 | /api/market/outlook, /api/market/sentiment, /api/market/sector-rotation | 市场展望卡片 | 已完成 |
| P1-4 五信号融合 | /api/fund/advice-five-signals, /api/fund/signals, /api/fund/decision-history | 五信号卡片 | 已完成 |
| P1-5 基金多智能体 | /api/agent/fund_analyze | multiAgentAnalyze弹窗 | 已完成 |
| P2 前端重写(Next.js) | - | - | 不执行(硬约束保留原生HTML) |
| P3 AI建议生成 | /api/scripts/ai-generate | - | 待确认 |
| 智能体辩论 | /api/agent/debate | - | 待确认 |

**结论**: P1 后端 11 个端点已全部实现且前端已集成。主要工作量在质量提升而非功能缺失。

## 4. 分批执行计划(8 批次)

### 批次 1: 代码审查与异常处理修复

**目标**: 系统性审查全部源代码, 消除 78 处 except:pass, 引入结构化错误日志

#### 4.1.1 代码审查检查项矩阵

| 审查维度 | 检查内容 | 工具 |
|---------|---------|------|
| 逻辑错误 | 空指针、除零、越界、类型混淆、off-by-one | Grep + 人工 |
| 性能瓶颈 | 串行网络请求、N+1查询、大对象未释放、无缓存 | Grep + 人工 |
| 安全漏洞 | 硬编码密钥、SQL注入、SSRF、未鉴权端点 | Grep + 已审查 |
| 编码规范 | 命名、类型注解、docstring、import顺序 | 人工 |
| 异常处理 | except:pass、裸except、异常吞噬 | Grep 定位 |
| 可维护性 | 单文件过长、函数过长、耦合度高 | 人工 |

#### 4.1.2 问题分级标准

| 级别 | 定义 | 处理时限 | 示例 |
|------|------|---------|------|
| P0 | 阻断性Bug/安全漏洞 | 立即修复 | 硬编码密钥、未鉴权写端点 |
| P1 | 影响功能正确性 | 本批次修复 | except:pass导致错误不可见 |
| P2 | 影响可维护性/性能 | 本批次修复 | 串行请求、无docstring |
| P3 | 优化建议 | 后续迭代 | 命名风格、import顺序 |

#### 4.1.3 修复范围

| 模块 | except:pass 数 | except Exception 数 | 修复方式 |
|------|----------------|---------------------|---------|
| src/strategies/ (6模块) | 51 | 60+ | except Exception as e: logger.warning(f"{操作} failed: {e}") |
| src/agents/ (7模块) | 13 | 20+ | 同上 |
| src/core/data_source_v2.py | 3 | 56 | 同上(保留降级返回值) |
| src/core/ai_service.py | 1 | 16 | 同上 |
| src/analysis/ (5模块) | 10 | 20+ | 同上 |

**修复原则**:
1. except:pass → except Exception as e: logger.warning(f"<操作> failed: {e}")
2. 不改变业务逻辑, 仅增加错误可观测性
3. 保留原有降级返回值, 不引入新异常抛出
4. 每个修复附带问题编号、描述、修复方案(写入commit message)

**产出物**:
- 代码审查报告(检查项矩阵 + 问题清单 + 分级)
- 修复后的源代码(git diff)
- 回归测试结果(全部现有测试通过)

**验收**: 全量 grep 确认 except:pass 数量降至 0

---

### 批次 2: 测试补全 - 核心模块

**目标**: 为 src/core + src/analysis + web/routes 剩余模块补全测试

#### 4.2.1 测试三层架构

| 层级 | 范围 | 目标覆盖率 | 工具 |
|------|------|-----------|------|
| 单元测试 | 函数级, mock外部依赖 | 80%+ | pytest + monkeypatch |
| 集成测试 | 路由级, FastAPI TestClient | 关键路由100% | pytest + httpx |
| 系统测试 | 端到端, 真实数据源(只读) | 核心场景3个 | 手动/浏览器 |

#### 4.2.2 新增测试文件清单

| 测试文件 | 覆盖模块 | 用例数(估) | 测试策略 |
|---------|---------|-----------|---------|
| test_ai_service.py | ai_service | 12 | mock LLM调用, 验证数据聚合 |
| test_capital_flow.py | capital_flow | 8 | mock akshare, 验证资金流解析 |
| test_fundamental.py | fundamental | 8 | mock数据源, 验证基本面指标 |
| test_news.py | news | 6 | mock akshare新闻 |
| test_news_aggregator.py | news_aggregator | 10 | mock Tavily+LLM, 验证4源聚合 |
| test_sentiment.py | sentiment | 6 | 验证情绪指标计算 |
| test_technical.py | technical | 12 | 纯pandas指标, 无需mock |
| test_executor.py | executor | 6 | mock订单执行 |
| test_risk_manager.py | risk_manager | 8 | 验证风控规则 |
| test_response.py | response | 4 | 验证响应格式 |
| test_scheduler.py | scheduler | 4 | 验证定时任务 |
| test_agent_routes.py | web/routes/agent.py | 10 | TestClient, mock ai_service |
| test_holdings_routes.py | web/routes/holdings.py | 6 | TestClient |
| test_news_routes.py | web/routes/news.py | 6 | TestClient |
| test_fund_routes.py | web/routes/fund.py | 8 | TestClient |
| test_market_routes.py | web/routes/market.py | 6 | TestClient |
| test_trade_routes.py | web/routes/trade.py | 6 | TestClient |
| test_config_routes.py | web/routes/config.py | 4 | TestClient |

**预计新增用例**: ~124 个

#### 4.2.3 缺陷跟踪记录模板

每个测试发现的缺陷记录:
```
| 缺陷ID | 模块 | 严重度 | 描述 | 复现步骤 | 修复状态 |
|--------|------|--------|------|---------|---------|
| BUG-001 | ai_service | P1 | JSON解析失败未降级 | 1.调用market_outlook 2.LLM返回非JSON | 已修复 |
```

**验收**: 核心模块覆盖率 80%+, 全部测试通过

---

### 批次 3: 测试补全 - 智能体与策略层

**目标**: 为 src/agents (7模块) + src/strategies (6模块) 补全测试

| 测试文件 | 覆盖模块 | 用例数(估) | 测试策略 |
|---------|---------|-----------|---------|
| test_base_agent.py | agents/base | 6 | 验证基类接口 |
| test_fundamental_agent.py | fundamental_agent | 8 | mock数据, 验证分析逻辑 |
| test_technical_agent.py | technical_agent | 8 | mock K线, 验证信号 |
| test_sentiment_agent.py | sentiment_agent | 6 | mock情绪数据 |
| test_news_agent.py | news_agent | 6 | mock新闻数据 |
| test_research_team.py | research_team | 8 | mock多智能体协作 |
| test_trading_manager.py | trading_manager | 6 | 验证组合管理 |
| test_a_stock_analyst.py | strategies/a_stock_analyst | 8 | mock数据, 验证信号 |
| test_limit_up.py | strategies/limit_up | 6 | 验证涨停监控 |
| test_stock_monitor.py | strategies/stock_monitor | 6 | 验证监控逻辑 |
| test_trading_quant.py | strategies/trading_quant | 6 | 验证量化信号 |
| test_bspro_quant.py | strategies/bspro_quant | 6 | 验证策略 |
| test_cb_t0_sniper.py | strategies/cb_t0_sniper | 6 | 验证可转债T0 |

**预计新增用例**: ~84 个

**验收**: 全量覆盖率 80%+

---

### 批次 4: 功能完善与页面检查

**目标**: 优化交互流程、加载状态、错误提示; 检查响应式适配

#### 4.4.1 页面检查清单

| 检查项 | 范围 | 方法 |
|--------|------|------|
| 响应式适配 | 375px/768px/1024px/1440px | 浏览器代理截图 |
| 跨浏览器兼容 | Chrome/Firefox/Edge | 浏览器代理 |
| 视觉一致性 | 10个页面配色/字体/间距 | 人工+截图 |
| 内容准确性 | 数据展示与API返回一致 | API对比 |
| 图片加载 | 无broken image | 浏览器检查 |
| 交互反馈 | 按钮loading/成功/失败toast | 交互测试 |

#### 4.4.2 交互优化项

1. 加载状态: 所有异步请求显示 loading 占位(非"加载中"纯文字)
2. 错误提示: 统一 toast 组件, 区分警告/错误/成功
3. 空状态: 无数据时显示引导提示(非空白)
4. 超时处理: AI分析超时显示友好提示(非无限等待)
5. 操作确认: 危险操作(删除持仓)增加二次确认

**验收**: 3 个断点截图正常, 无broken image, 交互反馈完整

---

### 批次 5: 前端文案与交互优化

**目标**: 在不改 CSS/配色/布局/字体前提下优化文案与交互反馈

#### 4.5.1 文案优化清单

1. AI相关文案中性化复查(智能分析/分析引擎, 不得出现"AI")
2. 操作引导文案优化(如"点击刷新获取" → 更具体的引导)
3. 错误提示文案友好化(技术错误 → 用户可理解)
4. 数据标签准确性(涨/跌颜色与市场习惯一致)

#### 4.5.2 交互增强项

1. 按钮loading态: 点击后立即禁用+显示加载指示
2. 成功/失败toast: 操作完成后显示反馈(2秒自动消失)
3. 数据刷新动画: 卡片刷新时轻微高亮
4. 键盘导航: 支持Tab/Enter操作

**验收**: 无"AI"字样残留, 交互反馈完整, 错误提示友好

---

### 批次 6: UX 评估

**目标**: 以真实用户视角执行 3 个核心场景, 输出 SUS 评分与改进建议

#### 4.6.1 评估方法论

| 方法 | 工具 | 输出 |
|------|------|------|
| 用户旅程图 | 浏览器代理记录操作路径 | 旅程图(步骤+情绪曲线) |
| SUS量表 | 10题标准问卷(1-5分) | SUS分数(0-100) |
| 任务完成率 | 3场景成功/失败统计 | 完成率% |
| 满意度评分 | 7点量表 | 评分 |

#### 4.6.2 核心场景

| 场景 | 操作路径 | 预期时长 | 验收点 |
|------|---------|---------|--------|
| 基金多智能体分析 | 基金页→选基金→点击"深度"→查看7分析师→查看决策 | <90s | 弹窗显示完整分析 |
| 持仓管理+板块轮动 | 持仓页→查看板块轮动→点击"分析"→查看重仓股 | <30s | 板块轮动+重仓股显示 |
| 全球市场+AI检索 | 国际市场→查看总览→输入关键词→AI检索 | <30s | 总览+检索结果 |

#### 4.6.3 产出物

- 用户旅程图(3场景, 含痛点标注)
- SUS评分表(10题)
- 任务完成率统计
- 改进建议清单(问题/方案/预期效果)
- 潜在新功能建议(至少3项, 含用户价值/难度/优先级)

**验收**: 3场景全部完成, SUS报告输出, 改进建议>=5条, 新功能建议>=3项

---

### 批次 7: 开源调研

**目标**: 调研 8+ 个相似开源项目, 输出对比分析报告, 提出架构借鉴建议

#### 4.7.1 调研对象(10 个候选)

| # | 项目 | 领域 | 调研重点 |
|---|------|------|---------|
| 1 | akshare/akshare | 金融数据源 | 数据源覆盖、接口设计、降级策略 |
| 2 | mootdx/mootdx | TDX行情 | 行情获取、协议实现 |
| 3 | RiceQuant/qlib | 量化平台 | 架构设计、模型抽象 |
| 4 | vnpy/vnpy | 量化交易框架 | 模块化设计、插件体系 |
| 5 | shidenggui/easytrader | 自动交易 | 券商接口、订单管理 |
| 6 | ai-bot-classroom/Qbot | AI量化 | AI集成、前端设计 |
| 7 | yunjieliu/AiBot | AI交易机器人 | 多智能体、LLM集成 |
| 8 | jiangzhonglian/stock-analysis | 股票分析 | 分析维度、可视化 |
| 9 | CedricFql/python-stock-analysis | Python股票分析 | 技术指标、数据流 |
| 10 | ricequant/rqalpha | 回测框架 | 策略引擎、事件驱动 |

#### 4.7.2 对比维度

| 维度 | 说明 |
|------|------|
| 核心功能模块 | 主要功能列表 |
| 系统架构 | 单体/微服务/插件化 |
| 技术栈 | 前端框架/后端语言/数据库/中间件 |
| 性能指标 | 响应时间/并发/数据量 |
| 社区活跃度 | Star数/PR数/Issue数 |
| 更新频率 | 最近commit时间 |
| 维护状况 | 是否活跃维护 |
| 与本项目契合度 | 可借鉴点 |

#### 4.7.3 产出物

- 对比分析表格(10项目 × 8维度)
- 架构借鉴建议(具体可落地的改进点)
- 功能扩展建议(基于调研的新功能设想)

**验收**: 10个项目调研完成, 对比表格输出, 借鉴建议>=5条

---

### 批次 8: 交付文档

**目标**: 整理全部交付文档, 满足产品验收标准

#### 4.8.1 文档清单

| 文档 | 内容 | 路径 |
|------|------|------|
| 技术架构文档 | 系统架构、模块说明、数据流 | docs/2026-07-29-architecture.md |
| API文档 | 全部端点(请求/响应/示例) | docs/2026-07-29-api-reference.md |
| 用户操作手册 | 操作指南、常见问题 | docs/2026-07-29-user-manual.md |
| 测试报告 | 覆盖率、用例统计、缺陷跟踪 | docs/2026-07-29-test-report.md |
| 交付报告 | 功能清单、测试结果、修复记录 | docs/2026-07-29-delivery-report.md |
| UX评估报告 | 旅程图、SUS、改进建议 | docs/2026-07-29-ux-assessment.md |
| 开源调研报告 | 对比表格、借鉴建议 | docs/2026-07-29-opensource-research.md |

#### 4.8.2 交付报告结构

```
1. 项目概览(定位/技术栈/部署)
2. 功能清单(按模块分类, 标注完成状态)
   - 行情总览模块
   - 智能分析模块
   - 基金建议模块
   - 消息面模块
   - 国际市场模块
   - 持仓管理模块
   - 数据监控模块
   - 建议模板模块
   - 系统配置模块
3. 测试结果(按测试类型分类)
   - 单元测试(用例数/通过数/覆盖率)
   - 集成测试(路由数/通过数)
   - 系统测试(场景数/通过数)
   - 缺陷统计(P0/P1/P2/P3, 已修复/待修复)
4. 问题修复记录
   - 问题编号/描述/级别/修复方案/状态
5. 验收清单
   - except:pass 数量: 0
   - 测试覆盖率: 80%+
   - 全部测试通过
   - 前端文案无AI字样
   - 3场景UX评估完成
   - 10项目调研完成
   - 交付文档齐全
   - Render部署稳定
```

**验收**: 7篇文档全部完成, 交付报告验收清单全部勾选

---

## 5. 执行原则

1. **分批提交**: 每批次完成后 git commit + push, 触发 Render 部署验证
2. **不破坏现有功能**: API 路径与函数名保持不变
3. **测试先行**: 修复后必须通过现有测试 + 新增测试
4. **遵守硬约束**: 前端不改 CSS/配色/布局/字体
5. **文档同步**: 每批次完成后更新对应文档
6. **commit规范**: feat/fix/test/docs/refactor 前缀 + 详细描述

## 6. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| 测试补全工作量极大(33模块) | 高 | 高 | 分2批次, 核心优先; 用例数适中不过度 |
| 策略层测试需mock大量依赖 | 中 | 中 | conftest.py统一fixture, monkeypatch |
| except:pass修复改变降级行为 | 低 | 中 | 仅加日志, 不改返回值; 回归测试验证 |
| Render Free实例资源限制 | 中 | 中 | 测试本地运行, 部署后抽样验证 |
| 开源调研项目信息不足 | 低 | 低 | WebSearch补充, 优先GitHub热门项目 |

## 7. 验收标准总表

| # | 验收项 | 目标 | 验证方法 |
|---|--------|------|---------|
| 1 | except:pass 数量 | 0 | grep 全量扫描 |
| 2 | 测试覆盖率 | 80%+ | pytest --cov |
| 3 | 全部测试通过 | 0 失败 | pytest 运行 |
| 4 | 前端无"AI"字样 | 0 处 | grep index.html |
| 5 | 响应式适配 | 4断点正常 | 浏览器截图 |
| 6 | 交互反馈完整 | loading/toast/空状态 | 交互测试 |
| 7 | 3场景UX评估 | 全部完成 | 报告输出 |
| 8 | SUS评分 | >=60(及格) | 问卷统计 |
| 9 | 改进建议 | >=5条 | 报告输出 |
| 10 | 新功能建议 | >=3项 | 报告输出 |
| 11 | 开源调研 | 10项目 | 对比表格 |
| 12 | 借鉴建议 | >=5条 | 报告输出 |
| 13 | 交付文档 | 7篇 | 文件检查 |
| 14 | Render部署 | 稳定运行 | 线上验证 |
