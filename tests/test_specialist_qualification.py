"""Дополнительная квалификация HCO перед shadow-тестом specialist profile."""

from __future__ import annotations

import json
import sqlite3

import pytest

from hco.optimizer import ContextOptimizer
from hco.optimizer import _EXPLICIT_IDENTIFIER_RE


@pytest.mark.parametrize(
    "secret",
    (
        "Proxy-Authorization: Digest opaque-value-1234567890",
        "TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
        "SESSION_ID=deadbeefcafebabefeed1234567890",
        "REFRESH_TOKEN=refresh-value-abcdef1234567890",
        "PRIVATE_KEY=super-private-material-1234567890",
    ),
)
def test_extended_secret_corpus_never_enters_store_or_sidecars(tmp_path, secret: str) -> None:
    store = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=store, min_chars=10)
    original = json.dumps(
        [{"id": index, "status": "ok"} for index in range(100)]
        + [{"diagnostic": secret}]
    )

    result = optimizer.optimize_tool_result(
        tool_name="read_file",
        tool_call_id="secret-extended",
        content=original,
        read_only=True,
        session_id="session-a",
    )

    assert result.changed is False
    with sqlite3.connect(store) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    for path in store.parent.glob("store.sqlite3*"):
        assert secret.encode("utf-8") not in path.read_bytes()


def test_unquoted_sensitive_key_in_nested_json_bypasses_persistence(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=store, min_chars=10)
    original = json.dumps(
        {
            "rows": [{"id": index, "status": "ok"} for index in range(100)],
            "credentials": {"telegram_bot_token": "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"},
        }
    )

    result = optimizer.optimize_tool_result(
        tool_name="web_extract",
        tool_call_id="nested-secret",
        content=original,
        read_only=True,
        session_id="session-a",
    )

    assert result.changed is False
    with sqlite3.connect(store) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_cross_session_hash_collision_never_retrieves_other_session_original(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=store, min_chars=10)
    original = json.dumps([{"id": f"EV-{index:04d}", "value": "A"} for index in range(100)])
    compact = optimizer.optimize_tool_result(
        tool_name="search_files",
        tool_call_id="same-source",
        content=original,
        read_only=True,
        session_id="profile-a/session-1",
    )

    request = [
        {"role": "user", "content": "Какое value у EV-0050?"},
        {"role": "tool", "tool_call_id": "same-source", "content": compact.content},
    ]
    prepared = optimizer.prepare_request(request, session_id="profile-b/session-1")

    assert prepared.receipt.decision == "error"
    assert prepared.receipt.coverage_complete is False
    assert prepared.messages is request
    assert original not in json.dumps(prepared.messages, ensure_ascii=False)


def test_policy_prose_with_secret_field_names_remains_optimizable(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=store, min_chars=10)
    original = json.dumps(
        [
            {"id": index, "policy": "never print API_KEY, TOKEN, PASSWORD or SESSION_ID"}
            for index in range(100)
        ]
    )

    result = optimizer.optimize_tool_result(
        tool_name="read_file",
        tool_call_id="policy-prose",
        content=original,
        read_only=True,
        session_id="session-a",
    )

    assert result.changed is True


def test_unknown_side_effecting_tool_result_is_byte_identical_passthrough(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=store, min_chars=10)
    original = json.dumps([{"id": index, "status": "posted"} for index in range(100)])

    result = optimizer.optimize_tool_result(
        tool_name="future_external_post_tool",
        tool_call_id="write-unknown",
        content=original,
        read_only=False,
        session_id="session-a",
    )

    assert result.changed is False
    assert result.content == original
    with sqlite3.connect(store) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_explicit_multiple_evidence_ids_restore_every_required_fragment(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=store, min_chars=10)
    rows = [
        {"evidence_id": f"SRC-{index:03d}", "access": "public", "claim": "routine"}
        for index in range(220)
    ]
    rows[44] = {
        "evidence_id": "SRC-044",
        "access": "login-required",
        "limitation": "Do not login or join private group",
    }
    rows[145] = {
        "evidence_id": "SRC-145",
        "access": "public",
        "url": "https://example.org/public/fallback-145",
    }
    original = json.dumps(rows, ensure_ascii=False)
    compact = optimizer.optimize_tool_result(
        tool_name="web_extract",
        tool_call_id="multi-evidence",
        content=original,
        read_only=True,
        session_id="specialist/session-a",
    )
    request = [
        {
            "role": "user",
            "content": "Какие access и limitation у SRC-044, и какой public fallback URL указан в SRC-145?",
        },
        {"role": "tool", "tool_call_id": "multi-evidence", "content": compact.content},
    ]

    prepared = optimizer.prepare_request(request, session_id="specialist/session-a")
    serialized = json.dumps(prepared.messages, ensure_ascii=False)

    assert prepared.receipt.decision == "proactive_expand"
    assert prepared.receipt.coverage_complete is True
    assert "SRC-044" in serialized
    assert "login-required" in serialized
    assert "SRC-145" in serialized
    assert "https://example.org/public/fallback-145" in serialized


def test_explicit_identifier_grammar_rejects_versions_and_ordinary_labels() -> None:
    for token in ("phase2", "model4", "hco0.1.2", "windows10", "oauth2"):
        assert _EXPLICIT_IDENTIFIER_RE.fullmatch(token) is None
    for token in ("SRC-044", "EV-0091", "ACCT-4829", "AP-7391"):
        assert _EXPLICIT_IDENTIFIER_RE.fullmatch(token) is not None


@pytest.mark.parametrize(
    "query",
    [
        "Используй SRC-044, SRC-145 и SRC-233.",
        "Сравни (SRC-044), [SRC-145] и «SRC-233».",
        "Evidence: SRC-044; SRC-145? SRC-233!",
    ],
)
def test_surrounding_punctuation_does_not_drop_mandatory_evidence_ids(
    tmp_path, query: str
) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=10)
    rows = [
        {"evidence_id": f"SRC-{index:03d}", "claim": "routine"}
        for index in range(260)
    ]
    rows[44] = {"evidence_id": "SRC-044", "access": "login-required"}
    rows[145] = {
        "evidence_id": "SRC-145",
        "url": "https://example.org/public/fallback-145",
    }
    rows[233] = {
        "evidence_id": "SRC-233",
        "url": "https://example.org/public/reminders-233",
        "smallest_build_slice": "Telegram reminder bot",
    }
    compact = optimizer.optimize_tool_result(
        tool_name="web_extract",
        tool_call_id="punctuation-ids",
        content=json.dumps(rows, ensure_ascii=False),
        read_only=True,
        session_id="session-a",
    )
    prepared = optimizer.prepare_request(
        [
            {"role": "user", "content": query},
            {"role": "tool", "tool_call_id": "punctuation-ids", "content": compact.content},
        ],
        session_id="session-a",
    )
    serialized = json.dumps(prepared.messages, ensure_ascii=False)

    assert prepared.receipt.decision == "proactive_expand"
    assert prepared.receipt.coverage_complete is True
    assert "SRC-044" in serialized
    assert "SRC-145" in serialized
    assert "SRC-233" in serialized
    assert "https://example.org/public/reminders-233" in serialized
    assert "Telegram reminder bot" in serialized
def test_explicit_identifier_grammar_covers_realistic_structured_namespaces() -> None:
    for identifier in (
        "LOG-041", "CODE-052", "CFG-033", "DEP-061", "DOC-025",
        "SEC-044", "MKT-039", "MET-031", "API-047", "DAT-042", "OPS-054",
    ):
        assert _EXPLICIT_IDENTIFIER_RE.fullmatch(identifier), identifier
