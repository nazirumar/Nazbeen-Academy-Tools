"""phase 2: candles market-data cache

Revision ID: c5f21a0b93d2
Revises: 9c7783174bf9
Create Date: 2026-11-02 00:00:00.000000

Historical candle cache for the MetaTrader 5 integration. The composite
primary key (symbol, timeframe, open_time) prevents duplicate inserts at the
database level; the store layer refreshes forming candles via
INSERT ... ON CONFLICT DO UPDATE.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5f21a0b93d2"
down_revision: str | None = "9c7783174bf9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candles",
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Double(), nullable=False),
        sa.Column("high", sa.Double(), nullable=False),
        sa.Column("low", sa.Double(), nullable=False),
        sa.Column("close", sa.Double(), nullable=False),
        sa.Column("volume", sa.Double(), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="mt5"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=True),
        sa.CheckConstraint(
            "open > 0 AND high > 0 AND low > 0 AND close > 0", name="positive_prices"
        ),
        sa.CheckConstraint("high >= low", name="high_gte_low"),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "open_time", name="pk_candles"),
    )
    # DESC ordering matches the hot read path (latest N candles per series).
    op.create_index(
        "ix_candles_series_open_time_desc",
        "candles",
        ["symbol", "timeframe", sa.text("open_time DESC")],
        unique=False,
    )
    op.create_index(
        "ix_candles_open_time", "candles", ["open_time"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_candles_open_time", table_name="candles")
    op.drop_index("ix_candles_series_open_time_desc", table_name="candles")
    op.drop_table("candles")
