import sys
import os
import argparse
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.strategies.limit_up import LimitUpAnalyzer


def main() -> None:
    logger = get_logger("limit_up_monitor")
    config = get_config()

    parser = argparse.ArgumentParser(description="涨停板实时监控")
    parser.add_argument("--interval", type=int, default=60, help="刷新间隔秒数")
    args = parser.parse_args()

    analyzer = LimitUpAnalyzer(config)

    logger.info(f"涨停板实时监控启动, 刷新间隔: {args.interval}秒")

    try:
        while True:
            now = datetime.now()
            if now.hour < 9 or (now.hour == 9 and now.minute < 30):
                logger.info("非交易时间, 等待中...")
                time.sleep(60)
                continue
            if now.hour >= 15:
                logger.info("已收盘, 停止监控")
                break

            try:
                results = analyzer.scan_limit_up()
                logger.info(f"当前涨停股数量: {len(results)}")
                for item in results:
                    logger.info(
                        f"  {item.get('code', '')} {item.get('name', '')} "
                        f"涨幅: {item.get('change_pct', 0):.2f}% "
                        f"封单: {item.get('封单金额', 0)}"
                    )
            except Exception as e:
                logger.error(f"扫描异常: {e}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("监控已停止")


if __name__ == "__main__":
    main()
