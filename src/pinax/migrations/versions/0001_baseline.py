# SPDX-License-Identifier: Apache-2.0
"""The catalog as Pinax 0.9.0 left it: the first revision.

A new catalog gets every table. A catalog made before migrations (Pinax 0.9.0 and earlier, which created new tables
and added new columns at start-up) gets the tables and columns it lacks, as Pinax did then: each column added is
nullable or has a default, so rows already there keep working. The tables are written out here, not taken from
models.py, so that this revision stays what it was as the models change; later revisions change them with
Alembic's operations.

Revision ID: 0001
Revises:
Created: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.schema import CreateIndex, CreateTable

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

META = sa.MetaData()  # every name given, as the naming rule in db.py made them

sa.Table(
    'filesystem', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('root', sa.String(length=2048), nullable=False),
    sa.Column('display_root', sa.String(length=2048), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name='pk_filesystem'),
    sa.UniqueConstraint('slug', name='uq_filesystem_slug'),
)


sa.Table(
    'heartbeat', META,
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('name', name='pk_heartbeat'),
)


sa.Table(
    'lab', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('division', sa.String(length=200), nullable=False),
    sa.Column('research_area', sa.String(length=200), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name='pk_lab'),
    sa.UniqueConstraint('slug', name='uq_lab_slug'),
)


sa.Table(
    'user', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('password_set_at', sa.DateTime(), nullable=True),
    sa.Column('must_change_password', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('failed_logins', sa.Integer(), server_default='0', nullable=False),
    sa.Column('locked_until', sa.DateTime(), nullable=True),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id', name='pk_user'),
    sa.UniqueConstraint('username', name='uq_user_username'),
)


sa.Table(
    'audit_event', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['user.id'], name='fk_audit_event_actor_id_user'),
    sa.PrimaryKeyConstraint('id', name='pk_audit_event'),
    sa.Index('ix_audit_event_ts', 'ts'),
)


sa.Table(
    'capacity', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filesystem_id', sa.Integer(), nullable=False),
    sa.Column('starts', sa.Date(), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.Column('unit', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['filesystem_id'], ['filesystem.id'], name='fk_capacity_filesystem_id_filesystem', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_capacity'),
    sa.UniqueConstraint('filesystem_id', 'starts', name='uq_capacity_filesystem_id_starts'),
    sa.Index('ix_capacity_filesystem_id', 'filesystem_id'),
)


sa.Table(
    'folder_owner', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filesystem_id', sa.Integer(), nullable=False),
    sa.Column('folder', sa.String(length=1024), nullable=False),
    sa.Column('lab_id', sa.Integer(), nullable=True),
    sa.Column('starts', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['filesystem_id'], ['filesystem.id'], name='fk_folder_owner_filesystem_id_filesystem', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['lab_id'], ['lab.id'], name='fk_folder_owner_lab_id_lab', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name='pk_folder_owner'),
    sa.Index('ix_folder_owner_filesystem_id', 'filesystem_id'),
)


sa.Table(
    'free_tier', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filesystem_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=True),
    sa.Column('lab_id', sa.Integer(), nullable=True),
    sa.Column('starts', sa.Date(), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=True),
    sa.Column('unit', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['filesystem_id'], ['filesystem.id'], name='fk_free_tier_filesystem_id_filesystem', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['lab_id'], ['lab.id'], name='fk_free_tier_lab_id_lab', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_free_tier'),
    sa.UniqueConstraint('filesystem_id', 'kind', 'lab_id', 'starts', name='uq_free_tier_filesystem_id_kind_lab_id_starts'),
    sa.Index('ix_free_tier_filesystem_id', 'filesystem_id'),
)


sa.Table(
    'lab_folder', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lab_id', sa.Integer(), nullable=False),
    sa.Column('filesystem_id', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(length=1024), nullable=False),
    sa.ForeignKeyConstraint(['filesystem_id'], ['filesystem.id'], name='fk_lab_folder_filesystem_id_filesystem'),
    sa.ForeignKeyConstraint(['lab_id'], ['lab.id'], name='fk_lab_folder_lab_id_lab'),
    sa.PrimaryKeyConstraint('id', name='pk_lab_folder'),
    sa.UniqueConstraint('filesystem_id', 'path', name='uq_lab_folder_filesystem_id_path'),
)


sa.Table(
    'lab_membership', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('lab_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['lab_id'], ['lab.id'], name='fk_lab_membership_lab_id_lab'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='fk_lab_membership_user_id_user'),
    sa.PrimaryKeyConstraint('id', name='pk_lab_membership'),
    sa.UniqueConstraint('user_id', 'lab_id', name='uq_lab_membership_user_id_lab_id'),
)


sa.Table(
    'login_session', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('address', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='fk_login_session_user_id_user', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_login_session'),
    sa.UniqueConstraint('token_hash', name='uq_login_session_token_hash'),
    sa.Index('ix_login_session_user_id', 'user_id'),
)


sa.Table(
    'price', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filesystem_id', sa.Integer(), nullable=False),
    sa.Column('starts', sa.Date(), nullable=False),
    sa.Column('micros', sa.BigInteger(), nullable=False),
    sa.Column('unit', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['filesystem_id'], ['filesystem.id'], name='fk_price_filesystem_id_filesystem', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_price'),
    sa.UniqueConstraint('filesystem_id', 'starts', name='uq_price_filesystem_id_starts'),
    sa.Index('ix_price_filesystem_id', 'filesystem_id'),
)


sa.Table(
    'saved_view', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('address', sa.String(length=2000), nullable=False),
    sa.Column('lab_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lab_id'], ['lab.id'], name='fk_saved_view_lab_id_lab', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='fk_saved_view_user_id_user', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_saved_view'),
    sa.Index('ix_saved_view_lab_id', 'lab_id'),
    sa.Index('ix_saved_view_user_id', 'user_id'),
)


sa.Table(
    'search_job', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('request', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('files', sa.BigInteger(), nullable=True),
    sa.Column('bytes', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='fk_search_job_user_id_user', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_search_job'),
    sa.Index('ix_search_job_status', 'status'),
    sa.Index('ix_search_job_user_id', 'user_id'),
)


sa.Table(
    'snapshot', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filesystem_id', sa.Integer(), nullable=False),
    sa.Column('scan_id', sa.String(length=64), nullable=False),
    sa.Column('scan_dir', sa.String(length=2048), nullable=False),
    sa.Column('index_dir', sa.String(length=2048), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('scan_seconds', sa.Float(), nullable=True),
    sa.Column('scan_status', sa.String(length=32), nullable=False),
    sa.Column('tool_version', sa.String(length=32), nullable=False),
    sa.Column('host', sa.String(length=200), nullable=False),
    sa.Column('ingested_at', sa.DateTime(), nullable=True),
    sa.Column('build_seconds', sa.Float(), nullable=True),
    sa.Column('entries', sa.BigInteger(), nullable=False),
    sa.Column('files', sa.BigInteger(), nullable=False),
    sa.Column('dirs', sa.BigInteger(), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.Column('alloc_bytes', sa.BigInteger(), nullable=False),
    sa.Column('errors', sa.BigInteger(), nullable=False),
    sa.Column('index_bytes', sa.BigInteger(), nullable=False),
    sa.Column('info', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['filesystem_id'], ['filesystem.id'], name='fk_snapshot_filesystem_id_filesystem'),
    sa.PrimaryKeyConstraint('id', name='pk_snapshot'),
    sa.UniqueConstraint('scan_id', name='uq_snapshot_scan_id'),
    sa.Index('ix_snapshot_filesystem_id', 'filesystem_id'),
    sa.Index('ix_snapshot_status', 'status'),
)


sa.Table(
    'tag', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('lab_id', sa.Integer(), nullable=True),
    sa.Column('value_type', sa.String(length=16), nullable=False),
    sa.Column('choices', sa.JSON(), nullable=False),
    sa.Column('many', sa.Boolean(), nullable=False),
    sa.Column('applied_by', sa.String(length=16), nullable=False),
    sa.Column('protects', sa.Boolean(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lab_id'], ['lab.id'], name='fk_tag_lab_id_lab', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_tag'),
    sa.UniqueConstraint('lab_id', 'key', name='uq_tag_lab_id_key'),
    sa.Index('ix_tag_lab_id', 'lab_id'),
)


sa.Table(
    'folder_stat', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('snapshot_id', sa.Integer(), nullable=False),
    sa.Column('filesystem_id', sa.Integer(), nullable=False),
    sa.Column('folder', sa.String(length=1024), nullable=False),
    sa.Column('taken_at', sa.DateTime(), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.Column('alloc_bytes', sa.BigInteger(), nullable=False),
    sa.Column('files', sa.BigInteger(), nullable=False),
    sa.Column('dirs', sa.BigInteger(), nullable=False),
    sa.Column('cold_bytes', sa.BigInteger(), nullable=False),
    sa.Column('mean_age_days', sa.Float(), nullable=True),
    sa.Column('newest_mtime', sa.DateTime(), nullable=True),
    sa.Column('oldest_mtime', sa.DateTime(), nullable=True),
    sa.Column('incomplete', sa.BigInteger(), nullable=False),
    sa.Column('age_bytes', sa.JSON(), nullable=False),
    sa.Column('cat_bytes', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['filesystem_id'], ['filesystem.id'], name='fk_folder_stat_filesystem_id_filesystem'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['snapshot.id'], name='fk_folder_stat_snapshot_id_snapshot', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_folder_stat'),
    sa.UniqueConstraint('snapshot_id', 'folder', name='uq_folder_stat_snapshot_id_folder'),
    sa.Index('ix_folder_stat_fs_folder', 'filesystem_id', 'folder'),
)


sa.Table(
    'marked_folder', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filesystem_id', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(length=4096), nullable=False),
    sa.Column('dev', sa.BigInteger(), nullable=True),
    sa.Column('ino', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('moved_to', sa.String(length=4096), nullable=True),
    sa.Column('seen_snapshot_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['filesystem_id'], ['filesystem.id'], name='fk_marked_folder_filesystem_id_filesystem', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['seen_snapshot_id'], ['snapshot.id'], name='fk_marked_folder_seen_snapshot_id_snapshot', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name='pk_marked_folder'),
    sa.UniqueConstraint('filesystem_id', 'path', name='uq_marked_folder_filesystem_id_path'),
    sa.Index('ix_marked_folder_filesystem_id', 'filesystem_id'),
)


sa.Table(
    'cleanup_item', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lab_id', sa.Integer(), nullable=False),
    sa.Column('mark_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('target_filesystem_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('added_by', sa.Integer(), nullable=True),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.Column('baseline_snapshot_id', sa.Integer(), nullable=True),
    sa.Column('baseline_bytes', sa.BigInteger(), nullable=False),
    sa.Column('baseline_files', sa.BigInteger(), nullable=False),
    sa.Column('baseline_estimate', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['added_by'], ['user.id'], name='fk_cleanup_item_added_by_user', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['baseline_snapshot_id'], ['snapshot.id'], name='fk_cleanup_item_baseline_snapshot_id_snapshot', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lab_id'], ['lab.id'], name='fk_cleanup_item_lab_id_lab', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['mark_id'], ['marked_folder.id'], name='fk_cleanup_item_mark_id_marked_folder', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_filesystem_id'], ['filesystem.id'], name='fk_cleanup_item_target_filesystem_id_filesystem', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name='pk_cleanup_item'),
    sa.UniqueConstraint('lab_id', 'mark_id', name='uq_cleanup_item_lab_id_mark_id'),
    sa.Index('ix_cleanup_item_lab_id', 'lab_id'),
    sa.Index('ix_cleanup_item_mark_id', 'mark_id'),
)


sa.Table(
    'folder_tag', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tag_id', sa.Integer(), nullable=False),
    sa.Column('mark_id', sa.Integer(), nullable=False),
    sa.Column('value', sa.String(length=200), nullable=False),
    sa.Column('added_by', sa.Integer(), nullable=True),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['added_by'], ['user.id'], name='fk_folder_tag_added_by_user', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['mark_id'], ['marked_folder.id'], name='fk_folder_tag_mark_id_marked_folder', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tag_id'], ['tag.id'], name='fk_folder_tag_tag_id_tag', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_folder_tag'),
    sa.UniqueConstraint('tag_id', 'mark_id', 'value', name='uq_folder_tag_tag_id_mark_id_value'),
    sa.Index('ix_folder_tag_mark_id', 'mark_id'),
    sa.Index('ix_folder_tag_tag_id', 'tag_id'),
)


sa.Table(
    'marked_folder_stat', META,
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('mark_id', sa.Integer(), nullable=False),
    sa.Column('snapshot_id', sa.Integer(), nullable=False),
    sa.Column('taken_at', sa.DateTime(), nullable=False),
    sa.Column('present', sa.Boolean(), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.Column('alloc_bytes', sa.BigInteger(), nullable=False),
    sa.Column('files', sa.BigInteger(), nullable=False),
    sa.Column('dirs', sa.BigInteger(), nullable=False),
    sa.Column('cold_bytes', sa.BigInteger(), nullable=False),
    sa.Column('mean_age_days', sa.Float(), nullable=True),
    sa.Column('age_bytes', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['mark_id'], ['marked_folder.id'], name='fk_marked_folder_stat_mark_id_marked_folder', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['snapshot.id'], name='fk_marked_folder_stat_snapshot_id_snapshot', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_marked_folder_stat'),
    sa.UniqueConstraint('mark_id', 'snapshot_id', name='uq_marked_folder_stat_mark_id_snapshot_id'),
    sa.Index('ix_marked_folder_stat_mark_id', 'mark_id'),
    sa.Index('ix_marked_folder_stat_snapshot_id', 'snapshot_id'),
)


def upgrade() -> None:
    if context.is_offline_mode():  # `pinax db upgrade --sql` for a new catalog
        for table in META.sorted_tables:
            op.execute(CreateTable(table))
            for index in sorted(table.indexes, key=lambda i: i.name):
                op.execute(CreateIndex(index))
        return
    bind = op.get_bind()
    had = set(sa.inspect(bind).get_table_names())
    META.create_all(bind)  # only the tables it lacks
    prep = bind.dialect.identifier_preparer
    for table in META.sorted_tables:
        if table.name not in had:
            continue
        have = {c["name"] for c in sa.inspect(bind).get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = f"ALTER TABLE {prep.quote(table.name)} ADD COLUMN {prep.quote(col.name)} {col.type.compile(bind.dialect)}"
            if col.server_default is not None:
                arg = col.server_default.arg
                ddl += f" DEFAULT {arg if isinstance(arg, str) else arg.compile(dialect=bind.dialect)}"
            op.execute(ddl)


def downgrade() -> None:
    META.drop_all(op.get_bind())
