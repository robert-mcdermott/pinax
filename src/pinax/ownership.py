# SPDX-License-Identifier: Apache-2.0
"""Who owned each top-level folder, and when: the history charges for past months follow.

LabFolder says who owns a folder now. When that changes (on *Folders and labs*, or by `pinax import`),
`record_change` keeps the change in `folder_owner`; the first change of a folder also records who had it until
then, from the start of time. A folder never changed has no history and belongs to its current owner all along, so
a catalog from before this history existed bills as it did.
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import FolderOwner, LabFolder

SINCE_ALWAYS = datetime(1970, 1, 1)


def record_change(session: Session, filesystem_id: int, folder: str, before: int | None, after: int | None,
                  when: datetime | None = None) -> None:
    """A folder's owner changed from lab `before` to lab `after` (either None: no one) at `when` (now)."""
    if before == after:
        return
    known = session.scalar(select(FolderOwner.id).where(FolderOwner.filesystem_id == filesystem_id,
                                                        FolderOwner.folder == folder).limit(1))
    if known is None:
        session.add(FolderOwner(filesystem_id=filesystem_id, folder=folder, lab_id=before, starts=SINCE_ALWAYS))
    session.add(FolderOwner(filesystem_id=filesystem_id, folder=folder, lab_id=after, starts=when or utcnow()))


class Owners:
    """Who owned each folder of a filesystem at any moment."""

    def __init__(self, session: Session, filesystem_id: int):
        rows = session.scalars(select(FolderOwner).where(FolderOwner.filesystem_id == filesystem_id)
                               .order_by(FolderOwner.starts, FolderOwner.id)).all()
        history: dict[str, list[tuple[datetime, int | None]]] = defaultdict(list)
        for r in rows:
            history[r.folder].append((r.starts, r.lab_id))
        for f in session.scalars(select(LabFolder).where(LabFolder.filesystem_id == filesystem_id)):
            if f.path not in history:
                history[f.path].append((SINCE_ALWAYS, f.lab_id))
        self._starts = {k: [t for t, _ in v] for k, v in history.items()}
        self._labs = {k: [lab for _, lab in v] for k, v in history.items()}

    def at(self, folder: str, when: datetime) -> int | None:
        starts = self._starts.get(folder)
        if not starts:
            return None
        i = bisect_right(starts, when) - 1
        return self._labs[folder][i] if i >= 0 else None

    def changes(self) -> set[datetime]:
        """Every moment some folder changed owner."""
        return {t for starts in self._starts.values() for t in starts if t != SINCE_ALWAYS}
