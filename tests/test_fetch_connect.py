"""Tests for connection racing in pw_core.fetch (RFC 8305 Happy Eyeballs).

Hermetic: ``socket.getaddrinfo``, ``socket.socket`` and ``selectors.DefaultSelector`` are
replaced with fakes whose connect outcome is scripted per address, so nothing leaves the process.
"""

from __future__ import annotations

import errno
import io
import selectors
import socket
import ssl
import threading
import time
import urllib.error

import pytest

from pw_core.fetch import Fetcher

V6 = "2606:2800:220:1:248:1893:25c8:1946"
V6B = "2606:2800:220:1:248:1893:25c8:1947"
V4 = "93.184.216.34"
V4B = "93.184.216.35"

OK, REFUSED, UNREACHABLE, BLACKHOLE = "ok", "refused", "unreachable", "blackhole"

_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"


class _Net:
    """Fake resolver, sockets and selector. An outcome per address; a float connects after
    that delay. A blocking ``connect`` and a non-blocking ``connect_ex`` follow the same script."""

    def __init__(self) -> None:
        self.hosts: dict[str, list[str]] = {}
        self.outcomes: dict[str, str | float] = {}
        self.no_family: set[int] = set()
        self.sockets: list = []
        self.attempts: list[tuple[str, float, threading.Thread]] = []
        self.release = threading.Event()

    def getaddrinfo(self, host, port, family=0, type=0, proto=0, flags=0):
        infos = []
        for ip in self.hosts.get(host, [V4]):
            if ":" in ip:
                infos.append(
                    (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port, 0, 0))
                )
            else:
                infos.append(
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))
                )
        return infos

    def socket_class(self) -> type:
        net = self

        class _FakeSocket:
            def __init__(
                self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, fileno=None
            ):
                if family in net.no_family:
                    raise OSError(errno.EAFNOSUPPORT, "Address family not supported")
                self.family = family
                self.timeout: float | None = socket.getdefaulttimeout()
                self.ready_at: float | None = None
                self.so_error = 0
                self.bound = None
                self.target: tuple | None = None
                self.peer: tuple | None = None
                self.closed = False
                self.sent = b""
                net.sockets.append(self)

            def settimeout(self, value):
                self.timeout = value

            def setblocking(self, flag):
                self.timeout = None if flag else 0.0

            def connect_ex(self, address):
                ip = address[0]
                self.target = address
                net.attempts.append((ip, time.monotonic(), threading.current_thread()))
                outcome = net.outcomes.get(ip, OK)
                now = time.monotonic()
                if outcome == UNREACHABLE:
                    return errno.ENETUNREACH
                if outcome == REFUSED:
                    self.ready_at, self.so_error = now, errno.ECONNREFUSED
                elif outcome != BLACKHOLE:
                    self.ready_at = now + (outcome if isinstance(outcome, float) else 0.0)
                return errno.EINPROGRESS

            def getsockopt(self, level, option, *args):
                assert (level, option) == (socket.SOL_SOCKET, socket.SO_ERROR)
                return self.so_error

            def gettimeout(self):
                return self.timeout

            def bind(self, address):
                self.bound = address

            def setsockopt(self, *args):
                pass

            def connect(self, address):
                ip = address[0]
                self.target = address
                net.attempts.append((ip, time.monotonic(), threading.current_thread()))
                outcome = net.outcomes.get(ip, OK)
                if outcome == REFUSED:
                    raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
                if outcome == UNREACHABLE:
                    raise OSError(errno.ENETUNREACH, "Network is unreachable")
                if outcome == BLACKHOLE:
                    net.release.wait(self.timeout)
                    raise TimeoutError("timed out")
                if isinstance(outcome, float):
                    time.sleep(outcome)
                self.peer = address

            def sendall(self, data):
                self.sent += data

            def makefile(self, *args, **kwargs):
                return io.BytesIO(_RESPONSE)

            def close(self):
                self.closed = True

        return _FakeSocket

    def selector_class(self) -> type:
        class _FakeSelector:
            def __init__(self):
                self.registered: list = []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.registered.clear()

            def register(self, sock, events, data=None):
                assert events == selectors.EVENT_WRITE
                self.registered.append(sock)

            def unregister(self, sock):
                self.registered.remove(sock)

            def select(self, timeout=None):
                due = [s.ready_at for s in self.registered if s.ready_at is not None]
                wait = timeout
                if due:
                    until_due = max(0.0, min(due) - time.monotonic())
                    wait = until_due if wait is None else min(wait, until_due)
                threading.Event().wait(5.0 if wait is None else wait)
                now = time.monotonic()
                ready = [s for s in self.registered if s.ready_at is not None and s.ready_at <= now]
                for sock in ready:
                    if not sock.so_error:
                        sock.peer = sock.target
                return [
                    (
                        selectors.SelectorKey(sock, 0, selectors.EVENT_WRITE, None),
                        selectors.EVENT_WRITE,
                    )
                    for sock in ready
                ]

        return _FakeSelector

    def connected(self) -> list[str]:
        return [s.peer[0] for s in self.sockets if s.peer]

    def attempted(self) -> list[str]:
        return [ip for ip, _, _ in self.attempts]

    def started_at(self, ip: str) -> float:
        return next(t for addr, t, _ in self.attempts if addr == ip)

    def join(self, timeout: float = 5.0) -> list[threading.Thread]:
        """Wait for every connect attempt's thread; return the ones still running."""
        threads = {thread for _, _, thread in self.attempts}
        threads.discard(threading.current_thread())
        for thread in threads:
            thread.join(timeout)
        return [thread for thread in threads if thread.is_alive()]


@pytest.fixture
def net(monkeypatch):
    fake = _Net()
    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    monkeypatch.setattr(socket, "socket", fake.socket_class())
    monkeypatch.setattr(selectors, "DefaultSelector", fake.selector_class())
    for var in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.upper(), raising=False)
    monkeypatch.setenv("no_proxy", "*")  # keeps a system proxy setting out of the tests
    yield fake
    fake.release.set()
    assert fake.join() == []


def _elapsed(fn) -> float:
    start = time.monotonic()
    fn()
    return time.monotonic() - start


class TestFetcherConnects:
    def test_dead_ipv6_fails_over_to_ipv4_well_under_the_timeout(self, net):
        net.hosts["example.com"] = [V6, V4]
        net.outcomes[V6] = BLACKHOLE
        result: list = []
        elapsed = _elapsed(
            lambda: result.append(
                Fetcher(retries=0).get_bytes("http://example.com/page", timeout_s=2.0)
            )
        )
        assert result == [("http://example.com/page", b"ok")]
        assert elapsed < 1.0
        assert net.connected() == [V4]
        assert all(s.closed for s in net.sockets)
        assert {thread for _, _, thread in net.attempts} == {threading.current_thread()}

    @pytest.mark.parametrize(
        ("verify_tls", "mode"), [(True, ssl.CERT_REQUIRED), (False, ssl.CERT_NONE)]
    )
    def test_https_wraps_the_winner_with_the_fetchers_tls_context(
        self, net, monkeypatch, verify_tls, mode
    ):
        wrapped: list = []

        def fake_wrap(context, sock, server_hostname=None, **kwargs):
            wrapped.append((sock.peer[0], server_hostname, context.verify_mode))
            return sock

        monkeypatch.setattr(ssl.SSLContext, "wrap_socket", fake_wrap)
        net.hosts["example.com"] = [V6, V4]
        net.outcomes[V6] = BLACKHOLE
        fetcher = Fetcher(retries=0, verify_tls=verify_tls)
        elapsed = _elapsed(lambda: fetcher.get_bytes("https://example.com/page", timeout_s=2.0))
        assert elapsed < 1.0
        assert wrapped == [(V4, "example.com", mode)]

    def test_a_proxy_is_connected_through_the_race(self, net, monkeypatch):
        monkeypatch.delenv("no_proxy")
        monkeypatch.setenv("http_proxy", "http://proxy.example:3128")
        net.hosts["proxy.example"] = [V6, V4]
        net.outcomes[V6] = BLACKHOLE
        fetcher = Fetcher(retries=0)
        elapsed = _elapsed(lambda: fetcher.get_bytes("http://example.com/page", timeout_s=2.0))
        assert elapsed < 1.0
        winner = next(s for s in net.sockets if s.peer)
        assert winner.peer[:2] == (V4, 3128)
        assert winner.sent.startswith(b"GET http://example.com/page HTTP/1.1\r\n")

    def test_every_address_failing_reaches_the_retry_loop_as_urlerror(self, net, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        net.hosts["example.com"] = [V6, V4]
        net.outcomes.update({V6: REFUSED, V4: REFUSED})
        with pytest.raises(urllib.error.URLError) as exc_info:
            Fetcher(retries=1).get_bytes("http://example.com/page", timeout_s=2.0)
        assert isinstance(exc_info.value.reason, ConnectionRefusedError)
        assert net.attempted() == [V6, V4, V6, V4]


def _race(net, *ips, timeout=2.0, source_address=None):
    from pw_core.fetch import _connect

    net.hosts["example.com"] = list(ips)
    return _connect.create_connection(("example.com", 443), timeout, source_address)


class TestRace:
    @pytest.mark.parametrize(
        ("resolved", "order"),
        [([V6, V6B, V4, V4B], [V6, V4, V6B, V4B]), ([V4, V4B, V6], [V4, V6, V4B])],
        ids=["v6-first", "v4-first"],
    )
    def test_families_alternate_starting_with_the_resolvers_first(self, net, resolved, order):
        net.outcomes.update(dict.fromkeys(resolved, REFUSED))
        with pytest.raises(ConnectionRefusedError):
            _race(net, *resolved)
        assert net.attempted() == order

    def test_next_attempt_starts_250ms_later_while_the_first_is_pending(self, net):
        net.outcomes[V6] = BLACKHOLE
        sock = _race(net, V6, V4)
        assert sock.peer[0] == V4
        assert 0.2 <= net.started_at(V4) - net.started_at(V6) < 0.6

    @pytest.mark.parametrize("failure", [UNREACHABLE, REFUSED], ids=["at-once", "reported-later"])
    def test_a_failure_starts_the_next_attempt_at_once(self, net, failure):
        net.outcomes[V6] = failure
        elapsed = _elapsed(lambda: _race(net, V6, V4))
        assert elapsed < 0.15
        assert net.connected() == [V4]

    def test_first_to_connect_wins_and_every_other_socket_is_closed(self, net):
        net.outcomes.update({V6: 0.5, V6B: BLACKHOLE})
        sock = _race(net, V6, V4, V6B)
        assert sock.peer[0] == V4
        assert net.attempted() == [V6, V4]
        assert net.connected() == [V4]
        assert [s.closed for s in net.sockets if s is not sock] == [True]
        assert not sock.closed

    def test_a_won_race_closes_a_pending_loser_before_returning(self, net):
        net.outcomes[V6] = BLACKHOLE
        sock = _race(net, V6, V4, timeout=60.0)
        loser = next(s for s in net.sockets if s is not sock)
        assert (sock.peer[0], loser.closed) == (V4, True)

    def test_an_attempt_that_would_connect_after_the_deadline_is_closed(self, net):
        net.outcomes.update({V6: BLACKHOLE, V4: 0.6})
        with pytest.raises(TimeoutError):
            _race(net, V6, V4, timeout=0.5)
        assert net.connected() == []
        assert all(s.closed for s in net.sockets)

    def test_a_family_without_sockets_is_skipped_at_once(self, net):
        net.no_family.add(socket.AF_INET6)
        elapsed = _elapsed(lambda: _race(net, V6, V4))
        assert elapsed < 0.15
        assert net.connected() == [V4]

    def test_every_address_failing_raises_what_create_connection_raises(self, net):
        net.outcomes.update({V6: REFUSED, V4: UNREACHABLE})
        with pytest.raises(OSError) as raced:
            _race(net, V6, V4)
        with pytest.raises(OSError) as stdlib:
            socket.create_connection(("example.com", 443), 2.0)
        assert type(raced.value) is type(stdlib.value) is OSError
        assert raced.value.errno == stdlib.value.errno == errno.ENETUNREACH
        assert all(s.closed for s in net.sockets)

    def test_the_whole_race_is_bounded_by_the_timeout(self, net):
        net.outcomes.update(dict.fromkeys([V6, V4, V6B], BLACKHOLE))
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            _race(net, V6, V4, V6B, timeout=0.6)
        assert 0.55 <= time.monotonic() - start < 1.0
        assert net.attempted() == [V6, V4, V6B]
        assert all(s.closed for s in net.sockets)

    def test_no_attempt_starts_after_the_deadline(self, net):
        net.outcomes.update(dict.fromkeys([V6, V4, V6B], BLACKHOLE))
        with pytest.raises(TimeoutError):
            _race(net, V6, V4, V6B, timeout=0.4)
        assert net.attempted() == [V6, V4]

    def test_the_winner_keeps_the_request_timeout_for_its_reads(self, net):
        net.outcomes[V6] = BLACKHOLE
        assert _race(net, V6, V4, timeout=5.0).timeout == 5.0

    def test_no_timeout_races_without_a_deadline(self, net):
        net.outcomes[V6] = BLACKHOLE
        sock = _race(net, V6, V4, timeout=None)
        assert (sock.peer[0], sock.timeout) == (V4, None)

    def test_a_single_address_connects_in_the_calling_thread(self, net):
        sock = _race(net, V4)
        assert net.attempts[0][2] is threading.current_thread()
        assert (sock.peer[0], sock.timeout) == (V4, 2.0)

    def test_a_single_address_failure_raises_raw(self, net):
        net.outcomes[V4] = REFUSED
        with pytest.raises(ConnectionRefusedError):
            _race(net, V4)
        assert net.sockets[0].closed

    def test_source_address_is_bound_on_every_attempt(self, net):
        net.outcomes[V6] = REFUSED
        _race(net, V6, V4, source_address=("", 0))
        assert [s.bound for s in net.sockets] == [("", 0), ("", 0)]

    def test_no_addresses_raises_like_create_connection(self, net):
        with pytest.raises(OSError, match="getaddrinfo returns an empty list"):
            _race(net)

    def test_a_resolution_failure_propagates(self, net, monkeypatch):
        def unresolvable(*args, **kwargs):
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")

        monkeypatch.setattr(socket, "getaddrinfo", unresolvable)
        with pytest.raises(socket.gaierror):
            _race(net, V4)
