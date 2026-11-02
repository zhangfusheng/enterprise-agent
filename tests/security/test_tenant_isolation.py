import pytest

from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate
from enterprise_agent.infrastructure.repositories.runs import TenantNotFound


def test_run_is_not_visible_to_other_tenant(container):
    run_id = submit_run(container.runs, IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True), RunCreate(input="hello"))
    with pytest.raises(TenantNotFound):
        container.runs.get_run_view("tenant-b", run_id)

