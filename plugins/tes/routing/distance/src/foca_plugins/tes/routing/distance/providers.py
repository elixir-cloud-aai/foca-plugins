"""Location providers.

A provider gives the location of a public IP address, or `None` if it
knows none (a miss). Lookups that fail (e.g., a service is unavailable)
raise :py:class:`LookupFailed`.

- :py:class:`OfflineProvider` reads a local GeoIP database in MaxMind DB
  format, e.g., DB-IP's "IP to City Lite" (CC BY 4.0): no data leaves the
  gateway.
- :py:class:`DbipProvider` asks DB-IP's free API for the place of an
  address (city, region, country), then Nominatim (OpenStreetMap) for its
  coordinates: the address goes to db-ip.com, the place to Nominatim. It
  sends requests through `ctx.http` only, caches places and coordinates,
  and backs off when DB-IP refuses lookups (e.g., over its quota).
"""

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

from foca_plugins.tes.routing.distance.cache import TtlCache

Place = tuple[str, str, str]
"""City, region (may be empty) and country code of an address."""


class LookupFailed(Exception):
    """A lookup failed, e.g., as a service is unavailable; not a miss.

    Attributes:
        status: HTTP status of the response that failed the lookup, if any.
        transient: Whether the lookup failed only for lack of time in this
            call (e.g., a rate limit), so that it may succeed in the next
            call; such failures aren't cached.
    """

    def __init__(
        self, message: str, status: int | None = None, *, transient: bool = False
    ) -> None:
        """Create the error.

        Args:
            message: Message, without addresses.
            status: HTTP status of the response, if any.
            transient: Whether the lookup failed only for lack of time.
        """
        super().__init__(message)
        self.status = status
        self.transient = transient


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
    The reader is safe for concurrent use. Lookups after `close()` fail
    (hooks that the engine abandoned may still run at shutdown).
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
        self._closed = False

    def locate(self, address: str, ctx: Context) -> Location | None:
        """Locate an address in the database.

        Args:
            address: Public IP address.
            ctx: Context of the call (unused: local lookups are fast).

        Returns:
            Location, or `None` if the database has none for the address.

        Raises:
            LookupFailed: The database is closed.
        """
        if self._closed:
            raise LookupFailed("The GeoIP database is closed")
        try:
            record = self._reader.get(address)
        except ValueError as exc:
            # also raised by a reader closed during the lookup
            if self._closed:
                raise LookupFailed("The GeoIP database is closed") from exc
            return None
        if not isinstance(record, dict):
            return None
        location = record.get("location")
        if not isinstance(location, dict):
            return None
        return coordinates(location.get("latitude"), location.get("longitude"))

    def close(self) -> None:
        """Close the database."""
        self._closed = True
        self._reader.close()


class DbipProvider:
    """Locations from DB-IP's free API and Nominatim.

    Nominatim's usage policy allows at most one request per second, from an
    application that identifies itself in its user agent: the requests of a
    process take turns (`interval`), and none waits beyond the call's
    deadline. Before DB-IP is asked, the next free turn must be before the
    deadline (so that answers of DB-IP are rarely lost for lack of time);
    turns are booked only for requests to Nominatim. Places (by address)
    and coordinates (by place) are cached; when DB-IP refuses lookups (an
    `error` answer, e.g., over its daily quota, or HTTP 429), the provider
    asks it nothing for `backoff` seconds.

    Attributes:
        dbip_url: Base URL of DB-IP's API.
        nominatim_url: Base URL of Nominatim.
        headers: Headers of the requests.
        interval: Seconds between requests to Nominatim.
        backoff: Seconds to ask DB-IP nothing after it refused a lookup.
        cache_ttl: Seconds to keep places and coordinates.
    """

    def __init__(
        self,
        dbip_url: str,
        nominatim_url: str,
        user_agent: str,
        interval: float,
        *,
        backoff: float = 600,
        cache_size: int = 10000,
        cache_ttl: float = 86400,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the provider.

        Args:
            dbip_url: Base URL of DB-IP's API, without trailing slash.
            nominatim_url: Base URL of Nominatim, without trailing slash.
            user_agent: User agent of the requests.
            interval: Seconds between requests to Nominatim.
            backoff: Seconds to ask DB-IP nothing after it refused a lookup.
            cache_size: Maximum number of cached places and of cached
                coordinates; 0 caches none.
            cache_ttl: Seconds to keep places and coordinates.
            clock: Clock of the throttle, the back-off and the caches, for
                tests.
            sleep: Wait function of the throttle, for tests.
        """
        self.dbip_url = dbip_url
        self.nominatim_url = nominatim_url
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.interval = interval
        self.backoff = backoff
        self.cache_ttl = cache_ttl
        self._places: TtlCache[str, Place | None] = TtlCache(cache_size, clock)
        self._searches: TtlCache[str, Location | None] = TtlCache(cache_size, clock)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0
        self._blocked_until = 0.0

    def locate(self, address: str, ctx: Context) -> Location | None:
        """Locate an address: its place, then the place's coordinates.

        Args:
            address: Public IP address.
            ctx: Context of the call, with `ctx.http`.

        Returns:
            Location, or `None` if DB-IP knows no city and country of the
            address, or Nominatim no coordinates of the place.

        Raises:
            LookupFailed: A service failed or answered invalidly, DB-IP is
                backed off, or the rate limit of Nominatim leaves no time.
            PluginError: The context has no HTTP client.
        """
        http = ctx.http
        if http is None:
            raise PluginError("Provider 'dbip' needs ctx.http")
        cached, place = self._places.get(address)
        if not cached:
            if self._clock() < self._blocked_until:
                raise LookupFailed("DB-IP refused lookups recently; backing off")
            self._check_turn(ctx)
            place = self._place(http, address)
            self._places.put(address, place, self.cache_ttl)
        if place is None:
            return None
        city, region, country = place
        queries = [f"{city}, {country}"]
        if region:
            queries.insert(0, f"{city}, {region} {country}")
        for query in queries:
            cached, location = self._searches.get(query)
            if not cached:
                self._wait(self._book(ctx))
                location = self._search(http, query)
                self._searches.put(query, location, self.cache_ttl)
            if location is not None:
                return location
        return None

    def close(self) -> None:
        """Release resources (none)."""

    def _place(self, http: GuardedClient, address: str) -> Place | None:
        """Ask DB-IP for the place of an address.

        Args:
            http: Guarded HTTP client of the call.
            address: Public IP address.

        Returns:
            Place, or `None` if DB-IP knows no city and country.

        Raises:
            LookupFailed: DB-IP failed, refused the lookup (then the
                provider backs off) or answered invalidly.
        """
        try:
            body = self._get(http, f"{self.dbip_url}/{quote(address, safe='')}")
        except LookupFailed as exc:
            if exc.status == 429:
                self._back_off()
            raise
        if isinstance(body, dict) and "error" in body:
            self._back_off()
            raise LookupFailed("DB-IP refused the lookup")
        if not isinstance(body, dict):
            raise LookupFailed("DB-IP gave no place")
        city, region, country = (
            str(body.get(key) or "").strip()
            for key in ("city", "stateProv", "countryCode")
        )
        # `ZZ`: unknown country
        if not city or not country or country == "ZZ":
            return None
        return city, region, country

    def _search(self, http: GuardedClient, query: str) -> Location | None:
        """Ask Nominatim for the coordinates of a place.

        Args:
            http: Guarded HTTP client of the call.
            query: Free-form query of the place.

        Returns:
            Location of the first result, if valid.

        Raises:
            LookupFailed: Nominatim failed or answered invalidly.
        """
        results = self._get(
            http,
            f"{self.nominatim_url}/search",
            {"q": query, "format": "jsonv2", "limit": "1"},
        )
        if not isinstance(results, list):
            raise LookupFailed("Nominatim answered invalidly")
        if results and isinstance(results[0], dict):
            return coordinates(results[0].get("lat"), results[0].get("lon"))
        return None

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
            raise LookupFailed(
                f"Request failed: HTTP {response.status}", status=response.status
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LookupFailed("Response is no JSON") from exc

    def _back_off(self) -> None:
        """Ask DB-IP nothing for `backoff` seconds."""
        with self._lock:
            self._blocked_until = self._clock() + self.backoff

    def _check_turn(self, ctx: Context) -> None:
        """Check that the next free turn comes before the deadline.

        Args:
            ctx: Context of the call (deadline).

        Raises:
            LookupFailed: The next turn comes after the deadline (transient).
        """
        with self._lock:
            now = self._clock()
            if max(now, self._next) - now > ctx.remaining():
                raise LookupFailed(
                    "Nominatim's rate limit leaves no time", transient=True
                )

    def _book(self, ctx: Context) -> float:
        """Book the next turn for a request to Nominatim.

        Args:
            ctx: Context of the call (deadline).

        Returns:
            Time of the turn (of `clock`).

        Raises:
            LookupFailed: The turn would come after the deadline (transient).
        """
        with self._lock:
            now = self._clock()
            turn = max(now, self._next)
            if turn - now > ctx.remaining():
                raise LookupFailed(
                    "Nominatim's rate limit leaves no time", transient=True
                )
            self._next = turn + self.interval
            return turn

    def _wait(self, turn: float) -> None:
        """Wait for a booked turn, without holding the lock.

        Args:
            turn: Time of the turn (of `clock`).
        """
        wait = turn - self._clock()
        if wait > 0:
            self._sleep(wait)
