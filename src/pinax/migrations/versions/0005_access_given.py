# SPDX-License-Identifier: Apache-2.0
"""The access review: when a person was made a storage administrator, and when each lab membership was given
(wave 12). Given before, it was not recorded, and stays empty.

Revision ID: 0005
Revises: 0004
Created: 2026-11-01 13:02:34.948665
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('lab_membership', sa.Column('granted_at', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('admin_since', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.drop_column('admin_since')
    with op.batch_alter_table('lab_membership') as batch_op:
        batch_op.drop_column('granted_at')
