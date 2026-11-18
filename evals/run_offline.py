from pathlib import Path

import yaml

from enterprise_agent.contracts.goals import InputMaterial, InputRequirement
from enterprise_agent.orchestration.routing.input_requirements import (
    resolve_input_requirements,
)
from enterprise_agent.orchestration.routing.parser import GoalParser
from enterprise_agent.orchestration.routing.router import Router


def main() -> None:
    root = Path(__file__).parent / "datasets"
    route_dataset = yaml.safe_load(
        (root / "offline.yaml").read_text(encoding="utf-8")
    )
    route_scores = []
    for item in route_dataset:
        route = Router().decide(GoalParser().parse(item["input"])).route
        route_scores.append(route == item["expected_route"])
        print(f"{item['name']}: actual={route} expected={item['expected_route']} {'PASS' if route == item['expected_route'] else 'FAIL'}")
    requirement_dataset = yaml.safe_load(
        (root / "requirements.yaml").read_text(encoding="utf-8")
    )
    requirement_scores = []
    for item in requirement_dataset:
        requirement = InputRequirement(
            id="requirement_eval",
            kind=item["kind"],
            description="eval requirement",
            goal_ids=["goal_main"],
            minimum_items=item.get("minimum_items", 1),
            status="missing",
            source="server",
        )
        resolution = resolve_input_requirements(
            [requirement],
            item["request"],
            [InputMaterial.model_validate(value) for value in item["inputs"]],
        )
        status = resolution.requirements[0].status
        passed = status == item["expected_status"]
        requirement_scores.append(passed)
        print(
            f"{item['id']}: actual={status} expected={item['expected_status']} "
            f"{'PASS' if passed else 'FAIL'}"
        )
    print(f"route_accuracy={sum(route_scores) / len(route_scores):.3f}")
    print(
        "requirement_accuracy="
        f"{sum(requirement_scores) / len(requirement_scores):.3f}"
    )
    if not all((*route_scores, *requirement_scores)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

