"""a ticket has a work type

The work CATEGORY the operator sees on a proposal: bug | feature | task. It is a
separate axis from the existing `type_id`/ticket_type, which mirrors `execution`
(Code/Judgement/External) and gates unattended dispatch. Folding the two onto one
column would make a bug ticket's dispatchability depend on its being a bug, which
is the coupling this column exists to avoid.

  work_type   VARCHAR(16), NULLABLE. Null on every existing row and by default:
              "unclassified", which the store deliberately keeps distinct from a
              defaulted "task" so "nobody said" reads differently from "someone
              said task". The JSON-backed scopes already carry this field; this
              gives the relational scopes (development, workshop) the same home.

Revision ID: a1b2c7e40d59
Revises: d3f5a90c71b2
Create Date: 2026-08-26

`down_revision` names d3f5a90c71b2 explicitly (the single head at write time,
confirmed via `alembic heads`) rather than a relative parent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c7e40d59'
down_revision: Union[str, Sequence[str], None] = 'd3f5a90c71b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ticket", sa.Column("work_type", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("ticket", "work_type")
