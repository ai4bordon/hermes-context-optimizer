"""Host-side strict guard contract for HCO middleware results."""

from __future__ import annotations

import pytest

from hco.strict_guard import HCOCoverageError, enforce_coverage


def test_strict_guard_allows_complete_receipt() -> None:
    result = {
        "request": {"messages": [{"role": "user", "content": "ok"}]},
        "blocked": False,
        "trace": {"coverage_receipt": {"coverage_complete": True}},
    }
    assert enforce_coverage(result, strict=True) == result["request"]


def test_strict_guard_blocks_incomplete_receipt() -> None:
    result = {
        "request": {"messages": [{"role": "user", "content": "unsafe"}]},
        "blocked": True,
        "trace": {"coverage_receipt": {"coverage_complete": False}},
    }
    with pytest.raises(HCOCoverageError):
        enforce_coverage(result, strict=True)


def test_non_strict_guard_falls_back_to_effective_request() -> None:
    result = {
        "request": {"messages": [{"role": "user", "content": "baseline"}]},
        "blocked": True,
        "trace": {"coverage_receipt": {"coverage_complete": False}},
    }
    assert enforce_coverage(result, strict=False) == result["request"]
