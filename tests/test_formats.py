"""Structured payload support beyond JSON arrays."""

from __future__ import annotations

import json

from hco.optimizer import ContextOptimizer


def _optimize(optimizer, content: str):
    return optimizer.optimize_tool_result(
        tool_name="search_files",
        tool_call_id="call-1",
        content=content,
        read_only=True,
        session_id="session-a",
    )


def test_json_object_values_are_segmented_and_retrieved(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=10)
    original = json.dumps(
        {f"service-{i}": {"owner": "ordinary"} for i in range(100)}
        | {"service-50": {"owner": "origin-chat", "state": "RESULT_READY not DONE"}}
    )
    compact = _optimize(optimizer, original)
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "Кто owner у service-50 и какое state?"},
            {"role": "tool", "content": compact.content},
        ],
        session_id="session-a",
    )

    wire = json.dumps(prepared.messages)
    assert compact.changed is True
    assert "origin-chat" in wire
    assert "RESULT_READY not DONE" in wire


def test_jsonl_rows_are_segmented_and_retrieved(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=10)
    rows = [{"id": f"EV-{i:04d}", "status": "ok"} for i in range(100)]
    rows[50]["rollback"] = "C:\\Backups\\snap-50"
    original = "\n".join(json.dumps(row) for row in rows)
    compact = _optimize(optimizer, original)
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "Какой rollback у EV-0050?"},
            {"role": "tool", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert compact.changed is True
    assert "snap-50" in json.dumps(prepared.messages)


def test_plain_text_blocks_are_segmented_and_retrieved(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=10)
    blocks = [f"Record EV-{i:04d}\nStatus: ok" for i in range(100)]
    blocks[50] += "\nApproval ticket: AP-7391"
    original = "\n\n".join(blocks)
    compact = _optimize(optimizer, original)
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": "Какой approval ticket у EV-0050?"},
            {"role": "tool", "content": compact.content},
        ],
        session_id="session-a",
    )

    assert compact.changed is True
    assert "AP-7391" in json.dumps(prepared.messages)
