"""A-09(2026-11-17):股票代码必须校验后才能拼进上游 filter。

根因: `/margin`、`/block_trades`、`/shareholder` 把 `code: Query("")` 原样拼进
东财过滤表达式(`f'(SECURITY_CODE="{code}")'` / `f'(SCODE="{code}")'`),
`600519")+OR+(1=1` 之类可以改写查询条件;异常又被 `except Exception` 吞成
"无数据", 接口就成了任意东财报表探测器。同文件 `/stock_flow_streak` 早有
`pattern=r"^\\d{6}$"`, 这三处没对齐。

两层都要测: 路由层 422 且**不调用上游**; 数据源层连请求都不发。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core import data_source_v2 as ds2
from web.api import app
from web.routes import market

client = TestClient(app)

ILLEGAL_CODES = ["60051", "6005199", '600519")+OR+(1=1', "abc123", "600519 ", "600519;drop"]


@pytest.fixture(autouse=True)
def _clear_cache():
    market.cache.clear()
    yield
    market.cache.clear()


class TestRouteRejectsIllegalCode:
    @pytest.mark.parametrize("path", ["/api/market/margin", "/api/market/block_trades",
                                      "/api/market/shareholder"])
    @pytest.mark.parametrize("bad", ILLEGAL_CODES)
    def test_illegal_code_is_422_and_upstream_not_called(self, path, bad, monkeypatch):
        calls: list = []

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return {}

        monkeypatch.setattr(market.ds2, "get_margin_trading", spy)
        monkeypatch.setattr(market.ds2, "get_block_trades", spy)
        monkeypatch.setattr(market.ds2, "get_shareholder_count", spy)

        resp = client.get(path, params={"code": bad})

        assert resp.status_code == 422, f"{path} 未拦截非法代码 {bad!r}"
        detail = resp.json()["detail"]
        assert detail["code"] == "INVALID_STOCK_CODE"
        assert detail["field"] == "code"
        assert calls == [], "非法代码不得触发任何上游取数"

    @pytest.mark.parametrize("path", ["/api/market/margin", "/api/market/block_trades",
                                      "/api/market/shareholder"])
    def test_empty_code_keeps_optional_semantics(self, path, monkeypatch):
        """空串必须放行(前端有 `code ? code : '600519'` 兜底, 不能 422)。

        原行为是"空 code 直接返回空集合 + 降级, 不打上游", 这里保持不变;
        关键是**不能**因为加了校验就把空串也判 422。
        """
        called: list = []
        monkeypatch.setattr(market.ds2, "get_margin_trading", lambda c: called.append(c) or {})
        monkeypatch.setattr(market.ds2, "get_block_trades", lambda c: called.append(c) or [])
        monkeypatch.setattr(market.ds2, "get_shareholder_count", lambda c: called.append(c) or {})

        resp = client.get(path)

        assert resp.status_code == 200, "空 code 不得被判 422"
        assert resp.json()["data"] == []
        assert called == [], "空 code 仍走原来的短路分支, 不打上游"

    @pytest.mark.parametrize("path", ["/api/market/margin", "/api/market/block_trades",
                                      "/api/market/shareholder"])
    def test_legal_code_passes_through(self, path, monkeypatch):
        seen: list[str] = []

        def fake(code):
            seen.append(code)
            return {} if "margin" in path or "shareholder" in path else []

        monkeypatch.setattr(market.ds2, "get_margin_trading", fake)
        monkeypatch.setattr(market.ds2, "get_block_trades", fake)
        monkeypatch.setattr(market.ds2, "get_shareholder_count", fake)

        resp = client.get(path, params={"code": "600519"})

        assert resp.status_code == 200
        assert seen == ["600519"]


class TestDataSourceDefenceInDepth:
    """即使绕过路由直接调用, 数据源层也不得把非法代码发到上游。"""

    @pytest.mark.parametrize("bad", ILLEGAL_CODES)
    def test_margin_returns_empty_without_request(self, bad, monkeypatch):
        called: list = []
        monkeypatch.setattr(ds2, "em_get", lambda *a, **k: called.append(1))

        assert ds2.get_margin_trading(bad) == {}
        assert called == []

    @pytest.mark.parametrize("bad", ILLEGAL_CODES)
    def test_block_trades_returns_empty_without_request(self, bad, monkeypatch):
        called: list = []
        monkeypatch.setattr(ds2, "em_get", lambda *a, **k: called.append(1))

        assert ds2.get_block_trades(bad) == []
        assert called == []

    @pytest.mark.parametrize("bad", ILLEGAL_CODES)
    def test_shareholder_returns_empty_without_request(self, bad, monkeypatch):
        called: list = []
        monkeypatch.setattr(ds2, "em_get", lambda *a, **k: called.append(1))

        assert ds2.get_shareholder_count(bad) == {}
        assert called == []

    def test_legal_code_still_queries(self, monkeypatch):
        """合法代码仍要正常发起请求(不能因为加了校验就变成永远空)。"""
        called: list = []

        class _Resp:
            def json(self):
                return {"result": {"data": []}}

        monkeypatch.setattr(ds2, "em_get", lambda *a, **k: called.append(1) or _Resp())

        ds2.get_margin_trading("600519")
        assert called == [1]
