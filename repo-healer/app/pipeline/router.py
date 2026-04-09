"""Pipeline — FastAPI router.

POST /api/v1/pipeline/run   — orchestrates all modules
GET  /api/v1/pipeline/{id}  — poll pipeline status
DELETE /api/v1/context/{id} — purge a run's context
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.context_store import ContextStore, InMemoryContextStore
from app.core.schemas import RunContext
from app.pipeline.orchestrator import run_pipeline

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

_store = InMemoryContextStore()


def get_store() -> ContextStore:
    return _store


class PipelineRequest(BaseModel):
    repo_url: str
    branch: str = "main"


class PipelineResponse(BaseModel):
    run_id: str
    pr_url: str | None = None
    files_healed: int = 0
    stage_flags: dict = {}


@router.post("/run", response_model=PipelineResponse)
async def start_pipeline(
    body: PipelineRequest,
    store: ContextStore = Depends(get_store),
) -> PipelineResponse:
    """Run the full end-to-end pipeline."""
    ctx = RunContext(repo_url=body.repo_url, branch=body.branch)
    await store.set(ctx.run_id, ctx)

    ctx = await run_pipeline(ctx, store)

    return PipelineResponse(
        run_id=ctx.run_id,
        pr_url=ctx.pr_url,
        files_healed=len([f for f in ctx.fixes if f.changed]),
        stage_flags=ctx.stage_flags,
    )


@router.get("/{run_id}")
async def get_pipeline_status(
    run_id: str,
    store: ContextStore = Depends(get_store),
) -> dict:
    """Poll pipeline status by run ID."""
    ctx = await store.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"run_id '{run_id}' not found")
    return ctx.model_dump(mode="json")


# Context management endpoint (mounted at /api/v1/context/)
context_router = APIRouter(prefix="/context", tags=["context"])


@context_router.delete("/{run_id}")
async def delete_context(
    run_id: str,
    store: ContextStore = Depends(get_store),
) -> dict:
    """Purge a run's context from the store."""
    await store.delete(run_id)
    return {"deleted": run_id}
