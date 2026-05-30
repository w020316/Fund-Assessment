import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.core.data_source import DataSourceManager


def main() -> None:
    logger = get_logger("analyze_trades")
    config = get_config()

    parser = argparse.ArgumentParser(description="分析当日交易")
    parser.add_argument("--date", type=str, default=None, help="指定日期 YYYYMMDD")
    args = parser.parse_args()

    today = args.date or datetime.now().strftime("%Y%m%d")
    logger.info(f"开始分析 {today} 交易数据...")

    ds_manager = DataSourceManager(config)

    try:
        trades = ds_manager.get_today_trades(today)
    except Exception as e:
        logger.error(f"获取交易数据失败: {e}")
        return

    if not trades:
        logger.info("今日无交易记录")
        return

    total_count = len(trades)
    win_trades = [t for t in trades if t.get("pnl", 0) > 0]
    loss_trades = [t for t in trades if t.get("pnl", 0) < 0]
    flat_trades = [t for t in trades if t.get("pnl", 0) == 0]

    win_count = len(win_trades)
    loss_count = len(loss_trades)
    win_rate = (win_count / total_count * 100) if total_count > 0 else 0

    total_win = sum(t.get("pnl", 0) for t in win_trades)
    total_loss = abs(sum(t.get("pnl", 0) for t in loss_trades))
    profit_loss_ratio = (total_win / total_loss) if total_loss > 0 else float("inf")

    max_win = max((t.get("pnl", 0) for t in trades), default=0)
    max_loss = min((t.get("pnl", 0) for t in trades), default=0)

    logger.info("=" * 50)
    logger.info(f"交易分析报告 - {today}")
    logger.info("=" * 50)
    logger.info(f"交易次数: {total_count}")
    logger.info(f"盈利次数: {win_count} 亏损次数: {loss_count} 持平: {len(flat_trades)}")
    logger.info(f"胜率: {win_rate:.2f}%")
    logger.info(f"总盈利: {total_win:,.2f} 总亏损: {total_loss:,.2f}")
    logger.info(f"盈亏比: {profit_loss_ratio:.2f}")
    logger.info(f"最大单笔盈利: {max_win:,.2f}")
    logger.info(f"最大单笔亏损: {max_loss:,.2f}")

    strategy_groups: dict[str, list] = {}
    for t in trades:
        strategy = t.get("strategy", "unknown")
        strategy_groups.setdefault(strategy, []).append(t)

    if strategy_groups:
        logger.info("-" * 50)
        logger.info("按策略分组统计:")
        for strategy, group in strategy_groups.items():
            s_count = len(group)
            s_win = len([t for t in group if t.get("pnl", 0) > 0])
            s_win_rate = (s_win / s_count * 100) if s_count > 0 else 0
            s_total_pnl = sum(t.get("pnl", 0) for t in group)
            logger.info(
                f"  {strategy}: 交易 {s_count} 笔, "
                f"胜率 {s_win_rate:.2f}%, 总盈亏 {s_total_pnl:,.2f}"
            )

    logger.info("=" * 50)


if __name__ == "__main__":
    main()
