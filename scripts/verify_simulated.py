import sys
import os
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from src.core.data_source import DataSourceManager
from src.core.risk_manager import RiskManager, RiskLevel, TradeRecord
from src.core.executor import (
    SimulatedBroker,
    TradeExecutor,
    Signal,
    OrderSide,
    OrderType,
    OrderStatus,
)
from src.core.scheduler import Scheduler


class VerifyResult:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        msg = f"  [{status}] {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        self.results.append((name, condition, detail))

    def summary(self) -> None:
        total = len(self.results)
        passed = sum(1 for _, ok, _ in self.results if ok)
        failed = total - passed
        print(f"\n{'='*60}")
        print(f"  验证汇总: 共 {total} 项, 通过 {passed} 项, 失败 {failed} 项")
        if failed > 0:
            print("  失败项:")
            for name, ok, detail in self.results:
                if not ok:
                    print(f"    - {name}: {detail}")
        print(f"{'='*60}")


def verify_data_source(vr: VerifyResult) -> None:
    print("\n[1] DataSourceManager 初始化验证")
    try:
        dm = DataSourceManager()
        vr.check("DataSourceManager 初始化", True)
        vr.check("主数据源数量", len(dm._primary_sources) >= 3, f"共 {len(dm._primary_sources)} 个主数据源")
        vr.check("缓存数据源", dm._cache is not None)
    except Exception as e:
        vr.check("DataSourceManager 初始化", False, str(e))

    try:
        dm = DataSourceManager()
        result = dm.get_history_kline("000001", "2025-01-01", "2025-01-31")
        vr.check("历史K线获取(4层降级)", result is not None and result.data is not None and not result.data.empty, f"获取到 {len(result.data) if result and result.data is not None else 0} 条记录")
    except Exception as e:
        vr.check("历史K线获取(4层降级)", False, str(e))


def verify_risk_manager(vr: VerifyResult) -> None:
    print("\n[2] RiskManager 初始化验证")
    test_db = Path("data/test_risk_verify.db")
    try:
        rm = RiskManager(db_path=test_db, initial_assets=1_000_000.0)
        vr.check("RiskManager 初始化", True)

        status = rm.get_risk_status()
        vr.check("风控状态-NORMAL", status.level == RiskLevel.NORMAL, f"level={status.level.value}")
        vr.check("初始资产", status.total_assets == 1_000_000.0, f"assets={status.total_assets}")
        vr.check("未暂停", not status.is_paused)
        vr.check("未紧急停止", not status.is_emergency_stopped)
    except Exception as e:
        vr.check("RiskManager 初始化", False, str(e))
    finally:
        if test_db.exists():
            test_db.unlink()


def verify_simulated_broker(vr: VerifyResult) -> None:
    print("\n[3] SimulatedBroker 初始化验证")
    try:
        broker = SimulatedBroker(initial_cash=1_000_000.0)
        vr.check("SimulatedBroker 初始化", True)

        balance = broker.get_balance()
        vr.check("初始资金", balance.total_assets == 1_000_000.0, f"total={balance.total_assets}")
        vr.check("可用现金", balance.available_cash == 1_000_000.0, f"cash={balance.available_cash}")
        vr.check("持仓为空", len(broker.get_positions()) == 0)
    except Exception as e:
        vr.check("SimulatedBroker 初始化", False, str(e))


def verify_trade_executor(vr: VerifyResult) -> None:
    print("\n[4] TradeExecutor 初始化验证")
    test_db = Path("data/test_trade_verify.db")
    risk_db = Path("data/test_risk_exec.db")
    try:
        rm = RiskManager(db_path=risk_db, initial_assets=1_000_000.0)
        broker = SimulatedBroker(initial_cash=1_000_000.0)
        executor = TradeExecutor(broker=broker, risk_manager=rm, db_path=test_db)
        vr.check("TradeExecutor 初始化", True)
    except Exception as e:
        vr.check("TradeExecutor 初始化", False, str(e))
    finally:
        if test_db.exists():
            test_db.unlink()
        if risk_db.exists():
            risk_db.unlink()


def verify_trading_scenarios(vr: VerifyResult) -> None:
    print("\n[5] 模拟交易场景验证")
    test_db = Path("data/test_trade_scenario.db")
    risk_db = Path("data/test_risk_scenario.db")
    try:
        rm = RiskManager(db_path=risk_db, initial_assets=1_000_000.0)
        broker = SimulatedBroker(initial_cash=1_000_000.0)
        executor = TradeExecutor(broker=broker, risk_manager=rm, db_path=test_db)

        buy_signal = Signal(
            symbol="000001",
            side=OrderSide.BUY,
            price=10.0,
            quantity=100,
            order_type=OrderType.MARKET,
            strategy="verify",
            reason="验证买入",
        )
        order = executor.execute_signal(buy_signal)
        vr.check("买入 000001 100股", order is not None and order.status == OrderStatus.FILLED, f"status={order.status.value if order else 'None'}")

        positions = broker.get_positions()
        has_pos = any(p.symbol == "000001" for p in positions)
        vr.check("检查持仓-有000001", has_pos, f"持仓数={len(positions)}")

        broker.update_price("000001", 10.50)

        sell_signal = Signal(
            symbol="000001",
            side=OrderSide.SELL,
            price=10.50,
            quantity=100,
            order_type=OrderType.MARKET,
            strategy="verify",
            reason="验证卖出",
        )
        order = executor.execute_signal(sell_signal)
        vr.check("卖出 000001 100股", order is not None and order.status == OrderStatus.FILLED, f"status={order.status.value if order else 'None'}")

        positions = broker.get_positions()
        vr.check("检查持仓-已清仓", len(positions) == 0, f"持仓数={len(positions)}")

        print("\n  --- 风控拦截测试 ---")
        rm2 = RiskManager(db_path=Path("data/test_risk_intercept.db"), initial_assets=1_000_000.0)
        rm2._daily_pnl = -60_000.0
        rm2._daily_start_assets = 1_000_000.0
        rm2._save_state()

        check_order = {"symbol": "000001", "side": "buy", "price": 10.0, "quantity": 100}
        passed, reason = rm2.check_order(check_order)
        vr.check("日级风控拦截-禁止开新仓", not passed, f"reason={reason}")

        rm3 = RiskManager(db_path=Path("data/test_risk_emergency.db"), initial_assets=1_000_000.0)
        rm3.emergency_stop()
        status = rm3.get_risk_status()
        vr.check("紧急停止-激活", status.is_emergency_stopped)

        check_order2 = {"symbol": "000001", "side": "buy", "price": 10.0, "quantity": 100}
        passed2, reason2 = rm3.check_order(check_order2)
        vr.check("紧急停止-拦截交易", not passed2, f"reason={reason2}")

        rm3.resume()
        status2 = rm3.get_risk_status()
        vr.check("紧急停止-恢复", not status2.is_emergency_stopped and status2.level == RiskLevel.NORMAL)

    except Exception as e:
        vr.check("模拟交易场景", False, str(e))
    finally:
        for p in [test_db, risk_db, Path("data/test_risk_intercept.db"), Path("data/test_risk_emergency.db")]:
            if p.exists():
                p.unlink()


def verify_scheduler(vr: VerifyResult) -> None:
    print("\n[6] Scheduler 初始化验证")
    try:
        scheduler = Scheduler()
        vr.check("Scheduler 初始化", True)

        jobs = scheduler.get_jobs()
        vr.check("默认调度任务注册", len(jobs) > 0, f"共 {len(jobs)} 个任务")

        job_names = [j["name"] for j in jobs]
        vr.check("盘前任务", any("盘前" in n for n in job_names))
        vr.check("盘中任务", any("盘中" in n for n in job_names))
        vr.check("尾盘任务", any("尾盘" in n for n in job_names))
        vr.check("盘后任务", any("盘后" in n for n in job_names))

        scheduler.set_callback("post_daily_report", lambda: print("  [callback] 日报回调执行"))
        vr.check("设置回调函数", True)
    except Exception as e:
        vr.check("Scheduler 初始化", False, str(e))


def main() -> None:
    print("=" * 60)
    print("  模拟盘全流程验证")
    print("=" * 60)

    vr = VerifyResult()

    verify_data_source(vr)
    verify_risk_manager(vr)
    verify_simulated_broker(vr)
    verify_trade_executor(vr)
    verify_trading_scenarios(vr)
    verify_scheduler(vr)

    vr.summary()


if __name__ == "__main__":
    main()
