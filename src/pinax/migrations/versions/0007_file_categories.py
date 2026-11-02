# SPDX-License-Identifier: Apache-2.0
"""File categories a site defines (wave 14): kept in the catalog rather than in the code, filled with the built-in
ones the first time Pinax starts (categories.ensure_defaults).

Revision ID: 0007
Revises: 0006
Created: 2026-11-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('file_category',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=32), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('extensions', sa.JSON(), nullable=False),
    sa.Column('patterns', sa.JSON(), nullable=False),
    sa.Column('builtin', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_file_category')),
    sa.UniqueConstraint('key', name=op.f('uq_file_category_key'))
    )


def downgrade() -> None:
    op.drop_table('file_category')
