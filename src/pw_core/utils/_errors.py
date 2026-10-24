"""Exception text for logs and records that cannot itself raise."""

from __future__ import annotations


def error_text(exc: BaseException, limit: int) -> str:
    """``"<Type>: <message>"`` clipped to ``limit``.

    The type name alone when the message is empty or ``str(exc)`` raises.
    """
    name = type(exc).__name__
    try:
        message = str(exc)
    except Exception:
        message = ""
    return (f"{name}: {message}" if message else name)[:limit]
