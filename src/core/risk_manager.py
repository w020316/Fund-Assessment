from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from loguru import logger

_DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"

_SYSTEM_DRAWDOWN_THRESHOLD = 0.15
_SYSTEM_PAUSE_DAYS = 5
_DAILY_LOSS_THRESHOLD = 0.05
_CONSECUTIVE_STOP_LOSS_LIMIT = 3
_POSITION_REDUCTION_RATIO = 0.5


class RiskLevel(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    DANGER = "danger"
    EMERGENCY = "emergency"


@dataclass
class RiskStatus:
    level: RiskLevel
    total_assets: float
    peak_assets: float
    drawdown_pct: float
    daily_pnl: float
    daily_pnl_pct: float
    consecutive_stop_losses: int
    is_paused: bool
    pause_until: Optional[date]
    is_emergency_stopped: bool
    no_new_positions: bool
    position_reduction: float
    message: str


@dataclass
class TradeRecord:
    symbol: str
    side: str
    price: float
    quantity: float
    amount: float
    profit: float
    is_stop_loss: bool
    timestamp: datetime = field(default_factory=datetime.now)


class RiskManager:
    def __init__(self, db_path: Optional[Path] = None, initial_assets: float = 1_000_000.0):
        self._db_path = db_path or _DEFAULT_DB_DIR / "risk.db"
        self._total_assets: float = initial_assets
        self._peak_assets: float = initial_assets
        self._daily_start_assets: float = initial_assets
        self._daily_pnl: float = 0.0
        self._consecutive_stop_losses: int = 0
        self._is_paused: bool = False
        self._pause_until: Optional[date] = None
        self._is_emergency_stopped: bool = False
        self._no_new_positions: bool = False
        self._position_reduction: float = 1.0
        self._last_trade_date: Optional[date] = None
        self._init_db()
        self._load_state()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS risk_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    total_assets REAL NOT NULL,
                    peak_assets REAL NOT NULL,
                    daily_start_assets REAL NOT NULL,
                    daily_pnl REAL NOT NULL DEFAULT 0,
                    consecutive_stop_losses INTEGER NOT NULL DEFAULT 0,
                    is_paused INTEGER NOT NULL DEFAULT 0,
                    pause_until TEXT,
                    is_emergency_stopped INTEGER NOT NULL DEFAULT 0,
                    no_new_positions INTEGER NOT NULL DEFAULT 0,
                    position_reduction REAL NOT NULL DEFAULT 1.0,
                    last_trade_date TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    amount REAL NOT NULL,
                    profit REAL NOT NULL DEFAULT 0,
                    is_stop_loss INTEGER NOT NULL DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _load_state(self) -> None:
        try:
            with self._get_conn() as conn:
                cursor = conn.execute("SELECT * FROM risk_state WHERE id = 1")
                row = cursor.fetchone()
                if row is None:
                    self._save_state()
                    return
                (
                    _,
                    self._total_assets,
                    self._peak_assets,
                    self._daily_start_assets,
                    self._daily_pnl,
                    self._consecutive_stop_losses,
                    self._is_paused,
                    pause_until_str,
                    self._is_emergency_stopped,
                    self._no_new_positions,
                    self._position_reduction,
                    last_trade_date_str,
                    _,
                ) = row
                self._is_paused = bool(self._is_paused)
                self._is_emergency_stopped = bool(self._is_emergency_stopped)
                self._no_new_positions = bool(self._no_new_positions)
                self._pause_until = (
                    date.fromisoformat(pause_until_str) if pause_until_str else None
                )
                self._last_trade_date = (
                    date.fromisoformat(last_trade_date_str)
                    if last_trade_date_str
                    else None
                )
                self._check_pause_expiry()
                self._check_daily_reset()
        except Exception as e:
            logger.warning(f"RiskManager._load_state failed: {e}")
            self._save_state()

    def _save_state(self) -> None:
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO risk_state
                    (id, total_assets, peak_assets, daily_start_assets, daily_pnl,
                     consecutive_stop_losses, is_paused, pause_until, is_emergency_stopped,
                     no_new_positions, position_reduction, last_trade_date, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        self._total_assets,
                        self._peak_assets,
                        self._daily_start_assets,
                        self._daily_pnl,
                        self._consecutive_stop_losses,
                        int(self._is_paused),
                        self._pause_until.isoformat() if self._pause_until else None,
                        int(self._is_emergency_stopped),
                        int(self._no_new_positions),
                        self._position_reduction,
                        self._last_trade_date.isoformat() if self._last_trade_date else None,
                        datetime.now().isoformat(),
                    ),
                )
        except Exception as e:
            logger.warning(f"RiskManager._save_state failed: {e}")

    def _check_pause_expiry(self) -> None:
        if self._is_paused and self._pause_until:
            if date.today() >= self._pause_until:
                self._is_paused = False
                self._pause_until = None
                self._save_state()
                logger.info("Risk pause period expired, resuming trading")

    def _check_daily_reset(self) -> None:
        today = date.today()
        if self._last_trade_date and self._last_trade_date < today:
            self._daily_start_assets = self._total_assets
            self._daily_pnl = 0.0
            if self._no_new_positions:
                self._no_new_positions = False
                logger.info("Daily new-position restriction lifted for new trading day")
            self._save_state()

    def _calc_drawdown(self) -> float:
        if self._peak_assets <= 0:
            return 0.0
        return (self._peak_assets - self._total_assets) / self._peak_assets

    def _calc_daily_pnl_pct(self) -> float:
        if self._daily_start_assets <= 0:
            return 0.0
        return self._daily_pnl / self._daily_start_assets

    def check_order(self, order: dict[str, Any]) -> tuple[bool, str]:
        self._check_pause_expiry()
        self._check_daily_reset()

        if self._is_emergency_stopped:
            return False, "紧急停止已激活，禁止所有交易"

        if self._is_paused:
            return False, f"系统暂停中，恢复日期: {self._pause_until}"

        drawdown = self._calc_drawdown()
        if drawdown > _SYSTEM_DRAWDOWN_THRESHOLD:
            self._is_paused = True
            self._pause_until = date.today() + timedelta(days=_SYSTEM_PAUSE_DAYS)
            self._save_state()
            return False, f"资产回撤 {drawdown:.2%} 超过 {_SYSTEM_DRAWDOWN_THRESHOLD:.0%} 阈值，清仓并暂停{_SYSTEM_PAUSE_DAYS}日"

        daily_pnl_pct = self._calc_daily_pnl_pct()
        if daily_pnl_pct < -_DAILY_LOSS_THRESHOLD:
            side = order.get("side", "")
            if side.lower() in ("buy", "买入"):
                self._no_new_positions = True
                self._save_state()
                return False, f"单日亏损 {daily_pnl_pct:.2%} 超过 {_DAILY_LOSS_THRESHOLD:.0%} 阈值，禁止开新仓"

        if self._no_new_positions:
            side = order.get("side", "")
            if side.lower() in ("buy", "买入"):
                return False, "风控限制：当日禁止开新仓"

        if self._consecutive_stop_losses >= _CONSECUTIVE_STOP_LOSS_LIMIT:
            side = order.get("side", "")
            if side.lower() in ("buy", "买入"):
                self._position_reduction = _POSITION_REDUCTION_RATIO
                self._save_state()
                return True, f"连续{_CONSECUTIVE_STOP_LOSS_LIMIT}次止损，买入仓位缩减至{_POSITION_REDUCTION_RATIO:.0%}"

        return True, "风控检查通过"

    def update_position(self, position: dict[str, Any]) -> None:
        total_assets = position.get("total_assets", self._total_assets)
        self._total_assets = total_assets
        if total_assets > self._peak_assets:
            self._peak_assets = total_assets
        self._daily_pnl = self._total_assets - self._daily_start_assets
        self._save_state()

    def record_trade(self, trade: TradeRecord) -> None:
        today = date.today()
        if self._last_trade_date != today:
            self._daily_start_assets = self._total_assets
            self._daily_pnl = 0.0
            self._last_trade_date = today

        self._daily_pnl += trade.profit
        self._total_assets += trade.profit
        if self._total_assets > self._peak_assets:
            self._peak_assets = self._total_assets

        if trade.is_stop_loss:
            self._consecutive_stop_losses += 1
        else:
            if trade.side.lower() in ("sell", "卖出") and trade.profit > 0:
                self._consecutive_stop_losses = 0
                if self._position_reduction < 1.0:
                    self._position_reduction = 1.0

        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO trade_records (symbol, side, price, quantity, amount, profit, is_stop_loss, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.symbol,
                        trade.side,
                        trade.price,
                        trade.quantity,
                        trade.amount,
                        trade.profit,
                        int(trade.is_stop_loss),
                        trade.timestamp.isoformat(),
                    ),
                )
        except Exception as e:
            logger.warning(f"RiskManager.record_trade DB insert failed: {e}")

        self._save_state()

    def get_risk_status(self) -> RiskStatus:
        self._check_pause_expiry()
        self._check_daily_reset()

        drawdown = self._calc_drawdown()
        daily_pnl_pct = self._calc_daily_pnl_pct()

        if self._is_emergency_stopped:
            level = RiskLevel.EMERGENCY
            message = "紧急停止已激活"
        elif self._is_paused:
            level = RiskLevel.DANGER
            message = f"系统暂停中，恢复日期: {self._pause_until}"
        elif drawdown > _SYSTEM_DRAWDOWN_THRESHOLD:
            level = RiskLevel.DANGER
            message = f"资产回撤 {drawdown:.2%} 超过阈值"
        elif daily_pnl_pct < -_DAILY_LOSS_THRESHOLD:
            level = RiskLevel.DANGER
            message = f"单日亏损 {daily_pnl_pct:.2%} 超过阈值"
        elif self._consecutive_stop_losses >= _CONSECUTIVE_STOP_LOSS_LIMIT:
            level = RiskLevel.WARNING
            message = f"连续{self._consecutive_stop_losses}次止损，仓位缩减"
        elif drawdown > _SYSTEM_DRAWDOWN_THRESHOLD * 0.5:
            level = RiskLevel.WARNING
            message = f"资产回撤 {drawdown:.2%}，接近阈值"
        else:
            level = RiskLevel.NORMAL
            message = "风控正常"

        return RiskStatus(
            level=level,
            total_assets=self._total_assets,
            peak_assets=self._peak_assets,
            drawdown_pct=drawdown,
            daily_pnl=self._daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            consecutive_stop_losses=self._consecutive_stop_losses,
            is_paused=self._is_paused,
            pause_until=self._pause_until,
            is_emergency_stopped=self._is_emergency_stopped,
            no_new_positions=self._no_new_positions,
            position_reduction=self._position_reduction,
            message=message,
        )

    def emergency_stop(self) -> None:
        self._is_emergency_stopped = True
        self._save_state()
        logger.critical("紧急停止已激活！所有自动交易已停止")

    def resume(self) -> None:
        self._is_emergency_stopped = False
        self._is_paused = False
        self._pause_until = None
        self._no_new_positions = False
        self._position_reduction = 1.0
        self._consecutive_stop_losses = 0
        self._save_state()
        logger.info("风控状态已重置，恢复交易")
