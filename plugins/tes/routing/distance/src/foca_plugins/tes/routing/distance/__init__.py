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

Host names are resolved in parallel, within a time budget per call and
with a bounded number of resolver threads per process. Resolutions and
lookups (also misses and, briefly, failures) are cached per process, with
TTLs. Annotations hold counts only: hosts, addresses and places of inputs
could be personal data.

The plugin API is documented in FOCA's `docs/plugins.md`
(https://foca.readthedocs.io/en/latest/plugins/).
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import time
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

from foca_plugins.tes.routing.distance.cache import TtlCache
from foca_plugins.tes.routing.distance.geo import (
    Resolver,
    distance_km,
    input_urls,
    literal_address,
    public_address,
    url_host,
)
from foca_plugins.tes.routing.distance.providers import (
    DbipProvider,
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
        dbip_backoff: Seconds to ask DB-IP nothing after it refused a
            lookup (e.g., over its daily quota).
        dbip_max_lookups: Maximum number of lookups by DB-IP and Nominatim
            per call (not counting cached ones), for the inputs and for the
            backends each; further addresses are not located in that call,
            so that no single task uses up DB-IP's quota.
        locate_backends: Whether backends without configured location are
            located by their address; if not, they rank after the located
            ones.
        max_hosts: Maximum number of input hosts to locate per task; inputs
            on further hosts are not located.
        resolve_timeout: Seconds to resolve the host names of a call, in
            total (names are resolved in parallel): half for inputs, the
            rest for backends.
        resolve_threads: Maximum number of host names resolved at a time,
            per process, for inputs and for backends each; a call resolves
            at most a quarter of them for its inputs; further names are not
            resolved.
        resolve_cache_ttl: Seconds to keep the addresses of host names,
            also of names that couldn't be resolved.
        cache_size: Maximum number of entries of each cache (addresses,
            host names of inputs and of backends, and, with `dbip`, places
            and coordinates); 0 turns the caches off.
        cache_ttl: Seconds to keep located addresses.
        cache_miss_ttl: Seconds to keep addresses without location.
        cache_failure_ttl: Seconds to keep addresses whose lookup failed as
            unlocated, so that failures aren't retried on every request.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["offline", "dbip"] = "offline"
    database: FilePath | None = None
    dbip_url: str = "https://api.db-ip.com/v2/free"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    # visible ASCII and spaces, not starting with a space (a valid header)
    user_agent: str = Field(
        default="foca-plugin-tes-routing-distance",
        max_length=256,
        pattern=r"^[\x21-\x7e][\x20-\x7e]*$",
    )
    nominatim_interval: float = Field(default=1.0, ge=0, le=60)
    dbip_backoff: float = Field(default=600, ge=0, le=86400)
    dbip_max_lookups: int = Field(default=5, ge=1, le=1000)
    locate_backends: bool = True
    max_hosts: int = Field(default=20, ge=1, le=1000)
    resolve_timeout: float = Field(default=2.0, gt=0, le=60)
    resolve_threads: int = Field(default=16, ge=1, le=256)
    resolve_cache_ttl: int = Field(default=300, ge=0)
    cache_size: int = Field(default=10000, ge=0)
    cache_ttl: int = Field(default=86400, ge=0)
    cache_miss_ttl: int = Field(default=3600, ge=0)
    cache_failure_ttl: int = Field(default=60, ge=0)

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
        cache: Cache of looked-up locations, by address.
        settings: Admin settings.
        ctx: Context of the call.
        max_lookups: Maximum number of lookups by the provider per batch of
            hosts (inputs, backends); `None` for no limit.
        hosts: Locations of the hosts located in this call.
        lookups: Number of lookups by the provider.
        cache_hits: Number of addresses found in the cache.
        failed_lookups: Number of failed lookups.
        skipped_lookups: Number of addresses not looked up, for lack of time
            or as `max_lookups` was reached.
        batch_lookups: Number of lookups by the provider in the current
            batch.
    """

    provider: Provider
    cache: TtlCache[str, Location | None]
    settings: DistanceConfig
    ctx: Context
    max_lookups: int | None
    hosts: dict[str, Location | None] = field(default_factory=dict)
    lookups: int = 0
    cache_hits: int = 0
    failed_lookups: int = 0
    skipped_lookups: int = 0
    batch_lookups: int = 0

    def locate(self, hosts: Iterable[str], resolver: Resolver, deadline: float) -> None:
        """Locate a batch of hosts not located in this call yet (cf. `hosts`).

        Host names are resolved together, in parallel.

        Args:
            hosts: Host names or IP addresses.
            resolver: Resolver of the host names.
            deadline: Deadline of their resolution (`time.monotonic()`
                seconds).
        """
        self.batch_lookups = 0
        new = [host for host in dict.fromkeys(hosts) if host not in self.hosts]
        literals = {host: literal_address(host) for host in new}
        names = [host for host, literal in literals.items() if literal is None]
        resolved = resolver.resolve(names, deadline)
        for host in new:
            literal = literals[host]
            addresses = resolved.get(host, ()) if literal is None else (literal,)
            address = public_address(addresses)
            self.hosts[host] = None if address is None else self._lookup(address)

    def _lookup(self, address: str) -> Location | None:
        """Look up the location of a public address, from the cache if there.

        Args:
            address: Public IP address.

        Returns:
            Location, if known.
        """
        cached, location = self.cache.get(address)
        if cached:
            self.cache_hits += 1
            return location
        if self.ctx.remaining() <= 0 or (
            self.max_lookups is not None and self.batch_lookups >= self.max_lookups
        ):
            self.skipped_lookups += 1
            return None
        self.lookups += 1
        self.batch_lookups += 1
        try:
            location = self.provider.locate(address, self.ctx)
        except LookupFailed as exc:
            self.failed_lookups += 1
            self.ctx.log.warning("Lookup failed: %s", exc)
            if not exc.transient:
                # keeps a location that a concurrent call found meanwhile
                self.cache.add(address, None, self.settings.cache_failure_ttl)
            return None
        settings = self.settings
        ttl = settings.cache_miss_ttl if location is None else settings.cache_ttl
        self.cache.put(address, location, ttl)
        return location


class Distance(Plugin[DistanceConfig, EmptyModel]):
    """Route to the backends closest to the task's inputs.

    Attributes:
        provider: Provider of locations (cf. `DistanceConfig.provider`);
            kept after `close()`, when its lookups fail.
        cache: Cache of looked-up locations, by address, shared by all
            calls.
        input_resolver: Resolver of the host names of inputs, shared by
            all calls; a call starts at most a quarter of its threads.
        backend_resolver: Resolver of the host names of backends, with
            threads of its own, so that names of inputs (which clients
            choose) can't hold up backends.
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
    cache: TtlCache[str, Location | None]
    input_resolver: Resolver
    backend_resolver: Resolver
    settings: DistanceConfig

    def setup(self, config: DistanceConfig) -> None:
        """Open the provider and create the cache; closes earlier ones.

        Idempotent, as `setup()` may run more than once.

        Args:
            config: Validated admin settings.

        Raises:
            ValueError: The provider is `offline`, but there is no database
                (prevented by `DistanceConfig`'s validation).
        """
        self.close()
        self.settings = config
        if config.provider == "offline":
            if config.database is None:
                raise ValueError("provider 'offline' needs 'database'")
            self.provider = OfflineProvider(config.database)
        else:
            self.provider = DbipProvider(
                config.dbip_url,
                config.nominatim_url,
                config.user_agent,
                config.nominatim_interval,
                backoff=config.dbip_backoff,
                cache_size=config.cache_size,
                cache_ttl=config.cache_ttl,
                miss_ttl=config.cache_miss_ttl,
            )
        self.cache = TtlCache(config.cache_size)
        self.input_resolver = Resolver(
            config.resolve_threads,
            config.resolve_cache_ttl,
            config.cache_size,
            per_call=max(1, config.resolve_threads // 4),
        )
        self.backend_resolver = Resolver(
            config.resolve_threads, config.resolve_cache_ttl, config.cache_size
        )

    def close(self) -> None:
        """Close the provider, if any; it stays, and its lookups fail."""
        provider: Provider | None = getattr(self, "provider", None)
        if provider is not None:
            provider.close()

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
        settings = self.settings
        locator = _Locator(
            provider=self.provider,
            cache=self.cache,
            settings=settings,
            ctx=ctx,
            max_lookups=(
                settings.dbip_max_lookups if settings.provider == "dbip" else None
            ),
        )
        # inputs per host: distances are computed once per host, weighted
        weights = Counter(host for url in urls if (host := url_host(url)))
        hosts = list(weights)
        # names of inputs may take half of the time, backends the rest
        budget = min(settings.resolve_timeout, ctx.remaining())
        started = time.monotonic()
        locator.locate(
            hosts[: settings.max_hosts], self.input_resolver, started + budget / 2
        )
        inputs = [
            (location, weights[host])
            for host in hosts
            if (location := locator.hosts.get(host)) is not None
        ]
        ranked: list[tuple[float, Backend]] = []
        unlocated: list[Backend] = []
        if inputs:
            if settings.locate_backends:
                locator.locate(
                    (
                        host
                        for backend in candidates
                        if backend.location is None
                        and (host := url_host(backend.url)) is not None
                    ),
                    self.backend_resolver,
                    started + budget,
                )
            for backend in candidates:
                location = self._backend_location(backend, locator)
                if location is None:
                    unlocated.append(backend)
                else:
                    total = sum(
                        weight * distance_km(location, place)
                        for place, weight in inputs
                    )
                    ranked.append((total, backend))
        ctx.record("inputs", len(urls))
        ctx.record("input_hosts", len(hosts))
        ctx.record("located_inputs", sum(weight for _, weight in inputs))
        ctx.record("located_backends", len(ranked))
        ctx.record("lookups", locator.lookups)
        ctx.record("cache_hits", locator.cache_hits)
        ctx.record("failed_lookups", locator.failed_lookups)
        ctx.record("skipped_lookups", locator.skipped_lookups)
        if not inputs:
            raise NotApplicable("No input of the task could be located")
        if not ranked:
            raise NotApplicable("No candidate backend could be located")
        ranked.sort(key=lambda item: item[0])  # stable: ties keep their order
        return [backend for _, backend in ranked] + unlocated

    def _backend_location(self, backend: Backend, locator: _Locator) -> Location | None:
        """Get the location of a backend.

        Args:
            backend: Candidate backend.
            locator: Locator of the call.

        Returns:
            The configured location, or else that of its host's address
            (unless `locate_backends` is off), if located.
        """
        if backend.location is not None:
            return backend.location
        if not self.settings.locate_backends:
            return None
        host = url_host(backend.url)
        return locator.hosts.get(host) if host is not None else None
