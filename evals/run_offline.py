from pathlib import Path

import yaml

from enterprise_agent.orchestration.routing.parser import GoalParser
from enterprise_agent.orchestration.routing.router import Router


def main() -> None:
    dataset = yaml.safe_load((Path(__file__).parent / "datasets/offline.yaml").read_text(encoding="utf-8"))
    scores = []
    for item in dataset:
        route = Router().decide(GoalParser().parse(item["input"])).route
        scores.append(route == item["expected_route"])
        print(f"{item['name']}: actual={route} expected={item['expected_route']} {'PASS' if route == item['expected_route'] else 'FAIL'}")
    print(f"route_accuracy={sum(scores) / len(scores):.3f}")
    if not all(scores):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

