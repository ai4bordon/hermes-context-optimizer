"""Контракт расширенной telemetry и dashboard snapshot."""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from hco.dashboard import build_snapshot, create_server
from hco.hermes_plugin import HCOMiddleware


def test_telemetry_records_payload_sizes_and_usage(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=10,
    )
    original = json.dumps([{"id": f"EV-{i:04d}", "fact": "x" * 40} for i in range(100)])
    compact = middleware.tool_execution(
        next_call=lambda _: original,
        tool_name="search_files",
        args={},
        session_id="session-a",
        tool_call_id="call-1",
        api_request_id="run-1",
        model="cx/gpt-5.6-luna",
        provider="anymodel",
    )
    middleware.llm_request(
        request={
            "model": "cx/gpt-5.6-luna",
            "provider": "anymodel",
            "messages": [
                {"role": "user", "content": "Find EV-0050"},
                {"role": "tool", "content": compact},
            ],
        },
        session_id="session-a",
        api_request_id="run-1",
        provider_usage={"prompt_tokens": 123, "completion_tokens": 17, "total_tokens": 140},
    )

    snapshot = build_snapshot(tmp_path / "telemetry.sqlite3", tmp_path / "store.sqlite3")
    assert snapshot["schema_version"] == "hco.telemetry.v2"
    assert snapshot["summary"]["tool_results"] == 1
    assert snapshot["summary"]["llm_requests"] == 1
    assert snapshot["summary"]["actual_tokens"]["total"] == 140
    assert snapshot["events"][0]["data"]["original_chars"] > snapshot["events"][0]["data"]["wire_chars"]


def test_dashboard_marks_missing_usage_unknown(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=10,
    )
    middleware.llm_request(
        request={"messages": [{"role": "user", "content": "hello"}]},
        session_id="s",
        api_request_id="r",
    )
    snapshot = build_snapshot(tmp_path / "telemetry.sqlite3", tmp_path / "store.sqlite3")
    assert snapshot["summary"]["actual_tokens"]["total"] is None
    assert snapshot["summary"]["actual_tokens"]["status"] == "unknown"


def test_dashboard_is_read_only_and_returns_recent_events(tmp_path) -> None:
    snapshot = build_snapshot(tmp_path / "missing.sqlite3", tmp_path / "missing-store.sqlite3")
    assert snapshot["events"] == []
    assert snapshot["summary"]["tool_results"] == 0
    assert snapshot["summary"]["stored_sources"] == 0


def test_dashboard_serves_local_health_and_snapshot(tmp_path) -> None:
    server = create_server(tmp_path / "telemetry.sqlite3", tmp_path / "store.sqlite3", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz").read() == b'{"status":"ok"}'
        snapshot = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot").read())
        assert snapshot["read_only"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_dashboard_refuses_non_localhost_bind(tmp_path) -> None:
    with pytest.raises(ValueError, match="localhost-only"):
        create_server(tmp_path / "telemetry.sqlite3", tmp_path / "store.sqlite3", host="0.0.0.0")
