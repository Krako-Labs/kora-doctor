# Feedback Sprint 001 — individual replies and verified engineering scope

Captured 2026-10-15 (Asia/Seoul). Source snapshots: SocialOS `data/socialos.db` read-only, public KORA Doctor GitHub issues, and the KORA Doctor code. This document is a local review artifact; **no SNS post, approval, GitHub push, or release is authorized**.

## Individual signals mapped to product work

| Reporter / platform | Actual signal | Doctor issue | Current disposition |
| --- | --- | --- | --- |
| Ali Asghar / LinkedIn | Identical metadata cannot prove equal prompts; repeated retrieval is a more compelling signal | #1, #4 | Fixed a mixed-fingerprint false positive **locally**; stronger evidence still needed for semantic identity |
| Taimoor Taak / LinkedIn | Metadata-only similarity is a candidate, not a semantic duplicate | #1, #4 | Preserve evidence/confidence labels; no new outreach solely to repeat a consensus point |
| Jarrod De Lange / LinkedIn | Successful retrieval replayed after downstream failure triggered whole-chain retry | #1, #4 | Explicit retry-of-successful-operation supported *when lineage is supplied*; causal origin not inferred without trace. Final reply already posted and verified by SocialOS (reply 7515934673735188480). **No duplicate reply** |
| u/verstands / Reddit | Argument/result fingerprint, unknown outcomes, missed planning optimization, conservative false-positive handling | #2, #4 | Existing fingerprint + retry-status support; mixed fingerprints and span collisions fixed **locally** |
| u/KimLikeJ / Reddit | Context carry-over, unused output, rare conditional consumer, local vs cumulative growth | #3 | Existing v0.1.10 local/cumulative diagnostics + conditional consumer awareness; prior follow-ups logged in SocialOS. No redundant post |
| u/Most-Agent-7566 (Acrid) / Reddit | Shared quota contention, wake-up bursts, rate-limit ambiguity, safe source freshness | #5, #8 | **Waiting for privacy-scrubbed real fixture**. No guessed quota bucket, TTL, or production savings; already engaged repeatedly |
| u/dylan_skydive / Reddit | Plan/next-action fingerprints; polling and backoff exceptions | #2 | Existing cautious replanning detector; prior follow-ups logged. No redundant post |
| Abozar / X | Timeout means unknown outcome, not failure; reconcile before mutating retry | #4 | Existing retry outcomes; span-collision hardening locally tested. Doctor is diagnostic, not a remote-state reconciler |
| Gaurav Bhowmick / X | Harness waste, per-run repeated tool-read count, real messy log | #1, #2, #8 | Existing CLI reports category count; invite a redacted real-world trace, do not imply measured real-world savings |
| u/Wooden_Jelly_5295 / Reddit | Can a run be replayed without a suspected call? | #7 | Not possible faithfully from AUDR alone; runtime fixture required. Do **not** build full replay before demand |
| u/Future_AGI / Reddit | Non-executed planning branches and silent retries with status | #2, #3, #4 | Existing explicit-evidence analysis; explore interoperable traces only when real export is available, no integration claim |
| u/Significant_Tune9219 / Reddit | Read/cache candidate vs legitimate freshness guard; chain retry re-fetch | #1, #4 | Existing low-confidence and fingerprint candidates; prior reply already logged |
| u/arthaudm / Reddit | Re-reading invoices before payment may be a safety check, not waste | #1 | No automatic savings on repeated reads; retain freshness/safety uncertainty |
| u/Jolly_Equivalent_985 / Reddit | Paid search/scraping duplicates, short TTL risks, unused outputs | #1, #3, #4 | Existing per-tool analysis, no guaranteed cost savings or inferred freshness policy |
| u/Physical_Economy_340 / Reddit | LLM JSON-schema validation and re-sent tool definitions | #6 | Existing deterministic validation candidate/context analysis; do not promise runtime removal |
| u/Ctbhatia / Reddit | Cache miss and unstable prefix | #2, #6 | Cache counters observed, no universal 90% 'correct' rate claimed |

## Engineering change on branch `feedback-001-evidence-precision`

- Reproduce: 3 reads, 2 known-distinct argument hashes, one missing hash. Previously 2 records were flagged; now only the uninstrumented later read is a low-confidence candidate (and never counted as proven savings).
- Retry span IDs can recur across different agent runs. Previously global span lookup selected the last match. Now lookup prefers a unique same-run match and refuses ambiguous cross-run matches.
- Regression: explicit `retry_of` on a successful prior operation yields a candidate, never an automatic cost-saving claim. A downstream failure without explicit lineage does not prove chain-level retry causality.
- Independent clean virtualenv installation and CLI smoke verification passed. 40 unit tests passed. No production Paperclip trace or real third-party customer trace was obtained in this sprint.

## Reply execution policy

- A comment with a verified existing reply (e.g., Jarrod), or a recent substantive follow-up, must not receive a duplicate status update.
- Drafts in `docs/feedback/2026-10-15_socialos_reply_candidates.json` are **approval-only candidates**, not a publish queue. The SocialOS project has not been edited or its approval database mutated.
- Drafts about the fingerprint/span correction must explicitly say **local QA / not released** until a public version is independently verified and approved.
- All targets must be resolved and checked for existing replies before sending. One reply per relevant ongoing conversation rather than a mass broadcast.
- Prioritize the first real trace submission under Issue #8 before implementing shared-quota analytics (#5), counterfactual replay (#7), or new integrations.

## Next decision

Technical release gate: reviewer checks source diff, CLI install, tests, absent-value handling, and public claim wording. **GO to request approval** only; no public push/release or SNS posting until explicit user approval.
