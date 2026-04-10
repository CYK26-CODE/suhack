"""Repo Analyzer Agent - service layer.

Mines Git commit history using PyDriller and produces file-level metadata.
Clones the repo locally so downstream agents can read source files.
"""

from __future__ import annotations

import tempfile
from datetime import datetime

import structlog
from pydriller import Repository

from app.core.config import get_settings
from app.core.context_store import ContextStore
from app.core.exceptions import AnalysisError
from app.core.schemas import AnalysisResult, FileRecord, RunContext, StageStatus

log = structlog.get_logger(__name__)
settings = get_settings()


class _FileStat:
    """Accumulator for per-file statistics during traversal."""

    def __init__(self, file: str) -> None:
        self.file = file
        self.total_churn = 0
        self.commit_count = 0
        self.contributors: set[str] = set()
        self.last_modified: datetime = datetime.min

    def to_record(self) -> FileRecord:
        ext = ""
        if "." in self.file:
            ext = "." + self.file.rsplit(".", 1)[-1]
        return FileRecord(
            file=self.file,
            total_churn=self.total_churn,
            commit_count=self.commit_count,
            contributors=len(self.contributors),
            last_modified=self.last_modified,
            extension=ext,
            is_deleted=False,
        )


def clone_repo(repo_url: str, branch: str = "main") -> str:
    """Clone a remote repo to a temp directory. Returns the local path."""
    import git as gitpython

    clone_dir = tempfile.mkdtemp(prefix="repo_healer_")
    log.info("cloning_repo", url=repo_url, dest=clone_dir, branch=branch)
    try:
        gitpython.Repo.clone_from(
            repo_url, clone_dir, branch=branch
        )
    except Exception as exc:
        if branch == "main":
            log.info("clone_fallback_to_default", url=repo_url)
            try:
                # Omit branch parameter to clone the default branch
                gitpython.Repo.clone_from(
                    repo_url, clone_dir
                )
                log.info("clone_complete", path=clone_dir)
                return clone_dir
            except Exception as exc2:
                log.error("clone_failed", url=repo_url, error=str(exc2))
                raise AnalysisError(f"Failed to clone repo (tried main and default): {exc2}") from exc2
        else:
            log.error("clone_failed", url=repo_url, error=str(exc))
            raise AnalysisError(f"Failed to clone repo: {exc}") from exc
    log.info("clone_complete", path=clone_dir)
    return clone_dir


def traverse_repo(
    repo_path: str, branch: str, to_commit: str | None, since_days: int | None = None
) -> list[FileRecord]:
    """Traverse commit history of a LOCAL repo and collect per-file metrics."""
    try:
        kwargs: dict = {
            "path_to_repo": repo_path,
            "only_no_merge": True,
        }
        if to_commit:
            kwargs["to_commit"] = to_commit
        if since_days:
            from datetime import timedelta, timezone

            kwargs["since"] = datetime.now(timezone.utc) - timedelta(days=since_days)

        file_stats: dict[str, _FileStat] = {}

        for commit in Repository(**kwargs).traverse_commits():
            try:
                for mod in commit.modified_files:
                    if mod is None or mod.new_path is None:
                        continue
                    # Filter by extension
                    if settings.file_extensions:
                        ext = ""
                        if "." in mod.new_path:
                            ext = "." + mod.new_path.rsplit(".", 1)[-1]
                        if ext not in settings.file_extensions:
                            continue

                    stat = file_stats.setdefault(
                        mod.new_path, _FileStat(file=mod.new_path)
                    )
                    stat.total_churn += (mod.added_lines or 0) + (
                        mod.deleted_lines or 0
                    )
                    stat.commit_count += 1
                    stat.contributors.add(commit.author.email)
                    stat.last_modified = commit.author_date
            except (AttributeError, TypeError) as exc:
                log.warning(
                    "malformed_commit_skipped",
                    commit_hash=getattr(commit, "hash", "unknown"),
                    error=str(exc),
                )
                continue

        records = [stat.to_record() for stat in file_stats.values()]
        log.info("traverse_complete", total_files_found=len(file_stats), filtered_records=len(records))
        return records

    except AnalysisError:
        raise
    except Exception as exc:
        raise AnalysisError(str(exc)) from exc


async def run_analysis(ctx: RunContext, store: ContextStore) -> AnalysisResult:
    """Clone the repo, traverse history, and checkpoint results."""
    log.info("analysis_started", run_id=ctx.run_id, repo_url=ctx.repo_url)
    ctx.mark_stage("analysis", StageStatus.RUNNING)

    # Step 1: Clone the repo locally (if it's a URL)
    repo_url = ctx.repo_url.strip()
    if repo_url.startswith("http") or repo_url.startswith("git@"):
        local_path = clone_repo(repo_url, ctx.branch)
    else:
        local_path = repo_url  # already a local path

    ctx.local_repo_path = local_path

    # Step 2: Traverse commit history on the local clone
    records = traverse_repo(local_path, ctx.branch, ctx.last_commit_sha)

    ctx.analysis = records
    ctx.mark_stage("analysis", StageStatus.COMPLETE)
    await store.set(ctx.run_id, ctx)

    log.info("analysis_complete", run_id=ctx.run_id, files_processed=len(records))
    return AnalysisResult(
        run_id=ctx.run_id, file_count=len(records), analysis=records
    )
