"""Validation Agent — FastAPI router.

POST /api/v1/validate/fix
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.context_store import ContextStore, InMemoryContextStore
from app.core.schemas import HealResult, ValidationResult
from app.modules.validation.service import validate_fix

router = APIRouter(prefix="/validate", tags=["validation"])

_store = InMemoryContextStore()


def get_store() -> ContextStore:
    return _store


class ValidateRequest(BaseModel):
    run_id: str
    file: str
    fixed_code: str


@router.post("/fix", response_model=ValidationResult)
async def validate_fix_endpoint(
    body: ValidateRequest,
    store: ContextStore = Depends(get_store),
) -> ValidationResult:
    """Validate a healed file: syntax → flake8 → pytest → complexity gate."""
    ctx = await store.get(body.run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"run_id '{body.run_id}' not found")

    fix = HealResult(
        run_id=body.run_id,
        file=body.file,
        fixed_code=body.fixed_code,
        summary="manual validation",
        changed=True,
    )
    return await validate_fix(ctx, fix, store)
