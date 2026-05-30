import sys
import os
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.strategies.trading_quant import TradingQuant
from src.strategies.limit_up import LimitUpAnalyzer
from src.strategies.cb_t0_sniper import CBT0Sniper
from src.core.data_source import DataSourceManager


def main() -> None:
    logger = get_logger("generate_daily_report")
    config = get_config()

    parser = argparse.ArgumentParser(description="生成收盘日报")
    parser.add_argument("--date", type=str, default=None, help="指定日期 YYYYMMDD")
    args = parser.parse_args()

    today = args.date or datetime.now().strftime("%Y%m%d")
    logger.info(f"开始生成 {today} 收盘日报...")

    ds_manager = DataSourceManager(config)
    quant = TradingQuant(config)
    limit_up_analyzer = LimitUpAnalyzer(config)
    cb_sniper = CBT0Sniper(config)

    report_lines: list[str] = []
    report_lines.append(f"# 收盘日报 {today}")
    report_lines.append("")

    report_lines.append("## 今日交易汇总")
    try:
        trades = ds_manager.get_today_trades(today)
        report_lines.append(f"- 交易笔数: {len(trades)}")
        total_buy = sum(t.get("amount", 0) for t in trades if t.get("side") == "buy")
        total_sell = sum(t.get("amount", 0) for t in trades if t.get("side") == "sell")
        report_lines.append(f"- 买入总额: {total_buy:,.2f}")
        report_lines.append(f"- 卖出总额: {total_sell:,.2f}")
    except Exception as e:
        report_lines.append(f"- 数据获取失败: {e}")
    report_lines.append("")

    report_lines.append("## 持仓盈亏")
    try:
        positions = ds_manager.get_positions()
        total_pnl = 0.0
        for pos in positions:
            code = pos.get("code", "")
            name = pos.get("name", "")
            pnl = pos.get("pnl", 0)
            pnl_pct = pos.get("pnl_pct", 0)
            total_pnl += pnl
            report_lines.append(f"- {code} {name}: 盈亏 {pnl:,.2f} ({pnl_pct:.2f}%)")
        report_lines.append(f"\n**总盈亏: {total_pnl:,.2f}**")
    except Exception as e:
        report_lines.append(f"- 数据获取失败: {e}")
    report_lines.append("")

    report_lines.append("## 策略表现")
    try:
        limit_up_results = limit_up_analyzer.scan_limit_up()
        report_lines.append(f"- 涨停板策略: 今日涨停 {len(limit_up_results)} 只")
    except Exception as e:
        report_lines.append(f"- 涨停板策略: 获取失败 {e}")
    try:
        cb_results = cb_sniper.scan_all()
        report_lines.append(f"- 可转债T+0策略: 今日信号 {len(cb_results)} 只")
    except Exception as e:
        report_lines.append(f"- 可转债T+0策略: 获取失败 {e}")
    report_lines.append("")

    report_lines.append("## 风控状态")
    try:
        risk_status = ds_manager.get_risk_status()
        report_lines.append(f"- 最大回撤: {risk_status.get('max_drawdown', 'N/A')}")
        report_lines.append(f"- 仓位比例: {risk_status.get('position_ratio', 'N/A')}")
        report_lines.append(f"- 风控等级: {risk_status.get('risk_level', 'N/A')}")
    except Exception as e:
        report_lines.append(f"- 数据获取失败: {e}")
    report_lines.append("")

    report_lines.append("## 明日关注")
    try:
        anomaly = quant.market_anomaly()
        report_lines.append(f"- 市场异动标的: {len(anomaly)} 只")
        for item in anomaly[:10]:
            report_lines.append(
                f"  - {item.get('code', '')} {item.get('name', '')} "
                f"涨幅: {item.get('change_pct', 0):.2f}%"
            )
    except Exception as e:
        report_lines.append(f"- 数据获取失败: {e}")
    report_lines.append("")

    report_content = "\n".join(report_lines)

    output_dir = Path("data/logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"daily_report_{today}.md"
    output_path.write_text(report_content, encoding="utf-8")

    logger.info(f"收盘日报已生成: {output_path}")


if __name__ == "__main__":
    main()
