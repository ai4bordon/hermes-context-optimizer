"""Real Hermes PluginManager discovery under a temporary HERMES_HOME."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest
import yaml


def test_plugin_discovers_and_registers_both_middlewares(tmp_path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    hermes_root_value = os.environ.get("HERMES_SOURCE_ROOT")
    if importlib.util.find_spec("hermes_cli") is None and not hermes_root_value:
        pytest.skip(
            "Hermes source is not installed; set HERMES_SOURCE_ROOT to run host discovery"
        )
    hermes_root = Path(hermes_root_value) if hermes_root_value else None
    plugin_source = repo_root / "plugin" / "__init__.py"
    home = tmp_path / "hermes-home"
    plugin_dir = home / "plugins" / "hermes-context-optimizer"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: hermes-context-optimizer\nversion: 0.1.0\ndescription: test\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(plugin_source.read_text(encoding="utf-8"), encoding="utf-8")
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {"enabled": ["hermes-context-optimizer"]},
                "hco": {"enabled": True, "strict": True, "min_chars": 1000},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    if hermes_root is not None:
        monkeypatch.syspath_prepend(str(hermes_root))
    monkeypatch.syspath_prepend(str(repo_root / "src"))

    import hermes_cli.plugins as plugins

    manager = plugins.PluginManager()
    manager.discover_and_load()

    loaded = manager._plugins["hermes-context-optimizer"]
    assert loaded.enabled is True
    assert set(loaded.middleware_registered) == {"tool_execution", "llm_request"}
    assert set(manager._middleware) >= {"tool_execution", "llm_request"}
