from enterprise_agent.contracts.traces import TraceView
from enterprise_agent.infrastructure.repositories.runs import RunRepository
from enterprise_agent.infrastructure.repositories.traces import TraceRepository


def query_trace(
    runs: RunRepository, traces: TraceRepository, tenant_id: str, run_id: str
) -> TraceView:
    run = runs.get_run_view(tenant_id, run_id)
    view = traces.get(tenant_id, run_id)
    view.run_status = run.status
    if run.status not in {"PENDING", "RUNNING"}:
        # A stopped business run cannot keep an unclosed span live forever.
        pending = [*view.runs, *view.roots]
        while pending:
            span = pending.pop()
            span.incomplete = span.end_time is None
            pending.extend(span.children)
    return view
