"""MCP 只读端点契约测试。"""
import json

from fastapi.testclient import TestClient

from web.api import app

client = TestClient(app)


def _rpc(method, params=None, req_id=1):
    body = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        body["id"] = req_id
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body)


def test_initialize_and_capabilities():
    resp = _rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "0"}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["protocolVersion"]
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"]["name"] == "quantflow-pro"


def test_tools_list_contains_readonly_tools():
    resp = _rpc("tools/list")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert {"get_index_realtime", "get_sector_flow", "get_stock_kline",
            "get_chanlun_structure", "scan_stock_signals",
            "get_institution_holdings", "get_review_board"} <= names
    kline = next(t for t in resp.json()["result"]["tools"] if t["name"] == "get_stock_kline")
    assert kline["inputSchema"]["required"] == ["code"]


def test_notification_returns_202():
    resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp.status_code == 202
    assert resp.text == ""


def test_unknown_method_and_tool():
    resp = _rpc("does/not/exist")
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32601

    resp = _rpc("tools/call", {"name": "drop_database", "arguments": {}})
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32602


def test_tools_call_index(monkeypatch):
    monkeypatch.setattr("src.core.data_source_v2.get_index_realtime",
                        lambda: [{"code": "000001", "name": "上证指数", "price": 3500.0}])
    resp = _rpc("tools/call", {"name": "get_index_realtime", "arguments": {}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["data"]["data"][0]["name"] == "上证指数"


def test_tools_call_invalid_code_is_error():
    resp = _rpc("tools/call", {"name": "get_stock_kline", "arguments": {"code": "abc"}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "6 位" in result["content"][0]["text"]


def test_parse_error_and_get_405():
    resp = client.post("/mcp", content="not-json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32700

    resp = client.get("/mcp")
    assert resp.status_code == 405


def test_tools_call_upstream_error_does_not_leak_internals(monkeypatch):
    """S9-11: 上游异常只回固定文案, str(e) 里的内部路径/上游 URL 不得外泄。"""
    def _boom(*a, **k):
        raise RuntimeError("connect to tcp://10.0.0.1:7709 failed: /app/pylibs/secret.sock")

    monkeypatch.setattr("src.core.data_source_v2.get_kline_mootdx", _boom)
    resp = _rpc("tools/call", {"name": "get_stock_kline", "arguments": {"code": "600519"}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "10.0.0.1" not in text and "/app/pylibs" not in text and "RuntimeError" not in text
    assert "上游数据暂不可用" in text


def test_tools_call_value_error_keeps_validation_message():
    """S9-11: 自产校验文案(ValueError)仍原样回传, 便于 Agent 自纠参数。"""
    resp = _rpc("tools/call", {"name": "get_stock_kline",
                               "arguments": {"code": "600519", "count": 999}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "30-250" in result["content"][0]["text"]


def test_mcp_post_exempt_from_write_limit():
    """S9-02: /mcp 不吃匿名写桶(10/min), 连打 12 次 tools/list 不得 429。"""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    codes = []
    for _ in range(12):
        codes.append(client.post("/mcp", json=body).status_code)
    assert all(c == 200 for c in codes), codes
