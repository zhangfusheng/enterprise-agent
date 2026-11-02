from enterprise_agent.contracts.runs import RunStatus


class InvalidTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {RunStatus.WAITING_INPUT, RunStatus.SUCCEEDED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.WAITING_INPUT: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.SUCCEEDED: set(),
    RunStatus.PARTIAL: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def assert_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"invalid run transition {current} -> {target}")

