"""Configuration contract for HCO plugin."""

from __future__ import annotations

from hco.config import HCOConfig


def test_config_reads_behavior_from_config_mapping_not_environment(monkeypatch) -> None:
    monkeypatch.setenv("HCO_STRICT", "0")
    monkeypatch.setenv("HCO_MIN_CHARS", "999999")
    config = HCOConfig.from_mapping(
        {
            "enabled": True,
            "strict": True,
            "min_chars": 12345,
            "retention_ttl_seconds": 3600,
            "retention_max_rows": 500,
            "read_only_tools": ["search_files", "web_extract"],
        }
    )

    assert config.enabled is True
    assert config.strict is True
    assert config.min_chars == 12345
    assert config.retention_ttl_seconds == 3600
    assert config.retention_max_rows == 500
    assert config.read_only_tools == frozenset({"search_files", "web_extract"})


def test_invalid_config_fails_safe() -> None:
    config = HCOConfig.from_mapping(
        {"enabled": "yes", "strict": "yes", "min_chars": -1, "read_only_tools": "all"}
    )

    assert config.enabled is False
    assert config.strict is False
    assert config.min_chars == 20000
    assert config.retention_ttl_seconds == 86400
    assert config.retention_max_rows == 1000
    assert config.read_only_tools
