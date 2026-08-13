"""Secret-bearing payloads must bypass HCO storage entirely."""

from __future__ import annotations

import json
import sqlite3

from hco.optimizer import ContextOptimizer


def test_credential_value_bypasses_compaction_and_is_not_persisted(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=store, min_chars=10)
    secret = "sk-live-super-secret-1234567890"
    original = json.dumps(
        [{"id": i, "status": "ok"} for i in range(100)]
        + [{"MODEL_API_KEY": secret}]
    )

    result = optimizer.optimize_tool_result(
        tool_name="read_file",
        tool_call_id="call-secret",
        content=original,
        read_only=True,
        session_id="session-a",
    )

    assert result.changed is False
    assert result.content == original
    with sqlite3.connect(store) as connection:
        stored = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    assert stored == 0
    assert secret not in store.read_bytes().decode("utf-8", errors="ignore")


def test_secret_boundary_policy_text_without_value_remains_optimizable(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=10)
    original = json.dumps(
        [{"id": i, "policy": "never print MODEL_API_KEY into reports"} for i in range(100)]
    )

    result = optimizer.optimize_tool_result(
        tool_name="read_file",
        tool_call_id="call-policy",
        content=original,
        read_only=True,
        session_id="session-a",
    )

    assert result.changed is True
