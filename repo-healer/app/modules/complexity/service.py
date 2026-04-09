"""Complexity Agent — service layer.

Runs Radon cc_visit + mi_visit on each file from the analysis stage.
"""

from __future__ import annotations

import pathlib
from datetime import datetime

import aiofiles
import structlog
from radon.complexity import cc_visit
from radon.metrics import mi_visit

from app.core.context_store import ContextStore
from app.core.exceptions import ComplexityError
from app.core.schemas import (
    ComplexityRecord,
    ComplexityResult,
    RunContext,
    StageStatus,
)

log = structlog.get_logger(__name__)


def compute_complexity(file_path: str, source_code: str) -> ComplexityRecord:
    """Compute cyclomatic complexity and maintainability index.
    
    Radon only supports Python. Non-Python files return default metrics.
    """
    if not file_path.endswith(".py"):
        return ComplexityRecord(
            file=file_path,
            complexity=0.0,
            maintainability=100.0,  # default to "perfect" for untracked langs
            function_count=0,
            parse_error=False,
        )

    try:
        functions = cc_visit(source_code)
        if functions:
            avg_complexity = sum(f.complexity for f in functions) / len(functions)
        else:
            avg_complexity = 0.0

        mi_score = mi_visit(source_code, multi=False)
        if mi_score is None:
            mi_score = 0.0
        mi_score = max(0.0, min(100.0, float(mi_score)))  # clamp to valid range

        return ComplexityRecord(
            file=file_path,
            complexity=round(avg_complexity, 2),
            maintainability=round(mi_score, 2),
            function_count=len(functions),
            parse_error=False,
        )
    except SyntaxError as exc:
        log.warning("syntax_error_in_file", file=file_path, error=str(exc))
        return ComplexityRecord(
            file=file_path,
            complexity=-1.0,
            maintainability=-1.0,
            function_count=0,
            parse_error=True,
        )
    except Exception as exc:
        log.error("complexity_unexpected_error", file=file_path, error=str(exc))
        return ComplexityRecord(
            file=file_path,
            complexity=-1.0,
            maintainability=-1.0,
            function_count=0,
            parse_error=True,
        )


async def read_source(local_repo_path: str, file: str) -> str:
    """Read a source file from the cloned repo."""
    full_path = pathlib.Path(local_repo_path) / file
    async with aiofiles.open(full_path, encoding="utf-8", errors="replace") as f:
        return await f.read()


async def run_complexity(ctx: RunContext, store: ContextStore) -> ComplexityResult:
    """Execute complexity stage and checkpoint results."""
    if not ctx.analysis:
        raise ComplexityError("analysis stage must run before complexity")

    log.info("complexity_started", run_id=ctx.run_id)
    ctx.mark_stage("complexity", StageStatus.RUNNING)

    records = []
    for file_record in ctx.analysis:
        try:
            source = await read_source(ctx.local_repo_path, file_record.file)
            records.append(compute_complexity(file_record.file, source))
        except FileNotFoundError:
            log.warning("file_not_found", file=file_record.file)
            records.append(
                ComplexityRecord(
                    file=file_record.file,
                    complexity=-1.0,
                    maintainability=-1.0,
                    function_count=0,
                    parse_error=True,
                )
            )

    ctx.complexity = records
    ctx.mark_stage("complexity", StageStatus.COMPLETE)
    await store.set(ctx.run_id, ctx)  # checkpoint

    log.info("complexity_complete", run_id=ctx.run_id, files=len(records))
    return ComplexityResult(run_id=ctx.run_id, complexity=records)
