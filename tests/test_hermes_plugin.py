"""Hermes middleware adapter contract (tested without live installation)."""

from __future__ import annotations

import json

from hco.hermes_plugin import HCOMiddleware


def test_tool_execution_middleware_compacts_read_only_result(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "hco.sqlite3",
        ledger_path=tmp_path / "ledger.jsonl",
        min_chars=10,
    )
    original = json.dumps([{"id": f"EV-{i:04d}", "status": "ok"} for i in range(100)])

    result = middleware.tool_execution(
        next_call=lambda args: original,
        tool_name="search_files",
        args={"pattern": "EV"},
        session_id="session-a",
        tool_call_id="call-1",
        api_request_id="request-1",
    )

    assert "<hco source_hash=" in result
    assert len(result) < len(original)


def test_llm_request_middleware_expands_request_and_attaches_internal_receipt(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "hco.sqlite3",
        ledger_path=tmp_path / "ledger.jsonl",
        min_chars=10,
    )
    rows = [{"id": f"EV-{i:04d}", "approval": f"AP-{i:04d}"} for i in range(100)]
    compact = middleware.tool_execution(
        next_call=lambda args: json.dumps(rows),
        tool_name="search_files",
        args={},
        session_id="session-a",
        tool_call_id="call-1",
        api_request_id="request-1",
    )
    request = {
        "model": "test-model",
        "messages": [
            {"role": "user", "content": "Какой approval у EV-0050?"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact},
        ],
    }

    result = middleware.llm_request(
        request=request,
        session_id="session-a",
        api_request_id="request-2",
    )

    assert "AP-0050" in json.dumps(result["request"]["messages"])
    receipt = result["trace"]["coverage_receipt"]
    assert receipt["coverage_complete"] is True
    assert receipt["decision"] == "proactive_expand"
    # Middleware metadata is returned outside provider kwargs.
    assert "trace" not in result["request"]


def test_incomplete_coverage_marks_request_blocked(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "hco.sqlite3",
        ledger_path=tmp_path / "ledger.jsonl",
        min_chars=10,
        strict=True,
    )
    request = {
        "messages": [
            {"role": "user", "content": "Найди ticket"},
            {"role": "tool", "content": f"<hco source_hash={'a' * 64} />"},
        ]
    }

    result = middleware.llm_request(
        request=request,
        session_id="session-a",
        api_request_id="request-1",
    )

    assert result["blocked"] is True
    assert result["decision"] == "block"
    assert result["reason"] == "coverage_incomplete"
    assert result["receipt"]["coverage_complete"] is False
    assert result["trace"]["coverage_receipt"]["coverage_complete"] is False
