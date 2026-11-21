"""Workflow drafts from the designer

Revision: 0021
Follows: 0020
"""
from alembic import op
import sqlalchemy as sa


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('workflow_draft',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('case_type', sa.String(length=60), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('design', sa.JSON(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('origin', sa.String(length=80), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=False),
    sa.Column('created', sa.DateTime(), nullable=False),
    sa.Column('updated_by', sa.String(length=40), nullable=False),
    sa.Column('updated', sa.DateTime(), nullable=False),
    sa.Column('published_version', sa.String(length=20), nullable=True),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('workflow_draft', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_workflow_draft_case_type'), ['case_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_workflow_draft_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_workflow_draft_updated'), ['updated'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('workflow_draft', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_workflow_draft_updated'))
        batch_op.drop_index(batch_op.f('ix_workflow_draft_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_workflow_draft_case_type'))
    op.drop_table('workflow_draft')
