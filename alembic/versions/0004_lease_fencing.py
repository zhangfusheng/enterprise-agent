"""lease fencing token, retry counters, and durable tool operations"""

import sqlalchemy as sa
from alembic import op

revision = "0004_lease_fencing"
down_revision = "0003_chat_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(sa.Column("lease_token", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("claim_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("lease_loss_count", sa.Integer(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("task_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("arguments_hash", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("has_side_effect", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_column("has_side_effect")
        batch.drop_column("arguments_hash")
        batch.drop_column("task_id")
    with op.batch_alter_table("work_items") as batch:
        batch.drop_column("lease_loss_count")
        batch.drop_column("execution_attempts")
        batch.drop_column("claim_attempts")
        batch.drop_column("last_heartbeat_at")
        batch.drop_column("lease_generation")
        batch.drop_column("lease_token")
