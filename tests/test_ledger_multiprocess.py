"""Cross-process ledger serialization contract."""

from __future__ import annotations

import multiprocessing

from hco.ledger import TelemetryLedger


def _append_many(path: str, worker: int, count: int) -> None:
    ledger = TelemetryLedger(path)
    for index in range(count):
        ledger.append(
            event_type="request_prepare",
            attempt_id=f"worker-{worker}-{index}",
            decision="passthrough",
            data={"worker": worker, "index": index},
        )


def test_multiprocess_appends_preserve_hash_chain(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    processes = [
        multiprocessing.Process(target=_append_many, args=(str(path), worker, 25))
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    assert TelemetryLedger(path).verify() == 100
