import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from enterprise_agent.contracts.goals import (
    AcceptanceCriterion,
    ConstraintSet,
    Deliverable,
    Goal,
    GoalSpec,
    InputMaterial,
)

_MIN_TEXT_LENGTH = 3
_MISSING_GOAL = "请提供至少一个可执行目标。"
_MISSING_MATERIAL = "请提供要分析的资料或文件。"

_CAPABILITY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("web_search", re.compile(r"https?://|网页|搜索|最新|实时|来源", re.IGNORECASE)),
    ("analysis", re.compile(r"报告|比较|分析|研究|总结|评估", re.IGNORECASE)),
    ("calculate", re.compile(r"计算|均值|平均|增长率|差值|求和", re.IGNORECASE)),
    ("workspace_read", re.compile(r"保存|文件|资料|文档", re.IGNORECASE)),
)
_REPORT_PATTERN = re.compile(r"报告|markdown|交付物|保存", re.IGNORECASE)
_MATERIAL_PATTERN = re.compile(r"我的资料|上传的|附件|这份文件|给定资料", re.IGNORECASE)

_REPORT = Deliverable(
    id="deliverable_report",
    description="Markdown research/analysis report",
    format="markdown",
)
_CRITERION = AcceptanceCriterion(id="criterion_goal", description="完成用户目标并返回可核验结果")


@dataclass(frozen=True, slots=True)
class ParserTrace:
    """Which parser produced the spec, and which model prompt was used."""

    name: str = "rules"
    model_version: str | None = None
    prompt_version: str | None = None


_TRACE: ContextVar[ParserTrace | None] = ContextVar("goal_parser_trace", default=None)


class RequestParser(Protocol):
    def parse(
        self,
        text: str,
        inputs: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> GoalSpec: ...


def parser_trace() -> ParserTrace:
    return _TRACE.get() or ParserTrace()


def set_parser_trace(trace: ParserTrace) -> None:
    _TRACE.set(trace)


class GoalParser:
    """Parse a request into explicit data without treating model confidence as probability."""

    def parse(
        self,
        text: str,
        inputs: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> GoalSpec:
        set_parser_trace(ParserTrace())
        cleaned = " ".join(text.split())
        if len(cleaned) < _MIN_TEXT_LENGTH:
            return GoalSpec(missing_information=[_MISSING_GOAL])

        raw_constraints = constraints or {}
        materials = [InputMaterial.model_validate(item) for item in (inputs or [])]
        return GoalSpec(
            goals=[
                Goal(
                    id="goal_main",
                    description=cleaned,
                    required_capabilities=_capabilities(cleaned),
                )
            ],
            inputs=materials,
            constraints=request_constraints(raw_constraints),
            deliverables=[_REPORT.model_copy()] if _REPORT_PATTERN.search(cleaned) else [],
            acceptance_criteria=[_CRITERION.model_copy()],
            missing_information=material_gap(cleaned, materials),
        )


_NEGATION = re.compile(r"(?:不要|别|无需|不需要|禁止).{0,6}$")


def _capabilities(text: str) -> list[str]:
    found: list[str] = []
    for name, pattern in _CAPABILITY_RULES:
        if any(not _NEGATION.search(text[max(0, match.start() - 8) : match.start()]) for match in pattern.finditer(text)):
            found.append(name)
    return sorted(found)


def request_constraints(raw: dict[str, Any] | None) -> ConstraintSet:
    """Constraints come from the caller. A model candidate cannot supply them."""
    source = raw or {}
    values = list(source.get("values", []))
    if source.get("require_sources"):
        values.append("require_sources")
    return ConstraintSet.model_validate({**source, "values": values})


def material_gap(text: str, materials: list[InputMaterial]) -> list[str]:
    if _MATERIAL_PATTERN.search(text) and not materials:
        return [_MISSING_MATERIAL]
    return []
