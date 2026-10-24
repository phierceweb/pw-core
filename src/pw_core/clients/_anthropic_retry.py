"""Retry predicates for the Anthropic client: which failures are safe to re-send."""

from __future__ import annotations

# An allow-list, not a 5xx range: 501/505/507 are caller or capability errors
# that fail identically on every attempt. 529 is Anthropic's "overloaded".
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})

# Backoff before retry N (0-based): _RETRY_BACKOFF_BASE * (N + 1) seconds.
_RETRY_BACKOFF_BASE = 0.5


def _timeout_left_the_client(exc: Exception) -> bool:
    """True when an SDK timeout happened after the request was on the wire.

    A read timeout can leave a completion generated — and billed — server
    side; re-sending pays for the same prompt twice.
    """
    import httpx

    return not isinstance(exc.__cause__, (httpx.ConnectTimeout, httpx.PoolTimeout))


def _is_retryable(exc: Exception) -> bool:
    """Whether re-sending the request can plausibly succeed without paying twice."""
    from anthropic import APIConnectionError, APIStatusError, APITimeoutError

    if isinstance(exc, APITimeoutError):
        return not _timeout_left_the_client(exc)
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in _RETRYABLE_STATUS_CODES
    return True  # unclassifiable (non-SDK) failure — assume transient
