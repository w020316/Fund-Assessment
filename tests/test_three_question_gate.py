"""出手三问闸门测试"""
from __future__ import annotations

from src.strategies.three_question_gate import ThreeQuestionGate, check_three_questions


def test_all_three_pass():
    gate = ThreeQuestionGate()
    result = gate.check(
        "600519",
        stock_name="贵州茅台",
        sector="白酒",
        prev_close=10.0,
        break_close=10.5,
        auction_price=11.0,
        sector_change_pct=4.0,
        sector_limit_up_count=6,
        is_sector_leader=True,
    )
    assert result.passed is True
    assert [q.key for q in result.questions] == ["identity", "break_board", "auction"]
    assert all(q.passed for q in result.questions)
    assert "三问全过" in result.verdict
    assert result.missing == []


def test_auction_failure_blocks_trade():
    gate = ThreeQuestionGate()
    result = gate.check(
        "600519",
        prev_close=10.0,
        break_close=10.5,
        auction_price=10.4,
        sector_change_pct=4.0,
        sector_limit_up_count=6,
        is_sector_leader=True,
    )
    assert result.passed is False
    assert result.failed == ["竞价"]
    assert "放弃" in result.verdict


def test_missing_data_is_fail_closed():
    gate = ThreeQuestionGate()
    result = gate.check(
        "600519",
        sector_change_pct=5.0,
        sector_limit_up_count=8,
        is_sector_leader=True,
    )
    assert result.passed is False
    assert set(result.missing) == {"断板", "竞价"}
    assert "数据不足" in result.verdict


def test_identity_requires_hot_sector_and_leader():
    gate = ThreeQuestionGate()
    result = gate.check(
        "600519",
        prev_close=10.0,
        break_close=10.6,
        auction_price=10.8,
        sector_change_pct=0.5,
        sector_limit_up_count=1,
        is_sector_leader=False,
    )
    assert result.passed is False
    assert "身份" in result.failed


def test_auto_check_uses_injected_data(monkeypatch):
    from src.strategies import three_question_gate as module

    pool = [
        {"code": "600519", "name": "贵州茅台", "streak": 3, "seal_amount": 200_000_000, "industry": "白酒"},
        {"code": "600520", "name": "甲", "streak": 1, "seal_amount": 10_000_000, "industry": "白酒"},
        {"code": "600521", "name": "乙", "streak": 1, "seal_amount": 5_000_000, "industry": "白酒"},
    ]
    monkeypatch.setattr(module, "fetch_limit_pool", lambda date_str: pool)
    monkeypatch.setattr(
        module.ds2,
        "get_realtime_quote_tencent",
        lambda codes: [{"code": "600519", "name": "贵州茅台", "open": 11.0}],
    )
    today = __import__("datetime").date.today().strftime("%Y-%m-%d")
    monkeypatch.setattr(
        module.ds2,
        "get_kline_mootdx",
        lambda symbol, period="daily", count=6: [
            {"date": "2026-10-29", "close": 9.5},
            {"date": "2026-10-30", "close": 10.0},
            {"date": today, "close": 10.8},
        ],
    )
    result = module.ThreeQuestionGate().auto_check("600519")
    assert result.mode == "auto"
    # 今日 K 线被剔除:断板日 10.0 > 前一日 9.5,竞价 11.0 > 10.0
    assert result.questions[1].passed is True
    assert result.questions[2].passed is True
    assert result.questions[0].passed is True
    assert result.passed is True


def test_check_three_questions_manual_wrapper():
    data = check_three_questions(
        "600519",
        mode="manual",
        prev_close=10.0,
        break_close=9.9,
        auction_price=10.0,
        sector_change_pct=3.0,
        sector_limit_up_count=5,
        is_sector_leader=True,
    )
    assert data["passed"] is False
    assert "断板" in data["failed"]
