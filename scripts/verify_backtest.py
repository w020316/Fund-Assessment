import sys
import os
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
)


_STRATEGIES = {
    "new_high": ("创业板新高策略", new_high_strategy),
    "limit_up": ("涨停板策略", limit_up_strategy),
    "cb_t0": ("可转债T+0策略", cb_t0_strategy),
    "long_value": ("长线价值策略", long_value_strategy),
}


def _format_result(name: str, result: BacktestResult) -> str:
    lines = [
        f"### {name}\n",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 总收益率 | {result.total_return:.2%} |",
        f"| 年化收益率 | {result.annual_return:.2%} |",
        f"| 最大回撤 | {result.max_drawdown:.2%} |",
        f"| 夏普比率 | {result.sharpe_ratio:.4f} |",
        f"| 胜率 | {result.win_rate:.2%} |",
        f"| 盈亏比 | {result.profit_loss_ratio:.4f} |",
        f"| 交易次数 | {result.trade_count} |",
    ]
    if result.equity_curve:
        lines.append(f"| 最终权益 | {result.equity_curve[-1]:.2f} |")
    return "\n".join(lines)


def _generate_report(
    stock_code: str,
    start_date: str,
    end_date: str,
    results: dict[str, BacktestResult],
) -> Path:
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    report_path = log_dir / f"backtest_report_{date.today().strftime('%Y%m%d')}.md"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 回测验证报告\n\n")
        f.write(f"- **股票**: {stock_code}\n")
        f.write(f"- **回测区间**: {start_date} ~ {end_date}\n")
        f.write(f"- **生成时间**: {date.today().isoformat()}\n")
        f.write(f"- **初始资金**: 1,000,000.00\n\n")
        f.write(f"---\n\n")

        for strategy_key, result in results.items():
            name = _STRATEGIES.get(strategy_key, (strategy_key,))[0]
            f.write(_format_result(name, result))
            f.write("\n\n---\n\n")

            if result.trades:
                f.write(f"#### {name} 交易记录\n\n")
                f.write("| 日期 | 方向 | 价格 | 数量 | 金额 | 佣金 | 印花税 | 策略 | 原因 |\n")
                f.write("|------|------|------|------|------|------|--------|------|------|\n")
                for t in result.trades[:50]:
                    profit_str = f" (盈亏: {t['profit']:.2f})" if "profit" in t else ""
                    f.write(
                        f"| {t['date']} | {t['side']} | {t['price']:.2f} | "
                        f"{t['quantity']:.0f} | {t['amount']:.2f} | "
                        f"{t['commission']:.2f} | {t.get('stamp_tax', 0):.2f} | "
                        f"{t['strategy']} | {t['reason']}{profit_str} |\n"
                    )
                if len(result.trades) > 50:
                    f.write(f"\n> 仅展示前50条，共 {len(result.trades)} 条交易记录\n")
                f.write("\n---\n\n")

    return report_path


def main() -> None:
    stock_code = "000001"
    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=365)).isoformat()

    print("=" * 60)
    print("  回测验证")
    print(f"  股票: {stock_code}")
    print(f"  区间: {start_date} ~ {end_date}")
    print("=" * 60)

    engine = BacktestEngine()
    results: dict[str, BacktestResult] = {}

    for strategy_key, (display_name, strategy_func) in _STRATEGIES.items():
        print(f"\n>>> 运行策略: {display_name}")
        logger.info(f"Running backtest: {display_name} ({strategy_key})")
        try:
            result = engine.run(strategy_func, stock_code, start_date, end_date)
            results[strategy_key] = result
            print(f"    总收益率: {result.total_return:.2%}")
            print(f"    年化收益率: {result.annual_return:.2%}")
            print(f"    最大回撤: {result.max_drawdown:.2%}")
            print(f"    夏普比率: {result.sharpe_ratio:.4f}")
            print(f"    胜率: {result.win_rate:.2%}")
            print(f"    盈亏比: {result.profit_loss_ratio:.4f}")
            print(f"    交易次数: {result.trade_count}")
        except Exception as e:
            logger.error(f"Backtest failed for {display_name}: {e}")
            print(f"    [ERROR] {e}")

    if results:
        report_path = _generate_report(stock_code, start_date, end_date, results)
        print(f"\n回测报告已生成: {report_path}")
        logger.info(f"Backtest report generated: {report_path}")
    else:
        print("\n所有策略回测均失败，未生成报告")


if __name__ == "__main__":
    main()
