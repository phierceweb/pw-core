"""The ``PF_*`` spellings of the framework env vars are not read."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "pw_core"


@pytest.fixture
def only_pf(monkeypatch):
    def set_(pw_name: str, value: str) -> None:
        monkeypatch.delenv(pw_name, raising=False)
        monkeypatch.setenv("PF_" + pw_name[3:], value)

    return set_


def test_verify_tls(only_pf, monkeypatch):
    from pw_core.utils.http_tls import verify_tls

    monkeypatch.delenv("URL_CHECK_VERIFY_TLS", raising=False)
    only_pf("PW_VERIFY_TLS", "0")
    assert verify_tls() is True


def test_fetch_user_agent(only_pf):
    from pw_core.fetch import _UA_DEFAULT, Fetcher

    only_pf("PW_FETCH_UA", "legacy-agent/1")
    assert Fetcher()._request("https://example.com/").get_header("User-agent") == _UA_DEFAULT


def test_claude_code_model(only_pf):
    from pw_core.clients.claude_code import ClaudeCodeClient

    only_pf("PW_CORE_CLAUDE_CODE_MODEL", "opus")
    assert ClaudeCodeClient().model is None


def test_server_test_database(only_pf):
    from pw_core.testing import _server_db

    only_pf("PW_TEST_DATABASE_URL", "postgresql://legacy/db")
    only_pf("PW_TEST_LOCK_TIMEOUT_S", "99")
    assert _server_db.configured_url() == ""
    assert _server_db._lock_timeout_s() == _server_db._LOCK_TIMEOUT_S_DEFAULT


def test_no_module_builds_a_pf_name():
    """A floor: catches a literal ``PF_`` name, not one assembled from pieces."""
    builders = re.compile(r"""["']PF_""")
    hits = [
        f"{p.relative_to(SRC.parent)}:{n}"
        for p in sorted(SRC.rglob("*.py"))
        if p != SRC / "doctor.py"  # names the old spellings only to flag them
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if builders.search(line)
    ]
    assert not hits, "source builds a PF_ env name:\n" + "\n".join(hits)


def test_article_wayback_fallback(only_pf, monkeypatch):
    pytest.importorskip("trafilatura")
    from pw_core.utils import article_fetch

    calls = []
    monkeypatch.setattr(article_fetch, "_live_fetch_with_retry", lambda url: ("timeout", ""))
    monkeypatch.setattr(article_fetch, "_try_wayback", lambda url, **kw: calls.append(url))
    only_pf("PW_ARTICLE_WAYBACK_FALLBACK", "0")
    article_fetch.fetch_article("https://example.com/a")
    assert calls == ["https://example.com/a"]


def test_article_extractor_log_level(only_pf):
    import logging

    from pw_core.utils import _article_extract

    only_pf("PW_ARTICLE_EXTRACTOR_LOG_LEVEL", "DEBUG")
    _article_extract._quiet_extractor_loggers()
    assert logging.getLogger("trafilatura").level == logging.CRITICAL
