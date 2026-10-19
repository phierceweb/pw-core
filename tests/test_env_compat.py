"""``PW_*`` env vars read with their ``PF_*`` spelling honored until 1.0."""

from __future__ import annotations

import warnings

import pytest

from pw_core import _env_compat


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("PW_VERIFY_TLS", raising=False)
    monkeypatch.delenv("PF_VERIFY_TLS", raising=False)
    _env_compat._warned.clear()


def test_pw_name_wins(monkeypatch):
    monkeypatch.setenv("PW_VERIFY_TLS", "0")
    monkeypatch.setenv("PF_VERIFY_TLS", "1")
    assert _env_compat.getenv("PW_VERIFY_TLS") == "0"


def test_legacy_name_is_honored_with_one_warning(monkeypatch):
    monkeypatch.setenv("PF_VERIFY_TLS", "0")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _env_compat.getenv("PW_VERIFY_TLS") == "0"
        assert _env_compat.getenv("PW_VERIFY_TLS") == "0"
    msgs = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(msgs) == 1 and "PF_VERIFY_TLS" in msgs[0] and "PW_VERIFY_TLS" in msgs[0]


def test_unset_returns_default():
    assert _env_compat.getenv("PW_VERIFY_TLS", "x") == "x"


def test_env_name_for_resolvers(monkeypatch):
    monkeypatch.setenv("PF_VERIFY_TLS", "0")
    assert _env_compat.env_name("PW_VERIFY_TLS") == "PF_VERIFY_TLS"


def test_names_outside_the_pw_prefix_pass_through(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    assert _env_compat.env_name("DATABASE_URL") == "DATABASE_URL"
    assert _env_compat.getenv("DATABASE_URL") == "sqlite://"
