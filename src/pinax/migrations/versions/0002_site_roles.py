# SPDX-License-Identifier: Apache-2.0
"""Site roles: auditor, finance, and division head, beside a person's lab memberships (wave 12).

Revision ID: 0002
Revises: 0001
Created: 2026-11-01 09:22:04.297132
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('user_role',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('division', sa.String(length=200), server_default='', nullable=False),
    sa.Column('granted_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_user_role_user_id_user'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_role')),
    sa.UniqueConstraint('user_id', 'role', 'division', name=op.f('uq_user_role_user_id_role_division'))
    )


def downgrade() -> None:
    op.drop_table('user_role')
