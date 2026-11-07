"""每日复盘报告生成脚本(scripts/gen_daily_report.py)单元测试。

全 mock, 不触网。覆盖:
- 交易日守卫: 探针基金 update_time=今天 → 放行; =昨天/无数据 → 跳过(exit 0 且不写文件)
- 渲染: mock 各数据源 → HTML 含指数名/基金代码/红涨绿跌 class
- 单源失败容错: 某数据源抛异常 → 报告仍生成且含"数据源未覆盖"
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

# 让 pytest 可以导入 scripts/ 下的独立脚本(脚本自身也带 sys.path 引导, 双保险)
_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_SCRIPTS), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gen_daily_report as gdr  # noqa: E402
from src.core import data_health  # noqa: E402
from src.core import data_source_v2 as ds2  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_health_state():
    """隔离 data_health 全局状态与数据源模块级 TTL 缓存(参照 test_data_health.py)。"""
    data_health.reset()
    ds2._cache.clear()
    yield
    data_health.reset()
    ds2._cache.clear()


def _guard_row(update_time: str) -> list[dict]:
    return [{
        "code": "018957",
        "name": "守卫探针基金",
        "nav": 1.2345,
        "estimated_nav": 1.2400,
        "change": 0.0055,
        "change_pct": 0.45,
        "update_time": update_time,
        "source": "tencent",
    }]


class TestTradingDayGuard:
    def test_today_update_time_passes_guard(self, monkeypatch):
        today = gdr.beijing_now().strftime("%Y-%m-%d")
        monkeypatch.setattr(
            gdr.ds2, "get_fund_realtime_tencent",
            lambda codes: _guard_row(f"{today} 15:00:00"),
        )
        assert gdr.check_trading_day() is True

    def test_yesterday_update_time_fails_guard(self, monkeypatch):
        yesterday = (gdr.beijing_now() - timedelta(days=1)).strftime("%Y-%m-%d")
        monkeypatch.setattr(
            gdr.ds2, "get_fund_realtime_tencent",
            lambda codes: _guard_row(f"{yesterday} 15:00:00"),
        )
        assert gdr.check_trading_day() is False

    def test_empty_probe_result_fails_guard(self, monkeypatch):
        monkeypatch.setattr(gdr.ds2, "get_fund_realtime_tencent", lambda codes: [])
        assert gdr.check_trading_day() is False

    def test_main_skips_file_on_non_trading_day(self, monkeypatch, tmp_path):
        yesterday = (gdr.beijing_now() - timedelta(days=1)).strftime("%Y-%m-%d")
        monkeypatch.setattr(
            gdr.ds2, "get_fund_realtime_tencent",
            lambda codes: _guard_row(f"{yesterday} 15:00:00"),
        )
        out = tmp_path / "report.html"
        assert gdr.main(["--out", str(out)]) == 0
        assert not out.exists()


class TestRender:
    def _mock_all_sources(self, monkeypatch):
        monkeypatch.setattr(gdr.ds2, "get_index_realtime", lambda: [
            {"code": "000001", "name": "上证指数", "price": 3250.12,
             "change": 10.01, "change_pct": 0.31, "volume": 1000, "amount": 2000},
            {"code": "399001", "name": "深证成指", "price": 10400.50,
             "change": -19.75, "change_pct": -0.19, "volume": 900, "amount": 1800},
        ])
        monkeypatch.setattr(gdr.ds2, "get_market_sentiment", lambda: {
            "rise_count": 3000, "fall_count": 2000, "flat_count": 300,
            "sentiment": "偏多", "ratio": 0.568,
        })
        monkeypatch.setattr(gdr.ds2, "get_sector_ranking", lambda: [
            {"code": "BK0001", "name": "半导体", "change_pct": 3.20},
            {"code": "BK0002", "name": "银行", "change_pct": -1.10},
        ])
        # 注意: 此处不 patch get_fund_realtime_tencent —— 守卫与基金区块
        # 各测试自行按需 patch(需要控制 update_time 的日期)。

    def test_main_writes_report_on_trading_day(self, monkeypatch, tmp_path):
        today = gdr.beijing_now().strftime("%Y-%m-%d")

        def _fake_fund(codes):
            return [{
                "code": c, "name": f"基金{c}", "nav": 1.5, "estimated_nav": 1.51,
                "change": 0.01, "change_pct": 0.67,
                "update_time": f"{today} 15:00:00", "source": "tencent",
            } for c in codes]

        monkeypatch.setattr(gdr.ds2, "get_fund_realtime_tencent", _fake_fund)
        self._mock_all_sources(monkeypatch)

        out = tmp_path / "report.html"
        assert gdr.main(["--out", str(out)]) == 0
        html_text = out.read_text(encoding="utf-8")
        # 指数名与基金代码必须出现
        assert "上证指数" in html_text
        assert "002891" in html_text
        # 红涨绿跌 class: 0.31% → up, -0.19% → down
        assert 'class="up"' in html_text
        assert 'class="down"' in html_text
        # 风险提示必备
        assert "不构成投资建议" in html_text

    def test_single_source_failure_shows_placeholder(self, monkeypatch, tmp_path):
        today = gdr.beijing_now().strftime("%Y-%m-%d")
        monkeypatch.setattr(gdr.ds2, "get_fund_realtime_tencent", lambda codes: [
            {"code": c, "name": f"基金{c}", "nav": 1.5, "estimated_nav": 1.51,
             "change": 0.0, "change_pct": 0.0,
             "update_time": f"{today} 15:00:00", "source": "tencent"}
            for c in codes
        ])

        def _boom(*args, **kwargs):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(gdr.ds2, "get_index_realtime", _boom)
        self._mock_rest_sources(monkeypatch)

        out = tmp_path / "report.html"
        assert gdr.main(["--out", str(out)]) == 0
        html_text = out.read_text(encoding="utf-8")
        assert "数据源未覆盖" in html_text
        # 其余正常区块仍应渲染
        assert "002891" in html_text

    def _mock_rest_sources(self, monkeypatch):
        monkeypatch.setattr(gdr.ds2, "get_market_sentiment", lambda: {
            "rise_count": 1500, "fall_count": 1500, "flat_count": 200,
            "sentiment": "中性", "ratio": 0.5,
        })
        monkeypatch.setattr(gdr.ds2, "get_sector_ranking", lambda: [
            {"code": "BK0001", "name": "半导体", "change_pct": 2.0},
        ])


class TestNews:
    """财经快讯区块: 端到端渲染 + 数据源失败占位。"""

    def _mock_funds_today(self, monkeypatch):
        today = gdr.beijing_now().strftime("%Y-%m-%d")
        monkeypatch.setattr(gdr.ds2, "get_fund_realtime_tencent", lambda codes: [
            {"code": c, "name": f"基金{c}", "nav": 1.5, "estimated_nav": 1.51,
             "change": 0.0, "change_pct": 0.0,
             "update_time": f"{today} 15:00:00", "source": "tencent"}
            for c in codes
        ])

    def _mock_indices(self, monkeypatch):
        monkeypatch.setattr(gdr.ds2, "get_index_realtime", lambda: [
            {"code": "000001", "name": "上证指数", "price": 3250.12,
             "change": 0.0, "change_pct": 0.0, "volume": 1000, "amount": 2000},
        ])
        monkeypatch.setattr(gdr.ds2, "get_market_sentiment", lambda: {
            "rise_count": 100, "fall_count": 100, "flat_count": 10,
            "sentiment": "中性", "ratio": 0.5,
        })
        monkeypatch.setattr(gdr.ds2, "get_sector_ranking", lambda: [
            {"code": "BK0001", "name": "半导体", "change_pct": 1.0},
        ])

    def test_news_rendered_in_report(self, monkeypatch, tmp_path):
        """mock get_global_news 返回构造数据 → HTML 含新闻标题与来源(含可点击链接)。"""
        self._mock_funds_today(monkeypatch)
        self._mock_indices(monkeypatch)
        monkeypatch.setattr(gdr.ds2, "get_global_news", lambda: [
            None,  # 单条结构异常: 应被跳过, 不影响整体
            {"title": "", "content": "无标题条目应跳过", "source": "X",
             "publish_time": "2026-01-01 10:00", "url": "https://x.com/1"},
            {"title": "央行开展逆回购操作", "content": "流动性维持合理充裕，规模超上期。",
             "source": "东方财富", "publish_time": "2026-01-01 09:30",
             "url": "https://finance.example.com/news/1"},
            {"title": "某指数尾盘拉升", "content": "c" * 200,  # 摘要超长应被截断
             "source": "", "publish_time": "", "url": ""},
        ])

        out = tmp_path / "report.html"
        assert gdr.main(["--out", str(out)]) == 0
        html_text = out.read_text(encoding="utf-8")
        # 区块标题与新闻标题、来源必须出现
        assert "财经快讯" in html_text
        assert "央行开展逆回购操作" in html_text
        assert "东方财富" in html_text
        # url 非空 → <a href target=_blank> 可点击
        assert 'href="https://finance.example.com/news/1" target="_blank"' in html_text
        # publish_time 为空 → 时间列显示占位 "—"
        assert "<td>—</td>" in html_text
        # 摘要截断到 80 字
        assert "c" * 80 in html_text
        assert "c" * 81 not in html_text
        # 结构异常与空标题条目被跳过
        assert "无标题条目应跳过" not in html_text

    def test_news_source_failure_shows_placeholder(self, monkeypatch, tmp_path):
        """mock get_global_news 抛异常 → 报告仍生成且快讯区块显示占位。"""
        self._mock_funds_today(monkeypatch)
        self._mock_indices(monkeypatch)

        def _boom(*args, **kwargs):
            raise RuntimeError("news upstream down")

        monkeypatch.setattr(gdr.ds2, "get_global_news", _boom)

        out = tmp_path / "report.html"
        assert gdr.main(["--out", str(out)]) == 0
        html_text = out.read_text(encoding="utf-8")
        assert "财经快讯" in html_text
        assert "数据源未覆盖" in html_text
        # 其余正常区块仍应渲染
        assert "002891" in html_text
