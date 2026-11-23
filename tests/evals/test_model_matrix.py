from datetime import UTC, datetime
from pathlib import Path

from evals.model_matrix.cost import cost_for, deepseek_tier, load_prices
from evals.model_matrix.grade import grade_case, load_dataset, percentile
from evals.model_matrix.runner import build_container, run_matrix

from enterprise_agent.infrastructure.observability.metrics import quote_cost
from enterprise_agent.orchestration.routing.parser import GoalParser
from enterprise_agent.orchestration.routing.router import Router


def test_dataset_matches_the_rule_router():
    dataset = load_dataset()
    parser = GoalParser()
    router = Router()

    assert len(dataset["cases"]) == 8
    for case in dataset["cases"]:
        decision = router.decide(parser.parse(case["input"], case.get("inputs") or []))
        assert decision.route == case["expected_route"]
        if case["expected_role"]:
            assert decision.selected_role == case["expected_role"]


def test_quote_cost_matches_the_eval_price_table():
    prices = load_prices()
    moment = datetime(2026, 11, 20, 3, 30, tzinfo=UTC)

    assert deepseek_tier(moment, prices["profiles"]["deepseek-default"]) == "peak"
    priced = cost_for("deepseek-default", prices, 1_000_000, 2_000_000, moment)
    assert priced["cost_native"] == quote_cost(
        {"input_usd_per_million": 0.30, "output_usd_per_million": 1.20},
        1_000_000,
        2_000_000,
        offline=False,
    )
    assert priced["cost_usd"] == 2.7

    qwen = cost_for("qwen-default", prices, 1_000_000, 1_000_000, moment)
    assert qwen["currency"] == "CNY"
    assert qwen["cost_native"] == 48
    assert abs(qwen["cost_usd"] - 48 / 6.7131) < 1e-9
    assert quote_cost({}, 10, 5, offline=False) is None
    assert quote_cost({}, 10, 5, offline=True) == 0.0


def test_weekday_outside_the_deepseek_windows_is_off_peak():
    prices = load_prices()
    moment = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert deepseek_tier(moment, prices["profiles"]["deepseek-default"]) == "off_peak"


def test_percentile_interpolates_the_sample():
    assert percentile([10, 20, 30, 40, 50], 0.50) == 30
    assert percentile([10, 20, 30, 40, 50], 0.95) == 48
    assert percentile([], 0.95) is None


def test_grader_rejects_a_self_reported_success_that_misses_the_rubric():
    case = load_dataset()["cases"][0]
    graded = grade_case(
        case,
        {
            "route": "direct",
            "status": "SUCCEEDED",
            "summary": "已完成。",
            "tool_calls": [],
            "sources": [],
            "tasks": [],
        },
    )
    assert graded["passed"] is False
    assert graded["failure_class"] == "answer_rubric"


def test_matching_calculate_arguments_still_fail_when_the_tool_rejects_them():
    case = next(item for item in load_dataset()["cases"] if item["case_id"] == "S02")
    graded = grade_case(
        case,
        {
            "route": "single",
            "selected_role": "analysis",
            "status": "PARTIAL",
            "summary": "已调用计算。",
            "tool_calls": [
                {
                    "name": "calculate",
                    "arguments": {"operation": "mean", "values": [10, 20, 30]},
                    "rejected": True,
                }
            ],
            "sources": [],
            "tasks": [],
            "calculation_value": None,
        },
    )
    assert graded["checks"]["tool_argument"] is True
    assert graded["checks"]["calculation"] is False
    assert graded["passed"] is False
    assert graded["failure_class"] == "calculation"


def test_grader_accepts_a_fixture_research_result():
    case = next(item for item in load_dataset()["cases"] if item["case_id"] == "S01")
    graded = grade_case(
        case,
        {
            "route": "single",
            "selected_role": "research",
            "status": "SUCCEEDED",
            "summary": "收集到 1 条来源。",
            "tool_calls": [{"name": "web_search", "arguments": {"query": "来源"}}],
            "sources": ["https://fixture.invalid/enterprise-agent"],
            "tasks": [{"task_id": "task_worker", "agent_role": "research", "depends_on": []}],
        },
    )
    assert graded["passed"] is True
    assert graded["failure_class"] is None


def test_live_container_keeps_models_online_and_search_on_the_fixture(tmp_path: Path):
    container = build_container(tmp_path, live_models=True)
    try:
        assert container.settings.offline_mode is False
        assert "enterprise-agent.db" not in container.settings.database_url
        result = container.tools.get("web_search").function("幂等性", 1)
        assert result[0]["url"].startswith("https://fixture.invalid/")
    finally:
        container.close()


def test_offline_matrix_records_route_latency_and_zero_cost(tmp_path: Path):
    output = tmp_path / "report.json"
    report = run_matrix(
        live_models=False,
        profiles=("offline-default",),
        modes=("auto", "forced"),
        repeats=1,
        output=output,
    )

    assert output.is_file()
    assert report["config"]["tool_backend"] == "fixture"
    assert report["config"]["database"] == "temporary sqlite"
    assert len(report["records"]) == 16
    researched = next(
        record
        for record in report["records"]
        if record["case_id"] == "S01" and record["mode"] == "forced"
    )
    assert researched["router_correct"] is True
    assert researched["task_success"] is True
    assert researched["e2e_ms"] >= 0
    assert researched["cost_usd"] == 0.0
    assert any(url.startswith("https://fixture.invalid/") for url in researched["sources"])
    direct = next(record for record in report["records"] if record["case_id"] == "D01")
    assert direct["router_correct"] is True
    assert direct["checks"]["tool_selection"] is True
    assert direct["judge"] == "rubric"
