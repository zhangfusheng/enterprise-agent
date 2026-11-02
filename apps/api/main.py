from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.dependencies import container
from apps.api.routes import artifacts, events, models, runs, sessions, traces
from enterprise_agent.bootstrap.container import shutdown_container
from enterprise_agent.bootstrap.settings import Settings, load_local_env
from enterprise_agent.infrastructure.observability.logging import configure_logging


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    load_local_env()
    settings = Settings()
    configure_logging(settings.log_level, settings.log_file)
    yield
    shutdown_container()


app = FastAPI(title="enterprise-agent", version="0.1.0", lifespan=_lifespan)
app.include_router(runs.router)
app.include_router(models.router)
app.include_router(sessions.router)
app.include_router(events.router)
app.include_router(artifacts.router)
app.include_router(traces.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    instance = container()
    with instance.sessions() as session:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
    return {"status": "ready", "offline_mode": instance.settings.offline_mode}

