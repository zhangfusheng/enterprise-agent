from pydantic import BaseModel, ValidationError

from enterprise_agent.orchestration.routing.errors import (
    CandidatePolicyError,
    CandidateSchemaError,
    CandidateSemanticError,
    CompletionTransportError,
    GoalParseError,
    GoalSpecInvariantError,
    classify_completion_error,
)


class _RequiredValue(BaseModel):
    value: int


def _validation_error() -> ValidationError:
    try:
        _RequiredValue.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("fixture did not raise ValidationError")


def test_error_taxonomy_exposes_stable_categories_and_retry_policy():
    assert issubclass(CompletionTransportError, GoalParseError)
    assert issubclass(CandidateSchemaError, GoalParseError)
    assert issubclass(CandidateSemanticError, GoalParseError)
    assert issubclass(CandidatePolicyError, GoalParseError)
    assert issubclass(GoalSpecInvariantError, GoalParseError)

    assert CompletionTransportError.category == "transport"
    assert CandidateSchemaError.category == "schema"
    assert CandidateSemanticError.category == "semantic"
    assert CandidatePolicyError.category == "policy"
    assert GoalSpecInvariantError.category == "invariant"

    assert CandidateSchemaError.retryable is True
    assert CandidateSemanticError.retryable is True
    assert CompletionTransportError.retryable is False
    assert CandidatePolicyError.retryable is False
    assert GoalSpecInvariantError.retryable is False


def test_completion_classifier_normalizes_structured_output_failures():
    assert isinstance(classify_completion_error(_validation_error()), CandidateSchemaError)
    assert isinstance(classify_completion_error(TypeError("invalid result")), CandidateSchemaError)

    structured_error = type("StructuredOutputError", (Exception,), {})()
    assert isinstance(classify_completion_error(structured_error), CandidateSchemaError)


def test_completion_classifier_normalizes_provider_runtime_failures():
    assert isinstance(classify_completion_error(TimeoutError("timed out")), CompletionTransportError)
    assert isinstance(
        classify_completion_error(ConnectionError("connection failed")),
        CompletionTransportError,
    )
    assert isinstance(classify_completion_error(RuntimeError("provider failed")), CompletionTransportError)


def test_completion_classifier_preserves_explicit_domain_errors():
    errors = [
        CompletionTransportError(),
        CandidateSchemaError(),
        CandidateSemanticError(),
        CandidatePolicyError(),
        GoalSpecInvariantError(),
    ]

    for error in errors:
        assert classify_completion_error(error) is error


def test_default_messages_do_not_include_source_exception_details():
    source = RuntimeError("secret request content")
    classified = classify_completion_error(source)

    assert str(classified) == "goal completion failed"
    assert "secret request content" not in str(classified)
