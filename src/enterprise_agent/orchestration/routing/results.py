"""Explicit goal parsing results and compatibility adaptation."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from enterprise_agent.contracts.goals import GoalSpec

COMPILER_VERSION = "goal-compiler-v1"


class ParseProvenance(BaseModel):
    parser: Literal["rules", "llm", "fallback", "legacy"]
    model_version: str | None = None
    prompt_version: str | None = None
    compiler_version: str = COMPILER_VERSION
    attempts: int = 0
    fallback_reason: Literal["transport", "schema", "semantic", "policy", "invariant"] | None = None


class ParsedRequest(BaseModel):
    spec: GoalSpec
    provenance: ParseProvenance


@runtime_checkable
class ResultRequestParser(Protocol):
    def parse_result(
        self,
        text: str,
        inputs: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> ParsedRequest: ...


class LegacyRequestParser(Protocol):
    def parse(
        self,
        text: str,
        inputs: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> GoalSpec: ...


def parse_request(
    parser: LegacyRequestParser,
    text: str,
    inputs: list[dict[str, Any]] | None = None,
    constraints: dict[str, Any] | None = None,
) -> ParsedRequest:
    """Return one atomic parse result while supporting legacy parser doubles."""

    if isinstance(parser, ResultRequestParser):
        return parser.parse_result(text, inputs, constraints)
    return ParsedRequest(
        spec=parser.parse(text, inputs, constraints),
        provenance=ParseProvenance(
            parser="legacy",
            compiler_version="legacy",
        ),
    )
