"""Execution semantics for capabilities and deliverables.

Goal parsers describe what a request needs.  This registry is the server-owned
contract that tells routing what those requirements mean operationally.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    name: str
    requires_external_operation: bool = False
    requires_realtime_information: bool = False


CAPABILITIES: dict[str, CapabilityDefinition] = {
    "analysis": CapabilityDefinition("analysis"),
    "calculate": CapabilityDefinition("calculate"),
    "save_report": CapabilityDefinition("save_report"),
    "web_fetch": CapabilityDefinition(
        "web_fetch", requires_external_operation=True, requires_realtime_information=True
    ),
    "web_search": CapabilityDefinition(
        "web_search", requires_external_operation=True, requires_realtime_information=True
    ),
    "workspace_read": CapabilityDefinition("workspace_read"),
}

# An inline response is rendered by the Answer Agent and needs no worker.
# Independent persisted artifacts require an explicit artifact capability.
ARTIFACT_CAPABILITIES: dict[str, str] = {
    "text": "save_report",
    "markdown": "save_report",
    "json": "save_report",
}


def artifact_capability(output_format: str) -> str | None:
    return ARTIFACT_CAPABILITIES.get(output_format)


def external_flags(required: set[str]) -> tuple[bool, bool]:
    definitions = [CAPABILITIES[name] for name in required if name in CAPABILITIES]
    return (
        any(item.requires_external_operation for item in definitions),
        any(item.requires_realtime_information for item in definitions),
    )
