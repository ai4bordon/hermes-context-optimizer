"""Append-only SQLite ledger with canonical SHA-256 hash chain."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any


class LedgerIntegrityError(RuntimeError):
    """Ledger hash chain or canonical event hash is invalid."""


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class TelemetryLedger:
    """Process-safe local ledger serialized by SQLite BEGIN IMMEDIATE."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_store()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _init_store(self) -> None:
        attempts = 6
        for attempt in range(attempts):
            connection = self._connect()
            try:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if mode is None or str(mode[0]).casefold() != "wal":
                    raise sqlite3.OperationalError(
                        f"Could not enable SQLite WAL mode: {mode!r}"
                    )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_json TEXT NOT NULL,
                        event_hash TEXT NOT NULL UNIQUE
                    )"""
                )
                connection.commit()
                return
            except sqlite3.OperationalError as error:
                connection.rollback()
                transient = any(
                    marker in str(error).casefold()
                    for marker in ("database is locked", "database is busy")
                )
                if not transient or attempt == attempts - 1:
                    raise
            finally:
                connection.close()
            time.sleep(0.02 * (2**attempt))

    def append(
        self,
        *,
        event_type: str,
        attempt_id: str,
        decision: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            last = connection.execute(
                "SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(last["event_hash"]) if last else "0" * 64
            event: dict[str, Any] = {
                "schema_version": "hco.telemetry.v1",
                "event_type": event_type,
                "attempt_id": attempt_id,
                "decision": decision,
                "data": data,
                "previous_event_hash": previous_hash,
            }
            event["event_hash"] = hashlib.sha256(_canonical(event)).hexdigest()
            connection.execute(
                "INSERT INTO events (event_json, event_hash) VALUES (?, ?)",
                (
                    _canonical(event).decode("utf-8"),
                    event["event_hash"],
                ),
            )
            connection.commit()
            return event

    def verify(self) -> int:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sequence, event_json, event_hash FROM events ORDER BY sequence"
            ).fetchall()
        previous = "0" * 64
        for row in rows:
            event = json.loads(row["event_json"])
            observed_hash = event.pop("event_hash", None)
            if event.get("previous_event_hash") != previous:
                raise LedgerIntegrityError(
                    f"Invalid previous hash at ledger sequence {row['sequence']}"
                )
            expected_hash = hashlib.sha256(_canonical(event)).hexdigest()
            if observed_hash != expected_hash or row["event_hash"] != expected_hash:
                raise LedgerIntegrityError(
                    f"Invalid event hash at ledger sequence {row['sequence']}"
                )
            previous = expected_hash
        return len(rows)

    def metrics(self) -> dict[str, int | float]:
        """Return decision rates derived from the append-only ledger."""
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT event_json FROM events ORDER BY sequence"
            ).fetchall()
        events = [json.loads(row["event_json"]) for row in rows]
        tool_events = [event for event in events if event["event_type"] == "tool_result"]
        request_events = [event for event in events if event["event_type"] == "llm_request"]
        compressed = sum(event["decision"] == "compact" for event in tool_events)
        optimized = sum(
            event["decision"] in {"proactive_expand", "full_fallback"}
            for event in request_events
        )
        fallbacks = sum(event["decision"] == "full_fallback" for event in request_events)
        blocked = sum(event["decision"] == "blocked" for event in request_events)
        errors = sum(event["decision"] == "error" for event in request_events)
        return {
            "tool_results": len(tool_events),
            "compressed": compressed,
            "compression_rate": compressed / len(tool_events) if tool_events else 0.0,
            "llm_requests": len(request_events),
            "optimized_requests": optimized,
            "fallbacks": fallbacks,
            "blocked": blocked,
            "errors": errors,
            "optimized_fallback_rate": fallbacks / optimized if optimized else 0.0,
            "fallback_rate": fallbacks / len(request_events) if request_events else 0.0,
        }
