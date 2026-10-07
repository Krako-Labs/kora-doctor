# KORA Doctor checkpoint — 2026-10-07

## Goal

Enter the AUDR / agent-cost / agent-observability attention window with a useful public OSS CLI.

## Product

KORA Doctor answers: **Which inference calls may not have been necessary?**

It consumes AUDR-compatible JSON/JSONL and reports heuristic candidates with confidence labels.

## v0 scope

- duplicate/repeated model calls
- cross-run cache/reuse candidates
- deterministic candidates inferred only from safe metadata
- smaller/cheaper-model candidates
- suspicious orchestration overhead
- observed vs estimated savings when AUDR cost fields exist

## Known limitation

AUDR intentionally does not carry prompt content. KORA Doctor therefore cannot prove semantic equivalence or necessity from AUDR alone. It reports candidates, never certainty.

## Stop rule

After public repo, install smoke test, samples, README, and release are working: stop adding architecture and measure user pull.
