# KORA Doctor — Technical Reference

[← Back to the quick-start README](../README.md)

This reference describes the current development tree: diagnostics, optional metadata, input validation, and savings assumptions. The published `v0.1.11` may differ until local corrections are released. Findings are candidates, not evidence of actual saved costs or permission to delete calls.

---

## What it looks for

KORA Doctor now prioritizes execution waste before model downsizing:

- **Repeated tool/read calls** — the same AUDR tool/resource and operation repeating inside one run.
- **Context amplification** — input-token growth that can indicate carried retrieval results, growing context, or repeated static tool definitions.
- **Prompt cache efficiency** — observed cache-read share from AUDR cache counters, surfaced without assuming one universal 'good' threshold.
- **Deterministic validation** — validation/schema/format work that may belong in JSON Schema, parsing, or ordinary code instead of an LLM.
- **Repeated inference** — model/resource and usage signatures repeating inside one execution, scoped by `run_id` and `trace_id` when available.
- **Cross-run reuse candidates** — similar signatures appearing across multiple reported run IDs.
- **Suspicious orchestration overhead** — long model-call chains that deserve inspection.
- **Smaller-model candidates** — considered after execution waste is removed.

**Fix execution waste first. Downsize models second.**

The goal is to narrow a trace down to the execution steps a developer should inspect first.

### Context-growth shape

Context growth can show up in more than one way. KORA Doctor keeps the existing
step-to-step amplification trigger for sharp local jumps, and also surfaces a
low-confidence cumulative-growth candidate when input grows substantially
relative to the first reported model call even though no single transition
crosses that local trigger.

The finding reports baseline-relative growth and the largest step-to-step
delta. It still claims no savings from token growth alone because the trace
does not prove which carried payload, if any, was unnecessary.

For records without `run.step`, Doctor compares timezone-aware `event_time`
values as UTC instants. Lexically sorting ISO strings with different offsets
can reverse the actual call order and hide context growth. Missing or invalid
timestamps come after known timestamps. When both a step and a usable timestamp
are missing, original record order is only a fallback, not reliable chronology.

## Why the output says “candidate”

AUDR v1.0.0 deliberately records usage/cost telemetry without prompt content or secrets. That is good for security, but it also means an AUDR record alone usually cannot prove that two model calls were semantically identical or that a task could definitely have been deterministic.

KORA Doctor therefore uses:

- **observed** for values directly present in the trace
- **estimated** for derived savings
- **candidate** for optimization opportunities
- **confidence** for heuristic strength
- **insufficient evidence** where the trace cannot support a stronger conclusion

Cost fields and currency totals must fit the finite numeric reporting range. An extremely large integer or a sum that would produce `Infinity` fails with exit code `2`; the error does not echo trace values. Ordinary missing or malformed cost fields remain non-evidence rather than fabricated amounts.

Savings are only calculated when `cost.total_cost` is present. Multiple currencies are never silently converted. **Illustrative savings are not measured savings.** Do not remove or reroute calls without replay and quality checks. In `--json`, the existing `potential_savings` and `estimated_optimized_costs` keys are preserved for compatibility, and `savings_basis=illustrative_heuristic_not_measured` explicitly marks their meaning.

### Current savings assumptions

The dollar estimate is a scenario estimate attached to each candidate, not a measured future bill:

- repeated tool/read candidate: not included in savings without stronger request/freshness evidence
- context amplification candidate: not included in savings without payload evidence
- matching model usage signature in one run: **0%**; token counters cannot prove identical inputs or redundant work
- cross-run matching usage signature: **0%**; counters cannot prove reusable inputs or a safe prompt-cache hit
- deterministic candidate: **0%** (diagnostic only; even explicit schema checks need outcome validation before attributing avoidable cost)
- orchestration-overhead candidate: **0%**; call count does not prove a step was unnecessary
- smaller-model candidate: **0%**; call length and missing reasoning telemetry do not prove that substitution is safe

If one call matches several rules, KORA Doctor keeps the largest ratio for that record; it never stacks estimates. **Current metadata-only rules assign no quantifiable savings.** Reported `0%` reflects missing proof of avoidable work, **not** proof that an execution was optimal. Keep `potential_savings` and `estimated_optimized_costs` keys for existing JSON consumers. Only introduce nonzero attribution after safe, independent outcome evidence and measured cost comparisons.

## Model usage signatures and identifier integrity

Matching model, operation and token counters are a **low-confidence investigation signal**, not proof that two prompts or outputs match. Both within-run duplicate candidates and cross-run reuse candidates contribute **0 illustrative savings**. These findings remain visible so developers can compare them against their own verified workload evidence. Do not assume a 100% removable call or 70% cache benefit based on counter equality.

The input parser rejects non-string, empty or whitespace-only `record_id`, `run.run_id`, and `run.span_id`. If `run.trace_id` is included, it must be a non-empty string without surrounding whitespace. Only canonical hexadecimal UUID trace identities are case/hyphen normalized; arbitrary trace labels retain their exact case. This prevents numeric/string and opaque-label collisions; parser errors do not repeat identifier values. Missing optional `trace_id` is allowed.

## Optional tool fingerprints

AUDR intentionally avoids raw tool arguments and results. KORA Doctor can still use privacy-preserving fingerprints when a trace source places hashes in `attribution.labels`:

```json
{
  "attribution": {
    "labels": {
      "tool_args_hash": "sha256:...",
      "tool_result_hash": "sha256:..."
    }
  }
}
```

Tool argument/result equality only uses `sha256:` followed by 64 hexadecimal characters. Other strings (including `redacted`, placeholders, or incomplete hashes) are treated as missing fingerprint evidence. Format validation cannot prove the hash was generated honestly or that matching payloads imply safe call removal.

When the same tool repeats in one run (and the same trace, if trace IDs are available):

- same tool, no fingerprint -> low-confidence repeat candidate
- same tool + same args hash, but missing or differing result hashes -> **low-confidence** repeat candidate, not confirmed duplicate output
- same tool + same args hash + same result hash -> medium-confidence exact repeated-work evidence (still not proof of unnecessary work)
- same tool + different argument hashes -> **not** treated as a duplicate

KORA Doctor never needs the raw tool payload for this check, and fingerprint-based findings still do not claim guaranteed savings because freshness and safety checks can make a repeat legitimate. A child call in a verified explicit retry lineage is recorded as retry overhead, not also as an independent repeated read; unrelated later reads remain eligible for review.

## Validation candidates: structural vs semantic

Doctor gives a small validation call **medium** diagnostic confidence only when its **entire run name** consists of a structural check verb and structural words (for example, `Validate JSON schema` or `Check JSON format`). Extra subjective goals, even if their wording is unfamiliar, keep the finding **low**. A JSON transport label is not task evidence, and `validate JSON response` can describe subjective correctness rather than syntax checking.

A generic `validate customer intent` task remains a **low** confidence manual-review finding. Mixed tasks such as `validate JSON format for medical correctness and patient safety` also remain low-confidence: a structural keyword does not override an explicitly semantic goal. All deterministic candidates now contribute **zero** illustrative savings, even when the format is explicit: metadata alone cannot establish that the model can be replaced safely. Words must match at their start; `information` does not imply `format`. Conflicting fingerprint aliases are not accepted as matching payload evidence. Trace IDs remove hyphens only when they are canonical hexadecimal UUIDs; opaque identifiers preserve their original case.

## OTel sidecar enrichment

KORA Doctor can combine portable AUDR cost/usage records with richer OpenTelemetry execution evidence:

```bash
kora-doctor audit audr.jsonl --otel trace.json
```

The sidecar accepts standard OTLP JSON `resourceSpans -> scopeSpans -> spans` as well as simpler JSON span arrays.
OTel JSON integer literals longer than 128 characters are rejected as invalid input, including in unmatched spans. This avoids interpreter-dependent crashes; the CLI returns exit code `2` without printing raw trace data.

When matching spans are found by `span_id` / `trace_id`, KORA Doctor can enrich the AUDR record with:

- SHA-256 fingerprints of `gen_ai.tool.call.arguments`
- SHA-256 fingerprints of `gen_ai.tool.call.result`
- OTel span status as per-operation status
- optional retry / consumption / planning attributes when present
- inferred retry lineage when the same hashed tool call follows an explicit failed/timeout/unknown span

Raw tool arguments and results are hashed in memory and are not copied into the KORA Doctor report.

For OTel fingerprinting, JSON objects and arrays are canonicalized by sorting JSON keys and removing formatting whitespace. **Raw tool text preserves its whitespace.** Even when an OTel string happens to contain a valid JSON scalar such as `42`, `true`, `null`, or a quoted string, it is hashed as text rather than converted to the scalar type. This prevents distinct tool text and typed scalar values from certifying an exact repeat. A matching digest does not prove the second call was unnecessary.

OTel matching is conservative. OTel `spanId`/`span_id` and `traceId`/`trace_id` must be strings when supplied; malformed scalar identifiers are rejected without echoing their values. If both spellings are supplied for the same identifier, their canonical identities must agree; conflicting pairs cause a private input error (exit code `2`) even on an otherwise unmatched span. Equivalent canonical-hex spellings remain supported. Canonical 16-hex span IDs and 32-hex or UUID trace IDs may be case/UUID normalized. **Opaque span and trace IDs must match exactly**; case changes, punctuation removal, or whitespace trimming cannot establish that two IDs refer to the same execution. This same identity rule controls OTel-inferred retry grouping. When both sides report trace IDs, they must agree. A repeated or ambiguous span match is ignored rather than assigning status or argument evidence to the wrong run. A unique span may match without a trace ID **only when both AUDR and OTel omit the trace ID**. A trace ID reported by just one side is not sufficient proof to attach sidecar evidence. Existing optional AUDR labels are preserved.

If AUDR and OTel disagree about a matching record's status, argument fingerprint, retry link, or other imported label, Doctor skips **all OTel enrichment for that record**. It keeps the original AUDR fields and reports the count of skipped source conflicts in CLI warnings. A conflict is not evidence that one source is correct or that the operation can be removed.

Inferred retry links require one run, one trace, unique record IDs, **OTel-observed argument fingerprints for both calls and an OTel-observed failure on the earlier call**, and strictly ordered, timezone-aware timestamps. AUDR-only hashes, statuses, or ID-matched empty spans do not establish OTel-inferred retry evidence. These links carry a `retry_inferred_by_otel` marker and remain low-confidence candidates. Only **explicit** retry links can group HTTP 429 attempts into one logical operation. Neither form alone proves an avoidable retry or cost savings.

This keeps AUDR as the portable usage/cost layer while letting OTel/traceAI provide stronger execution evidence.

## Optional dead-work evidence

KORA Doctor does not infer unused outputs from timing alone. Richer trace sources can provide explicit downstream-consumption evidence:

```json
{
  "attribution": {
    "labels": {
      "output_consumed": "false",
      "step_role": "planner",
      "planned_step_count": "5",
      "executed_step_count": "2"
    }
  }
}
```

This enables two conservative findings:

- outputs explicitly marked as unconsumed
- planners that explicitly report more planned steps than executed steps

These findings stay out of savings estimates until a real replay/evaluation proves the work can be removed safely.

## Optional retry lineage

AUDR v1.0.0 has run-level errors and outcomes, but not per-operation retry lineage. Richer trace sources can preserve retry evidence in `attribution.labels`:

```json
{
  "attribution": {
    "labels": {
      "tool_args_hash": "sha256:...",
      "retry_of": "01KD...",
      "retry_attempt": "2",
      "operation_status": "timeout"
    }
  }
}
```

KORA Doctor resolves `retry_of` against either a prior `record_id` or `span_id` only within the same run and matching trace identity (if both trace IDs are absent, that is treated as one unknown trace domain). If one record has a trace ID and the other does not, or the trace IDs disagree, Doctor does not connect them. Confidence increases when retry lineage, matching argument fingerprints, and prior per-operation status all agree.

A retry is reported as overhead evidence, not automatically as removable waste. Timeouts, idempotency, and unknown remote outcomes can make a retry necessary.

### HTTP 429 retry chains (advisory)

When an instrumented AUDR record or matched OTel span includes `http_status_code` (or `http.response.status_code` / `http.status_code`), Doctor also recognizes **linear chains of explicit retries** if every link stays in one run, uses matching tool/argument fingerprints, and has ordered timestamps.

For example, three HTTP 429 attempts followed by HTTP 200 produce **one chain finding with four attempts**, not four independent failed logical operations. The finding reports the observed number of rate-limit responses and the last known outcome. Chains without explicit retry links, with ambiguous/branching lineage, or without matching argument fingerprints are not grouped. Attempts after the root of a confirmed HTTP 429 chain are **not also counted as repeated tool/read waste candidates**. A separate, unlinked call to the same tool can still be reported as a repeat.

This is a candidate-level diagnostic and claims **no savings**. HTTP status alone does not prove why a retry was scheduled. Doctor does not copy raw Retry-After or rate-limit headers into its report, and does not claim a header conflict from status codes alone. If the final HTTP status is missing, the outcome remains uncertain. This lightweight instrumentation is optional and does not change the AUDR v1.0.0 schema.


## Prompt cache metrics

When AUDR records include prompt-cache counters, KORA Doctor reports observed reuse directly:

```text
Prompt cache reuse: 0%  (0 read / 9,600 uncached; 3 calls)
```

`usage.llm.input_tokens` in AUDR v1.0.0 is uncached input and excludes cache reads. KORA Doctor therefore calculates cache-read share from `input_tokens + cache_read_tokens`.

It does **not** hard-code a universal cache-hit target. A low reuse rate becomes more interesting when the same run also shows persistent high context or context amplification.

Context-growth, cache, and short-call findings require valid numeric usage counters. JSON booleans such as `"input_tokens": true` are **not** token counts. Doctor treats malformed token values as missing evidence for those findings and for structural-validation confidence, rather than treating them as `0` or `1`. This prevents unsupported findings; it does not validate the entire AUDR schema.

If a resource identifier or a numeric counter contains a JSON array/object instead of a scalar, or `usage.llm` / `usage.tool` is not an object, Doctor rejects the malformed structure with **exit code 2** and a field-only error. It does not print the input value or an internal traceback. Other malformed scalar counters remain missing evidence, not measurements.

## AUDR compatibility

KORA Doctor currently targets **AUDR v1.0.0** and accepts:

- one AUDR JSON object
- a JSON array of AUDR objects
- JSONL with one AUDR object per line

It checks the core fields needed for analysis, but it is **not** a replacement for the official AUDR JSON Schema conformance validator.

AUDR upstream currently provides adapters for LiteLLM, Vercel AI SDK, Mastra, NVIDIA NeMo Relay, and Merge Gateway, plus sinks including Chargebee.

## Share a real trace

A real, correctly sanitized trace is more useful than another synthetic test. The tool runs locally, but **neither the AUDR input nor the `--json` report is automatically anonymized**. Follow the [real trace intake and privacy guide](REAL_TRACE_INTAKE.md) before sharing anything to [Issue #8](https://github.com/Krako-Labs/kora-doctor/issues/8).

## Samples

```bash
python3 -m kora_doctor audit samples/simple.jsonl
python3 -m kora_doctor audit samples/multi_step.jsonl
python3 -m kora_doctor audit samples/inefficient_agent.jsonl
python3 -m kora_doctor audit samples/harness_waste.jsonl
python3 -m kora_doctor audit samples/cache_instability.jsonl
python3 -m kora_doctor audit samples/tool_fingerprints.jsonl
python3 -m kora_doctor audit samples/retry_lineage.jsonl
python3 -m kora_doctor audit samples/http_429_retry_chain.jsonl
python3 -m kora_doctor audit samples/unused_work.jsonl
```

The samples are synthetic AUDR-compatible traces created for KORA Doctor. The inefficient trace is intentionally constructed to trigger multiple heuristics.

## Local development

KORA Doctor has no runtime dependencies.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
./scripts/demo.sh
```

## Limitations

- AUDR v1.0.0 does not include normalized tool arguments or prompt bodies, so repeated tool/read and duplicate-model findings remain candidates.
- Context amplification can be observed from token growth, but AUDR alone cannot identify the exact carried payload.
- Prompt-cache metrics are only available when the adapter/provider reports `cache_read_tokens` / `cache_write_tokens`; KORA Doctor does not assume that missing cache telemetry means zero reuse.
- Deterministic candidates are inferred from run names, labels, and reported usage only.
- Smaller-model recommendations do not benchmark output quality.
- Estimated savings are scenario estimates, not guaranteed savings.
- KORA Doctor does not modify your agent or automatically reroute traffic in v0.

These are deliberate v0 constraints. If real traces show that an extra signal is necessary, we will add the smallest useful one.

## Relationship to KORA

KORA Doctor is standalone. It does **not** require the full KORA runtime.

Today:

```text
AUDR trace -> KORA Doctor -> diagnose
```

If users pull for it later:

```text
AUDR trace -> diagnose -> recommend -> optimize automatically with KORA
```


## Additional optional evidence

### Conditional consumers and unused output

Optional AUDR `attribution.labels` (also accepted from matching OTel span attributes):
`output_consumed=false`, `conditional_consumer_exists=true`, and
`no_downstream_consumer=true`. These distinguish output not consumed **in the
observed run** from a dormant fallback/approval/error path and an explicitly
reported absence of any downstream consumer. Without the last signal, unused
output remains a low-confidence candidate, **not proven dead work**. These
findings never contribute to estimated savings. Example:

```bash
kora-doctor audit samples/conditional_consumers.jsonl
```


### Re-planning loop candidates (explicit evidence only)

To inspect planning unchanged after a non-empty tool result, attach stable digests
of **action lists**, never prose or raw payloads, as `attribution.labels`:
`plan_before_hash`, `plan_after_hash`, `next_action_before_hash`,
`next_action_after_hash`, `nonempty_result=true`, and `expected_repeat=false`.
The last label must be supplied by the instrumented runtime; when unknown or
true, KORA Doctor does not flag an intentional polling/retry-with-backoff loop.
These are candidates, not validated waste or cost savings.

```bash
kora-doctor audit samples/replanning_loops.jsonl
```


### Observed source-refresh repetition

Optional `attribution.labels` (also accepted in OTel span attributes):
`source_refresh=true`, `source_id_hash`, and `source_snapshot_hash`.
Three or more timestamped refreshes for the same provider, resource and
hashed source, including two consecutive unchanged transitions, allow a
low-confidence *unchanged refresh* candidate. Snapshot hashes must cover
equivalent, stable content. This is **not** a safe TTL recommendation: changes
between observations remain invisible. No dollar savings are estimated.

```bash
kora-doctor audit samples/freshness_refresh.jsonl
```

## Contributing

Real AUDR traces, false positives, and missed optimization opportunities are the highest-value feedback.

See [CONTRIBUTING.md](../CONTRIBUTING.md) or open an issue.

## License

Apache-2.0.
