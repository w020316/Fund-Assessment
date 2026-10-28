"""fund_nav_source 单元测试(阶段六批次 A,T-A1)。

铁律:
- akshare 一律 mock/打桩,绝不真实网络请求
- 缓存实例指向 tmp_path,不污染 data/ 目录
- 验证:惰性导入失败降级(AkshareUnavailable → None)、披露持仓解析、
  拟合公式对拍、QDII 识别、历史净值备源降级。
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pandas as pd
import pytest

from src.core import fund_nav_source as fns


@pytest.fixture(autouse=True)
def isolated_caches(tmp_path, monkeypatch):
    """将两个 DataCache 指向 tmp_path,隔离测试副作用。"""
    from src.core.cache import DataCache

    monkeypatch.setattr(
        fns, "_holdings_cache",
        DataCache(cache_dir=str(tmp_path / "holdings"), default_ttl=86400),
    )
    monkeypatch.setattr(
        fns, "_nav_history_cache",
        DataCache(cache_dir=str(tmp_path / "navhist"), default_ttl=3600),
    )
    yield


# ============ AkshareUnavailable / 惰性导入 ============


class TestLazyImport:
    def test_import_failure_raises_akshare_unavailable(self):
        """akshare 导入失败 → 抛可识别异常(绝不裸 ImportError)。"""
        with patch.dict("sys.modules", {"akshare": None}):
            with pytest.raises(fns.AkshareUnavailable):
                # 进程内可能已有缓存模块,强制走导入路径
                fns._akshare_module = None
                fns._import_akshare()
        fns._akshare_module = None

    def test_get_fund_holdings_degrades_to_none_on_unavailable(self, tmp_path, monkeypatch):
        """akshare 不可用 → get_fund_holdings 返回 None(降级,不抛异常)。"""
        def _boom():
            raise fns.AkshareUnavailable("akshare 不可用")
        monkeypatch.setattr(fns, "_import_akshare", _boom)
        assert fns.get_fund_holdings("110022") is None

    def test_get_fund_holdings_invalid_code(self):
        """非法代码直接返回 None。"""
        monkey_codes = ["", "abc123", "12345", "1100220"]
        for c in monkey_codes:
            assert fns.get_fund_holdings(c) is None


# ============ 披露持仓解析 ============


class TestGetFundHoldings:
    def test_parse_holdings_latest_quarter(self, monkeypatch):
        """带季度列的 DataFrame → 取最新一期,权重降序截取 topN。"""
        df = pd.DataFrame([
            {"季度": "2026Q2", "股票代码": "600519", "股票名称": "贵州茅台", "占净值比例": "9.85"},
            {"季度": "2026Q2", "股票代码": "000858", "股票名称": "五粮液", "占净值比例": "8.12"},
            {"季度": "2026Q3", "股票代码": "300308", "股票名称": "中际旭创", "占净值比例": "10.20"},
            {"季度": "2026Q3", "股票代码": "002463", "股票名称": "沪电股份", "占净值比例": "7.66"},
        ])

        class _FakeAk:
            @staticmethod
            def fund_portfolio_hold_em(symbol, date):
                assert symbol == "008984"
                return df

        monkeypatch.setattr(fns, "_import_akshare", lambda: _FakeAk)
        info = fns.get_fund_holdings("008984", top=10)
        assert info is not None
        assert info.fund_code == "008984"
        assert info.as_of == "2026Q3"
        codes = [h["stock_code"] for h in info.holdings]
        assert codes == ["300308", "002463"]
        assert info.holdings[0]["weight"] == 10.2

    def test_parse_holdings_no_quarter_column(self, monkeypatch):
        """无季度列(部分 akshare 版本)→ 用 _latest_quarter 推断。"""
        df = pd.DataFrame([
            {"股票代码": "600519", "股票名称": "贵州茅台", "占净值比例": 9.85},
        ])

        class _FakeAk:
            @staticmethod
            def fund_portfolio_hold_em(symbol, date):
                return df

        monkeypatch.setattr(fns, "_import_akshare", lambda: _FakeAk)
        info = fns.get_fund_holdings("110022")
        assert info is not None
        assert info.holdings[0]["stock_code"] == "600519"
        assert info.as_of.endswith(("Q1", "Q2", "Q3", "Q4"))

    def test_holdings_cached_second_call_no_akshare(self, monkeypatch):
        """第二次调用命中缓存,不再触达 akshare。"""
        df = pd.DataFrame([{"股票代码": "600519", "股票名称": "贵州茅台", "占净值比例": 9.85}])
        calls = {"n": 0}

        class _FakeAk:
            @staticmethod
            def fund_portfolio_hold_em(symbol, date):
                calls["n"] += 1
                return df

        monkeypatch.setattr(fns, "_import_akshare", lambda: _FakeAk)
        assert fns.get_fund_holdings("110022") is not None
        assert fns.get_fund_holdings("110022") is not None
        assert calls["n"] == 1

    def test_holdings_empty_df_returns_none(self, monkeypatch):
        class _FakeAk:
            @staticmethod
            def fund_portfolio_hold_em(symbol, date):
                return pd.DataFrame()

        monkeypatch.setattr(fns, "_import_akshare", lambda: _FakeAk)
        assert fns.get_fund_holdings("110022") is None

    def test_holdings_interface_error_returns_none(self, monkeypatch):
        """接口抛异常(字段变更/限流)→ 返回 None,不向外传播。"""
        class _FakeAk:
            @staticmethod
            def fund_portfolio_hold_em(symbol, date):
                raise RuntimeError("接口被限流")

        monkeypatch.setattr(fns, "_import_akshare", lambda: _FakeAk)
        assert fns.get_fund_holdings("110022") is None


# ============ 估值拟合公式(手算对拍) ============


class TestFitEstimate:
    def _holdings(self):
        return fns.FundHoldings(fund_code="008984", as_of="2026Q3", holdings=[
            {"stock_code": "300308", "stock_name": "中际旭创", "weight": 10.0},
            {"stock_code": "002463", "stock_name": "沪电股份", "weight": 5.0},
        ])

    def test_fit_full_coverage(self):
        """全覆盖: est = 0.10*2 + 0.05*(-1) = 0.15%。"""
        got = fns.fit_estimate_from_holdings(self._holdings(), {"300308": 2.0, "002463": -1.0}, 0.0)
        assert got is not None
        est, coverage = got
        assert est == pytest.approx(0.15, abs=1e-9)
        assert coverage == pytest.approx(0.15, abs=1e-9)

    def test_fit_with_index_fill(self):
        """未覆盖 85% 用基准指数填充: est = 0.15 + 0.85*1.0 = 1.0%。"""
        got = fns.fit_estimate_from_holdings(self._holdings(), {"300308": 2.0, "002463": -1.0}, 1.0)
        assert got is not None
        est, coverage = got
        assert est == pytest.approx(0.15 + 0.85 * 1.0, abs=1e-9)
        assert coverage == pytest.approx(0.15, abs=1e-9)

    def test_fit_partial_stock_missing(self):
        """个股涨跌缺失 → 该权重并入未覆盖部分用指数填充。"""
        got = fns.fit_estimate_from_holdings(self._holdings(), {"300308": 2.0}, 0.5)
        assert got is not None
        est, coverage = got
        # est = 0.10*2 + 0.90*0.5 = 0.65
        assert est == pytest.approx(0.65, abs=1e-9)
        assert coverage == pytest.approx(0.10, abs=1e-9)

    def test_fit_insufficient_data_returns_none(self):
        """持仓为空 / 全部个股缺涨跌 → None(数据不足,不编数据)。"""
        assert fns.fit_estimate_from_holdings(None, {"300308": 2.0}) is None
        assert fns.fit_estimate_from_holdings(self._holdings(), {}) is None


# ============ QDII 识别 ============


class TestQdii:
    @pytest.mark.parametrize("name,expected", [
        ("华夏移动互联混合(QDII)", True),
        ("qdii global fund", True),
        ("易方达消费行业", False),
        ("", False),
    ])
    def test_is_qdii(self, name, expected):
        assert fns.is_qdii(name) is expected


# ============ 历史净值备源 ============


class TestNavHistory:
    def test_main_source_success(self, monkeypatch):
        """主源(东财)成功 → 直接返回,不走 akshare。"""
        main = [{"date": "2026-10-23", "nav": 2.0, "acc_nav": 3.0, "change_pct": 1.0}]
        with patch("src.core.data_source_v2.get_fund_history_tencent", return_value=main):
            out = fns.get_fund_nav_history("110022", "3m")
        assert out == main

    def test_main_source_sorted_ascending(self, monkeypatch):
        """主源返回顺序不可信 → 统一升序(风险计算依赖时间方向)。"""
        main = [
            {"date": "2026-10-23", "nav": 2.0, "acc_nav": 3.0, "change_pct": 1.0},
            {"date": "2026-10-22", "nav": 1.9, "acc_nav": 2.9, "change_pct": -1.0},
        ]
        with patch("src.core.data_source_v2.get_fund_history_tencent", return_value=main):
            out = fns.get_fund_nav_history("110022", "3m")
        assert [r["date"] for r in out] == ["2026-10-22", "2026-10-23"]

    def test_fallback_filters_period_window(self, monkeypatch):
        """akshare 备源返回全量历史 → 本地裁剪到 period 窗口内。"""
        today = dt.date.today()
        old = (today - dt.timedelta(days=400)).isoformat()
        recent = (today - dt.timedelta(days=30)).isoformat()
        latest = (today - dt.timedelta(days=29)).isoformat()
        df = pd.DataFrame([
            {"净值日期": old, "累计净值": 1.0},
            {"净值日期": latest, "累计净值": 1.111},
            {"净值日期": recent, "累计净值": 1.1},
        ])

        class _FakeAk:
            @staticmethod
            def fund_open_fund_info_em(symbol, indicator):
                assert indicator == "累计净值"
                return df

        with patch("src.core.data_source_v2.get_fund_history_tencent", return_value=[]):
            monkeypatch.setattr(fns, "_import_akshare", lambda: _FakeAk)
            out = fns.get_fund_nav_history("110022", "3m")
        assert [r["date"] for r in out] == [recent, latest]
        assert out[1]["change_pct"] == pytest.approx(1.0, abs=0.01)

    def test_fallback_to_akshare(self, monkeypatch):
        """主源空 → 备源 akshare 累计净值,升序输出并推导涨跌。"""
        df = pd.DataFrame([
            {"净值日期": "2026-10-22", "累计净值": 3.0},
            {"净值日期": "2026-10-23", "累计净值": 3.03},
        ])

        class _FakeAk:
            @staticmethod
            def fund_open_fund_info_em(symbol, indicator):
                assert indicator == "累计净值"
                return df

        with patch("src.core.data_source_v2.get_fund_history_tencent", return_value=[]):
            monkeypatch.setattr(fns, "_import_akshare", lambda: _FakeAk)
            out = fns.get_fund_nav_history("110022", "3m")
        assert len(out) == 2
        assert out[0]["date"] == "2026-10-22"
        assert out[1]["change_pct"] == pytest.approx(1.0, abs=0.01)

    def test_all_sources_fail_returns_empty(self, monkeypatch):
        """主备源全失败 → [](不抛异常,调用方标 degraded)。"""
        with patch("src.core.data_source_v2.get_fund_history_tencent", return_value=[]):
            monkeypatch.setattr(
                fns, "_import_akshare",
                lambda: (_ for _ in ()).throw(fns.AkshareUnavailable("unavailable")),
            )
            assert fns.get_fund_nav_history("110022") == []

    def test_invalid_code_returns_empty(self):
        assert fns.get_fund_nav_history("") == []
        assert fns.get_fund_nav_history("abc") == []
