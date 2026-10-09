"""Сохраняем границу удалённого префикса журнала независимо от наличия событий."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_03"
down_revision = "20260926_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stream_state",
        sa.Column("retained_after", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_stream_retained_after",
        "stream_state",
        "retained_after >= 0 AND retained_after <= position",
    )


def downgrade() -> None:
    # После физического удаления старая схема не умеет отличать stale cursor от актуального.
    # Блокировка защищает проверку от параллельного cleaner; отказ откатывает весь DDL.
    op.execute("LOCK TABLE stream_state IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM stream_state WHERE retained_after > 0) THEN
                RAISE EXCEPTION 'retained_after > 0: downgrade would lose the replay boundary';
            END IF;
        END $$
    """)
    op.drop_constraint("ck_stream_retained_after", "stream_state", type_="check")
    op.drop_column("stream_state", "retained_after")
