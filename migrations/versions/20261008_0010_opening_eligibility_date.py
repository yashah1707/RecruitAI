"""opening eligibility date

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-08 19:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('job_openings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('eligibility_date', sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('job_openings', schema=None) as batch_op:
        batch_op.drop_column('eligibility_date')
