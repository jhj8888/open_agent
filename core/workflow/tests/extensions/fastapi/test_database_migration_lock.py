from pathlib import Path

import pytest

from workflow.extensions.fastapi.lifespan import database_migration
from workflow.extensions.middleware.cache.manager import RedisCache


class _FakeLock:
    def __init__(self, acquired: bool = True, release_error: Exception | None = None):
        self.acquired = acquired
        self.release_error = release_error
        self.released = False

    def acquire(self) -> bool:
        return self.acquired

    def release(self) -> None:
        if self.release_error is not None:
            raise self.release_error
        self.released = True


class _LockingCache:
    def __init__(self, lock: _FakeLock):
        self.lock = lock
        self.arguments: tuple[object, ...] | None = None

    def distributed_lock(
        self,
        key: str,
        *,
        timeout: float,
        blocking_timeout: float,
        sleep: float,
    ) -> _FakeLock:
        self.arguments = (key, timeout, blocking_timeout, sleep)
        return self.lock


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, cache: _LockingCache) -> None:
    monkeypatch.setattr(database_migration, "get_cache_service", lambda: cache)
    monkeypatch.setattr(database_migration.command, "upgrade", lambda *_args: None)
    monkeypatch.setattr(Path, "exists", lambda _self: True)


def test_migration_loser_blocks_until_ownership_safe_lock_is_acquired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = _FakeLock()
    cache = _LockingCache(lock)
    _patch_runtime(monkeypatch, cache)

    database_migration.run_database_migration()

    assert cache.arguments == (
        database_migration.MIGRATION_LOCK_KEY,
        database_migration.MIGRATION_LOCK_TTL_SECONDS,
        database_migration.MIGRATION_LOCK_WAIT_SECONDS,
        database_migration.MIGRATION_LOCK_POLL_SECONDS,
    )
    assert lock.released


def test_migration_lock_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _LockingCache(_FakeLock(acquired=False))
    _patch_runtime(monkeypatch, cache)

    with pytest.raises(TimeoutError, match="timed out"):
        database_migration.run_database_migration()


def test_expired_owner_cannot_silently_release_new_owner_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _LockingCache(
        _FakeLock(release_error=RuntimeError("lock is no longer owned"))
    )
    _patch_runtime(monkeypatch, cache)

    with pytest.raises(RuntimeError, match="no longer owned"):
        database_migration.run_database_migration()


def test_redis_lock_uses_redis_3_5_compatible_arguments() -> None:
    class _RecordingRedisClient:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def lock(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "lock"

    client = _RecordingRedisClient()
    cache = RedisCache.__new__(RedisCache)
    cache._client = client

    lock = cache.distributed_lock(
        "migration-lock", timeout=10.0, blocking_timeout=12.0, sleep=0.2
    )

    assert lock == "lock"
    assert client.kwargs == {
        "name": "migration-lock",
        "timeout": 10.0,
        "sleep": 0.2,
        "blocking_timeout": 12.0,
        "thread_local": False,
    }
