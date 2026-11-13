import json
import math
from pathlib import Path
from typing import Any, Dict, List


class InputError(ValueError):
    pass


_REQUIRED_TOP = ("spec_version", "record_id", "emitter", "timing", "resource", "usage", "run", "attribution")
_REQUIRED_RESOURCE = ("provider", "type", "name", "operation")
_REQUIRED_RUN = ("run_id", "span_id")


def _validate_record(record: Any, index: int) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise InputError(f"record {index}: expected a JSON object")

    missing = [k for k in _REQUIRED_TOP if k not in record]
    if missing:
        raise InputError(f"record {index}: missing required AUDR field(s): {', '.join(missing)}")

    for field in ("emitter", "timing", "attribution"):
        if not isinstance(record.get(field), dict):
            raise InputError(f"record {index}: {field} must be an object")
    labels = record["attribution"].get("labels")
    if labels is not None and not isinstance(labels, dict):
        raise InputError(f"record {index}: attribution.labels must be an object")

    resource = record.get("resource")
    if not isinstance(resource, dict):
        raise InputError(f"record {index}: resource must be an object")
    missing_resource = [k for k in _REQUIRED_RESOURCE if k not in resource]
    if missing_resource:
        raise InputError(
            f"record {index}: resource missing required field(s): {', '.join(missing_resource)}"
        )
    # These fields participate in grouping keys and are resource identifiers,
    # not structured data. An array/map would crash dictionary-key grouping.
    for field in _REQUIRED_RESOURCE:
        if not isinstance(resource[field], str):
            raise InputError(f"record {index}: resource.{field} must be a string")
    if resource.get("modality") is not None and not isinstance(resource["modality"], str):
        raise InputError(f"record {index}: resource.modality must be a string")

    run = record.get("run")
    if not isinstance(run, dict):
        raise InputError(f"record {index}: run must be an object")
    missing_run = [k for k in _REQUIRED_RUN if k not in run]
    if missing_run:
        raise InputError(f"record {index}: run missing required field(s): {', '.join(missing_run)}")

    # Identifiers must not merge after numeric/bool-to-string coercion.
    # Report only the field name, never the user's identifier value.
    if not isinstance(record["record_id"], str) or not record["record_id"].strip():
        raise InputError(f"record {index}: record_id must be a non-empty string")
    for field in _REQUIRED_RUN:
        if not isinstance(run[field], str) or not run[field].strip():
            raise InputError(f"record {index}: run.{field} must be a non-empty string")
    if "trace_id" in run and (
        not isinstance(run["trace_id"], str)
        or not run["trace_id"]
        or run["trace_id"] != run["trace_id"].strip()
    ):
        # Analyzer normalizes canonical UUIDs, not opaque trace labels.
        # Do not let leading/trailing whitespace create a false identity match.
        raise InputError(f"record {index}: run.trace_id must be a non-empty string without surrounding whitespace")

    # Optional step is an integer sequence index. Reject malformed values
    # before mixed types reach the analyzer's ordered comparisons.
    step = run.get("step")
    if step is not None and (type(step) is not int or step < 0):
        raise InputError(f"record {index}: run.step must be a non-negative integer")

    usage = record.get("usage")
    if not isinstance(usage, dict) or not usage:
        raise InputError(f"record {index}: usage must be a non-empty object")
    # Optional usage sections are objects. Reject malformed containers early
    # with field-only errors rather than traceback or private input content.
    for section in ("llm", "tool"):
        if section in usage and not isinstance(usage[section], dict):
            raise InputError(f"record {index}: usage.{section} must be an object")
    llm = usage.get("llm") or {}
    for field in ("input_tokens", "output_tokens", "reasoning_tokens",
                  "cache_read_tokens", "cache_write_tokens", "requests"):
        if isinstance(llm.get(field), (dict, list)):
            raise InputError(f"record {index}: usage.llm.{field} must be a scalar value")
    tool = usage.get("tool") or {}
    if isinstance(tool.get("type"), (dict, list)):
        raise InputError(f"record {index}: usage.tool.type must be a scalar value")

    return record


def _reject_nonfinite(_value: str) -> None:
    # json.loads accepts NaN and Infinity by default, although JSON does not.
    raise InputError("invalid AUDR JSON number: non-finite value")


def _parse_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InputError("invalid AUDR JSON number: non-finite value")
    return parsed


def _load_json(text: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_nonfinite, parse_float=_parse_float)
    except RecursionError as exc:
        raise InputError("invalid AUDR JSON: nesting too deep") from exc
    except ValueError as exc:
        if isinstance(exc, (InputError, json.JSONDecodeError)):
            raise
        raise InputError("invalid AUDR JSON number") from exc


def load_records(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise InputError(f"input not found: {path}")
    if not p.is_file():
        raise InputError(f"input must be a file: {path}")

    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("input must be UTF-8 encoded") from exc
    if not text.strip():
        raise InputError("input contains no records")

    records: List[Any]
    try:
        parsed = _load_json(text)
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            records = [parsed]
        else:
            raise InputError("JSON input must be an AUDR object or array of AUDR objects")
    except json.JSONDecodeError:
        records = []
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(_load_json(line))
            except json.JSONDecodeError as exc:
                raise InputError(f"invalid JSONL at line {line_no}: {exc.msg}") from exc
            except InputError as exc:
                # Only propagate controlled parser messages, never source values.
                raise InputError(f"invalid JSONL at line {line_no}: {exc}") from exc

    if not records:
        raise InputError("input contains no records")

    validated = []
    seen_record_ids: Dict[str, int] = {}
    for index, record in enumerate(records, 1):
        checked = _validate_record(record, index)
        # Findings reference record_id, and savings are looked up by ID.
        # A collision would assign a finding to an unrelated call's cost.
        record_id = str(checked["record_id"])
        if record_id in seen_record_ids:
            raise InputError(
                f"record {index}: duplicate record_id (already seen in "
                f"record {seen_record_ids[record_id]})"
            )
        seen_record_ids[record_id] = index
        validated.append(checked)
    return validated
