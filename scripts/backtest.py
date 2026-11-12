import sys
import os
import argparse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from src.core.backtest import (
    BacktestEngine,
    BacktestResult,
    new_high_strategy,
    limit_up_strategy,
    cb_t0_strategy,
    long_value_strategy,
    ma_cross_strategy,
)

_STRATEGY_MAP = {
    "new_high": ("创业板新高策略", new_high_strategy),
    "limit_up": ("涨停板策略", limit_up_strategy),
    "cb_t0": ("可转债T+0策略", cb_t0_strategy),
    "long_value": ("长线价值策略", long_value_strategy),
    "ma_cross": ("MA金叉死叉策略(5/20)", ma_cross_strategy),
}


def _format_result(name: str, result: BacktestResult) -> str:
    lines = [
        f"\n{'='*60}",
        f"  策略: {name}",
        f"{'='*60}",
        f"  总收益率:     {result.total_return:>10.2%}",
        f"  年化收益率:   {result.annual_return:>10.2%}",
        f"  最大回撤:     {result.max_drawdown:>10.2%}",
        f"  夏普比率:     {result.sharpe_ratio:>10.4f}",
        f"  信息比率:     {result.information_ratio:>10.4f}",
        f"  胜率:         {result.win_rate:>10.2%}",
        f"  盈亏比:       {result.profit_loss_ratio:>10.4f}",
        f"  交易次数:     {result.trade_count:>10d}",
        f"  最终权益:     {result.equity_curve[-1]:>14.2f}" if result.equity_curve else "",
        f"{'='*60}",
    ]
    return "\n".join(lines)


def _save_report(
    stock_code: str,
    start_date: str,
    end_date: str,
    results: dict[str, BacktestResult],
) -> Path:
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    report_path = log_dir / f"backtest_{stock_code}_{date.today().strftime('%Y%m%d')}.txt"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("回测报告\n")
        f.write(f"股票: {stock_code}\n")
        f.write(f"区间: {start_date} ~ {end_date}\n")
        f.write(f"生成时间: {date.today().isoformat()}\n\n")
        for strategy_key, result in results.items():
            name = _STRATEGY_MAP.get(strategy_key, (strategy_key,))[0]
            f.write(_format_result(name, result))
            f.write("\n\n")

    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="回测引擎")
    parser.add_argument(
        "--strategy",
        choices=["new_high", "limit_up", "cb_t0", "long_value", "ma_cross", "all"],
        default="all",
        help="策略选择",
    )
    parser.add_argument("--stock", default="000001", help="股票代码")
    parser.add_argument("--start", default=None, help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--optimize-ma", action="store_true",
                        help="对 ma_cross 做 MA_short × MA_long 网格优化,按信息比率排序输出")
    args = parser.parse_args()

    end_date = args.end or date.today().isoformat()
    start_date = args.start or (date.today() - timedelta(days=365)).isoformat()

    engine = BacktestEngine()

    if args.optimize_ma:
        logger.info(f"MA 参数网格优化: {args.stock} [{start_date} ~ {end_date}]")
        opt = engine.optimize_ma_cross(args.stock, start_date, end_date)
        if not opt["grid"]:
            logger.warning("网格为空(无有效K线数据)")
            return
        print(f"\nMA 参数网格优化: {args.stock} [{start_date} ~ {end_date}]  组合数={len(opt['grid'])}")
        print(f"{'short':>6} {'long':>6} {'年化收益':>10} {'最大回撤':>10} {'夏普':>8} {'IR':>8} {'交易数':>6}")
        for row in opt["grid"][:20]:
            print(f"{row['short_window']:>6} {row['long_window']:>6} "
                  f"{row['annual_return']:>10.2%} {row['max_drawdown']:>10.2%} "
                  f"{row['sharpe_ratio']:>8.4f} {row['information_ratio']:>8.4f} {row['trade_count']:>6}")
        best = opt["best"]
        logger.info(f"最优组合: MA{best['short_window']}/MA{best['long_window']} IR={best['information_ratio']}")
        return

    if args.strategy == "all":
        logger.info(f"运行全部策略回测: {args.stock} [{start_date} ~ {end_date}]")
        results = engine.run_all_strategies(args.stock, start_date, end_date)
    else:
        display_name, strategy_func = _STRATEGY_MAP[args.strategy]
        logger.info(f"运行策略回测: {display_name} {args.stock} [{start_date} ~ {end_date}]")
        result = engine.run(strategy_func, args.stock, start_date, end_date)
        results = {args.strategy: result}

    for strategy_key, result in results.items():
        name = _STRATEGY_MAP.get(strategy_key, (strategy_key,))[0]
        print(_format_result(name, result))

    report_path = _save_report(args.stock, start_date, end_date, results)
    logger.info(f"回测报告已保存: {report_path}")


if __name__ == "__main__":
    main()
