"""Restart recovery for SQLite WAL store and telemetry."""

from __future__ import annotations

import json

from hco.ledger import TelemetryLedger
from hco.optimizer import ContextOptimizer


def test_restart_recovers_store_and_hash_chained_telemetry(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    telemetry = tmp_path / "telemetry.sqlite3"
    original = json.dumps(
        [{"id":f"EV-{i:04d}","approval":f"AP-{i:04d}"} for i in range(100)]
    )
    first = ContextOptimizer(store_path=store, min_chars=10)
    compact = first.optimize_tool_result(
        tool_name="search_files", tool_call_id="call-1", content=original,
        read_only=True, session_id="session-a",
    )
    ledger1 = TelemetryLedger(telemetry)
    ledger1.append(
        event_type="tool_result", attempt_id="one", decision="compact", data={}
    )

    second = ContextOptimizer(store_path=store, min_chars=10)
    prepared = second.prepare_request(
        [
            {"role":"user","content":"Какой approval у EV-0050?"},
            {"role":"tool","content":compact.content},
        ],
        session_id="session-a",
    )
    ledger2 = TelemetryLedger(telemetry)
    ledger2.append(
        event_type="llm_request", attempt_id="two", decision="proactive_expand", data={}
    )

    assert prepared.receipt.coverage_complete is True
    assert "AP-0050" in json.dumps(prepared.messages)
    assert ledger2.verify() == 2
