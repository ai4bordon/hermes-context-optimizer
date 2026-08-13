"""Deterministic acceptance corpus: critical recall, fallback and savings."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from hco.optimizer import ContextOptimizer


@dataclass(frozen=True)
class Case:
    name: str
    question: str
    target: dict[str, object] | None
    expected_terms: tuple[str, ...]
    expect_fallback: bool = False


CASES = (
    Case("approval", "approval ticket deleting profile", {"id": "EV-0050", "approval_ticket": "AP-7391", "action": "deleting profile"}, ("AP-7391", "EV-0050")),
    Case("rollback", "rollback snapshot path", {"id": "EV-0051", "rollback_snapshot_path": "C:\\Backups\\snap-77"}, ("snap-77", "EV-0051")),
    Case("owner", "delivery owner result ready done", {"id": "EV-0052", "delivery_owner": "origin-chat", "rule": "RESULT_READY is not DONE"}, ("origin-chat", "RESULT_READY", "DONE")),
    Case("date", "publication date current", {"id": "EV-0053", "publication_date": "2025-09-04", "state": "current"}, ("2025-09-04", "EV-0053")),
    Case("secret", "secret boundary reports", {"id": "EV-0054", "secret_boundary": "never print MODELHUB_API_KEY into reports"}, ("MODELHUB_API_KEY", "EV-0054")),
    Case("dod", "definition done requirements", {"id": "EV-0055", "definition_done": ["pytest", "artifact readback", "independent review"]}, ("pytest", "artifact readback", "independent review")),
    Case("timezone", "account timezone reminders", {"id": "EV-0056", "account": "ACCT-4829", "timezone": "UTC+12", "reminders": ["6h", "1h"]}, ("UTC+12", "6h", "1h")),
    Case("threshold", "canary threshold route alpha", {"id": "EV-0057", "canary_threshold": 0.37, "route": "alpha"}, ("0.37", "EV-0057")),
    Case("checksum", "final checksum label", {"id": "EV-0058", "final_checksum_label": "cobalt-iris-994"}, ("cobalt-iris-994", "EV-0058")),
    Case("duration", "duration 9876 evidence", {"id": "EV-0059", "duration_ms": 9876}, ("9876", "EV-0059")),
    Case("fatal", "fatal database pool", {"id": "EV-0060", "status": "failed", "message": "FATAL database connection pool exhausted"}, ("FATAL", "EV-0060")),
    Case("absent", "incident ticket assigned", None, (), True),
)


def build_rows(case: Case) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {"id": f"EV-{i:04d}", "status": "ok", "message": "Routine operation completed"}
        for i in range(180)
    ]
    if case.target is not None:
        rows[int(str(case.target["id"]).split("-")[1])] = dict(case.target)
    return rows


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_critical_corpus_has_complete_coverage_or_full_fallback(tmp_path, case: Case) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / f"{case.name}.sqlite3", min_chars=100)
    original = json.dumps(build_rows(case), ensure_ascii=False, separators=(",", ":"))
    compact = optimizer.optimize_tool_result(
        tool_name="search_files",
        tool_call_id=f"call-{case.name}",
        content=original,
        read_only=True,
        session_id="acceptance",
    )
    assert compact.changed is True
    for term in case.expected_terms:
        assert term not in compact.content

    request = [
        {"role": "user", "content": case.question},
        {"role": "tool", "tool_call_id": f"call-{case.name}", "content": compact.content},
    ]
    prepared = optimizer.prepare_request(request, session_id="acceptance")
    wire = json.dumps(prepared.messages, ensure_ascii=False)

    assert prepared.receipt.coverage_complete is True
    if case.expect_fallback:
        assert prepared.receipt.decision == "full_fallback"
        assert prepared.messages[1]["content"] == original
    else:
        assert prepared.receipt.decision == "proactive_expand"
        assert all(term in wire for term in case.expected_terms)
        assert len(wire) < len(original) * 0.30
