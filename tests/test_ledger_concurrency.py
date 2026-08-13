"""Concurrent append contract for the immutable ledger."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from hco.ledger import TelemetryLedger


def test_concurrent_appends_preserve_one_valid_hash_chain(tmp_path) -> None:
    ledger = TelemetryLedger(tmp_path / "ledger.jsonl")

    def append(index: int) -> None:
        ledger.append(
            event_type="request_prepare",
            attempt_id=f"attempt-{index}",
            decision="passthrough",
            data={"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(100)))

    assert ledger.verify() == 100
