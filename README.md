# KORA Doctor

**Find execution waste in AI agent runs.**

AUDR can tell you what an agent ran and what it cost. KORA Doctor asks the next question:

> **Which parts of that execution should disappear?**

KORA Doctor is a small open-source CLI that analyzes [AUDR](https://openaudr.dev/) JSON/JSONL and surfaces waste across tool usage, context growth, deterministic work, repeated inference, orchestration, and model choice.

No dashboard. No account. No hosted service.

![KORA Doctor terminal demo](assets/demo.svg)

## Quick start

Install directly from GitHub:

```bash
pipx install git+https://github.com/Krako-Labs/kora-doctor.git
```

Then audit an AUDR file:

```bash
kora-doctor audit audr.jsonl
```

From a clone:

```bash
python3 -m kora_doctor audit samples/inefficient_agent.jsonl
```

Machine-readable output:

```bash
kora-doctor audit audr.jsonl --json
```

## Example

The v0.1.1 harness sample is synthetic and exists to exercise the heuristics. It is not a benchmark.

```text
KORA Doctor
Find execution waste in AI agent runs.

Observed: 7 records · 1 runs · 5 model calls · 2 tool calls
Observed cost:                      $0.0660
Potentially avoidable:              $0.0130  (20%)
Estimated optimized cost:           $0.0530

Execution waste candidates
-----------------------------------------------
Repeated tool/read calls           1
Context amplification              2
Deterministic candidates           1
Repeated model calls               0
Cross-run reuse candidates         0
Orchestration overhead             0
Smaller-model candidates           2
```

Run it:

```bash
kora-doctor audit samples/harness_waste.jsonl
```

## What it looks for

KORA Doctor now prioritizes execution waste before model downsizing:

- **Repeated tool/read calls** — the same AUDR tool/resource and operation repeating inside one run.
- **Context amplification** — input-token growth that can indicate carried retrieval results, growing context, or repeated static tool definitions.
- **Deterministic validation** — validation/schema/format work that may belong in JSON Schema, parsing, or ordinary code instead of an LLM.
- **Repeated inference** — model/resource and usage signatures repeating inside a run.
- **Cross-run reuse candidates** — similar signatures appearing across multiple runs.
- **Suspicious orchestration overhead** — long model-call chains that deserve inspection.
- **Smaller-model candidates** — considered after execution waste is removed.

**Fix execution waste first. Downsize models second.**

The goal is to narrow a trace down to the execution steps a developer should inspect first.

## Why the output says “candidate”

AUDR v1.0.0 deliberately records usage/cost telemetry without prompt content or secrets. That is good for security, but it also means an AUDR record alone usually cannot prove that two model calls were semantically identical or that a task could definitely have been deterministic.

KORA Doctor therefore uses:

- **observed** for values directly present in the trace
- **estimated** for derived savings
- **candidate** for optimization opportunities
- **confidence** for heuristic strength
- **insufficient evidence** where the trace cannot support a stronger conclusion

Savings are only calculated when `cost.total_cost` is present. Multiple currencies are never silently converted.

### Current savings assumptions

The dollar estimate is a scenario estimate attached to each candidate, not a measured future bill:

- repeated tool/read candidate: not included in savings without stronger request/freshness evidence
- context amplification candidate: not included in savings without payload evidence
- duplicate/repeated model call: 100% of that call's observed cost
- cache/reuse candidate: 70%
- deterministic candidate: 80–90% depending on evidence
- orchestration-overhead candidate: 50%
- smaller-model candidate: 50%

If one call matches several rules, KORA Doctor uses only the largest ratio for that call; it never stacks savings estimates. These defaults are intentionally easy to inspect and change as real traces arrive.

## AUDR compatibility

KORA Doctor currently targets **AUDR v1.0.0** and accepts:

- one AUDR JSON object
- a JSON array of AUDR objects
- JSONL with one AUDR object per line

It checks the core fields needed for analysis, but it is **not** a replacement for the official AUDR JSON Schema conformance validator.

AUDR upstream currently provides adapters for LiteLLM, Vercel AI SDK, Mastra, NVIDIA NeMo Relay, and Merge Gateway, plus sinks including Chargebee.

## Samples

```bash
python3 -m kora_doctor audit samples/simple.jsonl
python3 -m kora_doctor audit samples/multi_step.jsonl
python3 -m kora_doctor audit samples/inefficient_agent.jsonl
python3 -m kora_doctor audit samples/harness_waste.jsonl
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

## Contributing

Real AUDR traces, false positives, and missed optimization opportunities are the highest-value feedback.

See [CONTRIBUTING.md](CONTRIBUTING.md) or open an issue.

## License

Apache-2.0.
