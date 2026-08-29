"""dev_only flag on app

The development app is excluded from GET /apps and all catalogue surfaces
when WORKSHOP_DEV_MODE is not '1'.  A boolean column is the smallest hook:
the service layer reads it at query time; the backfill sets it to true for
the development row; everything else defaults to false.

Ticket: 2d6e7597366e4541ab2e52e05aae43dd

Revision ID: a8b7c6d5e4f3
Revises: f3b8d2a17e94
Create Date: 2026-08-29
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b7c6d5e4f3"
down_revision: str | Sequence[str] | None = "a4e2c7b19f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app",
        sa.Column("dev_only", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("app", "dev_only")
