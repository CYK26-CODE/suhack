"""Risk Prediction Agent - FastAPI router.

POST /api/v1/predict/risk
GET  /api/v1/predict/explain/{run_id}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.context_store import ContextStore, InMemoryContextStore
from app.core.schemas import ExplainabilityReport, RiskResult
from app.modules.risk.explainability import generate_report
from app.modules.risk.service import run_risk

router = APIRouter(prefix="/predict", tags=["risk"])

_store = InMemoryContextStore()


def get_store() -> ContextStore:
    return _store


class RiskRequest(BaseModel):
    run_id: str


@router.post("/risk", response_model=RiskResult)
async def predict_risk(
    body: RiskRequest,
    store: ContextStore = Depends(get_store),
) -> RiskResult:
    """Run IsolationForest risk prediction on analysed and complexity-scored files."""
    ctx = await store.get(body.run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"run_id '{body.run_id}' not found")
    return await run_risk(ctx, store)


@router.get("/explain/{run_id}", response_model=ExplainabilityReport)
async def explain_risk(
    run_id: str,
    store: ContextStore = Depends(get_store),
) -> ExplainabilityReport:
    """Generate an explainability report for a completed pipeline run."""
    ctx = await store.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"run_id '{run_id}' not found")
    if not ctx.risk:
        raise HTTPException(status_code=400, detail="Risk stage has not completed yet")
    return generate_report(ctx)

