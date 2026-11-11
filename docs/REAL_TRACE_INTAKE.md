# Share a real KORA Doctor trace safely

**Goal:** validate whether KORA Doctor's findings match actual AI agent behavior. A synthetic sample tests code; it cannot establish real false-positive rates or cost savings.

The CLI runs locally. **KORA Doctor does not anonymize your AUDR file or its JSON report.** A report can include record IDs, run IDs, costs, model names, and finding details. Do not post an unreviewed report or original trace to a public issue.

## Before you share

1. **Export a short real run** in AUDR v1.0.0 JSON/JSONL format. For retry and HTTP 429 diagnostics, include a complete attempt sequence, not just the final error. A 10–50 record excerpt is often enough.
2. **Make a new copy and sanitize it at the source.** Remove prompts, responses, tool arguments/results, user/customer information, access tokens, URLs, organization names, job names, and arbitrary labels that can identify people or internal systems.
3. **Replace identifiers consistently.** If one `record_id` is referenced by a later `retry_of`, replace both with the *same new value*. Keep unique record IDs unique, and preserve same-run/same-trace relationships. Replace `run_id`, `span_id`, `trace_id`, `emitter`, and `resource` values if they disclose private details.
4. **Keep diagnostic evidence.** Preserve event order and time gaps (you may shift all timestamps by one constant offset); resource `type` and operation category; observed usage and costs if permitted; relevant HTTP status, operation status, retry links, and fingerprints. Exact absolute clock time is useful for concurrency but not required if intervals and ordering remain correct.
5. **Inspect the copy manually.** Search for emails, tokens, user IDs, hostnames, URLs, message text, payloads, and company-specific identifiers. Hashes and grouping labels can still enable cross-trace linkage. Use per-submission salted identifiers or omit optional grouping labels when in doubt.
6. **Test the sanitized copy locally** before sharing:
   ```bash
   kora-doctor audit sanitized.jsonl
   kora-doctor audit sanitized.jsonl --json
   ```
   If there is a sanitized OTel sidecar:
   ```bash
   kora-doctor audit sanitized.jsonl --otel sanitized-otel.json --json
   ```
   Read both the sanitized input and the generated report. The report is **not** a sanitizer.

## Minimum useful AUDR fields

| Field | Why it matters |
|---|---|
| `spec_version`, `record_id` | AUDR format and stable unique call identity |
| `emitter`, `timing.event_time` | Source context and order; precise spacing helps burst analysis |
| `resource.provider/type/name/operation` | Distinguishes model calls from tool calls |
| `usage` | Observed token, request, or tool activity |
| `run.run_id`, `run.span_id` | Groups records and links optional OTel spans |
| `attribution` | Contains optional privacy-reviewed labels |

Useful optional evidence: `attribution.labels.retry_of`, `retry_attempt`, `operation_status`, `http_status_code`, `tool_args_hash`, `tool_result_hash`, `run.trace_id`, cost counters and `sr.*` labels when the data owner has approved sharing. Do not include raw inputs just to recreate hashes.

For a valid **synthetic** example and a known HTTP 429 chain, see [`samples/http_429_retry_chain.jsonl`](../samples/http_429_retry_chain.jsonl). It is not a real-user trace or proof of savings.

## Report the expected outcome

Use [Issue #8: Real agent trace](https://github.com/Krako-Labs/kora-doctor/issues/8) for a **publicly shareable sanitized sample**. State the source adapter and version, which events are real versus synthetic, which output is incorrect or missing, and what evidence establishes ground truth (for example, a confirmed retry or a known intentional duplicate). State the sanitizer's effect on timestamps, IDs, or usage counters.

If you cannot safely publish a trace, **do not upload it**. A description of the symptom and a privacy-reviewed synthetic reproduction are useful, but they cannot validate production accuracy.

## What is still unproven

KORA Doctor does not know whether an agent could safely skip a call or replay a timeout without harming correctness. Findings and projected savings are hypotheses. Real traces help test false positives and evidence coverage, but measuring savings requires controlled before/after execution and outcome checks.
