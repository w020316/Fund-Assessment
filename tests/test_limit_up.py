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
