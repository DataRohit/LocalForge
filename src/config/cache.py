"""Cache behaviour when the cache itself is unavailable.

Wraps the framework's Redis cache so a page that only reads through the cache degrades to its
uncached behaviour instead of returning a server error when the instance is unreachable.
"""

import logging
from typing import Any, override

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

UNAVAILABLE = "the cache is unavailable; serving without it"


class ResilientRedisCache(RedisCache):
    """Redis cache that survives losing its instance.

    Inherits from the framework's ``RedisCache`` and converts a connection failure on the ordinary
    read and write paths into the miss the caller already handles. Sessions use a separate strict
    alias, because a silently discarded session write is a lost identity rather than a slower page.

    Attributes:
        None beyond those the base cache defines.

    Members:
        get: Read one value, or the default when the cache is unreachable.
        get_many: Read several values, or nothing when the cache is unreachable.
        set: Write one value, ignoring an unreachable cache.
        set_many: Write several values, ignoring an unreachable cache.
        add: Write one value if absent, ignoring an unreachable cache.
        touch: Extend one entry's life, ignoring an unreachable cache.
        delete: Remove one value, ignoring an unreachable cache.
        delete_many: Remove several values, ignoring an unreachable cache.
        incr: Increase a counter, treating an unreachable cache as a miss.
        has_key: Report whether a key is present, or false when unreachable.
    """

    @override
    def get(self, key: str, default: Any = None, version: int | None = None) -> Any:
        """Read one value, or the default when the cache is unreachable.

        Treats a connection failure as a miss, which is the outcome the caller already has code for
        and the reason a cache outage should slow a page rather than break it.

        Arguments:
            key: Key to read.
            default: Value to return when the key is absent or the cache is unreachable.
            version: Cache version to read from.

        Returns:
            The cached value, or the default.

        Raises:
            None.
        """
        try:
            return super().get(key, default, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

            return default

    @override
    def get_many(self, keys: Any, version: int | None = None) -> dict[str, Any]:
        """Read several values, or nothing when the cache is unreachable.

        Returns an empty mapping on failure, which every caller already treats as a complete set of
        misses.

        Arguments:
            keys: Keys to read.
            version: Cache version to read from.

        Returns:
            The values that were present, or an empty mapping.

        Raises:
            None.
        """
        try:
            return super().get_many(keys, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

            return {}

    @override
    def set(
        self,
        key: str,
        value: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> None:
        """Write one value, ignoring an unreachable cache.

        Discards the write on failure rather than raising, so the page that computed the value
        still serves it to the caller who asked for it.

        Arguments:
            key: Key to write.
            value: Value to store.
            timeout: Seconds the entry should live for.
            version: Cache version to write to.

        Returns:
            None.

        Raises:
            None.
        """
        try:
            super().set(key, value, timeout, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

    @override
    def add(
        self,
        key: str,
        value: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        """Write one value if absent, ignoring an unreachable cache.

        Reports that nothing was added on failure, which is the honest answer and the one a caller
        using this for locking must already handle.

        Arguments:
            key: Key to write.
            value: Value to store.
            timeout: Seconds the entry should live for.
            version: Cache version to write to.

        Returns:
            True when the value was stored.

        Raises:
            None.
        """
        try:
            return super().add(key, value, timeout, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

            return False

    @override
    def touch(
        self,
        key: str,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        """Extend one entry's life, ignoring an unreachable cache.

        Reports that nothing was extended on failure, leaving the caller to treat the entry as gone
        — which is what the read path will tell it a moment later anyway.

        Arguments:
            key: Key to extend.
            timeout: Seconds the entry should live for.
            version: Cache version to touch.

        Returns:
            True when the entry was extended.

        Raises:
            None.
        """
        try:
            return super().touch(key, timeout, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

            return False

    @override
    def delete(self, key: str, version: int | None = None) -> bool:
        """Remove one value, ignoring an unreachable cache.

        Reports that nothing was removed on failure; an entry in an unreachable cache is already
        unreadable, and it expires on its own.

        Arguments:
            key: Key to remove.
            version: Cache version to remove from.

        Returns:
            True when an entry was removed.

        Raises:
            None.
        """
        try:
            return super().delete(key, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

            return False

    @override
    def has_key(self, key: str, version: int | None = None) -> bool:
        """Report whether a key is present, or false when unreachable.

        Answers false on failure, matching the miss the read path reports for the same key, so the
        two never disagree about whether an entry exists.

        Arguments:
            key: Key to check.
            version: Cache version to check.

        Returns:
            True when the key is present.

        Raises:
            None.
        """
        try:
            return super().has_key(key, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

            return False

    @override
    def set_many(
        self,
        data: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> list[str]:
        """Write several values, ignoring an unreachable cache.

        Reports every key as unwritten on failure, which is the same answer the caller gets for a
        partial write and the one it already has to handle.

        Arguments:
            data: Mapping of keys to values.
            timeout: Seconds the entries should live for.
            version: Cache version to write to.

        Returns:
            The keys that could not be stored.

        Raises:
            None.
        """
        try:
            return [str(key) for key in super().set_many(data, timeout, version)]
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

            return [str(key) for key in data]

    @override
    def delete_many(self, keys: Any, version: int | None = None) -> None:
        """Remove several values, ignoring an unreachable cache.

        Discards the removal on failure, because entries in an unreachable cache are already
        unreadable and expire on their own.

        Arguments:
            keys: Keys to remove.
            version: Cache version to remove from.

        Returns:
            None.

        Raises:
            None.
        """
        try:
            super().delete_many(keys, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

    @override
    def incr(self, key: str, delta: int = 1, version: int | None = None) -> int:
        """Increase a counter, treating an unreachable cache as a miss.

        Raises the miss the base cache raises for an absent key, so a counter that cannot be read
        behaves like one that was never set rather than like a server fault.

        Arguments:
            key: Counter to increase.
            delta: Amount to add.
            version: Cache version to use.

        Returns:
            The new value.

        Raises:
            ValueError: If the counter is absent or the cache is unreachable.
        """
        try:
            return super().incr(key, delta, version)
        except RedisError:
            logger.warning(UNAVAILABLE, exc_info=True)

            message = f"Key '{key}' not found."
            raise ValueError(message) from None
