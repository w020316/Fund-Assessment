"""同花顺涨停池解析回归（2026-11-14）。

上游把 filter 从日期式 ``GHS3A_YYYYMMDD`` 改成市场集合式 ``HS,GEM2STAR``，
旧参数返回 ``{"status_code": -1, "status_msg": "filter参数不合法"}``，涨停池
长期为空。这里锁住新参数与新响应结构（data.info / reason_type / unix 封板时间）。
"""
from __future__ import annotations

from src.core import data_source_v2 as ds2


def _reset_cache(monkeypatch):
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda *a, **k: None)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_parses_new_info_schema(monkeypatch):
    _reset_cache(monkeypatch)
    captured = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        captured.update(params or {})
        return _Resp({
            "status_code": 0,
            "data": {
                "page": {"total": 1},
                "info": [{
                    "code": "002042",
                    "name": "华孚时尚",
                    "latest": 3.62,
                    "change_rate": 10.0304,
                    "open_num": 4,
                    "first_limit_up_time": "1794534987",
                    "reason_type": "算力租赁+色纺纱",
                }],
            },
        })

    monkeypatch.setattr(ds2.requests, "get", fake_get)
    out = ds2.get_hot_stocks_ths()

    assert captured["filter"] == "HS,GEM2STAR"
    assert out[0]["code"] == "002042"
    assert out[0]["name"] == "华孚时尚"
    assert out[0]["price"] == 3.62
    assert out[0]["change_pct"] == 10.0304
    assert out[0]["reason"] == "算力租赁+色纺纱"
    assert out[0]["open_times"] == 4
    assert len(out[0]["limit_up_time"]) == 8  # HH:MM:SS


def test_rejects_upstream_error_payload(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(
        ds2.requests, "get",
        lambda *a, **k: _Resp({"status_code": -1, "status_msg": "filter参数不合法"}),
    )
    assert ds2.get_hot_stocks_ths() == []


def test_keeps_old_list_schema_compatible(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(
        ds2.requests, "get",
        lambda *a, **k: _Resp({
            "status_code": 0,
            "data": {"list": [{
                "code": "600519", "name": "贵州茅台", "latest_price": 1680.0,
                "change_pct": 9.9, "reason": "涨停", "first_limit_up_time": "09:35:00",
                "open_limit_up_times": 1,
            }]},
        }),
    )
    out = ds2.get_hot_stocks_ths()
    assert out[0]["limit_up_time"] == "09:35:00"
    assert out[0]["open_times"] == 1
