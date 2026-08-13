"""Standalone Hermes middleware adapter for HCO core."""

from __future__ import annotations

import copy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .ledger import TelemetryLedger
from .optimizer import ContextOptimizer

# Conservative first allowlist. Side-effecting or unknown tools bypass HCO.
_DEFAULT_READ_ONLY_TOOLS = frozenset(
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


class HCOMiddleware:
    def __init__(
        self,
        *,
        store_path: str | Path,
        ledger_path: str | Path,
        min_chars: int = 20_000,
        retention_ttl_seconds: int = 86_400,
        retention_max_rows: int = 1_000,
        strict: bool = False,
        read_only_tools: frozenset[str] = _DEFAULT_READ_ONLY_TOOLS,
    ) -> None:
        self.optimizer = ContextOptimizer(
            store_path=store_path,
            min_chars=min_chars,
            retention_ttl_seconds=retention_ttl_seconds,
            retention_max_rows=retention_max_rows,
        )
        self.ledger = TelemetryLedger(ledger_path)
        self.strict = strict
        self.read_only_tools = read_only_tools
        self.state_home = Path(store_path).parent

    def _harden_state(self) -> None:
        from .permissions import harden_state_files

        harden_state_files(self.state_home)

    def tool_execution(
        self,
        next_call: Callable[[dict[str, Any]], Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        session_id: str = "",
        tool_call_id: str = "",
        api_request_id: str = "",
        **_: Any,
    ) -> Any:
        result = next_call(args)
        if not isinstance(result, str):
            return result
        compact = self.optimizer.optimize_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            content=result,
            read_only=tool_name in self.read_only_tools,
            session_id=session_id,
        )
        self._harden_state()
        self.ledger.append(
            event_type="tool_result",
            attempt_id=api_request_id or tool_call_id or "unknown",
            decision="compact" if compact.changed else "passthrough",
            data={
                "session_id_hash": _opaque_id(session_id),
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "source_hash": compact.source_hash,
            },
        )
        return compact.content

    def llm_request(
        self,
        *,
        request: dict[str, Any],
        session_id: str = "",
        api_request_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        messages = request.get("messages")
        if not isinstance(messages, list):
            return {"request": request, "blocked": False, "trace": {"coverage_receipt": None}}
        prepared = self.optimizer.prepare_request(messages, session_id=session_id)
        receipt = asdict(prepared.receipt)
        blocked = bool(self.strict and not prepared.receipt.coverage_complete)
        updated = copy.deepcopy(request)
        updated["messages"] = prepared.messages
        self.ledger.append(
            event_type="llm_request",
            attempt_id=api_request_id or "unknown",
            decision="blocked" if blocked else prepared.receipt.decision,
            data={
                "session_id_hash": _opaque_id(session_id),
                "coverage_receipt": receipt,
            },
        )
        self._harden_state()
        return {
            "request": updated,
            "blocked": blocked,
            "decision": "block" if blocked else "allow",
            "reason": "coverage_incomplete" if blocked else "coverage_complete",
            "receipt": receipt,
            "trace": {"coverage_receipt": receipt},
        }


def _opaque_id(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24] if value else ""
