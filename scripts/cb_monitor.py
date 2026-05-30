import sys
import os
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.strategies.cb_t0_sniper import CBT0Sniper


def main() -> None:
    logger = get_logger("cb_monitor")
    config = get_config()

    parser = argparse.ArgumentParser(description="可转债T+0监控")
    parser.add_argument("--continuous", type=int, default=30, help="扫描间隔秒数")
    parser.add_argument("--stock", type=str, default=None, help="指定股票代码")
    args = parser.parse_args()

    sniper = CBT0Sniper(config)

    logger.info(f"可转债T+0监控启动, 扫描间隔: {args.continuous}秒")
    if args.stock:
        logger.info(f"指定监控股票: {args.stock}")

    try:
        while True:
            try:
                if args.stock:
                    result = sniper.scan_single(args.stock)
                    logger.info(f"监控结果 [{args.stock}]: {result}")
                else:
                    results = sniper.scan_all()
                    for code, result in results.items():
                        logger.info(f"监控结果 [{code}]: {result}")
            except Exception as e:
                logger.error(f"扫描异常: {e}")

            logger.info(f"等待 {args.continuous} 秒后下次扫描...")
            time.sleep(args.continuous)
    except KeyboardInterrupt:
        logger.info("监控已停止")


if __name__ == "__main__":
    main()
