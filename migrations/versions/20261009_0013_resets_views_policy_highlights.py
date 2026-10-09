"""password resets; views, policy rules and highlight norms

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-09 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('password_resets',
    sa.Column('reset_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ),
    sa.PrimaryKeyConstraint('reset_id')
    )
    with op.batch_alter_table('password_resets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_password_resets_token_hash'), ['token_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_password_resets_user_id'), ['user_id'], unique=False)

    op.create_table('view_definitions',
    sa.Column('view_id', sa.Integer(), nullable=False),
    sa.Column('view_name', sa.String(length=40), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=True),
    sa.PrimaryKeyConstraint('view_id'),
    sa.UniqueConstraint('view_name')
    )
    op.create_table('view_field_visibility',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('view_id', sa.Integer(), nullable=False),
    sa.Column('entity_name', sa.String(length=60), nullable=False),
    sa.Column('field_name', sa.String(length=60), nullable=False),
    sa.Column('is_visible', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['view_id'], ['view_definitions.view_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('view_id', 'entity_name', 'field_name')
    )
    with op.batch_alter_table('view_field_visibility', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_view_field_visibility_view_id'), ['view_id'], unique=False)

    op.create_table('university_policy_rules',
    sa.Column('policy_id', sa.Integer(), nullable=False),
    sa.Column('school_id', sa.String(length=10), nullable=True),
    sa.Column('designation', sa.String(length=40), nullable=False),
    sa.Column('criterion_name', sa.String(length=40), nullable=False),
    sa.Column('criterion_value', sa.String(length=40), nullable=False),
    sa.Column('rule_version_id', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['rule_version_id'], ['rule_versions.rule_version_id'], ),
    sa.ForeignKeyConstraint(['school_id'], ['schools.school_id'], ),
    sa.PrimaryKeyConstraint('policy_id')
    )
    op.create_table('highlight_norms',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('school_id', sa.String(length=10), nullable=True),
    sa.Column('min_h_index', sa.Integer(), nullable=True),
    sa.Column('min_citations', sa.Integer(), nullable=True),
    sa.Column('min_impact_factor', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['school_id'], ['schools.school_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('school_id')
    )


def downgrade() -> None:
    op.drop_table('highlight_norms')
    op.drop_table('university_policy_rules')
    op.drop_table('view_field_visibility')
    op.drop_table('view_definitions')
    op.drop_table('password_resets')
