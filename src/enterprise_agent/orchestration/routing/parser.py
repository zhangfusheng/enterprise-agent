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
    ("calculate", re.compile(r"计算|均值|平均|增长率|差值|求和", re.IGNORECASE)),
)
_ANALYZE_MATERIAL_PATTERN = re.compile(
    r"(?:分析|统计|检查|识别).{0,8}(?:CSV|数据|日志|附件|资料|文件)|"
    r"(?:CSV|数据|日志|附件|资料|文件).{0,8}(?:分析|统计|异常|趋势)",
    re.IGNORECASE,
)
_ANALYZE_EXTERNAL_PATTERN = re.compile(r"分析|比较|评估|总结|报告", re.IGNORECASE)
_REPORT_PATTERN = re.compile(r"报告|markdown|JSON|交付物|文档|文件", re.IGNORECASE)
_ARTIFACT_PATTERN = re.compile(r"保存|文件|下载|写入", re.IGNORECASE)
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
            deliverables=[_deliverable(cleaned)]
            if _REPORT_PATTERN.search(cleaned)
            else [],
            acceptance_criteria=[_CRITERION.model_copy()],
            missing_information=material_gap(cleaned, materials),
        )


_NEGATION = re.compile(r"(?:不要|别|无需|不需要|禁止).{0,6}$")


def _capabilities(text: str) -> list[str]:
    found: list[str] = []
    for name, pattern in _CAPABILITY_RULES:
        if any(not _NEGATION.search(text[max(0, match.start() - 8) : match.start()]) for match in pattern.finditer(text)):
            found.append(name)
    if _ANALYZE_MATERIAL_PATTERN.search(text):
        found.append("analysis")
    # Synthesizing externally acquired evidence is worker analysis; the same
    # verbs in a self-contained knowledge question remain ordinary reasoning.
    if "web_search" in found and _ANALYZE_EXTERNAL_PATTERN.search(text):
        found.append("analysis")
    return sorted(found)


def _deliverable(text: str) -> Deliverable:
    output_format = "json" if re.search(r"JSON", text, re.IGNORECASE) else "markdown"
    return _REPORT.model_copy(
        update={
            "format": output_format,
            "delivery_mode": "artifact" if _ARTIFACT_PATTERN.search(text) else "inline",
        }
    )


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
