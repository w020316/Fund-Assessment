"""数据源健康度(阶段七,T-A1)测试。

覆盖:
- 状态机:unknown → ok / degraded / down、连续失败阈值、成功清零
- 上报入口:data_source_v2 的成功/空结果/异常三条路径真实落状态
- 路由:GET /api/data/health 契约
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core import data_health
from src.core import data_source_v2 as ds2


@pytest.fixture(autouse=True)
def _reset_health_state():
    data_health.reset()
    ds2._cache.clear()
    yield
    data_health.reset()
    ds2._cache.clear()


def _source(name: str) -> dict:
    for item in data_health.snapshot()["sources"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"source {name} not recorded")


class TestStateMachine:
    def test_empty_state_is_unknown(self):
        snap = data_health.snapshot()
        assert snap["overall"] == "unknown"
        assert snap["sources"] == []
        assert snap["generated_at"]

    def test_success_marks_ok(self):
        data_health.record_success("index_realtime", 3)
        src = _source("index_realtime")
        assert src["status"] == "ok"
        assert src["label"] == "指数实时行情"
        assert src["last_count"] == 3
        assert src["last_success_at"]
        assert src["last_success_age_s"] >= 0
        assert src["total_success"] == 1
        assert src["consecutive_failures"] == 0

    def test_first_failure_without_history_is_down(self):
        data_health.record_failure("fund_history", "500")
        src = _source("fund_history")
        assert src["status"] == "down"
        assert src["last_error"] == "500"
        assert src["last_success_at"] is None
        assert src["last_success_age_s"] is None

    def test_degraded_then_down_after_threshold(self):
        data_health.record_success("sector_ranking", 50)
        data_health.record_failure("sector_ranking", "timeout")
        src = _source("sector_ranking")
        assert src["status"] == "degraded"
        # 最近成功时间在失败后仍保留,前端可显示"多久之前还是好的"
        assert src["last_success_at"]

        data_health.record_failure("sector_ranking", "timeout")
        assert _source("sector_ranking")["status"] == "degraded"
        data_health.record_failure("sector_ranking", "timeout")
        src = _source("sector_ranking")
        assert src["status"] == "down"
        assert src["consecutive_failures"] == data_health.DOWN_THRESHOLD
        assert src["total_failure"] == 3
        assert src["total_success"] == 1

    def test_success_resets_consecutive_failures(self):
        data_health.record_failure("global_news", "a")
        data_health.record_failure("global_news", "b")
        data_health.record_success("global_news", 20)
        src = _source("global_news")
        assert src["status"] == "ok"
        assert src["consecutive_failures"] == 0
        assert src["last_error"] == ""
        assert src["total_failure"] == 2

    def test_overall_is_worst_status(self):
        data_health.record_success("index_realtime", 3)
        data_health.record_success("fund_realtime", 5)
        assert data_health.snapshot()["overall"] == "ok"

        data_health.record_failure("fund_realtime", "x")
        assert data_health.snapshot()["overall"] == "degraded"

        for _ in range(data_health.DOWN_THRESHOLD):
            data_health.record_failure("global_news", "x")
        assert data_health.snapshot()["overall"] == "down"

    def test_error_is_truncated(self):
        data_health.record_failure("index_realtime", "x" * 500)
        assert len(_source("index_realtime")["last_error"]) == 160

    def test_unregistered_source_uses_name_as_label(self):
        data_health.record_success("custom_source", 1)
        assert _source("custom_source")["label"] == "custom_source"

    def test_zero_count_success_keeps_zero(self):
        data_health.record_success("index_realtime", 0)
        assert _source("index_realtime")["last_count"] == 0


class TestInstrumentation:
    def test_index_realtime_success_recorded(self, monkeypatch):
        parts = [f"f{i}" for i in range(1, 45)]
        parts[0] = 'v_sh000001="1'
        parts[1] = "上证指数"
        parts[2] = "000001"
        parts[3] = "3200.0"
        parts[31] = "10.0"
        parts[32] = "0.31"
        parts[36] = "1000"
        parts[37] = "2000"
        text = "~".join(parts) + ";"

        class _Resp:
            encoding = "gbk"
            text = ""

        resp = _Resp()
        resp.text = text
        monkeypatch.setattr(ds2.requests, "get", lambda *a, **k: resp)

        result = ds2.get_index_realtime()
        assert result and result[0]["code"] == "000001"
        src = _source("index_realtime")
        assert src["status"] == "ok"
        assert src["last_count"] == 1

    def test_index_realtime_empty_parse_is_failure(self, monkeypatch):
        class _Resp:
            encoding = "gbk"
            text = "garbage-without-quotes;"

        monkeypatch.setattr(ds2.requests, "get", lambda *a, **k: _Resp())
        assert ds2.get_index_realtime() == []
        src = _source("index_realtime")
        assert src["status"] == "down"
        assert src["last_error"] == "empty result"

    def test_index_realtime_exception_recorded(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(ds2.requests, "get", _boom)
        assert ds2.get_index_realtime() == []
        src = _source("index_realtime")
        assert src["status"] == "down"
        assert "connection refused" in src["last_error"]

    def test_fund_realtime_success_recorded(self, monkeypatch):
        class _Resp:
            encoding = "gbk"
            text = 'v_jj161725="161725~招商中证白酒~1.05~x~x~1.20~2.50~1.50~2026-10-28 15:00:00~";'

        monkeypatch.setattr(ds2.requests, "get", lambda *a, **k: _Resp())
        result = ds2.get_fund_realtime_tencent(["161725"])
        assert result and result[0]["nav"] == 1.2
        src = _source("fund_realtime")
        assert src["status"] == "ok"
        assert src["last_count"] == 1

    def test_fund_realtime_empty_codes_not_counted(self):
        # 空入参不发请求,不应污染健康度
        assert ds2.get_fund_realtime_tencent([]) == []
        assert data_health.snapshot()["sources"] == []


class TestRoute:
    def test_health_endpoint_contract(self):
        data_health.record_success("index_realtime", 3)
        data_health.record_failure("fund_history", "all sources failed")

        from web.api import app
        resp = TestClient(app).get("/api/data/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["overall"] == "down"
        names = [s["name"] for s in body["sources"]]
        assert "index_realtime" in names
        assert "fund_history" in names
        for item in body["sources"]:
            assert {"name", "label", "status", "last_error",
                    "consecutive_failures", "last_count"} <= set(item)
