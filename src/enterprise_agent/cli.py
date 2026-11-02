import typer

from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.bootstrap.container import get_container
from enterprise_agent.bootstrap.settings import Settings, load_local_env
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate
from enterprise_agent.infrastructure.observability.logging import configure_logging

app = typer.Typer(help="enterprise-agent command line utilities")


@app.callback()
def _configure_logging() -> None:
    load_local_env()
    settings = Settings()
    configure_logging(settings.log_level, settings.log_file)


@app.command()
def run(input_text: str, delegate: bool = False) -> None:
    container = get_container()
    run_id = submit_run(container.runs, IdentityContext(tenant_id="local-tenant", user_id="local-user", is_authenticated=True), RunCreate(input=input_text, constraints={"request_delegation": delegate}), {"model_config": "models.yaml:v1"})
    worker_id = "cli-worker"
    container.runs.claim_work(worker_id, container.settings.lease_seconds)
    execute_run(container, run_id, worker_id)
    print(container.runs.get_run_view("local-tenant", run_id).model_dump_json(indent=2))


@app.command()
def recover_expired(run_id: str | None = None) -> None:
    """Requeue RUNNING work whose lease has already expired.

    Stop the worker that was executing the run before using this command.
    A live lease is not touched.
    """
    container = get_container()
    recovered = container.runs.requeue_expired(run_id)
    if not recovered:
        print("no expired work")
        return
    for item in recovered:
        print(item)


@app.command()
def check_config() -> None:
    container = get_container()
    print(f"offline_mode={container.settings.offline_mode}; models={len(container.model_config.profiles)}; roles={sorted(container.policy._policies)}")

