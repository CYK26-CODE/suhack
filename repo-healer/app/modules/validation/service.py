"""Validation Agent — service layer.

Runs 4 sequential checks on LLM-generated fixes: syntax, flake8, pytest, complexity.
Short-circuit evaluation: if a check fails, subsequent checks are SKIP.
"""

from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

import structlog

from app.core.context_store import ContextStore
from app.core.schemas import (
    CheckResult,
    ComplexityRecord,
    HealResult,
    RunContext,
    StageStatus,
    ValidationResult,
)

log = structlog.get_logger(__name__)


# ── Check 1: Syntax ─────────────────────────────────────────────────────────


def check_syntax(source_code: str) -> CheckResult:
    """Verify the code parses as valid Python via ast.parse."""
    try:
        ast.parse(source_code)
        return CheckResult(status="PASS", message="ok")
    except SyntaxError as exc:
        return CheckResult(
            status="FAIL",
            message=f"SyntaxError at line {exc.lineno}: {exc.msg}",
        )


# ── Check 2: flake8 ─────────────────────────────────────────────────────────


def check_flake8(source_code: str) -> CheckResult:
    """Run flake8 on the source code in a temp file."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False
        ) as f:
            f.write(source_code)
            tmp_path = f.name

        result = subprocess.run(
            ["flake8", "--max-line-length=100", "--ignore=E303,W503", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return CheckResult(status="PASS", message="0 errors")
        errors = result.stdout.strip().replace(tmp_path, "<file>")
        return CheckResult(status="FAIL", message=errors[:500])
    except subprocess.TimeoutExpired:
        return CheckResult(status="FAIL", message="flake8 timed out after 30s")
    except FileNotFoundError:
        return CheckResult(status="SKIP", message="flake8 not installed")
    finally:
        if tmp_path:
            pathlib.Path(tmp_path).unlink(missing_ok=True)


# ── Check 3: pytest ──────────────────────────────────────────────────────────


def check_pytest(repo_path: str, file: str, fixed_code: str) -> CheckResult:
    """Run existing test suite against the patched file in a temp copy of the repo."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            shutil.copytree(repo_path, tmp_dir, dirs_exist_ok=True)
            patched_path = pathlib.Path(tmp_dir) / file
            patched_path.parent.mkdir(parents=True, exist_ok=True)
            patched_path.write_text(fixed_code, encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    tmp_dir,
                    "-x",
                    "-q",
                    "--no-header",
                    "--tb=short",
                    "--timeout=60",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=tmp_dir,
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                passed = _extract_passed_count(output)
                return CheckResult(status="PASS", message=passed)
            return CheckResult(status="FAIL", message=output[-1000:])
    except subprocess.TimeoutExpired:
        return CheckResult(status="FAIL", message="pytest timed out after 120s")
    except Exception as exc:
        return CheckResult(status="FAIL", message=f"pytest error: {str(exc)[:500]}")


def _extract_passed_count(output: str) -> str:
    """Extract pass/fail counts from pytest output."""
    for line in output.splitlines()[::-1]:
        if "passed" in line or "failed" in line:
            return line.strip()
    return output[-200:] if output else "no output"


# ── Check 4: Complexity Regression ───────────────────────────────────────────


def check_complexity(
    file: str, fixed_code: str, baseline: ComplexityRecord | None
) -> CheckResult:
    """Verify the fix doesn't significantly increase complexity."""
    if baseline is None or baseline.parse_error:
        return CheckResult(status="SKIP", message="no baseline complexity available")

    try:
        from radon.complexity import cc_visit

        functions = cc_visit(fixed_code)
        new_complexity = (
            sum(f.complexity for f in functions) / len(functions) if functions else 0.0
        )
        delta = new_complexity - baseline.complexity

        if delta > 2.0:
            return CheckResult(
                status="FAIL",
                message=f"complexity increased by {delta:.2f} "
                f"(baseline {baseline.complexity:.2f} → new {new_complexity:.2f})",
            )
        sign = "+" if delta >= 0 else ""
        label = "worsened" if delta > 0 else "improved or unchanged"
        return CheckResult(
            status="PASS",
            message=f"delta {sign}{delta:.2f} ({label})",
        )
    except Exception as exc:
        return CheckResult(status="SKIP", message=f"complexity check error: {exc}")


# ── Orchestrate All Checks ───────────────────────────────────────────────────


def _build_result(
    file: str,
    overall: str,
    syntax: CheckResult,
    flake: CheckResult | None = None,
    pytest_r: CheckResult | None = None,
    complexity_r: CheckResult | None = None,
    skip_rest: bool = False,
) -> ValidationResult:
    """Assemble a ValidationResult with short-circuit SKIPs."""
    skip = CheckResult(status="SKIP", message="skipped due to prior failure")
    return ValidationResult(
        status=overall,
        file=file,
        details={
            "syntax": syntax,
            "flake8": flake or (skip if skip_rest else skip),
            "pytest": pytest_r or (skip if skip_rest else skip),
            "complexity": complexity_r or (skip if skip_rest else skip),
        },
    )


def _update_context(
    ctx: RunContext, fix: HealResult, result: ValidationResult
) -> None:
    """Append validation result to context."""
    ctx.validations.append(result)
    ctx.last_updated = datetime.utcnow()


async def validate_fix(
    ctx: RunContext, fix: HealResult, store: ContextStore
) -> ValidationResult:
    """Run all 4 checks with short-circuit evaluation and checkpoint."""
    baseline = next((r for r in ctx.complexity if r.file == fix.file), None)

    # Check 1: syntax
    syntax = check_syntax(fix.fixed_code)
    if syntax.status == "FAIL":
        result = _build_result(fix.file, "FAIL", syntax, skip_rest=True)
        _update_context(ctx, fix, result)
        await store.set(ctx.run_id, ctx)
        return result

    # Check 2: flake8
    flake = check_flake8(fix.fixed_code)
    if flake.status == "FAIL":
        result = _build_result(fix.file, "FAIL", syntax, flake, skip_rest=True)
        _update_context(ctx, fix, result)
        await store.set(ctx.run_id, ctx)
        return result

    # Check 3: pytest
    pytest_res = check_pytest(ctx.local_repo_path, fix.file, fix.fixed_code)
    if pytest_res.status == "FAIL":
        result = _build_result(
            fix.file, "FAIL", syntax, flake, pytest_res, skip_rest=True
        )
        _update_context(ctx, fix, result)
        await store.set(ctx.run_id, ctx)
        return result

    # Check 4: complexity regression
    complexity_res = check_complexity(fix.file, fix.fixed_code, baseline)
    overall = "PASS" if complexity_res.status in ("PASS", "SKIP") else "FAIL"
    result = _build_result(fix.file, overall, syntax, flake, pytest_res, complexity_res)
    _update_context(ctx, fix, result)
    await store.set(ctx.run_id, ctx)
    return result
