"""inbox body excerpt

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08 15:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('inbox_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('body_excerpt', sa.String(length=1200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('inbox_messages', schema=None) as batch_op:
        batch_op.drop_column('body_excerpt')
