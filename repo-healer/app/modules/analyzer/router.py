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


@router.get("/file/{run_id}")
async def get_file_content(
    run_id: str,
    file_path: str = Query(..., description="Path to the file relative to repo root"),
    store: ContextStore = Depends(get_store),
):
    """Retrieve raw file content from the cloned repository."""
    from fastapi import HTTPException
    import pathlib
    
    # Needs to hit the pipeline store where runs are actually saved
    ctx = await store.get(run_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Run not found")
        
    local_repo_path = ctx.local_repo_path
    if not local_repo_path:
        raise HTTPException(status_code=400, detail="Repository not cloned locally")
        
    full_path = pathlib.Path(local_repo_path) / file_path
    
    # Prevent directory traversal
    try:
        resolved_full = full_path.resolve(strict=False)
        resolved_base = pathlib.Path(local_repo_path).resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File or repo not found")
        
    if not str(resolved_full).startswith(str(resolved_base)):
        raise HTTPException(status_code=403, detail="Invalid path")
        
    if not resolved_full.exists() or not resolved_full.is_file():
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        content = resolved_full.read_text(encoding="utf-8")
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
