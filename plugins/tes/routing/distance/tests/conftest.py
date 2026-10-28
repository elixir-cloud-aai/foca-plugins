"""Fixtures of the tests of `tes.routing.distance`.

The GeoIP database is written for the tests with `mmdb-writer` (a
development dependency), in the format and layout of DB-IP's "IP to City
Lite" database, with a few public networks at known places and some
records without valid locations.
"""

from pathlib import Path

from foca.plugins import Location
from mmdb_writer import MMDBWriter
from netaddr import IPSet  # type: ignore[import-untyped]  # no stubs for netaddr 1.x
import pytest

PLACES = {
    "zurich": Location(latitude=47.3769, longitude=8.5417),
    "helsinki": Location(latitude=60.1699, longitude=24.9384),
    "mountain_view": Location(latitude=37.4056, longitude=-122.0775),
    "sydney": Location(latitude=-33.8688, longitude=151.2093),
}
"""Places of the networks of the test database."""

ADDRESSES = {
    "zurich": "9.9.9.9",
    "helsinki": "2001:4860::1",
    "mountain_view": "8.8.8.8",
    "sydney": "1.1.1.1",
}
"""An address in each network of `PLACES`."""


@pytest.fixture(scope="session")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the test GeoIP database.

    Args:
        tmp_path_factory: Pytest fixture.

    Returns:
        Path of the database: the networks of `ADDRESSES` (`/24` and `/32`)
        at `PLACES`; `5.5.5.0/24` without location, `6.6.6.0/24` with a
        latitude beyond 90 and `7.7.7.0/24` with coordinates that are no
        numbers; other addresses are not in it.
    """
    networks = {
        "9.9.9.0/24": "zurich",
        "2001:4860::/32": "helsinki",
        "8.8.8.0/24": "mountain_view",
        "1.1.1.0/24": "sydney",
    }
    writer = MMDBWriter(
        ip_version=6, ipv4_compatible=True, database_type="DBIP-City-Lite"
    )
    for network, place in networks.items():
        location = PLACES[place]
        writer.insert_network(
            IPSet([network]),
            {
                "city": {"names": {"en": place}},
                "location": {
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                },
            },
        )
    writer.insert_network(IPSet(["5.5.5.0/24"]), {"country": {"iso_code": "DE"}})
    writer.insert_network(
        IPSet(["6.6.6.0/24"]), {"location": {"latitude": 100.0, "longitude": 0.0}}
    )
    writer.insert_network(
        IPSet(["7.7.7.0/24"]), {"location": {"latitude": "x", "longitude": "y"}}
    )
    path = tmp_path_factory.mktemp("geoip") / "dbip-city-lite.mmdb"
    writer.to_db_file(str(path))
    return path


@pytest.fixture
def places() -> dict[str, Location]:
    """Get the places of the networks of the test database.

    Returns:
        Locations by name (`PLACES`).
    """
    return dict(PLACES)


@pytest.fixture
def addresses() -> dict[str, str]:
    """Get an address in each network of the test database.

    Returns:
        Addresses by place name (`ADDRESSES`).
    """
    return dict(ADDRESSES)
