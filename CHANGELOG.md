# Changelog

## 0.1.11 — 2026-08-15

### Added

- Cache-stable proactive expansion: selected fragments are appended at the request tail without rewriting previous messages.
- BM25-like length-normalized lexical retrieval with a score-gap confidence gate.
- Bounded top-k selection with adjacent fragments (`±1`) for local context.
- Structured evidence namespaces for `LOG`, `CODE`, `CFG`, `DEP`, `DOC`, `SEC`, `MKT`, `MET`, `API`, `DAT`, and `OPS` in addition to the existing namespaces.
- `TelemetryLedger.metrics()` with `compression_rate` and `fallback_rate` derived from the append-only ledger.
- Regression coverage for prefix preservation, long-fragment bias, neighboring context, telemetry aggregation, and realistic evidence namespaces.

### Verification

- Source suite: 90 passed; optional host-source integration skipped in the standalone run.
- Hermes discovery integration: 1 passed against the installed Hermes source.
- Decision-grade model matrix: baseline 60/60 and HCO 60/60, with zero provider errors and zero unknown evidence IDs.
- Large-prefix provider-cache study: 89.50% observed billing cache saving; bounded observed HCO request cost was 93.50% lower than the warm large-context baseline in the tested environment.

### Limitations

- Model, provider, cache behavior, pricing, and latency results are environment-specific.
- macOS and Linux have automated package/build coverage; live Hermes Gateway/profile canaries remain Windows-only.
- This remains a prerelease candidate for bounded testing, not a universal production recommendation.
