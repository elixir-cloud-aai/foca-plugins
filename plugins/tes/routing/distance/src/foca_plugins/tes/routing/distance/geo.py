"""Geographic helpers: distances, input URLs and host addresses.

Plain functions without state, used by the plugin and its providers.
"""

import ipaddress
import math
import socket
import threading
from typing import Any
from urllib.parse import urlsplit

from foca.plugins import Location
from foca.utils.http import is_public_address

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


def resolve(host: str, timeout: float) -> list[str]:
    """Resolve a host name with the system resolver, within a time limit.

    The resolver cannot be interrupted, so it runs in a daemon thread that
    is abandoned when the time is up (as FOCA's guarded HTTP client does).

    Args:
        host: Host name.
        timeout: Seconds for the resolution.

    Returns:
        Addresses, in the resolver's order, without duplicates; none if the
        host can't be resolved in time.
    """
    outcome: dict[str, list[Any]] = {}
    done = threading.Event()

    def lookup() -> None:
        try:
            outcome["infos"] = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            outcome["infos"] = []
        finally:
            done.set()

    threading.Thread(target=lookup, name="distance-resolve", daemon=True).start()
    if not done.wait(max(timeout, 0)):
        return []
    return list(dict.fromkeys(str(info[4][0]) for info in outcome["infos"]))


def public_address(host: str, timeout: float) -> str | None:
    """Get the public address of a host, to look up its location.

    Args:
        host: Host name or IP address.
        timeout: Seconds to resolve a host name.

    Returns:
        The host itself if it is a public IP address, or else the first
        public address that the host name resolves to; `None` if there is
        none (e.g., private addresses, unknown hosts), as only public
        addresses have meaningful locations, and only they may be sent to
        online providers.
    """
    try:
        candidates = [str(ipaddress.ip_address(host))]
    except ValueError:
        candidates = resolve(host, timeout)
    for address in candidates:
        if is_public_address(ipaddress.ip_address(address)):
            return address
    return None
