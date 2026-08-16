# Changelog

## 0.1.11 — 2026-08-15

### Added

- Cache-stable proactive expansion: selected fragments are appended at the request tail without rewriting previous messages.
- BM25-like length-normalized lexical retrieval with a score-gap confidence gate.
- Bounded top-k selection with adjacent fragments (`±1`) for local context.
- Structured evidence namespaces for `LOG`, `CODE`, `CFG`, `DEP`, `DOC`, `SEC`, `MKT`, `MET`, `API`, `DAT`, and `OPS` in addition to the existing namespaces.
- `TelemetryLedger.metrics()` with `compression_rate` and `fallback_rate` derived from the append-only ledger.
- Regression coverage for prefix preservation, long-fragment bias, neighboring context, telemetry aggregation, and realistic evidence namespaces.
- Fail-closed mixed explicit-ID and lexical-facet coverage.
- Structural validation for canonical HCO compact envelopes; malformed, nested, duplicate, mismatched, unclosed, and stray markers are rejected.

### Verification

- Standalone source suite: 104 passed, 1 optional Hermes discovery test skipped when host source is absent.
- Separate Hermes discovery integration: 1 passed against the installed Hermes source; combined suite count with host source available is 105 passed.
- Decision-grade model matrix: baseline 120/120 and HCO 120/120 across six repeats, with zero provider errors and zero unknown evidence IDs.
- Provider billing reconciliation for the same 240 accepted attempts: baseline `$0.186496`, HCO `$0.012073`, actual saving 93.53% (15.45×), based on a hash-addressed usage export and exact token-pair correlation.
- Large-prefix provider-cache study: 89.50% observed billing cache saving; the observed small HCO request cost was 93.50% lower than the warm large-context baseline in a separate descriptive cross-run comparison (not a paired controlled measurement).

### Limitations

- Model, provider, cache behavior, pricing, and latency results are environment-specific.
- macOS and Linux have automated package/build coverage; live Hermes Gateway/profile canaries remain Windows-only.
- This remains a prerelease candidate for bounded testing, not a universal production recommendation.
