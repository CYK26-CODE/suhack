"""Global exception hierarchy for Repo Healer.

Every module service raises typed exceptions that inherit from RepoHealerError.
The pipeline orchestrator catches these and writes stage_flags accordingly.
"""

from fastapi import Request
from fastapi.responses import JSONResponse


class RepoHealerError(Exception):
    """Base exception for all Repo Healer errors."""

    status_code: int = 500

    def __init__(self, message: str = "An unexpected error occurred"):
        self.message = message
        super().__init__(self.message)


class AnalysisError(RepoHealerError):
    """PyDriller / git failure during repository analysis."""

    status_code = 502


class ComplexityError(RepoHealerError):
    """Radon parse failure or missing prerequisite stage."""

    status_code = 424


class RiskError(RepoHealerError):
    """Feature matrix shape mismatch or missing prerequisite."""

    status_code = 424


class HealError(RepoHealerError):
    """Grok API failure, max retries exceeded, or response parse error."""

    status_code = 502


class ValidationError(RepoHealerError):
    """Subprocess failure during validation checks."""

    status_code = 500


class PRError(RepoHealerError):
    """GitHub API failure or auth error."""

    status_code = 502


# ── FastAPI Exception Handlers ───────────────────────────────────────────────


async def repo_healer_error_handler(
    request: Request, exc: RepoHealerError
) -> JSONResponse:
    """Global handler for all RepoHealerError subclasses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.__class__.__name__, "detail": exc.message},
    )
