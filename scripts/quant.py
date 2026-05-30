import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.strategies.trading_quant import TradingQuant


def main() -> None:
    logger = get_logger("quant")
    config = get_config()

    parser = argparse.ArgumentParser(description="量化分析入口")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    stock_analysis_parser = subparsers.add_parser("stock_analysis", help="股票分析")
    stock_analysis_parser.add_argument("code", type=str, help="股票代码")

    capital_flow_parser = subparsers.add_parser("capital_flow", help="资金流向")
    capital_flow_parser.add_argument("code", type=str, help="股票代码")

    subparsers.add_parser("northbound_flow", help="北向资金流向")

    subparsers.add_parser("market_anomaly", help="市场异动检测")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    quant = TradingQuant(config)

    match args.command:
        case "stock_analysis":
            logger.info(f"开始分析股票: {args.code}")
            result = quant.stock_analysis(args.code)
            logger.info(f"分析结果: {result}")
        case "capital_flow":
            logger.info(f"开始分析资金流向: {args.code}")
            result = quant.capital_flow(args.code)
            logger.info(f"资金流向结果: {result}")
        case "northbound_flow":
            logger.info("开始分析北向资金流向")
            result = quant.northbound_flow()
            logger.info(f"北向资金流向结果: {result}")
        case "market_anomaly":
            logger.info("开始检测市场异动")
            result = quant.market_anomaly()
            logger.info(f"市场异动结果: {result}")
        case _:
            parser.print_help()


if __name__ == "__main__":
    main()
