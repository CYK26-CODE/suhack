"""Pipeline Orchestrator — sequential runner.

Chains all 6 modules in order with context checkpoints at each stage.
The healer stage is a placeholder stub (skipped per design).
"""

from __future__ import annotations

from datetime import datetime

import structlog

from app.core.context_store import ContextStore
from app.core.exceptions import RepoHealerError
from app.core.schemas import RiskLevel, RunContext, StageStatus
from app.modules.analyzer.service import run_analysis
from app.modules.complexity.service import run_complexity
from app.modules.risk.service import run_risk
from app.modules.validation.service import validate_fix

log = structlog.get_logger(__name__)


async def run_pipeline(ctx: RunContext, store: ContextStore) -> RunContext:
    """Execute the full pipeline: analyze → complexity → risk → heal → validate → pr.

    Each stage checkpoints after completion. If any stage fails, the
    orchestrator marks it as FAILED and aborts.
    """
    log.info("pipeline_started", run_id=ctx.run_id, repo_url=ctx.repo_url)

    try:
        # Stage 1: Analyze
        await run_analysis(ctx, store)
    except RepoHealerError as exc:
        ctx.mark_stage("analysis", StageStatus.FAILED)
        await store.set(ctx.run_id, ctx)
        log.error("pipeline_stage_failed", stage="analysis", error=str(exc))
        return ctx

    try:
        # Stage 2: Complexity
        await run_complexity(ctx, store)
    except RepoHealerError as exc:
        ctx.mark_stage("complexity", StageStatus.FAILED)
        await store.set(ctx.run_id, ctx)
        log.error("pipeline_stage_failed", stage="complexity", error=str(exc))
        return ctx

    try:
        # Stage 3: Risk
        await run_risk(ctx, store)
    except RepoHealerError as exc:
        ctx.mark_stage("risk", StageStatus.FAILED)
        await store.set(ctx.run_id, ctx)
        log.error("pipeline_stage_failed", stage="risk", error=str(exc))
        return ctx

    # Stage 4: Healer — placeholder (skipped)
    # For each HIGH-risk file, would call healer.service.heal_file(ctx, file)
    high_risk_files = [r for r in ctx.risk if r.risk_level == RiskLevel.HIGH]
    log.info(
        "healer_skipped",
        run_id=ctx.run_id,
        high_risk_count=len(high_risk_files),
        reason="healer module not built yet",
    )
    ctx.mark_stage("healer", StageStatus.SKIPPED)
    await store.set(ctx.run_id, ctx)

    # Stage 5: Validation — skipped because no fixes to validate
    if ctx.fixes:
        ctx.mark_stage("validation", StageStatus.RUNNING)
        for fix in ctx.fixes:
            try:
                await validate_fix(ctx, fix, store)
            except RepoHealerError as exc:
                log.warning(
                    "validation_failed",
                    file=fix.file,
                    error=str(exc),
                )
        ctx.mark_stage("validation", StageStatus.COMPLETE)
        await store.set(ctx.run_id, ctx)
    else:
        ctx.mark_stage("validation", StageStatus.SKIPPED)
        await store.set(ctx.run_id, ctx)

    # Stage 6: PR — skipped if no validated fixes
    passed_validations = [v for v in ctx.validations if v.status == "PASS"]
    if passed_validations:
        try:
            from app.modules.pr.service import run_pr

            await run_pr(ctx, store)
        except RepoHealerError as exc:
            ctx.mark_stage("pr", StageStatus.FAILED)
            await store.set(ctx.run_id, ctx)
            log.error("pipeline_stage_failed", stage="pr", error=str(exc))
            return ctx
    else:
        ctx.mark_stage("pr", StageStatus.SKIPPED)
        await store.set(ctx.run_id, ctx)

    log.info("pipeline_complete", run_id=ctx.run_id, pr_url=ctx.pr_url)
    return ctx
