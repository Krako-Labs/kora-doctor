import base64
import binascii
import copy
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .parser import InputError
from .analyzer import _canonical_id


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
    "http_status_code": ("http.response.status_code", "http.status_code", "http_status_code"),
    "output_consumed": ("output_consumed", "kora.output_consumed", "gen_ai.output.consumed"),
    "conditional_consumer_exists": ("conditional_consumer_exists", "kora.conditional_consumer_exists", "gen_ai.output.conditional_consumer_exists"),
    "no_downstream_consumer": ("no_downstream_consumer", "kora.no_downstream_consumer", "gen_ai.output.no_downstream_consumer"),
    "plan_before_hash": ("plan_before_hash", "kora.plan_before_hash"),
    "plan_after_hash": ("plan_after_hash", "kora.plan_after_hash"),
    "next_action_before_hash": ("next_action_before_hash", "kora.next_action_before_hash"),
    "next_action_after_hash": ("next_action_after_hash", "kora.next_action_after_hash"),
    "nonempty_result": ("nonempty_result", "kora.nonempty_result"),
    "expected_repeat": ("expected_repeat", "kora.expected_repeat"),
    "source_id_hash": ("source_id_hash", "kora.source_id_hash"),
    "source_snapshot_hash": ("source_snapshot_hash", "kora.source_snapshot_hash"),
    "source_refresh": ("source_refresh", "kora.source_refresh"),
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

# Existing AUDR labels and the OTel sidecar are independent evidence sources.
# If they disagree, do not replace one with the other or infer lineage from
# that ambiguous record. Keep the original AUDR metadata unchanged.
_LABEL_ALIASES = {
    name: tuple(dict.fromkeys((name,) + sources))
    for name, sources in _CUSTOM_LABEL_MAP.items()
}
_LABEL_ALIASES.update({
    "tool_args_hash": ("tool_args_hash", "kora.tool_args_hash", "args_hash", "input_hash"),
    "tool_result_hash": ("tool_result_hash", "kora.tool_result_hash", "result_hash", "output_hash"),
    "operation_status": ("operation_status", "tool_status", "kora.operation_status"),
})


def _labels_conflict(labels: Dict[str, Any], proposed: Dict[str, str]) -> bool:
    for name, value in proposed.items():
        for alias in _LABEL_ALIASES.get(name, (name,)):
            existing = labels.get(alias)
            if existing is not None and str(existing).strip().lower() != str(value).strip().lower():
                return True
    return False



def _parse_otel_integer(raw: str) -> int:
    # Keep OTLP numeric decoding bounded and consistent on Python 3.9/3.13.
    # JSON may contain giant numeric literals even in unmatched OTel spans.
    if len(raw) > 128:
        raise InputError("invalid OTel JSON number: integer exceeds supported length")
    try:
        return int(raw)
    except ValueError as exc:
        raise InputError("invalid OTel JSON number: malformed integer") from exc


def _validate_json_nesting(payload: Any, *, max_depth: int = 128) -> None:
    """Bound nested JSON structures before recursive AnyValue decoding/hashing."""
    pending = [(payload, 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (dict, list)):
            if depth > max_depth:
                raise InputError("invalid OTel structure: nesting limit exceeded")
            children = value.values() if isinstance(value, dict) else value
            pending.extend((item, depth + 1) for item in children)
        elif isinstance(value, float) and not math.isfinite(value):
            raise InputError("invalid OTel structure: non-finite JSON number")


def _normalize_any_scalar(kind: str, scalar: Any) -> Any:
    # OTLP JSON uses int64 decimal strings, double JSON numbers (or special
    # numeric strings), boolean JSON booleans, and base64 text for bytes.
    error = "invalid OTel attributes: malformed AnyValue scalar"
    if kind == "stringValue":
        if not isinstance(scalar, str):
            raise InputError(error)
        return scalar
    elif kind == "boolValue":
        if not isinstance(scalar, bool):
            raise InputError(error)
        return scalar
    elif kind == "intValue":
        if isinstance(scalar, bool) or not isinstance(scalar, (int, float, str)):
            raise InputError(error)
        if isinstance(scalar, str) and len(scalar) > 128:
            raise InputError(error)
        try:
            number = Decimal(str(scalar))
        except InvalidOperation as exc:
            raise InputError(error) from exc
        if (not number.is_finite() or number != number.to_integral_value()
                or not -(2 ** 63) <= number < 2 ** 63):
            raise InputError(error)
        return int(number)
    elif kind == "doubleValue":
        if isinstance(scalar, bool) or not isinstance(scalar, (int, float, str)):
            raise InputError(error)
        if isinstance(scalar, str) and len(scalar) > 128:
            raise InputError(error)
        if scalar in ("NaN", "Infinity", "-Infinity"):
            return scalar
        try:
            number = float(scalar)
        except (ValueError, OverflowError) as exc:
            raise InputError(error) from exc
        if not math.isfinite(number):
            raise InputError(error)
        return number
    elif kind == "bytesValue":
        if not isinstance(scalar, str):
            raise InputError(error)
        try:
            data = scalar.encode("ascii")
            decoded = base64.b64decode(data + b"=" * (-len(data) % 4),
                                       altchars=b"-_", validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise InputError(error) from exc
        return base64.b64encode(decoded).decode("ascii")


def _decode_any_value(value: Any, *, wrapped: bool = False) -> Any:
    """Decode OTLP AnyValue containers, preserving simple-map raw values."""
    if not isinstance(value, dict):
        if wrapped:
            raise InputError("invalid OTel attributes: AnyValue must be an object")
        return value

    variants = ("stringValue", "intValue", "doubleValue", "boolValue",
                "bytesValue", "arrayValue", "kvlistValue")
    present = [key for key in variants if key in value]
    if len(present) > 1:
        raise InputError("invalid OTel attributes: multiple AnyValue types")
    if not present:
        if wrapped:
            raise InputError("invalid OTel attributes: missing AnyValue type")
        # Simplified sidecars may contain raw argument/result JSON objects.
        return value

    kind = present[0]
    if kind in variants[:5]:
        return _normalize_any_scalar(kind, value[kind])

    container = value[kind]
    if not isinstance(container, dict):
        raise InputError("invalid OTel attributes: AnyValue container must be an object")
    values = container.get("values", [])
    if not isinstance(values, list):
        raise InputError("invalid OTel attributes: AnyValue values must be an array")
    if kind == "arrayValue":
        return [_decode_any_value(item, wrapped=True) for item in values]

    result: Dict[str, Any] = {}
    for item in values:
        if (not isinstance(item, dict) or not isinstance(item.get("key"), str)
                or "value" not in item):
            raise InputError("invalid OTel attributes: kvlist entries must be key/value objects")
        if item["key"] in result:
            raise InputError("invalid OTel attributes: duplicate kvlist key")
        result[item["key"]] = _decode_any_value(item["value"], wrapped=True)
    return result


def _attributes(span: Dict[str, Any]) -> Dict[str, Any]:
    raw = span.get("attributes", {})
    if isinstance(raw, dict):
        return {key: _decode_any_value(value) for key, value in raw.items()}
    if not isinstance(raw, list):
        raise InputError("invalid OTel attributes: expected an object or key/value array")
    result: Dict[str, Any] = {}
    for item in raw:
        if (not isinstance(item, dict) or not isinstance(item.get("key"), str)
                or "value" not in item):
            raise InputError("invalid OTel attributes: expected key/value objects")
        if item["key"] in result:
            raise InputError("invalid OTel attributes: duplicate attribute key")
        result[item["key"]] = _decode_any_value(item["value"], wrapped=True)
    return result


def _iter_spans(payload: Any) -> Iterable[Dict[str, Any]]:
    # A malformed exporter payload must not look like a successful analysis
    # with zero matched spans. Never print raw span fields in an error.
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                raise InputError("invalid OTel structure: span array items must be objects")
            yield item
        return

    if not isinstance(payload, dict):
        raise InputError("invalid OTel structure: expected an object or span array")

    if "resourceSpans" in payload:
        groups = payload["resourceSpans"]
        if not isinstance(groups, list):
            raise InputError("invalid OTel structure: resourceSpans must be an array")
        for resource_span in groups:
            if not isinstance(resource_span, dict):
                raise InputError("invalid OTel structure: resourceSpans items must be objects")
            scope_groups = resource_span.get("scopeSpans", [])
            if not isinstance(scope_groups, list):
                raise InputError("invalid OTel structure: scopeSpans must be an array")
            if not scope_groups and "instrumentationLibrarySpans" in resource_span:
                # Preserve legacy OTLP fallback when an empty scopeSpans
                # coexists with actual instrumentationLibrarySpans.
                scope_groups = resource_span["instrumentationLibrarySpans"]
                if not isinstance(scope_groups, list):
                    raise InputError(
                        "invalid OTel structure: instrumentationLibrarySpans must be an array"
                    )
            for scope in scope_groups:
                if not isinstance(scope, dict):
                    raise InputError("invalid OTel structure: scopeSpans items must be objects")
                spans = scope.get("spans", [])
                if not isinstance(spans, list):
                    raise InputError("invalid OTel structure: spans must be an array")
                for span in spans:
                    if not isinstance(span, dict):
                        raise InputError("invalid OTel structure: spans items must be objects")
                    yield span
        return

    if "spans" in payload:
        spans = payload["spans"]
        if not isinstance(spans, list):
            raise InputError("invalid OTel structure: spans must be an array")
        for span in spans:
            if not isinstance(span, dict):
                raise InputError("invalid OTel structure: spans items must be objects")
            yield span
        return

    if "spanId" in payload or "span_id" in payload:
        yield payload
        return

    raise InputError("invalid OTel structure: no supported span container found")


def _normalize_id(value: Any, *, trace: bool = False) -> str:
    # Match the analyzer's identity boundary. 16-hex span IDs and 32-hex / UUID
    # trace IDs are canonical; opaque identifiers must match byte-for-byte.
    return _canonical_id(value, hex_length=32 if trace else 16)


def _canonical_payload(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            # Raw tool text is byte-significant: do not trim it. Some valid
            # numeric-looking strings exceed Python's integer digit limit;
            # treat those as text rather than exposing a parser traceback.
            return value
        if not isinstance(parsed, (dict, list)):
            # An OTel stringValue may contain `42`, `true`, `null`, or a
            # quoted JSON string as literal tool text. Parsing these as JSON
            # scalars can erase whitespace and even make a stringValue `42`
            # identical to an intValue 42. Preserve its text and type.
            return json.dumps(value, ensure_ascii=False)
        # A JSON object/array string is structurally comparable regardless of
        # key order or insignificant JSON formatting whitespace.
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


def _event_time(record: Dict[str, Any]):
    value = record.get("timing", {}).get("event_time")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def enrich_with_otel(records: List[Dict[str, Any]], path: str):
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise InputError(f"OTel sidecar not found: {path}")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"), parse_int=_parse_otel_integer)
    except UnicodeDecodeError as exc:
        raise InputError("OTel sidecar must be UTF-8 encoded") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid OTel JSON: {exc.msg}") from exc
    except RecursionError as exc:
        raise InputError("invalid OTel JSON: nesting limit exceeded") from exc

    _validate_json_nesting(payload)
    spans = list(_iter_spans(payload))
    # Validate identifiers even for unmatched spans. Coercing OTel numbers or
    # booleans to strings could attach evidence to a different opaque AUDR ID.
    for span in spans:
        for key in ("spanId", "span_id", "traceId", "trace_id"):
            if key in span and span[key] is not None and not isinstance(span[key], str):
                raise InputError(f"invalid OTel structure: {key} must be a string")
        # Both OTLP spellings may appear in one sidecar. Do not silently pick
        # the first when they refer to different spans or traces.
        for camel, snake, trace in (
            ("spanId", "span_id", False),
            ("traceId", "trace_id", True),
        ):
            if span.get(camel) is not None and span.get(snake) is not None:
                if _normalize_id(span[camel], trace=trace) != _normalize_id(
                    span[snake], trace=trace
                ):
                    kind = "trace" if trace else "span"
                    raise InputError(f"invalid OTel structure: conflicting {kind} ID aliases")
    # Validate every exported span, including unmatched ones. Otherwise an
    # invalid sidecar can be silently reported as a successful zero-match run.
    attrs_by_span = {id(span): _attributes(span) for span in spans}
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
        "conflicts": 0,
        "inferred_retries": 0,
    }

    matched_candidates: Dict[int, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for span in spans:
        span_id = _normalize_id(span.get("spanId") or span.get("span_id"))
        if not span_id:
            continue
        candidates = by_span.get(span_id) or []
        if not candidates:
            continue

        trace_id = _normalize_id(span.get("traceId") or span.get("trace_id"), trace=True)
        record = None
        if trace_id:
            exact = [
                candidate for candidate in candidates
                if _normalize_id(candidate.get("run", {}).get("trace_id"), trace=True) == trace_id
            ]
            if len(exact) == 1:
                record = exact[0]
        elif len(candidates) == 1:
            # Span IDs can collide across traces. With no sidecar trace ID,
            # only a uniquely matching trace-less AUDR record is safe.
            candidate = candidates[0]
            if not _normalize_id(candidate.get("run", {}).get("trace_id"), trace=True):
                record = candidate

        if record is None:
            continue

        matched_candidates.setdefault(id(record), []).append((span, record))

    # Duplicate sidecar spans may disagree. Never use last-write-wins for
    # status or argument evidence attached to the same AUDR record.
    matched_record_objects = set()
    otel_argument_records = set()
    otel_status_records = set()
    for matches in matched_candidates.values():
        if len(matches) != 1:
            continue
        span, record = matches[0]
        attrs = attrs_by_span[id(span)]
        labels = record.setdefault("attribution", {}).setdefault("labels", {})
        proposed: Dict[str, str] = {}

        args_value = _first_attr(attrs, _ARG_ATTRIBUTE_NAMES)
        if args_value is not None:
            proposed["tool_args_hash"] = _fingerprint(args_value)

        result_value = _first_attr(attrs, _RESULT_ATTRIBUTE_NAMES)
        if result_value is not None:
            proposed["tool_result_hash"] = _fingerprint(result_value)

        status = _span_status(span, attrs)
        if status:
            proposed["operation_status"] = status

        for target, source_names in _CUSTOM_LABEL_MAP.items():
            value = _first_attr(attrs, source_names)
            if value is not None:
                proposed[target] = str(value).lower() if isinstance(value, bool) else str(value)

        # A record with contradictory original versus sidecar evidence is
        # not a trusted match. Fail closed, rather than using either result
        # to manufacture an explicit retry, status, or argument fingerprint.
        if _labels_conflict(labels, proposed):
            stats["conflicts"] += 1
            continue

        labels.update(proposed)
        matched_record_objects.add(id(record))
        if args_value is not None:
            otel_argument_records.add(id(record))
        if status:
            otel_status_records.add(id(record))
        stats["matched"] += 1
        stats["args_hashed"] += int(args_value is not None)
        stats["results_hashed"] += int(result_value is not None)
        stats["statuses"] += int(bool(status))

    # Inferred lineage is not proof of a real retry. Only connect uniquely
    # identified calls with the same run, trace, tool, arguments, and strictly
    # increasing offset-aware event times. Do not guess from step order alone.
    problem = {"failed", "timeout", "unknown"}
    record_id_counts = Counter(str(record.get("record_id") or "") for record in enriched)
    by_operation: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for record in enriched:
        # A missing or ambiguous sidecar match must not manufacture a link
        # solely from pre-existing AUDR labels.
        if id(record) not in matched_record_objects or id(record) not in otel_argument_records:
            continue
        run = record.get("run", {})
        run_id = run.get("run_id")
        labels = record.get("attribution", {}).get("labels") or {}
        args_hash = labels.get("tool_args_hash")
        if not run_id or not args_hash:
            continue
        key = (
            str(run_id),
            _normalize_id(run.get("trace_id"), trace=True),
        ) + _tool_key(record) + (str(args_hash),)
        by_operation.setdefault(key, []).append(record)

    for operation_records in by_operation.values():
        if len(operation_records) < 2:
            continue
        timed = [(_event_time(record), record) for record in operation_records]
        if any(
            when is None or record_id_counts[str(record.get("record_id") or "")] != 1
            for when, record in timed
        ):
            continue
        timed.sort(key=lambda item: item[0])
        # Equal timestamps and backwards step numbers cannot establish
        # a unique prior attempt. Disable inference for the entire operation.
        if any(timed[i - 1][0] >= timed[i][0] for i in range(1, len(timed))):
            continue
        steps = [record.get("run", {}).get("step") for _, record in timed]
        if any(
            isinstance(steps[i - 1], int) and not isinstance(steps[i - 1], bool)
            and isinstance(steps[i], int) and not isinstance(steps[i], bool)
            and steps[i - 1] > steps[i]
            for i in range(1, len(steps))
        ):
            continue

        previous = None
        for _, record in timed:
            labels = record.get("attribution", {}).get("labels") or {}
            status = str(labels.get("operation_status", "")).lower()
            if previous is not None and not labels.get("retry_of"):
                labels["retry_of"] = str(previous["record_id"])
                labels["retry_inferred_by_otel"] = "true"
                previous_attempt = previous.get("attribution", {}).get("labels", {}).get("retry_attempt")
                try:
                    labels["retry_attempt"] = str(int(previous_attempt or "1") + 1)
                except ValueError:
                    labels["retry_attempt"] = "2"
                stats["inferred_retries"] += 1
            # Only an OTel-observed failure can trigger inferred lineage.
            # AUDR-only status metadata and span IDs are not enough.
            previous = record if id(record) in otel_status_records and status in problem else None

    return enriched, stats
