import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .parser import InputError


_ARG_ATTRIBUTE_NAMES = (
    "gen_ai.tool.call.arguments",
    "tool_call.function.arguments",
    "tool.arguments",
)

_RESULT_ATTRIBUTE_NAMES = (
    "gen_ai.tool.call.result",
    "tool.result",
)

_CUSTOM_LABEL_MAP = {
    "retry_of": ("retry_of", "kora.retry_of", "gen_ai.retry_of"),
    "retry_attempt": ("retry_attempt", "kora.retry_attempt", "gen_ai.retry.attempt"),
    "output_consumed": ("output_consumed", "kora.output_consumed", "gen_ai.output.consumed"),
    "conditional_consumer_exists": ("conditional_consumer_exists", "kora.conditional_consumer_exists", "gen_ai.output.conditional_consumer_exists"),
    "no_downstream_consumer": ("no_downstream_consumer", "kora.no_downstream_consumer", "gen_ai.output.no_downstream_consumer"),
    "step_role": ("step_role", "kora.step_role", "gen_ai.step.role"),
    "planned_step_count": (
        "planned_step_count",
        "kora.planned_step_count",
        "gen_ai.plan.step_count",
    ),
    "executed_step_count": (
        "executed_step_count",
        "kora.executed_step_count",
        "gen_ai.plan.executed_step_count",
    ),
}


def _decode_any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            return value[key]
    array = value.get("arrayValue")
    if isinstance(array, dict):
        values = array.get("values") or []
        return [_decode_any_value(item) for item in values]
    kvlist = value.get("kvlistValue")
    if isinstance(kvlist, dict):
        result = {}
        for item in kvlist.get("values") or []:
            if isinstance(item, dict) and "key" in item:
                result[str(item["key"])] = _decode_any_value(item.get("value"))
        return result
    return value


def _attributes(span: Dict[str, Any]) -> Dict[str, Any]:
    raw = span.get("attributes") or {}
    if isinstance(raw, dict):
        return {str(k): _decode_any_value(v) for k, v in raw.items()}
    result: Dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or "key" not in item:
                continue
            result[str(item["key"])] = _decode_any_value(item.get("value"))
    return result


def _iter_spans(payload: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return

    if not isinstance(payload, dict):
        return

    if isinstance(payload.get("resourceSpans"), list):
        for resource_span in payload["resourceSpans"]:
            if not isinstance(resource_span, dict):
                continue
            scope_groups = resource_span.get("scopeSpans") or resource_span.get("instrumentationLibrarySpans") or []
            for scope in scope_groups:
                if not isinstance(scope, dict):
                    continue
                for span in scope.get("spans") or []:
                    if isinstance(span, dict):
                        yield span
        return

    if isinstance(payload.get("spans"), list):
        for span in payload["spans"]:
            if isinstance(span, dict):
                yield span
        return

    if "spanId" in payload or "span_id" in payload:
        yield payload


def _normalize_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    compact = text.replace("-", "")
    if compact and all(ch in "0123456789abcdef" for ch in compact):
        return compact
    return text


def _canonical_payload(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return stripped
        return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Any) -> str:
    canonical = _canonical_payload(value)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _span_status(span: Dict[str, Any], attrs: Dict[str, Any]):
    for key in ("operation_status", "tool_status", "gen_ai.operation.status"):
        value = attrs.get(key)
        if value is not None:
            return str(value).strip().lower()

    status = span.get("status")
    code = status.get("code") if isinstance(status, dict) else status
    if isinstance(code, str):
        upper = code.upper()
        if "ERROR" in upper:
            return "failed"
        if upper.endswith("_OK") or upper == "OK" or "STATUS_CODE_OK" in upper:
            return "success"
    if isinstance(code, (int, float)):
        if int(code) == 2:
            return "failed"
        if int(code) == 1:
            return "success"

    if attrs.get("error.type") or attrs.get("exception.type"):
        return "failed"
    return None


def _first_attr(attrs: Dict[str, Any], names: Tuple[str, ...]):
    for name in names:
        if name in attrs and attrs[name] is not None:
            return attrs[name]
    return None


def _tool_key(record: Dict[str, Any]):
    resource = record.get("resource", {})
    return (
        str(resource.get("provider", "")),
        str(resource.get("name", "")),
        str(resource.get("operation", "")),
    )


def enrich_with_otel(records: List[Dict[str, Any]], path: str):
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise InputError(f"OTel sidecar not found: {path}")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid OTel JSON: {exc.msg}") from exc

    spans = list(_iter_spans(payload))
    enriched = copy.deepcopy(records)

    by_span: Dict[str, List[Dict[str, Any]]] = {}
    for record in enriched:
        span_id = _normalize_id(record.get("run", {}).get("span_id"))
        if span_id:
            by_span.setdefault(span_id, []).append(record)

    stats = {
        "spans": len(spans),
        "matched": 0,
        "args_hashed": 0,
        "results_hashed": 0,
        "statuses": 0,
        "inferred_retries": 0,
    }

    for span in spans:
        span_id = _normalize_id(span.get("spanId") or span.get("span_id"))
        if not span_id:
            continue
        candidates = by_span.get(span_id) or []
        if not candidates:
            continue

        trace_id = _normalize_id(span.get("traceId") or span.get("trace_id"))
        record = None
        if trace_id:
            for candidate in candidates:
                candidate_trace = _normalize_id(candidate.get("run", {}).get("trace_id"))
                if candidate_trace and candidate_trace == trace_id:
                    record = candidate
                    break
        if record is None:
            record = candidates[0]

        stats["matched"] += 1
        attrs = _attributes(span)
        labels = record.setdefault("attribution", {}).setdefault("labels", {})

        args_value = _first_attr(attrs, _ARG_ATTRIBUTE_NAMES)
        if args_value is not None:
            labels["tool_args_hash"] = _fingerprint(args_value)
            stats["args_hashed"] += 1

        result_value = _first_attr(attrs, _RESULT_ATTRIBUTE_NAMES)
        if result_value is not None:
            labels["tool_result_hash"] = _fingerprint(result_value)
            stats["results_hashed"] += 1

        status = _span_status(span, attrs)
        if status:
            labels["operation_status"] = status
            stats["statuses"] += 1

        for target, source_names in _CUSTOM_LABEL_MAP.items():
            value = _first_attr(attrs, source_names)
            if value is not None:
                labels[target] = str(value).lower() if isinstance(value, bool) else str(value)

    # Infer retry lineage only from stronger evidence: same run, same tool,
    # same argument fingerprint, and an earlier failed/timeout/unknown status.
    problem = {"failed", "timeout", "unknown"}
    by_run: Dict[str, List[Dict[str, Any]]] = {}
    for record in enriched:
        by_run.setdefault(str(record.get("run", {}).get("run_id")), []).append(record)

    for run_records in by_run.values():
        ordered = sorted(
            run_records,
            key=lambda record: (
                record.get("run", {}).get("step") is None,
                record.get("run", {}).get("step") or 10**9,
                str(record.get("timing", {}).get("event_time", "")),
            ),
        )
        last_problem: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for record in ordered:
            labels = record.get("attribution", {}).get("labels") or {}
            args_hash = labels.get("tool_args_hash")
            if not args_hash:
                continue
            key = _tool_key(record) + (args_hash,)
            status = str(labels.get("operation_status", "")).lower()
            previous = last_problem.get(key)

            if previous is not None and not labels.get("retry_of"):
                labels["retry_of"] = str(previous["record_id"])
                previous_attempt = previous.get("attribution", {}).get("labels", {}).get("retry_attempt")
                try:
                    labels["retry_attempt"] = str(int(previous_attempt or "1") + 1)
                except ValueError:
                    labels["retry_attempt"] = "2"
                stats["inferred_retries"] += 1

            if status in problem:
                last_problem[key] = record
            elif status == "success" and key in last_problem:
                last_problem.pop(key, None)

    return enriched, stats
