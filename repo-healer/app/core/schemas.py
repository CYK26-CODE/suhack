"""Shared Pydantic models used across all modules.

RunContext is the single source of truth for the pipeline run state.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ── Enums ────────────────────────────────────────────────────────────────────


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class RiskLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ── Analyzer Schemas ─────────────────────────────────────────────────────────


class FileRecord(BaseModel):
    file: str
    total_churn: int = 0
    commit_count: int = 0
    contributors: int = 0
    last_modified: datetime
    extension: str = ""
    is_deleted: bool = False

    @field_validator("total_churn", "commit_count", "contributors")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("metric must be non-negative")
        return v


class AnalysisResult(BaseModel):
    run_id: str
    file_count: int
    analysis: list[FileRecord]


# ── Complexity Schemas ───────────────────────────────────────────────────────


class ComplexityRecord(BaseModel):
    file: str
    complexity: float  # average cyclomatic; -1.0 if parse error
    maintainability: float  # Radon MI (0-100); -1.0 if parse error
    function_count: int = 0
    parse_error: bool = False

    @field_validator("complexity", "maintainability")
    @classmethod
    def sentinel_or_valid(cls, v: float, info: Any) -> float:
        if v == -1.0:
            return v  # sentinel allowed
        if info.field_name == "complexity" and v < 0:
            raise ValueError("complexity must be >= 0")
        if info.field_name == "maintainability" and not (-1.0 <= v <= 100.0):
            raise ValueError("maintainability must be in [0, 100] or -1.0 sentinel")
        return v


class ComplexityResult(BaseModel):
    run_id: str
    complexity: list[ComplexityRecord]


# -- Risk Schemas --

class RiskRecord(BaseModel):
    file: str
    risk_score: float
    risk_level: RiskLevel
    features: dict[str, Any] | None = None


class RiskResult(BaseModel):
    run_id: str
    risk: list[RiskRecord]
    high_risk_count: int
    model_version: str = ""


class FeatureContribution(BaseModel):
    """Single feature's contribution to a file's risk score."""
    name: str                # e.g. "total_churn"
    label: str               # e.g. "Code Churn"
    raw_value: float         # original metric value
    z_score: float           # standard deviations from mean
    contribution: float      # 0-1 normalised weight for this feature
    severity: str            # "normal", "elevated", "high", "critical"


class RiskExplanation(BaseModel):
    """Per-file explanation of why it was flagged."""
    file: str
    risk_score: float
    risk_level: RiskLevel
    reasons: list[str]                       # human-readable sentences
    feature_contributions: list[FeatureContribution]
    top_driver: str                          # the single biggest factor


class ExplainabilityReport(BaseModel):
    """Full explainability report for a pipeline run."""
    run_id: str
    repo_url: str
    total_files: int
    high_risk_count: int
    risk_threshold: float
    methodology: str
    explanations: list[RiskExplanation]



# ── Healer Schemas ───────────────────────────────────────────────────────────


class HealRequest(BaseModel):
    run_id: str
    file: str
    source_code: str
    risk_summary: str = "HIGH"


class HealResult(BaseModel):
    run_id: str
    file: str
    fixed_code: str
    summary: str
    changed: bool = False
    no_fix_reason: str | None = None
    attempt: int = 1


# ── Validation Schemas ───────────────────────────────────────────────────────


class CheckResult(BaseModel):
    status: str  # "PASS", "FAIL", "SKIP"
    message: str


class ValidationResult(BaseModel):
    status: str  # "PASS" or "FAIL"
    file: str
    details: dict[str, CheckResult] = {}


# ── PR Schemas ───────────────────────────────────────────────────────────────


class PRResult(BaseModel):
    pr_url: str
    branch: str
    files_changed: int
    pr_number: int
    already_existed: bool = False


# ── RunContext — The Central Pipeline State ──────────────────────────────────


class RunContext(BaseModel):
    run_id: str = Field(
        default_factory=lambda: f"{datetime.utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    )
    repo_url: str
    branch: str = "main"
    local_repo_path: str = ""  # set by analyzer after clone
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    last_commit_sha: Optional[str] = None

    # Stage outputs — populated as pipeline progresses
    analysis: list[FileRecord] = Field(default_factory=list)
    complexity: list[ComplexityRecord] = Field(default_factory=list)
    risk: list[RiskRecord] = Field(default_factory=list)
    fixes: list[HealResult] = Field(default_factory=list)
    validations: list[ValidationResult] = Field(default_factory=list)
    pr_url: Optional[str] = None
    pr_branch: Optional[str] = None

    # Stage status flags
    stage_flags: dict[str, StageStatus] = Field(default_factory=dict)

    def is_stage_complete(self, stage: str) -> bool:
        return self.stage_flags.get(stage) == StageStatus.COMPLETE

    def mark_stage(self, stage: str, status: StageStatus) -> None:
        self.stage_flags[stage] = status
        self.last_updated = datetime.utcnow()
