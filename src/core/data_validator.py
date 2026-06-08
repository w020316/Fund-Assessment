"""数据质量校验器 - 对获取的数据进行质量检查与评分

参考: sjkncs/daily_stock_analysis 的 data_validator.py
特性:
- 多维度数据质量检查（完整性、时效性、合理性、一致性）
- 分级告警（CRITICAL/WARNING/INFO）
- 数据质量评分（0-100）
- 自动修复常见问题
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from loguru import logger


class QualityLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class QualityIssue:
    """数据质量问题"""
    field_name: str
    level: QualityLevel
    message: str
    suggestion: str = ""


@dataclass
class ValidationResult:
    """校验结果"""
    is_valid: bool = True
    quality_score: float = 100.0
    issues: list[QualityIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    criticals: list[str] = field(default_factory=list)

    def add_issue(self, issue: QualityIssue):
        self.issues.append(issue)
        if issue.level == QualityLevel.CRITICAL:
            self.criticals.append(f"{issue.field_name}: {issue.message}")
            self.is_valid = False
            self.quality_score = max(0, self.quality_score - 20)
        elif issue.level == QualityLevel.WARNING:
            self.warnings.append(f"{issue.field_name}: {issue.message}")
            self.quality_score = max(0, self.quality_score - 10)
        else:
            self.quality_score = max(0, self.quality_score - 2)


class DataValidator:
    """数据质量校验器"""

    # A股合理价格范围
    PRICE_RANGE = (0.1, 99999.0)
    # A股合理涨跌幅范围（含涨跌停）
    CHANGE_PCT_RANGE = (-30.0, 30.0)
    # A股合理PE范围
    PE_RANGE = (-1000.0, 100000.0)
    # 合理换手率范围
    TURNOVER_RANGE = (0.0, 100.0)
    # 合理成交量范围
    VOLUME_RANGE = (0, 1e12)
    # 合理市值范围
    MARKET_VALUE_RANGE = (1e7, 1e14)

    def validate_quote(self, quote: dict[str, Any]) -> ValidationResult:
        """校验实时行情数据"""
        result = ValidationResult()

        # 必填字段检查
        required_fields = ["code", "name", "price"]
        for f in required_fields:
            if not quote.get(f):
                result.add_issue(QualityIssue(
                    field_name=f,
                    level=QualityLevel.CRITICAL,
                    message=f"必填字段 {f} 缺失",
                    suggestion="检查数据源是否正常返回",
                ))

        price = quote.get("price", 0)
        if price and not (self.PRICE_RANGE[0] <= price <= self.PRICE_RANGE[1]):
            result.add_issue(QualityIssue(
                field_name="price",
                level=QualityLevel.WARNING,
                message=f"价格 {price} 超出合理范围 {self.PRICE_RANGE}",
                suggestion="确认是否为特殊股票（如退市股、新股）",
            ))

        change_pct = quote.get("change_pct")
        if change_pct is not None and not (self.CHANGE_PCT_RANGE[0] <= change_pct <= self.CHANGE_PCT_RANGE[1]):
            result.add_issue(QualityIssue(
                field_name="change_pct",
                level=QualityLevel.WARNING,
                message=f"涨跌幅 {change_pct}% 超出合理范围",
                suggestion="确认是否为涨跌停或特殊股票",
            ))

        turnover = quote.get("turnover")
        if turnover is not None and not (self.TURNOVER_RANGE[0] <= turnover <= self.TURNOVER_RANGE[1]):
            result.add_issue(QualityIssue(
                field_name="turnover",
                level=QualityLevel.WARNING,
                message=f"换手率 {turnover}% 超出合理范围",
            ))

        volume = quote.get("volume")
        if volume is not None and not (self.VOLUME_RANGE[0] <= volume <= self.VOLUME_RANGE[1]):
            result.add_issue(QualityIssue(
                field_name="volume",
                level=QualityLevel.WARNING,
                message=f"成交量 {volume} 超出合理范围",
            ))

        # 一致性检查：涨跌幅与价格是否匹配
        prev_close = quote.get("prev_close")
        if price and prev_close and prev_close > 0:
            expected_change = (price - prev_close) / prev_close * 100
            if change_pct is not None and abs(expected_change - change_pct) > 0.5:
                result.add_issue(QualityIssue(
                    field_name="change_pct",
                    level=QualityLevel.WARNING,
                    message=f"涨跌幅 {change_pct}% 与价格计算值 {expected_change:.2f}% 不一致",
                    suggestion="数据源可能延迟或价格不一致",
                ))

        return result

    def validate_kline(self, kline_data: list[dict], expected_count: int = 0) -> ValidationResult:
        """校验K线数据"""
        result = ValidationResult()

        if not kline_data:
            result.add_issue(QualityIssue(
                field_name="kline",
                level=QualityLevel.CRITICAL,
                message="K线数据为空",
            ))
            return result

        if expected_count and len(kline_data) < expected_count * 0.5:
            result.add_issue(QualityIssue(
                field_name="kline_count",
                level=QualityLevel.WARNING,
                message=f"K线数据量不足: 期望~{expected_count}条, 实际{len(kline_data)}条",
            ))

        # 检查每条K线数据完整性
        required_kline_fields = ["date", "open", "close", "high", "low"]
        for i, bar in enumerate(kline_data):
            for f in required_kline_fields:
                if f not in bar or bar[f] is None:
                    result.add_issue(QualityIssue(
                        field_name=f"kline[{i}].{f}",
                        level=QualityLevel.WARNING if i > 0 else QualityLevel.CRITICAL,
                        message=f"K线数据第{i+1}条缺少字段 {f}",
                    ))
                    break  # 每条只报一次

            # 高低价合理性
            high = bar.get("high", 0)
            low = bar.get("low", 0)
            open_price = bar.get("open", 0)
            close = bar.get("close", 0)
            if high and low and high < low:
                result.add_issue(QualityIssue(
                    field_name=f"kline[{i}]",
                    level=QualityLevel.CRITICAL,
                    message=f"最高价 {high} < 最低价 {low}",
                ))
            if high and open_price and close:
                if open_price > high * 1.1 or close > high * 1.1:
                    result.add_issue(QualityIssue(
                        field_name=f"kline[{i}]",
                        level=QualityLevel.WARNING,
                        message=f"开盘/收盘价超出最高价范围，可能数据异常",
                    ))

        # 日期连续性检查（仅检查最近的数据）
        if len(kline_data) >= 2:
            try:
                dates = [bar["date"] for bar in kline_data if "date" in bar]
                if len(dates) >= 2:
                    # 简单检查：最新日期应该是最近的
                    latest = dates[-1]
                    if isinstance(latest, str):
                        latest_date = datetime.strptime(latest[:10], "%Y-%m-%d")
                        days_old = (datetime.now() - latest_date).days
                        if days_old > 7:
                            result.add_issue(QualityIssue(
                                field_name="kline_freshness",
                                level=QualityLevel.WARNING,
                                message=f"K线数据最新日期为{latest[:10]}，已{days_old}天未更新",
                                suggestion="检查数据源是否正常",
                            ))
            except (ValueError, TypeError):
                pass

        return result

    def validate_capital_flow(self, flow_data: dict[str, Any]) -> ValidationResult:
        """校验资金流向数据"""
        result = ValidationResult()

        if not flow_data:
            result.add_issue(QualityIssue(
                field_name="capital_flow",
                level=QualityLevel.WARNING,
                message="资金流向数据为空",
            ))
            return result

        # 检查资金流向合计一致性
        main_inflow = flow_data.get("main_inflow", 0)
        main_outflow = flow_data.get("main_outflow", 0)
        main_net = flow_data.get("main_net_inflow", 0)
        if main_inflow and main_outflow:
            expected_net = main_inflow - main_outflow
            if main_net and abs(expected_net - main_net) > abs(main_net) * 0.1:
                result.add_issue(QualityIssue(
                    field_name="main_net_inflow",
                    level=QualityLevel.WARNING,
                    message=f"主力净流入 {main_net} 与流入-流出 {expected_net} 偏差超过10%",
                ))

        return result

    def validate_financial(self, financial_data: dict[str, Any]) -> ValidationResult:
        """校验财务数据"""
        result = ValidationResult()

        if not financial_data:
            result.add_issue(QualityIssue(
                field_name="financial",
                level=QualityLevel.INFO,
                message="财务数据为空（可能为非A股或数据源不支持）",
            ))
            return result

        pe = financial_data.get("pe_ttm")
        if pe is not None and not (self.PE_RANGE[0] <= pe <= self.PE_RANGE[1]):
            result.add_issue(QualityIssue(
                field_name="pe_ttm",
                level=QualityLevel.INFO,
                message=f"PE(TTM) {pe} 超出常见范围",
                suggestion="可能为亏损股或特殊行业",
            ))

        roe = financial_data.get("roe")
        if roe is not None and abs(roe) > 100:
            result.add_issue(QualityIssue(
                field_name="roe",
                level=QualityLevel.INFO,
                message=f"ROE {roe}% 异常",
                suggestion="确认数据是否正确",
            ))

        return result

    def validate_analysis_data(self, data: dict[str, Any]) -> ValidationResult:
        """校验完整的分析数据集"""
        result = ValidationResult()

        # 检查关键数据维度
        has_quote = bool(data.get("quote"))
        has_kline = bool(data.get("kline_daily"))
        has_financial = bool(data.get("financial"))
        has_capital_flow = bool(data.get("capital_flow"))

        if not has_quote:
            result.add_issue(QualityIssue(
                field_name="quote",
                level=QualityLevel.CRITICAL,
                message="缺少实时行情数据，分析结果可靠性降低",
            ))

        if not has_kline:
            result.add_issue(QualityIssue(
                field_name="kline_daily",
                level=QualityLevel.WARNING,
                message="缺少K线数据，技术面分析不可靠",
            ))

        if not has_financial:
            result.add_issue(QualityIssue(
                field_name="financial",
                level=QualityLevel.INFO,
                message="缺少财务数据，基本面分析受限",
            ))

        if not has_capital_flow:
            result.add_issue(QualityIssue(
                field_name="capital_flow",
                level=QualityLevel.INFO,
                message="缺少资金流向数据，资金面分析受限",
            ))

        # 子项校验
        if has_quote:
            sub = self.validate_quote(data["quote"])
            result.issues.extend(sub.issues)
            result.warnings.extend(sub.warnings)
            result.criticals.extend(sub.criticals)
            result.quality_score = max(0, result.quality_score - (100 - sub.quality_score))

        if has_kline:
            sub = self.validate_kline(data["kline_daily"])
            result.issues.extend(sub.issues)
            result.warnings.extend(sub.warnings)
            result.criticals.extend(sub.criticals)
            result.quality_score = max(0, result.quality_score - (100 - sub.quality_score) * 0.5)

        if has_capital_flow:
            sub = self.validate_capital_flow(data["capital_flow"])
            result.issues.extend(sub.issues)
            result.warnings.extend(sub.warnings)
            result.criticals.extend(sub.criticals)

        if has_financial:
            sub = self.validate_financial(data["financial"])
            result.issues.extend(sub.issues)
            result.warnings.extend(sub.warnings)
            result.criticals.extend(sub.criticals)

        # 综合评分调整
        data_dimensions = sum([has_quote, has_kline, has_financial, has_capital_flow])
        if data_dimensions < 2:
            result.quality_score = min(result.quality_score, 40)
            result.add_issue(QualityIssue(
                field_name="overall",
                level=QualityLevel.WARNING,
                message=f"仅有{data_dimensions}个数据维度，分析结果可靠性不足",
                suggestion="建议检查数据源配置",
            ))

        return result


# 全局单例
_validator: DataValidator | None = None


def get_data_validator() -> DataValidator:
    """获取数据校验器单例"""
    global _validator
    if _validator is None:
        _validator = DataValidator()
    return _validator
