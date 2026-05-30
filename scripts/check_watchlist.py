import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.core.data_source import DataSourceManager


def main() -> None:
    logger = get_logger("check_watchlist")
    config = get_config()

    parser = argparse.ArgumentParser(description="更新自选股行情")
    parser.add_argument("--source", type=str, default="default", help="数据源")
    args = parser.parse_args()

    ds_manager = DataSourceManager(config)

    logger.info("开始读取自选股列表...")

    watchlist = ds_manager.get_watchlist()
    if not watchlist:
        logger.warning("自选股列表为空")
        return

    logger.info(f"自选股数量: {len(watchlist)}")

    success_count = 0
    fail_count = 0

    for item in watchlist:
        code = item.get("code", "") if isinstance(item, dict) else item
        try:
            quote = ds_manager.get_realtime_quote(code)
            if quote:
                name = quote.get("name", "")
                price = quote.get("price", 0)
                change_pct = quote.get("change_pct", 0)
                logger.info(f"  {code} {name} 现价: {price} 涨跌: {change_pct:.2f}%")
                success_count += 1
            else:
                logger.warning(f"  {code} 行情获取为空")
                fail_count += 1
        except Exception as e:
            logger.error(f"  {code} 行情更新失败: {e}")
            fail_count += 1

    logger.info(f"自选股行情更新完成: 成功 {success_count}, 失败 {fail_count}")


if __name__ == "__main__":
    main()
