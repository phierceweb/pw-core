"""Shared test fixtures — uses pw_core.testing plugins.

The pw_engine, pw_connection, and pw_tables fixtures come from
pw_core.testing.db_fixtures (opt-in plugin, requires the ``[db]`` extra).
The base plugin pw_core.testing.fixtures (auto-registered via pytest11
entry point) provides pw_app_client only.

This conftest defines the project-level schema that pw_tables will create,
compiled for whichever backend ``PW_TEST_DATABASE_URL`` selects.
"""

from __future__ import annotations

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
)

# DB fixtures are opt-in. pw-core's own tests use them, so we explicitly
# load the DB plugin here. Consumers without the [db] extra don't need this.
pytest_plugins = ["pw_core.testing.db_fixtures", "pytester"]

_metadata = MetaData()

Table(
    "items",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("data", Text),
    Column("status", String(32), server_default="active"),
    Column("created_at", DateTime, server_default=func.now()),
)
Table(
    "models",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False, unique=True),
)
Table(
    "usage_events",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("prompt_tokens", Integer, server_default="0"),
    Column("completion_tokens", Integer, server_default="0"),
    Column("cost_usd", Float, server_default="0"),
    Column("created_at", DateTime, server_default=func.now()),
)


@pytest.fixture(autouse=True)
def pw_schema():
    """Schema for pw-core's own tests.

    Consumer projects define their own pw_schema fixture with their tables.
    """
    from pw_core.testing.db_fixtures import metadata_ddl

    return metadata_ddl(_metadata)
