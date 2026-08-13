"""Packaging contract for pip-distributed Hermes plugin."""

from __future__ import annotations

import importlib.metadata


def test_distribution_exposes_hermes_plugin_entry_point() -> None:
    entry_points = importlib.metadata.entry_points(group="hermes_agent.plugins")
    matches = [entry for entry in entry_points if entry.name == "hermes-context-optimizer"]

    assert len(matches) == 1
    module = matches[0].load()
    assert callable(module.register)
