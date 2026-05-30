import sys
from pathlib import Path

from loguru import logger

_LOG_DIR = Path("data/logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger.remove()

logger.add(
    sys.stderr,
    format="[{time:YYYY-MM-DD HH:mm:ss}] [{level}] [{module}] {message}",
    level="DEBUG",
)

logger.add(
    _LOG_DIR / "{time:YYYY-MM-DD}.log",
    format="[{time:YYYY-MM-DD HH:mm:ss}] [{level}] [{module}] {message}",
    rotation="00:00",
    retention="30 days",
    encoding="utf-8",
    level="DEBUG",
)


def get_logger(name: str = "root") -> logger.__class__:
    return logger.bind(name=name)
