import re
from typing import Any, Protocol

from enterprise_agent.contracts.goals import (
    AcceptanceCriterion,
    ConstraintSet,
    Deliverable,
    Goal,
    GoalSpec,
    InputMaterial,
)
from enterprise_agent.orchestration.routing.input_requirements import (
    infer_caller_material_requirements,
    resolve_input_requirements,
)
from enterprise_agent.orchestration.routing.results import ParsedRequest, ParseProvenance

_MIN_TEXT_LENGTH = 3
_MISSING_GOAL = "请提供至少一个可执行目标。"

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

_REPORT = Deliverable(
    id="deliverable_report",
    description="Markdown research/analysis report",
    format="markdown",
)
_CRITERION = AcceptanceCriterion(id="criterion_goal", description="完成用户目标并返回可核验结果")


class RequestParser(Protocol):
    def parse(
        self,
        text: str,
        inputs: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> GoalSpec: ...


class GoalParser:
    """Parse a request into explicit data without treating model confidence as probability."""

    def parse(
        self,
        text: str,
        inputs: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> GoalSpec:
        return self.parse_result(text, inputs, constraints).spec

    def parse_result(
        self,
        text: str,
        inputs: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> ParsedRequest:
        cleaned = " ".join(text.split())
        if len(cleaned) < _MIN_TEXT_LENGTH:
            spec = GoalSpec(missing_information=[_MISSING_GOAL])
            return ParsedRequest(spec=spec, provenance=ParseProvenance(parser="rules"))

        raw_constraints = constraints or {}
        materials = [InputMaterial.model_validate(item) for item in (inputs or [])]
        goals = [
            Goal(
                id="goal_main",
                description=cleaned,
                required_capabilities=_capabilities(cleaned),
            )
        ]
        resolution = resolve_input_requirements(
            infer_caller_material_requirements(
                cleaned,
                [item.id for item in goals],
                source="rules",
            ),
            cleaned,
            materials,
        )
        spec = GoalSpec(
            goals=goals,
            inputs=materials,
            constraints=request_constraints(raw_constraints),
            deliverables=[_deliverable(cleaned)] if _REPORT_PATTERN.search(cleaned) else [],
            acceptance_criteria=[_CRITERION.model_copy()],
            input_requirements=resolution.requirements,
            missing_information=resolution.missing_information,
        )
        return ParsedRequest(spec=spec, provenance=ParseProvenance(parser="rules"))


_NEGATION = re.compile(r"(?:不要|别|无需|不需要|禁止).{0,6}$")


def _capabilities(text: str) -> list[str]:
    found: list[str] = []
    for name, pattern in _CAPABILITY_RULES:
        if any(
            not _NEGATION.search(text[max(0, match.start() - 8) : match.start()])
            for match in pattern.finditer(text)
        ):
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
