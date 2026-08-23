"""a merged change keeps its base sha

Revision ID: c8299a81457a
Revises: a7c3e10d9b64
Create Date: 2026-08-22 18:24:09.305912

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8299a81457a'
down_revision: Union[str, Sequence[str], None] = 'a7c3e10d9b64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("change", sa.Column("base_sha", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("change", "base_sha")
