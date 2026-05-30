import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.strategies.trading_quant import TradingQuant
from src.strategies.limit_up import LimitUpAnalyzer


def main() -> None:
    logger = get_logger("scan_pre_limit_up")
    config = get_config()

    parser = argparse.ArgumentParser(description="涨停预警扫描")
    parser.add_argument("--threshold", type=float, default=7.0, help="涨幅预警阈值(%)")
    args = parser.parse_args()

    quant = TradingQuant(config)
    analyzer = LimitUpAnalyzer(config)

    logger.info("开始盘前涨停预警扫描...")

    anomaly_results = quant.market_anomaly()
    logger.info(f"市场异动检测完成, 发现 {len(anomaly_results)} 个异动标的")

    pre_limit_stocks: list[dict] = []
    for item in anomaly_results:
        code = item.get("code", "")
        change_pct = item.get("change_pct", 0)
        if change_pct >= args.threshold:
            pre_limit_stocks.append(item)

    logger.info(f"涨幅超过 {args.threshold}% 的股票: {len(pre_limit_stocks)} 只")

    for stock in pre_limit_stocks:
        code = stock.get("code", "")
        try:
            prediction = analyzer.predict_promotion(code)
            stock["promotion_prediction"] = prediction
            logger.info(
                f"  {code} {stock.get('name', '')} "
                f"涨幅: {stock.get('change_pct', 0):.2f}% "
                f"晋级预测: {prediction}"
            )
        except Exception as e:
            logger.warning(f"  {code} 晋级预测失败: {e}")

    logger.info(f"涨停预警扫描完成, 共 {len(pre_limit_stocks)} 只预警股票")


if __name__ == "__main__":
    main()
