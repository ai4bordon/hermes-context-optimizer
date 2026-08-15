"""Первый end-to-end контракт HCO."""

from __future__ import annotations

import json

from hco.optimizer import ContextOptimizer


def records() -> list[dict[str, object]]:
    rows = [
        {
            "id": f"EV-{i:04d}",
            "status": "ok",
            "message": "Routine operation completed within normal parameters",
        }
        for i in range(300)
    ]
    rows[150]["message"] = (
        "Approval ticket TINY-APPROVAL-7429 is required before deleting profile"
    )
    return rows


def test_large_json_is_compacted_then_proactively_expanded_from_user_query(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1_000)
    original = json.dumps(records(), ensure_ascii=False)

    compact = optimizer.optimize_tool_result(
        tool_name="search_records",
        tool_call_id="call-1",
        content=original,
        read_only=True,
        session_id="session-a",
    )

    assert compact.changed is True
    assert "TINY-APPROVAL-7429" not in compact.content
    assert compact.source_hash in compact.content

    request = [
        {"role": "system", "content": "Не выдумывай."},
        {"role": "user", "content": "Какой approval ticket нужен перед удалением профиля?"},
        {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
    ]
    expanded = optimizer.prepare_request(request, session_id="session-a")

    wire = json.dumps(expanded.messages, ensure_ascii=False)
    assert "TINY-APPROVAL-7429" in wire
    assert "EV-0150" in wire
    assert "row-000150" not in wire
    assert '"position"' not in wire
    assert expanded.receipt.decision == "proactive_expand"
    assert expanded.receipt.coverage_complete is True
    assert expanded.receipt.source_hashes == (compact.source_hash,)
    assert len(wire) < len(original) * 0.20


def test_no_marker_is_byte_equivalent_noop(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1_000)
    messages = [{"role": "user", "content": "hello"}]

    expanded = optimizer.prepare_request(messages, session_id="session-a")

    assert expanded.messages is messages
    assert expanded.receipt.decision == "passthrough"


def test_lexical_selection_normalizes_length_and_includes_neighbors(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [
        {"text": "setup context before target"},
        {"text": "database timeout retry policy"},
        {"text": "cleanup context after target"},
        {"text": ("database timeout " + "unrelated filler " * 200)},
        {"text": "other material"},
    ]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )

    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "What is the database timeout retry policy?"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )
    wire = json.dumps(prepared.messages)
    injected = prepared.messages[-1]["content"]

    assert prepared.receipt.decision == "proactive_expand"
    assert "database timeout retry policy" in wire
    assert "setup context before target" in wire
    assert "cleanup context after target" in wire
    assert "unrelated filler" not in injected


def test_partial_lexical_facet_match_falls_back_to_full_original(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [
        {"text": "database inventory only"},
        {"text": "unrelated material"},
        {"text": "more unrelated material"},
    ]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )

    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "database timeout retry policy"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.decision == "full_fallback"


def test_selected_fragments_must_cover_multiple_lexical_facets(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [
        {"text": "alpha alpha alpha alpha decisive record"},
        {"text": "neighbor"},
        {"text": "unrelated"},
        {"text": "more unrelated"},
        {"text": "beta secondary facet"},
    ]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )

    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "alpha beta"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.decision == "full_fallback"
