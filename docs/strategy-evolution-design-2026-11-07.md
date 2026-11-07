# 策略自进化 · 增量架构设计与任务分解（2026-11-07）

作者：高见远（架构师）· 团队 software-stage5-design
设计依据：`docs/strategy-evolution-prd-2026-11-07.md`（P0-2/P0-4 按主理人裁定修订版）+ 实际代码调研。

**关键事实**：`TradingQuant` 目前只读模块级硬编码 `WEIGHTS`/`_signal_from_score` 阈值（src/strategies/trading_quant.py:33-39、202-214），**config/strategies.yaml 的 trading_quant 段当前不被任何策略代码消费**——若不做参数接线，"激活"只是改了 yaml 而分析链路无感。因此「参数生效接线」列为后端必做任务（B2）。

主理人裁定（待明确事项）：#1 QA 用性质断言（单调性/有界）非数值断言；#2 P0 模拟仅用 buy/sell 两执行阈值，分级仓位（1/0.7/0.4）留作 P2 预留接口；#3 Render 重启 data/ 丢失可接受（yaml 是真源），交付说明写明，不做持久化增强。

---

## 1. 实现方案

### 1.1 模块划分

```
src/strategies/evolution.py      # 纯逻辑核心：变异器 / 净值择时评估器 / 适应度 / 代际存储（无任何 FastAPI 依赖，可独立单测）
web/routes/evolution.py          # 6 个端点：挂 require_admin + 复用回测单飞锁；只做参数校验、asyncio 编排、错误码映射
```

复用清单（零重写）：
| 既有设施 | 复用方式 |
|---|---|
| `web/routes/strategy.py:26` `_BACKTEST_GUARD` + `_single_backtest_guard` | **复用同一把锁**（P0-10）：在 strategy.py 加一行公开别名 `single_backtest_guard = _single_backtest_guard`，evolution.py import 该依赖 → 进化与回测天然互斥，防止叠加打满 512MB |
| `src/core/fund_nav_source.py:266` `get_fund_nav_history(code, period)` | 取 9 只持仓基金净值，`period="6m"`（180 自然日 ≈ 120 交易日），自动走 `data/cache/fund_nav_history/` DataCache 文件缓存（TTL 1h）——**整代评估只取一次数据，N 候选共享** |
| `web/routes/fund.py:170` `_load_positions()` | 持仓基金代码来源（`web/user_fund_positions.json`，当前恰 9 只）；evolution.py 直接 `safe_read_json` 同文件，不动 fund.py |
| `src/utils/file_io.py:37` `atomic_write_json` / `safe_read_json` | generations / lineage.json / active.json 全部原子写 + .bak |
| `web/routes/config.py:131` `_save_yaml` / `_load_yaml` | 激活写 strategies.yaml：在 config.py 增加公开薄封装 `load_strategies_yaml()` / `save_strategies_yaml()`（原私有函数改由公开函数调用，端点行为零变化），evolution.py import 之 |
| `asyncio.to_thread` + `asyncio.wait_for` | 与 /analyze、/backtest 完全一致的编排模式（strategy.py:269、418） |

### 1.2 净值择时模拟评估器（P0-4 具体口径）

**代理分数矩阵（一次性预计算，全部候选共享）**：
trading_quant 五维是对个股评分的维度，基金净值无此数据 → 用**净值可观察量的代理分数**，9 只基金 × 120 日逐日构造 s[d][f][k]（k=五维），明确标注"代理口径，非真实五维数据"：
- technical = 动量映射：`50 + 25*tanh(mom5/0.03) + 25*tanh(mom20/0.08)`，clip [0,100]（mom5/mom20 为净值 5/20 日涨幅）
- capital = 动能差：`50 + 50*tanh((mom5-mom20)/0.05)`（短期相对长期的加速度，代理资金推动）
- fundamental = 趋势质量：`50 + 50*(R² of 60 日线性回归 of ln(nav))`（代理基本面稳定性）
- news = 事件代理：`50 + 25*tanh(突破20日高/低的天数信号)`（近 5 日创 60 日新高 +、新低 −）
- sentiment = 波动情绪：`50 + 50*tanh((目标波动率参考 1.2% − 已实现 20 日年化波动)/0.08)`（低波动加分）

**合成与择时**（每候选，纯 numpy 数组运算，单候选 <5ms）：
1. 候选参数 `weights`（5 维，归一）与 `signals`（5 阈值）
2. 每日每基金合成分子：`c[d][f] = Σ_k weights[k] × s[d][f][k]`
3. 组合信号：`sig[d] = mean_f c[d][f]`（等权 9 只）
4. 阈值择时：`position[d] = 1 if sig[d] ≥ signals.buy else (0 if sig[d] ≤ signals.sell else position[d-1])`（持仓状态保持，首日按 sig 判定；strong_buy/watch/hold 参与变异但 P0 模拟只用 buy/sell 两执行阈值，其余阈值改变 sig 分布间接影响结果——**写入设计注释**）
5. 组合日收益：`r[d] = position[d-1] × mean_f(fund_daily_return[d])`（T+1 执行语义：昨日信号今日持仓，**与真实交易 T+1 语义同构，不触碰 TradeExecutor**）
6. 指标：年化 = `(1+总收益)^(252/n)−1`；最大回撤 = 净值峰谷；夏普 = `mean(r)/std(r)×√252`（std=0 时记 0）；胜率 = `r[d]>0` 占比

**适应度**（回撤惩罚项，PRD 权重 0.4/0.25/0.25/0.1）：
```
fitness = 0.4*tanh(annual/0.30) + 0.25*tanh(sharpe/2.0) + 0.1*win_rate − 0.25*min(1, |max_dd|/0.30)
```
tanh 归一：annual 30%/sharpe 2 视为满分档；回撤 30% 扣满惩罚。适应度越高越好，全指标同代可比。

**变异器（P0-3）**：`numpy.random.default_rng(seed)`（seed=f"gen{n}-{ts}"）：
- weights：每维 `+N(0,0.05)` → clip [0.05,0.5] → 除以总和归一
- signals：各阈值 `+N(0,3)` → 限幅 strong_buy∈[70,95], buy∈[55,80], watch∈[40,65], hold∈[25,50], sell∈[5,35] → **强制排序** strong_buy>buy>watch>hold>sell（不满足时取相邻中点修复）
- 每代 N=5 候选；父代参数先重评一次作基线（相同评估窗，保证可比）

**预算收割**：`time.monotonic()` 起表，逐候选评估前检查剩余；25s 到点收割已完成者。数据获取失败（≥2 只基金净值样本 <30 日）→ 抛 `EvolutionDataUnavailable` → 503，不写任何文件。

### 1.3 激活生效链（P0-7 + 接线）
`activate(gen)` → diff 校验（前端已二次确认）→ `_save_yaml` 更新 `trading_quant` 段 → 更新 active.json → **B2 接线使下一次 analyze/scan 实例化 TradingQuant 时读到新参数**。

---

## 2. 文件列表及相对路径

**新增（5）**
| 文件 | 内容 |
|---|---|
| `src/strategies/evolution.py` | 变异器、代理分数矩阵、择时模拟、适应度、代际存储（约 450 行） |
| `web/routes/evolution.py` | 6 端点 + Pydantic 模型 + 单飞锁复用 + 503 错误码映射（约 260 行） |
| `data/strategy_evolution/` | 运行时生成：generations/、lineage.json、active.json（代码建目录，不入库） |
| `tests/test_strategy_evolution.py` | 第 7 节全部用例 |
| `web/static/js/evolution.js` | 前端逻辑（/* ===== STAGE5: evolution ===== */ 锚点） |

**修改（5，均最小 diff）**
| 文件 | 位置 | 性质 |
|---|---|---|
| `web/routes/strategy.py` | :26 后 | 增 1 行公开别名 `single_backtest_guard = _single_backtest_guard`（复用锁，零行为变化） |
| `src/strategies/trading_quant.py` | :33-39、:42-44、:228-241 | **参数接线**：新增 `_read_active_params()`（safe_load strategies.yaml，异常/缺段回退模块常量 WEIGHTS/阈值表）；`__init__` 存 `self.weights/self.signals`；`stock_analysis` 与 `_signal_from_score` 改用实例参数（默认值=现硬编码，未激活时行为不变） |
| `web/routes/config.py` | :131 附近 | 增 `load_strategies_yaml()` / `save_strategies_yaml()` 公开封装（内部调用原私有函数，端点改调公开名，行为零变化） |
| `web/api.py` | :262 后 | 增 1 行 `app.include_router(evolution_route.router, prefix="/api/strategy/evolution", tags=["策略进化"])` |
| `web/static/index.html` | :1173 子 tab 行 + 策略页容器区 | 新增「策略进化」hud-subtab + 页面容器（仅新增节点，带 STAGE5 锚点注释，不动既有 10→5 IA） |

---

## 3. 数据结构

**generations/gen\_<n>\_<ts>.json**（≤50KB，保留最近 50 代）
```json
{
  "version": 1,
  "generation_id": 12,
  "created_at": "2026-11-07T15:30:00+08:00",
  "parent_id": 11,
  "origin": "evolution",
  "params": {
    "weights": {"technical": 0.22, "capital": 0.31, "fundamental": 0.11, "news": 0.21, "sentiment": 0.15},
    "signals": {"strong_buy": 82, "buy": 66, "watch": 51, "hold": 34, "sell": 19}
  },
  "metrics": {
    "annual_return": 0.142, "max_drawdown": -0.083, "sharpe": 1.21,
    "win_rate": 0.55, "fitness": 0.3872,
    "eval_window_days": 120, "fund_count": 9, "fund_codes": ["006503"],
    "evaluated_candidates": 5, "budget_used_ms": 18320
  },
  "parent_fitness": 0.3501,
  "improved_over_parent": true
}
```

**lineage.json**（索引，只存摘要；详情读单代文件）
```json
{
  "version": 1, "updated_at": "...", "next_gen_id": 13,
  "generations": [
    {"generation_id": 12, "created_at": "...", "parent_id": 11,
     "fitness": 0.3872, "improved": true, "origin": "evolution",
     "file": "generations/gen_12_20261107T153000.json",
     "params_digest": "a1b2c3d4"}
  ]
}
```

**active.json**
```json
{
  "version": 1, "generation_id": 12,
  "activated_at": "...", "activated_by": "admin",
  "params_snapshot": {"weights": {}, "signals": {}},
  "note": "真源为 config/strategies.yaml 的 trading_quant 段；此文件是谱系指针与展示快照"
}
```
适应度指标结构即 metrics 段（第 1.2 节公式）；`generation_id=null` 表示"从未激活，当前为出厂参数"。

---

## 4. 接口定义（全部挂 `Depends(require_admin)`，逐条显式写入代码注释）

| # | 端点 | 请求 | 成功响应 | 错误码 |
|---|---|---|---|---|
| 1 | `POST /api/strategy/evolution/run` | `{}`（无 body 或 `{"candidates": 1-10}` 可选） | `{generation_id, improved, parent_fitness, new_fitness, metrics, candidates_evaluated, elapsed_ms, message}`；improved=false 时 generation_id=null（200 非错误） | 503 `EVOLUTION_BUSY`/`EVOLUTION_TIMEOUT`/`EVOLUTION_DATA_UNAVAILABLE`；401 未鉴权 |
| 2 | `GET /api/strategy/evolution/lineage` | — | `{active_generation_id, count, generations: [<摘要>]}`（按代号倒序） | 401 |
| 3 | `GET /api/strategy/evolution/generations/{gen_id}` | path: int | 单代完整 JSON | 400 `INVALID_GEN_ID`；404 `GEN_NOT_FOUND` |
| 4 | `GET /api/strategy/evolution/compare?gen={n}` | query: gen 可选（缺省=最新代） | `{active, candidate, param_diff, metric_diff}` | 404 `GEN_NOT_FOUND`/`NO_ACTIVE_GENERATION`；503 `EVOLUTION_CORRUPTED` |
| 5 | `POST /api/strategy/evolution/activate` | `{generation_id: int}`（必填） | `{activated: true, generation_id, activated_at, yaml_updated: true, params_written, prev_generation_id}` | 400/404/503 `ACTIVATE_WRITE_FAILED`（yaml 写失败时**不更新 active.json**，先写 yaml 后写指针） |
| 6 | `GET /api/strategy/evolution/status` | — | `{running, last_run, lock_holder}` | 401 |

错误响应统一走既有 `HTTPException(status_code=503, detail={"code","message","retryable"})` 结构（与 strategy.py:294 `_scan_unavailable` 同款）。

---

## 5. 程序调用流程

```
POST /run
  ├─ Depends(require_admin)                    [web 层，401 短路]
  ├─ Depends(single_backtest_guard)            [threading.Lock 非阻塞 acquire；占用→503 EVOLUTION_BUSY]
  │    └─ 与 POST /api/strategy/backtest 共用同一把锁（P0-10）
  ├─ asyncio.wait_for(asyncio.to_thread(run_evolution), timeout=25)   ← wait_for 边界
  │    run_evolution（线程池内，同步纯 CPU/文件 I/O）：
  │      1. 读 active.json + lineage.json → 父代参数（无则用 yaml 现值/出厂常量）
  │      2. 读 9 只基金码 → get_fund_nav_history(period="6m")（<2 只可用或样本<30 日 → 异常）
  │      3. 预计算代理分数矩阵（9×120×5，numpy）
  │      4. 逐候选：预算检查→变异→评估→记录；25s 到点收割
  │      5. Top-1 vs 父代 fitness：更优→写 gen 文件+lineage；无更优→零写盘 improved=false
  │      ✗ 任一步骤异常→路由层转 503；【铁律：失败路径零写盘】
  └─ 释放锁（dependency finally）→ 返回响应

POST /activate
  ├─ require_admin → 校验 gen_id（404 短路）
  ├─ asyncio.to_thread: save_strategies_yaml(merge trading_quant 段)   [先写参数真源]
  ├─ asyncio.to_thread: atomic_write_json(active.json)                 [后写指针]
  └─ 任一失败：ACTIVATE_WRITE_FAILED；yaml 已写而指针未写时以 yaml 为准自愈

后台零任务：无定时器、无线程常驻、无 Signal 产生 —— T+1/RiskManager 零触碰的结构性保证
```

---

## 6. 任务列表（按实现顺序）

### 阶段 A：后端
| # | 任务 | 依赖 | 验收标准 |
|---|---|---|---|
| B1 | 代际存储层（evolution.py 前半）：目录建立、gen/lineage/active 读写、50 代滚动清理、params_digest | 无 | 单测：读写 round-trip、.bak 回退、第 51 代清理最旧、损坏文件走 safe_read_json |
| B2 | **trading_quant 参数接线**：`_read_active_params()` + 实例化读参 + `_signal_from_score` 参数化 | 无 | 单测：yaml 有段则生效、缺段/损坏回退硬编码；未配置时既有测试全绿 |
| B3 | 变异器 + 归一化 + 阈值序约束 | B1 | 单测：1000 次变异全部 sum(weights)=1±1e-9、序约束、限幅 |
| B4 | 净值择时评估器 + 适应度 | B3 | 单测：合成净值序列已知答案；buy=sell=50 时 position 恒定；T+1 错位 1 日用例 |
| B5 | run_evolution 编排：预算收割、失败零写盘 | B4 | 单测：mock 25s 超时→收割部分候选；mock 数据不可用→503 且目录无新文件 |
| B6 | web/routes/evolution.py 6 端点 + config.py 封装 + strategy.py 锁别名 + api.py 挂载 | B5 | TestClient：6 端点 401/400/404/503/200 全路径；并发两 run 第二个 503；activate 后 GET config 返回新参数 |
| B7 | T+1/风控隔离验证用例 | B6 | import 闭包不含 executor/risk_manager/TradeExecutor；全量 pytest 后 trade.db/risk.db mtime 不变 |
| B8 | 503 兜底专项：三路径 + 失败后 lineage/active/yaml 逐字节不变 | B6 | 单测对比失败前后三文件 sha256 |

### 阶段 B：前端（STAGE5 锚点、48px 触摸目标、红涨绿跌）
| # | 任务 | 依赖 | 验收标准 |
|---|---|---|---|
| F1 | 「策略进化」子 tab + 页面容器（index.html 增量） | B6 | 375/768/1440 无横向滚动；子 tab 平级、键盘可达 |
| F2 | evolution.js：谱系列表 + 单代详情 | F1 | 激活代高亮；空态/加载/错误用既有组件；Token 门控走 tokenGateHTML |
| F3 | 对比面板：指标对照表 + 参数 diff 高亮 | F2 | 双列逐行渲染；gen=最新代缺省可用 |
| F4 | 操作流：run 置灰+10s 轮询 → activate 二次确认（先 diff）→ 回滚快捷 | F3 | 503 展示 code+message；弹窗 ESC 可关；触摸目标 ≥48px |

---

## 7. 测试要点（tests/test_strategy_evolution.py）

1. 变异归一化：weights 和=1、clip 边界、signals 严格递减、种子可复现
2. 评估器正确性：恒涨净值→fitness 分量符号正确；buy=sell 边界不抖动；等权
3. 预算收割：mock 慢评估，第 3 候选到点 → evaluated_candidates=2
4. 503 兜底：BUSY/TIMEOUT（wait_for 后锁仍正确释放）/DATA_UNAVAILABLE
5. 失败零写盘：三条失败路径前后四文件哈希不变
6. 激活写 yaml：activate 后 yaml 段=该代参数；.bak 存在；再 activate 旧代=回滚；写失败→active.json 未动
7. T+1/风控不受影响：import 闭包断言 + trade.db/risk.db mtime 不变 + test_executor/test_risk 全绿
8. 鉴权：6 端点无 token 全 401（参数化）
9. 接线回归：yaml 无段时与旧实现输出逐位一致（快照测试）
10. 单飞互斥：evolution 进行中调 /api/strategy/backtest → 503（反方向也成立）

---

## 8. 共享知识/跨文件约定

- 锁唯一真源 = `web/routes/strategy.py` 的 `_BACKTEST_GUARD`，任何新重型任务必须复用，禁止再开新锁
- evolution.py 不 import 任何 fastapi/web 模块（纯逻辑层）；路由文件不写业务算法
- 代文件 ≤50KB：净值序列**不落盘**（可由缓存重取），只落指标与参数
- 写序铁律：yaml（参数真源）→ active.json（指针）；读侧以 yaml 为准
- 代理分数是模拟口径，代码注释与前端详情页都要标注"基于历史净值的假设性模拟，非真实五维数据"（对齐 index.html:1782 免责文案惯例）
- Render 重启 data/ 丢失可接受：代际文件属可再生分析产物，yaml 是真源（交付说明写明）
