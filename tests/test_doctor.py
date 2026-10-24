"""pw-core's doctor rows: extras, env, router, deps and the --db group, and their place in pw-doctor."""

from __future__ import annotations

import sqlite3
import subprocess

import pytest

from pw_core import _env_compat, doctor
from pw_core.doctor import check_deps, check_env, check_extras, check_router, checks, db_checks
from pw_core.llm import _router_loader


@pytest.fixture(autouse=True)
def _router_cache_reset():
    _router_loader.clear_cache()
    yield
    _router_loader.clear_cache()


@pytest.fixture
def _fresh_legacy_warnings():
    _env_compat._warned.clear()
    yield
    _env_compat._warned.clear()


class TestCheckExtras:
    def test_lists_installed_extras(self):
        (res,) = check_extras()
        assert res.status == "PASS"
        # dev env has pydantic + sqlalchemy installed
        assert "validate" in res.detail
        assert "db" in res.detail


class TestCheckEnv:
    def test_the_row_is_env_pw_core(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (res,) = check_env()
        assert (res.group, res.name) == ("env", "pw-core")

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

    @pytest.mark.usefixtures("_fresh_legacy_warnings")
    def test_names_the_legacy_variable_and_warns(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PW_VERIFY_TLS", raising=False)
        monkeypatch.setenv("PF_VERIFY_TLS", "0")
        with pytest.warns(FutureWarning, match="PF_VERIFY_TLS"):
            (res,) = check_env()
        assert res.status == "WARN"
        assert "PW_VERIFY_TLS=0 (read from PF_VERIFY_TLS, deprecated)" in res.detail

    @pytest.mark.usefixtures("_fresh_legacy_warnings")
    def test_names_an_empty_legacy_variable(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PW_VERIFY_TLS", raising=False)
        monkeypatch.setenv("PF_VERIFY_TLS", "")
        with pytest.warns(FutureWarning, match="PF_VERIFY_TLS"):
            (res,) = check_env()
        assert res.status == "WARN"
        assert "; PW_VERIFY_TLS= (read from PF_VERIFY_TLS, deprecated);" in res.detail
        assert "PW_VERIFY_TLS unset" not in res.detail

    @pytest.mark.usefixtures("_fresh_legacy_warnings")
    def test_an_empty_pw_name_hiding_a_legacy_value_warns(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PW_VERIFY_TLS", "")
        monkeypatch.setenv("PF_VERIFY_TLS", "0")
        (res,) = check_env()
        assert res.status == "WARN"
        assert "PW_VERIFY_TLS= (set empty, PF_VERIFY_TLS ignored)" in res.detail.split("; ")
        assert "PW_VERIFY_TLS unset" not in res.detail

    @pytest.mark.parametrize("legacy", [None, ""], ids=["legacy-unset", "legacy-empty"])
    def test_an_empty_pw_name_shows_as_set_empty(self, monkeypatch, tmp_path, legacy):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PW_VERIFY_TLS", "")
        if legacy is None:
            monkeypatch.delenv("PF_VERIFY_TLS", raising=False)
        else:
            monkeypatch.setenv("PF_VERIFY_TLS", legacy)
        (res,) = check_env()
        assert res.status == "PASS"
        assert "PW_VERIFY_TLS= (set empty)" in res.detail.split("; ")
        assert "PW_VERIFY_TLS unset" not in res.detail and "ignored" not in res.detail

    @pytest.mark.parametrize("name", ["DATABASE_URL", "REDIS_URL", "PW_VERIFY_TLS"])
    def test_any_listed_variable_set_empty_shows_as_set_empty(self, monkeypatch, tmp_path, name):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PF_VERIFY_TLS", raising=False)
        monkeypatch.delenv("WEB_HOST", raising=False)
        monkeypatch.setenv(name, "")
        (res,) = check_env()
        rows = res.detail.split("; ")
        assert res.status == "PASS"
        assert f"{name}= (set empty)" in rows
        assert f"{name} unset" not in rows
        assert "WEB_HOST unset" in rows

    def test_an_unset_pw_name_is_unset(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PW_VERIFY_TLS", raising=False)
        monkeypatch.delenv("PF_VERIFY_TLS", raising=False)
        (res,) = check_env()
        assert "PW_VERIFY_TLS unset" in res.detail.split("; ")

    def test_pw_name_set_means_no_legacy_note(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PW_VERIFY_TLS", "1")
        monkeypatch.setenv("PF_VERIFY_TLS", "0")
        (res,) = check_env()
        assert res.status == "PASS"
        assert "PW_VERIFY_TLS=1" in res.detail and "read from" not in res.detail


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
