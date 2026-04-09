# Module 08 — Context Memory

## Purpose

Context Memory is the **shared state bus** of the entire pipeline. It stores and retrieves
`RunContext` objects — the single source of truth for the current state of a pipeline run.
Every agent reads from and writes to the context store. Without it, agents would need to
re-derive their inputs from scratch on every call, and partial pipeline runs could not be
resumed.

---

## Tech Stack

| Dependency          | Version | Role                                               |
|---------------------|---------|----------------------------------------------------|
| Pydantic v2         | ≥2.7    | `RunContext` model definition and serialisation    |
| redis[asyncio]      | ≥5.0    | Production persistent store                        |
| orjson              | ≥3.10   | Fast JSON serialisation of context objects         |
| pytest              | ≥8.0    | Unit tests for store implementations               |
| pytest-asyncio      | ≥0.23   | Async test support                                 |
| fakeredis           | ≥2.21   | In-memory Redis fake for testing                   |

---

## RunContext Schema

```python
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid

class StageStatus(str, Enum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETE  = "COMPLETE"
    FAILED    = "FAILED"
    SKIPPED   = "SKIPPED"

class RunContext(BaseModel):
    run_id:          str        = Field(default_factory=lambda: f"{datetime.utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}")
    repo_url:        str
    branch:          str        = "main"
    local_repo_path: str        = ""      # set by analyzer after clone
    started_at:      datetime   = Field(default_factory=datetime.utcnow)
    last_updated:    datetime   = Field(default_factory=datetime.utcnow)
    last_commit_sha: Optional[str] = None

    # Stage outputs
    analysis:        list       = Field(default_factory=list)   # list[FileRecord]
    complexity:      list       = Field(default_factory=list)   # list[ComplexityRecord]
    risk:            list       = Field(default_factory=list)   # list[RiskRecord]
    fixes:           list       = Field(default_factory=list)   # list[HealResult]
    validations:     list       = Field(default_factory=list)   # list[ValidationResult]
    pr_url:          Optional[str] = None
    pr_branch:       Optional[str] = None

    # Stage tracking
    stage_flags:     dict[str, StageStatus] = Field(default_factory=dict)

    def is_stage_complete(self, stage: str) -> bool:
        return self.stage_flags.get(stage) == StageStatus.COMPLETE

    def mark_stage(self, stage: str, status: StageStatus) -> None:
        self.stage_flags[stage] = status
        self.last_updated = datetime.utcnow()
```

---

## ContextStore Interface

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ContextStore(Protocol):
    async def get(self, run_id: str) -> Optional[RunContext]: ...
    async def set(self, run_id: str, ctx: RunContext) -> None: ...
    async def delete(self, run_id: str) -> None: ...
    async def list_runs(self) -> list[str]: ...
```

### InMemoryContextStore (development / test)

```python
class InMemoryContextStore:
    def __init__(self):
        self._store: dict[str, RunContext] = {}

    async def get(self, run_id: str) -> Optional[RunContext]:
        return self._store.get(run_id)

    async def set(self, run_id: str, ctx: RunContext) -> None:
        self._store[run_id] = ctx

    async def delete(self, run_id: str) -> None:
        self._store.pop(run_id, None)

    async def list_runs(self) -> list[str]:
        return list(self._store.keys())
```

### RedisContextStore (production)

```python
import redis.asyncio as aioredis
import orjson

class RedisContextStore:
    TTL_SECONDS = 86400 * 7  # 7 days

    def __init__(self, redis_url: str):
        self._redis = aioredis.from_url(redis_url, decode_responses=False)

    async def get(self, run_id: str) -> Optional[RunContext]:
        raw = await self._redis.get(f"repo_healer:run:{run_id}")
        if raw is None:
            return None
        return RunContext.model_validate(orjson.loads(raw))

    async def set(self, run_id: str, ctx: RunContext) -> None:
        serialised = orjson.dumps(ctx.model_dump(mode="json"))
        await self._redis.setex(f"repo_healer:run:{run_id}", self.TTL_SECONDS, serialised)

    async def delete(self, run_id: str) -> None:
        await self._redis.delete(f"repo_healer:run:{run_id}")

    async def list_runs(self) -> list[str]:
        keys = await self._redis.keys("repo_healer:run:*")
        return [k.decode().split("repo_healer:run:")[-1] for k in keys]
```

---

## Checkpoint Intervals

| Stage        | Checkpoint Frequency              | Rationale                                   |
|--------------|-----------------------------------|---------------------------------------------|
| analysis     | Once at stage end                 | Fast; no partial state needed               |
| complexity   | Once at stage end                 | Fast; no partial state needed               |
| risk         | Once at stage end                 | Fast; no partial state needed               |
| healer       | After each file healed            | LLM calls are expensive; partial recovery   |
| validation   | After each file validated         | Subprocess calls can hang; partial recovery  |
| pr           | After PR creation                 | Idempotency guard handles re-runs           |

---

## Testing Module: `tests/test_08_context_memory.py`

```python
import pytest
import pytest_asyncio
from app.core.context_store import InMemoryContextStore
from app.core.schemas import RunContext, StageStatus
from datetime import datetime

try:
    import fakeredis.aioredis as fakeredis
    FAKEREDIS_AVAILABLE = True
except ImportError:
    FAKEREDIS_AVAILABLE = False


@pytest.fixture
def in_memory_store():
    return InMemoryContextStore()

@pytest.fixture
def sample_context():
    return RunContext(
        repo_url="https://github.com/test/repo",
        branch="main",
    )


class TestInMemoryContextStore:

    @pytest.mark.asyncio
    async def test_set_and_get_roundtrip(self, in_memory_store, sample_context):
        await in_memory_store.set(sample_context.run_id, sample_context)
        retrieved = await in_memory_store.get(sample_context.run_id)
        assert retrieved is not None
        assert retrieved.run_id == sample_context.run_id
        assert retrieved.repo_url == sample_context.repo_url

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, in_memory_store):
        result = await in_memory_store.get("nonexistent-run")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_removes_key(self, in_memory_store, sample_context):
        await in_memory_store.set(sample_context.run_id, sample_context)
        await in_memory_store.delete(sample_context.run_id)
        assert await in_memory_store.get(sample_context.run_id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_does_not_raise(self, in_memory_store):
        await in_memory_store.delete("never-existed")  # should not raise

    @pytest.mark.asyncio
    async def test_list_runs_returns_all_run_ids(self, in_memory_store):
        ctx1 = RunContext(repo_url="https://github.com/test/repo1", branch="main")
        ctx2 = RunContext(repo_url="https://github.com/test/repo2", branch="main")
        await in_memory_store.set(ctx1.run_id, ctx1)
        await in_memory_store.set(ctx2.run_id, ctx2)
        runs = await in_memory_store.list_runs()
        assert ctx1.run_id in runs
        assert ctx2.run_id in runs

    @pytest.mark.asyncio
    async def test_overwrite_updates_context(self, in_memory_store, sample_context):
        await in_memory_store.set(sample_context.run_id, sample_context)
        sample_context.stage_flags["analysis"] = StageStatus.COMPLETE
        await in_memory_store.set(sample_context.run_id, sample_context)
        retrieved = await in_memory_store.get(sample_context.run_id)
        assert retrieved.stage_flags["analysis"] == StageStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_stage_flags_preserve_across_updates(self, in_memory_store, sample_context):
        sample_context.mark_stage("analysis", StageStatus.COMPLETE)
        await in_memory_store.set(sample_context.run_id, sample_context)
        sample_context.mark_stage("complexity", StageStatus.COMPLETE)
        await in_memory_store.set(sample_context.run_id, sample_context)
        retrieved = await in_memory_store.get(sample_context.run_id)
        assert retrieved.stage_flags["analysis"] == StageStatus.COMPLETE
        assert retrieved.stage_flags["complexity"] == StageStatus.COMPLETE


@pytest.mark.skipif(not FAKEREDIS_AVAILABLE, reason="fakeredis not installed")
class TestRedisContextStore:

    @pytest_asyncio.fixture
    async def redis_store(self):
        from app.core.context_store import RedisContextStore
        fake = fakeredis.FakeRedis()
        store = RedisContextStore.__new__(RedisContextStore)
        store._redis = fake
        store.TTL_SECONDS = 3600
        return store

    @pytest.mark.asyncio
    async def test_serialise_deserialise_roundtrip(self, redis_store, sample_context):
        await redis_store.set(sample_context.run_id, sample_context)
        retrieved = await redis_store.get(sample_context.run_id)
        assert retrieved.run_id == sample_context.run_id
        assert retrieved.repo_url == sample_context.repo_url

    @pytest.mark.asyncio
    async def test_complex_nested_data_survives_serialisation(self, redis_store):
        from app.modules.analyzer.schemas import FileRecord
        ctx = RunContext(repo_url="https://github.com/test/repo", branch="main")
        ctx.analysis = [FileRecord(
            file="src/x.py", total_churn=100, commit_count=5,
            contributors=2, last_modified=datetime(2024, 1, 1)
        )]
        await redis_store.set(ctx.run_id, ctx)
        retrieved = await redis_store.get(ctx.run_id)
        assert len(retrieved.analysis) == 1
        assert retrieved.analysis[0]["file"] == "src/x.py"

    @pytest.mark.asyncio
    async def test_ttl_applied(self, redis_store, sample_context):
        await redis_store.set(sample_context.run_id, sample_context)
        ttl = await redis_store._redis.ttl(f"repo_healer:run:{sample_context.run_id}")
        assert ttl > 0
        assert ttl <= redis_store.TTL_SECONDS


class TestRunContext:

    def test_run_id_auto_generated(self):
        ctx = RunContext(repo_url="https://github.com/test/repo", branch="main")
        assert ctx.run_id != ""
        assert len(ctx.run_id) > 8

    def test_two_contexts_have_unique_run_ids(self):
        ctx1 = RunContext(repo_url="url1", branch="main")
        ctx2 = RunContext(repo_url="url2", branch="main")
        assert ctx1.run_id != ctx2.run_id

    def test_is_stage_complete_returns_false_initially(self):
        ctx = RunContext(repo_url="url", branch="main")
        assert not ctx.is_stage_complete("analysis")

    def test_mark_stage_updates_flag_and_timestamp(self):
        ctx = RunContext(repo_url="url", branch="main")
        before = ctx.last_updated
        ctx.mark_stage("analysis", StageStatus.COMPLETE)
        assert ctx.stage_flags["analysis"] == StageStatus.COMPLETE
        assert ctx.last_updated >= before

    def test_multiple_stage_flags_coexist(self):
        ctx = RunContext(repo_url="url", branch="main")
        ctx.mark_stage("analysis", StageStatus.COMPLETE)
        ctx.mark_stage("complexity", StageStatus.RUNNING)
        assert ctx.stage_flags["analysis"] == StageStatus.COMPLETE
        assert ctx.stage_flags["complexity"] == StageStatus.RUNNING
```

---

## Running Tests

```bash
pip install fakeredis --break-system-packages   # for Redis tests
pytest tests/test_08_context_memory.py -v
pytest tests/test_08_context_memory.py --cov=app/core --cov-report=term-missing
```
