# KORA Doctor launch copy

## Hacker News

**Title**

Show HN: KORA Doctor – find the LLM calls your agent may not have needed

**Body**

AUDR launched an open standard for recording what an agent run did and what it cost.

I wanted the next question: did all of that inference need to happen?

KORA Doctor is a small open-source CLI that reads AUDR JSON/JSONL and flags duplicate/repeated inference, cache/reuse opportunities, deterministic candidates, smaller-model candidates, and suspicious orchestration overhead.

It deliberately uses heuristics first. AUDR does not contain prompt bodies, so KORA Doctor reports candidates with confidence levels rather than pretending it can prove a call was unnecessary.

Quick try with the current public build (no account needed):

    pipx install --force git+https://github.com/Krako-Labs/kora-doctor.git@v0.1.11
    kora-doctor demo
    curl -fsSL https://raw.githubusercontent.com/Krako-Labs/kora-doctor/main/samples/inefficient_agent.jsonl -o kora-doctor-example.jsonl
    kora-doctor audit ./kora-doctor-example.jsonl

`pipx` installs the CLI, not the repository's sample files. This example downloads a synthetic fixture separately. All findings are diagnostic candidates; estimated savings are illustrative, not verified. The `demo` command ships with v0.1.11 and uses bundled synthetic fixture data.

GitHub: https://github.com/Krako-Labs/kora-doctor

I’m looking for reproducible false positives or carefully scrubbed AUDR traces. Review the data locally before sharing; never post prompts, customer data, raw tool payloads, tokens, or private URLs. Intake checklist: https://github.com/Krako-Labs/kora-doctor/issues/8

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

I’m looking for reproducible false positives or sanitized traces where the heuristics are wrong or miss something obvious. Never share raw prompts, tool payloads, customer data, tokens or private URLs. Trace intake: https://github.com/Krako-Labs/kora-doctor/issues/8. That evidence is more useful right now than feature requests for a bigger platform.

Repo: https://github.com/Krako-Labs/kora-doctor
