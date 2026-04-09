# Module 05 — Validation Agent

## Purpose

The Validation Agent is the **fifth stage** of the pipeline and the last safety gate before any
code reaches GitHub. It runs four sequential checks on every LLM-generated fix. If any check
fails, the fix is discarded and the healer is given one retry. A fix that cannot pass all four
checks is never committed.

**This agent is not optional.** The healer agent, despite its constraints, can produce code
that parses correctly but fails tests or increases complexity. The Validation Agent is the
only thing preventing bad patches from reaching production.

---

## Tech Stack

| Dependency    | Version | Role                                                          |
|---------------|---------|---------------------------------------------------------------|
| FastAPI       | ≥0.111  | HTTP router                                                   |
| Pydantic v2   | ≥2.7    | `ValidationResult`, `ValidationDetail` schemas                |
| pytest        | ≥8.0    | Run existing test suite on fixed file                         |
| flake8        | ≥7.0    | PEP-8 style and syntax check                                  |
| radon         | ≥6.0    | Complexity regression check                                   |
| aiofiles      | ≥23.2   | Async temp file I/O                                           |
| structlog     | ≥24.1   | Structured logging                                            |
| pytest-mock   | ≥3.12   | Mock subprocess calls in tests                                |

---

## API Endpoint

### `POST /api/v1/validate/fix`

**Request Body:**

```json
{
  "run_id": "20241120-143200",
  "file": "src/utils.py",
  "fixed_code": "<patched source>"
}
```

**Success Response — `200 OK`:**

```json
{
  "status": "PASS",
  "file": "src/utils.py",
  "details": {
    "syntax": { "status": "PASS", "message": "ok" },
    "flake8": { "status": "PASS", "message": "0 errors" },
    "pytest": { "status": "PASS", "message": "14 passed, 0 failed" },
    "complexity": { "status": "PASS", "message": "delta -1.2 (improved)" }
  }
}
```

**Failure Response — `200 OK` (not 4xx — validation failure is a normal outcome):**

```json
{
  "status": "FAIL",
  "file": "src/utils.py",
  "details": {
    "syntax": { "status": "PASS", "message": "ok" },
    "flake8": { "status": "FAIL", "message": "E501 line too long (102 > 79 chars) [line 14]" },
    "pytest": { "status": "SKIP", "message": "skipped due to prior failure" },
    "complexity": { "status": "SKIP", "message": "skipped due to prior failure" }
  }
}
```

Checks are **short-circuit evaluated**: if a check fails, subsequent checks are marked `SKIP`.
This mirrors the fail-fast behaviour of CI pipelines and avoids running expensive test suites
on code that already fails style checks.

---

## Checks

### Check 1: Syntax (`ast.parse`)

```python
import ast

def check_syntax(source_code: str) -> CheckResult:
    try:
        ast.parse(source_code)
        return CheckResult(status="PASS", message="ok")
    except SyntaxError as exc:
        return CheckResult(status="FAIL", message=f"SyntaxError at line {exc.lineno}: {exc.msg}")
```

This is the fastest possible check — pure Python, no subprocess. A `SyntaxError` here means
the LLM produced unparseable code.

### Check 2: flake8

```python
import subprocess, tempfile, pathlib

def check_flake8(source_code: str) -> CheckResult:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source_code)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["flake8", "--max-line-length=100", "--ignore=E303,W503", tmp_path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return CheckResult(status="PASS", message="0 errors")
        errors = result.stdout.strip().replace(tmp_path, "<file>")
        return CheckResult(status="FAIL", message=errors[:500])
    except subprocess.TimeoutExpired:
        return CheckResult(status="FAIL", message="flake8 timed out after 30s")
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
```

**Rationale for `--max-line-length=100`:** The LLM may add verbose type hints that push lines
slightly over 79 chars. 100 is a reasonable production standard that avoids spurious failures
from reasonable type annotations.

**`E303` and `W503` ignored:** `E303` (too many blank lines) can be triggered by added
docstrings. `W503` (line break before binary operator) is a style preference that conflicts
with Black's formatting.

### Check 3: pytest

```python
import subprocess, shutil, tempfile, pathlib

def check_pytest(repo_path: str, file: str, fixed_code: str) -> CheckResult:
    # Copy the repo to a temp directory so tests run against the patched file
    with tempfile.TemporaryDirectory() as tmp_dir:
        shutil.copytree(repo_path, tmp_dir, dirs_exist_ok=True)
        patched_path = pathlib.Path(tmp_dir) / file
        patched_path.write_text(fixed_code, encoding="utf-8")

        result = subprocess.run(
            ["python", "-m", "pytest", tmp_dir, "-x", "-q",
             "--no-header", "--tb=short", "--timeout=60"],
            capture_output=True, text=True, timeout=120, cwd=tmp_dir
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            passed = _extract_passed_count(output)
            return CheckResult(status="PASS", message=passed)
        return CheckResult(status="FAIL", message=output[-1000:])
```

**Key implementation details:**

- The patched file is written into a **copy** of the repo in a temp directory. The original
  working tree is never modified during validation.
- `-x` (fail fast) stops pytest on the first failure — faster feedback, less log noise.
- `--timeout=60` (requires `pytest-timeout`) prevents test hangs from blocking the pipeline.
- The temp directory is always cleaned up via context manager, even on failure.

### Check 4: Complexity Regression

```python
from radon.complexity import cc_visit

def check_complexity(file: str, fixed_code: str, baseline: ComplexityRecord) -> CheckResult:
    if baseline is None or baseline.parse_error:
        return CheckResult(status="SKIP", message="no baseline complexity available")

    functions = cc_visit(fixed_code)
    new_complexity = sum(f.complexity for f in functions) / len(functions) if functions else 0.0
    delta = new_complexity - baseline.complexity

    if delta > 2.0:
        return CheckResult(
            status="FAIL",
            message=f"complexity increased by {delta:.2f} (baseline {baseline.complexity:.2f} → new {new_complexity:.2f})"
        )
    sign = "+" if delta >= 0 else ""
    return CheckResult(
        status="PASS",
        message=f"delta {sign}{delta:.2f} ({'worsened' if delta > 0 else 'improved or unchanged'})"
    )
```

A complexity **increase of more than 2.0** fails the check. A modest increase (≤2.0) is
tolerated — the LLM may add a helper function that adds one decision point. A decrease or
neutral change always passes.

---

## Context Store Integration

```python
async def validate_fix(ctx: RunContext, fix: HealResult, store: ContextStore) -> ValidationResult:
    baseline = next((r for r in ctx.complexity if r.file == fix.file), None)

    # Check 1
    syntax = check_syntax(fix.fixed_code)
    if syntax.status == "FAIL":
        result = _build_result(fix.file, "FAIL", syntax, skip=True)
        _update_context(ctx, fix, result)
        await store.set(ctx.run_id, ctx)
        return result

    # Check 2
    flake = check_flake8(fix.fixed_code)
    if flake.status == "FAIL":
        result = _build_result(fix.file, "FAIL", syntax, flake, skip_rest=True)
        _update_context(ctx, fix, result)
        await store.set(ctx.run_id, ctx)
        return result

    # Check 3
    pytest_res = check_pytest(ctx.local_repo_path, fix.file, fix.fixed_code)
    if pytest_res.status == "FAIL":
        result = _build_result(fix.file, "FAIL", syntax, flake, pytest_res, skip_rest=True)
        _update_context(ctx, fix, result)
        await store.set(ctx.run_id, ctx)
        return result

    # Check 4
    complexity_res = check_complexity(fix.file, fix.fixed_code, baseline)
    overall = "PASS" if complexity_res.status in ("PASS", "SKIP") else "FAIL"
    result = _build_result(fix.file, overall, syntax, flake, pytest_res, complexity_res)
    _update_context(ctx, fix, result)
    await store.set(ctx.run_id, ctx)
    return result
```

---

## Testing Module: `tests/test_05_validation.py`

```python
import pytest
import subprocess
from app.modules.validation.service import (
    check_syntax, check_flake8, check_pytest, check_complexity
)
from app.modules.validation.schemas import ValidationResult
from app.modules.complexity.schemas import ComplexityRecord

VALID_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
SYNTAX_ERROR_CODE = "def broken(:\n    pass"
LONG_LINE_CODE = "def f():\n    x = 'a' * 200  # " + "x" * 80 + "\n"
COMPLEX_CODE = "\n".join([
    "def f(x, y, z):",
    "    if x:",
    "        if y:",
    "            for i in range(z):",
    "                if i % 2:",
    "                    if i > 5:",
    "                        pass",
    "                else:",
    "                    pass",
    "        elif y < 0:",
    "            pass",
    "    return x",
])


# ── Syntax Tests ─────────────────────────────────────────────────────────────

class TestCheckSyntax:

    def test_valid_code_passes(self):
        result = check_syntax(VALID_CODE)
        assert result.status == "PASS"

    def test_syntax_error_fails(self):
        result = check_syntax(SYNTAX_ERROR_CODE)
        assert result.status == "FAIL"
        assert "SyntaxError" in result.message

    def test_empty_string_passes(self):
        result = check_syntax("")
        assert result.status == "PASS"

    def test_unicode_code_passes(self):
        result = check_syntax("x = '日本語'\n")
        assert result.status == "PASS"

    def test_line_number_in_error_message(self):
        result = check_syntax("x = 1\ndef bad(:\n    pass\n")
        assert "line" in result.message.lower()


# ── flake8 Tests ──────────────────────────────────────────────────────────────

class TestCheckFlake8:

    def test_clean_code_passes(self):
        result = check_flake8(VALID_CODE)
        assert result.status == "PASS"

    def test_long_line_fails(self):
        result = check_flake8(LONG_LINE_CODE)
        assert result.status == "FAIL"
        assert "E501" in result.message or result.status == "FAIL"

    def test_tmp_file_cleaned_up_on_pass(self, tmp_path, mocker):
        unlink_calls = []
        real_unlink = __builtins__["__import__"]("pathlib").Path.unlink
        def track_unlink(self, *args, **kwargs):
            unlink_calls.append(str(self))
            return real_unlink(self, *args, **kwargs)
        mocker.patch("pathlib.Path.unlink", track_unlink)
        check_flake8(VALID_CODE)
        assert any(".py" in p for p in unlink_calls)

    def test_tmp_file_cleaned_up_on_fail(self, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], returncode=1, stdout="E501 line too long", stderr=""),
        )
        result = check_flake8(LONG_LINE_CODE)
        assert result.status == "FAIL"

    def test_timeout_returns_fail(self, mocker):
        mocker.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("flake8", 30))
        result = check_flake8(VALID_CODE)
        assert result.status == "FAIL"
        assert "timed out" in result.message


# ── pytest Tests ──────────────────────────────────────────────────────────────

class TestCheckPytest:

    def test_passing_tests_return_pass(self, test_repo_with_tests, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], returncode=0, stdout="3 passed", stderr=""),
        )
        result = check_pytest(test_repo_with_tests, "src/utils.py", VALID_CODE)
        assert result.status == "PASS"
        assert "passed" in result.message

    def test_failing_test_returns_fail(self, test_repo_with_tests, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], returncode=1, stdout="1 failed\nAssertionError", stderr=""),
        )
        result = check_pytest(test_repo_with_tests, "src/utils.py", VALID_CODE)
        assert result.status == "FAIL"
        assert "failed" in result.message.lower()

    def test_original_repo_not_modified(self, test_repo_with_tests, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], returncode=0, stdout="1 passed", stderr=""),
        )
        import pathlib
        original_content = (pathlib.Path(test_repo_with_tests) / "src/utils.py").read_text()
        check_pytest(test_repo_with_tests, "src/utils.py", "# completely different content\n")
        actual_content = (pathlib.Path(test_repo_with_tests) / "src/utils.py").read_text()
        assert actual_content == original_content

    def test_pytest_timeout_returns_fail(self, test_repo_with_tests, mocker):
        mocker.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 120))
        result = check_pytest(test_repo_with_tests, "src/utils.py", VALID_CODE)
        assert result.status == "FAIL"


# ── Complexity Regression Tests ───────────────────────────────────────────────

class TestCheckComplexity:

    def test_reduced_complexity_passes(self):
        baseline = ComplexityRecord(file="f.py", complexity=10.0, maintainability=50.0)
        result = check_complexity("f.py", VALID_CODE, baseline)
        assert result.status == "PASS"
        assert "improved" in result.message or "delta" in result.message

    def test_large_increase_fails(self):
        baseline = ComplexityRecord(file="f.py", complexity=3.0, maintainability=80.0)
        result = check_complexity("f.py", COMPLEX_CODE, baseline)
        assert result.status == "FAIL"
        assert "increased" in result.message

    def test_small_increase_tolerated(self):
        baseline = ComplexityRecord(file="f.py", complexity=5.0, maintainability=70.0)
        slightly_more = VALID_CODE + "\ndef helper(x):\n    if x:\n        return x\n    return None\n"
        result = check_complexity("f.py", slightly_more, baseline)
        assert result.status == "PASS"

    def test_no_baseline_skips(self):
        result = check_complexity("f.py", VALID_CODE, None)
        assert result.status == "SKIP"

    def test_parse_error_baseline_skips(self):
        baseline = ComplexityRecord(file="f.py", complexity=-1.0, maintainability=-1.0, parse_error=True)
        result = check_complexity("f.py", VALID_CODE, baseline)
        assert result.status == "SKIP"


# ── Short-Circuit Tests ───────────────────────────────────────────────────────

class TestShortCircuit:

    @pytest.mark.asyncio
    async def test_syntax_failure_skips_remaining(
        self, run_context, context_store, mocker
    ):
        from app.modules.validation.service import validate_fix
        from app.modules.healer.schemas import HealResult
        fix = HealResult(
            run_id=run_context.run_id,
            file="src/bad.py",
            fixed_code=SYNTAX_ERROR_CODE,
            summary="bad fix",
            changed=True,
        )
        result = await validate_fix(run_context, fix, context_store)
        assert result.status == "FAIL"
        assert result.details["flake8"].status == "SKIP"
        assert result.details["pytest"].status == "SKIP"
        assert result.details["complexity"].status == "SKIP"

    @pytest.mark.asyncio
    async def test_passing_fix_writes_checkpoint(
        self, run_context, context_store, mocker
    ):
        from app.modules.validation.service import validate_fix
        from app.modules.healer.schemas import HealResult
        mocker.patch("app.modules.validation.service.check_flake8",
                     return_value=type("R", (), {"status": "PASS", "message": "ok"})())
        mocker.patch("app.modules.validation.service.check_pytest",
                     return_value=type("R", (), {"status": "PASS", "message": "5 passed"})())
        fix = HealResult(
            run_id=run_context.run_id,
            file="src/add.py",
            fixed_code=VALID_CODE,
            summary="typed",
            changed=True,
        )
        result = await validate_fix(run_context, fix, context_store)
        assert result.status == "PASS"
        stored = await context_store.get(run_context.run_id)
        assert any(v.file == "src/add.py" for v in stored.validations)
```

---

## Running Tests

```bash
pytest tests/test_05_validation.py -v
pytest tests/test_05_validation.py --cov=app/modules/validation --cov-report=term-missing
```

---

## Common Issues & Resolutions

**Issue:** pytest check hangs in CI with no timeout.
**Resolution:** Always pass `--timeout=60` (requires `pytest-timeout` in `requirements-dev.txt`).
Add `timeout=120` to the `subprocess.run` call as a hard backstop.

**Issue:** flake8 check fails on LLM-added type hints with `E501`.
**Resolution:** Use `--max-line-length=100`. If the LLM generates lines longer than 100 chars,
inject `# noqa: E501` handling or add a post-processing step to wrap long lines.

**Issue:** pytest runs against the wrong Python environment.
**Resolution:** Use `sys.executable` instead of `"python"` in the subprocess call:
`[sys.executable, "-m", "pytest", ...]`.

**Issue:** Temp directory is not cleaned up on Windows due to file lock by pytest.
**Resolution:** Use `shutil.rmtree(tmp_dir, ignore_errors=True)` in a `finally` block instead
of relying on `TemporaryDirectory.__exit__`.
