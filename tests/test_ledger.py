"""Immutable telemetry ledger contract."""

from __future__ import annotations

import json
import sqlite3

import pytest

from hco.ledger import LedgerIntegrityError, TelemetryLedger


def test_ledger_appends_hash_chained_events_and_verifies(tmp_path) -> None:
    ledger = TelemetryLedger(tmp_path / "ledger.jsonl")

    first = ledger.append(
        event_type="tool_compact",
        attempt_id="attempt-1",
        decision="compact",
        data={"source_hash": "a" * 64},
    )
    second = ledger.append(
        event_type="request_prepare",
        attempt_id="attempt-1",
        decision="proactive_expand",
        data={"selected_fragment_ids": ["row-000150"]},
    )

    assert first["previous_event_hash"] == "0" * 64
    assert second["previous_event_hash"] == first["event_hash"]
    assert ledger.verify() == 2


def test_ledger_detects_tampering(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = TelemetryLedger(path)
    ledger.append(
        event_type="request_prepare",
        attempt_id="attempt-1",
        decision="passthrough",
        data={},
    )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT sequence, event_json FROM events ORDER BY sequence LIMIT 1"
        ).fetchone()
        event = json.loads(row[1])
        event["decision"] = "forged"
        connection.execute(
            "UPDATE events SET event_json = ? WHERE sequence = ?",
            (json.dumps(event, sort_keys=True, separators=(",", ":")), row[0]),
        )
        connection.commit()

    with pytest.raises(LedgerIntegrityError):
        ledger.verify()
