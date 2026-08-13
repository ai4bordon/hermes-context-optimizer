"""Versioned migration from the legacy source_hash-only schema."""

from __future__ import annotations

import json
import sqlite3

from hco.optimizer import ContextOptimizer


def _legacy_store(path, original: str) -> str:
    import hashlib

    source_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
    fragments = json.dumps([
        {"fragment_id":"row-000000","position":0,"content":{"id":"LEGACY-1"}}
    ])
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE sources (
                source_hash TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                original_content TEXT NOT NULL,
                fragments_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
            (source_hash, "legacy-session", "read_file", "legacy-call", original, fragments),
        )
    return source_hash


def test_legacy_schema_is_atomically_migrated_and_idempotent(tmp_path) -> None:
    path = tmp_path / "store.sqlite3"
    original = json.dumps([{"id":"LEGACY-1","value":"preserved"}])
    legacy_hash = _legacy_store(path, original)

    first = ContextOptimizer(store_path=path, min_chars=10)
    second = ContextOptimizer(store_path=path, min_chars=10)

    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        pk = connection.execute("PRAGMA table_info(sources)").fetchall()
        row = connection.execute(
            "SELECT session_id, original_content FROM sources WHERE source_hash = ?",
            (legacy_hash,),
        ).fetchone()
    assert version == 3
    assert [(column[1], column[5]) for column in pk if column[5]] == [
        ("source_hash", 2), ("session_id", 1)
    ]
    assert row == ("legacy-session", original)

    fresh = json.dumps([{"id":f"EV-{i:04d}","approval":f"AP-{i:04d}"} for i in range(100)])
    result_a = first.optimize_tool_result(
        tool_name="search_files", tool_call_id="a", content=fresh,
        read_only=True, session_id="session-a",
    )
    result_b = second.optimize_tool_result(
        tool_name="search_files", tool_call_id="b", content=fresh,
        read_only=True, session_id="session-b",
    )
    assert result_a.changed is True
    assert result_b.changed is True
