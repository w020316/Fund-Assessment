import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

_CONFIG_DIR = Path("config")


class Config:
    _instance: "Config | None" = None
    _data: dict[str, Any]

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        for filename in ("settings.yaml", "strategies.yaml"):
            filepath = _CONFIG_DIR / filename
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if content:
                        self._data[filepath.stem] = content

        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        for key, value in os.environ.items():
            if key.startswith("APP_"):
                parts = key[4:].lower().split("__")
                self._set_nested(parts, value)

    def _set_nested(self, keys: list[str], value: str) -> None:
        current = self._data
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value

    def get(self, dotted_key: str, default: Any = None) -> Any:
        keys = dotted_key.split(".")
        current: Any = self._data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


def get_config() -> Config:
    return Config()
