"""数据质量校验器测试"""
import pytest
from src.core.data_validator import DataValidator, QualityLevel, QualityIssue, ValidationResult


class TestDataValidator:
    """DataValidator 测试套件"""

    def setup_method(self):
        self.validator = DataValidator()

    # ===== 行情数据校验 =====
    def test_valid_quote(self, sample_quote):
        """测试有效行情数据"""
        result = self.validator.validate_quote(sample_quote)
        assert result.is_valid
        assert result.quality_score >= 80

    def test_missing_required_fields(self):
        """测试缺少必填字段"""
        result = self.validator.validate_quote({"code": "600519"})
        assert not result.is_valid
        assert any("name" in c for c in result.criticals)
        assert any("price" in c for c in result.criticals)

    def test_price_out_of_range(self):
        """测试价格超出合理范围"""
        result = self.validator.validate_quote({"code": "600519", "name": "测试", "price": -1})
        assert len(result.warnings) > 0

    def test_change_pct_consistency(self):
        """测试涨跌幅与价格一致性"""
        result = self.validator.validate_quote({
            "code": "600519", "name": "测试", "price": 100,
            "prev_close": 90, "change_pct": 5.0,  # 实际应为11.11%
        })
        assert any("不一致" in w for w in result.warnings)

    def test_change_pct_consistent(self):
        """测试涨跌幅与价格一致"""
        result = self.validator.validate_quote({
            "code": "600519", "name": "测试", "price": 100,
            "prev_close": 99, "change_pct": 1.01,
        })
        # 不应有不一致的警告
        assert not any("不一致" in w for w in result.warnings)

    # ===== K线数据校验 =====
    def test_valid_kline(self, sample_kline):
        """测试有效K线数据"""
        result = self.validator.validate_kline(sample_kline)
        assert result.is_valid
        assert result.quality_score >= 70

    def test_empty_kline(self):
        """测试空K线数据"""
        result = self.validator.validate_kline([])
        assert not result.is_valid

    def test_kline_high_low_inverted(self):
        """测试最高价低于最低价"""
        bad_kline = [{"date": "2026-06-01", "open": 100, "close": 105, "high": 95, "low": 110, "volume": 1000}]
        result = self.validator.validate_kline(bad_kline)
        assert not result.is_valid

    def test_kline_missing_fields(self):
        """测试K线缺少字段"""
        bad_kline = [{"date": "2026-06-01", "open": 100}]  # 缺少 close, high, low
        result = self.validator.validate_kline(bad_kline)
        assert len(result.issues) > 0

    # ===== 资金流向校验 =====
    def test_valid_capital_flow(self, sample_capital_flow):
        """测试有效资金流向数据"""
        result = self.validator.validate_capital_flow(sample_capital_flow)
        assert result.is_valid

    def test_capital_flow_inconsistency(self):
        """测试资金流向不一致"""
        result = self.validator.validate_capital_flow({
            "main_inflow": 100,
            "main_outflow": 50,
            "main_net_inflow": 80,  # 应为50，偏差超10%
        })
        assert any("偏差" in w for w in result.warnings)

    # ===== 财务数据校验 =====
    def test_valid_financial(self, sample_financial):
        """测试有效财务数据"""
        result = self.validator.validate_financial(sample_financial)
        assert result.is_valid

    def test_empty_financial(self):
        """测试空财务数据"""
        result = self.validator.validate_financial({})
        assert result.is_valid  # 空财务数据仅INFO级别，不导致invalid

    # ===== 综合分析数据校验 =====
    def test_full_analysis_data(self, sample_quote, sample_kline, sample_capital_flow, sample_financial):
        """测试完整分析数据"""
        data = {
            "quote": sample_quote,
            "kline_daily": sample_kline,
            "capital_flow": sample_capital_flow,
            "financial": sample_financial,
        }
        result = self.validator.validate_analysis_data(data)
        assert result.is_valid
        assert result.quality_score >= 60

    def test_minimal_analysis_data(self):
        """测试最小分析数据（仅有行情）"""
        data = {"quote": {"code": "600519", "name": "测试", "price": 100}}
        result = self.validator.validate_analysis_data(data)
        # 数据维度不足应有警告
        assert len(result.warnings) > 0 or len(result.issues) > 0

    def test_no_data(self):
        """测试无数据"""
        result = self.validator.validate_analysis_data({})
        assert not result.is_valid
        assert result.quality_score < 50


class TestQualityIssue:
    """QualityIssue 测试"""

    def test_issue_creation(self):
        issue = QualityIssue(
            field_name="price",
            level=QualityLevel.WARNING,
            message="价格异常",
            suggestion="检查数据源",
        )
        assert issue.field_name == "price"
        assert issue.level == QualityLevel.WARNING

    def test_validation_result_scoring(self):
        result = ValidationResult()
        initial_score = result.quality_score

        result.add_issue(QualityIssue(field_name="test", level=QualityLevel.INFO, message="info"))
        assert result.quality_score < initial_score
        assert result.is_valid  # INFO不影响有效性

        result.add_issue(QualityIssue(field_name="test", level=QualityLevel.CRITICAL, message="critical"))
        assert not result.is_valid
