import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.strategies.trading_quant import TradingQuant
from src.core.data_source import DataSourceManager


def main() -> None:
    logger = get_logger("prepare_watchlist")
    config = get_config()

    parser = argparse.ArgumentParser(description="准备明日观察池")
    parser.add_argument("--min-score", type=float, default=50.0, help="最低综合评分")
    parser.add_argument("--date", type=str, default=None, help="指定日期 YYYYMMDD")
    args = parser.parse_args()

    today = args.date or datetime.now().strftime("%Y%m%d")
    logger.info(f"开始准备明日观察池 (基于 {today} 行情)...")

    quant = TradingQuant(config)
    ds_manager = DataSourceManager(config)

    candidates: list[dict] = []

    logger.info("扫描市场异动标的...")
    try:
        anomaly_results = quant.market_anomaly()
        for item in anomaly_results:
            item["_source"] = "market_anomaly"
            candidates.append(item)
        logger.info(f"市场异动标的: {len(anomaly_results)} 只")
    except Exception as e:
        logger.warning(f"市场异动扫描失败: {e}")

    logger.info("扫描涨停板标的...")
    try:
        from src.strategies.limit_up import LimitUpAnalyzer
        limit_up_analyzer = LimitUpAnalyzer(config)
        limit_up_results = limit_up_analyzer.scan_limit_up()
        for item in limit_up_results:
            item["_source"] = "limit_up"
            candidates.append(item)
        logger.info(f"涨停板标的: {len(limit_up_results)} 只")
    except Exception as e:
        logger.warning(f"涨停板扫描失败: {e}")

    seen_codes: set[str] = set()
    unique_candidates: list[dict] = []
    for item in candidates:
        code = item.get("code", "")
        if code and code not in seen_codes:
            seen_codes.add(code)
            unique_candidates.append(item)

    logger.info(f"去重后候选标的: {len(unique_candidates)} 只")

    qualified: list[dict] = []
    for item in unique_candidates:
        code = item.get("code", "")
        try:
            analysis = quant.stock_analysis(code)
            score = analysis.get("score", 0)
            item["score"] = score
            item["analysis"] = analysis
            if score >= args.min_score:
                qualified.append(item)
                logger.info(
                    f"  ✓ {code} {item.get('name', '')} "
                    f"评分: {score:.1f} 来源: {item.get('_source', '')}"
                )
        except Exception as e:
            logger.debug(f"  {code} 分析失败: {e}")

    logger.info(f"综合评分>={args.min_score} 的标的: {len(qualified)} 只")

    if qualified:
        try:
            ds_manager.save_watchlist(qualified, date=today)
            logger.info(f"已保存 {len(qualified)} 只股票到明日观察池")
        except Exception as e:
            logger.error(f"保存观察池失败: {e}")
    else:
        logger.info("无符合条件的标的, 观察池为空")

    logger.info("明日观察池准备完成")


if __name__ == "__main__":
    main()
