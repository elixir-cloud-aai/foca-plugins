"""Tests of the geographic helpers of `tes.routing.distance`."""

from collections.abc import Iterator
import socket
import threading
import time
from typing import Any

from foca.plugins import Location
import pytest

from foca_plugins.tes.routing.distance import geo
from foca_plugins.tes.routing.distance.geo import (
    Resolver,
    distance_km,
    input_urls,
    literal_address,
    public_address,
    url_host,
)

ZURICH = Location(latitude=47.3769, longitude=8.5417)
HELSINKI = Location(latitude=60.1699, longitude=24.9384)


def addrinfo(*addresses: str) -> list[Any]:
    """Build the result of `socket.getaddrinfo()`.

    Args:
        addresses: IP addresses.

    Returns:
        Address infos, one per address.
    """
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        for address in addresses
    ]


def test_distance_is_great_circle() -> None:
    """Distances are great-circle distances in kilometres."""
    assert distance_km(ZURICH, ZURICH) == 0
    # spherical law of cosines: 1774.1 km
    assert distance_km(ZURICH, HELSINKI) == pytest.approx(1774.1, rel=1e-4)
    assert distance_km(HELSINKI, ZURICH) == distance_km(ZURICH, HELSINKI)
    north = Location(latitude=90, longitude=0)
    south = Location(latitude=-90, longitude=0)
    # half the mean circumference
    assert distance_km(north, south) == pytest.approx(20015.1, rel=1e-4)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, []),
        ([], []),
        ({}, []),
        ({"inputs": "x"}, []),
        ({"inputs": [None, "x", {"path": "/in"}, {"url": ""}, {"url": 1}]}, []),
        (
            {
                "inputs": [
                    {"url": "https://b.example.org/1"},
                    {"url": "s3://bucket/key"},
                    {"url": "https://b.example.org/1"},
                    {"content": "text", "path": "/in"},
                    {"url": "https://a.example.org/2"},
                ]
            },
            ["https://b.example.org/1", "s3://bucket/key", "https://a.example.org/2"],
        ),
    ],
)
def test_input_urls(payload: Any, expected: list[str]) -> None:
    """Input URLs are the non-empty string `url`s of the inputs, once each.

    Args:
        payload: Task.
        expected: Input URLs, in order.
    """
    assert input_urls(payload) == expected


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://Data.Example.org./x", "data.example.org"),
        ("ftp://user:secret@ftp.example.org:21/x", "ftp.example.org"),
        ("https://[2001:db8::1]:8443/x", "2001:db8::1"),
        ("HTTPS://data.example.org/x", "data.example.org"),
        ("sftp://sftp.example.org/x", "sftp.example.org"),
        ("s3://bucket.example.org/key", None),
        ("gs://bucket/key", None),
        ("drs://drs.example.org/object", None),
        ("file:///data/x", None),
        ("/data/x", None),
        ("http://[::1/x", None),
    ],
)
def test_url_host(url: str, host: str | None) -> None:
    """Hosts are lowercase, without user info, port or trailing dot.

    Only schemes whose authority is a host that serves the data count.

    Args:
        url: URL.
        host: Expected host, if any.
    """
    assert url_host(url) == host


def make_resolver(threads: int = 4, ttl: float = 60) -> Resolver:
    """Create a resolver.

    Args:
        threads: Maximum number of resolutions at a time.
        ttl: Seconds to keep results.

    Returns:
        Resolver.
    """
    return Resolver(threads=threads, ttl=ttl, cache_size=100)


def soon(seconds: float = 2) -> float:
    """Get a deadline.

    Args:
        seconds: Seconds from now.

    Returns:
        Deadline, in `time.monotonic()` seconds.
    """
    return time.monotonic() + seconds


@pytest.fixture
def hang(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Make the system resolver hang until the test ends.

    Args:
        monkeypatch: Pytest fixture.

    Yields:
        Names passed to the resolver, in order.
    """
    release = threading.Event()
    asked: list[str] = []

    def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> Any:
        """Hang until the test ends.

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Returns:
            Address info.
        """
        asked.append(host)
        release.wait(5)
        return addrinfo("9.9.9.9")

    monkeypatch.setattr(geo.socket, "getaddrinfo", getaddrinfo)
    yield asked
    release.set()


def test_resolver_returns_addresses_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Addresses come in the resolver's order, without duplicates.

    Args:
        monkeypatch: Pytest fixture.
    """
    monkeypatch.setattr(
        geo.socket,
        "getaddrinfo",
        lambda *args, **kwargs: addrinfo("9.9.9.9", "1.1.1.1", "9.9.9.9"),
    )
    assert make_resolver().resolve(["data.example.org"], soon()) == {
        "data.example.org": ("9.9.9.9", "1.1.1.1")
    }


@pytest.mark.parametrize("error", [OSError("nope"), UnicodeError("bad")])
def test_resolver_failures_give_no_addresses(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Hosts that can't be resolved have no addresses; that is cached too.

    Args:
        monkeypatch: Pytest fixture.
        error: Error of the resolver.
    """
    asked: list[str] = []

    def fail(host: str, *args: Any, **kwargs: Any) -> Any:
        """Fail to resolve.

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Raises:
            Exception: The error of the test.
        """
        asked.append(host)
        raise error

    monkeypatch.setattr(geo.socket, "getaddrinfo", fail)
    resolver = make_resolver()
    assert resolver.resolve(["bad.example.org"], soon()) == {"bad.example.org": ()}
    assert resolver.resolve(["bad.example.org"], soon()) == {"bad.example.org": ()}
    assert asked == ["bad.example.org"]


def test_resolver_resolves_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Names are resolved at the same time, not one after another.

    Args:
        monkeypatch: Pytest fixture.
    """
    barrier = threading.Barrier(3, timeout=2)

    def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> Any:
        """Resolve once all three names are resolved at the same time.

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Returns:
            Address info.
        """
        barrier.wait()  # breaks unless all three run at once
        return addrinfo("9.9.9.9")

    monkeypatch.setattr(geo.socket, "getaddrinfo", getaddrinfo)
    names = ["a.example.org", "b.example.org", "c.example.org"]
    assert make_resolver().resolve(names, soon()) == dict.fromkeys(names, ("9.9.9.9",))


def test_resolver_caches_until_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Results are cached for the TTL; a TTL of 0 caches nothing.

    Args:
        monkeypatch: Pytest fixture.
    """
    asked: list[str] = []

    def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> Any:
        """Resolve every name to the same address.

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Returns:
            Address info.
        """
        asked.append(host)
        return addrinfo("9.9.9.9")

    monkeypatch.setattr(geo.socket, "getaddrinfo", getaddrinfo)
    now = [1000.0]
    resolver = Resolver(threads=2, ttl=60, cache_size=10, clock=lambda: now[0])
    resolver.resolve(["c.example.org"], soon())
    resolver.resolve(["c.example.org"], soon())
    assert asked == ["c.example.org"]
    now[0] += 61
    resolver.resolve(["c.example.org"], soon())
    assert asked == ["c.example.org"] * 2
    uncached = make_resolver(ttl=0)
    uncached.resolve(["u.example.org"], soon())
    uncached.resolve(["u.example.org"], soon())
    assert asked[2:] == ["u.example.org"] * 2


def test_resolver_gives_up_at_deadline(hang: list[str]) -> None:
    """Names not resolved by the deadline are left out, all within it.

    Args:
        hang: Names passed to the hanging resolver.
    """
    resolver = make_resolver(threads=8)
    names = [f"h{i}.example.org" for i in range(5)]
    started = time.monotonic()
    assert resolver.resolve(names, soon(0.2)) == {}
    # in parallel: one deadline for all names, not one per name
    assert time.monotonic() - started < 1
    assert sorted(hang) == names


def test_resolver_starts_nothing_after_deadline(hang: list[str]) -> None:
    """No resolution starts once the deadline has passed.

    Args:
        hang: Names passed to the hanging resolver.
    """
    before = threading.active_count()
    assert make_resolver().resolve(["late.example.org"], soon(-1)) == {}
    assert hang == []
    assert threading.active_count() == before


def test_resolver_bounds_threads(hang: list[str]) -> None:
    """At most `threads` names are resolved at a time, each name once.

    Args:
        hang: Names passed to the hanging resolver.
    """
    resolver = make_resolver(threads=2)
    names = [f"h{i}.example.org" for i in range(5)]
    assert resolver.resolve(names, soon(0.05)) == {}
    assert resolver.resolve(names, soon(0.05)) == {}
    # two threads hang; the other names, and the hanging ones again, wait
    assert hang == names[:2]


def test_resolver_survives_thread_limits(
    hang: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """If no thread can be started, the name stays unresolved; slots free.

    Args:
        hang: Names passed to the hanging resolver.
        monkeypatch: Pytest fixture.
    """

    def refuse(self: threading.Thread) -> None:
        """Refuse to start a thread.

        Args:
            self: Thread.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("can't start new thread")

    resolver = make_resolver(threads=1)
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", refuse)
        started = time.monotonic()
        assert resolver.resolve(["a.example.org"], soon(5)) == {}
        assert time.monotonic() - started < 1
    # the slot was freed: the next name gets a thread
    assert resolver.resolve(["b.example.org"], soon(0.05)) == {}
    assert hang == ["b.example.org"]


def test_literal_address() -> None:
    """IP literals are normalized; names are no literals."""
    assert literal_address("9.9.9.9") == "9.9.9.9"
    assert literal_address("2001:4860:0::1") == "2001:4860::1"
    assert literal_address("data.example.org") is None


@pytest.mark.parametrize(
    ("addresses", "expected"),
    [
        (["10.0.0.1", "9.9.9.9", "1.1.1.1"], "9.9.9.9"),
        (["bogus", "fe80::1%eth0", "2001:4860::1"], "2001:4860::1"),
        (["::ffff:10.0.0.1", "10.0.0.1", "127.0.0.1", "::1"], None),
        ([], None),
    ],
)
def test_public_address(addresses: list[str], expected: str | None) -> None:
    """The first public address is used; private and invalid ones aren't.

    Args:
        addresses: Addresses of a host.
        expected: Address used.
    """
    assert public_address(addresses) == expected


def test_resolver_callers_share_a_running_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent callers wait for the running resolution of a name.

    Args:
        monkeypatch: Pytest fixture.
    """
    asked: list[str] = []

    def slow(host: str, *args: Any, **kwargs: Any) -> Any:
        """Resolve after a while.

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Returns:
            Address info.
        """
        asked.append(host)
        time.sleep(0.2)
        return addrinfo("9.9.9.9")

    monkeypatch.setattr(geo.socket, "getaddrinfo", slow)
    resolver = make_resolver(threads=1)
    results: list[dict[str, tuple[str, ...]]] = []
    callers = [
        threading.Thread(
            target=lambda: results.append(
                resolver.resolve(["shared.example.org"], soon())
            )
        )
        for _ in range(3)
    ]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(5)
    assert results == [{"shared.example.org": ("9.9.9.9",)}] * 3
    assert asked == ["shared.example.org"]


def test_resolver_limits_resolutions_per_call(hang: list[str]) -> None:
    """One call starts at most `per_call` resolutions.

    Args:
        hang: Names passed to the hanging resolver.
    """
    resolver = Resolver(threads=8, ttl=60, cache_size=100, per_call=2)
    names = [f"h{i}.example.org" for i in range(5)]
    assert resolver.resolve(names, soon(0.05)) == {}
    assert sorted(hang) == names[:2]


def test_resolver_wakes_callers_if_no_thread_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers that share a job whose thread didn't start don't wait for it.

    Args:
        monkeypatch: Pytest fixture.
    """
    resolver = make_resolver(threads=1)
    shared: list[Any] = []

    def refuse(self: threading.Thread) -> None:
        """Note the job, then refuse to start its thread.

        Args:
            self: Thread.

        Raises:
            RuntimeError: Always.
        """
        shared.append(resolver._jobs["a.example.org"])
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    assert resolver.resolve(["a.example.org"], soon(5)) == {}
    (job,) = shared
    # a caller that got the job before the failure isn't left waiting
    assert job.done.is_set()
    assert job.addresses == ()
