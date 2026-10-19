"""pw-core's doctor rows: extras, env, router, deps and the --db group, and their place in pw-doctor."""

from __future__ import annotations

import sqlite3
import subprocess

import pytest

from pw_core import doctor
from pw_core.doctor import check_deps, check_env, check_extras, check_router, checks, db_checks
from pw_core.llm import _router_loader


@pytest.fixture(autouse=True)
def _router_cache_reset():
    _router_loader.clear_cache()
    yield
    _router_loader.clear_cache()


class TestCheckExtras:
    def test_lists_installed_extras(self):
        (res,) = check_extras()
        assert res.status == "PASS"
        # dev env has pydantic + sqlalchemy installed
        assert "validate" in res.detail
        assert "db" in res.detail


class TestCheckEnv:
    def test_reports_set_and_unset_with_redaction(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # no .env here
        monkeypatch.setenv("DATABASE_URL", "mysql://u:pw@h:3306/d")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
        monkeypatch.delenv("REDIS_URL", raising=False)
        (res,) = check_env()
        assert res.status == "PASS"
        assert "pw" not in res.detail
        assert "sk-ant-xyz" not in res.detail
        assert "DATABASE_URL" in res.detail
        assert "REDIS_URL" in res.detail  # listed as unset
        assert "no .env" in res.detail

    def test_loads_cwd_dotenv_for_effective_env(self, monkeypatch, tmp_path):
        (tmp_path / ".env").write_text("WEB_HOST=TRACE_TEST\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WEB_HOST", raising=False)
        (res,) = check_env()
        assert "WEB_HOST=TRACE_TEST" in res.detail
        assert ".env loaded from" in res.detail

    def test_shell_value_wins_over_dotenv(self, monkeypatch, tmp_path):
        (tmp_path / ".env").write_text("WEB_HOST=FROM_FILE\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("WEB_HOST", "FROM_SHELL")
        (res,) = check_env()
        assert "WEB_HOST=FROM_SHELL" in res.detail


class TestCheckRouter:
    def test_skip_when_config_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MODEL_ROUTER_CONFIG", str(tmp_path / "nope.yaml"))
        (res,) = check_router()
        assert res.status == "SKIP"

    def test_pass_lists_agents(self, tmp_path, monkeypatch):
        cfg = tmp_path / "router.yaml"
        cfg.write_text("agents:\n  summarizer:\n    model: m1\n  classifier:\n    model: m2\n")
        monkeypatch.setenv("MODEL_ROUTER_CONFIG", str(cfg))
        (res,) = check_router()
        assert res.status == "PASS"
        assert "summarizer" in res.detail
        assert "classifier" in res.detail

    def test_fail_on_invalid_schema(self, tmp_path, monkeypatch):
        cfg = tmp_path / "router.yaml"
        cfg.write_text("agents:\n  broken: {}\n")
        monkeypatch.setenv("MODEL_ROUTER_CONFIG", str(cfg))
        (res,) = check_router()
        assert res.status == "FAIL"


class TestCheckDeps:
    def test_reports_installed_versions(self):
        (res,) = check_deps()
        assert res.status == "PASS"
        assert "pydantic" in res.detail


# ---------------------------------------------------------------------------
# --db group
# ---------------------------------------------------------------------------


class TestDbChecks:
    def test_missing_url_fails(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        results = db_checks()
        assert results[0].status == "FAIL"
        assert "DATABASE_URL" in results[0].detail

    def test_sqlite_missing_file_fails_without_creating(self, tmp_path, monkeypatch):
        db_file = tmp_path / "absent.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
        results = db_checks()
        connect = next(r for r in results if r.name == "connect")
        assert connect.status == "FAIL"
        assert not db_file.exists()  # read-only invariant: no file created

    def test_network_probe_uses_the_shared_connect_args(self, monkeypatch):
        import sqlalchemy

        monkeypatch.delenv("DB_CONNECT_TIMEOUT_S", raising=False)
        monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@127.0.0.1:3306/db")
        captured: dict = {}

        def fake_create_engine(_url, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("no connect")

        monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
        db_checks()
        assert captured["connect_args"] == {"connect_timeout": 5}

    def test_network_probe_defers_to_a_url_that_sets_the_timeout(self, monkeypatch):
        import sqlalchemy

        monkeypatch.setenv(
            "DATABASE_URL", "mysql+pymysql://u:p@127.0.0.1:3306/db?connect_timeout=45"
        )
        captured: dict = {}

        def fake_create_engine(_url, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("no connect")

        monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
        db_checks()
        assert captured["connect_args"] == {}

    def test_no_second_connect_args_helper(self):
        assert not hasattr(doctor, "_connect_args")

    def test_sqlite_existing_file_connects(self, tmp_path, monkeypatch):
        db_file = tmp_path / "real.db"
        sqlite3.connect(db_file).close()
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
        monkeypatch.chdir(tmp_path)  # no alembic/ here
        results = db_checks()
        by_name = {r.name: r for r in results}
        assert by_name["url"].status == "PASS"
        assert by_name["connect"].status == "PASS"
        assert by_name["migrations"].status == "SKIP"


# ---------------------------------------------------------------------------
# Runner + CLI
# ---------------------------------------------------------------------------


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _git_repo_with_release(tmp_path, *, version="1.0.0", changelog="1.0.0", tag=None):
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{version}"\n')
    if changelog is not None:
        (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n## v{changelog} — 2026-07-02\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "release")
    if tag:
        _git(tmp_path, "tag", tag)
    return tmp_path


class TestChecks:
    def test_core_groups_without_db(self, monkeypatch):
        monkeypatch.delenv("MODEL_ROUTER_CONFIG", raising=False)
        groups = {r.group for r in checks()}
        assert {"extras", "env", "router", "deps"} <= groups
        assert "db" not in groups

    def test_db_group_when_asked(self, tmp_path, monkeypatch):
        db_file = tmp_path / "x.db"
        sqlite3.connect(db_file).close()
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
        monkeypatch.chdir(tmp_path)
        assert any(r.group == "db" for r in checks(db=True))

    def test_unknown_options_from_pw_doctor_are_ignored(self):
        assert checks(release=True, future_flag=1)

    def test_pw_doctor_includes_the_core_rows(self, monkeypatch):
        from pw_foundation.doctor import run_checks

        monkeypatch.delenv("MODEL_ROUTER_CONFIG", raising=False)
        groups = {r.group for r in run_checks()}
        assert {"extras", "router", "deps"} <= groups
        assert "plugin" not in groups, [r.detail for r in run_checks() if r.group == "plugin"]


def test_pw_doctor_prints_each_row_name_once():
    """pw-core's rows join pw-foundation's under names of their own."""
    from collections import Counter

    from pw_foundation.doctor import run_checks

    counts = Counter((r.group, r.name) for r in run_checks())
    assert [k for k, n in counts.items() if n > 1] == []
