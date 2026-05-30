from enum import Enum
from typing import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class AlertInfo:
    rule_name: str
    stock_code: str
    level: AlertLevel
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    details: dict = field(default_factory=dict)


class AlertManager:
    def __init__(self, dedup_minutes: int = 5) -> None:
        self._rules: dict[str, dict[str, Callable[[dict], bool] | Callable[[AlertInfo], None]]] = {}
        self._last_triggered: dict[str, datetime] = {}
        self._dedup_minutes: int = dedup_minutes

    def register_alert(
        self,
        rule_name: str,
        condition_func: Callable[[dict], bool],
        callback: Callable[[AlertInfo], None],
    ) -> None:
        self._rules[rule_name] = {
            "condition": condition_func,
            "callback": callback,
        }

    def check_all(self, stock_code: str, market_data: dict | None = None) -> list[AlertInfo]:
        triggered: list[AlertInfo] = []
        data = market_data or {}
        for rule_name, rule in self._rules.items():
            condition: Callable[[dict], bool] = rule["condition"]
            try:
                if condition(data):
                    alert_info = AlertInfo(
                        rule_name=rule_name,
                        stock_code=stock_code,
                        level=AlertLevel.WARNING,
                        message=f"Rule '{rule_name}' triggered for {stock_code}",
                        details=data,
                    )
                    if self._is_deduplicated(stock_code, rule_name):
                        logger.debug(
                            "Alert deduplicated: %s / %s within %d minutes",
                            stock_code,
                            rule_name,
                            self._dedup_minutes,
                        )
                        continue
                    self.trigger_alert(alert_info)
                    triggered.append(alert_info)
            except Exception:
                logger.exception("Error checking rule '%s' for %s", rule_name, stock_code)
        return triggered

    def trigger_alert(self, alert_info: AlertInfo) -> None:
        key = f"{alert_info.stock_code}:{alert_info.rule_name}"
        self._last_triggered[key] = datetime.now()
        rule = self._rules.get(alert_info.rule_name)
        if rule is None:
            logger.warning("No rule found for alert: %s", alert_info.rule_name)
            return
        callback: Callable[[AlertInfo], None] = rule["callback"]
        try:
            callback(alert_info)
        except Exception:
            logger.exception("Error executing callback for rule '%s'", alert_info.rule_name)

    def _is_deduplicated(self, stock_code: str, rule_name: str) -> bool:
        key = f"{stock_code}:{rule_name}"
        last_time = self._last_triggered.get(key)
        if last_time is None:
            return False
        return (datetime.now() - last_time) < timedelta(minutes=self._dedup_minutes)
