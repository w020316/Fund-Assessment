import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.strategies.limit_up import LimitUpAnalyzer


def main() -> None:
    logger = get_logger("predict_promotion")
    config = get_config()

    parser = argparse.ArgumentParser(description="涨停板类型预测")
    parser.add_argument("code", nargs="?", default=None, help="股票代码(可选, 不传则扫描所有涨停股)")
    args = parser.parse_args()

    analyzer = LimitUpAnalyzer(config)

    if args.code:
        logger.info(f"开始预测 {args.code} 的涨停板类型...")
        try:
            result = analyzer.predict_promotion(args.code)
            logger.info(f"预测结果: {result}")
        except Exception as e:
            logger.error(f"预测失败: {e}")
    else:
        logger.info("开始扫描所有涨停股并预测板型...")
        try:
            limit_up_stocks = analyzer.scan_limit_up()
            logger.info(f"当前涨停股数量: {len(limit_up_stocks)}")

            for stock in limit_up_stocks:
                code = stock.get("code", "")
                try:
                    prediction = analyzer.predict_promotion(code)
                    logger.info(
                        f"  {code} {stock.get('name', '')} "
                        f"涨停类型: {stock.get('limit_type', '')} "
                        f"晋级预测: {prediction}"
                    )
                except Exception as e:
                    logger.warning(f"  {code} 预测失败: {e}")
        except Exception as e:
            logger.error(f"扫描涨停股失败: {e}")

    logger.info("涨停板类型预测完成")


if __name__ == "__main__":
    main()
