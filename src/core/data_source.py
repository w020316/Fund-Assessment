from __future__ import annotations

import json
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import akshare as ak
import numpy as np
import pandas as pd
import tushare as ts
from loguru import logger

_DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class DataSourceError(Exception):
    pass


@dataclass
class QuoteResult:
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: float
    amount: float
    timestamp: datetime
    source: str
    latency_ms: float


@dataclass
class KlineResult:
    symbol: str
    data: pd.DataFrame
    source: str
    latency_ms: float


@dataclass
class CapitalFlowResult:
    symbol: str
    main_net_inflow: float
    main_inflow: float
    main_outflow: float
    retail_net_inflow: float
    date: date
    source: str
    latency_ms: float


@dataclass
class NorthboundFlowResult:
    date: date
    sh_net_inflow: float
    sz_net_inflow: float
    total_net_inflow: float
    source: str
    latency_ms: float


@dataclass
class NewsResult:
    symbol: str
    title: str
    content: str
    publish_time: datetime
    source: str
    latency_ms: float


@dataclass
class SourceLog:
    query_type: str
    symbol: str
    source: str
    latency_ms: float
    success: bool
    timestamp: datetime = field(default_factory=datetime.now)


class DataSourceBase(ABC):
    @abstractmethod
    def get_realtime_quote(self, symbol: str) -> Optional[QuoteResult]: ...

    @abstractmethod
    def get_history_kline(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> Optional[KlineResult]: ...

    @abstractmethod
    def get_capital_flow(self, symbol: str) -> Optional[CapitalFlowResult]: ...

    @abstractmethod
    def get_northbound_flow(self) -> Optional[NorthboundFlowResult]: ...

    @abstractmethod
    def get_news(self, symbol: str) -> Optional[list[NewsResult]]: ...


class AkShareSource(DataSourceBase):
    def get_realtime_quote(self, symbol: str) -> Optional[QuoteResult]:
        t0 = time.monotonic()
        try:
            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == symbol]
            if row.empty:
                return None
            r = row.iloc[0]
            return QuoteResult(
                symbol=symbol,
                name=str(r.get("名称", "")),
                price=float(r.get("最新价", 0) or 0),
                change_pct=float(r.get("涨跌幅", 0) or 0),
                volume=float(r.get("成交量", 0) or 0),
                amount=float(r.get("成交额", 0) or 0),
                timestamp=datetime.now(),
                source="akshare",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"AkShareSource.get_realtime_quote failed: {e}")
            return None

    def get_history_kline(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> Optional[KlineResult]:
        t0 = time.monotonic()
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq",
            )
            return KlineResult(
                symbol=symbol,
                data=df,
                source="akshare",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"AkShareSource.get_history_kline failed: {e}")
            return None

    def get_capital_flow(self, symbol: str) -> Optional[CapitalFlowResult]:
        t0 = time.monotonic()
        try:
            market = "sh" if symbol.startswith("6") else "sz"
            df = ak.stock_individual_fund_flow(stock=symbol, market=market)
            if df is None or df.empty:
                return None
            r = df.iloc[0]
            return CapitalFlowResult(
                symbol=symbol,
                main_net_inflow=float(r.get("主力净流入-净额", 0) or 0),
                main_inflow=float(r.get("主力净流入-净流入", 0) or 0),
                main_outflow=float(r.get("主力净流入-净流出", 0) or 0),
                retail_net_inflow=float(r.get("散户净流入-净额", 0) or 0),
                date=date.today(),
                source="akshare",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"AkShareSource.get_capital_flow failed: {e}")
            return None

    def get_northbound_flow(self) -> Optional[NorthboundFlowResult]:
        t0 = time.monotonic()
        try:
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北向")
            if df is None or df.empty:
                return None
            r = df.iloc[0]
            return NorthboundFlowResult(
                date=date.today(),
                sh_net_inflow=0.0,
                sz_net_inflow=0.0,
                total_net_inflow=float(r.get("当日净流入", 0) or 0),
                source="akshare",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"AkShareSource.get_northbound_flow failed: {e}")
            return None

    def get_news(self, symbol: str) -> Optional[list[NewsResult]]:
        t0 = time.monotonic()
        try:
            df = ak.stock_news_em(symbol=symbol)
            if df is None or df.empty:
                return None
            latency = (time.monotonic() - t0) * 1000
            results: list[NewsResult] = []
            for _, row in df.iterrows():
                results.append(
                    NewsResult(
                        symbol=symbol,
                        title=str(row.get("新闻标题", "")),
                        content=str(row.get("新闻内容", "")),
                        publish_time=datetime.now(),
                        source="akshare",
                        latency_ms=latency,
                    )
                )
            return results
        except Exception as e:
            logger.warning(f"AkShareSource.get_news failed: {e}")
            return None


class TushareSource(DataSourceBase):
    def __init__(self, token: Optional[str] = None):
        self._token = token or os.environ.get("TUSHARE_TOKEN", "")
        self._pro: Any = None

    @property
    def pro(self) -> Any:
        if self._pro is None and self._token:
            ts.set_token(self._token)
            self._pro = ts.pro_api()
        return self._pro

    @staticmethod
    def _to_ts_code(symbol: str) -> str:
        if symbol.startswith("6"):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    def get_realtime_quote(self, symbol: str) -> Optional[QuoteResult]:
        return None

    def get_history_kline(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> Optional[KlineResult]:
        t0 = time.monotonic()
        try:
            if self.pro is None:
                return None
            ts_code = self._to_ts_code(symbol)
            df = self.pro.daily(
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if df is None or df.empty:
                return None
            df = df.sort_values("trade_date").reset_index(drop=True)
            df = df.rename(
                columns={
                    "trade_date": "日期",
                    "open": "开盘",
                    "close": "收盘",
                    "high": "最高",
                    "low": "最低",
                    "vol": "成交量",
                    "amount": "成交额",
                }
            )
            return KlineResult(
                symbol=symbol,
                data=df,
                source="tushare",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"TushareSource.get_history_kline failed: {e}")
            return None

    def get_capital_flow(self, symbol: str) -> Optional[CapitalFlowResult]:
        t0 = time.monotonic()
        try:
            if self.pro is None:
                return None
            ts_code = self._to_ts_code(symbol)
            df = self.pro.moneyflow(ts_code=ts_code)
            if df is None or df.empty:
                return None
            r = df.iloc[0]
            buy_elg = float(r.get("buy_elg_vol", 0) or 0)
            sell_elg = float(r.get("sell_elg_vol", 0) or 0)
            return CapitalFlowResult(
                symbol=symbol,
                main_net_inflow=buy_elg - sell_elg,
                main_inflow=buy_elg,
                main_outflow=sell_elg,
                retail_net_inflow=0.0,
                date=date.today(),
                source="tushare",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"TushareSource.get_capital_flow failed: {e}")
            return None

    def get_northbound_flow(self) -> Optional[NorthboundFlowResult]:
        t0 = time.monotonic()
        try:
            if self.pro is None:
                return None
            today = datetime.now().strftime("%Y%m%d")
            df = self.pro.moneyflow_hsgt(start_date=today)
            if df is None or df.empty:
                return None
            r = df.iloc[0]
            north = float(r.get("north_money", 0) or 0)
            return NorthboundFlowResult(
                date=date.today(),
                sh_net_inflow=north,
                sz_net_inflow=0.0,
                total_net_inflow=north,
                source="tushare",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"TushareSource.get_northbound_flow failed: {e}")
            return None

    def get_news(self, symbol: str) -> Optional[list[NewsResult]]:
        return None


class EastMoneySource(DataSourceBase):
    def get_realtime_quote(self, symbol: str) -> Optional[QuoteResult]:
        t0 = time.monotonic()
        try:
            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == symbol]
            if row.empty:
                return None
            r = row.iloc[0]
            return QuoteResult(
                symbol=symbol,
                name=str(r.get("名称", "")),
                price=float(r.get("最新价", 0) or 0),
                change_pct=float(r.get("涨跌幅", 0) or 0),
                volume=float(r.get("成交量", 0) or 0),
                amount=float(r.get("成交额", 0) or 0),
                timestamp=datetime.now(),
                source="eastmoney",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"EastMoneySource.get_realtime_quote failed: {e}")
            return None

    def get_history_kline(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> Optional[KlineResult]:
        t0 = time.monotonic()
        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=symbol,
                period=period,
                start_date=f"{start_date} 09:30:00",
                end_date=f"{end_date} 15:00:00",
                adjust="qfq",
            )
            return KlineResult(
                symbol=symbol,
                data=df,
                source="eastmoney",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"EastMoneySource.get_history_kline failed: {e}")
            return None

    def get_capital_flow(self, symbol: str) -> Optional[CapitalFlowResult]:
        t0 = time.monotonic()
        try:
            market = "sh" if symbol.startswith("6") else "sz"
            df = ak.stock_individual_fund_flow(stock=symbol, market=market)
            if df is None or df.empty:
                return None
            r = df.iloc[0]
            return CapitalFlowResult(
                symbol=symbol,
                main_net_inflow=float(r.get("主力净流入-净额", 0) or 0),
                main_inflow=float(r.get("主力净流入-净流入", 0) or 0),
                main_outflow=float(r.get("主力净流入-净流出", 0) or 0),
                retail_net_inflow=float(r.get("散户净流入-净额", 0) or 0),
                date=date.today(),
                source="eastmoney",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"EastMoneySource.get_capital_flow failed: {e}")
            return None

    def get_northbound_flow(self) -> Optional[NorthboundFlowResult]:
        t0 = time.monotonic()
        try:
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="沪股通")
            sh_flow = 0.0
            if df is not None and not df.empty:
                sh_flow = float(df.iloc[0].get("当日净流入", 0) or 0)

            df2 = ak.stock_hsgt_north_net_flow_in_em(symbol="深股通")
            sz_flow = 0.0
            if df2 is not None and not df2.empty:
                sz_flow = float(df2.iloc[0].get("当日净流入", 0) or 0)

            return NorthboundFlowResult(
                date=date.today(),
                sh_net_inflow=sh_flow,
                sz_net_inflow=sz_flow,
                total_net_inflow=sh_flow + sz_flow,
                source="eastmoney",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.warning(f"EastMoneySource.get_northbound_flow failed: {e}")
            return None

    def get_news(self, symbol: str) -> Optional[list[NewsResult]]:
        t0 = time.monotonic()
        try:
            df = ak.stock_news_em(symbol=symbol)
            if df is None or df.empty:
                return None
            latency = (time.monotonic() - t0) * 1000
            results: list[NewsResult] = []
            for _, row in df.iterrows():
                results.append(
                    NewsResult(
                        symbol=symbol,
                        title=str(row.get("新闻标题", "")),
                        content=str(row.get("新闻内容", "")),
                        publish_time=datetime.now(),
                        source="eastmoney",
                        latency_ms=latency,
                    )
                )
            return results
        except Exception as e:
            logger.warning(f"EastMoneySource.get_news failed: {e}")
            return None


class CacheSource(DataSourceBase):
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DEFAULT_DB_DIR / "cache.db"
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_type TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_lookup ON cache(query_type, symbol)"
            )

    def store(
        self, query_type: str, symbol: str, data: Any, ttl_seconds: int = 3600
    ) -> None:
        expires = datetime.now() + timedelta(seconds=ttl_seconds)
        data_str = self._serialize(data)
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM cache WHERE query_type = ? AND symbol = ?",
                (query_type, symbol),
            )
            conn.execute(
                "INSERT INTO cache (query_type, symbol, data, expires_at) VALUES (?, ?, ?, ?)",
                (query_type, symbol, data_str, expires.isoformat()),
            )

    def _retrieve(self, query_type: str, symbol: str) -> Optional[Any]:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                SELECT data FROM cache
                WHERE query_type = ? AND symbol = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (query_type, symbol),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._deserialize(query_type, row[0])

    def _serialize(self, data: Any) -> str:
        if isinstance(data, list):
            items = []
            for item in data:
                if hasattr(item, "__dataclass_fields__"):
                    d = asdict(item)
                    for k, v in d.items():
                        if isinstance(v, datetime):
                            d[k] = v.isoformat()
                        elif isinstance(v, date):
                            d[k] = v.isoformat()
                    items.append(d)
                else:
                    items.append(item)
            return json.dumps(items, default=str, ensure_ascii=False)
        if hasattr(data, "__dataclass_fields__"):
            d = asdict(data)
            if isinstance(data, KlineResult):
                d["data"] = data.data.to_json(orient="records", force_ascii=False)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
                elif isinstance(v, date):
                    d[k] = v.isoformat()
            return json.dumps(d, default=str, ensure_ascii=False)
        return json.dumps(data, default=str, ensure_ascii=False)

    def _deserialize(self, query_type: str, data_str: str) -> Any:
        try:
            d = json.loads(data_str)
            match query_type:
                case "realtime_quote":
                    if isinstance(d.get("timestamp"), str):
                        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
                    return QuoteResult(**d)
                case "history_kline":
                    df = pd.read_json(d["data"], orient="records")
                    return KlineResult(
                        symbol=d["symbol"],
                        data=df,
                        source=d["source"],
                        latency_ms=d["latency_ms"],
                    )
                case "capital_flow":
                    if isinstance(d.get("date"), str):
                        d["date"] = date.fromisoformat(d["date"])
                    return CapitalFlowResult(**d)
                case "northbound_flow":
                    if isinstance(d.get("date"), str):
                        d["date"] = date.fromisoformat(d["date"])
                    return NorthboundFlowResult(**d)
                case "news":
                    results: list[NewsResult] = []
                    for item in d:
                        if isinstance(item.get("publish_time"), str):
                            item["publish_time"] = datetime.fromisoformat(
                                item["publish_time"]
                            )
                        results.append(NewsResult(**item))
                    return results
                case _:
                    return d
        except Exception as e:
            logger.warning(f"CacheSource._deserialize failed: {e}")
            return None

    def get_realtime_quote(self, symbol: str) -> Optional[QuoteResult]:
        return self._retrieve("realtime_quote", symbol)

    def get_history_kline(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> Optional[KlineResult]:
        return self._retrieve("history_kline", symbol)

    def get_capital_flow(self, symbol: str) -> Optional[CapitalFlowResult]:
        return self._retrieve("capital_flow", symbol)

    def get_northbound_flow(self) -> Optional[NorthboundFlowResult]:
        return self._retrieve("northbound_flow", "")

    def get_news(self, symbol: str) -> Optional[list[NewsResult]]:
        return self._retrieve("news", symbol)


class DataSourceManager:
    def __init__(
        self,
        db_path: Optional[Path] = None,
        tushare_token: Optional[str] = None,
    ):
        self._primary_sources: list[DataSourceBase] = [
            AkShareSource(),
            TushareSource(token=tushare_token),
            EastMoneySource(),
        ]
        self._cache = CacheSource(db_path=db_path)
        self._db_path = db_path or _DEFAULT_DB_DIR / "data_source.db"
        self._init_log_db()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_log_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_type TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    success INTEGER NOT NULL DEFAULT 1,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _log_source(
        self,
        query_type: str,
        symbol: str,
        source: str,
        latency_ms: float,
        success: bool = True,
    ) -> None:
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO source_log (query_type, symbol, source, latency_ms, success) VALUES (?, ?, ?, ?, ?)",
                    (query_type, symbol, source, latency_ms, int(success)),
                )
        except Exception as e:
            logger.warning(f"Failed to log source: {e}")

    def _fallback(self, method_name: str, symbol: str, *args: Any, **kwargs: Any) -> Any:
        for source in self._primary_sources:
            method = getattr(source, method_name, None)
            if method is None:
                continue
            try:
                result = method(*args, **kwargs)
                if result is not None:
                    source_name = type(source).__name__
                    latency = 0.0
                    if hasattr(result, "latency_ms"):
                        latency = result.latency_ms
                    elif isinstance(result, list) and result and hasattr(result[0], "latency_ms"):
                        latency = result[0].latency_ms
                    self._log_source(method_name, symbol, source_name, latency, True)
                    self._cache.store(method_name, symbol, result)
                    return result
            except Exception as e:
                source_name = type(source).__name__
                self._log_source(method_name, symbol, source_name, 0.0, False)
                logger.warning(f"Source {source_name}.{method_name} failed: {e}")
                continue

        cached = getattr(self._cache, method_name)(*args, **kwargs)
        if cached is not None:
            self._log_source(method_name, symbol, "cache", 0.0, True)
            logger.info(f"Using cached data for {method_name}/{symbol}")
            return cached

        raise DataSourceError(f"All data sources failed for {method_name}/{symbol}")

    def get_realtime_quote(self, symbol: str) -> QuoteResult:
        return self._fallback("get_realtime_quote", symbol, symbol)

    def get_history_kline(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> KlineResult:
        return self._fallback(
            "get_history_kline", symbol, symbol, start_date, end_date, period
        )

    def get_capital_flow(self, symbol: str) -> CapitalFlowResult:
        return self._fallback("get_capital_flow", symbol, symbol)

    def get_northbound_flow(self) -> NorthboundFlowResult:
        return self._fallback("get_northbound_flow", "")

    def get_news(self, symbol: str) -> list[NewsResult]:
        return self._fallback("get_news", symbol, symbol)
