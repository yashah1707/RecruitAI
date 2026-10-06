"""evaluation details

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('evaluation_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('details', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('evaluation_results', schema=None) as batch_op:
        batch_op.drop_column('details')
