"""tests/test_prediction_signals.py — T02 信号引擎(纯函数)验收。

覆盖: 三信号合成方向/置信度、冲突口径、R4.1 红线(标的可追溯、无映射丢弃)、
三分类判命中、配置 fail-safe。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core import prediction_signals as sig  # noqa: E402


# ===== 配置加载 =====


def test_load_config_missing_file_falls_back(tmp_path: Path) -> None:
    cfg = sig.load_signal_config(tmp_path / "no_such.yaml")
    assert cfg["confidence"]["base"] == 50
    assert cfg["verify"]["flat_band_pct"] == pytest.approx(0.3)


def test_load_config_corrupt_file_falls_back(tmp_path: Path) -> None:
    p = tmp_path / "broken.yaml"
    p.write_text("{ not: valid: yaml: [", encoding="utf-8")
    cfg = sig.load_signal_config(p)
    assert cfg["breadth"]["ratio_bull"] == pytest.approx(0.60)


def test_load_config_override_applied(tmp_path: Path) -> None:
    p = tmp_path / "ok.yaml"
    p.write_text(
        "breadth:\n  ratio_bull: 0.7\nrecommend:\n  max_items: 3\n",
        encoding="utf-8",
    )
    cfg = sig.load_signal_config(p)
    assert cfg["breadth"]["ratio_bull"] == pytest.approx(0.7)
    assert cfg["recommend"]["max_items"] == 3
    assert cfg["confidence"]["weights"]["breadth"] == 25  # 未覆盖键保留默认


# ===== 三信号合成 =====


def _bull_sentiment() -> dict:
    return {"rise_count": 3200, "fall_count": 1500, "flat_count": 300,
            "sentiment": "偏多", "ratio": 3200 / 5000}


def _bear_sentiment() -> dict:
    return {"rise_count": 1200, "fall_count": 3600, "flat_count": 200,
            "sentiment": "偏空", "ratio": 1200 / 5000}


def test_synthesize_bullish() -> None:
    out = sig.synthesize(
        _bull_sentiment(),
        [{"name": "沪深300", "change_pct": 0.8}],
        [{"name": f"板块{i}", "change_pct": 2.0 - i * 0.1} for i in range(8)],
        sig.DEFAULT_CONFIG,
    )
    assert out["direction"] == "up"
    assert out["confidence"] > 50
    assert out["rule_direction"] == out["direction"]  # 规则结论=最终结论
    signals = {r["signal"] for r in out["reasons"]}
    assert {"breadth_ratio", "sentiment_score", "sector_momentum"} <= signals
    assert all(r["verdict"] != "missing" for r in out["reasons"])


def test_synthesize_bearish() -> None:
    out = sig.synthesize(
        _bear_sentiment(),
        [{"name": "沪深300", "change_pct": -1.2}],
        [{"name": f"板块{i}", "change_pct": -2.0 + i * 0.1} for i in range(8)],
        sig.DEFAULT_CONFIG,
    )
    assert out["direction"] == "down"
    assert out["confidence"] > 50


def test_synthesize_flat_when_signals_mixed() -> None:
    # 广度偏多(+25) 但板块动量偏空(-10), 情绪中性 → 合成 +15 > 8? 构造刚好平衡
    mixed = {"rise_count": 2600, "fall_count": 2400, "flat_count": 0,
             "sentiment": "中性", "ratio": 0.52}
    out = sig.synthesize(
        mixed,
        [],
        [{"name": "A", "change_pct": 2.0}, {"name": "B", "change_pct": -2.0}],
        sig.DEFAULT_CONFIG,
    )
    assert out["composite"] == 0  # breadth neutral + sentiment neutral + sector 0
    assert out["direction"] == "flat"
    assert out["confidence"] == pytest.approx(50.0)


def test_synthesize_all_missing_is_flat_low_confidence() -> None:
    out = sig.synthesize(None, None, None, sig.DEFAULT_CONFIG)
    assert out["direction"] == "flat"
    assert all(r["verdict"] == "missing" for r in out["reasons"])


def test_synthesize_confidence_clamped() -> None:
    out = sig.synthesize(_bull_sentiment(), [], None, sig.DEFAULT_CONFIG)
    assert 5 <= out["confidence"] <= 95


# ===== 推荐标的选取(R4.1) =====

_CANDIDATES = {
    "半导体": [{"symbol": "512480", "kind": "stock"}],
    "酿酒": [{"symbol": "512690", "kind": "stock"}],
}


def test_select_recommendations_traceable() -> None:
    sectors = [
        {"name": "半导体", "change_pct": 2.4},
        {"name": "酿酒行业", "change_pct": -1.5},
        {"name": "无映射板块", "change_pct": 3.0},
    ]
    out = sig.select_recommendations(sectors, _CANDIDATES, sig.DEFAULT_CONFIG)
    symbols = {r["symbol"] for r in out}
    assert symbols == {"512480", "512690"}  # 无映射板块不产生推荐
    for r in out:
        # 每条 source_signal 指向具体板块与数值(可追溯红线)
        assert r["source_signal"].startswith("sector_momentum:")
        assert "change_pct=" in r["source_signal"]
        assert r["direction"] == ("up" if r["symbol"] == "512480" else "down")
        assert r["name"] == ""  # 名称由 service 层用行情回填


def test_select_recommendations_below_threshold_empty() -> None:
    sectors = [{"name": "半导体", "change_pct": 0.5}]  # 低于 1.0 动量阈值
    assert sig.select_recommendations(sectors, _CANDIDATES, sig.DEFAULT_CONFIG) == []


def test_select_recommendations_dedup_and_cap() -> None:
    cfg = dict(sig.DEFAULT_CONFIG)
    cfg["recommend"] = {"max_items": 1, "min_confidence": 55}
    sectors = [{"name": "半导体", "change_pct": 2.0}, {"name": "酿酒", "change_pct": 1.8}]
    out = sig.select_recommendations(sectors, _CANDIDATES, cfg)
    assert len(out) == 1  # max_items 截断


def test_select_recommendations_rejects_bad_symbols() -> None:
    bad = {
        "半导体": [
            {"symbol": "abc", "kind": "stock"},
            {"symbol": "512480", "kind": "etf"},  # kind 非法
            {"symbol": "", "kind": "stock"},
        ],
    }
    assert sig.select_recommendations(
        [{"name": "半导体", "change_pct": 2.0}], bad, sig.DEFAULT_CONFIG
    ) == []


# ===== 三分类判命中 =====


def test_classify_hit_band() -> None:
    assert sig.classify_hit("up", 0.5) == 1
    assert sig.classify_hit("up", 0.2) == 0
    assert sig.classify_hit("down", -0.4) == 1
    assert sig.classify_hit("down", -0.2) == 0
    assert sig.classify_hit("flat", 0.3) == 1
    assert sig.classify_hit("flat", 0.31) == 0
    assert sig.classify_hit("up", None) is None  # 验证缺失


def test_direction_from_return() -> None:
    assert sig.direction_from_return(0.5) == "up"
    assert sig.direction_from_return(-0.5) == "down"
    assert sig.direction_from_return(0.1) == "flat"
    assert sig.direction_from_return(None) == ""


# ===== 规则摘要 =====


def test_build_rule_summary() -> None:
    payload = {
        "indices": [{"name": "沪深300", "change_pct": 0.42}],
        "sentiment": {"rise_count": 3120, "fall_count": 1780, "flat_count": 100},
        "top_sectors": [
            {"name": "半导体", "change_pct": 2.1},
            {"name": "银行", "change_pct": -0.8},
        ],
    }
    text = sig.build_rule_summary(payload)
    assert "上涨3120" in text
    assert "半导体" in text and "银行" in text
    assert not any(w in text for w in ("建议", "买入", "卖出"))  # 不含买卖建议


def test_build_rule_summary_empty_payload() -> None:
    assert "暂无" in sig.build_rule_summary({})
