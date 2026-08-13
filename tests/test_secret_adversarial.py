"""Adversarial secret formats must never enter HCO persistence."""

from __future__ import annotations

import json
import sqlite3

import pytest

from hco.optimizer import ContextOptimizer


SECRET_CASES = (
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
    "Cookie: sessionid=deadbeefcafebabefeed1234567890",
    "Set-Cookie: foo=supersecretvalue123456",
    "Cookie: foo=supersecretvalue123456",
    "Authorization: Basic c2VjcmV0LWNyZWRzLTEyMzQ1Ng==",
    "authorization=Bearer eyJhbGciOiJIUzI1NiJ9",
    "X-Api-Key: abcdefghijklmnop",
    "oauth_token=abcdefghijklmnop",
    "secret: abcdefghijklmnop",
    "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
    "password=hunter-correct-battery-staple",
    "client_secret: ultra-secret-client-value-9981",
    '"access_token": "token-value-abcdef1234567890"',
    "AWS_SECRET_ACCESS_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
)


@pytest.mark.parametrize("secret", SECRET_CASES)
def test_adversarial_secret_formats_bypass_store(tmp_path, secret: str) -> None:
    store = tmp_path / "store.sqlite3"
    optimizer = ContextOptimizer(store_path=store, min_chars=10)
    original = json.dumps(
        [{"id": index, "status": "ok"} for index in range(100)]
        + [{"diagnostic": secret}]
    )

    result = optimizer.optimize_tool_result(
        tool_name="read_file", tool_call_id="secret", content=original,
        read_only=True, session_id="session-a",
    )

    assert result.changed is False
    with sqlite3.connect(store) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    for path in store.parent.glob("store.sqlite3*"):
        assert secret.encode("utf-8") not in path.read_bytes()
