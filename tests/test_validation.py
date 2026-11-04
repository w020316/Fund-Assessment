"""回测验证(滚动样本外 + 蒙特卡洛)测试"""
from __future__ import annotations

from src.core.validation import (
    monte_carlo,
    validate_returns,
    walk_forward_splits,
)


def test_walk_forward_splits():
    splits = walk_forward_splits(100, train_size=60, test_size=20, step=20)
    assert len(splits) == 2
    assert splits[0] == {
        "train_start": 0,
        "train_end": 60,
        "test_start": 60,
        "test_end": 80,
    }
    assert splits[1]["test_end"] == 100
    assert walk_forward_splits(50, train_size=60, test_size=20) == []


def test_monte_carlo_deterministic():
    returns = [0.02, -0.01, 0.03, -0.02, 0.01]
    first = monte_carlo(returns, runs=200, seed=7)
    second = monte_carlo(returns, runs=200, seed=7)
    assert first == second
    assert first["available"] is True
    assert 0.0 <= first["prob_profit"] <= 1.0
    assert first["final_return"]["p5"] <= first["final_return"]["p95"]


def test_monte_carlo_empty():
    result = monte_carlo([], runs=100)
    assert result["available"] is False


def test_validate_returns_insufficient_samples():
    returns = [0.01, -0.01] * 10
    result = validate_returns(returns, train_size=60, test_size=20)
    assert result["walk_forward"]["stability"] == "样本不足"
    assert result["monte_carlo"]["available"] is True
    assert any("样本数" in note for note in result["notes"])


def test_validate_returns_with_windows():
    returns = [0.01, -0.004] * 50
    result = validate_returns(returns, train_size=40, test_size=20, runs=200)
    wf = result["walk_forward"]
    assert wf["window_count"] >= 1
    assert 0.0 <= wf["positive_ratio"] <= 1.0
    assert wf["stability"] in {"稳定", "一般", "不稳定"}
    assert result["monte_carlo"]["runs"] == 200
