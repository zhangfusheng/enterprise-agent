"""Tenant-scoped, bounded cache for research search and fetch results.

The cache is deliberately below the research quality gates: it stores only
provider-neutral search candidates and extracted page payloads. Ranking,
freshness evaluation, deduplication, and citation validation always run on the
current request. Cache failures are best-effort and never fail research.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit
from weakref import WeakValueDictionary

from enterprise_agent.contracts.context import ExecutionContext

CacheState = Literal["hit", "miss", "stale", "disabled", "error"]
CacheStage = Literal["search", "fetch"]
T = TypeVar("T")


class CacheLockUnavailable(RuntimeError):
    """The distributed lock could not be acquired; use local protection."""


@dataclass(frozen=True, slots=True)
class CacheEntry:
    value: Any
    expires_at: float


@dataclass(frozen=True, slots=True)
class CacheLookup:
    state: CacheState
    value: Any = None


class CacheStore(Protocol):
    def get(self, key: str) -> CacheEntry | None: ...

    def set(self, key: str, entry: CacheEntry) -> None: ...

    def delete(self, key: str) -> None: ...


class MemoryCacheStore:
    """Bounded process-local store used for development and single-worker use."""

    def __init__(self, *, max_entries: int = 1_000, max_value_bytes: int = 1_000_000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if max_value_bytes < 1:
            raise ValueError("max_value_bytes must be positive")
        self.max_entries = max_entries
        self.max_value_bytes = max_value_bytes
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return copy.deepcopy(entry) if entry is not None else None

    def set(self, key: str, entry: CacheEntry) -> None:
        encoded = json.dumps(entry.value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > self.max_value_bytes:
            return
        with self._lock:
            self._entries[key] = copy.deepcopy(entry)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


class RedisCacheStore:
    """Redis-backed store for multi-worker deployments.

    ``redis`` is an optional runtime dependency so the default offline/test
    path does not need a Redis server. Selecting the Redis backend fails fast
    if the package or URL is missing.
    """

    def __init__(
        self,
        url: str,
        *,
        namespace: str = "enterprise-agent:research",
        stale_grace_seconds: int = 60,
    ) -> None:
        if not url.strip():
            raise ValueError("Redis cache URL must not be blank")
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise RuntimeError("Redis cache requires the 'redis' package") from exc
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._namespace = namespace.strip(":")
        self._stale_grace_seconds = max(0, stale_grace_seconds)

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def get(self, key: str) -> CacheEntry | None:
        raw = cast(str | bytes | bytearray | None, self._client.get(self._key(key)))
        if raw is None:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, Mapping):
            self.delete(key)
            return None
        expires_at = payload.get("expires_at")
        if not isinstance(expires_at, int | float):
            self.delete(key)
            return None
        return CacheEntry(payload.get("value"), float(expires_at))

    def set(self, key: str, entry: CacheEntry) -> None:
        ttl = max(1, int(entry.expires_at - time.time()))
        payload = json.dumps(
            {"expires_at": entry.expires_at, "value": entry.value},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._client.set(self._key(key), payload, ex=ttl + self._stale_grace_seconds)

    def delete(self, key: str) -> None:
        self._client.delete(self._key(key))

    @contextmanager
    def key_lock(self, key: str) -> Iterator[None]:
        lock_key = self._key("lock:" + hashlib.sha256(key.encode()).hexdigest())
        try:
            lock = self._client.lock(lock_key, timeout=60, blocking_timeout=30)
            acquired = lock.acquire()
        except Exception as exc:  # pragma: no cover - requires Redis failure
            raise CacheLockUnavailable from exc
        if not acquired:
            raise CacheLockUnavailable("could not acquire Redis cache lock")
        try:
            yield
        finally:
            try:
                lock.release()
            except Exception:
                pass

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True, slots=True)
class ResearchCachePolicy:
    """Feature flags and rollout policy for one cache instance."""

    key_version: str = "research-cache-v1"
    read_enabled: bool = False
    write_enabled: bool = False
    fetch_enabled: bool = False
    gray_percentage: int = 0
    search_ttl_seconds: int = 300
    fetch_ttl_seconds: int = 900
    stale_if_error: bool = True
    stale_grace_seconds: int = 60
    observability_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.key_version.strip():
            raise ValueError("cache key version must not be blank")
        if not 0 <= self.gray_percentage <= 100:
            raise ValueError("gray_percentage must be between 0 and 100")
        if self.search_ttl_seconds < 1 or self.fetch_ttl_seconds < 1:
            raise ValueError("cache TTLs must be positive")
        if self.stale_grace_seconds < 0:
            raise ValueError("stale grace must not be negative")

    def enabled_for(self, context: ExecutionContext) -> bool:
        if self.gray_percentage <= 0:
            return False
        if self.gray_percentage >= 100:
            return True
        rollout_key = f"{context.tenant_id}\x00{context.run_id}".encode()
        bucket = int.from_bytes(hashlib.sha256(rollout_key).digest()[:4], "big") % 100
        return bucket < self.gray_percentage

    def can_read(self, context: ExecutionContext, stage: CacheStage) -> bool:
        return (
            self.read_enabled
            and self.enabled_for(context)
            and (stage != "fetch" or self.fetch_enabled)
        )

    def can_write(self, context: ExecutionContext, stage: CacheStage) -> bool:
        return (
            self.write_enabled
            and self.enabled_for(context)
            and (stage != "fetch" or self.fetch_enabled)
        )

    def ttl(self, stage: CacheStage) -> int:
        return self.fetch_ttl_seconds if stage == "fetch" else self.search_ttl_seconds


class CacheLoadFailure(RuntimeError):
    """A live provider/fetch failed and must never be cached."""


class _KeyLock:
    def __init__(self) -> None:
        self.lock = threading.Lock()


class ResearchCache:
    """Best-effort cache facade with per-process stampede protection."""

    def __init__(self, store: CacheStore, policy: ResearchCachePolicy) -> None:
        self.store = store
        self.policy = policy
        self._locks: WeakValueDictionary[str, _KeyLock] = WeakValueDictionary()
        self._locks_guard = threading.Lock()

    def lookup(self, key: str, *, allow_stale: bool = False) -> CacheLookup:
        try:
            entry = self.store.get(key)
        except Exception:
            return CacheLookup("error")
        if entry is None:
            return CacheLookup("miss")
        if entry.expires_at > time.time():
            return CacheLookup("hit", copy.deepcopy(entry.value))
        if allow_stale:
            return CacheLookup("stale", copy.deepcopy(entry.value))
        return CacheLookup("miss")

    def get(self, key: str, *, allow_stale: bool = False) -> CacheLookup:
        return self.lookup(key, allow_stale=allow_stale)

    def set(self, key: str, value: Any, *, ttl_seconds: int) -> bool:
        try:
            self.store.set(key, CacheEntry(value, time.time() + ttl_seconds))
        except Exception:
            return False
        return True

    def delete(self, key: str) -> None:
        try:
            self.store.delete(key)
        except Exception:
            return

    def get_or_set(
        self,
        key: str,
        loader: Callable[[], T],
        *,
        ttl_seconds: int,
        read_enabled: bool,
        write_enabled: bool,
        stale_if_error: bool,
    ) -> CacheLookup:
        backend_error = False
        if read_enabled:
            initial = self.lookup(key)
            if initial.state == "hit":
                return initial
            backend_error = initial.state == "error"
        if not read_enabled and not write_enabled:
            return CacheLookup("disabled", loader())

        with self._key_lock(key):
            if read_enabled:
                second = self.lookup(key)
                if second.state == "hit":
                    return second
                backend_error = backend_error or second.state == "error"
            try:
                value = loader()
            except Exception:
                if read_enabled and stale_if_error:
                    stale = self.lookup(key, allow_stale=True)
                    if stale.state == "stale":
                        return stale
                raise
            if write_enabled:
                stored = self.set(key, value, ttl_seconds=ttl_seconds)
                backend_error = backend_error or not stored
            return CacheLookup("error" if backend_error else "miss", copy.deepcopy(value))

    @contextmanager
    def _key_lock(self, key: str) -> Iterator[None]:
        distributed = getattr(self.store, "key_lock", None)
        if callable(distributed):
            try:
                with distributed(key):
                    yield
                return
            except CacheLockUnavailable:
                pass
        with self._locks_guard:
            lock_holder = self._locks.get(key)
            if lock_holder is None:
                lock_holder = _KeyLock()
                self._locks[key] = lock_holder
        with lock_holder.lock:
            yield

    def search_key(
        self,
        context: ExecutionContext,
        *,
        provider: str,
        query: str,
        limit: int,
    ) -> str:
        return self._key(
            context,
            "search",
            {
                "provider": provider,
                "query": " ".join(query.split()).casefold(),
                "limit": limit,
            },
        )

    def fetch_key(self, context: ExecutionContext, url: str) -> str:
        return self._key(context, "fetch", {"url": _normalize_url(url)})

    def close(self) -> None:
        close = getattr(self.store, "close", None)
        if callable(close):
            close()

    def _key(self, context: ExecutionContext, stage: CacheStage, payload: Mapping[str, Any]) -> str:
        material = {
            "version": self.policy.key_version,
            "tenant": context.tenant_id,
            "stage": stage,
            **payload,
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return f"{self.policy.key_version}:{stage}:{digest}"


def build_research_cache(settings: Any) -> ResearchCache:
    policy = ResearchCachePolicy(
        key_version=settings.research_cache_key_version,
        read_enabled=settings.research_cache_read_enabled,
        write_enabled=settings.research_cache_write_enabled,
        fetch_enabled=settings.research_cache_fetch_enabled,
        gray_percentage=settings.research_gray_percentage,
        search_ttl_seconds=settings.research_cache_search_ttl_seconds,
        fetch_ttl_seconds=settings.research_cache_fetch_ttl_seconds,
        stale_if_error=settings.research_cache_stale_if_error,
        stale_grace_seconds=settings.research_cache_stale_grace_seconds,
        observability_enabled=settings.research_observability_enabled,
    )
    backend = settings.research_cache_backend.strip().lower()
    if backend == "memory":
        store: CacheStore = MemoryCacheStore(
            max_entries=settings.research_cache_max_entries,
            max_value_bytes=settings.research_cache_max_value_bytes,
        )
    elif backend == "redis":
        store = RedisCacheStore(
            settings.research_cache_redis_url,
            stale_grace_seconds=settings.research_cache_stale_grace_seconds,
        )
    else:
        raise ValueError(f"unsupported research cache backend: {backend}")
    return ResearchCache(store, policy)


def _normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host
    if port is not None and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, ""))
