import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.strategies.trading_quant import TradingQuant


def main() -> None:
    logger = get_logger("scan_new_high")
    config = get_config()

    parser = argparse.ArgumentParser(description="创业板新高策略扫描")
    parser.add_argument("--days", type=int, default=20, help="新高周期天数")
    parser.add_argument("--min-score", type=float, default=65.0, help="最低综合评分")
    args = parser.parse_args()

    quant = TradingQuant(config)

    logger.info(f"开始扫描创 {args.days} 日新高的创业板股票...")

    cyb_stocks = quant.scan_cyb_new_high(days=args.days)
    logger.info(f"创 {args.days} 日新高的创业板股票: {len(cyb_stocks)} 只")

    qualified: list[dict] = []
    for stock in cyb_stocks:
        code = stock.get("code", "")
        try:
            analysis = quant.stock_analysis(code)
            score = analysis.get("score", 0)
            stock["score"] = score
            if score >= args.min_score:
                qualified.append(stock)
                logger.info(
                    f"  ✓ {code} {stock.get('name', '')} "
                    f"评分: {score:.1f} 新高价: {stock.get('high', 0)}"
                )
            else:
                logger.debug(
                    f"  ✗ {code} {stock.get('name', '')} "
                    f"评分: {score:.1f} (低于 {args.min_score})"
                )
        except Exception as e:
            logger.warning(f"  {code} 分析失败: {e}")

    logger.info(
        f"扫描完成, 创新高 {len(cyb_stocks)} 只, "
        f"评分>={args.min_score} 的 {len(qualified)} 只"
    )


if __name__ == "__main__":
    main()
