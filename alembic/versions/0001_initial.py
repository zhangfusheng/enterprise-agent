"""initial application tables"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("route", sa.String(32)),
        sa.Column("summary", sa.Text()),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("budget_json", sa.JSON(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_run_idempotency"),
    )
    op.create_index("ix_runs_tenant_id", "runs", ["tenant_id"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_table(
        "work_items",
        sa.Column("work_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_index("ix_work_items_run_id", "work_items", ["run_id"])
    op.create_index("ix_work_items_status", "work_items", ["status"])
    op.create_index("ix_work_claim", "work_items", ["status", "available_at", "lease_expires_at"])
    op.create_table(
        "run_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_event_sequence"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_tenant_id", "run_events", ["tenant_id"])
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifacts_tenant_id", "artifacts", ["tenant_id"])
    op.create_table(
        "operations",
        sa.Column("operation_id", sa.String(255), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operations_tenant_id", "operations", ["tenant_id"])
    op.create_index("ix_operations_run_id", "operations", ["run_id"])
    op.create_table(
        "plan_versions",
        sa.Column("plan_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plan_versions_run_id", "plan_versions", ["run_id"])
    op.create_index("ix_plan_versions_tenant_id", "plan_versions", ["tenant_id"])
    op.create_table(
        "delegations",
        sa.Column("delegation_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(128), nullable=False),
        sa.Column("parent_agent_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_delegations_run_id", "delegations", ["run_id"])
    op.create_index("ix_delegations_tenant_id", "delegations", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_delegations_tenant_id", table_name="delegations")
    op.drop_index("ix_delegations_run_id", table_name="delegations")
    op.drop_table("delegations")
    op.drop_index("ix_plan_versions_tenant_id", table_name="plan_versions")
    op.drop_index("ix_plan_versions_run_id", table_name="plan_versions")
    op.drop_table("plan_versions")
    op.drop_index("ix_operations_run_id", table_name="operations")
    op.drop_index("ix_operations_tenant_id", table_name="operations")
    op.drop_table("operations")
    op.drop_index("ix_artifacts_tenant_id", table_name="artifacts")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_run_events_tenant_id", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_work_claim", table_name="work_items")
    op.drop_index("ix_work_items_status", table_name="work_items")
    op.drop_index("ix_work_items_run_id", table_name="work_items")
    op.drop_table("work_items")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_tenant_id", table_name="runs")
    op.drop_table("runs")
