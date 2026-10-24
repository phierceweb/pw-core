"""pw_core.utils._errors.error_text — exception text for logs and records that cannot raise."""

from __future__ import annotations

import pytest

from pw_core.utils._errors import error_text


class _Unprintable(Exception):
    def __str__(self) -> str:
        raise RuntimeError("no text")


def test_a_long_message_is_clipped_to_exactly_the_limit() -> None:
    text = error_text(ValueError("x" * 2000), 500)
    assert len(text) == 500
    assert text == ("ValueError: " + "x" * 2000)[:500]


def test_a_short_message_is_returned_whole() -> None:
    assert error_text(ValueError("bad value"), 500) == "ValueError: bad value"


def test_the_type_name_alone_when_str_raises() -> None:
    assert error_text(_Unprintable(), 500) == "_Unprintable"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ValueError(), "ValueError"),
        (ValueError(""), "ValueError"),
        (ValueError(0), "ValueError: 0"),
    ],
    ids=["no-args", "empty-string", "falsy-but-not-empty"],
)
def test_an_empty_message_leaves_the_type_name_alone(exc: Exception, expected: str) -> None:
    assert error_text(exc, 500) == expected
