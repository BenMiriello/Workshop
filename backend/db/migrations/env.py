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

_url = os.environ.get("WORKSHOP_DATABASE_URL", "").strip()
if not _url:
    raise RuntimeError(
        "WORKSHOP_DATABASE_URL is not set. Source it before running alembic:\n"
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
