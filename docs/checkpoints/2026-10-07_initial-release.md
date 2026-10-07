# KORA Doctor checkpoint — 2026-10-07

## Current state

KORA Doctor v0.1.0 is public at https://github.com/Krako-Labs/kora-doctor.

Local canonical project:
- /Volumes/MSM2-1-DATA/Projects/80_KORA-Doctor

## Shipped

- standalone Python CLI with no runtime dependencies
- AUDR v1.0.0 JSON, JSON array, and JSONL ingestion
- duplicate/repeated inference candidates
- cache/reuse candidates
- deterministic candidates
- smaller-model candidates
- suspicious orchestration-overhead candidates
- observed cost plus scenario-based potential savings
- confidence labels and explicit limitations
- machine-readable JSON output
- 3 synthetic sample traces
- README terminal demo asset
- Apache-2.0 license
- launch copy for Hacker News, X, and Reddit

## Verification

- 11/11 automated tests pass
- malformed input fails with exit code 2
- official AUDR multi-emitter fixture parses and audits successfully
- fresh local package install passes
- clean public clone from GitHub passes
- isolated install directly from the public GitHub URL passes
- README demo asset is present through the GitHub API

## False positives removed before release

- emitter component name such as router is not treated as deterministic-task evidence
- explicitly low-cost model tiers such as mini, nano, haiku, flash, and small are excluded from the high-end-model heuristic

## Known limitations

AUDR intentionally does not include prompt bodies. KORA Doctor therefore cannot prove semantic duplication, deterministic replaceability, or model sufficiency from AUDR alone. Findings are heuristic candidates.

Savings are scenario estimates, not guaranteed bill reductions. Overlapping findings never stack; the largest per-call saving assumption wins.

## Baseline traction immediately after public push

- stars: 0
- forks: 0
- open issues: 0
- watchers: 0

This is the measurement baseline, not a success/failure judgment.

## Next step

Do not add a dashboard, hosted service, full KORA integration, generalized plugin system, or more architecture.

Release v0.1.0, distribute into the current AUDR conversation, and let real traces determine the next heuristic or integration.
