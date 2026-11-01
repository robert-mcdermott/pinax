# SPDX-License-Identifier: Apache-2.0
"""Catalog schema: people, labs, folder ownership, filesystems, snapshots, and folder history.

People, labs, memberships, and folder mappings use the same shape as Tundra's, so both systems can
share one directory of who owns what. Per-file data never lives here; it lives in each snapshot's
index (see ingest/build.py and index.py).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class LabKind:
    LAB = "lab"
    DEPARTMENT = "department"

    ALL = (LAB, DEPARTMENT)


class Lab(Base):
    """A lab or department: the unit that owns top-level folders and whose members may view them."""

    __tablename__ = "lab"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)  # stable short ID used by the API
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default=LabKind.LAB)
    division: Mapped[str] = mapped_column(String(200), default="")
    research_area: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    memberships: Mapped[list["LabMembership"]] = relationship(back_populates="lab", cascade="all, delete-orphan")
    folders: Mapped[list["LabFolder"]] = relationship(
        back_populates="lab", cascade="all, delete-orphan", order_by="LabFolder.path"
    )

    @property
    def pi_name(self) -> str:
        """The PI (for a lab) or owner (for a department), from the membership table."""
        names = [m.user.display_name for m in self.memberships if m.role == "pi" and m.user.active]
        return ", ".join(sorted(names))


class Filesystem(Base):
    """A file system (or one export of it) that is scanned as a unit: its root holds the top-level folders."""

    __tablename__ = "filesystem"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    root: Mapped[str] = mapped_column(String(2048))  # absolute path of the scan root
    # How paths are shown: the root as people know it, when the scan host mounts it elsewhere.
    display_root: Mapped[str] = mapped_column(String(2048), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="filesystem", order_by="Snapshot.started_at")


class Price(Base):
    """What a filesystem's storage costs per month, from a day on, until the next price (costs.py). Kept, so past
    months keep the prices they had. The amount is in millionths of the currency, so money is exact."""

    __tablename__ = "price"
    __table_args__ = (UniqueConstraint("filesystem_id", "starts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filesystem_id: Mapped[int] = mapped_column(ForeignKey("filesystem.id", ondelete="CASCADE"), index=True)
    starts: Mapped[date] = mapped_column(Date)
    micros: Mapped[int] = mapped_column(BigInteger)  # per `unit` per month, in millionths of the currency
    unit: Mapped[str] = mapped_column(String(8))  # GB, GiB, TB, or TiB
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    filesystem: Mapped[Filesystem] = relationship()


class FreeTier(Base):
    """How much storage on a filesystem is free of charge, from a day on: for every lab, or every department (`kind`),
    or for one lab or department (`lab_id`), whose own tier takes the place of its kind's until it ends (`bytes`
    None). Kept, like prices."""

    __tablename__ = "free_tier"
    __table_args__ = (UniqueConstraint("filesystem_id", "kind", "lab_id", "starts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filesystem_id: Mapped[int] = mapped_column(ForeignKey("filesystem.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)  # LabKind, for a default
    lab_id: Mapped[int | None] = mapped_column(ForeignKey("lab.id", ondelete="CASCADE"), nullable=True)  # an exception
    starts: Mapped[date] = mapped_column(Date)
    bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # None: an exception ends
    unit: Mapped[str] = mapped_column(String(8), default="TB")  # how the amount was given, to show it the same way
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    filesystem: Mapped[Filesystem] = relationship()
    lab: Mapped["Lab | None"] = relationship()


class Capacity(Base):
    """How much a filesystem holds, from a day on, as an administrator set it (storage expanded, or an expansion
    scheduled). It takes the place of what the storage reports from that day. Kept, like prices."""

    __tablename__ = "capacity"
    __table_args__ = (UniqueConstraint("filesystem_id", "starts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filesystem_id: Mapped[int] = mapped_column(ForeignKey("filesystem.id", ondelete="CASCADE"), index=True)
    starts: Mapped[date] = mapped_column(Date)
    bytes: Mapped[int] = mapped_column(BigInteger)
    unit: Mapped[str] = mapped_column(String(8), default="TB")  # how the amount was given, to show it the same way
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LabFolder(Base):
    """A top-level folder of a filesystem, owned by exactly one lab or department."""

    __tablename__ = "lab_folder"
    __table_args__ = (UniqueConstraint("filesystem_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_id: Mapped[int] = mapped_column(ForeignKey("lab.id"))
    filesystem_id: Mapped[int] = mapped_column(ForeignKey("filesystem.id"))
    path: Mapped[str] = mapped_column(String(1024))  # a single folder name directly under the filesystem root

    lab: Mapped[Lab] = relationship(back_populates="folders")
    filesystem: Mapped[Filesystem] = relationship()


class FolderOwner(Base):
    """Who owned a top-level folder from a moment on (`lab_id` None: no one), so charges for past months follow the
    owners they had (ownership.py). Written when a folder's owner changes; a folder never changed has no rows and
    belongs to its current owner (LabFolder) all along."""

    __tablename__ = "folder_owner"

    id: Mapped[int] = mapped_column(primary_key=True)
    filesystem_id: Mapped[int] = mapped_column(ForeignKey("filesystem.id", ondelete="CASCADE"), index=True)
    folder: Mapped[str] = mapped_column(String(1024))
    lab_id: Mapped[int | None] = mapped_column(ForeignKey("lab.id", ondelete="SET NULL"), nullable=True)
    starts: Mapped[datetime] = mapped_column(DateTime)


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # IT: sees every filesystem and folder
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Local sign-in (PINAX_AUTH_MODE=local) only; unused behind single sign-on. The hash names its algorithm and
    # parameters (signin.py). A password set by an administrator must be changed at the next sign-in.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    memberships: Mapped[list["LabMembership"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    roles: Mapped[list["UserRole"]] = relationship(back_populates="user", cascade="all, delete-orphan")


SITE_ROLES = ("auditor", "finance", "division")


class UserRole(Base):
    """A site role a person has beside their lab memberships (auth.py). An auditor sees everything a storage
    administrator does and changes nothing; finance sees every lab's charges and the prices behind them; a division
    head sees the labs of one division (`division`, matching labs' own), as their members do."""

    __tablename__ = "user_role"
    __table_args__ = (UniqueConstraint("user_id", "role", "division"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))  # SITE_ROLES
    division: Mapped[str] = mapped_column(String(200), default="", server_default="")
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="roles")


class LoginSession(Base):
    """A signed-in browser, for local sign-in. The cookie holds a random token; only its SHA-256 is stored, so the
    table cannot be used to sign in. Ended by signing out, by expiry, and when the person is deactivated or their
    password is set or changed."""

    __tablename__ = "login_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    address: Mapped[str] = mapped_column(String(64), default="")

    user: Mapped[User] = relationship()


class LabMembership(Base):
    __tablename__ = "lab_membership"
    __table_args__ = (UniqueConstraint("user_id", "lab_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    lab_id: Mapped[int] = mapped_column(ForeignKey("lab.id"))
    role: Mapped[str] = mapped_column(String(32))  # "pi" (PI or department owner) or "delegate"

    user: Mapped[User] = relationship(back_populates="memberships")
    lab: Mapped[Lab] = relationship(back_populates="memberships")


class SnapshotStatus:
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    PRUNED = "pruned"  # index deleted by retention; folder history kept
    SKIPPED = "skipped"  # found by automatic ingest but not loaded (an incomplete or failed scan); see message


class Snapshot(Base):
    """One fs-inventory scan of a filesystem, and the index Pinax built from it."""

    __tablename__ = "snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    filesystem_id: Mapped[int] = mapped_column(ForeignKey("filesystem.id"), index=True)
    scan_id: Mapped[str] = mapped_column(String(64), unique=True)
    scan_dir: Mapped[str] = mapped_column(String(2048))
    index_dir: Mapped[str] = mapped_column(String(2048), default="")
    status: Mapped[str] = mapped_column(String(32), default=SnapshotStatus.BUILDING, index=True)
    message: Mapped[str] = mapped_column(Text, default="")

    # When the scan ran. Ages are measured from started_at, so a report says "as of" the scan.
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scan_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    scan_status: Mapped[str] = mapped_column(String(32), default="complete")
    tool_version: Mapped[str] = mapped_column(String(32), default="")
    host: Mapped[str] = mapped_column(String(200), default="")

    ingested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    build_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    entries: Mapped[int] = mapped_column(BigInteger, default=0)
    files: Mapped[int] = mapped_column(BigInteger, default=0)
    dirs: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    alloc_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    errors: Mapped[int] = mapped_column(BigInteger, default=0)
    index_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # buckets, categories, build stats

    filesystem: Mapped[Filesystem] = relationship(back_populates="snapshots")


class FolderStat(Base):
    """Totals for one top-level folder in one snapshot. Kept after the snapshot's index is pruned,
    so folder growth and ageing can be charted over years of scans."""

    __tablename__ = "folder_stat"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "folder"),
        Index("ix_folder_stat_fs_folder", "filesystem_id", "folder"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id", ondelete="CASCADE"))
    filesystem_id: Mapped[int] = mapped_column(ForeignKey("filesystem.id"))
    folder: Mapped[str] = mapped_column(String(1024))  # top-level folder name ("" for files in the root itself)
    taken_at: Mapped[datetime] = mapped_column(DateTime)  # the snapshot's started_at, for charting

    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    alloc_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    files: Mapped[int] = mapped_column(BigInteger, default=0)
    dirs: Mapped[int] = mapped_column(BigInteger, default=0)
    cold_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mean_age_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    newest_mtime: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    oldest_mtime: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    incomplete: Mapped[int] = mapped_column(BigInteger, default=0)
    age_bytes: Mapped[list[int]] = mapped_column(JSON, default=list)  # bytes per age bucket (mtime)
    cat_bytes: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)  # bytes per file category


class MarkedFolder(Base):
    """A folder someone marked, for a cleanup plan or a tag, followed from scan to scan (marks.py): at its path
    (`here`), at another path with the same inode, offered as a move (`moved`, to `moved_to`), or `gone`."""

    __tablename__ = "marked_folder"
    __table_args__ = (UniqueConstraint("filesystem_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filesystem_id: Mapped[int] = mapped_column(ForeignKey("filesystem.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(4096))  # as the index has it
    dev: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ino: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="here")
    moved_to: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    seen_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("snapshot.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    filesystem: Mapped[Filesystem] = relationship()


class MarkedFolderStat(Base):
    """A marked folder's totals in one scan, recorded at ingest, so its history outlasts the snapshot's index."""

    __tablename__ = "marked_folder_stat"
    __table_args__ = (UniqueConstraint("mark_id", "snapshot_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mark_id: Mapped[int] = mapped_column(ForeignKey("marked_folder.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime)
    present: Mapped[bool] = mapped_column(Boolean, default=True)  # at its path in this scan
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    alloc_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    files: Mapped[int] = mapped_column(BigInteger, default=0)
    dirs: Mapped[int] = mapped_column(BigInteger, default=0)
    cold_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mean_age_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    age_bytes: Mapped[list[int]] = mapped_column(JSON, default=list)


class CleanupItem(Base):
    """A folder in a lab's cleanup plan (plans.py): what the lab means to do with it (`action`: delete, move to
    another filesystem, or compress), with a note, who added it, and its size then. Pinax does none of it; later
    scans show what happened."""

    __tablename__ = "cleanup_item"
    __table_args__ = (UniqueConstraint("lab_id", "mark_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_id: Mapped[int] = mapped_column(ForeignKey("lab.id", ondelete="CASCADE"), index=True)
    mark_id: Mapped[int] = mapped_column(ForeignKey("marked_folder.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(16))
    target_filesystem_id: Mapped[int | None] = mapped_column(ForeignKey("filesystem.id", ondelete="SET NULL"),
                                                             nullable=True)  # where a move goes
    note: Mapped[str] = mapped_column(Text, default="")
    added_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    baseline_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("snapshot.id", ondelete="SET NULL"),
                                                             nullable=True)
    baseline_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # its size when it was added
    baseline_files: Mapped[int] = mapped_column(BigInteger, default=0)
    baseline_estimate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # compression's, when added

    lab: Mapped["Lab"] = relationship()
    mark: Mapped[MarkedFolder] = relationship()


class Tag(Base):
    """A tag folders can have (tags.py): a standard one, kept by administrators, or a lab's own (`lab_id`). Its
    type says what a value is (`flag`: none; `text`; `date`; `choice`, one of `choices`); `many` lets a folder have
    more than one value; `applied_by` says who may tag (`owners`: a lab's PI and delegates, and administrators; or
    `admins`); `protects` keeps a folder out of cleanup plans (a date tag until its date). A tag in use is retired,
    not deleted."""

    __tablename__ = "tag"
    __table_args__ = (UniqueConstraint("lab_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    lab_id: Mapped[int | None] = mapped_column(ForeignKey("lab.id", ondelete="CASCADE"), nullable=True, index=True)
    value_type: Mapped[str] = mapped_column(String(16), default="flag")
    choices: Mapped[list[str]] = mapped_column(JSON, default=list)
    many: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_by: Mapped[str] = mapped_column(String(16), default="owners")
    protects: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lab: Mapped["Lab | None"] = relationship()


class FolderTag(Base):
    """A tag on a marked folder (and so on everything below it), with its value."""

    __tablename__ = "folder_tag"
    __table_args__ = (UniqueConstraint("tag_id", "mark_id", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"), index=True)
    mark_id: Mapped[int] = mapped_column(ForeignKey("marked_folder.id", ondelete="CASCADE"), index=True)
    value: Mapped[str] = mapped_column(String(200), default="")
    added_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tag: Mapped[Tag] = relationship()
    mark: Mapped[MarkedFolder] = relationship()


class SavedView(Base):
    """A page someone saved (views.py): the app's address of a report with its place and settings, under a name,
    kept to themselves or shared with one of their labs."""

    __tablename__ = "saved_view"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(2000))  # the part after the app's #
    lab_id: Mapped[int | None] = mapped_column(ForeignKey("lab.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship()
    lab: Mapped["Lab | None"] = relationship()


class SearchJob(Base):
    """A file search run in the background (jobs.py): what was asked (`request`), its state (`queued`, `running`,
    `done`, `failed`, `stopped`), and how many files it found; its results are a Parquet file under VAR_ROOT/jobs."""

    __tablename__ = "search_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship()


class Heartbeat(Base):
    """The latest word from a long-running process, such as `pinax ingest --watch`, so the interface can say
    whether it is running."""

    __tablename__ = "heartbeat"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    actor: Mapped[User | None] = relationship()

