"""Host encoding for :mod:`pw_core.fetch`: the standard IDNA codec, refused wherever a browser's
UTS 46 encoding could send the request to a different host."""

from __future__ import annotations

import re
import unicodedata

from pw_foundation.exceptions import InvalidInputError

# Characters UTS 46 keeps or drops where the codec maps them (ß to ss, ς to σ, the joiners, fillers).
_DEVIATIONS = re.compile(
    "[\u00df\u1e9e\u03c2\u200c\u200d\u115f\u1160\u17b4\u17b5\u1806\u3164\uffa0"
    "\U0002f868\U0002f874\U0002f91f\U0002f95f\U0002f9bf]"
)
_LABEL_DOTS = re.compile("[.。．｡]")
_DNS_NAME = re.compile(r"[a-z0-9_.-]+")


def _newer_and_unsafe(char: str) -> bool:
    """Unicode added ``char`` after the codec's 3.2 and it is not a letter, digit or symbol, which
    both encodings keep; a browser may drop or refuse any other newer character."""
    return (
        unicodedata.ucd_3_2_0.category(char) == "Cn" and unicodedata.category(char)[0] not in "LNS"
    )


def encode_host(host: str) -> str:
    """``host`` IDNA-encoded and lowercased, as a browser sends it.

    Raises:
        InvalidInputError: the host cannot be encoded, is not a DNS name once encoded (a
            compatibility character that becomes ``/``, ``@``, ``:``, ``?`` or a space), or a
            browser might send it to another host.
    """
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
        labels = ascii_host.encode("ascii").decode("idna")
    except UnicodeError as exc:
        raise InvalidInputError(f"host cannot be IDNA-encoded: {host[:100]!r}") from exc
    if not _DNS_NAME.fullmatch(ascii_host):
        raise InvalidInputError(f"host is not a DNS name once encoded: {host[:100]!r}")
    if (
        _DEVIATIONS.search(host)
        or any(_newer_and_unsafe(c) for c in host)
        or len(_LABEL_DOTS.findall(host)) != ascii_host.count(".")
        or unicodedata.normalize("NFKC", labels.casefold()) != labels
    ):
        raise InvalidInputError(f"host may not encode as a browser sends it: {host[:100]!r}")
    return ascii_host
