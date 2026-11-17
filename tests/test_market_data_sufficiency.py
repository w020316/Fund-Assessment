"""A-08(2026-11-17):四维全缺时不得输出「中性 50 分 / 观望」。

根因: `_calc_*_score` 对缺失一律回 50 并只写 note, `_calc_thermometer` 固定权重
加总 → 四维全缺恰好 50.0 → `level=中性` / `action=观望,等待方向`;
路由侧 `_build_meta("aggregator", cached=False)` 恒 80 分(degraded=False)。
**用缺失数据生成投资结论**, 比空态更有害。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.analysis.market_assessment import (
    _calc_capital_flow_score,
    _calc_thermometer,
    get_market_assessment,
    get_market_thermometer,
)
from web.routes import market

client = TestClient(__import__("web.api", fromlist=["app"]).app)


@pytest.fixture(autouse=True)
def _clear_cache():
    market.cache.clear()
    yield
    market.cache.clear()


def _mock_sources(monkeypatch, *, index, sectors, northbound, sentiment):
    monkeypatch.setattr("src.analysis.market_assessment.ds2.get_index_realtime", lambda: index)
    # P1-2: 温度计评分改用全量板块取数, patch 目标随之更新(断言未改)
    monkeypatch.setattr("src.analysis.market_assessment.ds2.get_sector_ranking_full", lambda: sectors)
    monkeypatch.setattr("src.analysis.market_assessment.ds2.get_sector_ranking", lambda: sectors)
    monkeypatch.setattr(
        "src.analysis.market_assessment.ds2.get_northbound_flow_realtime", lambda: northbound
    )
    monkeypatch.setattr("src.analysis.market_assessment.ds2.get_market_sentiment", lambda: sentiment)


class TestAllDimsMissingGivesNoConclusion:
    @pytest.mark.asyncio
    async def test_all_missing_marks_data_insufficient(self, monkeypatch):
        _mock_sources(monkeypatch, index=[], sectors=[], northbound={}, sentiment={})

        data = await get_market_thermometer()

        assert data["data_quality"]["available_dims"] == 0
        assert data["data_quality"]["total_dims"] == 4
        assert data["score_available"] is False
        # 不再是"中性/观望"这种投资结论
        assert data["level"] == "数据不足"
        assert "不评分" in data["action"]
        assert data["level"] != "中性"
        assert "观望" not in data["action"]
        # score 仍保留加权占位值(兼容既有契约), 但 score_available 明确否定其结论性
        assert data["score"] == 50.0

    @pytest.mark.asyncio
    async def test_assessment_text_does_not_invent_a_view(self, monkeypatch):
        """综合研判的文案同样不能从占位 50 分推出"震荡/观望"。"""
        _mock_sources(monkeypatch, index=[], sectors=[], northbound={}, sentiment={})

        data = await get_market_assessment()

        assert "数据全部缺失" in data["assessment"]
        assert "观望为主" not in data["assessment"]
        assert data["sector_signal"] == "板块数据不可用,无法判断轮动方向"


class TestPartialDims:
    @pytest.mark.asyncio
    async def test_one_or_two_dims_is_degraded_at_route(self, monkeypatch):
        # 只有指数可用 → available_dims == 1
        _mock_sources(
            monkeypatch,
            index=[{"name": "上证指数", "change_pct": 1.0}],
            sectors=[],
            northbound={},
            sentiment={},
        )

        payload = client.get("/api/market/thermometer").json()
        data, meta = payload["data"], payload["_meta"]

        assert data["data_quality"]["available_dims"] == 1
        assert meta["quality_score"] == 40.0
        assert meta["degraded"] is True
        assert "1/4" in meta["degraded_reason"]

    @pytest.mark.asyncio
    async def test_all_missing_route_quality_is_zero(self, monkeypatch):
        _mock_sources(monkeypatch, index=[], sectors=[], northbound={}, sentiment={})

        payload = client.get("/api/market/thermometer").json()
        meta = payload["_meta"]

        assert meta["quality_score"] == 0.0
        assert meta["degraded"] is True
        assert "全部缺失" in meta["degraded_reason"]

    @pytest.mark.asyncio
    async def test_four_dims_available_keeps_numbers(self, monkeypatch):
        """回归保护: 四维齐时数值与改动前一致, 且不降级。"""
        _mock_sources(
            monkeypatch,
            index=[{"name": "上证指数", "change_pct": 1.0}],
            sectors=[{"change_pct": 1.0, "main_net_inflow": 1_0000_0000}],
            northbound={"total_net_inflow": 1_0000_0000},
            sentiment={"rise_count": 3000, "fall_count": 2000, "sentiment": "偏多"},
        )

        payload = client.get("/api/market/thermometer").json()
        data, meta = payload["data"], payload["_meta"]

        assert data["data_quality"]["available_dims"] == 4
        assert data["score_available"] is True
        assert meta["degraded"] is False
        # 指数 60×0.30 + 板块 100×0.25 + 资金 50.5×0.25 + 情绪 60×0.20
        # = 18 + 25 + 12.625 + 12 = 67.625(四维齐, 与改动前算法一致)
        assert data["score"] == pytest.approx(67.6, abs=0.1)
        assert data["level"] == "偏热"


class TestCapitalDimensionNotPulledToFifty:
    def test_northbound_missing_equals_sector_score(self):
        sectors = [{"main_net_inflow": 20 * 1_0000_0000}]  # sector_score = 60
        result = _calc_capital_flow_score({}, sectors)

        assert result["score"] == result["sector_score"] == 60.0
        assert result["northbound_available"] is False
        assert result["missing"] == ["northbound"]
        assert result["complete"] is False

    def test_both_missing_is_placeholder_and_unavailable(self):
        result = _calc_capital_flow_score({}, [])

        assert result["score"] == 50.0
        assert result["available"] is False
        assert result["complete"] is False
        assert result["northbound_net"] is None


class TestThermometerComponentFlags:
    def test_components_expose_available_flag(self):
        out = _calc_thermometer(
            {"score": 50, "weight": 0.30, "available": False},
            {"score": 50, "weight": 0.25, "available": False},
            {"score": 50, "weight": 0.25, "available": False},
            {"score": 50, "weight": 0.20, "available": False},
        )

        assert out["data_quality"] == {
            "available_dims": 0,
            "total_dims": 4,
            "missing_dims": ["capital_flow", "index_trend", "sector_rotation", "sentiment"],
        }
        assert out["score_available"] is False

    def test_three_of_four_dims_keeps_score_and_flags_missing(self):
        out = _calc_thermometer(
            {"score": 80, "weight": 0.30, "available": True},
            {"score": 80, "weight": 0.25, "available": True},
            {"score": 80, "weight": 0.25, "available": True},
            {"score": 50, "weight": 0.20, "available": False},
        )

        assert out["data_quality"]["available_dims"] == 3
        assert out["data_quality"]["missing_dims"] == ["sentiment"]
        assert out["score_available"] is True
        assert out["level"] in {"偏热", "极热", "中性", "偏冷", "极冷"}
