"""initial schema

Creates every table declared by ``app.models``.

Generated with ``alembic revision --autogenerate`` against the models and then
reviewed by hand, so this file is the authoritative record of the starting
schema rather than something the application can silently drift away from.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-04

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('candidates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('phone', sa.String(length=60), nullable=True),
    sa.Column('location', sa.String(length=150), nullable=True),
    sa.Column('linkedin_url', sa.String(length=400), nullable=True),
    sa.Column('github_url', sa.String(length=400), nullable=True),
    sa.Column('portfolio_url', sa.String(length=400), nullable=True),
    sa.Column('current_title', sa.String(length=200), nullable=True),
    sa.Column('total_experience_years', sa.Float(), nullable=False),
    sa.Column('relevant_experience_years', sa.Float(), nullable=False),
    sa.Column('highest_degree', sa.String(length=120), nullable=True),
    sa.Column('degree_field', sa.String(length=150), nullable=True),
    sa.Column('institution', sa.String(length=200), nullable=True),
    sa.Column('graduation_year', sa.Integer(), nullable=True),
    sa.Column('certifications', sa.Text(), nullable=False),
    sa.Column('previous_positions', sa.Text(), nullable=False),
    sa.Column('employment_history', sa.Text(), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=False),
    sa.Column('sections', sa.Text(), nullable=False),
    sa.Column('resume_path', sa.String(length=500), nullable=False),
    sa.Column('original_filename', sa.String(length=300), nullable=True),
    sa.Column('status', sa.Enum('uploaded', 'parsing', 'parsed', 'failed', name='processingstatus', native_enum=False, length=20), nullable=False),
    sa.Column('processing_error', sa.Text(), nullable=True),
    sa.Column('shortlisted', sa.Boolean(), nullable=False),
    sa.Column('embedding', sa.LargeBinary(), nullable=True),
    sa.Column('is_deleted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidates'))
    )
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_candidates_current_title'), ['current_title'], unique=False)
        batch_op.create_index(batch_op.f('ix_candidates_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_candidates_full_name'), ['full_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_candidates_is_deleted'), ['is_deleted'], unique=False)
        batch_op.create_index(batch_op.f('ix_candidates_location'), ['location'], unique=False)
        batch_op.create_index(batch_op.f('ix_candidates_shortlisted'), ['shortlisted'], unique=False)
        batch_op.create_index('ix_candidates_status_deleted', ['status', 'is_deleted'], unique=False)

    op.create_table('skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('normalized_name', sa.String(length=120), nullable=False),
    sa.Column('category', sa.Enum('programming_language', 'framework', 'database', 'cloud', 'ai_ml', 'devops', 'data_science', 'frontend', 'mobile', 'testing', 'soft_skill', 'certification', 'other', name='skillcategory', native_enum=False, length=40), nullable=False),
    sa.Column('aliases', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_skills'))
    )
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_skills_normalized_name'), ['normalized_name'], unique=True)

    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('admin', 'recruiter', 'hiring_manager', name='userrole', native_enum=False, length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('entity_type', sa.String(length=60), nullable=True),
    sa.Column('entity_id', sa.String(length=60), nullable=True),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_logs_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_logs_action'), ['action'], unique=False)
        batch_op.create_index('ix_audit_logs_created_at', ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_user_id'), ['user_id'], unique=False)

    op.create_table('candidate_skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.Column('skill_id', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('years_experience', sa.Float(), nullable=True),
    sa.Column('is_certified', sa.Boolean(), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_candidate_skills_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_candidate_skills_skill_id_skills'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_skills')),
    sa.UniqueConstraint('candidate_id', 'skill_id', name='uq_candidate_skills_candidate_id_skill_id')
    )
    with op.batch_alter_table('candidate_skills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_candidate_skills_candidate_id'), ['candidate_id'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('department', sa.String(length=150), nullable=True),
    sa.Column('location', sa.String(length=150), nullable=True),
    sa.Column('employment_type', sa.String(length=80), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('experience_required_years', sa.Float(), nullable=False),
    sa.Column('education_required', sa.String(length=120), nullable=True),
    sa.Column('required_skills', sa.Text(), nullable=False),
    sa.Column('preferred_skills', sa.Text(), nullable=False),
    sa.Column('certifications', sa.Text(), nullable=False),
    sa.Column('responsibilities', sa.Text(), nullable=False),
    sa.Column('soft_skills', sa.Text(), nullable=False),
    sa.Column('source_file', sa.String(length=500), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('embedding', sa.LargeBinary(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('experience_required_years >= 0', name=op.f('ck_jobs_experience_required_years_non_negative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_jobs_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_is_active'), ['is_active'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_title'), ['title'], unique=False)

    op.create_table('refresh_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=512), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_refresh_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens'))
    )
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_refresh_tokens_token'), ['token'], unique=True)
        batch_op.create_index(batch_op.f('ix_refresh_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('scoring_weights',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_scoring_weights_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scoring_weights')),
    sa.UniqueConstraint('name', name=op.f('uq_scoring_weights_name'))
    )
    op.create_table('batch_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('completed', sa.Integer(), nullable=False),
    sa.Column('failed', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'processing', 'completed', 'failed', name='matchstatus', native_enum=False, length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_batch_jobs_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_batch_jobs_job_id_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_batch_jobs'))
    )
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('messages', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_conversations_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_conversations_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_table('job_skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('skill_id', sa.Integer(), nullable=False),
    sa.Column('importance', sa.String(length=20), nullable=False),
    sa.Column('minimum_years', sa.Float(), nullable=True),
    sa.Column('weight', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_job_skills_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_job_skills_skill_id_skills'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_skills')),
    sa.UniqueConstraint('job_id', 'skill_id', name='uq_job_skills_job_id_skill_id')
    )
    with op.batch_alter_table('job_skills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_skills_job_id'), ['job_id'], unique=False)

    op.create_table('match_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.Column('required_skills_score', sa.Float(), nullable=False),
    sa.Column('experience_score', sa.Float(), nullable=False),
    sa.Column('education_score', sa.Float(), nullable=False),
    sa.Column('preferred_skills_score', sa.Float(), nullable=False),
    sa.Column('semantic_score', sa.Float(), nullable=False),
    sa.Column('certifications_score', sa.Float(), nullable=False),
    sa.Column('overall_score', sa.Float(), nullable=False),
    sa.Column('weights', sa.Text(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_match_results_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_match_results_job_id_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_results')),
    sa.UniqueConstraint('job_id', 'candidate_id', name='uq_match_results_job_id_candidate_id')
    )
    with op.batch_alter_table('match_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_match_results_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_match_results_job_id'), ['job_id'], unique=False)
        batch_op.create_index('ix_match_results_job_overall', ['job_id', 'overall_score'], unique=False)
        batch_op.create_index(batch_op.f('ix_match_results_overall_score'), ['overall_score'], unique=False)

    op.create_table('match_evidence',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('component', sa.String(length=40), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('weight', sa.Float(), nullable=False),
    sa.Column('snippet', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['match_id'], ['match_results.id'], name=op.f('fk_match_evidence_match_id_match_results'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_evidence'))
    )
    with op.batch_alter_table('match_evidence', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_match_evidence_match_id'), ['match_id'], unique=False)

    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('match_evidence', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_match_evidence_match_id'))

    op.drop_table('match_evidence')
    with op.batch_alter_table('match_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_match_results_overall_score'))
        batch_op.drop_index('ix_match_results_job_overall')
        batch_op.drop_index(batch_op.f('ix_match_results_job_id'))
        batch_op.drop_index(batch_op.f('ix_match_results_candidate_id'))

    op.drop_table('match_results')
    with op.batch_alter_table('job_skills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_skills_job_id'))

    op.drop_table('job_skills')
    op.drop_table('conversations')
    op.drop_table('batch_jobs')
    op.drop_table('scoring_weights')
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_user_id'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_token'))

    op.drop_table('refresh_tokens')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_title'))
        batch_op.drop_index(batch_op.f('ix_jobs_is_active'))

    op.drop_table('jobs')
    with op.batch_alter_table('candidate_skills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_candidate_skills_candidate_id'))

    op.drop_table('candidate_skills')
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_logs_user_id'))
        batch_op.drop_index('ix_audit_logs_created_at')
        batch_op.drop_index(batch_op.f('ix_audit_logs_action'))

    op.drop_table('audit_logs')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_skills_normalized_name'))

    op.drop_table('skills')
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_index('ix_candidates_status_deleted')
        batch_op.drop_index(batch_op.f('ix_candidates_shortlisted'))
        batch_op.drop_index(batch_op.f('ix_candidates_location'))
        batch_op.drop_index(batch_op.f('ix_candidates_is_deleted'))
        batch_op.drop_index(batch_op.f('ix_candidates_full_name'))
        batch_op.drop_index(batch_op.f('ix_candidates_email'))
        batch_op.drop_index(batch_op.f('ix_candidates_current_title'))

    op.drop_table('candidates')
    # ### end Alembic commands ###