"""Validated HCO behavioral configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "search_files",
        "web_search",
        "web_extract",
        "session_search",
        "browser_snapshot",
        "browser_console",
    }
)


@dataclass(frozen=True)
class HCOConfig:
    enabled: bool = False
    strict: bool = False
    min_chars: int = 20_000
    retention_ttl_seconds: int = 86_400
    retention_max_rows: int = 1_000
    read_only_tools: frozenset[str] = DEFAULT_READ_ONLY_TOOLS

    @classmethod
    def from_mapping(cls, value: Any) -> "HCOConfig":
        if not isinstance(value, dict):
            return cls()
        enabled = value.get("enabled")
        strict = value.get("strict")
        min_chars = value.get("min_chars")
        retention_ttl = value.get("retention_ttl_seconds")
        retention_rows = value.get("retention_max_rows")
        tools = value.get("read_only_tools")
        return cls(
            enabled=enabled if isinstance(enabled, bool) else False,
            strict=strict if isinstance(strict, bool) else False,
            min_chars=(
                min_chars
                if isinstance(min_chars, int) and not isinstance(min_chars, bool) and min_chars >= 1_000
                else 20_000
            ),
            retention_ttl_seconds=(
                retention_ttl
                if isinstance(retention_ttl, int) and not isinstance(retention_ttl, bool) and retention_ttl >= 60
                else 86_400
            ),
            retention_max_rows=(
                retention_rows
                if isinstance(retention_rows, int) and not isinstance(retention_rows, bool) and retention_rows >= 1
                else 1_000
            ),
            read_only_tools=(
                frozenset(item for item in tools if isinstance(item, str) and item)
                if isinstance(tools, list) and tools
                else DEFAULT_READ_ONLY_TOOLS
            ),
        )
