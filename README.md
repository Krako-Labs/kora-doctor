<p align="center">
  <img src="assets/readme-hero.webp" alt="KORA Doctor: navy-and-coral wordmark framed by flying octopus doctors diagnosing AI agent execution, with five waste-detection labels" width="100%" />
</p>

<p align="center"><strong>Diagnose suspicious AI agent work. Review the evidence before changing the workflow.</strong></p>

<p align="center">A local-first CLI that turns agent usage records into evidence-backed execution-waste candidates.</p>

<p align="center">
  <a href="https://github.com/Krako-Labs/kora-doctor/releases/tag/v0.1.11"><img src="https://img.shields.io/github/v/release/Krako-Labs/kora-doctor?style=flat-square&amp;label=release&amp;color=05264B" alt="Release" /></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square" alt="Python 3.9+" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-E85B50?style=flat-square" alt="Apache-2.0 license" /></a>
  <img src="https://img.shields.io/badge/AUDR-v1.0.0-07897B?style=flat-square" alt="AUDR v1.0.0" />
  <img src="https://img.shields.io/badge/OTel-optional-7952B3?style=flat-square" alt="OTel optional" />
  <img src="https://img.shields.io/badge/demo-offline-176B50?style=flat-square" alt="Offline demo" />
</p>

<p align="center"><strong>No dashboard · No account · No hosted service · No runtime dependencies</strong></p>

<br />

> **AUDR answers:** What ran, and what did it cost?
>
> **KORA Doctor asks:** Which execution steps should you inspect for possible waste?

KORA Doctor reads [AUDR](https://openaudr.dev/) JSON/JSONL and reports **candidate findings with evidence and confidence**. It does not modify an agent, automatically skip calls, or promise savings.

## Contents

- [Quick start](#quick-start) — run the included demo before exporting a trace
- [Example output](#example-output) — understand the report in seconds
- [What it detects](#what-it-detects) — see the waste patterns covered
- [How to read findings](#how-to-read-findings) — separate observations from estimates
- [Bring your own trace](#bring-your-own-trace) — AUDR and optional OpenTelemetry
- [Limits and privacy](#limits-and-privacy) — know what the tool cannot prove
- [Contribute](#contribute) — submit real false positives and missing cases
- [Technical reference](#technical-reference) — detailed rules, input behavior, and assumptions

<br />

## Quick start

**1. Install** — requires Python 3.9+ and [`pipx`](https://pipx.pypa.io/).

```bash
pipx install git+https://github.com/Krako-Labs/kora-doctor.git@v0.1.11
```

**2. Try the offline demo** — no API key, account, or trace file needed.

```bash
kora-doctor demo
```

**3. Audit your own trace.**

```bash
kora-doctor audit audr.jsonl
```

The demo uses **synthetic data**. It shows how to read the tool's findings, not proven savings.

<details>
<summary><strong>Install without pipx</strong></summary>

Requires Python 3.9+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install git+https://github.com/Krako-Labs/kora-doctor.git@v0.1.11
kora-doctor demo
```

</details>

<br />

## Example output

```text
SYNTHETIC DEMO — example data, not live measurements.

KORA Doctor
Observed: 7 records · 1 run · 5 model calls · 2 tool calls
Observed cost:                     $0.0660
Illustrative savings:             $0.0130 (20%)

Execution waste candidates
--------------------------------------------
Repeated tool/read calls          1
Context amplification             2
Deterministic candidates          1
Smaller-model candidates          2

Top findings
--------------------------------------------
[MEDIUM] LLM used for deterministic validation
[LOW   ] Repeated read: fetch_customer_profile
[LOW   ] Input context amplification
```

**Interpretation:** The report identifies where to investigate first. It does **not** establish that the flagged calls can safely be removed. The amounts above come from a synthetic fixture.

<br />

## What it detects

| Signal | What Doctor looks for |
|---|---|
| **Repeated calls** | Repeat tool reads, repeated inference, and cross-run reuse candidates |
| **Retry overhead** | Explicit retry links, cautious OTel-inferred retries, and HTTP 429 chains |
| **Context & cache** | Context amplification and observed prompt-cache reuse |
| **Dead work & loops** | Explicitly unconsumed outputs, re-planning loops, and unchanged refresh candidates |
| **Deterministic work** | Possible schema/validation tasks that may not need an LLM |
| **Model choice** | Short tasks worth evaluating on a smaller model |

**Fix possible execution waste first. Consider smaller models second.**

<br />

## How to read findings

| Evidence label | Meaning |
|---|---|
| ![OBSERVED](https://img.shields.io/badge/OBSERVED-from_trace-147D75?style=flat-square) | Recorded events, usage, or reported costs |
| ![CANDIDATE](https://img.shields.io/badge/CANDIDATE-needs_review-E29C20?style=flat-square) | A heuristic flags a pattern worth investigating |
| ![ESTIMATED](https://img.shields.io/badge/ESTIMATED-illustrative_only-64748B?style=flat-square) | Scenario-based savings, not measured bill reductions |
| ![INSUFFICIENT](https://img.shields.io/badge/INSUFFICIENT-evidence-BA4B53?style=flat-square) | The trace does not support a firm conclusion |

A `LOW` or `MEDIUM` confidence finding is **not** permission to delete a call. A timeout does not prove failure. A repeated read can be a legitimate freshness or safety check.

<br />

## Bring your own trace

**AUDR v1.0.0** — a JSON object, a JSON array, or JSONL:

```bash
kora-doctor audit audr.jsonl --json
```

**Optional OpenTelemetry / OTLP JSON sidecar** — use span status and available fingerprints to enrich the AUDR findings:

```bash
kora-doctor audit audr.jsonl --otel trace.json --json
```

Doctor hashes supplied raw tool arguments/results in memory when processing supported OTel fields; they are not copied to its report. Optional data can strengthen evidence, but unknown or conflicting evidence is handled conservatively.

<details>
<summary><strong>More: supported evidence and input behavior</strong></summary>

- `attribution.labels` can hold optional argument/result hashes, retry relationships, consumption flags, and freshness labels.
- Input errors (such as invalid JSON/UTF-8 or duplicate `record_id`) return exit code `2` with a concise error.
- KORA Doctor is an analyzer, **not** the official AUDR schema validator.
- [Explore the example AUDR traces](samples) or run `kora-doctor --help`.

</details>

<br />

## Limits and privacy

- **Private by design:** the CLI runs locally and does not upload your trace to a KORA-hosted service.
- **Do not assume anonymization:** input files *and JSON reports* may contain identifiers, names, or usage metadata. Review them before sharing.
- **Findings are hypotheses:** metadata cannot prove two prompts were semantically identical or a call was unnecessary.
- **Savings are illustrative:** `potential_savings` is not validated savings; `savings_basis` marks the assumption.
- **No automatic optimization:** no execution changes, call skipping, or model rerouting in v0.

<br />

## Contribute

**The most useful contribution is a reproducible false positive or a missed case.**

- [Share a safely sanitized real trace](https://github.com/Krako-Labs/kora-doctor/issues/8) — read the [privacy checklist](docs/REAL_TRACE_INTAKE.md) first
- [Report a bug or detection gap](https://github.com/Krako-Labs/kora-doctor/issues)
- [Read the contribution guide](CONTRIBUTING.md)

KORA Doctor is an independent open-source project built on AUDR-compatible records. It does not require the full KORA runtime.

**License:** [Apache-2.0](LICENSE) · **Maintained by:** [Krako Labs](https://github.com/Krako-Labs)

<br />

## Technical reference

See the [technical reference](docs/TECHNICAL_REFERENCE.md) for detailed retry lineage, OTel status and fingerprint rules, confidence, context growth, illustrative cost assumptions, and optional evidence fields. Advanced material remains available without slowing down first-time readers.
