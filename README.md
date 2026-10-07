# KORA Doctor

**Find the LLM calls your AI agent may never have needed.**

AUDR can tell you what an agent run did and what it cost. KORA Doctor asks the next question:

> **Did all of that inference need to happen?**

KORA Doctor is a small open-source CLI that analyzes [AUDR](https://openaudr.dev/) JSON/JSONL and flags calls that may be worth removing, caching, replacing with deterministic logic, or moving to a cheaper model.

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

The included inefficient trace is synthetic and deliberately pathological. It is a demo of the reporting surface, not a benchmark or a claim that typical agents waste 89%.

```text
KORA Doctor
Find the LLM calls your AI agent may never have needed.

Observed: 11 records · 2 runs · 11 model calls · 0 tool calls
Observed cost:                      $0.1050
Potentially avoidable:              $0.0936  (89%)
Estimated optimized cost:           $0.0114

Candidates
-----------------------------------------------
Duplicate/repeated calls           5
Cache/reuse candidates             5
Deterministic candidates          11
Smaller-model candidates          11
Orchestration overhead             5
```

Run the included intentionally inefficient trace to see the full current output:

```bash
kora-doctor audit samples/inefficient_agent.jsonl
```

## What it looks for

KORA Doctor v0 intentionally starts with simple heuristics:

- **Duplicate/repeated inference** — the same model/resource and usage signature repeating inside one run.
- **Cache/reuse candidates** — the same signature appearing across multiple runs.
- **Deterministic candidates** — model calls whose AUDR run metadata looks like classification, routing, validation, extraction, formatting, parsing, or normalization.
- **Smaller-model candidates** — short calls on high-end models with no reported reasoning tokens.
- **Suspicious orchestration overhead** — agent runs with many model calls where later calls deserve inspection.

The goal is not to prove that an inference call was unnecessary. The goal is to narrow a long trace down to the calls a developer should inspect first.

## Why the output says “candidate”

AUDR v1.0.0 deliberately records usage/cost telemetry without prompt content or secrets. That is good for security, but it also means an AUDR record alone usually cannot prove that two model calls were semantically identical or that a task could definitely have been deterministic.

KORA Doctor therefore uses:

- **observed** for values directly present in the trace
- **estimated** for derived savings
- **candidate** for optimization opportunities
- **confidence** for heuristic strength
- **insufficient evidence** where the trace cannot support a stronger conclusion

Savings are only calculated when `cost.total_cost` is present. Multiple currencies are never silently converted.

### Current v0 savings assumptions

The dollar estimate is a scenario estimate attached to each candidate, not a measured future bill:

- duplicate/repeated call: 100% of that call's observed cost
- cache/reuse candidate: 70%
- deterministic candidate: 80%
- smaller-model candidate: 50%
- orchestration-overhead candidate: 50%

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

- No prompt/input fingerprints means duplicate and cache findings are heuristic.
- Deterministic candidates are inferred from run names and labels only.
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
