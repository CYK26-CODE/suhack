"""PR Agent — service layer.

Creates a Git branch, commits validated fixes, pushes, and opens a GitHub PR.
"""

from __future__ import annotations

import pathlib
from datetime import datetime

import git as gitpython
import structlog
from github import Github, GithubException

from app.core.config import get_settings
from app.core.context_store import ContextStore
from app.core.exceptions import PRError
from app.core.schemas import (
    HealResult,
    PRResult,
    RiskLevel,
    RunContext,
    StageStatus,
)

log = structlog.get_logger(__name__)
settings = get_settings()


def get_or_create_pr(
    repo: object,
    branch_name: str,
    base_branch: str,
    title: str,
    body: str,
) -> tuple:
    """Check for existing PR or create a new one. Returns (pr, already_existed)."""
    open_prs = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch_name}")  # type: ignore[union-attr]
    for pr in open_prs:
        if pr.head.ref == branch_name:
            return pr, True  # already existed

    new_pr = repo.create_pull(  # type: ignore[union-attr]
        title=title,
        body=body,
        head=branch_name,
        base=base_branch,
    )
    return new_pr, False


def _build_pr_body(ctx: RunContext, passed_files: list[HealResult]) -> str:
    """Generate the markdown body for the PR."""
    lines = [
        "## 🔬 Repo Healer Automated Fix",
        "",
        f"**Run ID:** `{ctx.run_id}`  ",
        f"**Repository:** {ctx.repo_url}  ",
        f"**Branch:** {ctx.branch}  ",
        f"**Files healed:** {len(passed_files)}",
        "",
        "### Changes",
    ]
    for fix in passed_files:
        risk = next((r for r in ctx.risk if r.file == fix.file), None)
        risk_str = (
            f" (risk: {risk.risk_score:.2f} {risk.risk_level})" if risk else ""
        )
        lines.append(f"- `{fix.file}`{risk_str}: {fix.summary}")
    lines += [
        "",
        "### Validation",
        "All fixes passed: ✅ syntax · ✅ flake8 · ✅ pytest · ✅ complexity regression",
        "",
        "---",
        "_Created automatically by [Repo Healer](https://github.com/your-org/repo-healer)._",
    ]
    return "\n".join(lines)


def create_pr(ctx: RunContext) -> PRResult:
    """Create a GitHub PR with all validated fixes."""
    # Only commit files with PASS validation
    passed_files = [
        fix
        for fix in ctx.fixes
        if any(
            v.file == fix.file and v.status == "PASS" for v in ctx.validations
        )
    ]

    if not passed_files:
        raise PRError("no validated fixes to commit")

    # Local git operations
    repo = gitpython.Repo(ctx.local_repo_path)
    branch_name = f"repo-healer/{ctx.run_id}"

    # Create branch from origin/base_branch
    origin = repo.remote("origin")
    repo.git.checkout("-b", branch_name, f"origin/{ctx.branch}")

    for fix in passed_files:
        file_path = pathlib.Path(ctx.local_repo_path) / fix.file
        file_path.write_text(fix.fixed_code, encoding="utf-8")
        repo.index.add([str(file_path)])
        repo.index.commit(
            f"fix({fix.file}): {fix.summary[:72]}\n\n"
            f"Automated fix by Repo Healer (run {ctx.run_id})"
        )

    origin.push(branch_name)

    # GitHub API
    g = Github(settings.github_token.get_secret_value())
    gh_repo = g.get_repo(
        ctx.repo_url.split("github.com/")[-1].rstrip(".git")
    )

    pr_body = _build_pr_body(ctx, passed_files)
    pr, already_existed = get_or_create_pr(
        gh_repo,
        branch_name,
        ctx.branch,
        title=f"[Repo Healer] {len(passed_files)} automated fix(es) ({ctx.run_id})",
        body=pr_body,
    )

    return PRResult(
        pr_url=pr.html_url,
        branch=branch_name,
        files_changed=len(passed_files),
        pr_number=pr.number,
        already_existed=already_existed,
    )


async def run_pr(ctx: RunContext, store: ContextStore) -> PRResult:
    """Execute PR creation stage and checkpoint."""
    log.info("pr_started", run_id=ctx.run_id)
    ctx.mark_stage("pr", StageStatus.RUNNING)

    result = create_pr(ctx)

    ctx.pr_url = result.pr_url
    ctx.pr_branch = result.branch
    ctx.mark_stage("pr", StageStatus.COMPLETE)
    await store.set(ctx.run_id, ctx)  # final checkpoint

    log.info("pr_complete", run_id=ctx.run_id, pr_url=result.pr_url)
    return result
