"""Complexity Agent — FastAPI router.

POST /api/v1/analyze/complexity
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.context_store import ContextStore, InMemoryContextStore
from app.core.exceptions import ComplexityError
from app.core.schemas import ComplexityResult
from app.modules.complexity.service import run_complexity

router = APIRouter(prefix="/analyze", tags=["complexity"])

_store = InMemoryContextStore()


def get_store() -> ContextStore:
    return _store


class ComplexityRequest(BaseModel):
    run_id: str | None = None


@router.post("/complexity", response_model=ComplexityResult)
async def compute_complexity_endpoint(
    body: ComplexityRequest,
    store: ContextStore = Depends(get_store),
) -> ComplexityResult:
    """Compute Radon complexity metrics for analysed files."""
    if not body.run_id:
        # Development convenience: use the most recent run
        runs = await store.list_runs()
        if not runs:
            raise ComplexityError("No runs found in context store")
        body.run_id = runs[-1]

    ctx = await store.get(body.run_id)
    if ctx is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"run_id '{body.run_id}' not found")

    return await run_complexity(ctx, store)
