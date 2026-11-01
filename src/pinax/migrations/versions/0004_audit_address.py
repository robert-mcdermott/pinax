# SPDX-License-Identifier: Apache-2.0
"""The audit log in the app: where each change came from, and indexes for its filters (wave 12).

Revision ID: 0004
Revises: 0003
Created: 2026-11-01 12:44:15.472951
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('audit_event', sa.Column('address', sa.String(length=64), server_default='', nullable=False))
    op.create_index(op.f('ix_audit_event_action'), 'audit_event', ['action'], unique=False)
    op.create_index(op.f('ix_audit_event_actor_id'), 'audit_event', ['actor_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_audit_event_actor_id'), table_name='audit_event')
    op.drop_index(op.f('ix_audit_event_action'), table_name='audit_event')
    with op.batch_alter_table('audit_event') as batch_op:
        batch_op.drop_column('address')
