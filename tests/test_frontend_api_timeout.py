"""C-05(2026-11-17):前端超时映射必须覆盖 diversification。

根因: `/api/portfolio/diversification` 未进 `API_TIMEOUT_MS` 分档, 落到默认 15s。
该端点逐只基金拉历史净值(信号量并发), 与 `/api/portfolio/risk` 同源同量级 ——
后者早已显式给 30s。持仓 ≥3 只时前端先 abort 报"网络错误", 后端仍在跑,
用户重试反而叠加上游压力。

验证方式(刻意不用"源码子串断言"):把 `API_TIMEOUT_MS` 函数体原样抽出来交给
node 执行, 用真实入参断言返回值。没有 node 则 skip(汇总里会报是否跳过)。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web/static/index.html"

NODE = shutil.which("node")
SIGNATURE = "const API_TIMEOUT_MS = (url) => {"


def _api_timeout_source() -> str:
    """按花括号配平截取 `API_TIMEOUT_MS` 的完整定义(含 const 前缀)。"""
    text = INDEX.read_text(encoding="utf-8")
    start = text.index(SIGNATURE)
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1] + ";"
    raise AssertionError("API_TIMEOUT_MS 花括号未配平")


def _timeouts(urls: list[str]) -> list[int]:
    script = (
        _api_timeout_source()
        + "\nconsole.log(JSON.stringify(" + json.dumps(urls) + ".map(API_TIMEOUT_MS)));"
    )
    proc = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert proc.returncode == 0, f"node 执行失败: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(NODE is None, reason="需要 node 才能执行前端超时映射")
def test_diversification_gets_thirty_seconds():
    """diversification 必须命中 30000 分支, 而不是默认 15000。"""
    got = _timeouts(["/api/portfolio/diversification?window=1y"])

    assert got[0] == 30000, "diversification 落回默认 15s → 持仓多只时前端先 abort"


@pytest.mark.skipif(NODE is None, reason="需要 node 才能执行前端超时映射")
def test_other_slow_endpoints_unchanged():
    """回归保护: risk / attribution 仍是 30s, 扫描仍是 25s, 普通行情仍 15s。"""
    got = _timeouts([
        "/api/portfolio/risk?window=1y",
        "/api/portfolio/attribution?period=mtd",
        "/api/strategy/scan/limit_up",
        "/api/market/index_realtime?_t=1",
    ])

    assert got == [30000, 30000, 25000, 15000]
