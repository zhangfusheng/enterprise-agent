"""Deterministic resolution of server-owned input requirement state."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from enterprise_agent.contracts.goals import InputMaterial, InputRequirement

_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?%?")
_TIME_RANGE = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{4}\s*年(?:\s*\d{1,2}\s*月)?|"
    r"(?:本|上|下)(?:周|月|季度|年)|"
    r"最近\s*\d+\s*(?:天|日|周|个月|月|年)",
    re.IGNORECASE,
)
_TEMPLATE_BODY = re.compile(r"(?:模板|template)\s*[:：]\s*\S+", re.IGNORECASE)
_DESTINATION = re.compile(
    r"(?:保存到|写入|输出到)\s*\S+|"
    r"(?:^|\s)(?:/[^\s]+|[\w.-]+\.(?:md|txt|json|csv|pdf|docx?))(?:\s|$)",
    re.IGNORECASE,
)
_CALLER_MATERIAL = re.compile(r"我的资料|上传的|附件|这份文件|给定资料", re.IGNORECASE)
_MISSING_MATERIAL = "请提供要分析的资料或文件。"


class RequirementResolution(BaseModel):
    requirements: list[InputRequirement]
    missing_information: list[str]


def infer_caller_material_requirements(
    request_text: str,
    goal_ids: list[str],
    *,
    source: Literal["rules", "legacy"],
) -> list[InputRequirement]:
    """Infer the old caller-material intent without resolving its status."""

    if not _CALLER_MATERIAL.search(request_text):
        return []
    return [
        InputRequirement(
            id="requirement_caller_material",
            kind="material",
            description=_MISSING_MATERIAL,
            goal_ids=goal_ids,
            status="missing",
            source=source,
        )
    ]


def resolve_input_requirements(
    requirements: list[InputRequirement],
    request_text: str,
    inputs: list[InputMaterial],
) -> RequirementResolution:
    """Resolve requirements without mutating caller-owned models or content."""

    resolved: list[InputRequirement] = []
    for requirement in requirements:
        references = _unique(_resolver(requirement.kind)(request_text, inputs))
        satisfied = len(references) >= requirement.minimum_items
        payload = requirement.model_dump(mode="python")
        payload.update(
            {
                "status": "satisfied" if satisfied else "missing",
                "satisfied_by": references if satisfied else [],
            }
        )
        resolved.append(InputRequirement.model_validate(payload))

    missing: list[str] = []
    for requirement in resolved:
        if (
            requirement.required
            and requirement.status == "missing"
            and requirement.description not in missing
        ):
            missing.append(requirement.description)
    return RequirementResolution(requirements=resolved, missing_information=missing)


def _resolver(
    kind: str,
) -> Callable[[str, list[InputMaterial]], list[str]]:
    return {
        "material": _material_references,
        "comparison_subject": _material_references,
        "calculation_operand": _number_references,
        "time_range": _time_references,
        "template": _template_references,
        "destination": _destination_references,
        "clarification": _clarification_references,
    }[kind]


def _usable_materials(inputs: list[InputMaterial]) -> list[tuple[int, InputMaterial]]:
    return [
        (index, item)
        for index, item in enumerate(inputs, start=1)
        if item.content.strip() or (item.uri is not None and item.uri.strip())
    ]


def _material_references(_text: str, inputs: list[InputMaterial]) -> list[str]:
    return [_material_reference(index, item) for index, item in _usable_materials(inputs)]


def _number_references(text: str, inputs: list[InputMaterial]) -> list[str]:
    references = [f"request:number:{index}" for index, _ in enumerate(_NUMBER.finditer(text), 1)]
    for material_index, item in _usable_materials(inputs):
        references.extend(
            f"{_material_reference(material_index, item)}#number:{number_index}"
            for number_index, _ in enumerate(_NUMBER.finditer(item.content), 1)
        )
    return references


def _time_references(text: str, inputs: list[InputMaterial]) -> list[str]:
    if _TIME_RANGE.search(text):
        return ["request:time_range"]
    return [
        f"{_material_reference(index, item)}#time_range"
        for index, item in _usable_materials(inputs)
        if _TIME_RANGE.search(item.content)
    ]


def _template_references(text: str, inputs: list[InputMaterial]) -> list[str]:
    references = ["request:template"] if _TEMPLATE_BODY.search(text) else []
    references.extend(
        _material_reference(index, item)
        for index, item in _usable_materials(inputs)
        if "模板" in item.name.casefold() or "template" in item.name.casefold()
    )
    return references


def _destination_references(text: str, inputs: list[InputMaterial]) -> list[str]:
    references = ["request:destination"] if _DESTINATION.search(text) else []
    references.extend(
        f"{_material_reference(index, item)}#destination"
        for index, item in enumerate(inputs, start=1)
        if item.uri
        and item.uri.strip()
        and any(token in item.name.casefold() for token in ("destination", "output", "目标", "输出"))
    )
    return references


def _clarification_references(_text: str, inputs: list[InputMaterial]) -> list[str]:
    return [
        _material_reference(index, item)
        for index, item in enumerate(inputs, start=1)
        if item.name == "user_input" and item.content.strip()
    ]


def _material_reference(index: int, material: InputMaterial) -> str:
    return f"material:{index}:{material.name}"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
