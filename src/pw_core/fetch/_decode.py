"""Bounded ``Content-Encoding`` decoding for :mod:`pw_core.fetch`.

A size cap on the wire bytes bounds nothing once the body is compressed — the
decoded output is what consumes memory. Decoding runs against the same budget
as the read, and every malformed-body failure leaves as :class:`ClientError` so
callers have one exception to catch.
"""

from __future__ import annotations

import re
import sys
import zlib
from email.message import Message

from pw_foundation.exceptions import ClientError

_GZIP_WBITS = 16 + zlib.MAX_WBITS
_RAW_DEFLATE_WBITS = -zlib.MAX_WBITS
_FIRST_SLICE = 256
_SLICE = 64 * 1024
_NOT_NUL = re.compile(rb"[^\x00]")


def _inflate(data: bytes, wbits: int, max_bytes: int | None, url: str) -> bytes:
    """Inflate buffered *data*, emitting at most *max_bytes* (``None`` = unlimited).

    Each member is fed by offset in slices doubling from 256 bytes to 64 KiB, so
    concatenated gzip members inflate in time linear in *data*, tiny ones included.

    Raises:
        ClientError: Decoded output exceeded *max_bytes*.
        zlib.error: Stream is malformed or ended early.
    """
    out = bytearray()
    view = memoryview(data)
    pos = 0
    while pos < len(data):
        obj = zlib.decompressobj(wbits=wbits)
        size = _FIRST_SLICE
        while not obj.eof:
            if pos >= len(data):
                raise zlib.error("compressed stream ended before the end-of-stream marker")
            chunk = view[pos : pos + size]
            pos += len(chunk)
            size = min(2 * size, _SLICE)
            # 0 is zlib's "unlimited" sentinel; otherwise one byte past the budget, so an
            # overrun is detectable without decoding the rest. zlib takes at most sys.maxsize.
            budget = 0 if max_bytes is None else min(max_bytes - len(out) + 1, sys.maxsize)
            out += obj.decompress(chunk, budget)
            if max_bytes is not None and len(out) > max_bytes:
                raise ClientError(
                    "decoded body exceeded max_bytes",
                    context={"url": url, "max_bytes": max_bytes},
                )
        pos -= len(obj.unused_data)
        if wbits != _GZIP_WBITS:  # only gzip concatenates members
            break
        # Matches gzip.decompress: NUL padding between members is not garbage.
        member = _NOT_NUL.search(data, pos)
        pos = member.start() if member else len(data)
    return bytes(out)


def decode_body(data: bytes, headers: Message, max_bytes: int | None, url: str) -> bytes:
    """Decode *data* per its ``Content-Encoding``; identity bodies pass through.

    Raises:
        ClientError: Body exceeded *max_bytes* once decoded, or could not be decoded.
    """
    encoding = (headers.get("Content-Encoding") or "").strip().lower()
    if encoding not in ("gzip", "deflate"):
        return data
    try:
        if encoding == "gzip":
            return _inflate(data, _GZIP_WBITS, max_bytes, url)
        try:
            return _inflate(data, zlib.MAX_WBITS, max_bytes, url)
        except zlib.error:
            # Raw deflate — servers that skip the zlib wrapper. Catching only
            # zlib.error matters: an over-cap ClientError must not retry here.
            return _inflate(data, _RAW_DEFLATE_WBITS, max_bytes, url)
    except (OSError, zlib.error, EOFError) as exc:
        raise ClientError(
            "undecodable Content-Encoding",
            context={"url": url, "encoding": encoding, "bytes": len(data)},
            cause=exc,
        ) from exc
