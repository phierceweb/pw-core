"""Parser failure shapes shared by all consumer parsers.

Two exception types form the contract between per-source parser modules
and the consumer's ingest orchestrator. Individual parsers may subclass
them for source-specific signals; orchestrators only ever catch these
two (and a generic ``Exception`` fallback).

The split exists so paywall skips do not count as parser failures:
:class:`ParseError` is a failure; :class:`PaywalledPost` is a soft
signal that a post is paid-only and should be skipped.
"""

from __future__ import annotations

from pw_foundation.exceptions import AppError


class ParseError(AppError):
    """Unrecoverable parser failure (network, shape mismatch, empty body).

    Orchestrator callers catch this and skip the source for this run
    while logging a WARNING; a single source failing does not fail the
    whole ingest.
    """


class PaywalledPost(AppError):
    """Soft signal from a parser's fetch: this post is paid-only and should
    be skipped without counting as a parser error.

    Orchestrator catches per-post, counts the skip apart from failures,
    and logs at WARNING.
    """
