"""Generic strict coverage gate intended for a minimal Hermes host hook."""

from __future__ import annotations

from typing import Any


class HCOCoverageError(RuntimeError):
    """Provider dispatch is blocked because coverage is incomplete."""


def enforce_coverage(middleware_result: dict[str, Any], *, strict: bool) -> dict[str, Any]:
    request = middleware_result.get("request")
    if not isinstance(request, dict):
        raise HCOCoverageError("HCO middleware returned no valid provider request")
    receipt = ((middleware_result.get("trace") or {}).get("coverage_receipt") or {})
    blocked = bool(middleware_result.get("blocked"))
    complete = bool(receipt.get("coverage_complete"))
    if strict and (blocked or not complete):
        raise HCOCoverageError(
            "HCO strict coverage gate blocked provider dispatch: coverage incomplete"
        )
    return request
