"""离线 CSV 因子诊断：公式、对齐、时间隔离及输入边界。"""
import csv
import io
import json
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.core.factor_research import (
    _daily_diagnostics,
    _read_csv,
    _summary,
    alpha144,
    research_alpha144,
)


FIELDS = ["date", "stock_code", "open", "close", "amount", "adj_factor", "is_st", "listing_days"]


def _rows(days=18, stocks=10, market_cap=False):
    records = []
    for t in range(days):
        for s in range(stocks):
            record = {
                "date": (date(2024, 1, 1) + timedelta(days=t)).isoformat(),
                "stock_code": f"{s + 1:06d}",
                "open": 100 * (1 + 0.002 * (s + 1)) ** t,
                "close": 100 * (1 - 0.005 * (s + 1)) ** t,
                "amount": 10_000_000, "adj_factor": 1, "is_st": 0, "listing_days": 200,
            }
            if market_cap:
                record["market_cap"] = (stocks - s) * 1e8
            records.append(record)
    return records


def _csv(records, fields=None):
    fields = fields or FIELDS + (["market_cap"] if any("market_cap" in row for row in records) else [])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue()


def _research(records, **kwargs):
    options = dict(window=2, horizon=1, groups=5, min_listing_days=0, min_average_amount=0)
    options.update(kwargs)
    return research_alpha144(_csv(records), **options)


def test_alpha144_hand_calculation_and_input_immutability():
    close = pd.DataFrame({"000001": [100, 90, 99, 79.2, 71.28]})
    amount = pd.DataFrame({"000001": [5, 10, 20, 40, 50]})
    original_close, original_amount = close.copy(), amount.copy()
    actual = alpha144(close, amount, 3)
    assert actual.iloc[:3].isna().all().all()
    assert actual.iloc[3, 0] == pytest.approx((0.1 / 10 + 0.2 / 40) / 2)
    assert actual.iloc[4, 0] == pytest.approx((0.2 / 40 + 0.1 / 50) / 2)
    pd.testing.assert_frame_equal(close, original_close)
    pd.testing.assert_frame_equal(amount, original_amount)


@pytest.mark.parametrize("bad", [0, np.nan])
def test_zero_or_missing_amount_invalidates_even_an_up_day(bad):
    close = pd.DataFrame({"a": [100, 90, 99, 80, 70, 60]})
    amount = pd.DataFrame({"a": [10, 10, bad, 10, 10, 10]})
    actual = alpha144(close, amount, 2)
    assert actual.iloc[2:4].isna().all().all()
    assert actual.iloc[4, 0] == pytest.approx(((1 - 80 / 99) + (1 - 70 / 80)) / 20)


def test_missing_price_requires_new_complete_return_window():
    close = pd.DataFrame({"a": [100, np.nan, 90, 80, 70]})
    amount = pd.DataFrame({"a": [10] * 5})
    actual = alpha144(close, amount, 2)
    assert actual.iloc[:4].isna().all().all()
    assert pd.notna(actual.iloc[4, 0])


@pytest.mark.parametrize("prices", [[100, 110, 120, 130], [100, 100, 100, 100]])
def test_no_down_days_remain_missing(prices):
    result = alpha144(pd.DataFrame({"a": prices}), pd.DataFrame({"a": [10] * 4}), 2)
    assert result.isna().all().all()


def test_future_changes_and_appended_rows_cannot_change_past_factor():
    close = pd.DataFrame({"a": [100, 90, 80, 85, 75, 70, 60, 55]})
    amount = pd.DataFrame({"a": [10, 20, 10, 20, 10, 20, 10, 20]})
    expected = alpha144(close, amount, 2)
    changed_close, changed_amount = close.copy(), amount.copy()
    changed_close.loc[5:, "a"] = [1000, 1, 2000]
    changed_amount.loc[5:, "a"] = [1, 0, 1000]
    actual = alpha144(changed_close, changed_amount, 2)
    pd.testing.assert_frame_equal(expected.iloc[:5], actual.iloc[:5])
    pd.testing.assert_frame_equal(expected.iloc[:5], alpha144(close.iloc[:5], amount.iloc[:5], 2))


def test_formula_aligns_amount_columns_by_stock():
    close = pd.DataFrame({"000001": [100, 90, 81], "600001": [100, 90, 81]})
    amount = pd.DataFrame({"600001": [20, 20, 20], "000001": [10, 10, 10]})
    result = alpha144(close, amount, 2)
    assert result.iloc[2].to_dict() == pytest.approx({"000001": 0.01, "600001": 0.005})


def test_adjustment_cancels_split_for_both_factor_and_open_labels():
    records = _rows()
    expected = _research(records)
    split_date = records[40]["date"]
    split_records = [dict(row) for row in records]
    for row in split_records:
        if row["date"] >= split_date:
            row["open"] /= 2
            row["close"] /= 2
            row["adj_factor"] = 2
    assert _research(split_records) == expected
    for row in split_records:
        row["adj_factor"] = 1
    unadjusted = _research(split_records)
    assert unadjusted["daily"][2]["group_returns_pct"][-1] < -40
    assert expected["daily"][2]["group_returns_pct"][-1] > 0


def test_flat_adjusted_price_does_not_create_artificial_down_factor():
    records = _rows(days=7, stocks=5)
    for i, row in enumerate(records):
        after_split = i // 5 >= 3
        row.update(open=50 if after_split else 100, close=50 if after_split else 100,
                   adj_factor=2 if after_split else 1)
    result = _research(records)
    assert result["coverage"]["eligible_observations"] == 0
    assert result["available"] is False


def test_unsorted_bom_csv_and_missing_stock_dates_are_aligned():
    records = _rows(days=8)
    del records[10]  # 000001 缺第二个上传日期，不能前向填充。
    expected = _research(records)
    shuffled = list(reversed(records))
    actual = research_alpha144("\ufeff" + _csv(shuffled), window=2, horizon=1,
                               min_average_amount=0, min_listing_days=0)
    assert actual == expected
    assert actual["coverage"]["missing_cells"] == 1
    assert actual["daily"][2]["samples"] == 9
    assert actual["daily"][3]["samples"] == 9
    assert actual["daily"][4]["samples"] == 10
    assert _read_csv(_csv(shuffled))["stock_code"].str.len().eq(6).all()
    assert "000001" in _read_csv(_csv(shuffled))["stock_code"].values


@pytest.mark.parametrize("field,bad", [
    ("open", ""), ("open", 0), ("close", -1), ("adj_factor", 0),
    ("is_st", ""), ("is_st", 1), ("listing_days", ""), ("listing_days", 119),
    ("amount", ""), ("amount", 0),
])
def test_eligibility_never_fills_missing_prices_or_status(field, bad):
    records = _rows(days=8, stocks=5)
    for row in records[15:20]:
        row[field] = bad
    result = _research(records, min_listing_days=120)
    assert result["daily"][3]["samples"] == 0
    assert result["daily"][3]["group_returns_pct"] == [None] * 5


def test_liquidity_filter_uses_trailing_window_average_inclusively():
    records = _rows(days=7, stocks=5)
    for row in records[5:10]:
        row["amount"] = 2_000_000
    for row in records[10:15]:
        row["amount"] = 8_000_000
    assert _research(records, min_average_amount=5_000_000)["daily"][2]["samples"] == 5
    assert _research(records, min_average_amount=5_000_001)["daily"][2]["samples"] == 0


def test_rank_ic_reranks_only_common_aligned_samples():
    score = pd.Series([1, 2, 3, 4, 5, 6, 100], index=list("abcdefg"))
    forward = pd.Series([np.nan, 40, 10, 30, np.nan, 20, 50], index=list("gfedcba"))
    actual = _daily_diagnostics(score, forward, 2)
    assert actual["samples"] == 5
    assert actual["rank_ic"] == pytest.approx(-0.3)
    expected_ic = np.corrcoef([1, 2, 4, 5, 6], [50, 20, 30, 10, 40])[0, 1]
    assert actual["ic"] == pytest.approx(expected_ic)
    assert actual["ic"] != pytest.approx(actual["rank_ic"])


@pytest.mark.parametrize("sign", [1, -1])
def test_exact_positive_negative_and_tied_rank_relationships(sign):
    score = pd.Series([1, 1, 2, 3, 4, 5])
    forward = score * sign / 100
    result = _daily_diagnostics(score, forward, 3)
    assert result["rank_ic"] == pytest.approx(sign)
    assert result["ic"] == pytest.approx(sign)
    assert result["group_counts"] == [2, 2, 2]


@pytest.mark.parametrize("score,forward", [
    ([1] * 5, [1, 2, 3, 4, 5]), ([1, 2, 3, 4, 5], [2] * 5),
    ([0] * 5, [1, 2, 3, 4, 5]),
])
def test_constant_cross_sections_have_no_ic(score, forward):
    result = _daily_diagnostics(pd.Series(score), pd.Series(forward), 5)
    assert result["samples"] == 5
    assert result["ic"] is None
    assert result["rank_ic"] is None


def test_ties_do_not_get_split_to_fill_groups():
    result = _daily_diagnostics(pd.Series([1] * 5), pd.Series([1, 2, 3, 4, 5]) / 100, 5)
    assert result["group_counts"] == [0, 0, 5, 0, 0]
    assert result["group_returns_pct"] == [None, None, 3.0, None, None]


@pytest.mark.parametrize("samples,groups,valid", [(4, 2, False), (5, 5, True), (9, 10, False), (10, 10, True)])
def test_ic_requires_max_five_or_groups_samples(samples, groups, valid):
    score = pd.Series(range(samples), dtype=float)
    result = _daily_diagnostics(score, score / 100, groups)
    assert (result["rank_ic"] is not None) is valid
    assert sum(result["group_counts"]) == samples


def test_groups_are_assigned_before_missing_labels_are_removed():
    score = pd.Series([1, 2, 3, 4, 5, 6])
    forward = pd.Series([np.nan, np.nan, np.nan, 0.1, 0.2, 0.3])
    actual = _daily_diagnostics(score, forward, 2)
    assert actual["samples"] == 3
    assert actual["group_counts"] == [0, 3]
    assert actual["group_returns_pct"][0] is None
    assert actual["group_returns_pct"][1] == pytest.approx(20)
    assert actual["rank_ic"] is None


def test_summaries_equal_weight_dates_not_stock_counts_or_compounding():
    daily = [
        {"ic": -0.8, "rank_ic": -1, "group_returns_pct": [10, None, 30], "group_counts": [1, 0, 2]},
        {"ic": 0.2, "rank_ic": 0.5, "group_returns_pct": [20, 40, 50], "group_counts": [9, 1, 3]},
        {"ic": None, "rank_ic": None, "group_returns_pct": [None, 60, None], "group_counts": [0, 2, 0]},
    ]
    result = _summary(daily, 3)
    assert result["days"] == 2
    assert result["ic_mean"] == pytest.approx(-0.3)
    assert result["rank_ic_mean"] == -0.25
    assert result["rank_ic_std"] == pytest.approx(np.std([-1, 0.5], ddof=1))
    assert result["rank_ic_ir"] == pytest.approx(-0.25 / np.std([-1, 0.5], ddof=1))
    assert result["rank_ic_positive_ratio"] == 0.5
    assert result["group_returns_pct"] == pytest.approx([15, 50, 40])
    assert result["group_counts"] == [10, 3, 5]
    assert result["top_bottom_spread_pct"] == pytest.approx(25)
    assert _summary(daily[:1], 3)["rank_ic_std"] is None
    assert _summary(daily[:1], 3)["rank_ic_ir"] is None
    assert _summary([daily[0], daily[0]], 3)["rank_ic_std"] == 0
    assert _summary([daily[0], daily[0]], 3)["rank_ic_ir"] is None
    empty = _summary([], 3)
    assert empty["group_returns_pct"] == [None] * 3
    assert empty["group_counts"] == [0] * 3
    assert empty["rank_ic_positive_ratio"] is None
    assert empty["top_bottom_spread_pct"] is None


def test_spread_only_compares_groups_on_common_dates():
    daily = [
        {"ic": None, "rank_ic": None, "group_returns_pct": [1, 4], "group_counts": [2, 2]},
        {"ic": None, "rank_ic": None, "group_returns_pct": [100, None], "group_counts": [2, 0]},
        {"ic": None, "rank_ic": None, "group_returns_pct": [None, -100], "group_counts": [0, 2]},
    ]
    assert _summary(daily, 2)["top_bottom_spread_pct"] == 3
    assert _summary(daily[1:], 2)["top_bottom_spread_pct"] is None


@pytest.mark.parametrize("horizon,expected", [(1, 50), (2, 350)])
def test_forward_label_is_next_open_to_horizon_later_union_date(horizon, expected):
    records = _rows(days=6, stocks=1)
    dates = ["2024-01-05", "2024-01-09", "2024-01-15", "2024-02-01", "2024-02-20", "2024-03-08"]
    for row, day, opening in zip(records, dates, [10, 11, 12, 20, 30, 90]):
        row.update(date=day, open=opening)
    result = _research(records, horizon=horizon, groups=2)
    assert [row["date"] for row in result["daily"]] == dates
    assert result["daily"][2]["group_returns_pct"] == [None, expected]
    assert result["daily"][2]["samples"] == 1
    assert result["daily"][-1]["segment"] == "unlabelled"
    assert result["daily"][-1]["samples"] == 0
    assert result["available"] is False  # 有组收益并不意味着有有效 RankIC。


def test_missing_future_open_is_missing_not_zero_return():
    records = _rows(days=7, stocks=5)
    for row in records[15:20]:
        row["open"] = ""
    result = _research(records)
    assert result["daily"][2]["samples"] == 0
    assert result["daily"][2]["group_returns_pct"] == [None] * 5
    coverage = result["coverage"]
    assert coverage["missing_forward_observations"] > 0
    assert coverage["paired_observations"] + coverage["missing_forward_observations"] == coverage["eligible_observations"]
    assert any("缺失远期收益" in note and "%" in note for note in result["notes"])


def test_time_holdout_purges_label_end_crossing_cutoff():
    result = _research(_rows(days=20), horizon=3, train_ratio=0.7)
    daily = result["daily"]
    assert result["split"]["cutoff_date"] == daily[14]["date"]
    assert [row["segment"] for row in daily] == ["train"] * 10 + ["purged"] * 4 + ["test"] * 2 + ["unlabelled"] * 4
    assert result["split"]["train_days"] == 10
    assert result["split"]["purged_days"] == 4
    assert result["split"]["test_days"] == 2
    assert result["summary"]["train"]["days"] == 8
    assert result["summary"]["test"]["days"] == 2
    assert result["summary"]["all"]["days"] == 14
    for position, row in enumerate(daily):
        if row["segment"] == "train":
            assert position + 4 < 14
        elif row["segment"] == "test":
            assert position >= 14 and position + 4 < 20
    assert result["summary"]["train"] == _summary(daily[:10], 5)
    assert result["summary"]["test"] == _summary(daily[14:16], 5)


def test_changes_at_and_after_cutoff_cannot_change_train_diagnostics():
    records = _rows(days=20)
    expected = _research(records, horizon=3)
    cutoff = expected["split"]["cutoff_date"]
    changed = [dict(row) for row in records]
    for row in changed:
        if row["date"] >= cutoff:
            row["open"] = 50 / row["open"]
            row["close"] *= 0.5
            row["amount"] *= 2
    actual = _research(changed, horizon=3)
    assert actual["summary"]["train"] == expected["summary"]["train"]
    assert actual["summary"]["test"] != expected["summary"]["test"]
    assert actual["summary"]["all"] != expected["summary"]["all"]


def test_short_sample_and_long_horizon_leave_all_dates_unlabelled():
    result = _research(_rows(days=1), horizon=120, train_ratio=0.5)
    assert result["split"]["cutoff_date"] == result["daily"][0]["date"]
    assert result["daily"][0]["segment"] == "unlabelled"
    assert result["summary"]["all"]["days"] == 0
    assert result["message"]


def test_direction_is_explicit_and_negative_ic_is_not_discarded():
    records = _rows()
    positive = _research(records, direction=1)
    negative = _research(records, direction=-1)
    assert positive["coverage"] == negative["coverage"]
    assert positive["parameters"]["direction"] == 1
    assert negative["parameters"]["direction"] == -1
    assert positive["summary"]["all"]["rank_ic_mean"] == pytest.approx(1)
    assert negative["summary"]["all"]["rank_ic_mean"] == pytest.approx(-1)
    assert negative["summary"]["all"]["rank_ic_positive_ratio"] == 0
    assert negative["available"] is True
    for a, b in zip(positive["daily"], negative["daily"]):
        assert a["group_returns_pct"] == b["group_returns_pct"][::-1]
        if a["ic"] is not None:
            assert a["ic"] == pytest.approx(-b["ic"])


def test_size_exposure_is_daily_rank_mean_and_stock_day_top_share():
    result = _research(_rows(market_cap=True))
    exposure = result["size_exposure"]
    assert exposure["available"] is True
    assert exposure["observations"] == result["coverage"]["eligible_observations"]
    assert exposure["coverage_ratio"] == 1
    assert exposure["rank_correlation"] == pytest.approx(-1)
    assert exposure["top_group_small_cap_share"] == 1
    # 尾部仍是可观测的当日暴露，不受未来收益是否已知影响。
    assert exposure["observations"] > result["coverage"]["paired_observations"]


def test_size_correlation_weights_dates_but_small_cap_share_weights_stock_days():
    records = _rows(days=6, market_cap=True)
    for i, row in enumerate(records):
        day, stock = divmod(i, 10)
        if day == 3:
            row["market_cap"] = (stock + 1) * 1e8 if stock < 9 else ""
        elif day != 2:
            row["market_cap"] = ""
    result = _research(records)
    exposure = result["size_exposure"]
    assert exposure["observations"] == 19
    assert exposure["coverage_ratio"] == 19 / 40
    assert exposure["rank_correlation"] == pytest.approx(0, abs=1e-14)
    assert exposure["top_group_small_cap_share"] == pytest.approx(2 / 3)


@pytest.mark.parametrize("cap", [None, "", 0, -1])
def test_missing_or_nonpositive_market_cap_is_not_fake_neutrality(cap):
    records = _rows()
    if cap is not None:
        for row in records:
            row["market_cap"] = cap
    result = _research(records)
    assert result["size_exposure"] == {
        "available": False, "observations": 0, "coverage_ratio": 0.0,
        "rank_correlation": None, "top_group_small_cap_share": None,
    }
    assert any("不代表中性" in note for note in result["notes"])


def test_partial_cap_coverage_reranks_and_does_not_reassign_top_group():
    records = _rows(market_cap=True)
    for row in records:
        if row["stock_code"] == "000010":
            row["market_cap"] = ""
    result = _research(records)
    exposure = result["size_exposure"]
    assert exposure["coverage_ratio"] == 0.9
    assert exposure["rank_correlation"] == pytest.approx(-1)
    assert exposure["top_group_small_cap_share"] == 1
    for row in records:
        if row["stock_code"] == "000009":
            row["market_cap"] = ""
    assert _research(records)["size_exposure"]["top_group_small_cap_share"] is None


def test_size_ties_expand_threshold_and_constant_rank_is_null():
    records = _rows(market_cap=True)
    for row in records:
        row["market_cap"] = 100
    result = _research(records)
    assert result["size_exposure"]["rank_correlation"] is None
    assert result["size_exposure"]["top_group_small_cap_share"] == 1
    assert any("阈值存在并列" in note and "扩张" in note for note in result["notes"])


def test_no_eligible_observations_leave_cap_coverage_null():
    records = _rows(days=1, market_cap=True)
    result = _research(records)
    assert result["size_exposure"]["available"] is False
    assert result["size_exposure"]["coverage_ratio"] is None


@pytest.mark.parametrize("name,value", [
    ("window", 1), ("window", 121), ("window", 2.0), ("window", True),
    ("horizon", 0), ("horizon", 121), ("horizon", "2"),
    ("groups", 1), ("groups", 11), ("groups", None),
    ("direction", 0), ("direction", 2), ("direction", -2), ("direction", True),
    ("direction", 1.0), ("train_ratio", 0.49), ("train_ratio", 0.91),
    ("train_ratio", float("nan")), ("train_ratio", float("inf")), ("train_ratio", "0.7"),
    ("min_listing_days", -1), ("min_listing_days", 1001), ("min_listing_days", 1.5),
    ("min_average_amount", -1), ("min_average_amount", 1e12 + 1),
    ("min_average_amount", float("inf")), ("min_average_amount", True),
    ("min_average_amount", 10 ** 1000),
])
def test_parameters_are_validated_inside_pure_function(name, value):
    with pytest.raises(ValueError, match="参数 " + name):
        research_alpha144(_csv(_rows(days=1, stocks=1)), **{name: value})


@pytest.mark.parametrize("options", [
    dict(window=2, horizon=1, groups=2, min_listing_days=0, min_average_amount=0, direction=-1, train_ratio=0.5),
    dict(window=120, horizon=120, groups=10, min_listing_days=1000, min_average_amount=1e12, direction=1, train_ratio=0.9),
])
def test_parameter_boundary_values_are_accepted(options):
    result = research_alpha144(_csv(_rows(days=1, stocks=1)), **options)
    assert result["parameters"] == options


@pytest.mark.parametrize("field,text", [
    ("date", "2024-1-01"), ("date", "2024-02-30"), ("date", "2024-01-01T00:00:00"),
    ("date", " 2024-01-01"), ("date", "0000-01-01"), ("date", ""),
    ("stock_code", "1"), ("stock_code", "00001A"), ("stock_code", "０００００１"),
    ("stock_code", "000001.0"), ("stock_code", "000001 "),
    ("open", "NaN"), ("close", "inf"), ("adj_factor", "-Infinity"),
    ("amount", "1e309"), ("amount", "hello"), ("amount", "1_000"),
    ("amount", "-1"), ("amount", "-1e-999"), ("is_st", "2"),
    ("is_st", "0.000000000000000000000001"), ("is_st", "1.00000000000000001"),
    ("listing_days", "-1"), ("listing_days", "1.1"),
    ("listing_days", "9007199254740992.1"), ("market_cap", "NaN"),
    ("market_cap", "1,000"), ("open", "0e999999999999999999999999999999"),
])
def test_csv_invalid_scalars_fail_readably(field, text):
    record = _rows(days=1, stocks=1)[0]
    record[field] = text
    with pytest.raises(ValueError, match="CSV 第 .*" + field):
        _research([record])


def test_blank_numeric_cells_are_missing_and_amount_zero_is_retained():
    record = _rows(days=1, stocks=1)[0]
    record.update(open="", close="  ", amount="0", adj_factor="-1", is_st="", listing_days="", market_cap="0")
    frame = _read_csv(_csv([record]))
    for field in ("open", "close", "adj_factor", "is_st", "listing_days", "market_cap"):
        assert pd.isna(frame.iloc[0][field])
    assert frame.iloc[0]["amount"] == 0


@pytest.mark.parametrize("csv_text,match", [
    ("", "不能为空"), ("\ufeff \n", "不能为空"), (",".join(FIELDS) + "\n", "没有数据行"),
    (",".join(FIELDS + ["open"]) + "\n", "重复表头"),
    (",".join(FIELDS[:-1]) + "\n", "缺少必需列"),
    (",".join(FIELDS + ["unknown"]) + "\n", "未知字段"),
    (",".join(FIELDS) + "\n2024-01-01,000001,1\n", "列数不齐"),
    (",".join(FIELDS) + "\n2024-01-01,000001,1,1,1,1,0,120,extra\n", "列数不齐"),
    (",".join(FIELDS) + "\n\n", "列数不齐"),
    (",".join(FIELDS) + '\n"2024-01-01,000001,1,1,1,1,0,120', "格式错误"),
    (None, "文本"), (b"csv", "文本"),
])
def test_csv_structure_is_strict(csv_text, match):
    with pytest.raises(ValueError, match=match):
        research_alpha144(csv_text)


def test_duplicate_stock_date_is_rejected():
    row = _rows(days=1, stocks=1)[0]
    with pytest.raises(ValueError, match="重复 date/stock_code"):
        _research([row, row])


def _small_record(day, stock):
    return dict(date=(date(2020, 1, 1) + timedelta(days=day)).isoformat(),
                stock_code=f"{stock:06d}", open=1, close=1, amount=1,
                adj_factor=1, is_st=0, listing_days=120)


def test_csv_character_limit():
    with pytest.raises(ValueError, match="字符数.*2000000"):
        research_alpha144("x" * 2_000_001)


def test_csv_row_limit_before_panel_allocation():
    records = [_small_record(t, s) for t in range(101) for s in range(200)][:20_001]
    text = _csv(records)
    assert len(text) < 2_000_000
    with pytest.raises(ValueError, match="行数.*20000"):
        research_alpha144(text)


def test_csv_stock_limit():
    with pytest.raises(ValueError, match="股票数.*200"):
        _research([_small_record(0, s) for s in range(201)])


def test_csv_date_limit():
    with pytest.raises(ValueError, match="日期数.*1000"):
        _research([_small_record(t, 1) for t in range(1001)])


def test_sparse_panel_product_limit():
    records = [_small_record(0, s) for s in range(200)]
    records += [_small_record(t, 0) for t in range(1, 501)]
    with pytest.raises(ValueError, match="面板大小.*100000"):
        _research(records)


def test_twenty_thousand_rows_and_two_hundred_stocks_are_accepted():
    records = [_small_record(t, s) for t in range(100) for s in range(200)]
    result = _research(records)
    assert result["coverage"]["rows"] == 20_000
    assert result["coverage"]["stocks"] == 200
    assert result["coverage"]["missing_cells"] == 0


def test_one_thousand_dates_and_exact_panel_product_limit_are_accepted():
    records = [_small_record(t, t % 100) for t in range(1000)]
    result = _research(records)
    assert result["coverage"]["dates"] == 1000
    assert result["coverage"]["stocks"] == 100
    assert result["coverage"]["missing_cells"] == 99_000


def test_response_exact_keys_and_strict_json_for_available_and_unavailable():
    for records in (_rows(), _rows(days=1), _rows(market_cap=True)):
        result = _research(records)
        assert set(result) == {"available", "message", "factor", "parameters", "coverage", "split", "summary", "daily", "size_exposure", "notes"}
        assert set(result["coverage"]) == {"rows", "dates", "stocks", "missing_cells", "eligible_observations", "paired_observations", "missing_forward_observations", "ic_days"}
        assert set(result["split"]) == {"cutoff_date", "train_days", "purged_days", "test_days", "label_rule", "method"}
        assert set(result["summary"]) == {"all", "train", "test"}
        for summary in result["summary"].values():
            assert set(summary) == {"days", "ic_mean", "rank_ic_mean", "rank_ic_std", "rank_ic_ir", "rank_ic_positive_ratio", "group_returns_pct", "group_counts", "top_bottom_spread_pct"}
        for row in result["daily"]:
            assert set(row) == {"date", "segment", "samples", "ic", "rank_ic", "group_returns_pct", "group_counts"}
            assert len(row["group_returns_pct"]) == len(row["group_counts"]) == 5
            assert all(value is None or math.isfinite(value) for value in row["group_returns_pct"])
        assert result["factor"] == "alpha144"
        assert result["available"] == (result["coverage"]["ic_days"] >= 1)
        assert json.loads(json.dumps(result, allow_nan=False)) == result
        notes = " ".join(result["notes"])
        for term in ("未训练模型", "未扣费用", "滑点", "涨跌停", "停牌", "幸存者", "日历", "历史 ST", "市值", "重叠", "未年化", "收益承诺", "负 IC"):
            assert term in notes


def test_extreme_finite_numbers_do_not_leak_nan_or_infinity_to_json():
    records = _rows(days=7)
    for i, row in enumerate(records):
        row["market_cap"] = 1e308
        if i // 10 == 3:
            row["open"] = 1e308
            row["adj_factor"] = 1e308
        elif i // 10 == 4:
            row["open"] = 1e-300
    result = _research(records)
    json.dumps(result, allow_nan=False)
    diagnostic = _daily_diagnostics(pd.Series(range(1, 6)), pd.Series([1e308] * 5), 5)
    json.dumps(diagnostic, allow_nan=False)
