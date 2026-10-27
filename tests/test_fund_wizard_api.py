"""阶段5 补录向导 API 单元测试

覆盖 web/routes/fund.py 与 web/routes/config.py 的新增/增强端点:
- POST   /api/fund/positions/validate   dry-run diff 校验(只读)
- POST   /api/fund/positions            响应增强(summary 变更摘要回执)
- POST   /api/fund/positions/add        响应增强(changes 字段级 old→new)
- GET    /api/fund/positions            响应增强(nav_date/confidence/confidence_reason)
- GET    /api/config/sector-map         板块映射读取
- _nav_confidence / _latest_weekday / _diff_positions 纯函数

铁律:全部用 mock/touch 临时数据,绝不读写 web/user_fund_positions.json(真实持仓)。
"""
from __future__ import annotations

import datetime as _dt
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def no_admin_token():
    """确保 ADMIN_TOKEN 未配置(开发模式放行),避免鉴权干扰"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_TOKEN", None)
        yield


# ============ mock 数据(绝不触碰真实持仓文件) ============

_MOCK_EXISTING = [
    {"fund_code": "110022", "fund_name": "易方达消费行业", "shares": 900.0,
     "cost_nav": 3.30, "buy_date": "2025-01-15", "note": ""},
    {"fund_code": "002891", "fund_name": "某CPO基金", "shares": 1000.0,
     "cost_nav": 1.50, "buy_date": "2025-03-01", "note": "板块A"},
]

# 净值滞后的种子代码(架构拍板:不硬编码进逻辑,仅作测试种子,mock 其 nav_date 滞后)
_LAG_CODES = ["002891", "018957", "017811", "018816"]


def _make_quotes(update_time_by_code: dict[str, str]) -> list[dict]:
    """构造腾讯实时行情 mock 返回"""
    return [
        {"code": code, "name": f"基金{code}", "nav": 2.0,
         "change_pct": 0.5, "update_time": ts}
        for code, ts in update_time_by_code.items()
    ]


# ============ 纯函数单元测试 ============

class TestLatestWeekday:
    """_latest_weekday:工作日回退(Mon-Fri,无节假日历)"""

    def test_monday_returns_itself(self):
        from web.routes.fund import _latest_weekday
        assert _latest_weekday(_dt.date(2026, 10, 26)) == _dt.date(2026, 10, 26)  # 周一

    def test_saturday_rolls_back_to_friday(self):
        from web.routes.fund import _latest_weekday
        assert _latest_weekday(_dt.date(2026, 10, 24)) == _dt.date(2026, 10, 23)  # 周六→周五

    def test_sunday_rolls_back_to_friday(self):
        from web.routes.fund import _latest_weekday
        assert _latest_weekday(_dt.date(2026, 10, 25)) == _dt.date(2026, 10, 23)  # 周日→周五


class TestNavConfidence:
    """_nav_confidence:滞后 ≥1 天 → low;正常 → high;缺失/非法 → low(fail-closed)"""

    def test_fresh_nav_is_high(self):
        from web.routes.fund import _nav_confidence
        today = _dt.date(2026, 10, 26)  # 周一
        conf, reason = _nav_confidence("2026-10-26 15:00", today)
        assert conf == "high"
        assert reason == ""

    def test_weekend_nav_still_high_on_monday(self):
        from web.routes.fund import _nav_confidence
        # 周一收盘后,最新净值日期仍是上周五 → 与"最近交易日"(周一)比滞后 1 天?
        # 规则口径:与最近交易日(工作日回退)比较。周一的最近交易日=周一,
        # 上周五净值 → lag=3 → low。这正是"盘后未更新"的真实语义。
        from web.routes.fund import _nav_confidence
        conf, reason = _nav_confidence("2026-10-23 15:00", _dt.date(2026, 10, 26))
        assert conf == "low"
        assert "3" in reason

    def test_lagging_nav_is_low(self):
        from web.routes.fund import _nav_confidence
        conf, reason = _nav_confidence("2026-10-20 15:00", _dt.date(2026, 10, 26))
        assert conf == "low"
        assert "滞后最近交易日" in reason

    def test_missing_date_is_low(self):
        from web.routes.fund import _nav_confidence
        conf, reason = _nav_confidence("", _dt.date(2026, 10, 26))
        assert conf == "low"
        assert "缺失" in reason

    def test_unparseable_date_is_low(self):
        from web.routes.fund import _nav_confidence
        conf, _ = _nav_confidence("not-a-date", _dt.date(2026, 10, 26))
        assert conf == "low"

    def test_future_nav_is_high(self):
        from web.routes.fund import _nav_confidence
        # 净值日期晚于最近交易日(时区/数据源异常) → 不误报
        conf, _ = _nav_confidence("2026-10-27 15:00", _dt.date(2026, 10, 26))
        assert conf == "high"


class TestDiffPositions:
    """_diff_positions:added / updated(字段级 old→new) / deleted"""

    def test_added_updated_deleted(self):
        from web.routes.fund import _diff_positions
        existing = [
            {"fund_code": "110022", "fund_name": "A", "shares": 900.0, "cost_nav": 3.30,
             "buy_date": "2025-01-15", "note": ""},
            {"fund_code": "002891", "fund_name": "B", "shares": 1000.0, "cost_nav": 1.5,
             "buy_date": "", "note": ""},
        ]
        incoming = [
            # 更新:shares/cost_nav 变化
            {"fund_code": "110022", "fund_name": "A", "shares": 1000.0, "cost_nav": 3.21,
             "buy_date": "2025-01-15", "note": ""},
            # 新增
            {"fund_code": "161725", "fund_name": "C", "shares": 500.0, "cost_nav": 0.85,
             "buy_date": "", "note": ""},
            # 002891 不在 → 删除
        ]
        diff = _diff_positions(existing, incoming)
        assert [a["fund_code"] for a in diff["added"]] == ["161725"]
        assert [d["fund_code"] for d in diff["deleted"]] == ["002891"]
        assert len(diff["updated"]) == 1
        upd = diff["updated"][0]
        assert upd["fund_code"] == "110022"
        assert upd["changes"]["shares"] == {"old": 900.0, "new": 1000.0}
        assert upd["changes"]["cost_nav"] == {"old": 3.30, "new": 3.21}
        # buy_date 未变 → 不出现在 changes
        assert "buy_date" not in upd["changes"]

    def test_no_change_no_update_entry(self):
        from web.routes.fund import _diff_positions
        existing = [{"fund_code": "110022", "fund_name": "A", "shares": 100.0,
                     "cost_nav": 1.0, "buy_date": "", "note": ""}]
        diff = _diff_positions(existing, [dict(existing[0])])
        assert diff["added"] == [] and diff["updated"] == [] and diff["deleted"] == []


# ============ API 端点测试 ============

class TestValidatePositions:
    """POST /api/fund/positions/validate(dry-run,只读)"""

    def test_validate_returns_diff_summary(self, client):
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_EXISTING)), \
             patch("web.routes.fund._save_positions") as mock_save, \
             patch("web.routes.fund.get_fund_realtime_tencent", return_value=[]):
            resp = client.post("/api/fund/positions/validate", json={
                "positions": [
                    {"fund_code": "110022", "fund_name": "易方达消费行业",
                     "shares": 1000.0, "cost_nav": 3.21, "buy_date": "2025-01-15", "note": ""},
                    {"fund_code": "161725", "fund_name": "招商白酒",
                     "shares": 500.0, "cost_nav": 0.85, "buy_date": "", "note": ""},
                ]
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        summary = data["summary"]
        assert [a["fund_code"] for a in summary["added"]] == ["161725"]
        assert [d["fund_code"] for d in summary["deleted"]] == ["002891"]
        assert summary["updated"][0]["changes"]["shares"] == {"old": 900.0, "new": 1000.0}
        # dry-run 铁律:绝不落盘
        mock_save.assert_not_called()

    def test_validate_readonly_does_not_modify_file(self, client):
        """回归保护:dry-run 即使 _load_positions 返回真实结构也不触发写入"""
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_EXISTING)), \
             patch("web.routes.fund._save_positions") as mock_save, \
             patch("web.routes.fund.get_fund_realtime_tencent", return_value=[]):
            resp = client.post("/api/fund/positions/validate", json={"positions": []})
        assert resp.status_code == 200
        mock_save.assert_not_called()
        # 空提交 → 现有持仓全部标记为将删除(全量覆盖语义)
        assert len(resp.json()["summary"]["deleted"]) == 2

    def test_validate_flags_low_confidence_warnings(self, client):
        """净值滞后持仓 → warnings 中出现 low_confidence"""
        quotes = _make_quotes({
            "002891": "2026-10-20 15:00",  # 滞后
            "110022": "2026-10-26 15:00",  # 正常(周一)
        })
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_EXISTING)), \
             patch("web.routes.fund.get_fund_realtime_tencent", return_value=quotes), \
             patch("web.routes.fund._today", return_value=_dt.date(2026, 10, 26)):
            resp = client.post("/api/fund/positions/validate", json={"positions": []})
        data = resp.json()
        warn_codes = [w["fund_code"] for w in data["warnings"]]
        assert "002891" in warn_codes
        assert "110022" not in warn_codes
        assert all(w["level"] == "low_confidence" for w in data["warnings"])

    def test_validate_rejects_invalid_position_422(self, client):
        """非法输入(代码非6位数字/负份额) → 422,由 pydantic 拦截"""
        resp = client.post("/api/fund/positions/validate", json={
            "positions": [{"fund_code": "abc", "fund_name": "x", "shares": -1.0}]
        })
        assert resp.status_code == 422


class TestSavePositionsSummary:
    """POST /api/fund/positions(全量覆盖,响应增强 summary)"""

    def test_save_returns_summary(self, client):
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_EXISTING)), \
             patch("web.routes.fund._save_positions", return_value=True) as mock_save:
            resp = client.post("/api/fund/positions", json={
                "positions": [
                    {"fund_code": "110022", "fund_name": "易方达消费行业",
                     "shares": 1000.0, "cost_nav": 3.21, "buy_date": "2025-01-15", "note": ""},
                ]
            })
        assert resp.status_code == 200
        data = resp.json()
        # 向后兼容:存量字段不变
        assert data["success"] is True
        assert "已保存 1 只基金持仓" in data["message"]
        # 新增:summary 变更摘要
        assert data["summary"]["added"] == 0
        assert data["summary"]["updated"] == 1
        assert data["summary"]["deleted"] == 1
        assert data["summary"]["deleted_codes"] == ["002891"]
        mock_save.assert_called_once()

    def test_save_failure_summary_is_none(self, client):
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_EXISTING)), \
             patch("web.routes.fund._save_positions", return_value=False):
            resp = client.post("/api/fund/positions", json={"positions": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["summary"] is None


class TestAddPositionChanges:
    """POST /api/fund/positions/add(upsert,响应增强 changes)"""

    def test_add_existing_returns_changes(self, client):
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_EXISTING)), \
             patch("web.routes.fund._save_positions", return_value=True):
            resp = client.post("/api/fund/positions/add", json={
                "fund_code": "110022", "fund_name": "易方达消费行业",
                "shares": 1000.0, "cost_nav": 3.21, "buy_date": "2025-01-15", "note": "",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["changes"]["fund_code"] == "110022"
        assert data["changes"]["old"]["shares"] == 900.0
        assert data["changes"]["new"]["shares"] == 1000.0

    def test_add_new_returns_null_changes(self, client):
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_EXISTING)), \
             patch("web.routes.fund._save_positions", return_value=True):
            resp = client.post("/api/fund/positions/add", json={
                "fund_code": "161725", "fund_name": "招商白酒",
                "shares": 500.0, "cost_nav": 0.85, "buy_date": "", "note": "",
            })
        assert resp.status_code == 200
        assert resp.json()["changes"] is None


class TestGetPositionsConfidence:
    """GET /api/fund/positions(响应增强 nav_date/confidence/confidence_reason)"""

    def test_positions_include_confidence_fields(self, client):
        quotes = _make_quotes({
            "110022": "2026-10-26 15:00",  # 正常
            "002891": "2026-10-20 15:00",  # 滞后
        })
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_EXISTING)), \
             patch("web.routes.fund.get_fund_realtime_tencent", return_value=quotes), \
             patch("web.routes.fund._today", return_value=_dt.date(2026, 10, 26)):
            resp = client.get("/api/fund/positions")
        assert resp.status_code == 200
        by_code = {p["fund_code"]: p for p in resp.json()["positions"]}
        # 正常净值 → high
        assert by_code["110022"]["confidence"] == "high"
        assert by_code["110022"]["nav_date"] == "2026-10-26"
        # 滞后净值 → low + 人话原因
        assert by_code["002891"]["confidence"] == "low"
        assert "滞后" in by_code["002891"]["confidence_reason"]
        assert by_code["002891"]["nav_date"] == "2026-10-20"

    def test_missing_update_time_fails_closed(self, client):
        """update_time 缺失 → confidence=low(不撒谎)"""
        quotes = [{"code": "110022", "name": "x", "nav": 2.0, "change_pct": 0.0, "update_time": ""}]
        with patch("web.routes.fund._load_positions", return_value=[_MOCK_EXISTING[0]]), \
             patch("web.routes.fund.get_fund_realtime_tencent", return_value=quotes):
            resp = client.get("/api/fund/positions")
        pos = resp.json()["positions"][0]
        assert pos["confidence"] == "low"
        assert pos["nav_date"] == ""

    def test_seed_codes_all_low_when_nav_lagging(self, client):
        """验收校验:4 个种子代码(002891/018957/017811/018816)在净值滞后时全部 low"""
        seed_positions = [
            {"fund_code": c, "fund_name": f"基金{c}", "shares": 100.0,
             "cost_nav": 1.0, "buy_date": "", "note": ""}
            for c in _LAG_CODES
        ]
        quotes = _make_quotes({c: "2026-10-19 15:00" for c in _LAG_CODES})
        with patch("web.routes.fund._load_positions", return_value=seed_positions), \
             patch("web.routes.fund.get_fund_realtime_tencent", return_value=quotes), \
             patch("web.routes.fund._today", return_value=_dt.date(2026, 10, 26)):
            resp = client.get("/api/fund/positions")
        result = {p["fund_code"]: p["confidence"] for p in resp.json()["positions"]}
        assert all(result[c] == "low" for c in _LAG_CODES)


class TestSectorMap:
    """GET /api/config/sector-map"""

    def test_sector_map_returns_yaml_content(self, client):
        resp = client.get("/api/config/sector-map")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert isinstance(data["sectors"], dict)
        assert "CPO" in data["sectors"]
        assert data["unmapped_label"] == "其他"

    def test_sector_map_missing_file_returns_empty(self, client):
        with patch("web.routes.config._load_yaml", return_value={}):
            resp = client.get("/api/config/sector-map")
        assert resp.status_code == 200
        assert resp.json()["sectors"] == {}


class TestWizardApiAuth:
    """阶段5 铁律:所有新增端点必须挂 require_admin,匿名访问 401/403"""

    @pytest.mark.parametrize("method,path", [
        ("post", "/api/fund/positions/validate"),
        ("get", "/api/config/sector-map"),
    ])
    def test_new_endpoints_require_token(self, client, method, path):
        """生产环境已配置令牌但未携带 Authorization → 401"""
        with patch.dict(os.environ, {"APP_ENV": "production", "ADMIN_TOKEN": "unit-test-token"}, clear=False):
            resp = getattr(client, method)(path, **({"json": {"positions": []}} if method == "post" else {}))
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("post", "/api/fund/positions/validate"),
        ("get", "/api/config/sector-map"),
    ])
    def test_new_endpoints_reject_wrong_token(self, client, method, path):
        with patch.dict(os.environ, {"APP_ENV": "production", "ADMIN_TOKEN": "unit-test-token"}, clear=False):
            kwargs = {"json": {"positions": []}} if method == "post" else {}
            resp = getattr(client, method)(path, headers={"Authorization": "Bearer wrong-token"}, **kwargs)
        assert resp.status_code == 401
