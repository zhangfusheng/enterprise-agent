from concurrent.futures import ThreadPoolExecutor

import pytest

from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.governance.authorization import (
    AuthorizationError,
    AuthorizationPolicy,
    RolePolicy,
)
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.governance.delegation import DelegationGuard
from enterprise_agent.runtime.budgets import BudgetExceeded, BudgetLedger


def test_child_scope_converges():
    policy = AuthorizationPolicy([RolePolicy("research", frozenset({"web_search"}), frozenset({"web_search"}), frozenset({"analysis"})), RolePolicy("analysis", frozenset({"analysis"}), frozenset({"calculate"}), frozenset())])
    parent = ExecutionContext(run_id="r", tenant_id="t", user_id="u", agent_id="p", workspace_root=".", allowed_roles={"research", "analysis"}, allowed_tools={"web_search", "calculate"}, metadata={"role": "research"})
    child = policy.issue_delegated_context(parent, "analysis", "c", "d")
    assert child.allowed_tools == {"calculate"}
    with pytest.raises(AuthorizationError):
        policy.issue_delegated_context(parent, "unknown", "c", "d")


def test_budget_consumption_survives_snapshot():
    ledger = BudgetLedger(BudgetLimits(model_calls=1, tool_calls=1, agents=1, steps=1, runtime_seconds=100), {"tool_calls": 1})
    with pytest.raises(BudgetExceeded):
        ledger.consume("tool_calls")


def test_budget_consumption_is_atomic_across_parallel_tool_calls():
    ledger = BudgetLedger(
        BudgetLimits(model_calls=1, tool_calls=3, agents=1, steps=1, runtime_seconds=30)
    )

    def consume() -> bool:
        try:
            ledger.consume("tool_calls")
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: consume(), range(8)))

    assert sum(results) == 3
    assert ledger.snapshot()["tool_calls"] == 3


def test_depth_guard():
    guard = DelegationGuard(AuthorizationPolicy([]), max_depth=1, max_agents=2)
    parent = ExecutionContext(run_id="r", tenant_id="t", user_id="u", agent_id="p", workspace_root=".", depth=1, allowed_roles={"analysis"})
    with pytest.raises(AuthorizationError):
        guard.check(parent, "analysis", 1)

