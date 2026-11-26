"""m2 datasets suites reviews exports

Revision ID: cdbca144a1a7
Revises: 2261be28cb23
Create Date: 2026-11-24 09:31:13.533954
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'cdbca144a1a7'
down_revision = '2261be28cb23'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table('exports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('format', sa.String(length=10), nullable=False),
    sa.Column('path', sa.String(length=500), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['test_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_exports_run_id'), 'exports', ['run_id'], unique=False)

    # New NOT NULL columns get server defaults so existing rows (M1 Studio cases) migrate.
    op.add_column('dataset_versions', sa.Column('fields_present', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column('dataset_versions', sa.Column('change_note', sa.String(length=300), nullable=False, server_default=''))
    op.add_column('dataset_versions', sa.Column('created_by', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_dataset_versions_created_by', 'dataset_versions', 'users', ['created_by'], ['id'])
    op.create_unique_constraint('uq_human_reviews_metric_reviewer', 'human_reviews', ['metric_result_id', 'reviewer_id'])
    op.add_column('test_cases', sa.Column('case_key', sa.String(length=36), nullable=False,
                                          server_default=sa.text('gen_random_uuid()::text')))
    op.add_column('test_cases', sa.Column('position', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('test_cases', sa.Column('recorded_output', sa.Text(), nullable=True))
    op.create_index(op.f('ix_test_cases_case_key'), 'test_cases', ['case_key'], unique=False)
    op.add_column('test_suites', sa.Column('selection', sa.String(length=10), nullable=False, server_default='all'))
    op.add_column('test_suites', sa.Column('tags', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column('test_suites', 'tags')
    op.drop_column('test_suites', 'selection')
    op.drop_index(op.f('ix_test_cases_case_key'), table_name='test_cases')
    op.drop_column('test_cases', 'recorded_output')
    op.drop_column('test_cases', 'position')
    op.drop_column('test_cases', 'case_key')
    op.drop_constraint('uq_human_reviews_metric_reviewer', 'human_reviews', type_='unique')
    op.drop_constraint('fk_dataset_versions_created_by', 'dataset_versions', type_='foreignkey')
    op.drop_column('dataset_versions', 'created_by')
    op.drop_column('dataset_versions', 'change_note')
    op.drop_column('dataset_versions', 'fields_present')
    op.drop_index(op.f('ix_exports_run_id'), table_name='exports')
    op.drop_table('exports')
