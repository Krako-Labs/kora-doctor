# KORA Doctor launch copy

## Hacker News

**Title**

Show HN: KORA Doctor – find the LLM calls your agent may not have needed

**Body**

AUDR launched an open standard for recording what an agent run did and what it cost.

I wanted the next question: did all of that inference need to happen?

KORA Doctor is a small open-source CLI that reads AUDR JSON/JSONL and flags duplicate/repeated inference, cache/reuse opportunities, deterministic candidates, smaller-model candidates, and suspicious orchestration overhead.

It deliberately uses heuristics first. AUDR does not contain prompt bodies, so KORA Doctor reports candidates with confidence levels rather than pretending it can prove a call was unnecessary.

Quick try:

    pipx install git+https://github.com/Krako-Labs/kora-doctor.git
    kora-doctor audit samples/inefficient_agent.jsonl

GitHub: https://github.com/Krako-Labs/kora-doctor

I’d especially like real AUDR traces that expose false positives or missing heuristics.

## X

AUDR tells you what an AI agent ran and what it cost.

KORA Doctor asks the next question:

**Did all of that inference need to happen?**

Open-source CLI. Reads AUDR traces and flags:
- repeated inference
- cache/reuse candidates
- deterministic candidates
- cheaper-model candidates
- orchestration overhead

It reports candidates + confidence, not fake certainty.

https://github.com/Krako-Labs/kora-doctor

## Reddit / agent developer communities

I built a small OSS analyzer on top of the new AUDR agent-cost standard.

The problem I’m testing: observability can tell us where tokens and dollars went, but it doesn’t directly tell us which model calls may have been avoidable.

KORA Doctor reads AUDR JSON/JSONL and surfaces optimization candidates using deliberately simple heuristics. No dashboard, account, or hosted service.

I’m looking for real traces where the heuristics are clearly wrong or miss something obvious. That feedback is more useful right now than feature requests for a bigger platform.

Repo: https://github.com/Krako-Labs/kora-doctor
