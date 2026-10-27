"""Location providers and the cache of their lookups.

A provider gives the location of a public IP address, or `None` if it
knows none (a miss, which may be cached). Lookups that fail (e.g., a
service is unavailable) raise :py:class:`LookupFailed` and aren't cached.

- :py:class:`OfflineProvider` reads a local GeoIP database in MaxMind DB
  format, e.g., DB-IP's "IP to City Lite" (CC BY 4.0): no data leaves the
  gateway.
- :py:class:`DbipProvider` asks DB-IP's free API for the place of an
  address (city, region, country), then Nominatim (OpenStreetMap) for its
  coordinates: the address goes to db-ip.com, the place to Nominatim. It
  sends requests through `ctx.http` only.
"""

from collections import OrderedDict
from collections.abc import Callable
import math
from pathlib import Path
import threading
import time
from typing import Any, Protocol
from urllib.parse import quote

from foca.plugins import Context, Location, PluginError
from foca.utils.http import GuardedClient, HttpError
import maxminddb
from maxminddb.errors import InvalidDatabaseError


class LookupFailed(Exception):
    """A lookup failed, e.g., as a service is unavailable; not a miss."""


class Provider(Protocol):
    """Gives the locations of public IP addresses."""

    def locate(self, address: str, ctx: Context) -> Location | None:
        """Locate an address.

        Args:
            address: Public IP address.
            ctx: Context of the call (deadline, `ctx.http`).

        Returns:
            Location, or `None` if the provider knows none.

        Raises:
            LookupFailed: The lookup failed.
        """
        ...  # pragma: no cover

    def close(self) -> None:
        """Release resources."""
        ...  # pragma: no cover


def coordinates(latitude: Any, longitude: Any) -> Location | None:
    """Build a location from coordinates of untrusted data.

    Args:
        latitude: Latitude in degrees, a number or a numeric string.
        longitude: Longitude in degrees, a number or a numeric string.

    Returns:
        Location, or `None` if a coordinate is no finite number or out of
        range.
    """
    values: list[float] = []
    for value in (latitude, longitude):
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            return None
        try:
            number = float(value)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        values.append(number)
    lat, lon = values
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return Location(latitude=lat, longitude=lon)


class OfflineProvider:
    """Locations from a local GeoIP database (MaxMind DB format).

    Works with DB-IP's "IP to City Lite" database and other databases with
    GeoIP2-City-style records (`location.latitude`, `location.longitude`).
    The reader is safe for concurrent use.
    """

    def __init__(self, path: Path) -> None:
        """Open the database.

        Args:
            path: Path of the database file.

        Raises:
            ValueError: The file is no valid MaxMind database.
        """
        try:
            self._reader = maxminddb.open_database(str(path))
        except InvalidDatabaseError as exc:
            raise ValueError(f"Not a valid GeoIP database: {path}") from exc

    def locate(self, address: str, ctx: Context) -> Location | None:
        """Locate an address in the database.

        Args:
            address: Public IP address.
            ctx: Context of the call (unused: local lookups are fast).

        Returns:
            Location, or `None` if the database has none for the address.
        """
        try:
            record = self._reader.get(address)
        except ValueError:
            return None
        if not isinstance(record, dict):
            return None
        location = record.get("location")
        if not isinstance(location, dict):
            return None
        return coordinates(location.get("latitude"), location.get("longitude"))

    def close(self) -> None:
        """Close the database."""
        self._reader.close()


class DbipProvider:
    """Locations from DB-IP's free API and Nominatim.

    Nominatim's usage policy allows at most one request per second, from an
    application that identifies itself in its user agent: the requests of a
    process wait for each other (`interval`), and wait no longer than the
    call's deadline.
    """

    def __init__(
        self,
        dbip_url: str,
        nominatim_url: str,
        user_agent: str,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the provider.

        Args:
            dbip_url: Base URL of DB-IP's API, without trailing slash.
            nominatim_url: Base URL of Nominatim, without trailing slash.
            user_agent: User agent of the requests.
            interval: Seconds between requests to Nominatim.
            clock: Clock of the throttle, for tests.
            sleep: Wait function of the throttle, for tests.
        """
        self.dbip_url = dbip_url
        self.nominatim_url = nominatim_url
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0

    def locate(self, address: str, ctx: Context) -> Location | None:
        """Locate an address: its place, then the place's coordinates.

        Args:
            address: Public IP address.
            ctx: Context of the call, with `ctx.http`.

        Returns:
            Location, or `None` if DB-IP knows no city and country of the
            address, or Nominatim no coordinates of the place.

        Raises:
            LookupFailed: A service failed or answered invalidly, or the
                rate limit of Nominatim leaves no time.
            PluginError: The context has no HTTP client.
        """
        http = ctx.http
        if http is None:
            raise PluginError("Provider 'dbip' needs ctx.http")
        place = self._get(http, f"{self.dbip_url}/{quote(address, safe='')}")
        if not isinstance(place, dict) or "error" in place:
            raise LookupFailed("DB-IP gave no place")
        city, region, country = (
            str(place.get(key) or "").strip()
            for key in ("city", "stateProv", "countryCode")
        )
        # `ZZ`: unknown country
        if not city or not country or country == "ZZ":
            return None
        queries = [f"{city}, {country}"]
        if region:
            queries.insert(0, f"{city}, {region} {country}")
        for query in queries:
            self._throttle(ctx)
            results = self._get(
                http,
                f"{self.nominatim_url}/search",
                {"q": query, "format": "jsonv2", "limit": "1"},
            )
            if not isinstance(results, list):
                raise LookupFailed("Nominatim answered invalidly")
            if results and isinstance(results[0], dict):
                location = coordinates(results[0].get("lat"), results[0].get("lon"))
                if location is not None:
                    return location
        return None

    def close(self) -> None:
        """Release resources (none)."""

    def _get(
        self, http: GuardedClient, url: str, params: dict[str, str] | None = None
    ) -> Any:
        """Get a JSON document.

        Messages leave out the URL, which holds the address.

        Args:
            http: Guarded HTTP client of the call.
            url: URL.
            params: Query parameters.

        Returns:
            Parsed body.

        Raises:
            LookupFailed: The request failed, its status is not 200, or the
                body is no JSON.
        """
        try:
            response = http.get(url, params=params, headers=self.headers)
        except HttpError as exc:
            raise LookupFailed(f"Request failed: {exc.reason}") from exc
        if response.status != 200:
            raise LookupFailed(f"Request failed: HTTP {response.status}")
        try:
            return response.json()
        except ValueError as exc:
            raise LookupFailed("Response is no JSON") from exc

    def _throttle(self, ctx: Context) -> None:
        """Wait for the next slot for a request to Nominatim.

        Args:
            ctx: Context of the call (deadline).

        Raises:
            LookupFailed: The wait would pass the deadline.
        """
        with self._lock:
            wait = self._next - self._clock()
            if wait > ctx.remaining():
                raise LookupFailed("Nominatim's rate limit leaves no time")
            if wait > 0:
                self._sleep(wait)
            self._next = self._clock() + self.interval


class LocationCache:
    """Cache of looked-up locations by address, with TTLs and a size limit.

    Locations and misses (`None`) have TTLs of their own; beyond its size,
    the entry used least recently goes. Safe for concurrent use.
    """

    def __init__(
        self,
        size: int,
        ttl: float,
        miss_ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the cache.

        Args:
            size: Maximum number of entries; 0 turns the cache off.
            ttl: Seconds to keep locations; 0 keeps none.
            miss_ttl: Seconds to keep misses; 0 keeps none.
            clock: Clock, for tests.
        """
        self.size = size
        self.ttl = ttl
        self.miss_ttl = miss_ttl
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Location | None]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, address: str) -> tuple[bool, Location | None]:
        """Get the cached location of an address.

        Args:
            address: IP address.

        Returns:
            Whether the address is cached (and not expired), and its
            location (`None` for a cached miss).
        """
        with self._lock:
            entry = self._entries.get(address)
            if entry is None:
                return False, None
            expires, location = entry
            if expires <= self._clock():
                del self._entries[address]
                return False, None
            self._entries.move_to_end(address)
            return True, location

    def put(self, address: str, location: Location | None) -> None:
        """Cache the location of an address, or a miss.

        Args:
            address: IP address.
            location: Location, or `None` for a miss.
        """
        ttl = self.miss_ttl if location is None else self.ttl
        if self.size <= 0 or ttl <= 0:
            return
        with self._lock:
            self._entries[address] = (self._clock() + ttl, location)
            self._entries.move_to_end(address)
            while len(self._entries) > self.size:
                self._entries.popitem(last=False)
