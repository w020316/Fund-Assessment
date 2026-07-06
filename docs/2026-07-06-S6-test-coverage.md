# S6 测试补全记录

**日期**: 2026-07-06
**子项目**: S6(测试补全)
**前置上下文**: S1-S5 累计 149 用例,但存在 3 个覆盖缺口:DataCache 核心模块未测、话术库路由层未测、国际市场路由层未测。

---

## 1. 模块概览

| # | 测试文件 | 覆盖目标 | 用例数 | 状态 |
|---|---------|---------|--------|------|
| 1 | `tests/test_cache.py` | `src/core/cache.py` DataCache 缓存 | 24 | ✅ |
| 2 | `tests/test_scripts_routes.py` | `web/routes/scripts.py` 话术库路由 | 21 | ✅ |
| 3 | `tests/test_global_market_routes.py` | `web/routes/global_market.py` 国际市场路由 | 10 | ✅ |
| 4 | `tests/test_api.py`(修复) | 同步 S5 品牌改造 | — | ✅ |

**新增**:55 用例
**全量**:S1-S6 累计 **204 passed**,无回归

---

## 2. 详细测试记录

### 2.1 DataCache 单元测试 `tests/test_cache.py`(24 用例)

**覆盖模块**: `src/core/cache.py`(核心缓存基础设施,所有路由依赖)

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|---------|
| TestSerialize | 4 | Pydantic 模型/set/bytes/其他类型序列化 |
| TestCacheGetSet | 6 | 读写/不存在/Pydantic/列表/覆盖/自定义 TTL |
| TestCacheTTL | 3 | 过期返回 None/未过期返回值/默认 TTL |
| TestCacheDelete | 2 | 删除存在/不存在键 |
| TestCacheClear | 2 | 清空所有/空目录 |
| TestSafeKey | 4 | 冒号/斜杠/问号星号/特殊字符持久化 |
| TestCorruptedCache | 2 | JSON 损坏/缺 timestamp 兜底 |
| TestCacheDirCreation | 1 | 目录不存在自动创建 |

**关键技术点**:
- 用 `tmp_path` fixture 隔离每个测试的缓存目录,避免相互干扰
- 用 `patch("src.core.cache.time.time", ...)` 模拟时间流逝测 TTL 过期
- 直接写文件模拟 JSON 损坏,验证兜底逻辑

### 2.2 话术库路由测试 `tests/test_scripts_routes.py`(21 用例)

**覆盖模块**: `web/routes/scripts.py`(6 个端点)

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|---------|
| TestListScripts | 6 | 全部/stock/fund/scene/组合过滤/_meta |
| TestCategories | 3 | stock/fund 分类 + 场景列表 |
| TestGetScript | 2 | 存在/不存在 |
| TestGenerateScript | 3 | 完整生成/缺失变量「—」/不存在模板 |
| TestMatchFund | 3 | take_profit/hold/watch 信号映射 |
| TestMatchStock | 4 | 大涨卖出/大跌观望/正常持有/浮盈止盈 |

**关键技术点**:
- 用 `TestClient` + 直接 POST JSON,无需 mock(话术库纯模板,无外部依赖)
- 验证变量填充正确性("贵州茅台突破关键阻力位 1800...")
- 验证信号 → 场景映射逻辑(take_profit→take_profit, watch→wait)

### 2.3 国际市场路由测试 `tests/test_global_market_routes.py`(10 用例)

**覆盖模块**: `web/routes/global_market.py`(6 个端点)

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|---------|
| TestIndices | 3 | 列表/_meta/空数据 |
| TestUsHot | 1 | 美股热门 |
| TestHkHot | 1 | 港股热门 |
| TestUsRealtime | 2 | 查询/空 codes |
| TestHkRealtime | 1 | 查询 |
| TestOverview | 2 | 完整总览/全失败空结构 |

**关键技术点**:
- **清缓存 fixture**:`autouse=True` 的 `clear_global_cache` 在每个测试前后清空文件缓存,避免 mock 被缓存命中跳过
- **mock 模块级导入**:`patch("web.routes.global_market.ds2.get_global_indices", ...)` 而非 patch 原函数
- **完整字段 mock 数据**:路由用 Pydantic 模型校验,mock 数据需含 prev_close/high/low 等必填字段

### 2.4 test_api.py 修复

S5 把品牌从 "OpenClaw" 改为 "QuantFlow Pro",但 `test_api.py::TestStaticFiles::test_index_html_accessible` 仍断言旧品牌。修复:

```python
# 改前
assert "OpenClaw" in resp.text
# 改后
assert "QuantFlow Pro" in resp.text
```

---

## 3. 测试结果

### 3.1 新增测试单独运行

```
tests/test_cache.py                    24 passed
tests/test_scripts_routes.py           21 passed
tests/test_global_market_routes.py     10 passed
                                      --------
新增合计                               55 passed
```

### 3.2 全量回归测试

```
============================== 204 passed, 2 warnings in 9.45s ===============================
```

| 子项目 | 测试文件数 | 用例数 |
|--------|-----------|--------|
| S1 | 6(test_auth/convert/data_fabrication/llm_router/market_routes/monitor_routes) | 81 |
| S2 | 1(test_fund_advisor) | 27 |
| S3 | 1(test_global_market) | 15 |
| S4 | 1(test_script_library) | 26 |
| S5 | 0(纯前端改造) | 0 |
| S6 | 3(test_cache/scripts_routes/global_market_routes) + 1 修复 | 55 |
| **合计** | **12** | **204** |

---

## 4. 覆盖率提升分析

### 4.1 已覆盖模块

| 模块 | 覆盖方式 |
|------|---------|
| `src/core/cache.py` | 单元测试(纯逻辑) |
| `src/core/data_source_v2.py` | S1 单元测试(mock 网络层) |
| `src/core/llm_router.py` | S1 单元测试 |
| `src/core/data_validator.py` | 单元测试 |
| `src/utils/auth.py` | S1 单元测试 |
| `src/utils/convert.py` | S1 单元测试 |
| `src/analysis/fund_advisor.py` | S2 单元测试 |
| `src/analysis/script_library.py` | S4 单元测试 |
| `web/routes/market.py` | S1 路由测试 |
| `web/routes/monitor.py` | S1 路由测试 |
| `web/routes/scripts.py` | S6 路由测试 |
| `web/routes/global_market.py` | S6 路由测试 |
| `web/api.py` | test_api 健康检查 |

### 4.2 未覆盖模块(评估)

| 模块 | 原因 | 风险 |
|------|------|------|
| `web/routes/fund.py` | 依赖真实 data_source_v2,mock 成本高 | 中(核心已测) |
| `web/routes/dashboard.py` | 依赖 broker/risk_manager 状态,需复杂 fixture | 低 |
| `web/routes/trade.py` | 依赖交易执行器,需复杂 fixture | 低 |
| `src/agents/*` | AI Agent 依赖 LLM,集成测试更适合 | 低 |
| `src/strategies/*` | 策略模块独立,可后续补 | 低 |

**决策**:S6 聚焦"核心基础设施 + S2-S4 新增功能"的测试补全,已覆盖最关键缺口。剩余模块依赖复杂外部状态,投入产出比低,留待集成测试或后续迭代。

---

## 5. 文件清单

**新增**:
- `tests/test_cache.py`(200 行)— DataCache 24 用例
- `tests/test_scripts_routes.py`(170 行)— 话术库路由 21 用例
- `tests/test_global_market_routes.py`(174 行)— 国际市场路由 10 用例
- `docs/2026-07-06-S6-test-coverage.md`— 本文档

**修改**:
- `tests/test_api.py`— 同步 S5 品牌改造(1 处断言)

---

## 6. 后续计划

S6 完成。下一子项目:
- **S7 交付文档**:整理 S1-S7 全部成果,生成最终交付报告(README/部署/架构/测试)
