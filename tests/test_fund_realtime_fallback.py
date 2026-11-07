"""基金实时行情双上游(腾讯主源 + 天天基金 fundgz 备源)测试。

覆盖:
- 主源腾讯失败时, 缺失 code 由 fundgz 补齐(source 溯源字段)
- 主源部分成功时, 仅缺失 code 走备源
- 双上游全失败 → 返回 [] 且健康度记 failure
- fundgz 返回非法 JSON / dwjz<=0 → 该条被跳过, 永不抛异常
- 空入参 → 不发请求、不污染健康度
- fundgz gszzl 超过 ±20 → change_pct 钳为 0
"""
from __future__ import annotations

import json

import pytest

from src.core import data_health
from src.core import data_source_v2 as ds2


@pytest.fixture(autouse=True)
def _reset_health_state():
    data_health.reset()
    yield
    data_health.reset()


def _source(name: str) -> dict:
    for item in data_health.snapshot()["sources"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"source {name} not recorded")


class _Resp:
    """最小 mock 响应对象, 只模拟 .text / .encoding。"""

    def __init__(self, text: str = "", encoding: str = "utf-8"):
        self.text = text
        self.encoding = encoding


def _fundgz_text(code: str, name: str = "测试基金", dwjz: str = "1.2000",
                 gsz: str = "1.2200", gszzl: str = "1.6700",
                 gztime: str = "2026-11-10 15:00") -> str:
    """构造 jsonpgz({...}); 形式的 fundgz 响应文本。"""
    payload = {
        "fundcode": code,
        "name": name,
        "dwjz": dwjz,
        "gsz": gsz,
        "gszzl": gszzl,
        "jzrq": "2026-11-10",
        "gztime": gztime,
    }
    return f"jsonpgz({json.dumps(payload)});"


class TestFallbackMerge:
    def test_tencent_fail_fundgz_success(self, monkeypatch):
        """主源异常 → 备源 fundgz 补齐, 字段正确且带 source 溯源, 健康度 last_ok"""
        def _boom(*a, **k):
            raise RuntimeError("tencent down")

        monkeypatch.setattr(ds2.requests, "get", _boom)
        monkeypatch.setattr(
            ds2._EM_SESSION, "get", lambda *a, **k: _Resp(_fundgz_text("161725"))
        )
        result = ds2.get_fund_realtime_tencent(["161725"])

        assert len(result) == 1
        row = result[0]
        assert row["code"] == "161725"
        assert row["name"] == "测试基金"
        assert row["nav"] == 1.2
        assert row["estimated_nav"] == 1.22
        assert row["change_pct"] == 1.67
        assert row["change"] == pytest.approx(0.02, abs=1e-4)
        assert row["update_time"] == "2026-11-10 15:00"
        assert row["source"] == "fundgz"

        src = _source("fund_realtime")
        assert src["status"] == "ok"
        assert src["last_count"] == 1

    def test_tencent_partial_fundgz_fills_missing(self, monkeypatch):
        """主源 2 个 code 只回 1 个 → 缺的那个由 fundgz 补齐, 共 2 条"""
        text = ('v_jj161725="161725~招商中证白酒~1.05~x~x~1.20~2.50~1.50~'
                '2026-10-28 15:00:00~";')
        monkeypatch.setattr(ds2.requests, "get", lambda *a, **k: _Resp(text))
        monkeypatch.setattr(
            ds2._EM_SESSION, "get",
            lambda *a, **k: _Resp(_fundgz_text("110022", name="易方达消费")),
        )
        result = ds2.get_fund_realtime_tencent(["161725", "110022"])

        assert len(result) == 2
        by_code = {r["code"]: r for r in result}
        assert by_code["161725"]["source"] == "tencent"
        assert by_code["161725"]["nav"] == 1.2
        assert by_code["110022"]["source"] == "fundgz"
        assert by_code["110022"]["nav"] == 1.2
        # 主源条目也必须带 source 字段, 保证结构同构
        assert set(by_code["161725"].keys()) == set(by_code["110022"].keys())

    def test_both_upstream_fail(self, monkeypatch):
        """双上游全失败 → 返回 [] 且健康度记 failure"""
        def _boom(*a, **k):
            raise RuntimeError("down")

        monkeypatch.setattr(ds2.requests, "get", _boom)
        monkeypatch.setattr(ds2._EM_SESSION, "get", _boom)
        assert ds2.get_fund_realtime_tencent(["161725"]) == []

        src = _source("fund_realtime")
        assert src["status"] == "down"
        assert src["last_error"]  # 携带失败原因

    def test_health_success_when_tencent_fully_covers(self, monkeypatch):
        """主源覆盖全部 code 时不调备源, 健康度按合并口径记 success"""
        calls: list[str] = []

        def _em_get(*a, **k):
            calls.append("fundgz")
            raise RuntimeError("fundgz should not be called")

        text = ('v_jj161725="161725~招商中证白酒~1.05~x~x~1.20~2.50~1.50~'
                '2026-10-28 15:00:00~";')
        monkeypatch.setattr(ds2.requests, "get", lambda *a, **k: _Resp(text))
        monkeypatch.setattr(ds2._EM_SESSION, "get", _em_get)

        result = ds2.get_fund_realtime_tencent(["161725"])
        assert len(result) == 1
        assert result[0]["source"] == "tencent"
        assert calls == []
        assert _source("fund_realtime")["status"] == "ok"


class TestFundgzRobustness:
    def test_fundgz_invalid_json_skipped(self, monkeypatch):
        """fundgz 返回非法 JSON → 该条被跳过, 不抛异常"""
        monkeypatch.setattr(
            ds2._EM_SESSION, "get",
            lambda *a, **k: _Resp("jsonpgz({not-valid-json});"),
        )
        assert ds2._fetch_fund_realtime_fundgz(["110022"]) == []

    def test_fundgz_zero_nav_skipped(self, monkeypatch):
        """fundgz dwjz<=0 → R-06, 该条被丢弃, 不返回 nav=0 假行情"""
        monkeypatch.setattr(
            ds2._EM_SESSION, "get",
            lambda *a, **k: _Resp(_fundgz_text("110022", dwjz="0.0000")),
        )
        assert ds2._fetch_fund_realtime_fundgz(["110022"]) == []

    def test_fundgz_network_error_never_raises(self, monkeypatch):
        """fundgz 单个 code 网络失败只 warning, 整体永不 raise"""
        def _boom(*a, **k):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(ds2._EM_SESSION, "get", _boom)
        assert ds2._fetch_fund_realtime_fundgz(["110022", "161725"]) == []

    def test_fundgz_empty_codes_no_request(self, monkeypatch):
        """fundgz 空入参直接返回 [], 不发请求"""
        def _boom(*a, **k):
            raise RuntimeError("should not be called")

        monkeypatch.setattr(ds2._EM_SESSION, "get", _boom)
        assert ds2._fetch_fund_realtime_fundgz([]) == []

    def test_fundgz_change_pct_clamped_beyond_20(self, monkeypatch):
        """gszzl 超过 ±20 → change_pct 钳为 0, change 随之为 0"""
        monkeypatch.setattr(
            ds2._EM_SESSION, "get",
            lambda *a, **k: _Resp(_fundgz_text("110022", gszzl="25.5")),
        )
        result = ds2._fetch_fund_realtime_fundgz(["110022"])
        assert len(result) == 1
        assert result[0]["change_pct"] == 0.0
        assert result[0]["change"] == 0.0


class TestEmptyCodes:
    def test_empty_codes_no_request_no_health_pollution(self, monkeypatch):
        """空入参 → 直接 [], 两个上游都不被请求, 健康度不被污染"""
        calls: list[str] = []

        def _track(name: str):
            def _inner(*a, **k):
                calls.append(name)
                raise RuntimeError(f"{name} should not be called")
            return _inner

        monkeypatch.setattr(ds2.requests, "get", _track("tencent"))
        monkeypatch.setattr(ds2._EM_SESSION, "get", _track("fundgz"))

        assert ds2.get_fund_realtime_tencent([]) == []
        assert calls == []

        snap = data_health.snapshot()
        fund_rt = next(s for s in snap["sources"] if s["name"] == "fund_realtime")
        assert fund_rt["status"] == "unknown"
        assert fund_rt["total_success"] == 0
        assert fund_rt["total_failure"] == 0
