"""PR Agent — FastAPI router.

POST /api/v1/pr/create
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.context_store import ContextStore, InMemoryContextStore
from app.core.exceptions import PRError
from app.core.schemas import PRResult
from app.modules.pr.service import run_pr

router = APIRouter(prefix="/pr", tags=["pr"])

_store = InMemoryContextStore()


def get_store() -> ContextStore:
    return _store


class PRRequest(BaseModel):
    run_id: str


@router.post("/create", response_model=PRResult)
async def create_pr_endpoint(
    body: PRRequest,
    store: ContextStore = Depends(get_store),
) -> PRResult:
    """Create a GitHub Pull Request with all validated fixes."""
    ctx = await store.get(body.run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"run_id '{body.run_id}' not found")

    if not ctx.validations:
        raise HTTPException(
            status_code=424, detail="No validated fixes in context"
        )

    return await run_pr(ctx, store)
