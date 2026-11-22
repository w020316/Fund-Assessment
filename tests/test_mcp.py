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
