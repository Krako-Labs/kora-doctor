import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from .model import AuditReport, Finding


CATEGORY_DUPLICATE = "duplicate_repeated"
CATEGORY_CACHE = "cache_reuse"
CATEGORY_DETERMINISTIC = "deterministic_candidate"
CATEGORY_SMALLER = "smaller_model_candidate"
CATEGORY_ORCHESTRATION = "orchestration_overhead"

DETERMINISTIC_KEYWORDS = (
    "classif", "route", "routing", "validat", "schema", "format",
    "extract", "normaliz", "mapping", "parse", "categor", "filter",
)

FRONTIER_MODEL_PATTERNS = (
    r"gpt[-_ ]?(?:5|6)",
    r"claude[-_ ].*(?:opus|sonnet)",
    r"(?:gemini|vertex).*(?:pro|ultra)",
    r"o[134][-_ ]",
)

LOW_COST_MODEL_MARKERS = ("mini", "nano", "haiku", "flash", "small")


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


def _frontier_model(name: str) -> bool:
    value = (name or "").lower()
    if any(marker in value for marker in LOW_COST_MODEL_MARKERS):
        return False
    return any(re.search(pattern, value) for pattern in FRONTIER_MODEL_PATTERNS)


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

    findings: List[Finding] = []

    by_run_signature = defaultdict(list)
    for record in model_records:
        run_id = str(record["run"]["run_id"])
        by_run_signature[(run_id, _usage_signature(record))].append(record)

    for (run_id, _), group in by_run_signature.items():
        if len(group) >= 2:
            duplicates = group[1:]
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

    by_signature = defaultdict(list)
    for record in model_records:
        by_signature[_usage_signature(record)].append(record)

    for _, group in by_signature.items():
        run_ids = {str(r["run"]["run_id"]) for r in group}
        if len(run_ids) >= 2 and len(group) >= 2:
            candidates = group[1:]
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

    for record in model_records:
        text = _context_text(record)
        hit = next((kw for kw in DETERMINISTIC_KEYWORDS if kw in text), None)
        if hit:
            findings.append(
                Finding(
                    category=CATEGORY_DETERMINISTIC,
                    title="Possible deterministic step",
                    reason=(
                        f"Run metadata contains '{hit}', a pattern often implemented with rules, parsing, "
                        "routing, validation, or fixed mappings. Payload evidence is not available in AUDR."
                    ),
                    confidence="low",
                    record_ids=[str(record["record_id"])],
                    run_ids=[str(record["run"]["run_id"])],
                    saving_ratio=0.80,
                )
            )

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
                        "A smaller model may be sufficient, but task quality requirements are not visible in AUDR."
                    ),
                    confidence="low",
                    record_ids=[str(record["record_id"])],
                    run_ids=[str(record["run"]["run_id"])],
                    saving_ratio=0.50,
                )
            )

    by_run = defaultdict(list)
    for record in model_records:
        by_run[str(record["run"]["run_id"])].append(record)

    for run_id, group in by_run.items():
        ordered = sorted(
            group,
            key=lambda r: (
                r.get("run", {}).get("step") is None,
                r.get("run", {}).get("step") or 10**9,
                str(r.get("timing", {}).get("event_time", "")),
            ),
        )
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
        CATEGORY_DUPLICATE: len(category_record_ids[CATEGORY_DUPLICATE]),
        CATEGORY_CACHE: len(category_record_ids[CATEGORY_CACHE]),
        CATEGORY_DETERMINISTIC: len(category_record_ids[CATEGORY_DETERMINISTIC]),
        CATEGORY_SMALLER: len(category_record_ids[CATEGORY_SMALLER]),
        CATEGORY_ORCHESTRATION: len(category_record_ids[CATEGORY_ORCHESTRATION]),
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
    )
