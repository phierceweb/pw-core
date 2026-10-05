"""Connection racing for :mod:`pf_core.fetch` (RFC 8305, Happy Eyeballs v2).

``socket.create_connection`` gives each resolved address the whole timeout in turn, so a host
whose IPv6 route black-holes costs that timeout before IPv4 is tried. Here the addresses race as
non-blocking connects in the calling thread: families alternate starting with the resolver's
first, a new attempt starts every ``_ATTEMPT_DELAY_S`` while earlier ones are pending (at once
when one fails), the first socket to connect wins, and every other socket is closed before the
winner is returned.
"""

from __future__ import annotations

import errno
import http.client
import itertools
import os
import selectors
import socket
import time
import urllib.request
from typing import Any

_ATTEMPT_DELAY_S = 0.25  # RFC 8305 §5 Connection Attempt Delay
_IN_PROGRESS = frozenset({errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EAGAIN})

_AddrInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, Any]
_SourceAddress = tuple[str, int] | None
_DEFAULT_TIMEOUT: Any = socket._GLOBAL_DEFAULT_TIMEOUT  # type: ignore[attr-defined]


def create_connection(
    address: tuple[str, int],
    timeout: Any = _DEFAULT_TIMEOUT,
    source_address: _SourceAddress = None,
) -> socket.socket:
    """:func:`socket.create_connection` with the resolved addresses raced, not tried in turn.

    The whole race is bounded by ``timeout``, which the winner keeps for its reads. When every
    attempt fails it raises the last error, as ``create_connection`` does; a race that runs out
    of time raises ``TimeoutError``. A single address connects as ``create_connection`` would.
    """
    host, port = address
    infos = _interleave(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM))
    if not infos:
        raise OSError("getaddrinfo returns an empty list")
    if len(infos) == 1:
        return _connect(infos[0], timeout, source_address)
    limit = socket.getdefaulttimeout() if timeout is _DEFAULT_TIMEOUT else timeout
    sock = _race(infos, source_address, limit)
    sock.settimeout(limit)
    return sock


def _interleave(infos: list[_AddrInfo]) -> list[_AddrInfo]:
    """Alternate address families, starting with the family of the first address."""
    by_family: dict[int, list[_AddrInfo]] = {}
    for info in infos:
        by_family.setdefault(info[0], []).append(info)
    rounds = itertools.zip_longest(*by_family.values())
    return [info for row in rounds for info in row if info is not None]


def _connect(info: _AddrInfo, timeout: Any, source_address: _SourceAddress) -> socket.socket:
    """One blocking attempt, made as ``socket.create_connection`` makes it."""
    family, kind, proto, _canonname, sockaddr = info
    sock = socket.socket(family, kind, proto)
    try:
        if timeout is not _DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


def _race(
    infos: list[_AddrInfo], source_address: _SourceAddress, limit: float | None
) -> socket.socket:
    deadline = None if limit is None else time.monotonic() + limit
    pending: list[socket.socket] = []
    errors: list[OSError] = []
    started = 0
    next_start = time.monotonic()
    winner: socket.socket | None = None
    with selectors.DefaultSelector() as selector:
        try:
            while True:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise TimeoutError("timed out")
                if started < len(infos) and (now >= next_start or not pending):
                    started += 1
                    next_start = now + _ATTEMPT_DELAY_S
                    try:
                        sock, connected = _start(infos[started - 1], source_address)
                    except OSError as exc:
                        errors.append(exc)
                        next_start = now
                        continue
                    if connected:
                        winner = sock
                        return sock
                    selector.register(sock, selectors.EVENT_WRITE)
                    pending.append(sock)
                    continue
                if not pending:
                    raise errors[-1]
                due = [
                    t
                    for t in (deadline, next_start if started < len(infos) else None)
                    if t is not None
                ]
                for key, _events in selector.select(min(due) - now if due else None):
                    sock = key.fileobj  # type: ignore[assignment]
                    selector.unregister(sock)
                    pending.remove(sock)
                    err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if not err:
                        winner = sock
                        return sock
                    sock.close()
                    errors.append(OSError(err, os.strerror(err)))
                    next_start = time.monotonic()
        finally:
            for sock in pending:
                if sock is not winner:
                    sock.close()


def _start(info: _AddrInfo, source_address: _SourceAddress) -> tuple[socket.socket, bool]:
    """Begin a non-blocking connect: ``(sock, True)`` if it connected at once, ``(sock, False)``
    while it is in progress; an attempt that fails at once raises its ``OSError``."""
    family, kind, proto, _canonname, sockaddr = info
    sock = socket.socket(family, kind, proto)
    try:
        sock.setblocking(False)
        if source_address:
            sock.bind(source_address)
        err = sock.connect_ex(sockaddr)
        if err and err not in _IN_PROGRESS:
            raise OSError(err, os.strerror(err))
    except BaseException:
        sock.close()
        raise
    return sock, not err


class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = create_connection


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = create_connection


class RacingHTTPHandler(urllib.request.HTTPHandler):
    """``HTTPHandler`` whose connections race the host's addresses."""

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPConnection, req)


class RacingHTTPSHandler(urllib.request.HTTPSHandler):
    """``HTTPSHandler`` whose connections race the host's addresses; TLS is unchanged."""

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPSConnection, req, context=self._context)  # type: ignore[attr-defined]
