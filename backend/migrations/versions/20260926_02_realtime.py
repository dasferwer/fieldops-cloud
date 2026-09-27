"""Добавляем долговечный журнал уведомлений без копирования содержимого заявок."""

import sqlalchemy as sa
from alembic import op

revision = "20260926_02"
down_revision = "20260901_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("id = 1 AND position >= 0", name="ck_stream_state"),
    )
    op.execute("INSERT INTO stream_state VALUES (1, 0)")
    op.create_table(
        "stream_events",
        sa.Column("position", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("work_order_id", sa.Uuid(), sa.ForeignKey("work_orders.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_assignee_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("stream_events")
    op.drop_table("stream_state")
