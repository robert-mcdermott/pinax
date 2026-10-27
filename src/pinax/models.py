# SPDX-License-Identifier: Apache-2.0
"""Catalog schema: people, labs, folder ownership, filesystems, snapshots, and folder history.

People, labs, memberships, and folder mappings use the same shape as Tundra's, so both systems can
share one directory of who owns what. Per-file data never lives here; it lives in each snapshot's
index (see ingest/build.py and index.py).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
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

    memberships: Mapped[list["LabMembership"]] = relationship(back_populates="user", cascade="all, delete-orphan")


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

