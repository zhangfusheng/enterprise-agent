from copy import deepcopy

import pytest

from enterprise_agent.contracts.goals import InputMaterial, InputRequirement
from enterprise_agent.orchestration.routing.input_requirements import (
    resolve_input_requirements,
)


def _requirement(kind: str, **updates) -> InputRequirement:
    payload = {
        "id": f"requirement_{kind}",
        "kind": kind,
        "description": f"missing {kind}",
        "goal_ids": ["goal_main"],
        "status": "missing",
        "source": "server",
    }
    payload.update(updates)
    return InputRequirement.model_validate(payload)


def _resolve(
    requirement: InputRequirement,
    text: str = "",
    inputs: list[InputMaterial] | None = None,
):
    return resolve_input_requirements([requirement], text, inputs or [])


def test_material_accepts_content_or_uri_and_rejects_empty_material():
    requirement = _requirement("material")
    assert _resolve(requirement).requirements[0].status == "missing"
    assert _resolve(requirement, inputs=[InputMaterial(name="empty")]).requirements[0].status == "missing"

    content = _resolve(
        requirement,
        inputs=[InputMaterial(name="notes", content="body")],
    ).requirements[0]
    uri = _resolve(
        requirement,
        inputs=[InputMaterial(name="file", uri="s3://bucket/file")],
    ).requirements[0]
    assert content.satisfied_by == ["material:1:notes"]
    assert uri.satisfied_by == ["material:1:file"]


def test_comparison_requires_distinct_materials():
    requirement = _requirement("comparison_subject", minimum_items=2)
    one = [InputMaterial(name="a", content="A")]
    two = [*one, InputMaterial(name="b", content="B")]

    assert _resolve(requirement, inputs=one).requirements[0].status == "missing"
    assert _resolve(requirement, inputs=two).requirements[0].satisfied_by == [
        "material:1:a",
        "material:2:b",
    ]


@pytest.mark.parametrize("text", ["10 -2 3.5 20%", "数值为 10 和 20"])
def test_calculation_operands_are_positionally_stable(text):
    requirement = _requirement("calculation_operand", minimum_items=2)
    resolved = _resolve(requirement, text).requirements[0]

    assert resolved.status == "satisfied"
    assert resolved.satisfied_by[:2] == ["request:number:1", "request:number:2"]


def test_calculation_operands_can_come_from_one_material():
    requirement = _requirement("calculation_operand", minimum_items=2)
    resolved = _resolve(
        requirement,
        inputs=[InputMaterial(name="data", content="baseline=10 current=12.5")],
    ).requirements[0]

    assert resolved.satisfied_by == [
        "material:1:data#number:1",
        "material:1:data#number:2",
    ]


@pytest.mark.parametrize("text", ["2026-11-14", "2026 年 11 月", "本周", "最近 30 天"])
def test_time_range_requires_an_explicit_supported_expression(text):
    requirement = _requirement("time_range")
    assert _resolve(requirement, text).requirements[0].status == "satisfied"
    assert _resolve(requirement, "请分析近期趋势").requirements[0].status == "missing"


def test_template_requires_an_explicit_template_marker_or_named_material():
    requirement = _requirement("template")
    assert _resolve(requirement, "模板：# 标题").requirements[0].satisfied_by == ["request:template"]
    assert _resolve(
        requirement,
        inputs=[InputMaterial(name="report-template.md", content="# title")],
    ).requirements[0].status == "satisfied"
    assert _resolve(
        requirement,
        inputs=[InputMaterial(name="notes.md", content="# title")],
    ).requirements[0].status == "missing"


@pytest.mark.parametrize("text", ["保存到 reports/out.md", "输出到 result.json", " /tmp/report.pdf "])
def test_destination_requires_an_explicit_location(text):
    requirement = _requirement("destination")
    assert _resolve(requirement, text).requirements[0].satisfied_by == ["request:destination"]


def test_clarification_requires_non_empty_user_input():
    requirement = _requirement("clarification")
    unrelated = [InputMaterial(name="notes", content="answer")]
    answer = [InputMaterial(name="user_input", content="answer")]
    assert _resolve(requirement, inputs=unrelated).requirements[0].status == "missing"
    assert _resolve(requirement, inputs=answer).requirements[0].status == "satisfied"


def test_optional_missing_requirement_does_not_block_or_render():
    optional = _requirement("material", required=False)
    resolution = _resolve(optional)

    assert resolution.requirements[0].status == "missing"
    assert resolution.missing_information == []


def test_missing_information_is_ordered_and_deduplicated():
    requirements = [
        _requirement("material", description="请补充资料"),
        _requirement(
            "template",
            id="requirement_template",
            description="请补充资料",
        ),
        _requirement("time_range", description="请补充时间范围"),
    ]
    resolution = resolve_input_requirements(requirements, "", [])

    assert resolution.missing_information == ["请补充资料", "请补充时间范围"]


def test_resolution_is_immutable_and_idempotent():
    requirements = [_requirement("material")]
    inputs = [InputMaterial(name="notes", content="private body")]
    original_requirements = deepcopy(requirements)
    original_inputs = deepcopy(inputs)

    first = resolve_input_requirements(requirements, "", inputs)
    second = resolve_input_requirements(first.requirements, "", inputs)

    assert second == first
    assert requirements == original_requirements
    assert inputs == original_inputs
    assert "private body" not in repr(first)
