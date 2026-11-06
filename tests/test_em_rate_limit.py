"""R-07 回归:东方财富限速锁只在预约时间片,不在网络 IO 期间持有。"""
from __future__ import annotations

from types import SimpleNamespace

from src.core import data_source_v2 as ds2


def _fake_response():
    return SimpleNamespace(status_code=200, json=lambda: {})


def test_rate_lock_released_before_network(monkeypatch):
    ds2._em_next_slot = 0.0
    observed = []

    def fake_get(*_args, **_kwargs):
        # 网络请求期间锁必须已释放,否则并发请求会被串行化
        acquired = ds2._em_rate_lock.acquire(blocking=False)
        observed.append(acquired)
        if acquired:
            ds2._em_rate_lock.release()
        return _fake_response()

    monkeypatch.setattr(ds2._EM_SESSION, "get", fake_get)
    monkeypatch.setattr(ds2, "_ensure_em_checked", lambda: True)
    monkeypatch.setattr(ds2.time, "sleep", lambda _seconds: None)

    ds2.em_get("https://push2.eastmoney.com/api/qt/stock/get")
    assert observed == [True]


def test_reserves_next_slot_and_sleeps_outside_lock(monkeypatch):
    ds2._em_next_slot = 0.0
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(float(seconds))

    monkeypatch.setattr(ds2._EM_SESSION, "get", lambda *_a, **_k: _fake_response())
    monkeypatch.setattr(ds2, "_ensure_em_checked", lambda: True)
    monkeypatch.setattr(ds2.time, "sleep", fake_sleep)

    ds2.em_get("https://push2.eastmoney.com/api/qt/stock/get")
    ds2.em_get("https://push2.eastmoney.com/api/qt/stock/get")

    assert ds2._em_next_slot > 0.0
    assert sleeps and sleeps[-1] >= ds2._EM_MIN_INTERVAL * 0.5
