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


def test_ledger_retries_transient_wal_initialization_lock(tmp_path, monkeypatch) -> None:
    from hco import ledger as ledger_module

    real_connect = sqlite3.connect
    wal_attempts = 0

    class ConnectionProxy:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, parameters=()):
            nonlocal wal_attempts
            if sql == "PRAGMA journal_mode=WAL":
                wal_attempts += 1
                if wal_attempts == 1:
                    raise sqlite3.OperationalError("database is locked")
            return self._connection.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

    def locked_once_connect(*args, **kwargs):
        return ConnectionProxy(real_connect(*args, **kwargs))

    monkeypatch.setattr(ledger_module.sqlite3, "connect", locked_once_connect)

    ledger = TelemetryLedger(tmp_path / "ledger.sqlite3")

    assert wal_attempts == 2
    assert ledger.verify() == 0


def test_ledger_reports_compression_and_fallback_rates(tmp_path) -> None:
    ledger = TelemetryLedger(tmp_path / "ledger.sqlite3")
    for decision in ("compact", "passthrough"):
        ledger.append(event_type="tool_result", attempt_id=decision, decision=decision, data={})
    for decision in ("proactive_expand", "proactive_expand", "full_fallback"):
        ledger.append(event_type="llm_request", attempt_id=decision, decision=decision, data={})

    metrics = ledger.metrics()

    assert metrics["tool_results"] == 2
    assert metrics["compressed"] == 1
    assert metrics["compression_rate"] == 0.5
    assert metrics["optimized_requests"] == 3
    assert metrics["fallbacks"] == 1
    assert metrics["fallback_rate"] == 1 / 3
