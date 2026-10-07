"""hr decisions

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06 19:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('hr_decisions',
    sa.Column('decision_id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=False),
    sa.Column('evaluation_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=12), nullable=False),
    sa.Column('final_outcome', sa.String(length=30), nullable=True),
    sa.Column('final_designation', sa.String(length=40), nullable=True),
    sa.Column('justification', sa.String(length=1000), nullable=True),
    sa.Column('actor', sa.String(length=80), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.application_id'], ),
    sa.ForeignKeyConstraint(['evaluation_id'], ['evaluation_results.evaluation_id'], ),
    sa.PrimaryKeyConstraint('decision_id')
    )
    with op.batch_alter_table('hr_decisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hr_decisions_application_id'), ['application_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('hr_decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_hr_decisions_application_id'))

    op.drop_table('hr_decisions')
