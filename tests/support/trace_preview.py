"""Isolated, offline API fixture for the trace console's browser tests.

Run with uvicorn tests.support.trace_preview:create_app --factory.
Never opens the project's configured database or calls a model provider.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from apps.api.dependencies import container, identity
from apps.api.routes.sessions import router as sessions_router
from apps.api.routes.traces import router
from fastapi import FastAPI, Header

from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate
from enterprise_agent.infrastructure.database.models import Base, RunRow
from enterprise_agent.infrastructure.database.session import build_engine, session_factory
from enterprise_agent.infrastructure.observability.tracing import TraceSpan
from enterprise_agent.infrastructure.repositories.runs import RunRepository
from enterprise_agent.infrastructure.repositories.traces import TraceRepository


def create_app() -> FastAPI:
    directory = TemporaryDirectory(prefix="trace-ui-e2e-")
    engine = build_engine(f"sqlite:///{directory.name}/fixture.sqlite")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    instance = SimpleNamespace(runs=RunRepository(sessions), traces=TraceRepository(sessions))
    now = datetime.now(UTC) - timedelta(minutes=5)
    for index in range(8):
        run_id = instance.runs.create(
            "local-tenant", "browser-test", RunCreate(input="测试研究报告")
        )
        with sessions() as session:
            row = session.get(RunRow, run_id)
            assert row is not None
            row.status = "SUCCEEDED" if index != 2 else "FAILED"
            row.route = "multi" if index < 3 else "direct"
            row.summary = "测试执行已完成"
            session.commit()
        start = now - timedelta(minutes=index * 12)
        root_id = f"trace-{index}"
        specs = [
            (root_id, None, "orchestration", "chain", 0, 12480, None),
            (f"parse-{index}", root_id, "parse_route", "chain", 20, 540, None),
            (f"plan-{index}", root_id, "plan", "chain", 560, 1740, None),
            (f"execute-{index}", root_id, "execute", "chain", 1800, 10900, None),
            (f"research-{index}", f"execute-{index}", "research", "chain", 1850, 7400, None),
            (f"llm-{index}", f"research-{index}", "research_model", "llm", 7200, 7201, None),
            (
                f"tool-{index}",
                f"research-{index}",
                "web_search",
                "tool",
                7210,
                7211,
                "Search provider timed out" if index in {0, 2} else None,
            ),
            (f"analysis-{index}", f"execute-{index}", "analysis", "chain", 7500, 10750, None),
            (f"aggregate-{index}", root_id, "aggregate", "chain", 10950, 11800, None),
            (f"accept-{index}", root_id, "accept", "chain", 11850, 12460, None),
        ]
        order: dict[str, str] = {}
        for number, (span_id, parent, name, kind, begin, end, error) in enumerate(specs):
            order[span_id] = f"{order[parent]}." if parent else ""
            order[span_id] += f"{number:04d}"
            replay = kind in {"llm", "tool"}
            instance.traces.record(
                TraceSpan(
                    id=span_id,
                    trace_id=root_id,
                    parent_run_id=parent,
                    name=name,
                    run_type=kind,
                    start_time=start + timedelta(milliseconds=begin),
                    end_time=start + timedelta(milliseconds=end),
                    dotted_order=order[span_id],
                    run_id=run_id,
                    tenant_id="local-tenant",
                    error=error,
                    tags=["offline", "research"] if parent is None else [],
                    inputs={
                        "query": "Agent observability",
                        "constraints": {"language": "zh-CN", "max_sources": 5},
                    },
                    outputs={"summary": "测试执行已完成", "source_count": 5},
                    extra={
                        "metadata": {
                            "timing_source": "reconstructed" if replay else "measured",
                            **(
                                {
                                    "usage": {
                                        "input_tokens": 1280,
                                        "output_tokens": 384,
                                        "total_tokens": 1664,
                                    }
                                }
                                if kind == "llm"
                                else {}
                            ),
                        }
                    },
                )
            )

    @asynccontextmanager
    async def lifespan(_app):
        yield
        engine.dispose()
        directory.cleanup()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.include_router(sessions_router)
    app.dependency_overrides[container] = lambda: instance

    def test_identity(x_tenant_id: str = Header("local-tenant")):
        return IdentityContext(tenant_id=x_tenant_id, user_id="browser-test", is_authenticated=True)

    app.dependency_overrides[identity] = test_identity
    return app
