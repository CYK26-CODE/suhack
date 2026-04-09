"""Repo Analyzer Agent — FastAPI router.

GET /api/v1/analyze/repo — all params via query strings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.context_store import ContextStore, InMemoryContextStore
from app.core.exceptions import AnalysisError
from app.core.schemas import AnalysisResult, RunContext
from app.modules.analyzer.service import run_analysis

router = APIRouter(prefix="/analyze", tags=["analyzer"])

# Will be replaced by DI in main.py
_store = InMemoryContextStore()


def get_store() -> ContextStore:
    return _store


@router.get("/repo", response_model=AnalysisResult)
async def analyze_repo(
    repo_url: str = Query(..., description="HTTPS or SSH URL of the target repository"),
    branch: str = Query("main", description="Branch to traverse"),
    last_commit_sha: str | None = Query(None, description="Stop traversal at this SHA"),
    run_id: str | None = Query(None, description="Attach to existing run context"),
    since_days: int | None = Query(None, description="Limit to commits in last N days"),
    store: ContextStore = Depends(get_store),
) -> AnalysisResult:
    """Analyse a repository's commit history and return file-level metrics."""
    ctx = RunContext(repo_url=repo_url, branch=branch, last_commit_sha=last_commit_sha)
    if run_id:
        existing = await store.get(run_id)
        if existing:
            ctx = existing
        else:
            ctx.run_id = run_id

    ctx.local_repo_path = repo_url  # For local paths; PyDriller handles clone for remote
    result = await run_analysis(ctx, store)
    return result
