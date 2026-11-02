import hashlib
import json


def arguments_hash(arguments: dict) -> str:
    payload = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def stable_operation_id(
    run_id: str,
    tool_name: str,
    arguments: dict,
    task_id: str | None = None,
) -> str:
    """Stable key: run, task, tool, and argument hash.

    A missing task id keeps the historical three-part form so older in-memory
    callers stay readable. The hash is the full SHA-256; the id carries a prefix.
    """
    digest = arguments_hash(arguments)[:24]
    if task_id:
        return f"{run_id}:{task_id}:{tool_name}:{digest}"
    return f"{run_id}:{tool_name}:{digest}"

