import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.core.data_source import DataSourceManager


def main() -> None:
    logger = get_logger("update_positions")
    config = get_config()

    parser = argparse.ArgumentParser(description="更新持仓记录")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入")
    args = parser.parse_args()

    ds_manager = DataSourceManager(config)

    logger.info("开始从券商API获取最新持仓...")

    try:
        remote_positions = ds_manager.fetch_broker_positions()
        logger.info(f"券商持仓数量: {len(remote_positions)}")
    except Exception as e:
        logger.error(f"获取券商持仓失败: {e}")
        return

    if args.dry_run:
        logger.info("预览模式, 不写入数据库")
        for pos in remote_positions:
            code = pos.get("code", "")
            name = pos.get("name", "")
            volume = pos.get("volume", 0)
            cost = pos.get("cost", 0)
            current_price = pos.get("current_price", 0)
            pnl = (current_price - cost) * volume if current_price and cost else 0
            pnl_pct = ((current_price - cost) / cost * 100) if cost else 0
            logger.info(
                f"  {code} {name} 数量: {volume} 成本: {cost:.2f} "
                f"现价: {current_price:.2f} 盈亏: {pnl:,.2f} ({pnl_pct:.2f}%)"
            )
        return

    try:
        ds_manager.update_positions(remote_positions)
        logger.info("持仓记录已更新到本地数据库")
    except Exception as e:
        logger.error(f"更新持仓记录失败: {e}")
        return

    try:
        positions = ds_manager.get_positions()
        total_pnl = 0.0
        total_market_value = 0.0
        for pos in positions:
            code = pos.get("code", "")
            name = pos.get("name", "")
            volume = pos.get("volume", 0)
            current_price = pos.get("current_price", 0)
            cost = pos.get("cost", 0)
            market_value = current_price * volume
            pnl = (current_price - cost) * volume if current_price and cost else 0
            pnl_pct = ((current_price - cost) / cost * 100) if cost else 0
            total_pnl += pnl
            total_market_value += market_value
            logger.info(
                f"  {code} {name} 数量: {volume} 市值: {market_value:,.2f} "
                f"盈亏: {pnl:,.2f} ({pnl_pct:.2f}%)"
            )
        logger.info(f"总市值: {total_market_value:,.2f} 总盈亏: {total_pnl:,.2f}")
    except Exception as e:
        logger.error(f"计算持仓盈亏失败: {e}")


if __name__ == "__main__":
    main()
