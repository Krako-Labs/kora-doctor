import json
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

    resource = record.get("resource")
    if not isinstance(resource, dict):
        raise InputError(f"record {index}: resource must be an object")
    missing_resource = [k for k in _REQUIRED_RESOURCE if k not in resource]
    if missing_resource:
        raise InputError(
            f"record {index}: resource missing required field(s): {', '.join(missing_resource)}"
        )

    run = record.get("run")
    if not isinstance(run, dict):
        raise InputError(f"record {index}: run must be an object")
    missing_run = [k for k in _REQUIRED_RUN if k not in run]
    if missing_run:
        raise InputError(f"record {index}: run missing required field(s): {', '.join(missing_run)}")

    usage = record.get("usage")
    if not isinstance(usage, dict) or not usage:
        raise InputError(f"record {index}: usage must be a non-empty object")

    return record


def load_records(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise InputError(f"input not found: {path}")
    if not p.is_file():
        raise InputError(f"input must be a file: {path}")

    text = p.read_text(encoding="utf-8")
    if not text.strip():
        raise InputError("input contains no records")

    records: List[Any]
    try:
        parsed = json.loads(text)
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
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise InputError(f"invalid JSONL at line {line_no}: {exc.msg}") from exc

    if not records:
        raise InputError("input contains no records")

    return [_validate_record(record, i) for i, record in enumerate(records, 1)]
