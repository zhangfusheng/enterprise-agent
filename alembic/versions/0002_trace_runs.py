"""trace runs"""

import sqlalchemy as sa
from alembic import op

revision = "0002_trace_runs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trace_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("parent_run_id", sa.String(64)),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("outputs_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("extra_json", sa.JSON(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("dotted_order", sa.Text(), nullable=False),
    )
    op.create_index("ix_trace_runs_trace_id", "trace_runs", ["trace_id"])
    op.create_index("ix_trace_runs_lookup", "trace_runs", ["tenant_id", "run_id"])


def downgrade() -> None:
    op.drop_index("ix_trace_runs_lookup", table_name="trace_runs")
    op.drop_index("ix_trace_runs_trace_id", table_name="trace_runs")
    op.drop_table("trace_runs")
