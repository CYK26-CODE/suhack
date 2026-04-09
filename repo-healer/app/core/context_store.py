"""Context store implementations — in-memory and Redis.

ContextStore is the single shared state bus. All six module services
read/write to the context store via this interface.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import orjson

from app.core.schemas import RunContext


@runtime_checkable
class ContextStore(Protocol):
    """Interface for context storage backends."""

    async def get(self, run_id: str) -> Optional[RunContext]: ...
    async def set(self, run_id: str, ctx: RunContext) -> None: ...
    async def delete(self, run_id: str) -> None: ...
    async def list_runs(self) -> list[str]: ...


# ── In-Memory Implementation (development / test) ───────────────────────────


class InMemoryContextStore:
    """Simple dict-backed context store for development and testing."""

    def __init__(self) -> None:
        self._store: dict[str, RunContext] = {}

    async def get(self, run_id: str) -> Optional[RunContext]:
        return self._store.get(run_id)

    async def set(self, run_id: str, ctx: RunContext) -> None:
        self._store[run_id] = ctx

    async def delete(self, run_id: str) -> None:
        self._store.pop(run_id, None)

    async def list_runs(self) -> list[str]:
        return list(self._store.keys())


# ── Redis Implementation (production) ────────────────────────────────────────


class RedisContextStore:
    """Redis-backed context store with TTL and orjson serialisation."""

    TTL_SECONDS = 86400 * 7  # 7 days

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=False)

    async def get(self, run_id: str) -> Optional[RunContext]:
        raw = await self._redis.get(f"repo_healer:run:{run_id}")
        if raw is None:
            return None
        return RunContext.model_validate(orjson.loads(raw))

    async def set(self, run_id: str, ctx: RunContext) -> None:
        serialised = orjson.dumps(ctx.model_dump(mode="json"))
        await self._redis.setex(
            f"repo_healer:run:{run_id}", self.TTL_SECONDS, serialised
        )

    async def delete(self, run_id: str) -> None:
        await self._redis.delete(f"repo_healer:run:{run_id}")

    async def list_runs(self) -> list[str]:
        keys = await self._redis.keys("repo_healer:run:*")
        return [k.decode().split("repo_healer:run:")[-1] for k in keys]


# ── Factory ──────────────────────────────────────────────────────────────────


def create_context_store() -> InMemoryContextStore | RedisContextStore:
    """Create the appropriate context store based on config."""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.redis_url:
        return RedisContextStore(settings.redis_url)
    return InMemoryContextStore()
