import re
from datetime import datetime, timezone
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from .model import AuditReport, Finding


CATEGORY_REPEATED_TOOL = "repeated_tool_retrieval"
CATEGORY_RETRY = "retry_overhead"
CATEGORY_RATE_LIMIT = "rate_limit_retry_chain"
CATEGORY_UNUSED = "unused_work"
CATEGORY_CONTEXT = "context_amplification"
CATEGORY_DUPLICATE = "duplicate_repeated"
CATEGORY_CACHE = "cache_reuse"
CATEGORY_DETERMINISTIC = "deterministic_candidate"
CATEGORY_SMALLER = "smaller_model_candidate"
CATEGORY_ORCHESTRATION = "orchestration_overhead"
CATEGORY_REPLANNING = "replanning_loop"
CATEGORY_FRESHNESS = "freshness_refresh"

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

HTTP_STATUS_LABELS = (
    "http_status_code",
    "http.response.status_code",
    "http.status_code",
)

OUTPUT_CONSUMED_LABELS = (
    "output_consumed",
    "kora.output_consumed",
)

CONDITIONAL_CONSUMER_LABELS = ("conditional_consumer_exists", "kora.conditional_consumer_exists")
NO_DOWNSTREAM_CONSUMER_LABELS = ("no_downstream_consumer", "kora.no_downstream_consumer")

PLAN_BEFORE_HASH_LABELS = ("plan_before_hash", "kora.plan_before_hash")
PLAN_AFTER_HASH_LABELS = ("plan_after_hash", "kora.plan_after_hash")
NEXT_ACTION_BEFORE_HASH_LABELS = ("next_action_before_hash", "kora.next_action_before_hash")
NEXT_ACTION_AFTER_HASH_LABELS = ("next_action_after_hash", "kora.next_action_after_hash")
NONEMPTY_RESULT_LABELS = ("nonempty_result", "kora.nonempty_result")
EXPECTED_REPEAT_LABELS = ("expected_repeat", "kora.expected_repeat")

SOURCE_ID_LABELS = ("source_id_hash", "kora.source_id_hash")
SOURCE_SNAPSHOT_LABELS = ("source_snapshot_hash", "kora.source_snapshot_hash")
SOURCE_REFRESH_LABELS = ("source_refresh", "kora.source_refresh")

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


def _http_status(record: Dict[str, Any]):
    value = _label_fingerprint(record, HTTP_STATUS_LABELS)
    if value is None or not value.isdecimal():
        return None
    status = int(value)
    return status if 100 <= status <= 599 else None


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


def _conditional_consumer(record: Dict[str, Any]):
    return _label_bool(record, CONDITIONAL_CONSUMER_LABELS)


def _no_downstream_consumer(record: Dict[str, Any]):
    return _label_bool(record, NO_DOWNSTREAM_CONSUMER_LABELS)


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


def _parsed_event_time(record: Dict[str, Any]):
    value = record.get("timing", {}).get("event_time")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _trace_id(record: Dict[str, Any]) -> str:
    # OTel trace IDs are hex strings; allow normalized UUID punctuation.
    value = record.get("run", {}).get("trace_id")
    return str(value).strip().lower().replace("-", "") if value is not None else ""


def analyze(records: List[Dict[str, Any]]) -> AuditReport:
    model_records = [r for r in records if _is_model(r)]
    tool_records = [r for r in records if not _is_model(r)]
    # A reused run_id in distinct OTel traces is not one logical execution.
    runs = {(str(r.get("run", {}).get("run_id")), _trace_id(r)) for r in records}

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

    # 1. Explicit retry lineage.
    #
    # AUDR v1.0.0 has run-level error/outcome fields but no per-operation retry
    # lineage. Richer trace sources can preserve that evidence in labels.
    records_by_record_id = defaultdict(list)
    records_by_span_id = defaultdict(list)
    for record in records:
        records_by_record_id[str(record.get("record_id"))].append(record)
        span_id = record.get("run", {}).get("span_id")
        if span_id:
            records_by_span_id[str(span_id)].append(record)

    resolved_retry_parents = {}
    for record in records:
        retry_ref = _retry_of(record)
        attempt = _retry_attempt(record)
        if not retry_ref and not (attempt is not None and attempt > 1):
            continue

        previous = None
        if retry_ref:
            # A logical retry must belong to the same run. Neither record IDs
            # nor span IDs are trustworthy links across run boundaries; also
            # reject duplicates instead of guessing which parent was retried.
            run_id = record.get("run", {}).get("run_id")
            candidates = [
                item for item in records_by_record_id.get(retry_ref, [])
                if item is not record and item.get("run", {}).get("run_id") == run_id
                and _trace_id(item) == _trace_id(record)
            ]
            if not candidates and retry_ref not in records_by_record_id:
                candidates = [
                    item for item in records_by_span_id.get(retry_ref, [])
                    if item is not record and item.get("run", {}).get("run_id") == run_id
                and _trace_id(item) == _trace_id(record)
                ]
            if len(candidates) == 1:
                previous = candidates[0]
                prev_time = _parsed_event_time(previous)
                retry_time = _parsed_event_time(record)
                if prev_time is not None and retry_time is not None and prev_time >= retry_time:
                    previous = None

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

        # An OTel-derived guess is not an explicit retry edge. It can be a
        # low-confidence candidate, but must not build an HTTP logical chain.
        inferred_retry = _label_bool(record, ("retry_inferred_by_otel",)) is True
        same_tool = _tool_signature(previous) == _tool_signature(record)
        prev_args = _tool_args_hash(previous)
        curr_args = _tool_args_hash(record)
        same_args = bool(prev_args and curr_args and prev_args == curr_args)
        prev_status = _operation_status(previous)
        prev_time = _parsed_event_time(previous)
        curr_time = _parsed_event_time(record)
        if (
            retry_ref and not inferred_retry and same_tool and same_args
            and prev_time is not None and curr_time is not None and prev_time < curr_time
            and len(records_by_record_id[str(record["record_id"])]) == 1
            and len(records_by_record_id[str(previous["record_id"])]) == 1
        ):
            resolved_retry_parents[str(record["record_id"])] = str(previous["record_id"])

        confidence = "low"
        if inferred_retry:
            title = "Possible retry inferred from OTel tool-call evidence"
            reason = (
                "OTel-inferred matching tool and argument fingerprints, a prior problem "
                "status, and time order suggest a retry. No explicit retry edge was observed, so this "
                "does not establish one logical operation, unnecessary work, or savings."
            )
        elif same_tool and same_args and prev_status in RETRY_PROBLEM_STATUSES:
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

    # HTTP 429 is an attempt-level observation, not an independent
    # logical-operation failure. Build only explicit, linear same-run retry
    # chains with matching argument fingerprints and increasing timestamps.
    # We do not inspect raw response headers or infer Retry-After conflicts.
    successors = defaultdict(list)
    for child_id, parent_id in resolved_retry_parents.items():
        successors[parent_id].append(child_id)

    for root_id in sorted(set(successors) - set(resolved_retry_parents)):
        chain = [root_id]
        current = root_id
        while current in successors:
            # Branches cannot safely be counted as one serial operation.
            if len(successors[current]) != 1:
                chain = []
                break
            child_id = successors[current][0]
            if child_id in chain:
                chain = []
                break
            chain.append(child_id)
            current = child_id

        if len(chain) < 2:
            continue
        events = [records_by_record_id[record_id][0] for record_id in chain]
        codes = [_http_status(record) for record in events]
        rate_limits = sum(code == 429 for code in codes)
        if rate_limits == 0:
            continue

        last_code = codes[-1]
        final_status = _operation_status(events[-1])
        # An HTTP result and a per-operation result may come from different
        # instrumentation layers. Contradictions must not certify success.
        contradictory_status = any(
            (code == 429 and _operation_status(event) in RETRY_SUCCESS_STATUSES)
            or (code is not None and 200 <= code < 300
                and _operation_status(event) in RETRY_PROBLEM_STATUSES)
            for code, event in zip(codes, events)
        )
        if contradictory_status:
            outcome = "conflicting HTTP and operation statuses; final outcome not established"
        elif last_code is not None and 200 <= last_code < 300:
            outcome = "eventual HTTP success"
        elif last_code == 429:
            outcome = "last observed attempt still rate-limited"
        elif last_code is None and final_status in RETRY_SUCCESS_STATUSES:
            outcome = "reported success without an HTTP status"
        else:
            outcome = "final outcome not established"

        findings.append(
            Finding(
                category=CATEGORY_RATE_LIMIT,
                title=f"HTTP 429 retry chain: {len(chain)} attempts, {rate_limits} rate-limit response(s)",
                reason=(
                    f"One explicit, same-run, same-argument retry chain contains {len(chain)} "
                    f"observed attempts, including {rate_limits} HTTP 429 response(s); {outcome}. "
                    "Attempts are not counted as independent failed logical operations. "
                    "Raw Retry-After and rate-limit headers are not available, so this does not "
                    "prove a header conflict or that any retry was unnecessary."
                ),
                confidence="medium" if all(code is not None for code in codes) and not contradictory_status else "low",
                record_ids=chain,
                run_ids=[str(events[0]["run"]["run_id"])],
                saving_ratio=0.0,
            )
        )

    # A complete explicit 429 chain already explains these later attempts.
    # Do not also report them as duplicate tool/read waste candidates.
    # Keep the chain root so independent subsequent reads can still be flagged.
    rate_limit_retry_ids = {
        record_id
        for finding in findings if finding.category == CATEGORY_RATE_LIMIT
        for record_id in finding.record_ids[1:]
    }

    # 2. Repeated tool/resource use inside one run.
    #
    # AUDR intentionally does not include tool arguments/results. When a trace
    # source supplies privacy-preserving hashes in attribution.labels, use those
    # as stronger evidence. Otherwise keep the old resource-only heuristic.
    by_run_tool = defaultdict(list)
    for record in tool_records:
        if str(record["record_id"]) in rate_limit_retry_ids:
            continue  # Already represented as an explicit HTTP 429 retry attempt.
        run_id = str(record["run"]["run_id"])
        # Reused run IDs do not establish a shared execution across distinct traces.
        by_run_tool[(run_id, _trace_id(record), _tool_signature(record))].append(record)

    for (run_id, _, _), group in by_run_tool.items():
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

        exact_groups = [items for items in by_args_hash.values() if len(items) >= 2]
        for exact_group in exact_groups:
            duplicates = exact_group[1:]
            result_hashes = [_tool_result_hash(record) for record in exact_group]
            same_result = all(result_hashes) and len(set(result_hashes)) == 1

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

        if by_args_hash:
            # Known-distinct hashes are *not* duplicate candidates merely
            # because another call in the group is missing a fingerprint.
            # Attribute uncertainty only to later uninstrumented calls.
            unverified = [record for record in ordered[1:] if not _tool_args_hash(record)]
            if unverified:
                findings.append(
                    Finding(
                        category=CATEGORY_REPEATED_TOOL,
                        title=f"{len(unverified)} unverified repeated tool/read call(s): {resource_name}",
                        reason=(
                            "Some calls lack argument fingerprints. Only those later uninstrumented "
                            "calls are flagged as low-confidence reuse candidates; calls with distinct "
                            "argument fingerprints are not marked as duplicates."
                        ),
                        confidence="low",
                        record_ids=[str(record["record_id"]) for record in unverified],
                        run_ids=[run_id],
                        saving_ratio=0.0,
                    )
                )
            continue

        # With no hashes available, metadata can suggest a repeated resource
        # but cannot establish identical arguments or unnecessary execution.
        duplicates = ordered[1:]
        findings.append(
            Finding(
                category=CATEGORY_REPEATED_TOOL,
                title=f"{len(duplicates)} repeated tool/read call(s): {resource_name}",
                reason=(
                    "The same AUDR tool/resource and operation repeated within one run. "
                    "No argument fingerprints were available, so this is an observed "
                    "repeat and a reuse candidate, not proof that the second call was unnecessary."
                ),
                confidence="low",
                record_ids=[str(record["record_id"]) for record in duplicates],
                run_ids=[run_id],
                saving_ratio=0.0,
            )
        )

    # Explicit action-plan/next-action identity, never prose similarity.
    # Only exact fingerprints from the same execution transition qualify.
    for record in records:
        before = _label_fingerprint(record, PLAN_BEFORE_HASH_LABELS)
        after = _label_fingerprint(record, PLAN_AFTER_HASH_LABELS)
        next_before = _label_fingerprint(record, NEXT_ACTION_BEFORE_HASH_LABELS)
        next_after = _label_fingerprint(record, NEXT_ACTION_AFTER_HASH_LABELS)
        nonempty = _label_bool(record, NONEMPTY_RESULT_LABELS)
        expected_repeat = _label_bool(record, EXPECTED_REPEAT_LABELS)
        if expected_repeat is not False or nonempty is not True:
            continue  # Cannot exclude intentional polling or backoff.
        same_plan = bool(before and after and before == after)
        same_next = bool(next_before and next_after and next_before == next_after)
        if not (same_plan or same_next):
            continue
        finding_type = "unchanged action plan" if same_plan else "unchanged next action"
        findings.append(
            Finding(
                category=CATEGORY_REPLANNING,
                title=f"Potential re-planning loop: {finding_type}",
                reason=(
                    "Explicit before/after action fingerprints match despite an explicitly non-empty "
                    "tool result, and the trace marks this repetition as not expected. "
                    "This is a review candidate, not proof that reasoning was removable."
                ),
                confidence="medium" if same_plan and same_next else "low",
                record_ids=[str(record["record_id"])],
                run_ids=[str(record["run"]["run_id"])],
                saving_ratio=0.0,
            )
        )

    # Snapshot fingerprints permit observed unchanged-refresh evidence, not
    # an inferred TTL, missed changes, or a claim of removable calls.
    snapshots = defaultdict(list)
    for record in records:
        if _label_bool(record, SOURCE_REFRESH_LABELS) is not True:
            continue
        source = _label_fingerprint(record, SOURCE_ID_LABELS)
        digest = _label_fingerprint(record, SOURCE_SNAPSHOT_LABELS)
        event_time = _parsed_event_time(record)
        if source and digest and event_time:
            resource = record.get("resource", {})
            scope = (resource.get("provider"), resource.get("name"), source)
            snapshots[scope].append((event_time, digest, record))
    for group in snapshots.values():
        ordered = sorted(group, key=lambda entry: entry[0])
        if len(ordered) < 3:
            continue
        # Inspect only adjacent, distinct timestamp refreshes.
        same = [
            record
            for (t0, h0, _), (t1, h1, record) in zip(ordered, ordered[1:])
            if t1 > t0 and h0 == h1
        ]
        if len(same) < 2:
            continue
        findings.append(
            Finding(
                category=CATEGORY_FRESHNESS,
                title=f"{len(same)} refresh(es) returned unchanged snapshots",
                reason=(
                    "Consecutive observed refreshes produced identical source fingerprints at "
                    "distinct timestamps. Consider measuring source-change cadence against refresh "
                    "frequency. This does not establish that the source was unchanged between samples "
                    "or that increasing TTL would be safe."
                ),
                confidence="low",
                record_ids=[str(r["record_id"]) for r in same],
                run_ids=sorted({str(r["run"]["run_id"]) for r in same}),
                saving_ratio=0.0,
            )
        )

    # 3. Explicit unused-output / dead-planning evidence.
    for record in records:
        consumed = _output_consumed(record)
        role = _step_role(record)
        conditional = _conditional_consumer(record)
        no_consumer = _no_downstream_consumer(record)
        planned = _planned_step_count(record)
        executed = _executed_step_count(record)

        if role == "planner" and planned is not None and executed is not None and planned > executed:
            dead = planned - executed
            detail = (
                f"The planner explicitly reports {planned} planned steps but only {executed} executed "
                f"steps, leaving {dead} planned step(s) unexecuted."
            )
            if consumed is False:
                detail += " The planner output was not consumed in this run."
            if conditional is True:
                detail += " A conditional downstream consumer exists, so unexecuted steps are not proven waste."
            findings.append(
                Finding(
                    category=CATEGORY_UNUSED,
                    title=f"Planner produced {dead} unexecuted step(s)",
                    reason=detail,
                    confidence="low" if conditional is True else "medium",
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
                    title=(
                        "Conditional output not consumed in this run" if conditional is True
                        else "Output explicitly has no downstream consumer" if no_consumer is True
                        else "Output explicitly marked unconsumed"
                    ),
                    reason=(
                        "The output was not consumed in this observed run, but a conditional downstream "
                        "consumer exists. This is not proof of dead work."
                        if conditional is True else
                        "The trace explicitly reports no downstream consumer and no consumption in this run. "
                        "Review before removal; execution may still be intentional."
                        if no_consumer is True else
                        "The trace marks this output unconsumed in the observed run, but does not establish "
                        "whether a conditional or future downstream consumer exists."
                    ),
                    confidence="low" if conditional is True or no_consumer is not True else "medium",
                    record_ids=[str(record["record_id"])],
                    run_ids=[str(record["run"]["run_id"])],
                    saving_ratio=0.0,
                )
            )

    # 2. Model calls with identical usage/resource signatures in one run.
    by_run_signature = defaultdict(list)
    for record in model_records:
        run_id = str(record["run"]["run_id"])
        by_run_signature[(run_id, _trace_id(record), _usage_signature(record))].append(record)

    for (run_id, _, _), group in by_run_signature.items():
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
        by_run_models[(str(record["run"]["run_id"]), _trace_id(record))].append(record)

    for (run_id, _trace), group in by_run_models.items():
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

        reported = [(record, _input_tokens(record)) for record in ordered]
        reported = [(record, tokens) for record, tokens in reported if tokens is not None]
        reported_tokens = [tokens for _, tokens in reported]
        baseline = reported_tokens[0] if reported_tokens else None
        final = reported_tokens[-1] if reported_tokens else None
        local_deltas = [
            current - previous
            for previous, current in zip(reported_tokens, reported_tokens[1:])
        ]
        max_local_delta = max(local_deltas) if local_deltas else 0
        cumulative_ratio = (
            (final / baseline)
            if baseline is not None and final is not None and baseline > 0
            else 0.0
        )
        cumulative_percent = (cumulative_ratio - 1.0) * 100.0

        if growth_records:
            first_before, first_after = growth_pairs[0]
            findings.append(
                Finding(
                    category=CATEGORY_CONTEXT,
                    title=f"Input context amplified across {len(growth_records)} call(s)",
                    reason=(
                        f"Reported input tokens grew from {first_before} to {first_after} on the first "
                        "flagged transition (>=1.5x and +500 tokens). "
                        f"Across the run, input moved from {baseline} to {final} "
                        f"({cumulative_percent:+.1f}% vs the first reported call); the largest step-to-step "
                        f"delta was {max_local_delta} tokens, which indicates a sharp local jump. "
                        "This can indicate context carry-over, repeated retrieval results, or static tool "
                        "definitions being paid for again. Token growth is evidence about where to inspect, "
                        "not proof that a particular payload was unnecessary."
                    ),
                    confidence="low",
                    record_ids=[str(r["record_id"]) for r in growth_records],
                    run_ids=[run_id],
                    saving_ratio=0.0,
                )
            )
        elif (
            len(reported_tokens) >= 3
            and baseline is not None
            and final is not None
            and baseline > 0
            and final - baseline >= 500
            and cumulative_ratio >= 1.5
        ):
            findings.append(
                Finding(
                    category=CATEGORY_CONTEXT,
                    title="Input context accumulated across the run",
                    reason=(
                        f"Reported input tokens grew from {baseline} to {final} "
                        f"({cumulative_percent:+.1f}% vs the first reported call), but no single transition "
                        "crossed the local >=1.5x and +500-token trigger. "
                        f"The largest step-to-step delta was {max_local_delta} tokens. This pattern is more "
                        "consistent with steady cumulative growth than one large payload jump. Token growth "
                        "is evidence about where to inspect, not proof that a particular payload was unnecessary."
                    ),
                    confidence="low",
                    record_ids=[str(record["record_id"]) for record, _ in reported[1:]],
                    run_ids=[run_id],
                    saving_ratio=0.0,
                )
            )

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
    for (run_id, _trace), group in by_run_models.items():
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
        # This category counts logical chains, not individual attempt records.
        CATEGORY_RATE_LIMIT: sum(f.category == CATEGORY_RATE_LIMIT for f in findings),
        CATEGORY_UNUSED: len(category_record_ids[CATEGORY_UNUSED]),
        CATEGORY_CONTEXT: len(category_record_ids[CATEGORY_CONTEXT]),
        CATEGORY_DETERMINISTIC: len(category_record_ids[CATEGORY_DETERMINISTIC]),
        CATEGORY_DUPLICATE: len(category_record_ids[CATEGORY_DUPLICATE]),
        CATEGORY_CACHE: len(category_record_ids[CATEGORY_CACHE]),
        CATEGORY_ORCHESTRATION: len(category_record_ids[CATEGORY_ORCHESTRATION]),
        CATEGORY_REPLANNING: len(category_record_ids[CATEGORY_REPLANNING]),
        CATEGORY_FRESHNESS: len(category_record_ids[CATEGORY_FRESHNESS]),
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
