"""Stable error taxonomy for goal parsing and compilation.

The model completion boundary may normalize provider-specific exceptions with
``classify_completion_error``. Candidate and compiler code must raise the
semantic, policy, and invariant errors explicitly; classifying those failures
from message text would make control flow provider-dependent and brittle.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import ValidationError

ParseErrorCategory = Literal["transport", "schema", "semantic", "policy", "invariant"]

_STRUCTURED_OUTPUT_ERROR_NAMES = frozenset(
    {"OutputParserException", "StructuredOutputError"}
)


class GoalParseError(Exception):
    """Base class for safely reportable goal parsing failures."""

    category: ClassVar[ParseErrorCategory]
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "goal parsing failed"

    def __init__(self, message: str | None = None) -> None:
        # Callers should use bounded, non-user-derived detail only. The default
        # messages are deliberately safe for logs and persisted diagnostics.
        super().__init__(message or self.default_message)


class CompletionTransportError(GoalParseError):
    """The model call did not produce a candidate."""

    category = "transport"
    default_message = "goal completion failed"


class CandidateSchemaError(GoalParseError):
    """The provider output could not be validated as a candidate."""

    category = "schema"
    retryable = True
    default_message = "goal candidate schema validation failed"


class CandidateSemanticError(GoalParseError):
    """A structured candidate contradicts request-level intent invariants."""

    category = "semantic"
    retryable = True
    default_message = "goal candidate semantic validation failed"


class CandidatePolicyError(GoalParseError):
    """A candidate requests data or authority owned by the server."""

    category = "policy"
    default_message = "goal candidate violates server policy"


class GoalSpecInvariantError(GoalParseError):
    """Server compilation produced an internally inconsistent GoalSpec."""

    category = "invariant"
    default_message = "compiled goal specification violates an invariant"


def classify_completion_error(exc: Exception) -> GoalParseError:
    """Normalize an exception raised at the model completion boundary.

    Existing domain errors retain their identity. Pydantic, adapter type, and
    known structured-output parser failures are schema errors. Every other
    exception at this boundary means no usable candidate was produced and is
    classified as a completion transport/runtime failure.
    """

    if isinstance(exc, GoalParseError):
        return exc
    if isinstance(exc, (ValidationError, TypeError)) or type(exc).__name__ in (
        _STRUCTURED_OUTPUT_ERROR_NAMES
    ):
        return CandidateSchemaError()
    return CompletionTransportError()
