"""Mark caller-supplied text so it cannot be read as an instruction."""

import json
from typing import Any

TASK_MARKER = "任务："


def fence(kind: str, content: str) -> str:
    """Wrap untrusted text. Closing tags and the task marker cannot break out."""
    safe = content.replace("</", "<\\/").replace(TASK_MARKER, "任务∶")
    return f"<{kind}>\n{safe}\n</{kind}>"


def task_suffix(payload: Any) -> str:
    """Append the machine-readable task after any fenced data."""
    return TASK_MARKER + json.dumps(payload, ensure_ascii=False)
