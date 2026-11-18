from typing import Any

from enterprise_agent.contracts.goals import Goal, GoalSpec, InputMaterial
from enterprise_agent.orchestration.routing.results import ParsedRequest, ParseProvenance


class TwoStageMultiGoalParser:
    """Require two append-only answers while keeping two goal identities stable."""

    def __init__(self) -> None:
        self.seen_inputs: list[list[dict[str, Any]]] = []

    def parse(self, text, inputs=None, constraints=None) -> GoalSpec:
        return self.parse_result(text, inputs, constraints).spec

    def parse_result(self, text, inputs=None, constraints=None) -> ParsedRequest:
        del text, constraints
        raw_inputs = list(inputs or [])
        self.seen_inputs.append(raw_inputs)
        materials = [InputMaterial.model_validate(item) for item in raw_inputs]
        missing = []
        if len(materials) == 0:
            missing = ["请提供第一段资料。"]
        elif len(materials) == 1:
            missing = ["请提供第二段资料。"]
        return ParsedRequest(
            spec=GoalSpec(
                goals=[
                    Goal(
                        id="goal_prepare",
                        description="整理用户资料",
                        required_capabilities=["analysis"],
                    ),
                    Goal(
                        id="goal_calculate",
                        description="计算资料中的指标",
                        required_capabilities=["calculate"],
                    ),
                ],
                inputs=materials,
                missing_information=missing,
            ),
            provenance=ParseProvenance(parser="rules"),
        )
