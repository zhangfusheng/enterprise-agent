import yaml

from enterprise_agent.orchestration.routing.parser import GoalParser
from enterprise_agent.orchestration.routing.router import Router


def test_declared_examples_have_expected_routes():
    for name in ("direct", "single", "multi"):
        with open(f"examples/{name}.yaml", encoding="utf-8") as file:
            example = yaml.safe_load(file)
        assert str(Router().decide(GoalParser().parse(example["input"])).route) == example["expected_route"]

