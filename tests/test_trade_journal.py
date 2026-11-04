"""交易日志与纪律归因测试"""
from __future__ import annotations

import json

import pytest

from src.analysis import trade_journal


def _entry(action: str, code: str = "600519", gate: bool | None = None, day: str = "2026-11-04", note: str = "") -> dict:
    payload = {
        "action": action,
        "stock_code": code,
        "price": 10.0,
        "quantity": 100,
        "note": note,
        "trade_date": day,
    }
    if gate is not None:
        payload["gate_passed"] = gate
        payload["gate_mode"] = "manual"
    return trade_journal.validate_entry(payload)


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"action": "hold", "stock_code": "600519"}, "action"),
        ({"action": "buy", "stock_code": "abc"}, "stock_code"),
        ({"action": "buy", "stock_code": "600519", "trade_date": "2026/11/04"}, "trade_date"),
        ({"action": "buy", "stock_code": "600519", "price": -1}, "负数"),
        ({"action": "buy", "stock_code": "600519", "emotion": "兴奋"}, "emotion"),
        ({"action": "buy", "stock_code": "600519", "note": "x" * 301}, "300"),
    ],
)
def test_validate_entry_rejects_bad_input(payload, message):
    with pytest.raises(ValueError) as exc:
        trade_journal.validate_entry(payload)
    assert message in str(exc.value)


def test_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = trade_journal.append_entry({"action": "buy", "stock_code": "600519", "gate_passed": True}, path=path)
    second = trade_journal.append_entry({"action": "abandon", "stock_code": "000001", "gate_passed": False}, path=path)
    assert first["id"] != second["id"]

    entries = trade_journal.load_entries(path=path)
    assert len(entries) == 2
    # 新→旧
    assert entries[0]["stock_code"] == "000001"


def test_load_skips_malformed_lines(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"stock_code":"600519","action":"buy","created_at":"2026-11-04 10:00:00"}\n'
        "not-json\n"
        "\n"
        '{"stock_code":"000001","action":"watch","created_at":"2026-11-04 11:00:00"}\n',
        encoding="utf-8",
    )
    entries = trade_journal.load_entries(path=path)
    assert len(entries) == 2


def test_compute_stats_discipline_score():
    entries = [
        _entry("buy", "600519", gate=True),
        _entry("buy", "600519", gate=False),
        _entry("buy", "000001", gate=None),
        _entry("buy", "000002", gate=True),
        _entry("abandon", "000003", gate=False),
        _entry("abandon", "000004", gate=False),
        _entry("watch", "000005", gate=True),
    ]
    stats = trade_journal.compute_stats(entries)
    assert stats["buy_count"] == 4
    assert stats["compliance_rate"] == pytest.approx(0.5)
    assert stats["buys_with_gate_fail"] == 1
    assert stats["buys_without_gate"] == 1
    assert stats["check_coverage"] == pytest.approx(0.75)  # 4 笔交易中 3 笔有三问记录
    assert stats["max_buys_per_day"] == 4
    assert stats["overtrade_days"] == ["2026-11-04"]
    # 100 - 50*1/4 - 30*1/4 - 10 = 70
    assert stats["score"] == pytest.approx(70.0)
    assert stats["grade"] == "待改进"
    assert any("不通过" in issue for issue in stats["issues"])
    assert any("没有三问" in issue for issue in stats["issues"])
    assert any("过度交易" in issue for issue in stats["issues"])


def test_compute_stats_sample_insufficient():
    stats = trade_journal.compute_stats([_entry("buy", "600519", gate=True)])
    assert stats["score"] is None
    assert stats["grade"] == "样本不足"
    assert stats["sample_sufficient"] is False


def test_compute_stats_no_buy_records():
    stats = trade_journal.compute_stats([_entry("abandon", gate=False), _entry("watch", gate=True), _entry("abandon", gate=False)])
    assert stats["compliance_rate"] is None
    assert stats["check_coverage"] is None
    assert stats["score"] == 100.0
    assert stats["grade"] == "优秀"


def test_journal_report(tmp_path):
    path = tmp_path / "journal.jsonl"
    trade_journal.append_entry({"action": "buy", "stock_code": "600519", "gate_passed": True}, path=path)
    trade_journal.append_entry({"action": "abandon", "stock_code": "000001", "gate_passed": False}, path=path)
    report = trade_journal.journal_report(path=path)
    assert report["stats"]["total"] == 2
    assert len(report["entries"]) == 2
    assert report["updated_at"]


def test_journal_default_path_from_env(monkeypatch, tmp_path):
    target = tmp_path / "env_journal.jsonl"
    monkeypatch.setenv("TRADE_JOURNAL_PATH", str(target))
    trade_journal.append_entry({"action": "watch", "stock_code": "600519"})
    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8").strip())
    assert payload["action"] == "watch"
