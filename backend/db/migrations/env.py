"""Alembic environment.

The URL comes from `WORKSHOP_DATABASE_URL`, never from alembic.ini, so the
credential lives in exactly one place (~/.config/workshop/secrets.env) and is
not committed to the repo.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# `backend/` on the path, so `import db.models` works however alembic is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MIGRATIONS RUN AS THE OWNER, NOT AS THE APPLICATION.
#
# `WORKSHOP_DATABASE_URL` is the least-privilege `workshop_app` role: it holds
# DML only and deliberately cannot execute DDL, so that a compromised
# application cannot drop an RLS policy. Running alembic with it fails with
# "permission denied for schema public", which is the separation working rather
# than a misconfiguration.
#
# The admin URL is preferred and the app URL is only a fallback, for a
# deployment that has not split the roles yet.
_url = (os.environ.get("WORKSHOP_DATABASE_ADMIN_URL", "").strip()
        or os.environ.get("WORKSHOP_DATABASE_URL", "").strip())
if not _url:
    raise RuntimeError(
        "Neither WORKSHOP_DATABASE_ADMIN_URL nor WORKSHOP_DATABASE_URL is set. "
        "Source them before running alembic:\n"
        "  set -a; . ~/.config/workshop/secrets.env; set +a"
    )
config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
