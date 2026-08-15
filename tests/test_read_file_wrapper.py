"""Regression для реального Hermes read_file wrapper."""

from __future__ import annotations

import json

from hco.hermes_plugin import HCOMiddleware
from hco.optimizer import ContextOptimizer


def _records() -> list[dict[str, str]]:
    rows = [
        {
            "evidence_id": f"SRC-{index:03d}",
            "access": "public",
            "url": f"https://example.org/public/{index:03d}",
            "padding": "x" * 80,
        }
        for index in range(200)
    ]
    rows[44] = {"evidence_id": "SRC-044", "access": "login-required"}
    rows[145] = {
        "evidence_id": "SRC-145",
        "access": "public",
        "url": "https://example.org/public/fallback-145",
    }
    return rows


def _read_file_wrapper(inner_content: str, *, truncated: bool) -> str:
    return json.dumps(
        {
            "content": inner_content,
            "total_lines": 0,
            "file_size": 108960,
            "truncated": truncated,
            "is_binary": False,
            "is_image": False,
        },
        ensure_ascii=False,
    )


def _paginated_read_file_wrapper(rows: list[dict[str, str]], *, next_offset: int) -> str:
    return json.dumps(
        {
            "content": "\n".join(
                f"{index}|{json.dumps(row, ensure_ascii=False)}"
                for index, row in enumerate(rows, start=1)
            ),
            "total_lines": 320,
            "file_size": 105532,
            "truncated": True,
            "truncated_by": "bytes",
            "next_offset": next_offset,
            "hint": f"Use offset={next_offset} to continue.",
            "is_binary": False,
            "is_image": False,
        },
        ensure_ascii=False,
    )


def test_complete_read_file_wrapper_unwraps_inner_json_for_multi_id_retrieval(tmp_path) -> None:
    optimizer = ContextOptimizer(store_path=tmp_path / "store.sqlite3", min_chars=100)
    wrapper = _read_file_wrapper("1|" + json.dumps(_records(), ensure_ascii=False), truncated=False)

    compact = optimizer.optimize_tool_result(
        tool_name="read_file",
        tool_call_id="read-complete",
        content=wrapper,
        read_only=True,
        session_id="session-a",
    )
    request = [
        {"role": "user", "content": "Сравни SRC-044 и SRC-145"},
        {"role": "tool", "tool_call_id": "read-complete", "content": compact.content},
    ]
    prepared = optimizer.prepare_request(request, session_id="session-a")
    serialized = json.dumps(prepared.messages, ensure_ascii=False)

    assert compact.changed is True
    assert prepared.receipt.decision == "proactive_expand"
    assert prepared.receipt.coverage_complete is True
    assert "login-required" in serialized
    assert "https://example.org/public/fallback-145" in serialized
    assert len(prepared.messages[1]["content"]) < len(wrapper) // 4
    additions = prepared.messages[-1]["content"].split("<hco-proactive-fragments>", 1)[1]
    assert "SRC-000" not in additions


def test_truncated_read_file_wrapper_blocks_strict_provider_dispatch(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=20_000,
        strict=True,
    )
    clipped = "1|" + json.dumps(_records()[:20], ensure_ascii=False) + " ... [truncated]"
    # Exact live failure shape: wrapper claimed ``truncated=false`` while its
    # inner content already contained the renderer's truncation marker.
    wrapper = _read_file_wrapper(clipped, truncated=False)

    compact = middleware.tool_execution(
        lambda _: wrapper,
        tool_name="read_file",
        args={"path": "fixture.json"},
        session_id="session-a",
        tool_call_id="read-truncated",
        api_request_id="req-a",
    )
    request = {
        "messages": [
            {"role": "user", "content": "Сравни SRC-044 и SRC-145"},
            {"role": "tool", "tool_call_id": "read-truncated", "content": compact},
        ]
    }
    result = middleware.llm_request(request=request, session_id="session-a", api_request_id="req-a")

    assert result["blocked"] is True
    assert result["reason"] == "coverage_incomplete"
    assert result["receipt"]["coverage_complete"] is False
    assert "<hco-incomplete" in compact


def test_paginated_page_with_all_explicit_ids_uses_complete_available_records(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=100,
        strict=True,
    )
    wrapper = _paginated_read_file_wrapper(_records(), next_offset=201)
    compact = middleware.tool_execution(
        lambda _: wrapper,
        tool_name="read_file",
        args={"path": "fixture.jsonl", "offset": 1, "limit": 200},
        session_id="session-a",
        tool_call_id="read-page",
        api_request_id="req-page",
    )
    result = middleware.llm_request(
        request={
            "messages": [
                {"role": "user", "content": "Сравни SRC-044 и SRC-145"},
                {"role": "tool", "tool_call_id": "read-page", "content": compact},
            ]
        },
        session_id="session-a",
        api_request_id="req-page",
    )
    serialized = json.dumps(result["request"]["messages"], ensure_ascii=False)

    assert result["blocked"] is False
    assert result["receipt"]["decision"] == "proactive_expand"
    assert result["receipt"]["coverage_complete"] is True
    assert "login-required" in serialized
    assert "https://example.org/public/fallback-145" in serialized


def test_paginated_page_missing_explicit_id_blocks_until_next_page(tmp_path) -> None:
    middleware = HCOMiddleware(
        store_path=tmp_path / "store.sqlite3",
        ledger_path=tmp_path / "telemetry.sqlite3",
        min_chars=100,
        strict=True,
    )
    wrapper = _paginated_read_file_wrapper(_records()[:100], next_offset=101)
    compact = middleware.tool_execution(
        lambda _: wrapper,
        tool_name="read_file",
        args={"path": "fixture.jsonl", "offset": 1, "limit": 100},
        session_id="session-a",
        tool_call_id="read-page-missing",
        api_request_id="req-page-missing",
    )
    result = middleware.llm_request(
        request={
            "messages": [
                {"role": "user", "content": "Сравни SRC-044 и SRC-145"},
                {"role": "tool", "tool_call_id": "read-page-missing", "content": compact},
            ]
        },
        session_id="session-a",
        api_request_id="req-page-missing",
    )

    assert result["blocked"] is True
    assert result["receipt"]["decision"] == "upstream_incomplete"
    assert result["receipt"]["coverage_complete"] is False
