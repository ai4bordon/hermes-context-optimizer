"""Middleware composition and cache-stable request contracts."""

from __future__ import annotations

import json

from hco.hermes_plugin import HCOMiddleware


def test_identical_request_produces_byte_identical_provider_payload(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=10,
        strict=True,
    )
    rows = [{"id":f"EV-{i:04d}","approval":f"AP-{i:04d}"} for i in range(100)]
    compact = middleware.tool_execution(
        next_call=lambda _args: json.dumps(rows),
        tool_name="search_files", args={}, session_id="session-a",
        tool_call_id="call-1", api_request_id="tool-1",
    )
    request = {"messages":[
        {"role":"user","content":"Какой approval у EV-0050?"},
        {"role":"tool","tool_call_id":"call-1","content":compact},
    ]}

    first = middleware.llm_request(
        request=request, session_id="session-a", api_request_id="llm-1"
    )
    second = middleware.llm_request(
        request=request, session_id="session-a", api_request_id="llm-2"
    )

    assert json.dumps(first["request"], sort_keys=True) == json.dumps(
        second["request"], sort_keys=True
    )
    wire = json.dumps(first["request"])
    assert "fragment_id" not in wire
    assert "row-000050" not in wire
    assert "coverage_receipt" not in wire


def test_proactive_fragments_are_appended_without_mutating_history(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=10,
        strict=True,
    )
    rows = [{"id": f"EV-{i:04d}", "detail": f"detail-{i}"} for i in range(100)]
    compact = middleware.tool_execution(
        next_call=lambda _args: json.dumps(rows),
        tool_name="search_files", args={}, session_id="session-a",
        tool_call_id="call-1", api_request_id="tool-1",
    )
    history = [
        {"role": "system", "content": "stable-system"},
        {"role": "user", "content": "stable-old-turn"},
        {"role": "assistant", "content": "stable-old-answer"},
        {"role": "user", "content": "Покажи EV-0050"},
        {"role": "tool", "tool_call_id": "call-1", "content": compact},
    ]
    original = json.loads(json.dumps(history))

    prepared = middleware.llm_request(
        request={"messages": history}, session_id="session-a", api_request_id="llm-1"
    )["request"]["messages"]

    assert prepared[: len(original)] == original
    assert len(prepared) == len(original) + 1
    assert prepared[-1]["role"] == "user"
    assert "<hco-proactive-fragments>" in prepared[-1]["content"]
    assert "EV-0050" in prepared[-1]["content"]


def test_proactive_fragments_merge_into_trailing_user_message(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=10,
        strict=True,
    )
    rows = [{"id": f"EV-{i:04d}", "detail": f"detail-{i}"} for i in range(100)]
    compact = middleware.tool_execution(
        next_call=lambda _args: json.dumps(rows),
        tool_name="search_files", args={}, session_id="session-a",
        tool_call_id="call-1", api_request_id="tool-1",
    )
    history = [
        {"role": "system", "content": "stable-system"},
        {"role": "user", "content": "old turn"},
        {"role": "assistant", "content": "old answer"},
        {"role": "tool", "tool_call_id": "call-1", "content": compact},
        {"role": "user", "content": "Покажи EV-0050"},
    ]

    original = json.loads(json.dumps(history))
    prepared = middleware.llm_request(
        request={"messages": history}, session_id="session-a", api_request_id="llm-1"
    )["request"]["messages"]

    assert prepared[:-1] == original[:-1]
    assert prepared[-1]["content"].startswith(original[-1]["content"])
    roles = [message["role"] for message in prepared]
    assert not any(a == b == "user" for a, b in zip(roles, roles[1:]))
    assert "<hco-proactive-fragments>" in prepared[-1]["content"]
    assert "EV-0050" in prepared[-1]["content"]


def test_proactive_fragments_append_after_assistant(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=10,
        strict=True,
    )
    rows = [{"id": f"EV-{i:04d}", "detail": f"detail-{i}"} for i in range(100)]
    compact = middleware.tool_execution(
        next_call=lambda _args: json.dumps(rows),
        tool_name="search_files", args={}, session_id="session-a",
        tool_call_id="call-1", api_request_id="tool-1",
    )
    history = [
        {"role": "system", "content": "stable-system"},
        {"role": "user", "content": "Расскажи про EV-0050"},
        {"role": "tool", "tool_call_id": "call-1", "content": compact},
        {"role": "assistant", "content": "old answer"},
    ]

    prepared = middleware.llm_request(
        request={"messages": history}, session_id="session-a", api_request_id="llm-1"
    )["request"]["messages"]

    assert prepared[-1]["role"] == "user"
    assert "<hco-proactive-fragments>" in prepared[-1]["content"]
    assert "EV-0050" in prepared[-1]["content"]


def test_disabled_plugin_is_exact_baseline_by_not_registering(tmp_path, monkeypatch) -> None:
    import sys
    import types
    from hco.hermes_registration import register

    context = types.SimpleNamespace(calls=[])
    context.register_middleware = lambda *args: context.calls.append(args)
    monkeypatch.setenv("HCO_HOME", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        types.SimpleNamespace(load_config=lambda: {"hco":{"enabled":False}}),
    )

    register(context)

    assert context.calls == []
    assert list(tmp_path.iterdir()) == []
