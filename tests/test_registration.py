"""Проверка регистрации HCO через штатный Hermes PluginContext."""

from __future__ import annotations

import sys
import types

from hco.hermes_registration import register


class FakeContext:
    def __init__(self) -> None:
        self.middleware: dict[str, object] = {}

    def register_middleware(self, kind: str, callback) -> None:
        self.middleware[kind] = callback


def _config_module(config: dict[str, object]) -> types.SimpleNamespace:
    return types.SimpleNamespace(load_config=lambda: config)


def test_register_wires_both_supported_middlewares_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCO_HOME", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        _config_module({"hco": {"enabled": True, "strict": True, "min_chars": 1000}}),
    )
    context = FakeContext()

    register(context)

    assert set(context.middleware) == {"tool_execution", "llm_request"}
    assert callable(context.middleware["tool_execution"])
    assert callable(context.middleware["llm_request"])
    assert (tmp_path / "store.sqlite3").exists()


def test_register_does_nothing_and_creates_no_state_when_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCO_HOME", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        _config_module({"hco": {"enabled": False, "strict": True, "min_chars": 1000}}),
    )
    context = FakeContext()

    register(context)

    assert context.middleware == {}
    assert not (tmp_path / "store.sqlite3").exists()
    assert not (tmp_path / "telemetry.sqlite3").exists()
