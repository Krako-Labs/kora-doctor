import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from .model import AuditReport, Finding


CATEGORY_REPEATED_TOOL = "repeated_tool_retrieval"
CATEGORY_RETRY = "retry_overhead"
CATEGORY_UNUSED = "unused_work"
CATEGORY_CONTEXT = "context_amplification"
CATEGORY_DUPLICATE = "duplicate_repeated"
CATEGORY_CACHE = "cache_reuse"
CATEGORY_DETERMINISTIC = "deterministic_candidate"
CATEGORY_SMALLER = "smaller_model_candidate"
CATEGORY_ORCHESTRATION = "orchestration_overhead"

DETERMINISTIC_KEYWORDS = (
    "classif", "route", "routing", "validat", "schema", "format",
    "extract", "normaliz", "mapping", "parse", "categor", "filter",
)

VALIDATION_KEYWORDS = ("validat", "schema", "json", "format", "parse")

FRONTIER_MODEL_PATTERNS = (
    r"gpt[-_ ]?(?:5|6)",
    r"claude[-_ ].*(?:opus|sonnet)",
    r"(?:gemini|vertex).*(?:pro|ultra)",
    r"o[134][-_ ]",
)

LOW_COST_MODEL_MARKERS = ("mini", "nano", "haiku", "flash", "small")


TOOL_ARGS_HASH_LABELS = (
    "tool_args_hash",
    "kora.tool_args_hash",
    "args_hash",
    "input_hash",
)

TOOL_RESULT_HASH_LABELS = (
    "tool_result_hash",
    "kora.tool_result_hash",
    "result_hash",
    "output_hash",
)


RETRY_OF_LABELS = (
    "retry_of",
    "kora.retry_of",
)

RETRY_ATTEMPT_LABELS = (
    "retry_attempt",
    "kora.retry_attempt",
)

OPERATION_STATUS_LABELS = (
    "operation_status",
    "tool_status",
    "kora.operation_status",
)

OUTPUT_CONSUMED_LABELS = (
    "output_consumed",
    "kora.output_consumed",
)

STEP_ROLE_LABELS = (
    "step_role",
    "kora.step_role",
)

PLANNED_STEP_COUNT_LABELS = (
    "planned_step_count",
    "planned_steps",
    "kora.planned_step_count",
)

EXECUTED_STEP_COUNT_LABELS = (
    "executed_step_count",
    "executed_steps",
    "kora.executed_step_count",
)

RETRY_PROBLEM_STATUSES = {"failed", "timeout", "unknown"}
RETRY_SUCCESS_STATUSES = {"success", "succeeded", "ok"}


def _is_model(record: Dict[str, Any]) -> bool:
    resource = record.get("resource", {})
    return resource.get("type") == "model" or "llm" in record.get("usage", {})


def _cost(record: Dict[str, Any]):
    cost = record.get("cost")
    if not isinstance(cost, dict):
        return None
    value = cost.get("total_cost")
    currency = cost.get("currency")
    if isinstance(value, (int, float)) and value >= 0 and isinstance(currency, str) and currency:
        return float(value), currency.upper()
    return None


def _usage_signature(record: Dict[str, Any]) -> Tuple[Any, ...]:
    resource = record.get("resource", {})
    llm = record.get("usage", {}).get("llm", {})
    return (
        resource.get("provider"),
        resource.get("type"),
        resource.get("name"),
        resource.get("operation"),
        resource.get("modality"),
        llm.get("input_tokens"),
        llm.get("output_tokens"),
        llm.get("reasoning_tokens"),
        llm.get("cache_read_tokens"),
        llm.get("requests"),
    )


def _tool_signature(record: Dict[str, Any]) -> Tuple[Any, ...]:
    resource = record.get("resource", {})
    tool = record.get("usage", {}).get("tool", {})
    return (
        resource.get("provider"),
        resource.get("name"),
        resource.get("operation"),
        resource.get("modality"),
        tool.get("type"),
    )


def _label_fingerprint(record: Dict[str, Any], names: Tuple[str, ...]):
    labels = record.get("attribution", {}).get("labels") or {}
    if not isinstance(labels, dict):
        return None
    for name in names:
        value = labels.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _tool_args_hash(record: Dict[str, Any]):
    return _label_fingerprint(record, TOOL_ARGS_HASH_LABELS)


def _tool_result_hash(record: Dict[str, Any]):
    return _label_fingerprint(record, TOOL_RESULT_HASH_LABELS)


def _retry_of(record: Dict[str, Any]):
    return _label_fingerprint(record, RETRY_OF_LABELS)


def _retry_attempt(record: Dict[str, Any]):
    value = _label_fingerprint(record, RETRY_ATTEMPT_LABELS)
    if value is None:
        return None
    try:
        attempt = int(value)
    except (TypeError, ValueError):
        return None
    return attempt if attempt >= 1 else None


def _operation_status(record: Dict[str, Any]):
    value = _label_fingerprint(record, OPERATION_STATUS_LABELS)
    return value.lower() if isinstance(value, str) else None


def _label_bool(record: Dict[str, Any], names: Tuple[str, ...]):
    value = _label_fingerprint(record, names)
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None


def _label_int(record: Dict[str, Any], names: Tuple[str, ...]):
    value = _label_fingerprint(record, names)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _output_consumed(record: Dict[str, Any]):
    return _label_bool(record, OUTPUT_CONSUMED_LABELS)


def _step_role(record: Dict[str, Any]):
    value = _label_fingerprint(record, STEP_ROLE_LABELS)
    return value.lower() if isinstance(value, str) else None


def _planned_step_count(record: Dict[str, Any]):
    return _label_int(record, PLANNED_STEP_COUNT_LABELS)


def _executed_step_count(record: Dict[str, Any]):
    return _label_int(record, EXECUTED_STEP_COUNT_LABELS)


def _context_text(record: Dict[str, Any]) -> str:
    run = record.get("run", {})
    attribution = record.get("attribution", {})
    labels = attribution.get("labels") or {}
    label_text = " ".join(str(v) for v in labels.values()) if isinstance(labels, dict) else ""
    return " ".join(
        str(x)
        for x in (
            run.get("name", ""),
            run.get("run_type", ""),
            label_text,
        )
    ).lower()


def _token_total(record: Dict[str, Any]) -> int:
    llm = record.get("usage", {}).get("llm", {})
    return sum(
        int(llm.get(k) or 0)
        for k in ("input_tokens", "output_tokens", "reasoning_tokens")
        if isinstance(llm.get(k, 0), (int, float))
    )


def _input_tokens(record: Dict[str, Any]):
    value = record.get("usage", {}).get("llm", {}).get("input_tokens")
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def _cache_counters(record: Dict[str, Any]):
    llm = record.get("usage", {}).get("llm", {})
    if not isinstance(llm, dict):
        return None
    if "cache_read_tokens" not in llm and "cache_write_tokens" not in llm:
        return None

    def _counter(name: str) -> int:
        value = llm.get(name)
        return int(value) if isinstance(value, (int, float)) and value >= 0 else 0

    input_tokens = _counter("input_tokens")
    cache_read = _counter("cache_read_tokens")
    cache_write = _counter("cache_write_tokens")
    return input_tokens, cache_read, cache_write


def _frontier_model(name: str) -> bool:
    value = (name or "").lower()
    if any(marker in value for marker in LOW_COST_MODEL_MARKERS):
        return False
    return any(re.search(pattern, value) for pattern in FRONTIER_MODEL_PATTERNS)


def _ordered(group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        group,
        key=lambda r: (
            r.get("run", {}).get("step") is None,
            r.get("run", {}).get("step") or 10**9,
            str(r.get("timing", {}).get("event_time", "")),
        ),
    )


def analyze(records: List[Dict[str, Any]]) -> AuditReport:
    model_records = [r for r in records if _is_model(r)]
    tool_records = [r for r in records if not _is_model(r)]
    runs = {str(r.get("run", {}).get("run_id")) for r in records}

    observed_costs = defaultdict(float)
    warnings: List[str] = []
    for record in records:
        c = _cost(record)
        if c:
            value, currency = c
            observed_costs[currency] += value

    if not observed_costs:
        warnings.append(
            "No AUDR cost.total_cost values were present; candidate counts are available, "
            "but dollar savings cannot be estimated."
        )
    if len(observed_costs) > 1:
        warnings.append(
            "Multiple currencies detected. Costs and savings are reported separately and are never converted."
        )

    cache_reported_calls = 0
    cache_uncached_input_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    for record in model_records:
        counters = _cache_counters(record)
        if counters is None:
            continue
        uncached, read, written = counters
        cache_reported_calls += 1
        cache_uncached_input_tokens += uncached
        cache_read_tokens += read
        cache_write_tokens += written

    cache_denominator = cache_uncached_input_tokens + cache_read_tokens
    cache_metrics = {}
    if cache_reported_calls:
        cache_metrics = {
            "reported_calls": cache_reported_calls,
            "uncached_input_tokens": cache_uncached_input_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "read_share_percent": (
                cache_read_tokens / cache_denominator * 100.0
                if cache_denominator > 0 else 0.0
            ),
        }

    findings: List[Finding] = []

    # 1. Repeated tool/resource use inside one run.
    #
    # AUDR intentionally does not include tool arguments/results. When a trace
    # source supplies privacy-preserving hashes in attribution.labels, use those
    # as stronger evidence. Otherwise keep the old resource-only heuristic.
    by_run_tool = defaultdict(list)
    for record in tool_records:
        run_id = str(record["run"]["run_id"])
        by_run_tool[(run_id, _tool_signature(record))].append(record)

    for (run_id, _), group in by_run_tool.items():
        if len(group) < 2:
            continue

        ordered = _ordered(group)
        resource_name = str(ordered[0].get("resource", {}).get("name", "tool"))

        by_args_hash = defaultdict(list)
        without_args_hash = []
        for record in ordered:
            args_hash = _tool_args_hash(record)
            if args_hash:
                by_args_hash[args_hash].append(record)
            else:
                without_args_hash.append(record)

        exact_groups = [records for records in by_args_hash.values() if len(records) >= 2]
        if exact_groups:
            for exact_group in exact_groups:
                duplicates = exact_group[1:]
                result_hashes = [_tool_result_hash(record) for record in exact_group]
                nonempty_results = [value for value in result_hashes if value]
                same_result = (
                    len(nonempty_results) == len(exact_group)
                    and len(set(nonempty_results)) == 1
                )

                if same_result:
                    title = f"{len(duplicates)} exact repeated tool call(s): {resource_name}"
                    reason = (
                        "The same tool/resource repeated in one run with the same argument fingerprint "
                        "and the same result fingerprint. This is strong evidence of repeated work, "
                        "though freshness or safety checks may still justify the second call."
                    )
                else:
                    title = f"{len(duplicates)} same-argument tool call(s): {resource_name}"
                    reason = (
                        "The same tool/resource repeated in one run with the same argument fingerprint. "
                        "This is stronger evidence than a resource-name match alone, but changing external "
                        "state can still make a repeated read legitimate."
                    )

                findings.append(
                    Finding(
                        category=CATEGORY_REPEATED_TOOL,
                        title=title,
                        reason=reason,
                        confidence="medium",
                        record_ids=[str(record["record_id"]) for record in duplicates],
                        run_ids=[run_id],
                        saving_ratio=0.0,
                    )
                )

            # Hashes were available, so do not downgrade distinct hashed calls
            # into a generic duplicate merely because they used the same tool.
            if len(without_args_hash) >= 2:
                duplicates = without_args_hash[1:]
                findings.append(
                    Finding(
                        category=CATEGORY_REPEATED_TOOL,
                        title=f"{len(duplicates)} unverified repeated tool/read call(s): {resource_name}",
                        reason=(
                            "The same tool/resource repeated, but these calls did not carry an argument "
                            "fingerprint. They remain low-confidence reuse candidates."
                        ),
                        confidence="low",
                        record_ids=[str(record["record_id"]) for record in duplicates],
                        run_ids=[run_id],
                        saving_ratio=0.0,
                    )
                )
            continue

        # No repeated argument fingerprint was available. If hashes exist but
        # are all distinct, that is evidence these were different requests and
        # we should not flag them as duplicates.
        hashed = [record for record in ordered if _tool_args_hash(record)]
        if hashed and not without_args_hash:
            continue

        duplicates = ordered[1:]
        findings.append(
            Finding(
                category=CATEGORY_REPEATED_TOOL,
                title=f"{len(duplicates)} repeated tool/read call(s): {resource_name}",
                reason=(
                    "The same AUDR tool/resource and operation repeated within one run. "
                    "No repeated argument fingerprint was available, so this is an observed "
                    "repeat and a reuse candidate, not proof that the second call was unnecessary."
                ),
                confidence="low",
                record_ids=[str(record["record_id"]) for record in duplicates],
                run_ids=[run_id],
                saving_ratio=0.0,
            )
        )

    # 2. Explicit retry lineage.
    #
    # AUDR v1.0.0 has run-level error/outcome fields but no per-operation retry
    # lineage. Richer trace sources can preserve that evidence in labels.
    record_by_record_id = {str(record.get("record_id")): record for record in records}
    record_by_span_id = {
        str(record.get("run", {}).get("span_id")): record
        for record in records
        if record.get("run", {}).get("span_id")
    }

    for record in records:
        retry_ref = _retry_of(record)
        attempt = _retry_attempt(record)
        if not retry_ref and not (attempt is not None and attempt > 1):
            continue

        previous = None
        if retry_ref:
            previous = record_by_record_id.get(retry_ref) or record_by_span_id.get(retry_ref)

        if previous is None:
            findings.append(
                Finding(
                    category=CATEGORY_RETRY,
                    title="Retry attempt observed without resolvable prior operation",
                    reason=(
                        "Retry metadata indicates a repeated attempt, but the referenced prior operation "
                        "is not present in this trace. This is retry overhead evidence, not proof of waste."
                    ),
                    confidence="low",
                    record_ids=[str(record["record_id"])],
                    run_ids=[str(record["run"]["run_id"])],
                    saving_ratio=0.0,
                )
            )
            continue

        same_tool = _tool_signature(previous) == _tool_signature(record)
        prev_args = _tool_args_hash(previous)
        curr_args = _tool_args_hash(record)
        same_args = bool(prev_args and curr_args and prev_args == curr_args)
        prev_status = _operation_status(previous)

        confidence = "low"
        if same_tool and same_args and prev_status in RETRY_PROBLEM_STATUSES:
            confidence = "medium"
            title = "Retry replayed the same failed/unknown tool call"
            reason = (
                f"The retry references a prior {prev_status} operation with the same tool and argument "
                "fingerprint. This is explicit retry overhead; whether the retry was necessary depends "
                "on the failure mode and idempotency."
            )
        elif same_tool and same_args and prev_status in RETRY_SUCCESS_STATUSES:
            confidence = "medium"
            title = "Retry repeated a previously successful tool call"
            reason = (
                "The retry references a prior successful operation with the same tool and argument "
                "fingerprint. This is a strong candidate for avoidable duplicate work, but KORA Doctor "
                "does not assume the external state stayed unchanged."
            )
        elif same_tool and same_args:
            title = "Retry repeated the same tool arguments"
            reason = (
                "Explicit retry lineage points to a prior operation with the same tool and argument "
                "fingerprint, but the prior per-operation status is missing."
            )
        else:
            title = "Retry attempt observed"
            reason = (
                "Explicit retry lineage is present, but KORA Doctor lacks matching argument/status "
                "evidence to call the retried work redundant."
            )

        findings.append(
            Finding(
                category=CATEGORY_RETRY,
                title=title,
                reason=reason,
                confidence=confidence,
                record_ids=[str(record["record_id"])],
                run_ids=[str(record["run"]["run_id"])],
                saving_ratio=0.0,
            )
        )

    # 3. Explicit unused-output / dead-planning evidence.
    for record in records:
        consumed = _output_consumed(record)
        role = _step_role(record)
        planned = _planned_step_count(record)
        executed = _executed_step_count(record)

        if role == "planner" and planned is not None and executed is not None and planned > executed:
            dead = planned - executed
            detail = (
                f"The planner explicitly reports {planned} planned steps but only {executed} executed "
                f"steps, leaving {dead} planned step(s) unexecuted."
            )
            if consumed is False:
                detail += " The planner output is also explicitly marked unconsumed."
            findings.append(
                Finding(
                    category=CATEGORY_UNUSED,
                    title=f"Planner produced {dead} unexecuted step(s)",
                    reason=detail,
                    confidence="medium",
                    record_ids=[str(record["record_id"])],
                    run_ids=[str(record["run"]["run_id"])],
                    saving_ratio=0.0,
                )
            )
            continue

        if consumed is False:
            findings.append(
                Finding(
                    category=CATEGORY_UNUSED,
                    title="Output explicitly marked unconsumed",
                    reason=(
                        "The trace source explicitly marks this operation's output as not consumed by "
                        "downstream execution. KORA Doctor reports it as dead-work evidence without "
                        "inferring from timing or span order."
                    ),
                    confidence="medium",
                    record_ids=[str(record["record_id"])],
                    run_ids=[str(record["run"]["run_id"])],
                    saving_ratio=0.0,
                )
            )

    # 2. Model calls with identical usage/resource signatures in one run.
    by_run_signature = defaultdict(list)
    for record in model_records:
        run_id = str(record["run"]["run_id"])
        by_run_signature[(run_id, _usage_signature(record))].append(record)

    for (run_id, _), group in by_run_signature.items():
        if len(group) >= 2:
            duplicates = _ordered(group)[1:]
            findings.append(
                Finding(
                    category=CATEGORY_DUPLICATE,
                    title=f"{len(duplicates)} repeated model call(s) in one run",
                    reason=(
                        "Same run, model/resource, operation, and reported usage counters repeat. "
                        "AUDR has no prompt body, so this is a strong duplicate candidate, not proof."
                    ),
                    confidence="medium",
                    record_ids=[str(r["record_id"]) for r in duplicates],
                    run_ids=[run_id],
                    saving_ratio=1.0,
                )
            )

    # 3. Cross-run reuse candidates.
    by_signature = defaultdict(list)
    for record in model_records:
        by_signature[_usage_signature(record)].append(record)

    for _, group in by_signature.items():
        run_ids = {str(r["run"]["run_id"]) for r in group}
        if len(run_ids) >= 2 and len(group) >= 2:
            candidates = _ordered(group)[1:]
            findings.append(
                Finding(
                    category=CATEGORY_CACHE,
                    title=f"{len(candidates)} cross-run reuse candidate(s)",
                    reason=(
                        "The same model/resource and usage-counter signature appeared across multiple runs. "
                        "Without an input hash or prompt content, reuse cannot be confirmed."
                    ),
                    confidence="low",
                    record_ids=[str(r["record_id"]) for r in candidates],
                    run_ids=sorted(run_ids),
                    saving_ratio=0.70,
                )
            )

    # 4. Deterministic candidates. Validation/schema/format work is promoted to
    # medium confidence when the call is small enough to look like a checker.
    for record in model_records:
        text = _context_text(record)
        hit = next((kw for kw in DETERMINISTIC_KEYWORDS if kw in text), None)
        if hit:
            llm = record.get("usage", {}).get("llm", {})
            output_tokens = llm.get("output_tokens")
            is_validation = any(kw in text for kw in VALIDATION_KEYWORDS)
            small_checker = isinstance(output_tokens, (int, float)) and output_tokens <= 200
            if is_validation and small_checker:
                title = "LLM used for deterministic validation"
                reason = (
                    "Run metadata indicates validation/schema/format checking and the call returned "
                    f"{int(output_tokens)} output tokens. This is a strong candidate for JSON Schema, "
                    "parsing, or another deterministic check."
                )
                confidence = "medium"
                saving_ratio = 0.90
            else:
                title = "Possible deterministic step"
                reason = (
                    f"Run metadata contains '{hit}', a pattern often implemented with rules, parsing, "
                    "routing, validation, or fixed mappings. Payload evidence is not available in AUDR."
                )
                confidence = "low"
                saving_ratio = 0.80

            findings.append(
                Finding(
                    category=CATEGORY_DETERMINISTIC,
                    title=title,
                    reason=reason,
                    confidence=confidence,
                    record_ids=[str(record["record_id"])],
                    run_ids=[str(record["run"]["run_id"])],
                    saving_ratio=saving_ratio,
                )
            )

    # 5. Context amplification and repeated static-context tax.
    by_run_models = defaultdict(list)
    for record in model_records:
        by_run_models[str(record["run"]["run_id"])].append(record)

    for run_id, group in by_run_models.items():
        ordered = _ordered(group)
        previous = None
        growth_records = []
        growth_pairs = []
        for record in ordered:
            current = _input_tokens(record)
            if current is None:
                previous = current
                continue
            if previous is not None:
                delta = current - previous
                ratio = (current / previous) if previous > 0 else 0.0
                if delta >= 500 and ratio >= 1.5:
                    growth_records.append(record)
                    growth_pairs.append((previous, current))
            previous = current

        if growth_records:
            first_before, first_after = growth_pairs[0]
            findings.append(
                Finding(
                    category=CATEGORY_CONTEXT,
                    title=f"Input context amplified across {len(growth_records)} call(s)",
                    reason=(
                        f"Reported input tokens grew from {first_before} to {first_after} on the first "
                        "flagged transition (>=1.5x and +500 tokens). This can indicate context carry-over, "
                        "repeated retrieval results, or static tool definitions being paid for again."
                    ),
                    confidence="low",
                    record_ids=[str(r["record_id"]) for r in growth_records],
                    run_ids=[run_id],
                    saving_ratio=0.0,
                )
            )

        reported = [(record, _input_tokens(record)) for record in ordered]
        reported = [(record, tokens) for record, tokens in reported if tokens is not None]
        if len(reported) >= 3:
            values = [tokens for _, tokens in reported]
            low = min(values)
            high = max(values)
            if low >= 3000 and high <= low * 1.25:
                later = [record for record, _ in reported[1:]]
                findings.append(
                    Finding(
                        category=CATEGORY_CONTEXT,
                        title="Persistent high input-token baseline",
                        reason=(
                            f"{len(reported)} sequential model calls each carried at least {low} input tokens "
                            "with little variation. Static tool definitions or carried context may be imposing "
                            "a repeated token tax; AUDR alone cannot identify the exact payload."
                        ),
                        confidence="low",
                        record_ids=[str(r["record_id"]) for r in later],
                        run_ids=[run_id],
                        saving_ratio=0.0,
                    )
                )

        cache_reported = []
        for record in ordered:
            counters = _cache_counters(record)
            if counters is not None:
                cache_reported.append((record, counters))

        if len(cache_reported) >= 3:
            total_uncached = sum(counters[0] for _, counters in cache_reported)
            total_read = sum(counters[1] for _, counters in cache_reported)
            if total_uncached >= 6000 and total_read == 0:
                findings.append(
                    Finding(
                        category=CATEGORY_CONTEXT,
                        title="No prompt-cache reads reported across high-context run",
                        reason=(
                            f"{len(cache_reported)} model calls explicitly reported cache telemetry, "
                            f"with {total_uncached} uncached input tokens and zero cache-read tokens. "
                            "If the prefix/context was stable, this is a strong cache-reuse opportunity; "
                            "AUDR alone cannot prove prefix stability."
                        ),
                        confidence="low",
                        record_ids=[str(record["record_id"]) for record, _ in cache_reported],
                        run_ids=[run_id],
                        saving_ratio=0.0,
                    )
                )

    # 6. Suspicious orchestration overhead.
    for run_id, group in by_run_models.items():
        ordered = _ordered(group)
        if len(ordered) >= 6:
            extras = ordered[4:]
            confidence = "medium" if len(ordered) >= 8 else "low"
            findings.append(
                Finding(
                    category=CATEGORY_ORCHESTRATION,
                    title=f"{len(ordered)} model calls in a single agent run",
                    reason=(
                        "More than four model calls were observed in one run. Later calls are marked as "
                        "orchestration-overhead candidates; legitimate long-horizon work may still need them."
                    ),
                    confidence=confidence,
                    record_ids=[str(r["record_id"]) for r in extras],
                    run_ids=[run_id],
                    saving_ratio=0.50,
                )
            )

    # 7. Smaller-model candidates deliberately come last. Cheaper models should
    # not be the first answer to execution waste.
    for record in model_records:
        resource = record.get("resource", {})
        model = str(resource.get("name", ""))
        llm = record.get("usage", {}).get("llm", {})
        reasoning = llm.get("reasoning_tokens")
        total = _token_total(record)
        if _frontier_model(model) and total and total <= 2500 and (reasoning in (None, 0)):
            findings.append(
                Finding(
                    category=CATEGORY_SMALLER,
                    title=f"Short call on high-end model: {model}",
                    reason=(
                        f"Only {total} reported input/output tokens and no reported reasoning tokens. "
                        "A smaller model may be sufficient after execution waste is removed."
                    ),
                    confidence="low",
                    record_ids=[str(record["record_id"])],
                    run_ids=[str(record["run"]["run_id"])],
                    saving_ratio=0.50,
                )
            )

    # Conservative savings aggregation: findings can overlap, so only the
    # largest saving ratio is applied to each record.
    record_by_id = {str(r["record_id"]): r for r in records}
    max_ratio_by_record: Dict[str, float] = {}
    for finding in findings:
        for rid in finding.record_ids:
            max_ratio_by_record[rid] = max(max_ratio_by_record.get(rid, 0.0), finding.saving_ratio)

    potential_savings = defaultdict(float)
    for rid, ratio in max_ratio_by_record.items():
        record = record_by_id.get(rid)
        if not record:
            continue
        c = _cost(record)
        if c:
            value, currency = c
            potential_savings[currency] += value * ratio

    category_record_ids = defaultdict(set)
    for finding in findings:
        category_record_ids[finding.category].update(finding.record_ids)

    category_counts = {
        CATEGORY_REPEATED_TOOL: len(category_record_ids[CATEGORY_REPEATED_TOOL]),
        CATEGORY_RETRY: len(category_record_ids[CATEGORY_RETRY]),
        CATEGORY_UNUSED: len(category_record_ids[CATEGORY_UNUSED]),
        CATEGORY_CONTEXT: len(category_record_ids[CATEGORY_CONTEXT]),
        CATEGORY_DETERMINISTIC: len(category_record_ids[CATEGORY_DETERMINISTIC]),
        CATEGORY_DUPLICATE: len(category_record_ids[CATEGORY_DUPLICATE]),
        CATEGORY_CACHE: len(category_record_ids[CATEGORY_CACHE]),
        CATEGORY_ORCHESTRATION: len(category_record_ids[CATEGORY_ORCHESTRATION]),
        CATEGORY_SMALLER: len(category_record_ids[CATEGORY_SMALLER]),
    }

    return AuditReport(
        records=len(records),
        model_calls=len(model_records),
        tool_calls=len(tool_records),
        runs=len(runs),
        observed_costs=dict(sorted(observed_costs.items())),
        potential_savings=dict(sorted(potential_savings.items())),
        findings=findings,
        category_counts=category_counts,
        warnings=warnings,
        cache_metrics=cache_metrics,
    )
