"""gate 1 review edits and new reader fields

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06 15:10:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('review_edits',
    sa.Column('edit_id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(length=60), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('old_value', sa.String(length=300), nullable=True),
    sa.Column('new_value', sa.String(length=300), nullable=True),
    sa.Column('actor', sa.String(length=80), nullable=False),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.application_id'], ),
    sa.PrimaryKeyConstraint('edit_id')
    )
    with op.batch_alter_table('review_edits', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_edits_application_id'), ['application_id'], unique=False)

    # Every new column is nullable: rows read before Phase 4 simply have no value.
    with op.batch_alter_table('institutions_master', schema=None) as batch_op:
        batch_op.add_column(sa.Column('aliases', sa.JSON(), nullable=True))

    with op.batch_alter_table('candidate_qualifications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('discipline_listed', sa.String(length=60), nullable=True))

    with op.batch_alter_table('candidate_publications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('author_count', sa.Integer(), nullable=True))

    with op.batch_alter_table('candidate_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('level', sa.String(length=20), nullable=True))

    with op.batch_alter_table('candidate_achievements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('level', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('amount_stated', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('amount_inr', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('candidate_achievements', schema=None) as batch_op:
        batch_op.drop_column('amount_inr')
        batch_op.drop_column('amount_stated')
        batch_op.drop_column('level')

    with op.batch_alter_table('candidate_events', schema=None) as batch_op:
        batch_op.drop_column('level')

    with op.batch_alter_table('candidate_publications', schema=None) as batch_op:
        batch_op.drop_column('author_count')

    with op.batch_alter_table('candidate_qualifications', schema=None) as batch_op:
        batch_op.drop_column('discipline_listed')

    with op.batch_alter_table('institutions_master', schema=None) as batch_op:
        batch_op.drop_column('aliases')

    with op.batch_alter_table('review_edits', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_edits_application_id'))

    op.drop_table('review_edits')
