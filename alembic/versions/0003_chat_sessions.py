"""persist user chat sessions separately from agent runs"""

import sqlalchemy as sa
from alembic import op

revision = "0003_chat_sessions"
down_revision = "0002_trace_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_chat_sessions_owner",
        "chat_sessions",
        ["tenant_id", "user_id", "updated_at"],
    )
    op.create_index("ix_chat_sessions_tenant_id", "chat_sessions", ["tenant_id"])
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    op.create_table(
        "chat_messages",
        sa.Column("message_id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "sequence", name="uq_chat_message_sequence"),
    )
    op.create_index("ix_chat_messages_session", "chat_messages", ["session_id", "sequence"])
    op.create_index("ix_chat_messages_run", "chat_messages", ["run_id"])
    op.create_index("ix_chat_messages_tenant_id", "chat_messages", ["tenant_id"])
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])

    # Preserve existing Run history as one session per legacy Run. New runs
    # are linked transactionally by the application repository.
    connection = op.get_bind()
    legacy_runs = connection.execute(
        sa.text(
            "SELECT run_id, tenant_id, user_id, input_text, summary, created_at, updated_at "
            "FROM runs"
        )
    ).mappings()
    for row in legacy_runs:
        session_id = f"session_{row['run_id']}"
        connection.execute(
            sa.text(
                "INSERT INTO chat_sessions "
                "(session_id, tenant_id, user_id, title, created_at, updated_at) "
                "VALUES (:session_id, :tenant_id, :user_id, :title, :created_at, :updated_at)"
            ),
            {
                "session_id": session_id,
                "tenant_id": row["tenant_id"],
                "user_id": row["user_id"],
                "title": " ".join(str(row["input_text"]).split())[:80] or "新建聊天",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO chat_messages "
                "(message_id, session_id, tenant_id, user_id, run_id, role, sequence, content, created_at) "
                "VALUES (:message_id, :session_id, :tenant_id, :user_id, :run_id, 'user', 1, :content, :created_at)"
            ),
            {
                "message_id": f"message_{row['run_id']}",
                "session_id": session_id,
                "tenant_id": row["tenant_id"],
                "user_id": row["user_id"],
                "run_id": row["run_id"],
                "content": row["input_text"],
                "created_at": row["created_at"],
            },
        )
        if row["summary"]:
            connection.execute(
                sa.text(
                    "INSERT INTO chat_messages "
                    "(message_id, session_id, tenant_id, user_id, run_id, role, sequence, content, created_at) "
                    "VALUES (:message_id, :session_id, :tenant_id, :user_id, :run_id, 'assistant', 2, :content, :created_at)"
                ),
                {
                    "message_id": f"message_{row['run_id']}_assistant",
                    "session_id": session_id,
                    "tenant_id": row["tenant_id"],
                    "user_id": row["user_id"],
                    "run_id": row["run_id"],
                    "content": row["summary"],
                    "created_at": row["updated_at"],
                },
            )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_user_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_tenant_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_run", table_name="chat_messages")
    op.drop_index("ix_chat_messages_session", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_tenant_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_owner", table_name="chat_sessions")
    op.drop_table("chat_sessions")
