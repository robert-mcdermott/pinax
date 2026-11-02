# SPDX-License-Identifier: Apache-2.0
"""Emergency accounts (wave 13): storage administrators who may sign in with a password when the identity provider
fails, marked from the command line.

Revision ID: 0006
Revises: 0005
Created: 2026-11-01 17:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('user', sa.Column('emergency', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.drop_column('emergency')
