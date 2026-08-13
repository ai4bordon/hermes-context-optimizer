"""Multiple-marker full fallback preserves every original and surrounding content."""

from __future__ import annotations

import json

from hco.optimizer import ContextOptimizer


def test_full_fallback_expands_all_markers_without_losing_surrounding_content(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=10)
    first_original = json.dumps([{"id": f"A-{i}", "state": "alpha"} for i in range(50)])
    second_original = json.dumps([{"id": f"B-{i}", "state": "beta"} for i in range(50)])
    first = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-a", content=first_original,
        read_only=True, session_id="session-a",
    )
    second = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-b", content=second_original,
        read_only=True, session_id="session-a",
    )
    mixed = f"prefix\n{first.content}\nbetween\n{second.content}\nsuffix"
    request = [
        {"role": "user", "content": "Что здесь неизвестно?"},
        {"role": "tool", "content": mixed},
    ]

    prepared = optimizer.prepare_request(request, session_id="session-a")

    content = prepared.messages[1]["content"]
    assert prepared.receipt.decision == "full_fallback"
    assert first_original in content
    assert second_original in content
    assert "prefix" in content
    assert "between" in content
    assert "suffix" in content
    assert "<hco source_hash=" not in content
