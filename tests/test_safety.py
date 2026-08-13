"""Safety gates первого HCO core."""

from __future__ import annotations

import json

from hco.optimizer import ContextOptimizer


def test_side_effecting_result_is_never_optimized(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=10)
    content = json.dumps([{"id": i, "status": "deleted"} for i in range(100)])

    result = optimizer.optimize_tool_result(
        tool_name="delete_profile",
        tool_call_id="call-delete",
        content=content,
        read_only=False,
        session_id="session-a",
    )

    assert result.changed is False
    assert result.content == content


def test_ambiguous_conflicting_matches_force_full_original_fallback(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=10)
    rows = [
        {"id": "EV-1", "publication_date": "2025-09-04", "state": "current"},
        {"id": "EV-2", "publication_date": "2025-09-10", "state": "stale"},
    ] + [{"id": f"EV-{i}", "status": "ok"} for i in range(3, 80)]
    original = json.dumps(rows, ensure_ascii=False)
    compact = optimizer.optimize_tool_result(
        tool_name="read_ledger",
        tool_call_id="call-ledger",
        content=original,
        read_only=True,
        session_id="session-a",
    )
    request = [
        {"role": "user", "content": "Какие publication date current и stale?"},
        {"role": "tool", "tool_call_id": "call-ledger", "content": compact.content},
    ]

    prepared = optimizer.prepare_request(request, session_id="session-a")

    assert prepared.receipt.decision == "full_fallback"
    assert prepared.receipt.coverage_complete is True
    assert prepared.messages[1]["content"] == original


def test_missing_original_produces_incomplete_error_receipt(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=10)
    missing_hash = "a" * 64
    request = [
        {"role": "user", "content": "Найди approval ticket"},
        {"role": "tool", "tool_call_id": "call-x", "content": f"<hco source_hash={missing_hash} />"},
    ]

    prepared = optimizer.prepare_request(request, session_id="session-a")

    assert prepared.messages is request
    assert prepared.receipt.decision == "error"
    assert prepared.receipt.coverage_complete is False


def test_same_input_produces_byte_identical_prepared_messages(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=10)
    rows = [{"id": f"EV-{i:04d}", "approval": f"AP-{i:04d}"} for i in range(100)]
    original = json.dumps(rows)
    compact = optimizer.optimize_tool_result(
        tool_name="search_records",
        tool_call_id="call-1",
        content=original,
        read_only=True,
        session_id="session-a",
    )
    request = [
        {"role": "user", "content": "Какой approval у EV-0050?"},
        {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
    ]

    first = optimizer.prepare_request(request, session_id="session-a")
    second = optimizer.prepare_request(request, session_id="session-a")

    assert json.dumps(first.messages, sort_keys=True) == json.dumps(second.messages, sort_keys=True)
    assert first.receipt == second.receipt
