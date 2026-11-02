import pytest
from pydantic import ValidationError

from enterprise_agent.contracts.goals import Goal, GoalSpec
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.models.config import ModelConfig
from enterprise_agent.models.validation import ModelConfigError, validate_model_config


def test_goal_ids_are_unique():
    with pytest.raises(ValidationError):
        GoalSpec(goals=[Goal(id="goal_one", description="one"), Goal(id="goal_one", description="two")])


def test_agent_result_is_structured():
    result = AgentResult(status=ResultStatus.SUCCESS, summary="ok", sources=[{"url": "https://example.com"}])
    assert result.status == ResultStatus.SUCCESS


def test_invalid_model_binding_is_rejected():
    config = ModelConfig.model_validate({"providers": [{"name": "offline", "protocol": "fake"}], "profiles": [{"name": "p", "provider": "offline", "model_name": "x", "capabilities": []}], "bindings": [{"role": "router", "model_profile": "missing"}]})
    with pytest.raises(ModelConfigError):
        validate_model_config(config)

