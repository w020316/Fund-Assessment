# 阶段二修复记录：问题闭环与功能入口补全（2026-11-06）

- 上游输入：[阶段一审查报告](./quality-review-2026-11-06.md)（P0=0 / P1=5 / P2=8 / P3=12）
- 起点 HEAD：`66349b3`（阶段一报告提交后）
- 阶段二结束 HEAD：`24ab602`
- 实际执行模型：**DeepSeek-V4.1-Flash**（非用户偏好的 kimi-K3.1pro，无法自行切换）
- 环境：Windows / Python 3.12.9（`D:\dev-tools\Python312\python.exe`）/ 本地 dev 模式 / Render 线上只读探测

---

## 1. 结论摘要

| 项目 | 结果 |
|---|---|
| P1 关闭 | **5 / 5（100%）** |
| P2 关闭 | **8 / 8（100%，高于 90% 门槛）** |
| P3 处理 | 0 / 12（按约定进 backlog，不阻塞本轮） |
| 全量回归 | **2074 passed / 0 failed / 1 warning**（基线 2028） |
| 新增用例 | **+46**（6 个新测试文件 + 2 个测试文件扩充） |
| 覆盖率 | **82%**（14,219 语句 / 2,532 未覆盖；与基线持平） |
| Lint | **165 errors**（与基线持平，无新增；P3-R18 另行清理） |
| 语法 | `compileall -q src web launch.py` exit 0 |
| 前端语法 | `index.html` 两个内联 script 块 `node --check` 通过 |
| 本地冒烟 | 9 个新增/相关端点全部 200；`/` 307 为预期重定向 |
| 是否具备进入阶段三 | **具备** |

---

## 2. 提交清单（每条独立 commit）

| # | commit | 范围 | 对应问题 |
|---|---|---|---|
| 1 | `5b9576f` | `fix(ai)` 核心数据缺失不再用 0 值生成结论 | R-01 |
| 2 | `b1b21d0` | `fix(web)` LLM/外部文本进入 innerHTML 前统一转义 | R-02 |
| 3 | `2fc3ebf` | `fix(api)` 匿名读请求指纹分桶 + 全站安全阀 | R-06 |
| 4 | `074ed94` | `fix(datasource)` 东方财富限速锁移出网络 IO | R-07 |
| 5 | `f37f825` | `fix(datasource)` K 线/搜索缺失值不再转 0 | R-08 |
| 6 | `f908920` | `fix(llm)` chat 总预算 + 前端超时对齐 | R-09 |
| 7 | `34b4051` | `fix(strategy)` 扫描失败 503 + 删除默认假标的 | R-13 |
| 8 | `df2e41b` | `chore(lint)` 移除新增测试未使用导入 | 回归清理 |
| 9 | `1699179` | `feat(market)` 股票检索/个股详情 + 资金面 | R-03 / R-04 |
| 10 | `9919982` | `feat(position)` 组合级 AI 建议 + 失败兜底 | R-05 |
| 11 | `264052c` | `feat(review)` 大盘研判 + 全市场统计 | R-10 |
| 12 | `d81e96b` | `feat(config)` 策略参数编辑 | R-11 |
| 13 | `24ab602` | `feat(scripts)` 股票话术匹配/生成/统计 | R-12 |

---

## 3. P1 逐条闭环

### R-01 [P1] AI 分析不再用 0 值冒充缺失数据

- **原始问题**：`_gather_stock_data` 在大盘/全球新闻成功、个股数据全失败时仍返回非空 dict，`analyze_stock` 的 `not stock_data` 守卫失效；Prompt 用 `.get(key, 0)` 把价格/PE/ROE 写成 0，LLM 返回带置信度的真实形态结论。
- **技术方案**（[ai_service.py#L157-L254](file:///d:/xm/wz/Fund-Assessment/src/core/ai_service.py#L157-L254)）：
  1. `_prompt_value` 把缺失/None/空串/非有限数显式渲染为“缺失”；
  2. Prompt 顶部声明“禁止把缺失当作 0，禁止在关键数据缺失时编造数值”；
  3. `_compute_data_completeness` 按行情 30 / K 线 20 / 财务 20 / 资金流 10 / 公司 5 / 新闻 5 / 两融 5 / 股东 5 计分；
  4. `_attach_data_quality` 在 <40 分时标记 `degraded + DATA_INCOMPLETE`；路由 `_raise_if_degraded` 返回 503 并附完整性分数；
  5. K 线/研报/新闻/龙虎榜/北向/全球新闻空数据同步标注“缺失”。
- **验证**：`tests/test_ai_data_quality.py` 7 例（缺失标注、完整性 0/100、quote-only 降级、路由 503）；对 `000000` 空数据构造 Prompt 实测输出 `当前价格: 缺失`，不再出现 `当前价格: 0`。

### R-02 [P1] LLM/外部文本进入 innerHTML 前统一转义

- **原始问题**：分析师 `key_points`、多空论点、最终决策 `reasoning`、数据页表格字符串单元格均直接拼接；管理员 Token 存于 `localStorage`，CSP 仍允许 `unsafe-inline`，注入可窃取令牌。
- **技术方案**：四处统一调用 `escapeHtml()`；辩论论点兼容 string/object；不改 DOM 结构、样式与交互。
- **验证**：`tests/test_frontend_xss.py` 4 例（4 个注入点正向断言 + 裸插值反向断言）；内联 JS `node --check` 通过。

### R-03 [P1] 个股搜索/详情入口

- **原始问题**：`/api/market/search`、`/stock_detail` 已有实现，前端无入口。
- **技术方案**：行情页新增“股票检索”卡片：代码/名称搜索 → 结果按钮 → 个股详情（现价/涨跌/换手/PE/PB/ROE/市值/资金流/30 日区间）→ 一键跳转 K 线；搜索路由 `price/change_pct` 改为可空，缺失返回 null 并显示“—”。
- **验证**：`tests/test_market_feature_entries.py` 中断言搜索缺失价返回 null、前端入口存在；本地冒烟 `/api/market/search`、`/stock_detail` 均 200。

### R-04 [P1] 资金面入口

- **原始问题**：`/api/market/capital-flow` 已聚合北向与板块主力，前端无卡片。
- **技术方案**：行情页新增“资金面”卡片：北向合计/沪股通/深股通 + 板块主力净流入/净流出 Top5，展示更新时间与质量分；缺失显示“数据缺失”。
- **验证**：`tests/test_market_feature_entries.py` 中断言资金面结构；本地冒烟 200。

### R-05 [P1] 组合级 AI 建议入口

- **原始问题**：`POST /api/agent/portfolio_advice` 无前端入口。
- **技术方案**：持仓页“持仓列表”标题栏新增“组合 AI 建议”，结果卡渲染组合市值/集中度/最大权重/整体风险/建议/再平衡表/逐票建议；全 Provider 失败时展示“组合 AI 建议暂不可用”，并提供“改用组合规则建议”一键跳转 `fund/advice-v2`。
- **验证**：`tests/test_portfolio_advice_entry.py` 2 例（路由返回结构 + 前端入口/兜底文案）；agent 路由既有测试全过。

---

## 4. P2 逐条闭环

| 编号 | 修复内容 | 验证 |
|---|---|---|
| R-06 | 新增 `AnonymousReadGuard`：每指纹 60/min + 全站匿名 300/min；slowapi 默认额度 60→300；写端点 `write:anon` 语义与 10/min 不变；指纹表 4096 上限防内存膨胀 | `tests/test_anon_read_guard.py` 8 例 + 写限流/安全头/trace 55 例 |
| R-07 | `em_get` 锁内只预约发送时间片，`sleep + requests.get` 移到锁外；相邻请求间隔仍 ≥0.3s | `tests/test_em_rate_limit.py` 2 例（锁未被持有、时间片预约） |
| R-08 | `_sanitize_kline_rows` 丢弃 `close<=0` 行、OHLC 缺失用 close 兜底；搜索结果缺失价返回 null | `tests/test_kline_sanitize.py` 3 例 |
| R-09 | `chat()` 新增 `total_timeout`（缺省 25s）；每个 Provider 超时=min(配置, 剩余预算)；令牌桶/重试等待同样受预算约束；前端 `/api/agent/` 90s→30s，`fund_analyze` 40s | `tests/test_llm_budget.py` 3 例（预算收紧、0 预算快速失败、预算内成功） |
| R-10 | 复盘页新增“大盘研判”卡：温度计/涨跌比/情绪/融资余额/大宗交易/股东户数/综合研判/板块信号/资金信号；三接口并行、单点失败隔离 | `tests/test_market_feature_entries.py` 3 例 + 本地冒烟 200 |
| R-11 | 设置页新增“策略参数”卡：递归扁平化 + 数值/布尔/文本编辑 + 点号路径回填 + 非法数字拦截 | `tests/test_strategy_params_entry.py` 3 例（GET/PUT/前端） |
| R-12 | 话术库新增股票匹配（实时行情→`match/stock`→话术卡片+复制）、生成预览（`/scripts/generate`）、分类统计（`/scripts/categories`） | `tests/test_stock_scripts_entry.py` 4 例 |
| R-13 | 三个扫描端点异常统一 503 `SCAN_DATA_UNAVAILABLE`；前端删除 `defaultResults` 假标的（金龙鱼/中芯国际等 2025 数据），空结果显示“暂无扫描结果” | `tests/test_scan_failure.py` 4 例 |

---

## 5. 本地冒烟（热缓存，仅验证可达与状态码）

| 路径 | 状态 | 耗时 |
|---|---|---|
| `/api/health` | 200 | 0.005s |
| `/api/market/search?q=600519` | 200 | 0.004s |
| `/api/market/stock_detail?code=600519` | 200 | 0.004s |
| `/api/market/capital-flow` | 200 | 0.004s |
| `/api/market/assessment` | 200 | 0.004s |
| `/api/market/market_wide_stats` | 200 | 0.004s |
| `/api/market/market_sentiment` | 200 | 0.004s |
| `/api/scripts/categories` | 200 | 0.004s |
| `/api/config/strategies` | 200 | 0.027s |
| `/` | 307 | 0.003s（预期重定向） |

> 耗时来自本地磁盘热缓存，仅证明链路可达；真实冷启动与网络耗时在阶段四测量，不作为性能基线。

---

## 6. 未关闭项（P3 backlog）

| 编号 | 问题 | 状态 |
|---|---|---|
| R-14 | `apiCached` 陈旧缓存无标识 | backlog |
| R-15 | `defaultDebate` 示例论点（当前主路径不可达） | backlog |
| R-16 | `PUT /config/strategies` 异常仍返回 200 + 空对象 | backlog（前端已按空对象判失败） |
| R-17 | Sortino `inf` 潜在序列化问题（当前无路由暴露） | backlog |
| R-18 | ruff 165 errors（F401×121、F841×22、E402×20） | backlog |
| R-19 | 前端单文件 7,000+ 行、无构建链 | backlog（阶段五重构时处理） |
| R-20 | LLM 缓存注释称 LRU 实为 FIFO | backlog |
| R-21 | `renderStatus/renderEmpty` 的 hint 未转义 | backlog |
| R-22 | `fund_analyze_steps` 无鉴权（仅静态数据） | backlog |
| R-23 | 图片上传未校验文件魔数 | backlog |
| R-24 | README 用例数/文件数/运行时版本漂移 | backlog（阶段六交付报告统一修订） |
| R-25 | 数据页残留 `configs.*.default` 假研报/假龙虎榜（未渲染） | backlog |

---

## 7. 阶段二结论与风险

1. **P1 与 P2 已全部关闭**，新增功能入口均通过专项测试与本地冒烟；未修改任何既有测试断言、未删除失败用例、未调整覆盖率阈值。
2. **覆盖率保持 82%**：新增代码较多（前端为主），后端新增分支已由新测试覆盖；前端无 JS 测试运行器，采用静态契约断言 + `node --check` 兜底，已如实记录。
3. **主要遗留风险**：
   - 真实 LLM 全链路未在阶段二消耗额度验证（按约定留到阶段四，每条旅程最多 2 次）；
   - R-02 的 CSP `unsafe-inline` 未收紧，需等阶段五移除内联 `onclick` 后改为 nonce/hash；
   - 新增 UI 使用现有卡片/表格样式，未做视觉打磨，阶段五统一重构；
   - ruff 165 与前端巨石属结构性债务，未在本轮处理。
4. **是否具备进入阶段三**：**具备**。下一阶段按页面×分辨率矩阵检查新增入口在桌面/平板/移动端的布局、触摸目标、状态展示与兼容性。

---

## 附录：复现命令

```powershell
D:\dev-tools\Python312\python.exe -m pytest tests/ -q
D:\dev-tools\Python312\python.exe -m pytest tests/ -q --cov=src --cov=web --cov-report=term
D:\dev-tools\Python312\python.exe -m ruff check src web tests --statistics
D:\dev-tools\Python312\python.exe -m compileall -q src web launch.py
node --check .tmp\index-inline.js   # 先运行 .tmp\extract_inline_js.py 抽取内联脚本
powershell -ExecutionPolicy Bypass -File .tmp\smoke_stage2.ps1
```
