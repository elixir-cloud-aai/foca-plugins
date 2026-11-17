"""What WES backends support, from their service info, and its cache.

- :py:class:`Run`: what a run needs: its workflow type and engine, with
  their versions.
- :py:class:`Support`: what a backend supports, from the fields
  `workflow_type_versions` and `workflow_engine_versions` of its
  `GET /service-info` (:py:func:`parse_support`, :py:func:`fetch_support`).
- :py:class:`SupportCache`: the supports of backends, by URL, with TTLs, in
  the memory of the process.
"""

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import threading
import time
from typing import Any

from foca.utils.http import GuardedClient

TYPE_VERSIONS = "workflow_type_versions"
"""Field of the service info with the workflow types and their versions."""

ENGINE_VERSIONS = "workflow_engine_versions"
"""Field of the service info with the workflow engines and their versions."""

Versions = frozenset[str] | None
"""Versions of a workflow type or engine; `None` for any version."""

MAX_ENTRIES = 256
"""Maximum number of workflow types, and of engines, of a service info."""

MAX_VERSIONS = 1024
"""Maximum number of versions of the workflow types, and of the engines, of
a service info, in all."""

MAX_LENGTH = 256
"""Maximum length of names and versions; longer ones are left out."""


class ServiceInfoError(Exception):
    """The service info of a backend can't be read or has no supports."""


@dataclass(frozen=True)
class Run:
    """What a run needs of a backend.

    Attributes:
        workflow_type: Workflow type, e.g., `CWL`.
        workflow_type_version: Version of the workflow type, if any.
        workflow_engine: Workflow engine, if any (WES 1.1).
        workflow_engine_version: Version of the workflow engine, if any; only
            with `workflow_engine`.
    """

    workflow_type: str
    workflow_type_version: str | None = None
    workflow_engine: str | None = None
    workflow_engine_version: str | None = None


def _string(run: Mapping[str, Any], name: str) -> str | None:
    """Get a field of a run request if it is a string that isn't empty.

    Args:
        run: Run request.
        name: Field name.

    Returns:
        The value, or `None`.
    """
    value = run.get(name)
    return value if isinstance(value, str) and value else None


def run_needs(run: object) -> Run | None:
    """Get what a run needs of a backend, from its run request.

    Args:
        run: WES run request (cf. `foca.plugins.wes`).

    Returns:
        What the run needs, or `None` if it is no run request with a
        workflow type. Fields that aren't strings, or are empty, count as
        not set; an engine version counts only with an engine.
    """
    if not isinstance(run, Mapping):
        return None
    workflow_type = _string(run, "workflow_type")
    if workflow_type is None:
        return None
    engine = _string(run, "workflow_engine")
    return Run(
        workflow_type=workflow_type,
        workflow_type_version=_string(run, "workflow_type_version"),
        workflow_engine=engine,
        workflow_engine_version=(
            _string(run, "workflow_engine_version") if engine is not None else None
        ),
    )


def _merge(first: Versions, second: Versions) -> Versions:
    """Merge the versions of two entries of the same name.

    Args:
        first: Versions of one entry.
        second: Versions of the other.

    Returns:
        Their union; `None` (any version) if one of them is `None`.
    """
    if first is None or second is None:
        return None
    return first | second


def _versions(entries: object, key: str, field: str) -> dict[str, Versions]:
    """Read the versions of the workflow types or engines of a service info.

    Args:
        entries: Value of the field (`workflow_type_versions` or
            `workflow_engine_versions`).
        key: Key of the version list of each entry, e.g.,
            `workflow_type_version`.
        field: Name of the field, for errors.

    Returns:
        Versions by name, case-folded. Entries are objects with a list of
        versions under `key` (missing or empty: any version), or, as
        `workflow_engine_versions` of WES 1.0, a version string. Versions
        that aren't strings are left out; entries of other shapes are
        skipped, as if not listed; names and versions longer than
        :py:data:`MAX_LENGTH` are left out, too.

    Raises:
        ServiceInfoError: The field is no object, or it has more than
            :py:data:`MAX_ENTRIES` entries or :py:data:`MAX_VERSIONS`
            versions in all, so that no backend makes the cache large.
    """
    if not isinstance(entries, Mapping):
        raise ServiceInfoError(f"{field}: no object")
    if len(entries) > MAX_ENTRIES:
        raise ServiceInfoError(f"{field}: more than {MAX_ENTRIES} entries")
    versions: dict[str, Versions] = {}
    total = 0
    for name, entry in entries.items():
        # JSON keys are strings; others only in mappings built by hand
        if not isinstance(name, str) or len(name) > MAX_LENGTH:
            continue
        listed: Versions
        if isinstance(entry, str):
            values: object = [entry]
        elif isinstance(entry, Mapping):
            values = entry.get(key)
        else:
            continue
        if values is None or values == []:
            listed = None
        elif isinstance(values, list):
            total += len(values)
            if total > MAX_VERSIONS:
                raise ServiceInfoError(f"{field}: more than {MAX_VERSIONS} versions")
            listed = frozenset(
                value
                for value in values
                if isinstance(value, str) and len(value) <= MAX_LENGTH
            )
        else:
            continue
        folded = name.casefold()
        versions[folded] = (
            _merge(versions[folded], listed) if folded in versions else listed
        )
    return versions


@dataclass(frozen=True)
class Support:
    """What a backend supports, from its service info.

    Attributes:
        types: Versions of the workflow types, by case-folded type.
        engines: Versions of the workflow engines, by case-folded engine.
    """

    types: Mapping[str, Versions]
    engines: Mapping[str, Versions]

    def supports(self, run: Run) -> bool:
        """Check whether the backend supports a run.

        Args:
            run: What the run needs.

        Returns:
            Whether the backend lists the run's workflow type (ignoring
            case) with its version (if the run has one), and, if the run
            sets an engine, that engine (ignoring case) with its version (if
            the run has one). Versions are compared exactly.
        """
        if not _listed(self.types, run.workflow_type, run.workflow_type_version):
            return False
        return run.workflow_engine is None or _listed(
            self.engines, run.workflow_engine, run.workflow_engine_version
        )


def _listed(entries: Mapping[str, Versions], name: str, version: str | None) -> bool:
    """Check whether a name and version are listed.

    Args:
        entries: Versions by case-folded name.
        name: Name, e.g., a workflow type.
        version: Version, if any.

    Returns:
        Whether the name is listed, and the version too (if any), unless the
        name is listed with any version.
    """
    folded = name.casefold()
    if folded not in entries:
        return False
    versions = entries[folded]
    return version is None or versions is None or version in versions


def parse_support(info: object) -> Support:
    """Read what a backend supports from its service info.

    Args:
        info: Parsed service info.

    Returns:
        Supports: the workflow types of `workflow_type_versions` and the
        engines of `workflow_engine_versions` (none if missing or `null`).

    Raises:
        ServiceInfoError: The service info is no object, has no
            `workflow_type_versions`, or one of the fields is no object.
    """
    if not isinstance(info, Mapping):
        raise ServiceInfoError("service info: no object")
    if info.get(TYPE_VERSIONS) is None:
        raise ServiceInfoError(f"{TYPE_VERSIONS}: missing")
    types = _versions(info[TYPE_VERSIONS], "workflow_type_version", TYPE_VERSIONS)
    engines_field = info.get(ENGINE_VERSIONS)
    engines = (
        {}
        if engines_field is None
        else _versions(engines_field, "workflow_engine_version", ENGINE_VERSIONS)
    )
    return Support(types=types, engines=engines)


def fetch_support(http: GuardedClient, url: str) -> Support:
    """Fetch the service info of a backend and read what it supports.

    Args:
        http: HTTP client of the call (`ctx.http`).
        url: URL of the backend's service info.

    Returns:
        Supports of the backend.

    Raises:
        ServiceInfoError: The backend answered with another status than
            `200`, or with no valid service info.
        foca.utils.http.HttpError: The request failed (e.g., timeout).
    """
    response = http.get(url, headers={"Accept": "application/json"})
    if response.status != 200:
        raise ServiceInfoError(f"status {response.status}")
    try:
        info = response.json()
    except ValueError:
        raise ServiceInfoError("no valid JSON") from None
    return parse_support(info)


class SupportCache:
    """Supports of backends, by URL, each kept for a TTL of its own.

    `None` entries stand for backends whose service info couldn't be read.
    Beyond its size, the entry used least recently goes. Safe for
    concurrent use.

    Attributes:
        size: Maximum number of entries; 0 turns the cache off.
    """

    def __init__(self, size: int, clock: Callable[[], float] = time.monotonic) -> None:
        """Create the cache.

        Args:
            size: Maximum number of entries; 0 turns the cache off.
            clock: Clock, for tests.
        """
        self.size = size
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Support | None]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, url: str) -> tuple[bool, Support | None]:
        """Get the cached supports of a backend.

        Args:
            url: URL of the backend's service info.

        Returns:
            Whether the URL is cached (and not expired), and its supports
            (`None` if not cached, or unreadable).
        """
        with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return False, None
            expires, support = entry
            if expires <= self._clock():
                del self._entries[url]
                return False, None
            self._entries.move_to_end(url)
            return True, support

    def put(self, url: str, support: Support | None, ttl: float) -> None:
        """Cache the supports of a backend.

        Args:
            url: URL of the backend's service info.
            support: Supports; `None` if unreadable.
            ttl: Seconds to keep them; 0 keeps them not at all.
        """
        if self.size <= 0 or ttl <= 0:
            return
        with self._lock:
            self._entries[url] = (self._clock() + ttl, support)
            self._entries.move_to_end(url)
            while len(self._entries) > self.size:
                self._entries.popitem(last=False)
