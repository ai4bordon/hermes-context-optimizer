"""Первый end-to-end контракт HCO."""

from __future__ import annotations

import json

import pytest

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


def test_explicit_id_requires_full_token_boundary(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [
        {"id": "SRC-0440", "value": "wrong near-miss"},
        {"id": "SRC-999", "value": "unrelated"},
    ]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )

    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "Что говорит SRC-044?"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.decision == "full_fallback"


def test_full_fallback_returns_error_when_original_not_restored(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [{"id": "EV-0001", "value": "the-real-original"}]
    original = json.dumps(rows)
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=original, read_only=True, session_id="session-a",
    )

    bare_marker = [{
        "role": "tool",
        "tool_call_id": "call-1",
        "content": f"<hco source_hash={compact.source_hash} />",
    }]
    prepared = optimizer._full_fallback(
        bare_marker, [compact.source_hash], session_id="session-a"
    )

    assert prepared.receipt.decision == "error"
    assert prepared.receipt.coverage_complete is False


def test_explicit_id_does_not_hide_nonadjacent_lexical_facets(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [
        {"id": "SRC-044", "fact": "alpha only"},
        {"id": "EV-001", "fact": "unrelated"},
        {"id": "EV-002", "fact": "more unrelated"},
        {"id": "EV-005", "fact": "critical rollback procedure omega"},
    ]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "Using SRC-044 also provide critical rollback procedure omega"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.decision == "proactive_expand"
    tail = json.dumps(prepared.messages[-1], ensure_ascii=False)
    assert "SRC-044" in tail
    assert "critical rollback procedure omega" in tail


def test_explicit_id_augments_multiple_rare_lexical_facets(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [
        {"id": "SRC-044", "fact": "alpha only"},
        {"id": "EV-001", "fact": "timeout boundary"},
        {"id": "EV-002", "fact": "unrelated"},
        {"id": "EV-003", "fact": "rollback omega"},
    ]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "Using SRC-044 provide timeout and rollback omega"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.decision == "proactive_expand"
    tail = json.dumps(prepared.messages[-1], ensure_ascii=False)
    assert "SRC-044" in tail
    assert "timeout boundary" in tail
    assert "rollback omega" in tail


@pytest.mark.parametrize("shape", [
    "<hco source_hash={hash} />",
    "<hco-compact source_hash={hash}><hco source_hash={hash} />",
    "<hco-compact source_hash={hash}><hco-compact source_hash={hash}>"
    "<hco source_hash={hash} /></hco-compact>",
    "<hco-compact source_hash={hash}><hco source_hash={other} /></hco-compact>",
    "<hco-compact source_hash={hash}><hco source_hash={hash} />"
    "<hco source_hash={hash} /></hco-compact>",
])
def test_malformed_hco_envelopes_fail_closed(tmp_path, shape) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    original = json.dumps([{"id": "EV-0001", "fact": "omega"}])
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=original, read_only=True, session_id="session-a",
    )
    malformed = shape.format(hash=compact.source_hash, other="0" * 64)
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "EV-0001"},
            {"role": "tool", "tool_call_id": "call-1", "content": malformed},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.coverage_complete is False
    assert prepared.receipt.decision in {"error", "upstream_incomplete"}
    assert "<hco-proactive-fragments>" not in json.dumps(prepared.messages)


def test_five_unique_exact_ids_exceed_primary_limit(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [{"id": f"SRC-00{i}", "fact": f"fact-{i}"} for i in range(1, 7)]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "Compare SRC-001 SRC-002 SRC-003 SRC-004 SRC-005"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.decision == "full_fallback"


def test_four_exact_ids_plus_rare_lexical_facet_exceed_primary_limit(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [
        *({"id": f"SRC-00{i}", "fact": f"fact-{i}"} for i in range(1, 5)),
        {"id": "EV-900", "fact": "critical rollback omega"},
    ]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "SRC-001 SRC-002 SRC-003 SRC-004 plus rollback omega"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.decision == "full_fallback"


def test_duplicate_exact_id_does_not_inflate_primary_count(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "hco.sqlite3", min_chars=1)
    rows = [{"id": "SRC-001", "fact": "alpha"}, {"id": "SRC-002", "fact": "beta"}]
    compact = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1",
        content=json.dumps(rows), read_only=True, session_id="session-a",
    )
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "Compare SRC-001 SRC-001 SRC-002"},
            {"role": "tool", "tool_call_id": "call-1", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert prepared.receipt.decision == "proactive_expand"
