"""Hermes plugin registration without modifying Hermes core."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import HCOConfig
from .hermes_plugin import HCOMiddleware
from .permissions import harden_private_path

_middleware: HCOMiddleware | None = None


def _home() -> Path:
    explicit = os.environ.get("HCO_HOME")
    if explicit:
        return Path(explicit)
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "hco"


def _load_hco_config() -> HCOConfig:
    raw_config: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config

        host_config = load_config()
        if isinstance(host_config, dict):
            candidate = host_config.get("hco", {})
            if isinstance(candidate, dict):
                raw_config = candidate
    except Exception:
        raw_config = {}
    return HCOConfig.from_mapping(raw_config)


def register(ctx: Any) -> None:
    global _middleware
    config = _load_hco_config()
    if not config.enabled:
        _middleware = None
        return

    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    harden_private_path(home)
    _middleware = HCOMiddleware(
        store_path=home / "store.sqlite3",
        ledger_path=home / "telemetry.sqlite3",
        min_chars=config.min_chars,
        retention_ttl_seconds=config.retention_ttl_seconds,
        retention_max_rows=config.retention_max_rows,
        strict=config.strict,
        read_only_tools=config.read_only_tools,
    )
    ctx.register_middleware("tool_execution", _middleware.tool_execution)
    ctx.register_middleware("llm_request", _middleware.llm_request)
    for state_path in (home / "store.sqlite3", home / "telemetry.sqlite3"):
        if state_path.exists():
            harden_private_path(state_path)
