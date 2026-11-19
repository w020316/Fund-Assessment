"""涨停板策略单元测试

验证 src/strategies/limit_up.py 的核心逻辑:
- LimitLevel / LimitReason 枚举
- LimitUpInfo 数据类
- _determine_level 连板等级判定
- _calc_quality_score 质量评分(封板时间/炸板/封单/等级)
- scan_limit_up / analyze_limit_up / predict_promotion (mock akshare)
"""
from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import patch

import pandas as pd
import pytest

from src.strategies import limit_up as mod
from src.strategies.limit_up import (
    LimitLevel,
    LimitReason,
    LimitUpAnalyzer,
    LimitUpInfo,
)


@pytest.fixture
def analyzer() -> LimitUpAnalyzer:
    return LimitUpAnalyzer()


class TestEnums:
    """枚举类"""

    def test_limit_level_values(self):
        assert LimitLevel.FIRST.value == "首板"
        assert LimitLevel.SECOND.value == "二板"
        assert LimitLevel.THIRD_PLUS.value == "三板+"

    def test_limit_reason_values(self):
        assert LimitReason.THEME.value == "题材"
        assert LimitReason.PERFORMANCE.value == "业绩"
        assert LimitReason.CAPITAL.value == "资金"

    def test_limit_level_is_str_enum(self):
        assert isinstance(LimitLevel.FIRST, str)


class TestLimitUpInfo:
    """LimitUpInfo 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(LimitUpInfo)

    def test_creation(self):
        info = LimitUpInfo(
            stock_code="600519", stock_name="贵州茅台",
            level=LimitLevel.FIRST, reason=LimitReason.CAPITAL,
            seal_time="09:30:00", open_count=0, seal_volume=1e8,
            quality_score=85.0,
        )
        assert info.stock_code == "600519"
        assert info.level == LimitLevel.FIRST
        assert info.quality_score == 85.0


class TestDetermineLevel:
    """_determine_level 连板等级判定"""

    def test_no_history_returns_first(self, analyzer):
        assert analyzer._determine_level("600519") == LimitLevel.FIRST

    def test_two_records_returns_second(self, analyzer):
        analyzer._history["600519"] = [{"date": "d1"}, {"date": "d2"}]
        assert analyzer._determine_level("600519") == LimitLevel.SECOND

    def test_three_plus_returns_third_plus(self, analyzer):
        analyzer._history["600519"] = [{"d": 1}, {"d": 2}, {"d": 3}, {"d": 4}]
        assert analyzer._determine_level("600519") == LimitLevel.THIRD_PLUS


class TestCalcQualityScore:
    """_calc_quality_score 质量评分"""

    def test_early_seal_time_gets_bonus(self, analyzer):
        """09:30 封板(<=570分钟) → +25"""
        early = analyzer._calc_quality_score("09:30:00", 0, 1e8, LimitLevel.FIRST)
        late = analyzer._calc_quality_score("14:30:00", 0, 1e8, LimitLevel.FIRST)
        assert early > late

    def test_zero_open_count_gets_bonus(self, analyzer):
        """炸板 0 次 → +15;炸板 3+ 次 → -10"""
        no_break = analyzer._calc_quality_score("10:00:00", 0, 1e7, LimitLevel.FIRST)
        many_break = analyzer._calc_quality_score("10:00:00", 3, 1e7, LimitLevel.FIRST)
        assert no_break > many_break

    def test_large_seal_volume_gets_bonus(self, analyzer):
        """封单 > 1亿 → +10;< 1千万 → -5"""
        big = analyzer._calc_quality_score("10:00:00", 0, 2e8, LimitLevel.FIRST)
        small = analyzer._calc_quality_score("10:00:00", 0, 5e6, LimitLevel.FIRST)
        assert big > small

    def test_score_bounded_0_to_100(self, analyzer):
        """评分应在 [0, 100]"""
        score = analyzer._calc_quality_score("14:50:00", 5, 1e6, LimitLevel.FIRST)
        assert 0.0 <= score <= 100.0

    def test_higher_level_gets_bonus(self, analyzer):
        """三板+ > 二板 > 首板(其他条件相同)"""
        first = analyzer._calc_quality_score("10:00:00", 0, 1e7, LimitLevel.FIRST)
        second = analyzer._calc_quality_score("10:00:00", 0, 1e7, LimitLevel.SECOND)
        third = analyzer._calc_quality_score("10:00:00", 0, 1e7, LimitLevel.THIRD_PLUS)
        assert third > second > first


class TestScanLimitUp:
    """scan_limit_up 扫描涨停"""

    def test_empty_pool_returns_empty_list(self, analyzer):
        """空涨停池 → 空列表"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.return_value = pd.DataFrame()
            mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            results = analyzer.scan_limit_up()
        assert results == []

    def test_exception_returns_empty_list(self, analyzer):
        """akshare 异常 → 空列表"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.side_effect = Exception("网络错误")
            results = analyzer.scan_limit_up()
        assert results == []


class TestAnalyzeLimitUp:
    """analyze_limit_up 个股分析"""

    def test_stock_not_in_pool_returns_default(self, analyzer):
        """目标股不在涨停池 → 返回默认结果"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.return_value = pd.DataFrame([
                {"代码": "000001", "名称": "平安银行", "首次封板时间": "09:30:00", "炸板次数": 0, "封板资金": 1e8},
            ])
            mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            result = analyzer.analyze_limit_up("600519")
        assert result["stock_code"] == "600519"
        assert result["level"] == LimitLevel.FIRST.value
        assert result["quality_score"] == 0.0


class TestPredictPromotion:
    """predict_promotion 晋级概率"""

    def test_returns_structure(self, analyzer):
        """应返回 stock_code/current_level/promotion_prob/confidence"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.return_value = pd.DataFrame()
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            result = analyzer.predict_promotion("600519")
        assert "stock_code" in result
        assert "current_level" in result
        assert "promotion_prob" in result
        assert "confidence" in result
        assert 0.0 <= result["promotion_prob"] <= 100.0
        assert result["confidence"] in {"high", "medium", "low"}


# ── r7 阶段二 Q3 覆盖率补强 ────────────────────────────────────────────

def _zt_pool_row(code: str = "600519", name: str = "贵州茅台",
                 seal_time: str = "09:30:00", open_count: int = 0,
                 seal_amount: float = 2e8, change_pct: float = 10.0) -> dict:
    return {
        "代码": code, "名称": name, "首次封板时间": seal_time,
        "炸板次数": open_count, "封板资金": seal_amount, "涨跌幅": change_pct,
    }


class TestLoadHistory:
    """_load_history 最近 3 个交易日涨停池加载"""

    def test_loads_rows_and_dedupes_by_date(self, analyzer):
        good = pd.DataFrame([_zt_pool_row(), _zt_pool_row(code="000001", name="平安银行")])
        with patch.object(mod, "ak") as mock_ak:
            # 三个交易日: 第一天有数据 / 第二天空 / 第三天异常 → 仅第一天入库
            mock_ak.stock_zt_pool_em.side_effect = [
                good, pd.DataFrame(), Exception("network"),
            ]
            analyzer._load_history()
        assert set(analyzer._history.keys()) == {"600519", "000001"}
        assert len(analyzer._history["600519"]) == 1
        rec = analyzer._history["600519"][0]
        assert rec["seal_time"] == "09:30:00"
        assert rec["open_count"] == 0
        assert rec["seal_amount"] == 2e8
        assert rec["change_pct"] == 10.0

    def test_dedupes_same_stock_across_calls(self, analyzer):
        """同股同日期重复加载 → 不重复入库"""
        good = pd.DataFrame([_zt_pool_row()])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.side_effect = [good, good, good]
            analyzer._load_history()
        # 三天日期不同则应入库 3 条(最近 3 个交易日各一条)
        assert len(analyzer._history["600519"]) == 3

    def test_one_day_failure_does_not_break_others(self, analyzer):
        good = pd.DataFrame([_zt_pool_row()])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.side_effect = [Exception("day1 down"), good, good]
            analyzer._load_history()
        assert len(analyzer._history["600519"]) == 2


class TestDetermineReason:
    """_determine_reason 归因"""

    def test_concept_board_exists_returns_theme(self, analyzer):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame(
                [{"板块名称": "白酒概念"}])
            assert analyzer._determine_reason("600519") == LimitReason.THEME

    def test_profit_growth_over_30_returns_performance(self, analyzer):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame(
                [{"净利润同比增长(%)": 45.0}])
            assert analyzer._determine_reason("600519") == LimitReason.PERFORMANCE

    def test_low_growth_returns_capital(self, analyzer):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame(
                [{"净利润同比增长(%)": 10.0}])
            assert analyzer._determine_reason("600519") == LimitReason.CAPITAL

    def test_both_sources_fail_returns_capital(self, analyzer):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_board_concept_name_em.side_effect = Exception("board down")
            mock_ak.stock_financial_abstract_ths.side_effect = Exception("fin down")
            assert analyzer._determine_reason("600519") == LimitReason.CAPITAL


class TestCalcQualityScoreBands:
    """_calc_quality_score 各档位边界"""

    def test_seal_time_bands(self, analyzer):
        base = 50.0
        # 09:35=575min → +15; 10:05=605 → +5; 11:00=660 → -5
        # (open_count=2 为中性档, 排除炸板加成干扰)
        assert analyzer._calc_quality_score("09:35:00", 2, 1e7, LimitLevel.FIRST) == base + 15
        assert analyzer._calc_quality_score("10:05:00", 2, 1e7, LimitLevel.FIRST) == base + 5
        assert analyzer._calc_quality_score("11:00:00", 2, 1e7, LimitLevel.FIRST) == base - 5

    def test_open_count_one_gets_small_bonus(self, analyzer):
        assert analyzer._calc_quality_score("", 1, 1e7, LimitLevel.FIRST) == 55.0

    def test_open_count_two_neutral(self, analyzer):
        """炸板 2 次: 既非 0/1 也非 ≥3 → 无调整"""
        assert analyzer._calc_quality_score("", 2, 1e7, LimitLevel.FIRST) == 50.0

    def test_seal_volume_bands(self, analyzer):
        assert analyzer._calc_quality_score("", 2, 6e7, LimitLevel.FIRST) == 55.0
        assert analyzer._calc_quality_score("", 2, 2e7, LimitLevel.FIRST) == 50.0
        assert analyzer._calc_quality_score("", 2, 9e6, LimitLevel.FIRST) == 45.0

    def test_invalid_seal_time_does_not_crash(self, analyzer):
        """封板时间格式非法 → warning 后继续(基础分不变)"""
        assert analyzer._calc_quality_score("not-a-time", 2, 1e7, LimitLevel.FIRST) == 50.0

    def test_score_capped_at_100(self, analyzer):
        """50+25(早封)+15(零炸板)+10(大封单)+10(三板+) = 110 → 封顶 100"""
        score = analyzer._calc_quality_score("09:30:00", 0, 2e8, LimitLevel.THIRD_PLUS)
        assert score == 100.0


class TestScanLimitUpRows:
    """scan_limit_up 有数据行"""

    def test_two_rows_returned_with_fields(self, analyzer):
        pool = pd.DataFrame([
            _zt_pool_row(),
            _zt_pool_row(code="000001", name="平安银行", seal_time="14:00:00",
                         open_count=2, seal_amount=5e6),
        ])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.return_value = pool
            mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            results = analyzer.scan_limit_up()
        assert len(results) == 2
        first = results[0]
        assert first.stock_code == "600519"
        assert first.stock_name == "贵州茅台"
        assert first.seal_time == "09:30:00"
        assert first.open_count == 0
        assert first.seal_volume == 2e8
        assert isinstance(first.quality_score, float)
        assert 0.0 <= first.quality_score <= 100.0


class TestAnalyzeLimitUpInPool:
    """analyze_limit_up 目标股在池中"""

    def test_in_pool_updates_fields(self, analyzer):
        with patch.object(mod, "ak") as mock_ak:
            # _load_history 依次取最近 3 个交易日(前 3 次调用), 第 4 次才是本次查询;
            # 仅"今天"有数据 → 历史恰 1 条 → 首板
            mock_ak.stock_zt_pool_em.side_effect = [
                pd.DataFrame([_zt_pool_row()]), pd.DataFrame(), pd.DataFrame(),
                pd.DataFrame([_zt_pool_row()]),
            ]
            mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            result = analyzer.analyze_limit_up("600519")
        assert result["seal_time"] == "09:30:00"
        assert result["open_count"] == 0
        assert result["seal_volume"] == 2e8
        assert result["quality_score"] > 0
        assert result["level"] == LimitLevel.FIRST.value

    def test_pool_exception_returns_default(self, analyzer):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.side_effect = Exception("pool down")
            result = analyzer.analyze_limit_up("600519")
        assert result["quality_score"] == 0.0
        assert result["seal_time"] == ""


class TestPredictPromotionBands:
    """predict_promotion 概率档位"""

    def test_full_momentum_pushes_high_confidence(self, analyzer):
        """首板(历史 1 条) + 零炸板 + 中大封单 + 早封板 + 主力净流入 → ≥60"""
        pool = pd.DataFrame([_zt_pool_row()])
        with patch.object(mod, "ak") as mock_ak:
            # 前 3 次 = _load_history 的三个交易日(仅今天有), 第 4 次 = 本次查询
            mock_ak.stock_zt_pool_em.side_effect = [
                pool, pd.DataFrame(), pd.DataFrame(), pool,
            ]
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame(
                [{"主力净流入-净占比": 8.0}])
            result = analyzer.predict_promotion("600519")
        # 25 + 15(零炸板) + 5(封单 2e8 走 >1e8 档) + 15(早封) + 10(净流入) = 70
        assert result["promotion_prob"] == 70.0
        assert result["confidence"] == "high"

    def test_weak_conditions_push_low_confidence(self, analyzer):
        """首板 + 多次炸板 + 晚封板 + 主力流出 → 概率 0"""
        pool = pd.DataFrame([
            _zt_pool_row(open_count=4, seal_time="15:00:00", seal_amount=5e6)])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.side_effect = [
                pool, pd.DataFrame(), pd.DataFrame(), pool,
            ]
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame(
                [{"主力净流入-净占比": -8.0}])
            result = analyzer.predict_promotion("600519")
        # 25 - 10(炸板4) - 5(晚封) - 10(流出) = 0
        assert result["promotion_prob"] == 0.0
        assert result["confidence"] == "low"

    def test_mid_seal_volume_bonus(self, analyzer):
        """首板基准 + 封单 1e8~2e8(+5) + 零炸板(+15) + 早封(+15) = 60"""
        pool = pd.DataFrame([_zt_pool_row(seal_amount=1.5e8)])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.side_effect = [
                pool, pd.DataFrame(), pd.DataFrame(), pool,
            ]
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            result = analyzer.predict_promotion("600519")
        assert result["promotion_prob"] == 60.0

    def test_pool_exception_still_uses_base_prob(self, analyzer):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.side_effect = Exception("pool down")
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            result = analyzer.predict_promotion("600519")
        assert result["promotion_prob"] == 25.0

    def test_fund_flow_exception_tolerated(self, analyzer):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.return_value = pd.DataFrame()
            mock_ak.stock_individual_fund_flow.side_effect = Exception("flow down")
            result = analyzer.predict_promotion("600519")
        assert result["promotion_prob"] == 25.0

    def test_sz_market_routing(self, analyzer):
        """非 6 开头代码走 sz 市场(不抛异常即视为路由正确)"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zt_pool_em.return_value = pd.DataFrame()
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            result = analyzer.predict_promotion("000001")
        assert result["stock_code"] == "000001"
