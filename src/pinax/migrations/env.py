# SPDX-License-Identifier: Apache-2.0
"""Alembic's environment for the catalog, run by migrate.py: with a connection it changes the catalog (in the
caller's transaction); with only a URL it writes the SQL instead (`pinax db upgrade --sql`). New revisions are
written in batch mode, which SQLite needs to change a table and PostgreSQL runs as plain ALTERs, so a revision
written against either database runs on both."""

from alembic import context

from pinax import models  # noqa: F401 - register tables
from pinax.db import Base

config = context.config
connection = config.attributes.get("connection")
if connection is not None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True, render_as_batch=True)
else:
    context.configure(url=config.attributes["url"], target_metadata=Base.metadata, literal_binds=True,
                      render_as_batch=config.attributes["url"].get_backend_name() == "sqlite")
with context.begin_transaction():
    context.run_migrations()
