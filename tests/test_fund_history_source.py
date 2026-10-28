"""R2-01 回归:基金历史净值分页、排序、窗口过滤与降级。

真实上游行为(2026-10-28 实测):东财 f10/lsjz 忽略 pageSize(固定 20 条/页)、
TotalCount 在响应顶层、按日期倒序;pingzhongdata 单请求返回全量历史。
本文件全部 mock,禁止真实网络请求。
"""
from __future__ import annotations

import datetime as dt
import json
from datetime import timedelta, timezone

import pytest

from src.core import data_source_v2 as ds2

_CST = timezone(timedelta(hours=8))


class _Resp:
    def __init__(self, text: str):
        self.text = text


def _lsjz_payload(items: list[dict], total: int, page_index: int = 1) -> str:
    return "jQuery(" + json.dumps({
        "Data": {"LSJZList": items},
        "TotalCount": total,
        "PageSize": 20,
        "PageIndex": page_index,
    }) + ")"


def _item(date: str, nav: float, acc: float | None = None, pct: str = "0.00") -> dict:
    return {
        "FSRQ": date,
        "DWJZ": f"{nav:.4f}",
        "LJJZ": f"{(acc if acc is not None else nav):.4f}",
        "JZZZL": pct,
    }


def _ts(date: dt.date) -> int:
    return int(dt.datetime(date.year, date.month, date.day, tzinfo=_CST).timestamp() * 1000)


def _pingzhong_js(points: list[tuple[dt.date, float, float | None, float | None]]) -> str:
    """points: [(date, nav, acc, equity_return)] → 模拟 pingzhongdata JS。"""
    nav_arr = [
        {"x": _ts(d), "y": nav, "equityReturn": pct if pct is not None else None, "unitMoney": ""}
        for d, nav, _acc, pct in points
    ]
    acc_arr = [[_ts(d), acc if acc is not None else nav] for d, nav, acc, _pct in points]
    return (
        "var Data_netWorthTrend = " + json.dumps(nav_arr) + ";"
        "var Data_ACWorthTrend = " + json.dumps(acc_arr) + ";"
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ds2.time, "sleep", lambda *_: None)


class TestLsjzPagination:
    def test_paginates_by_total_count_and_sorts_ascending(self, monkeypatch):
        """45 条数据分 3 页返回(倒序)→ 全部取回并升序排列。"""
        today = dt.date(2026, 10, 27)
        dates = [(today - dt.timedelta(days=i)).isoformat() for i in range(45)]
        items = [_item(d, 1.0 + i / 1000.0) for i, d in enumerate(dates)]
        pages = {1: items[0:20], 2: items[20:40], 3: items[40:45]}
        calls: list[int] = []

        def fake_get(url, params=None, **kwargs):
            if url.endswith(".js"):
                return _Resp("")  # pingzhong 无数据 → 走 lsjz
            calls.append(params["pageIndex"])
            return _Resp(_lsjz_payload(pages[params["pageIndex"]], total=45, page_index=params["pageIndex"]))

        monkeypatch.setattr(ds2._EM_SESSION, "get", fake_get)
        rows = ds2.get_fund_history_tencent("110022", "all")

        assert [r["date"] for r in rows] == sorted(dates)
        assert calls == [1, 2, 3]
        assert all(r["nav"] > 0 for r in rows)

    def test_page_cap_protects_against_endless_pagination(self, monkeypatch):
        """TotalCount 异常巨大 → 最多请求 _LSJZ_MAX_PAGES 页,不无限循环。"""
        item = _item("2026-10-27", 1.5)
        calls: list[int] = []

        def fake_get(url, params=None, **kwargs):
            if url.endswith(".js"):
                return _Resp("")
            calls.append(params["pageIndex"])
            return _Resp(_lsjz_payload([item] * 20, total=10_000, page_index=params["pageIndex"]))

        monkeypatch.setattr(ds2._EM_SESSION, "get", fake_get)
        ds2.get_fund_history_tencent("110022", "all")
        assert len(calls) == ds2._LSJZ_MAX_PAGES


class TestPingzhongPrimary:
    def test_full_history_primary_uses_single_request(self, monkeypatch):
        """pingzhong 主源成功 → 不再请求 lsjz,累计净值按日期对齐。"""
        d1, d2 = dt.date(2026, 10, 26), dt.date(2026, 10, 27)
        js = _pingzhong_js([(d2, 2.79, 2.79, -0.64), (d1, 2.808, 2.83, 0.5)])
        calls: list[str] = []

        def fake_get(url, params=None, **kwargs):
            calls.append(url)
            return _Resp(js if url.endswith(".js") else "")

        monkeypatch.setattr(ds2._EM_SESSION, "get", fake_get)
        rows = ds2.get_fund_history_tencent("110022", "1y")

        assert [r["date"] for r in rows] == [d1.isoformat(), d2.isoformat()]
        assert rows[0]["acc_nav"] == pytest.approx(2.83)
        assert rows[1]["change_pct"] == pytest.approx(-0.64)
        assert all(url.endswith(".js") for url in calls)

    def test_window_filter_applied_locally(self, monkeypatch):
        """1m 窗口只保留最近 30 天,不依赖上游 startDate。"""
        today = dt.date.today()
        old = today - dt.timedelta(days=60)
        recent = today - dt.timedelta(days=2)
        js = _pingzhong_js([(old, 1.0, 1.0, 0.0), (recent, 1.1, 1.1, 1.0)])

        def fake_get(url, params=None, **kwargs):
            return _Resp(js if url.endswith(".js") else "")

        monkeypatch.setattr(ds2._EM_SESSION, "get", fake_get)
        rows = ds2.get_fund_history_tencent("110022", "1m")
        assert [r["date"] for r in rows] == [recent.isoformat()]

    def test_pingzhong_failure_falls_back_to_lsjz(self, monkeypatch):
        """主源异常 → 备源 lsjz 仍能返回升序数据(不抛异常)。"""
        def boom(code):
            raise RuntimeError("pingzhong down")

        monkeypatch.setattr(ds2, "_fetch_fund_history_pingzhong", boom)

        def fake_get(url, params=None, **kwargs):
            return _Resp(_lsjz_payload([_item("2026-10-27", 1.2), _item("2026-10-26", 1.1)], total=2))

        monkeypatch.setattr(ds2._EM_SESSION, "get", fake_get)
        rows = ds2.get_fund_history_tencent("110022", "1y")
        assert [r["date"] for r in rows] == ["2026-10-26", "2026-10-27"]


class TestNormalizeHistory:
    def test_backfill_pct_uses_previous_day_direction(self):
        """缺失涨跌幅按「前一日→当日」回填,升序下应为 +10% 而非 −9.09%。"""
        rows = ds2._normalize_history([
            {"date": "2026-10-26", "nav": 1.0, "change_pct": None},
            {"date": "2026-10-27", "nav": 1.1, "change_pct": None},
        ])
        assert rows[0]["change_pct"] == 0.0
        assert rows[1]["change_pct"] == pytest.approx(10.0)

    def test_dedup_keeps_latest_and_filters_invalid(self):
        rows = ds2._normalize_history([
            {"date": "2026-10-27", "nav": 1.0},
            {"date": "2026-10-27", "nav": 1.2},
            {"date": "2026-10-26", "nav": 0},       # 非法净值剔除
            {"date": "", "nav": 1.1},               # 空日期剔除
            {"date": "2026-10-20", "nav": 1.1},     # 窗口外剔除
        ], start_date="2026-10-25")
        assert [r["date"] for r in rows] == ["2026-10-27"]
        assert rows[0]["nav"] == pytest.approx(1.2)
