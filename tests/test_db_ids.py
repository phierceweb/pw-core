"""Tests for pw_core.db.ids — collision-safe allocation (generation is pw-foundation's)."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from pw_foundation.exceptions import InvalidInputError, PreconditionError
from pw_core.db.ids import allocate_id


class TestAllocateIdIdentifierValidation:
    """table/column are interpolated into SQL, so they must be plain identifiers."""

    def test_rejects_bad_table(self):
        with pytest.raises(InvalidInputError):
            allocate_id(None, table="users; DROP TABLE x")

    def test_rejects_bad_column(self):
        with pytest.raises(InvalidInputError):
            allocate_id(None, table="widgets", column="id) OR 1=1--")

    def test_rejects_empty_table(self):
        with pytest.raises(InvalidInputError):
            allocate_id(None, table="")


_WIDGETS_TABLE = "CREATE TABLE widgets (id VARCHAR(64) PRIMARY KEY, name TEXT)"


class TestAllocateId:
    @pytest.mark.pw_tables(_WIDGETS_TABLE)
    def test_generates_unique_id(self, pw_tables, pw_connection):
        result = allocate_id(pw_connection, table="widgets")
        assert len(result) == 12
        assert re.match(r"^[A-Za-z0-9_-]+$", result)

    @pytest.mark.pw_tables(_WIDGETS_TABLE)
    def test_preferred_id_used_when_available(self, pw_tables, pw_connection):
        result = allocate_id(pw_connection, table="widgets", preferred="my-custom-id")
        assert result == "my-custom-id"

    @pytest.mark.pw_tables(_WIDGETS_TABLE)
    def test_preferred_id_skipped_when_taken(self, pw_tables, pw_connection):
        pw_connection.execute(
            text("INSERT INTO widgets (id, name) VALUES (:id, :name)"),
            {"id": "taken-id", "name": "existing"},
        )
        result = allocate_id(pw_connection, table="widgets", preferred="taken-id")
        assert result != "taken-id"
        assert len(result) == 12

    @pytest.mark.pw_tables(_WIDGETS_TABLE)
    def test_preferred_none_generates(self, pw_tables, pw_connection):
        result = allocate_id(pw_connection, table="widgets", preferred=None)
        assert len(result) == 12

    @pytest.mark.pw_tables(_WIDGETS_TABLE)
    def test_preferred_empty_generates(self, pw_tables, pw_connection):
        result = allocate_id(pw_connection, table="widgets", preferred="  ")
        assert len(result) == 12

    @pytest.mark.pw_tables(_WIDGETS_TABLE)
    def test_custom_column(self, pw_tables, pw_connection):
        pw_connection.execute(
            text("INSERT INTO widgets (id, name) VALUES (:id, :name)"),
            {"id": "x", "name": "test-name"},
        )
        result = allocate_id(pw_connection, table="widgets", column="name", preferred="test-name")
        assert result != "test-name"

    @pytest.mark.pw_tables(_WIDGETS_TABLE)
    def test_custom_size(self, pw_tables, pw_connection):
        result = allocate_id(pw_connection, table="widgets", size=20)
        assert len(result) == 20

    @pytest.mark.pw_tables(_WIDGETS_TABLE)
    def test_raises_after_max_attempts(self, pw_tables, pw_connection, monkeypatch):
        # Insert an item, then make generate_id always return that same ID
        pw_connection.execute(
            text("INSERT INTO widgets (id, name) VALUES (:id, :name)"),
            {"id": "collision", "name": "x"},
        )
        monkeypatch.setattr("pw_core.db.ids.generate_id", lambda size=None: "collision")
        with pytest.raises(PreconditionError, match="Could not allocate"):
            allocate_id(pw_connection, table="widgets", max_attempts=3)
