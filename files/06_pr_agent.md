# Module 06 — PR Agent

## Purpose

The PR Agent is the **final stage** of the pipeline. After all fixes have passed validation,
it creates a Git branch, commits each fixed file with a descriptive message, pushes the branch
to the remote, and opens a Pull Request via the GitHub REST API.

This stage is the only one that writes to external state (GitHub). Every other stage only reads.

---

## Tech Stack

| Dependency    | Version | Role                                                        |
|---------------|---------|-------------------------------------------------------------|
| FastAPI       | ≥0.111  | HTTP router                                                 |
| Pydantic v2   | ≥2.7    | `PRResult` schema                                           |
| PyGitHub      | ≥2.3    | GitHub API: branch, commit, push, PR creation              |
| GitPython     | ≥3.1    | Local git operations (branch, add, commit, push)           |
| structlog     | ≥24.1   | Structured logging                                          |
| pytest        | ≥8.0    | Test runner                                                 |
| pytest-mock   | ≥3.12   | Mock PyGitHub and GitPython                                 |

---

## API Endpoint

### `POST /api/v1/pr/create`

**Request Body:**

```json
{ "run_id": "20241120-143200" }
```

**Success Response — `200 OK`:**

```json
{
  "pr_url": "https://github.com/org/repo/pull/42",
  "branch": "repo-healer/20241120-143200",
  "files_changed": 3,
  "pr_number": 42,
  "already_existed": false
}
```

**Idempotent — PR already exists:**

```json
{
  "pr_url": "https://github.com/org/repo/pull/42",
  "branch": "repo-healer/20241120-143200",
  "files_changed": 0,
  "pr_number": 42,
  "already_existed": true
}
```

**Error Responses:**

| Status | Condition                                                                |
|--------|--------------------------------------------------------------------------|
| 401    | `GITHUB_TOKEN` missing or lacks `repo` scope                             |
| 404    | `run_id` not found in context store                                      |
| 422    | Malformed body                                                           |
| 424    | No validated PASS fixes in `RunContext.validations`                      |
| 502    | GitHub API unreachable                                                   |

---

## Branch Naming

Branches follow the pattern `repo-healer/<run_id>`. The `run_id` is a
`YYYYMMDD-HHMMSS` timestamp generated at pipeline start. This gives:

- Human-readable ordering in the GitHub branch list.
- Idempotency: a second run with the same `run_id` targets the same branch.
- Namespace isolation: all healer branches are grouped under the `repo-healer/` prefix.

---

## Idempotency Guard

Before creating a PR, the agent checks whether an open PR from the healer branch already
exists:

```python
from github import Github, GithubException

def get_or_create_pr(repo, branch_name: str, base_branch: str, title: str, body: str):
    open_prs = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch_name}")
    for pr in open_prs:
        if pr.head.ref == branch_name:
            return pr, True   # already existed
    new_pr = repo.create_pull(
        title=title,
        body=body,
        head=branch_name,
        base=base_branch,
    )
    return new_pr, False
```

Without this check, re-running the pipeline on the same `run_id` (e.g. after a transient
failure) would create duplicate PRs.

---

## Service Implementation

```python
import git as gitpython
from github import Github
import pathlib

def create_pr(ctx: RunContext) -> PRResult:
    # Only commit files with PASS validation
    passed_files = [
        fix for fix in ctx.fixes
        if any(v.file == fix.file and v.status == "PASS" for v in ctx.validations)
    ]

    if not passed_files:
        raise PRError("no validated fixes to commit")

    # Local git operations
    repo = gitpython.Repo(ctx.local_repo_path)
    branch_name = f"repo-healer/{ctx.run_id}"
    
    # Create branch from origin/base_branch to avoid dirty state issues
    origin = repo.remote("origin")
    repo.git.checkout("-b", branch_name, f"origin/{ctx.branch}")

    for fix in passed_files:
        file_path = pathlib.Path(ctx.local_repo_path) / fix.file
        file_path.write_text(fix.fixed_code, encoding="utf-8")
        repo.index.add([str(file_path)])
        repo.index.commit(
            f"fix({fix.file}): {fix.summary[:72]}\n\nAutomated fix by Repo Healer (run {ctx.run_id})"
        )

    origin.push(branch_name)

    # GitHub API
    g = Github(settings.github_token.get_secret_value())
    gh_repo = g.get_repo(ctx.repo_url.split("github.com/")[-1].rstrip(".git"))
    
    pr_body = _build_pr_body(ctx, passed_files)
    pr, already_existed = get_or_create_pr(
        gh_repo, branch_name, ctx.branch,
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
```

### PR Body Template

```python
def _build_pr_body(ctx: RunContext, passed_files: list[HealResult]) -> str:
    lines = [
        "## 🔬 Repo Healer Automated Fix",
        f"",
        f"**Run ID:** `{ctx.run_id}`  ",
        f"**Repository:** {ctx.repo_url}  ",
        f"**Branch:** {ctx.branch}  ",
        f"**Files healed:** {len(passed_files)}",
        f"",
        "### Changes",
    ]
    for fix in passed_files:
        risk = next((r for r in ctx.risk if r.file == fix.file), None)
        risk_str = f" (risk: {risk.risk_score:.2f} {risk.risk_level})" if risk else ""
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
```

---

## Testing Module: `tests/test_06_pr.py`

```python
import pytest
from unittest.mock import MagicMock, patch
from app.modules.pr.service import create_pr, get_or_create_pr
from app.modules.pr.schemas import PRResult
from app.core.exceptions import PRError

def make_mock_github(existing_pr=None):
    pr_mock = MagicMock()
    pr_mock.html_url = "https://github.com/org/repo/pull/42"
    pr_mock.number = 42
    pr_mock.head.ref = "repo-healer/test-run"
    repo_mock = MagicMock()
    repo_mock.owner.login = "org"
    if existing_pr:
        repo_mock.get_pulls.return_value = [pr_mock]
    else:
        repo_mock.get_pulls.return_value = []
        repo_mock.create_pull.return_value = pr_mock
    g_mock = MagicMock()
    g_mock.get_repo.return_value = repo_mock
    return g_mock, repo_mock, pr_mock


class TestGetOrCreatePR:

    def test_creates_new_pr_when_none_exists(self):
        _, repo_mock, pr_mock = make_mock_github(existing_pr=None)
        pr, existed = get_or_create_pr(repo_mock, "repo-healer/test", "main", "title", "body")
        assert existed is False
        repo_mock.create_pull.assert_called_once()

    def test_returns_existing_pr_without_creating(self):
        _, repo_mock, pr_mock = make_mock_github(existing_pr=pr_mock := MagicMock())
        pr_mock.head.ref = "repo-healer/test"
        repo_mock.get_pulls.return_value = [pr_mock]
        pr, existed = get_or_create_pr(repo_mock, "repo-healer/test", "main", "title", "body")
        assert existed is True
        repo_mock.create_pull.assert_not_called()

    def test_branch_name_checked_precisely(self):
        pr_mock = MagicMock()
        pr_mock.head.ref = "repo-healer/other-run"  # different run_id
        repo_mock = MagicMock()
        repo_mock.get_pulls.return_value = [pr_mock]
        new_pr = MagicMock()
        new_pr.head.ref = "repo-healer/this-run"
        repo_mock.create_pull.return_value = new_pr
        pr, existed = get_or_create_pr(repo_mock, "repo-healer/this-run", "main", "t", "b")
        assert existed is False
        repo_mock.create_pull.assert_called_once()


class TestCreatePR:

    def test_raises_if_no_passing_fixes(self, run_context_no_passing_fixes, context_store):
        with pytest.raises(PRError, match="no validated fixes"):
            create_pr(run_context_no_passing_fixes)

    def test_only_passing_fixes_committed(self, mocker, run_context_mixed_validations):
        mock_repo = MagicMock()
        mock_repo.git.checkout = MagicMock()
        mock_repo.remote.return_value.push = MagicMock()
        mocker.patch("git.Repo", return_value=mock_repo)
        g_mock, repo_mock, pr_mock = make_mock_github()
        mocker.patch("github.Github", return_value=g_mock)
        mocker.patch("app.modules.pr.service.settings")
        result = create_pr(run_context_mixed_validations)
        # Only PASS validations should result in commits
        committed_files = [call.args[0] for call in mock_repo.index.add.call_args_list]
        for fix in run_context_mixed_validations.fixes:
            v = next((v for v in run_context_mixed_validations.validations if v.file == fix.file), None)
            if v and v.status == "FAIL":
                assert not any(fix.file in str(f) for f in committed_files)

    def test_pr_branch_follows_naming_convention(self, mocker, run_context_with_passing_fix):
        mock_repo = MagicMock()
        mocker.patch("git.Repo", return_value=mock_repo)
        g_mock, repo_mock, pr_mock = make_mock_github()
        mocker.patch("github.Github", return_value=g_mock)
        mocker.patch("app.modules.pr.service.settings")
        create_pr(run_context_with_passing_fix)
        checkout_args = mock_repo.git.checkout.call_args
        branch = checkout_args.args[1]
        assert branch.startswith("repo-healer/")

    def test_commit_message_includes_run_id(self, mocker, run_context_with_passing_fix):
        mock_repo = MagicMock()
        mocker.patch("git.Repo", return_value=mock_repo)
        g_mock, repo_mock, pr_mock = make_mock_github()
        mocker.patch("github.Github", return_value=g_mock)
        mocker.patch("app.modules.pr.service.settings")
        create_pr(run_context_with_passing_fix)
        commit_msg = mock_repo.index.commit.call_args.args[0]
        assert run_context_with_passing_fix.run_id in commit_msg

    def test_result_contains_pr_url(self, mocker, run_context_with_passing_fix):
        mock_repo = MagicMock()
        mocker.patch("git.Repo", return_value=mock_repo)
        g_mock, repo_mock, pr_mock = make_mock_github()
        mocker.patch("github.Github", return_value=g_mock)
        mocker.patch("app.modules.pr.service.settings")
        result = create_pr(run_context_with_passing_fix)
        assert result.pr_url.startswith("https://github.com/")
        assert isinstance(result.pr_number, int)


class TestPRRouter:

    def test_post_method_required(self, client):
        resp = client.get("/api/v1/pr/create")
        assert resp.status_code == 405

    def test_no_validations_returns_424(self, client, empty_run_context):
        resp = client.post("/api/v1/pr/create", json={"run_id": empty_run_context.run_id})
        assert resp.status_code == 424

    def test_missing_github_token_returns_401(self, client, mocker, seeded_pr_context):
        mocker.patch(
            "app.modules.pr.service.create_pr",
            side_effect=PRError("401 Unauthorized — check GITHUB_TOKEN"),
        )
        resp = client.post("/api/v1/pr/create", json={"run_id": seeded_pr_context.run_id})
        assert resp.status_code in (401, 502)


class TestPRContextPropagation:

    @pytest.mark.asyncio
    async def test_pr_url_written_to_context(self, run_context, context_store, mocker):
        from app.modules.pr.service import run_pr
        mocker.patch("app.modules.pr.service.create_pr", return_value=PRResult(
            pr_url="https://github.com/org/repo/pull/99",
            branch="repo-healer/test",
            files_changed=1,
            pr_number=99,
            already_existed=False,
        ))
        await run_pr(run_context, context_store)
        stored = await context_store.get(run_context.run_id)
        assert stored.pr_url == "https://github.com/org/repo/pull/99"
        assert stored.stage_flags["pr"].value == "COMPLETE"
```

---

## Running Tests

```bash
pytest tests/test_06_pr.py -v
pytest tests/test_06_pr.py --cov=app/modules/pr --cov-report=term-missing
```

---

## Common Issues & Resolutions

**Issue:** `401 Unauthorized` when creating a PR.
**Resolution:** Verify `GITHUB_TOKEN` has `repo` scope. Fine-grained tokens need
`contents: write` and `pull-requests: write` permissions on the target repository.

**Issue:** `422 Unprocessable Entity` from GitHub when creating a PR.
**Resolution:** This usually means the branch is up-to-date with base — there are no
commits to merge. Verify at least one file was committed before calling `create_pull`.

**Issue:** `git push` fails with `non-fast-forward` error.
**Resolution:** The branch already exists on the remote from a previous partial run.
Use `push --force-with-lease` to update it safely:
`origin.push(branch_name, force_with_lease=True)`.

**Issue:** `GitCommandError: 'origin' does not appear to be a git repository`.
**Resolution:** PyDriller clones to a temp directory. That clone has an `origin` remote
pointing to the original URL. Verify `ctx.local_repo_path` is the PyDriller-cloned path,
not a path created by the test fixture.
