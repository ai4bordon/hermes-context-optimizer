"""Bounded retention for plaintext originals."""

from __future__ import annotations

import json
import sqlite3
import time

from hco.optimizer import ContextOptimizer


def _save(optimizer: ContextOptimizer, session: str, marker: str) -> None:
    content = json.dumps([
        {"id": f"{marker}-{index:04d}", "status": "ok"} for index in range(100)
    ])
    result = optimizer.optimize_tool_result(
        tool_name="search_files", tool_call_id=marker, content=content,
        read_only=True, session_id=session,
    )
    assert result.changed is True


def test_retention_removes_expired_rows_and_caps_total_rows(tmp_path) -> None:
    path = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(
        store_path=path,
        min_chars=10,
        retention_ttl_seconds=60,
        retention_max_rows=2,
    )
    expired_marker = "HCO_EXPIRED_PRIVATE_MARKER_18bd02a7"
    expired_content = json.dumps([
        {"id": f"one-{index:04d}", "private_note": expired_marker}
        for index in range(100)
    ])
    expired_result = optimizer.optimize_tool_result(
        tool_name="search_files",
        tool_call_id="one",
        content=expired_content,
        read_only=True,
        session_id="s1",
    )
    assert expired_result.changed is True
    _save(optimizer, "s2", "two")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE sources SET created_at = ? WHERE session_id = 's1'",
            (time.time() - 120,),
        )
        connection.commit()
    _save(optimizer, "s3", "three")
    _save(optimizer, "s4", "four")

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT session_id FROM sources ORDER BY created_at DESC"
        ).fetchall()
    assert [row[0] for row in rows] == ["s4", "s3"]
    for state_file in path.parent.glob("store.sqlite3*"):
        assert expired_marker.encode("utf-8") not in state_file.read_bytes()


def test_purge_session_removes_only_target_session(tmp_path) -> None:
    path = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=path, min_chars=10)
    _save(optimizer, "session-a", "one")
    _save(optimizer, "session-b", "two")

    assert optimizer.purge_session("session-a") == 1

    with sqlite3.connect(path) as connection:
        sessions = connection.execute("SELECT session_id FROM sources").fetchall()
    assert sessions == [("session-b",)]


def test_purge_session_physically_scrubs_deleted_plaintext(tmp_path) -> None:
    path = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=path, min_chars=10)
    marker = "HCO_PURGE_PRIVATE_MARKER_7f90c1d4"
    content = json.dumps([
        {"id": f"row-{index:04d}", "private_note": marker}
        for index in range(100)
    ])
    result = optimizer.optimize_tool_result(
        tool_name="read_file",
        tool_call_id="private-purge",
        content=content,
        read_only=True,
        session_id="session-private",
    )
    assert result.changed is True
    assert marker.encode("utf-8") in path.read_bytes()

    assert optimizer.purge_session("session-private") == 1

    for state_file in path.parent.glob("store.sqlite3*"):
        assert marker.encode("utf-8") not in state_file.read_bytes()
