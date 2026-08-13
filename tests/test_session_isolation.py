"""Session isolation contracts for the HCO original store."""

from __future__ import annotations

import json

from hco.optimizer import ContextOptimizer


def _request(compact: str) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "Какой approval у EV-0050?"},
        {"role": "tool", "content": compact},
    ]


def test_identical_content_is_retrievable_in_two_sessions(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=10)
    rows = [{"id": f"EV-{i:04d}", "approval": f"AP-{i:04d}"} for i in range(100)]
    original = json.dumps(rows)

    compact_a = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-a", content=original,
        read_only=True, session_id="session-a",
    )
    compact_b = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-b", content=original,
        read_only=True, session_id="session-b",
    )

    prepared_a = optimizer.prepare_request(_request(compact_a.content), session_id="session-a")
    prepared_b = optimizer.prepare_request(_request(compact_b.content), session_id="session-b")

    assert prepared_a.receipt.coverage_complete is True
    assert prepared_b.receipt.coverage_complete is True
    assert "AP-0050" in json.dumps(prepared_a.messages)
    assert "AP-0050" in json.dumps(prepared_b.messages)


def test_source_from_other_session_is_not_retrievable(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=10)
    original = json.dumps([{"id": f"EV-{i:04d}", "approval": f"AP-{i:04d}"} for i in range(100)])
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-a", content=original,
        read_only=True, session_id="session-a",
    )

    prepared = optimizer.prepare_request(_request(compact.content), session_id="session-b")

    assert prepared.receipt.decision == "error"
    assert prepared.receipt.coverage_complete is False
    assert "AP-0050" not in json.dumps(prepared.messages)
