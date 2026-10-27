"""Geographic helpers: distances, input URLs and host addresses.

Plain functions, and the resolver of host names, used by the plugin and its
providers.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import ipaddress
import math
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from foca.plugins import Location
from foca.utils.http import is_public_address

from foca_plugins.tes.routing.distance.cache import TtlCache

EARTH_RADIUS_KM = 6371.0088
"""Mean radius of the Earth (IUGG), in kilometres."""


def distance_km(a: Location, b: Location) -> float:
    """Get the great-circle distance between two locations.

    Uses the haversine formula on a sphere with the Earth's mean radius,
    which is accurate to about 0.5%: enough to rank backends.

    Args:
        a: Location.
        b: Location.

    Returns:
        Distance, in kilometres.
    """
    lat_a, lat_b = math.radians(a.latitude), math.radians(b.latitude)
    d_lat = lat_b - lat_a
    d_lon = math.radians(b.longitude - a.longitude)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def input_urls(payload: Any) -> list[str]:
    """Get the URLs of the inputs of a TES task.

    Args:
        payload: Task, as submitted.

    Returns:
        The non-empty `url`s of the task's `inputs`, each once, in order;
        none if the task has no inputs or is malformed.
    """
    if not isinstance(payload, dict):
        return []
    inputs = payload.get("inputs")
    if not isinstance(inputs, list):
        return []
    urls = (item.get("url") for item in inputs if isinstance(item, dict))
    return list(dict.fromkeys(url for url in urls if isinstance(url, str) and url))


def url_host(url: str) -> str | None:
    """Get the host of a URL.

    Args:
        url: URL, of any scheme (e.g., `https`, `ftp`, `s3`).

    Returns:
        Host name or IP address, lowercase, without user info, port,
        brackets or trailing dot; `None` if the URL has no host or is
        invalid.
    """
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.rstrip(".") or None if host else None


def literal_address(host: str) -> str | None:
    """Get the IP address that a host is, if it is one.

    Args:
        host: Host name or IP address.

    Returns:
        The address, normalized; `None` for host names.
    """
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def public_address(addresses: Iterable[str]) -> str | None:
    """Get the first public address, to look up its location.

    Only public addresses have meaningful locations, and only they may be
    sent to online providers.

    Args:
        addresses: IP addresses; invalid ones are skipped.

    Returns:
        The first public address; `None` if there is none.
    """
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if is_public_address(ip):
            return str(ip)
    return None


@dataclass
class _Job:
    """Resolution of a host name in a thread of its own.

    Attributes:
        host: Host name.
        done: Set when the resolution is over.
        addresses: Addresses, once done.
    """

    host: str
    done: threading.Event = field(default_factory=threading.Event)
    addresses: tuple[str, ...] = ()


class Resolver:
    """Resolves host names with the system resolver, bounded in time and threads.

    The system resolver can't be interrupted, so each name is resolved in a
    daemon thread, which callers abandon at their deadline (as FOCA's
    guarded HTTP client does). To keep slow or hostile name servers from
    piling up threads, a process runs at most `threads` resolutions at a
    time and at most one per name; names beyond that stay unresolved.
    Results, also failures, are cached for `ttl`, so that a name is not
    resolved again on every request.
    """

    def __init__(
        self,
        threads: int,
        ttl: float,
        cache_size: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the resolver.

        Args:
            threads: Maximum number of resolutions at a time.
            ttl: Seconds to keep results; 0 keeps none.
            cache_size: Maximum number of cached names; 0 caches none.
            clock: Clock of the cache, for tests.
        """
        self.ttl = ttl
        self.cache: TtlCache[str, tuple[str, ...]] = TtlCache(cache_size, clock)
        self._slots = threading.BoundedSemaphore(threads)
        self._lock = threading.Lock()
        self._pending: set[str] = set()

    def resolve(
        self, hosts: Iterable[str], deadline: float
    ) -> dict[str, tuple[str, ...]]:
        """Resolve host names in parallel, until a deadline.

        Args:
            hosts: Host names.
            deadline: Deadline, in `time.monotonic()` seconds; no
                resolution starts after it.

        Returns:
            Addresses of the names that were cached or resolved in time, in
            the resolver's order, without duplicates (none for names that
            can't be resolved); other names are left out.
        """
        results: dict[str, tuple[str, ...]] = {}
        jobs: list[_Job] = []
        for host in dict.fromkeys(hosts):
            cached, addresses = self.cache.get(host)
            if cached:
                results[host] = addresses or ()
            elif time.monotonic() < deadline and (job := self._start(host)):
                jobs.append(job)
        for job in jobs:
            if job.done.wait(max(deadline - time.monotonic(), 0)):
                results[job.host] = job.addresses
        return results

    def _start(self, host: str) -> _Job | None:
        """Start resolving a name, if a thread is free and none resolves it.

        Args:
            host: Host name.

        Returns:
            The job, or `None` if it was not started.
        """
        with self._lock:
            if host in self._pending or not self._slots.acquire(blocking=False):
                return None
            self._pending.add(host)
        job = _Job(host)
        try:
            threading.Thread(
                target=self._run, args=(job,), name="distance-resolve", daemon=True
            ).start()
        except RuntimeError:  # no more threads
            self._release(host)
            return None
        return job

    def _run(self, job: _Job) -> None:
        """Resolve a name and cache the result (in the job's thread).

        Args:
            job: Job.
        """
        try:
            infos = socket.getaddrinfo(job.host, None, type=socket.SOCK_STREAM)
            job.addresses = tuple(dict.fromkeys(str(info[4][0]) for info in infos))
        except (OSError, UnicodeError):
            job.addresses = ()
        finally:
            self.cache.put(job.host, job.addresses, self.ttl)
            self._release(job.host)
            job.done.set()

    def _release(self, host: str) -> None:
        """Free the thread slot of a name.

        Args:
            host: Host name.
        """
        with self._lock:
            self._pending.discard(host)
            self._slots.release()
