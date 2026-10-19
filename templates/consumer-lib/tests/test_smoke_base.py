"""Day-1 smoke test: the scaffolded CLI runs."""

from __future__ import annotations

import pytest

from __PKG__.cli import main


def test_hello_runs(capsys):
    assert main(["hello", "Ada"]) == 0
    assert "hello, Ada" in capsys.readouterr().out


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
