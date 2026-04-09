# Module 09 — API Tests & End-to-End Pipeline Test

## Purpose

This module contains two layers of testing:

1. **Smoke tests** — `curl` scripts for manual end-to-end verification in development. These
   are not part of the automated test suite and are not used in CI.
2. **E2E pytest suite** — `tests/test_07_pipeline_e2e.py` runs the full pipeline from analysis
   through PR creation against a seeded local git fixture. This is the highest-confidence test
   in the suite and covers cross-module context propagation.

The original `09_api_test_scripts.md` contained only raw `curl` commands with `GET` on
state-mutating endpoints. Both issues are corrected here:
- All mutating endpoints use `POST`.
- The real test suite uses `pytest` with `httpx.AsyncClient` and session fixtures.

---

## Tech Stack

| Dependency        | Version | Role                                               |
|-------------------|---------|----------------------------------------------------|
| pytest            | ≥8.0    | E2E test runner                                    |
| httpx             | ≥0.27   | Async HTTP client for FastAPI test client          |
| pytest-asyncio    | ≥0.23   | Async test support                                 |
| pytest-mock       | ≥3.12   | Stub LLM and GitHub API in E2E tests               |
| fakeredis         | ≥2.21   | In-memory Redis for E2E store                      |

---

## Manual Smoke Tests (Development Only)

These scripts require a running server (`uvicorn app.main:app --reload --port 8000`) and a
valid `.env` file. They are for developer verification, not CI.

```bash
#!/usr/bin/env bash
# scripts/smoke_test.sh
set -e
BASE="http://localhost:8000/api/v1"
REPO_URL="https://github.com/your-org/your-repo"

echo "=== 1. Analyze Repo ==="
ANALYZE_RESP=$(curl -sf "${BASE}/analyze/repo?repo_url=${REPO_URL}&branch=main")
echo "$ANALYZE_RESP" | python3 -m json.tool
RUN_ID=$(echo "$ANALYZE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "run_id: $RUN_ID"

echo "=== 2. Compute Complexity ==="
curl -sf -X POST "${BASE}/analyze/complexity" \
  -H "Content-Type: application/json" \
  -d "{\"run_id\": \"$RUN_ID\"}" | python3 -m json.tool

echo "=== 3. Predict Risk ==="
curl -sf -X POST "${BASE}/predict/risk" \
  -H "Content-Type: application/json" \
  -d "{\"run_id\": \"$RUN_ID\"}" | python3 -m json.tool

echo "=== 4. Run Full Pipeline ==="
curl -sf -X POST "${BASE}/pipeline/run" \
  -H "Content-Type: application/json" \
  -d "{\"repo_url\": \"$REPO_URL\", \"branch\": \"main\"}" | python3 -m json.tool

echo "=== Smoke tests passed ==="
```

**Why `POST` for complexity, risk, heal, validate, pr, pipeline?** These endpoints mutate
state (they write to the context store, call external APIs, or trigger subprocesses). REST
semantics reserve `GET` for safe, idempotent, read-only operations. Using `GET` for
state-mutation breaks caching proxies, breaks browser prefetch, and is rejected by many API
gateways with WAF rules.

---

## E2E Test Suite: `tests/test_07_pipeline_e2e.py`

The E2E test runs the full 6-stage pipeline against a tiny local git repository created by
`scripts/seed_test_repo.sh`. The LLM and GitHub API are stubbed so the test is fast, hermetic,
and runnable in CI without credentials.

### Test Repo Seed Script

```bash
#!/usr/bin/env bash
# scripts/seed_test_repo.sh
# Creates a minimal Python repo at /tmp/test-repo for E2E tests

set -e
REPO=/tmp/test-repo
rm -rf "$REPO"
mkdir -p "$REPO/src" "$REPO/tests"
cd "$REPO"
git init
git config user.email "test@example.com"
git config user.name "Test"

# A simple module with measurable complexity
cat > src/utils.py << 'EOF'
def process(items, threshold):
    result = []
    for item in items:
        if item > threshold:
            if item % 2 == 0:
                result.append(item * 2)
            else:
                result.append(item)
    return result

def add(a, b):
    return a + b
EOF

# A passing test
cat > tests/test_utils.py << 'EOF'
from src.utils import add, process

def test_add():
    assert add(1, 2) == 3

def test_process_empty():
    assert process([], 5) == []

def test_process_filters():
    assert 3 not in process([1, 2, 3, 4, 5], 3)
EOF

git add .
git commit -m "initial commit"

# A second commit to create churn
sed -i 's/return result/return sorted(result)/' src/utils.py
git add .
git commit -m "sort results"

echo "Test repo seeded at $REPO"
```

### E2E Test File

```python
import pytest
import asyncio
import json
from fastapi.testclient import TestClient
from app.main import app
from app.core.context_store import InMemoryContextStore
from app.core.schemas import RunContext, StageStatus

client = TestClient(app)

MOCK_HEAL_RESPONSE = json.dumps({
    "fixed_code": "def process(items, threshold):\n    return sorted(i * 2 if i % 2 == 0 else i for i in items if i > threshold)\n\ndef add(a: int, b: int) -> int:\n    return a + b\n",
    "summary": "Refactored process() to list comprehension. Added type hints to add().",
})

MOCK_GITHUB_PR_URL = "https://github.com/test/repo/pull/1"


@pytest.fixture(scope="module")
def seeded_repo(tmp_path_factory):
    """Create a minimal git repo for E2E tests."""
    import subprocess, pathlib
    repo_dir = tmp_path_factory.mktemp("e2e_repo")
    subprocess.run(["git", "init", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    src_dir = repo_dir / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("")
    (src_dir / "utils.py").write_text(
        "def process(items, threshold):\n    result = []\n    for item in items:\n        if item > threshold:\n            result.append(item)\n    return result\n\ndef add(a, b):\n    return a + b\n"
    )
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_utils.py").write_text(
        "from src.utils import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "init"], check=True)
    return str(repo_dir)


class TestFullPipelineE2E:

    def test_analyze_endpoint_returns_file_records(self, seeded_repo):
        resp = client.get(
            "/api/v1/analyze/repo",
            params={"repo_url": seeded_repo, "branch": "main"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["analysis"]) > 0
        assert any("utils.py" in r["file"] for r in body["analysis"])

    def test_complexity_endpoint_enriches_context(self, seeded_repo, mocker):
        # Step 1: analyze
        analyze_resp = client.get(
            "/api/v1/analyze/repo",
            params={"repo_url": seeded_repo, "branch": "main"},
        )
        run_id = analyze_resp.json()["run_id"]

        # Step 2: complexity
        mocker.patch(
            "app.modules.complexity.service.read_source",
            return_value="def add(a, b):\n    return a + b\n",
        )
        complexity_resp = client.post(
            "/api/v1/analyze/complexity",
            json={"run_id": run_id},
        )
        assert complexity_resp.status_code == 200
        assert len(complexity_resp.json()["complexity"]) > 0

    def test_risk_produces_normalised_scores(self, seeded_repo, mocker):
        analyze_resp = client.get(
            "/api/v1/analyze/repo",
            params={"repo_url": seeded_repo},
        )
        run_id = analyze_resp.json()["run_id"]
        mocker.patch("app.modules.complexity.service.read_source",
                     return_value="def add(a, b):\n    return a + b\n")
        client.post("/api/v1/analyze/complexity", json={"run_id": run_id})
        risk_resp = client.post("/api/v1/predict/risk", json={"run_id": run_id})
        assert risk_resp.status_code == 200
        for item in risk_resp.json()["risk"]:
            assert 0.0 <= item["risk_score"] <= 1.0

    def test_full_pipeline_run_returns_run_id(self, seeded_repo, mocker):
        mocker.patch("app.modules.healer.service.call_llm",
                     return_value=json.loads(MOCK_HEAL_RESPONSE))
        mocker.patch("app.modules.complexity.service.read_source",
                     return_value="def add(a, b):\n    return a + b\n")
        mocker.patch("app.modules.validation.service.check_pytest",
                     return_value=type("R", (), {"status": "PASS", "message": "3 passed"})())
        mocker.patch("app.modules.pr.service.create_pr",
                     return_value=type("R", (), {
                         "pr_url": MOCK_GITHUB_PR_URL, "branch": "repo-healer/test",
                         "files_changed": 1, "pr_number": 1, "already_existed": False
                     })())
        resp = client.post("/api/v1/pipeline/run", json={
            "repo_url": seeded_repo,
            "branch": "main",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "run_id" in body

    def test_pipeline_context_checkpointed_at_each_stage(self, seeded_repo, mocker):
        """Each stage must checkpoint; polling /pipeline/{run_id} shows progress."""
        mocker.patch("app.modules.healer.service.call_llm",
                     return_value=json.loads(MOCK_HEAL_RESPONSE))
        mocker.patch("app.modules.complexity.service.read_source",
                     return_value="def add(a, b): return a + b\n")
        mocker.patch("app.modules.validation.service.check_pytest",
                     return_value=type("R", (), {"status": "PASS", "message": "ok"})())
        mocker.patch("app.modules.pr.service.create_pr",
                     return_value=type("R", (), {
                         "pr_url": MOCK_GITHUB_PR_URL, "branch": "b", "files_changed": 1,
                         "pr_number": 1, "already_existed": False,
                     })())
        run_resp = client.post("/api/v1/pipeline/run", json={"repo_url": seeded_repo})
        run_id = run_resp.json()["run_id"]
        status_resp = client.get(f"/api/v1/pipeline/{run_id}")
        assert status_resp.status_code == 200
        ctx = status_resp.json()
        assert ctx["stage_flags"].get("analysis") == "COMPLETE"

    def test_pipeline_status_404_for_unknown_run(self):
        resp = client.get("/api/v1/pipeline/nonexistent-run-id")
        assert resp.status_code == 404

    def test_get_on_mutating_endpoints_returns_405(self):
        for path in ["/api/v1/predict/risk", "/api/v1/heal/file",
                     "/api/v1/validate/fix", "/api/v1/pr/create"]:
            resp = client.get(path)
            assert resp.status_code == 405, f"Expected 405 for GET {path}, got {resp.status_code}"
```

---

## Running E2E Tests

```bash
# Full E2E suite (stubs LLM and GitHub)
pytest tests/test_07_pipeline_e2e.py -v

# Full suite including E2E
pytest tests/ -v --tb=short

# With coverage
pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=80
```

---

## Session Context Propagation — How It Works

The session-scoped `run_context` fixture in `tests/conftest.py` is the key to keeping context
alive across all 7 test files without re-running expensive setup:

```
test_01_analyzer.py      → populates run_context.analysis
test_02_complexity.py    → reads .analysis, populates .complexity
test_03_risk.py          → reads .complexity, populates .risk
test_04_healer.py        → reads .risk, populates .fixes
test_05_validation.py    → reads .fixes, populates .validations
test_06_pr.py            → reads .validations, writes .pr_url
test_07_pipeline_e2e.py  → starts fresh with module-scoped fixtures
```

Because `run_context` is `scope="session"`, pytest creates it once at the start of the session
and passes the same object to every test that requests it. Mutations made by test_01 are
visible to test_02, exactly as in production.

**Important:** Test ordering matters. Run the full test suite with `pytest tests/` — do not run
individual test files in isolation when testing context propagation. Individual module tests
work independently because they use their own patched fixtures, but the propagation chain
requires sequential execution.
