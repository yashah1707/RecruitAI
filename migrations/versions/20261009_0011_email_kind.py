"""email kind: decision letters and acknowledgements

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('email_drafts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=20), server_default='DECISION', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('email_drafts', schema=None) as batch_op:
        batch_op.drop_column('kind')
