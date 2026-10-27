"""Tests of the geographic helpers of `tes.routing.distance`."""

import socket
import threading
from typing import Any

from foca.plugins import Location
import pytest

from foca_plugins.tes.routing.distance import geo
from foca_plugins.tes.routing.distance.geo import (
    distance_km,
    input_urls,
    public_address,
    resolve,
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
        ("s3://bucket/key", "bucket"),
        ("file:///data/x", None),
        ("/data/x", None),
        ("http://[::1/x", None),
    ],
)
def test_url_host(url: str, host: str | None) -> None:
    """Hosts are lowercase, without user info, port or trailing dot.

    Args:
        url: URL.
        host: Expected host, if any.
    """
    assert url_host(url) == host


def test_resolve_returns_addresses_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Addresses come in the resolver's order, without duplicates.

    Args:
        monkeypatch: Pytest fixture.
    """
    monkeypatch.setattr(
        geo.socket,
        "getaddrinfo",
        lambda *args, **kwargs: addrinfo("9.9.9.9", "1.1.1.1", "9.9.9.9"),
    )
    assert resolve("data.example.org", 1) == ["9.9.9.9", "1.1.1.1"]


@pytest.mark.parametrize("error", [OSError("nope"), UnicodeError("bad")])
def test_resolve_failures_give_no_addresses(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Hosts that can't be resolved have no addresses.

    Args:
        monkeypatch: Pytest fixture.
        error: Error of the resolver.
    """

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(geo.socket, "getaddrinfo", fail)
    assert resolve("data.example.org", 1) == []


def test_resolve_gives_up_at_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolver that takes too long is abandoned.

    Args:
        monkeypatch: Pytest fixture.
    """
    release = threading.Event()

    def hang(*args: Any, **kwargs: Any) -> Any:
        release.wait(5)
        return addrinfo("9.9.9.9")

    monkeypatch.setattr(geo.socket, "getaddrinfo", hang)
    try:
        assert resolve("slow.example.org", 0.05) == []
        assert resolve("slow.example.org", -1) == []
    finally:
        release.set()


def test_public_address_of_literals() -> None:
    """IP literals are used as they are, if public; no resolver involved."""
    assert public_address("9.9.9.9", 1) == "9.9.9.9"
    assert public_address("2001:4860::1", 1) == "2001:4860::1"
    assert public_address("10.0.0.1", 1) is None
    assert public_address("::1", 1) is None


@pytest.mark.parametrize(
    ("addresses", "expected"),
    [
        (["10.0.0.1", "9.9.9.9", "1.1.1.1"], "9.9.9.9"),
        (["10.0.0.1", "127.0.0.1"], None),
        ([], None),
    ],
)
def test_public_address_of_names(
    monkeypatch: pytest.MonkeyPatch, addresses: list[str], expected: str | None
) -> None:
    """Host names give their first public address, if any.

    Args:
        monkeypatch: Pytest fixture.
        addresses: Addresses of the host.
        expected: Address used.
    """
    if addresses:
        monkeypatch.setattr(
            geo.socket, "getaddrinfo", lambda *a, **k: addrinfo(*addresses)
        )
    else:

        def fail(*args: Any, **kwargs: Any) -> Any:
            raise OSError("unknown host")

        monkeypatch.setattr(geo.socket, "getaddrinfo", fail)
    assert public_address("data.example.org", 1) == expected
