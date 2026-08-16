"""Локальный read-only dashboard для HCO telemetry."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def _events(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            "SELECT sequence, event_json FROM events ORDER BY sequence DESC LIMIT ?", (limit,)
        ).fetchall()
    result: list[dict[str, Any]] = []
    for sequence, event_json in reversed(rows):
        event = json.loads(event_json)
        event["sequence"] = sequence
        result.append(event)
    return result


def _stored_sources(path: Path) -> int:
    if not path.exists():
        return 0
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        return int(connection.execute("SELECT count(*) FROM sources").fetchone()[0])


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def build_snapshot(ledger_path: str | Path, store_path: str | Path, *, limit: int = 100) -> dict[str, Any]:
    """Возвращает только агрегаты и telemetry metadata; source contents не читает."""
    events = _events(Path(ledger_path), limit)
    tool_events = [event for event in events if event.get("event_type") == "tool_result"]
    request_events = [event for event in events if event.get("event_type") == "llm_request"]
    decisions = [str(event.get("decision", "unknown")) for event in events]
    saved_chars = sum(
        max(0, int(data.get("original_chars", 0)) - int(data.get("wire_chars", 0)))
        for event in tool_events
        if isinstance((data := event.get("data")), dict)
        and isinstance(data.get("original_chars"), int)
        and isinstance(data.get("wire_chars"), int)
    )
    usages = [
        event.get("data", {}).get("provider_usage")
        for event in request_events
        if isinstance(event.get("data"), dict) and isinstance(event["data"].get("provider_usage"), dict)
    ]
    totals = [_number(usage.get("total_tokens")) for usage in usages]
    actual_total = sum(value for value in totals if value is not None) if any(value is not None for value in totals) else None
    return {
        "schema_version": "hco.telemetry.v2",
        "read_only": True,
        "events": events,
        "summary": {
            "tool_results": len(tool_events),
            "llm_requests": len(request_events),
            "compact": decisions.count("compact"),
            "passthrough": decisions.count("passthrough"),
            "proactive_expand": decisions.count("proactive_expand"),
            "full_fallback": decisions.count("full_fallback"),
            "blocked": decisions.count("blocked"),
            "errors": decisions.count("error"),
            "estimated_context_chars_avoided": saved_chars,
            "stored_sources": _stored_sources(Path(store_path)),
            "actual_tokens": {"status": "available" if actual_total is not None else "unknown", "total": actual_total},
        },
    }


def create_server(ledger_path: str | Path, store_path: str | Path, host: str = "127.0.0.1", port: int = 8765):
    """Создаёт localhost-only HTTPServer; caller отвечает за serve_forever()."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if host not in {"127.0.0.1", "::1"}:
        raise ValueError("HCO dashboard is localhost-only")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                body = b'{"status":"ok"}'
                content_type = "application/json"
            elif self.path == "/api/snapshot":
                body = json.dumps(build_snapshot(ledger_path, store_path), ensure_ascii=False).encode("utf-8")
                content_type = "application/json"
            elif self.path == "/":
                body = _HTML.encode("utf-8")
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


_HTML = """<!doctype html><meta charset=utf-8><title>HCO Dashboard</title>
<style>body{font:16px system-ui;background:#0b1020;color:#e5e7eb;max-width:1100px;margin:32px auto}pre{background:#111827;padding:16px;border-radius:8px;overflow:auto}.cards{display:flex;gap:12px;flex-wrap:wrap}.card{background:#172033;padding:14px;border-radius:8px;min-width:150px}</style>
<h1>HCO Dashboard <small id=status>loading</small></h1><p>Локальный read-only dashboard. Actual tokens появляются только когда provider передал usage.</p><div class=cards id=cards></div><h2>Последние события</h2><pre id=events></pre>
<script>async function load(){let s=await fetch('/api/snapshot',{cache:'no-store'}).then(r=>r.json()),x=s.summary;status.textContent='telemetry.v2';cards.innerHTML=Object.entries(x).filter(([k])=>k!=='actual_tokens').map(([k,v])=>`<div class=card><b>${k}</b><br>${v}</div>`).join('')+`<div class=card><b>actual tokens</b><br>${x.actual_tokens.total??'UNKNOWN'}</div>`;events.textContent=JSON.stringify(s.events,null,2)}load();setInterval(load,3000)</script>"""


def main() -> None:
    """Запускает локальный dashboard для HCO_HOME или указанного пути."""
    import argparse
    import os

    parser = argparse.ArgumentParser(description="HCO localhost-only telemetry dashboard")
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("HCO_HOME", Path.home() / ".hermes" / "hco")))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = create_server(args.home / "telemetry.sqlite3", args.home / "store.sqlite3", port=args.port)
    print(f"HCO dashboard: http://127.0.0.1:{server.server_address[1]}")
    server.serve_forever()


if __name__ == "__main__":
    main()
