"""Service._repo against a real repository and connection (the base class itself is pw-foundation's)."""

from sqlalchemy import Column, Integer, MetaData, String, Table, insert, text

from pw_core.db.repository import Repository
from pw_foundation.services import Service


# --- Test doubles ---


_items = Table(
    "items", MetaData(), Column("id", Integer, primary_key=True), Column("name", String(255))
)


class ItemRepo(Repository):
    def insert(self, name: str) -> int:
        with self._tx() as conn:
            result = conn.execute(insert(_items).values(name=name))
            return result.inserted_primary_key[0]

    def get_by_name(self, name: str) -> dict | None:
        with self._tx() as conn:
            row = (
                conn.execute(
                    text("SELECT id, name FROM items WHERE name = :name"),
                    {"name": name},
                )
                .mappings()
                .fetchone()
            )
            return dict(row) if row else None


class ItemService(Service):
    def add_item(self, name: str) -> int:
        repo = self._repo(ItemRepo)
        return repo.insert(name)

    def find_item(self, name: str) -> dict | None:
        repo = self._repo(ItemRepo)
        return repo.get_by_name(name)


# --- Tests ---


class TestServiceRepo:
    def test_repo_without_conn(self, pw_tables):
        """Service with no conn — repos create their own transactions."""
        svc = ItemService()
        row_id = svc.add_item("no_conn_item")
        assert row_id is not None

        result = svc.find_item("no_conn_item")
        assert result is not None
        assert result["name"] == "no_conn_item"

    def test_repo_with_shared_conn(self, pw_tables, pw_connection):
        """Service with conn — repos share the caller's transaction."""
        svc = ItemService(conn=pw_connection)
        svc.add_item("shared_item")

        # The raw connection should see the insert (same transaction)
        row = pw_connection.execute(
            text("SELECT name FROM items WHERE name = :name"),
            {"name": "shared_item"},
        ).fetchone()
        assert row is not None

    def test_multiple_repos_share_conn(self, pw_tables, pw_connection):
        """Two repos from the same service share the connection."""
        svc = ItemService(conn=pw_connection)
        svc.add_item("multi_repo")

        # A second repo call should see the first repo's data
        result = svc.find_item("multi_repo")
        assert result is not None
