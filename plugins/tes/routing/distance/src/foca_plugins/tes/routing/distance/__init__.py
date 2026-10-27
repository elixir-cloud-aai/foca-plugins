"""Plugin `tes.routing.distance`: route to the backends closest to the data.

The successor of proTES's distance-based task distribution
(`TaskDistributionDistance`). The route step ranks the candidate backends
by the summed great-circle distance to the locations of the task's inputs,
so that tasks run close to their data.

Locations come from:

- the backend list, for backends with a configured location
  (`Backend.location`);
- the public IP address of a host, looked up by a provider (cf.
  :py:mod:`.providers`): by default a local GeoIP database (`offline`;
  DB-IP's "IP to City Lite" is recommended, CC BY 4.0, "IP Geolocation by
  DB-IP", https://db-ip.com), or, as an explicit opt-in, DB-IP's free API
  and Nominatim (`dbip`), which receive the addresses and places.

Lookups (also misses) are cached per process, with TTLs; failed lookups
aren't cached. Annotations hold counts only: hosts, addresses and places
of inputs could be personal data.

The plugin API is documented in FOCA's `docs/plugins.md`
(https://foca.readthedocs.io/en/latest/plugins/).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from foca.plugins import Backend, Context, Location, NotApplicable, Plugin
from foca.plugins.base import EmptyModel
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FilePath,
    field_validator,
    model_validator,
)

from foca_plugins.tes.routing.distance.geo import (
    distance_km,
    input_urls,
    public_address,
    url_host,
)
from foca_plugins.tes.routing.distance.providers import (
    DbipProvider,
    LocationCache,
    LookupFailed,
    OfflineProvider,
    Provider,
)


class DistanceConfig(BaseModel):
    """Admin settings (`plugins.available."tes.routing.distance".config`).

    Attributes:
        provider: Where locations of addresses come from: `offline` (a
            local GeoIP database, `database`) or `dbip` (DB-IP's free API
            and Nominatim; they receive the addresses of the hosts of
            inputs and backends, and their places).
        database: Path of the GeoIP database (MaxMind DB format, e.g.,
            DB-IP's "IP to City Lite"); required for `offline`.
        dbip_url: Base URL of DB-IP's API (`dbip`).
        nominatim_url: Base URL of Nominatim (`dbip`), e.g., of an
            instance of your own.
        user_agent: User agent of the requests to DB-IP and Nominatim;
            Nominatim's usage policy asks for one that identifies the
            application (e.g., with a contact).
        nominatim_interval: Seconds between requests to Nominatim, per
            process (its usage policy allows one per second).
        locate_backends: Whether backends without configured location are
            located by their address; if not, they rank after the located
            ones.
        max_hosts: Maximum number of input hosts to locate per task; inputs
            on further hosts are not located.
        resolve_timeout: Seconds to resolve a host name.
        cache_size: Maximum number of cached addresses; 0 turns the cache
            off.
        cache_ttl: Seconds to keep located addresses.
        cache_miss_ttl: Seconds to keep addresses without location.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["offline", "dbip"] = "offline"
    database: FilePath | None = None
    dbip_url: str = "https://api.db-ip.com/v2/free"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    user_agent: str = Field(
        default="foca-plugin-tes-routing-distance", min_length=1, max_length=256
    )
    nominatim_interval: float = Field(default=1.0, ge=0, le=60)
    locate_backends: bool = True
    max_hosts: int = Field(default=20, ge=1, le=1000)
    resolve_timeout: float = Field(default=5.0, gt=0, le=60)
    cache_size: int = Field(default=10000, ge=0)
    cache_ttl: int = Field(default=86400, ge=0)
    cache_miss_ttl: int = Field(default=3600, ge=0)

    @field_validator("dbip_url", "nominatim_url")
    @classmethod
    def _https(cls, url: str) -> str:
        """Require `https` base URLs, kept without trailing slash.

        Args:
            url: Base URL.

        Returns:
            URL without trailing slash.

        Raises:
            ValueError: The URL is not an `https` URL.
        """
        if not url.startswith("https://") or len(url) <= len("https://"):
            raise ValueError("must be an https URL")
        return url.rstrip("/")

    @model_validator(mode="after")
    def _database_for_offline(self) -> "DistanceConfig":
        """Require a database for the offline provider.

        Returns:
            The settings.

        Raises:
            ValueError: The provider is `offline`, but there is no database.
        """
        if self.provider == "offline" and self.database is None:
            raise ValueError("provider 'offline' needs 'database'")
        return self


@dataclass
class _Locator:
    """Locations of the hosts of one call, with counts for the annotations.

    Attributes:
        provider: Provider of locations.
        cache: Cache of the plugin.
        ctx: Context of the call.
        resolve_timeout: Seconds to resolve a host name.
        hosts: Locations of the hosts located in this call.
        lookups: Number of lookups by the provider.
        cache_hits: Number of addresses found in the cache.
        failed_lookups: Number of failed lookups.
    """

    provider: Provider
    cache: LocationCache
    ctx: Context
    resolve_timeout: float
    hosts: dict[str, Location | None] = field(default_factory=dict)
    lookups: int = 0
    cache_hits: int = 0
    failed_lookups: int = 0

    def locate(self, host: str) -> Location | None:
        """Locate a host, once per call.

        Args:
            host: Host name or IP address.

        Returns:
            Location of its public address, if known.
        """
        if host not in self.hosts:
            self.hosts[host] = self._locate(host)
        return self.hosts[host]

    def _locate(self, host: str) -> Location | None:
        """Locate a host by its public address.

        Args:
            host: Host name or IP address.

        Returns:
            Location, if known.
        """
        timeout = min(self.resolve_timeout, self.ctx.remaining())
        address = public_address(host, timeout)
        if address is None:
            return None
        cached, location = self.cache.get(address)
        if cached:
            self.cache_hits += 1
            return location
        self.lookups += 1
        try:
            location = self.provider.locate(address, self.ctx)
        except LookupFailed as exc:
            self.failed_lookups += 1
            self.ctx.log.warning("Lookup failed: %s", exc)
            return None
        self.cache.put(address, location)
        return location


class Distance(Plugin[DistanceConfig, EmptyModel]):
    """Route to the backends closest to the task's inputs.

    Attributes:
        provider: Provider of locations (cf. `DistanceConfig.provider`).
        cache: Cache of looked-up locations, shared by all calls.
        settings: Validated admin settings.
    """

    Config = DistanceConfig
    apis = frozenset({"tes"})
    capabilities = frozenset({"routing-by-data-locality"})
    maturity = "experimental"
    description = (
        "Ranks the candidate backends by their summed distance to the"
        " locations of the task's inputs, closest first; backends without"
        " location follow, in their order. Locations come from the backend"
        " list or from the public addresses of hosts, by default from a local"
        " GeoIP database. Does not apply to tasks without input URLs that can"
        " be located."
    )
    data_transfers = (
        "db-ip.com (api.db-ip.com): public IP addresses of the hosts of task"
        " inputs and backends; only if the admin enables provider 'dbip'",
        "OpenStreetMap Nominatim (or the instance the admin configures): city,"
        " region and country of those addresses; only with provider 'dbip'",
    )

    provider: Provider
    cache: LocationCache
    settings: DistanceConfig

    def setup(self, config: DistanceConfig) -> None:
        """Open the provider and create the cache; closes earlier ones.

        Idempotent, as `setup()` may run more than once.

        Args:
            config: Validated admin settings.
        """
        self.close()
        self.settings = config
        if config.provider == "offline":
            assert config.database is not None  # noqa: S101 (validated)
            self.provider = OfflineProvider(config.database)
        else:
            self.provider = DbipProvider(
                config.dbip_url,
                config.nominatim_url,
                config.user_agent,
                config.nominatim_interval,
            )
        self.cache = LocationCache(
            size=config.cache_size,
            ttl=config.cache_ttl,
            miss_ttl=config.cache_miss_ttl,
        )

    def close(self) -> None:
        """Close the provider, if any."""
        provider = getattr(self, "provider", None)
        if provider is not None:
            provider.close()
            del self.provider

    def on_route(self, candidates: Sequence[Backend], ctx: Context) -> list[Backend]:
        """Rank the candidates by their distance to the task's inputs.

        Args:
            candidates: Candidate backends, in order.
            ctx: Context of the call; `ctx.original` is the task as
                submitted.

        Returns:
            All candidates: the located ones by summed distance to the
            located inputs (ties in their order), then the others, in their
            order.

        Raises:
            NotApplicable: The task has no input URLs, no input could be
                located, or no candidate could be located.
        """
        urls = input_urls(ctx.original)
        if not urls:
            raise NotApplicable("The task has no input URLs")
        locator = _Locator(
            self.provider, self.cache, ctx, self.settings.resolve_timeout
        )
        hosts = list(dict.fromkeys(host for url in urls if (host := url_host(url))))
        for host in hosts[: self.settings.max_hosts]:
            locator.locate(host)
        inputs = [
            location
            for url in urls
            if (host := url_host(url)) is not None
            and (location := locator.hosts.get(host)) is not None
        ]
        ranked: list[tuple[float, Backend]] = []
        unlocated: list[Backend] = []
        if inputs:
            for backend in candidates:
                location = self._backend_location(backend, locator)
                if location is None:
                    unlocated.append(backend)
                else:
                    total = sum(distance_km(location, i) for i in inputs)
                    ranked.append((total, backend))
        ctx.record("inputs", len(urls))
        ctx.record("input_hosts", len(hosts))
        ctx.record("located_inputs", len(inputs))
        ctx.record("located_backends", len(ranked))
        ctx.record("lookups", locator.lookups)
        ctx.record("cache_hits", locator.cache_hits)
        ctx.record("failed_lookups", locator.failed_lookups)
        if not inputs:
            raise NotApplicable("No input of the task could be located")
        if not ranked:
            raise NotApplicable("No candidate backend could be located")
        ranked.sort(key=lambda item: item[0])  # stable: ties keep their order
        return [backend for _, backend in ranked] + unlocated

    def _backend_location(
        self, backend: Backend, locator: "_Locator"
    ) -> Location | None:
        """Get the location of a backend.

        Args:
            backend: Candidate backend.
            locator: Locator of the call.

        Returns:
            The configured location, or else that of its host's address
            (unless `locate_backends` is off), if known.
        """
        if backend.location is not None:
            return backend.location
        if not self.settings.locate_backends:
            return None
        host = url_host(backend.url)
        return locator.locate(host) if host is not None else None
