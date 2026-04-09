"""Tests for Module 08 — Context Memory."""

from __future__ import annotations

import pytest

from app.core.context_store import InMemoryContextStore
from app.core.schemas import RunContext, StageStatus


@pytest.fixture
def in_memory_store():
    return InMemoryContextStore()


@pytest.fixture
def sample_context():
    return RunContext(repo_url="https://github.com/test/repo", branch="main")


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
        await in_memory_store.delete("never-existed")

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
