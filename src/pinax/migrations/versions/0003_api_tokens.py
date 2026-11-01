# SPDX-License-Identifier: Apache-2.0
"""API tokens for scripts: a person's own, and service tokens for a named service (wave 12).

Revision ID: 0003
Revises: 0002
Created: 2026-11-01 11:19:40.872584
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('api_token',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('role', sa.String(length=32), server_default='', nullable=False),
    sa.Column('read_only', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('last_used_address', sa.String(length=64), server_default='', nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], name=op.f('fk_api_token_created_by_id_user')),
    sa.ForeignKeyConstraint(['revoked_by_id'], ['user.id'], name=op.f('fk_api_token_revoked_by_id_user')),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_api_token_user_id_user'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_api_token')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_api_token_token_hash'))
    )
    op.create_index(op.f('ix_api_token_user_id'), 'api_token', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_api_token_user_id'), table_name='api_token')
    op.drop_table('api_token')
