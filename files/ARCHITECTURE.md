# Repo Healer — System Architecture

## Architectural Style: Modular Monolith

Repo Healer is structured as a **modular monolith** — a single deployable process composed of
strongly-bounded internal modules that communicate exclusively through well-defined in-process
interfaces. Each module owns its own data models, service layer, router, and test suite. The
modules share nothing except a narrow set of core primitives (settings, context store, Pydantic
schemas) that live in `app/core/`.

This pattern is chosen deliberately over microservices for v1:

- One process = one deployment, zero network hops between agents.
- Bounded modules = clean cut lines if a future split to services is ever needed.
- Shared in-process state store removes the need for a message broker at this scale.
- Single `pytest` run covers every module end-to-end with session-scoped fixtures that
  propagate live context forward, preventing context loss between test stages.

---

## Top-Level Directory Layout

```
repo-healer/
├── app/
│   ├── main.py                    # FastAPI app factory, mounts all routers
│   ├── core/
│   │   ├── config.py              # Pydantic BaseSettings — single env var source of truth
│   │   ├── context_store.py       # In-process / Redis context memory (ContextStore)
│   │   ├── exceptions.py          # Global HTTP exception handlers
│   │   ├── logging.py             # Structured JSON logger (structlog)
│   │   └── schemas.py             # Shared Pydantic models (RunContext, FileRecord, etc.)
│   │
│   ├── modules/
│   │   ├── analyzer/              # Module 1 — Repo Analyzer
│   │   │   ├── router.py          #   GET /analyze/repo
│   │   │   ├── service.py         #   PyDriller logic
│   │   │   ├── schemas.py         #   FileRecord, AnalysisResult
│   │   │   └── exceptions.py
│   │   │
│   │   ├── complexity/            # Module 2 — Complexity Agent
│   │   │   ├── router.py          #   POST /analyze/complexity
│   │   │   ├── service.py         #   Radon cc_visit + mi_visit
│   │   │   └── schemas.py         #   ComplexityRecord
│   │   │
│   │   ├── risk/                  # Module 3 — Risk Prediction Agent
│   │   │   ├── router.py          #   POST /predict/risk
│   │   │   ├── service.py         #   IsolationForest + score normalisation
│   │   │   ├── model_store.py     #   joblib serialise / load trained model
│   │   │   └── schemas.py         #   RiskRecord, RiskLevel enum
│   │   │
│   │   ├── healer/                # Module 4 — Repo Healer Agent
│   │   │   ├── router.py          #   POST /heal/file
│   │   │   ├── service.py         #   Anthropic Claude API calls
│   │   │   ├── prompt.py          #   System prompt template
│   │   │   └── schemas.py         #   HealRequest, HealResult
│   │   │
│   │   ├── validation/            # Module 5 — Validation Agent
│   │   │   ├── router.py          #   POST /validate/fix
│   │   │   ├── service.py         #   ast.parse + flake8 + pytest + radon gate
│   │   │   └── schemas.py         #   ValidationResult, ValidationDetail
│   │   │
│   │   └── pr/                    # Module 6 — PR Agent
│   │       ├── router.py          #   POST /pr/create
│   │       ├── service.py         #   PyGitHub branch/commit/push/open PR
│   │       └── schemas.py         #   PRResult
│   │
│   └── pipeline/
│       ├── router.py              # POST /pipeline/run  (orchestrates all 6 modules)
│       └── orchestrator.py        # Sequential pipeline runner with context checkpoints
│
├── tests/
│   ├── conftest.py                # Session-scoped fixtures + context propagation
│   ├── test_01_analyzer.py
│   ├── test_02_complexity.py
│   ├── test_03_risk.py
│   ├── test_04_healer.py
│   ├── test_05_validation.py
│   ├── test_06_pr.py
│   └── test_07_pipeline_e2e.py
│
├── frontend/                      # Next.js 14 app (separate npm project)
│   ├── app/
│   │   ├── page.tsx               # Dashboard
│   │   ├── heatmap/page.tsx
│   │   └── file/[...path]/page.tsx
│   └── package.json
│
├── scripts/
│   └── seed_test_repo.sh          # Creates a local git repo fixture for tests
│
├── .env.example
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml                 # ruff + mypy + pytest config
├── Makefile
├── Dockerfile
└── docker-compose.yml
```

---

## Module Dependency Graph

```
                     ┌────────────────────────────────────┐
                     │           app/core/                 │
                     │  config · context_store · schemas   │
                     └──────────────┬─────────────────────┘
                                    │ imported by all modules
              ┌─────────────────────┼──────────────────────────┐
              │                     │                           │
      ┌───────▼──────┐   ┌──────────▼──────┐   ┌──────────────▼────┐
      │   analyzer   │   │   complexity     │   │      risk          │
      │   service    │──▶│   service        │──▶│      service       │
      └──────────────┘   └─────────────────┘   └──────────┬────────┘
                                                            │
                                               ┌────────────▼────────┐
                                               │     healer           │
                                               │     service          │
                                               └────────────┬────────┘
                                                            │
                                               ┌────────────▼────────┐
                                               │    validation         │
                                               │    service            │
                                               └────────────┬────────┘
                                                            │
                                               ┌────────────▼────────┐
                                               │      pr              │
                                               │      service         │
                                               └─────────────────────┘
```

Modules call each other **only** via their service layer functions — never by importing another
module's router or internal model. The `pipeline/orchestrator.py` is the only place that chains
modules sequentially.

---

## Core: Context Store

`app/core/context_store.py` is the single shared state bus. It is a thin wrapper that stores a
`RunContext` Pydantic model in either:

- **In-memory dict** (default, development / test): `_store: dict[str, RunContext]`
- **Redis** (production): `redis.asyncio.Redis` with JSON serialisation via `orjson`

All six module services read/write to the context store via the `ContextStore` interface:

```python
class ContextStore(Protocol):
    async def get(self, run_id: str) -> RunContext | None: ...
    async def set(self, run_id: str, ctx: RunContext) -> None: ...
    async def delete(self, run_id: str) -> None: ...
```

The `RunContext` model carries the full pipeline state:

```python
class RunContext(BaseModel):
    run_id:          str
    repo_url:        str
    branch:          str = "main"
    started_at:      datetime
    last_updated:    datetime
    last_commit_sha: str | None = None

    # Stage outputs — populated as pipeline progresses
    analysis:        list[FileRecord]        = []
    complexity:      list[ComplexityRecord]  = []
    risk:            list[RiskRecord]        = []
    fixes:           list[HealResult]        = []
    validations:     list[ValidationResult]  = []
    pr_url:          str | None              = None
    pr_branch:       str | None              = None

    # Stage status flags
    stage_flags:     dict[str, StageStatus]  = {}
```

The context is **checkpointed** after every stage. If the pipeline crashes mid-run, it can
resume from the last successful checkpoint rather than restarting from scratch.

---

## Core: Config

All configuration is centralised in `app/core/config.py` using `pydantic-settings`:

```python
class Settings(BaseSettings):
    # API
    anthropic_api_key: SecretStr
    github_token:      SecretStr
    target_repo_url:   str
    target_branch:     str         = "main"

    # Pipeline tuning
    risk_threshold:    float       = 0.7
    max_heal_retries:  int         = 2
    file_extensions:   list[str]   = [".py"]
    llm_temperature:   float       = 0.2
    llm_model:         str         = "claude-opus-4-6"

    # Infrastructure
    redis_url:         str | None  = None
    log_level:         str         = "INFO"

    model_config = SettingsConfigDict(env_file=".env", secrets_dir="/run/secrets")
```

No module may read `os.environ` directly. All configuration flows through `get_settings()` which
is a cached `lru_cache` singleton.

---

## Core: Logging

`app/core/logging.py` configures `structlog` for structured JSON output. Every log event
carries `run_id`, `module`, and `stage` fields automatically via `contextvars`. This allows
log aggregators (Loki, Datadog) to filter the full trace of a single pipeline run.

```python
# Usage in any service
log = structlog.get_logger(__name__)
log.info("stage_complete", run_id=run_id, files_processed=42, risk_high=3)
```

---

## Pipeline Orchestrator

`app/pipeline/orchestrator.py` is the sequential runner. It:

1. Creates a `RunContext` and checkpoints it to the store.
2. Calls `analyzer.service.run_analysis(ctx)` → checkpoints.
3. Calls `complexity.service.run_complexity(ctx)` → checkpoints.
4. Calls `risk.service.run_risk(ctx)` → checkpoints.
5. For each HIGH-risk file: calls `healer.service.heal_file(ctx, file)` → checkpoints after each.
6. For each healed file: calls `validation.service.validate_fix(ctx, fix)` → checkpoints.
7. Calls `pr.service.create_pr(ctx)` → checkpoints final state.

Each call passes the live `RunContext` object. If any stage raises `StageError`, the orchestrator
marks the stage as `FAILED` in `ctx.stage_flags`, checkpoints, and either retries (healer,
up to `MAX_HEAL_RETRIES`) or aborts with a partial result.

---

## API Surface

| Method | Path                  | Module      | Description                           |
|--------|-----------------------|-------------|---------------------------------------|
| GET    | /analyze/repo         | analyzer    | Analyse repo commits (query params)   |
| POST   | /analyze/complexity   | complexity  | Compute Radon complexity              |
| POST   | /predict/risk         | risk        | Run IsolationForest risk prediction   |
| POST   | /heal/file            | healer      | LLM-driven code healing               |
| POST   | /validate/fix         | validation  | pytest + flake8 + radon gate          |
| POST   | /pr/create            | pr          | Create GitHub Pull Request            |
| POST   | /pipeline/run         | pipeline    | Run full end-to-end pipeline          |
| GET    | /pipeline/{run_id}    | pipeline    | Poll pipeline status                  |
| DELETE | /context/{run_id}     | core        | Purge a run's context from store      |

All routes are prefixed with `/api/v1`.

---

## Tech Stack Matrix

| Layer              | Library / Tool          | Version  | Purpose                                  |
|--------------------|-------------------------|----------|------------------------------------------|
| Web framework      | FastAPI                 | ≥0.111   | Async API, OpenAPI docs, DI              |
| ASGI server        | uvicorn[standard]       | ≥0.29    | Production-grade ASGI                    |
| Data validation    | pydantic v2             | ≥2.7     | Request/response schemas, Settings       |
| Git mining         | pydriller               | ≥2.5     | Commit traversal, churn extraction       |
| Static analysis    | radon                   | ≥6.0     | Cyclomatic complexity + MI               |
| Linting            | flake8                  | ≥7.0     | Code style validation in Validation Agent|
| ML                 | scikit-learn            | ≥1.4     | IsolationForest, StandardScaler          |
| Model persistence  | joblib                  | ≥1.3     | Serialise/load trained IF model          |
| LLM               | anthropic               | ≥0.25    | Claude API for code healing              |
| GitHub API         | PyGitHub                | ≥2.3     | Branch/commit/push/PR                    |
| Testing            | pytest                  | ≥8.0     | Unit + integration tests                 |
| Test HTTP          | httpx                   | ≥0.27    | Async HTTP client for FastAPI TestClient |
| Mocking            | pytest-mock             | ≥3.12    | Service mocks, LLM stub                  |
| Coverage           | pytest-cov              | ≥5.0     | Coverage reporting                        |
| Logging            | structlog               | ≥24.1    | Structured JSON logs                     |
| Cache/state        | redis[asyncio]          | ≥5.0     | Optional persistent context store        |
| Serialisation      | orjson                  | ≥3.10    | Fast JSON for context store              |
| Type checking      | mypy                    | ≥1.10    | Static type safety                       |
| Linting (code)     | ruff                    | ≥0.4     | Fast linter/formatter                    |
| Frontend           | Next.js 14              | 14.x     | App Router, RSC                          |
| Frontend styles    | Tailwind CSS            | ≥3.4     | Utility-first CSS                        |
| Frontend diff      | react-diff-viewer-continued | latest | Side-by-side diff in File Viewer     |
| Containerisation   | Docker + Compose        | latest   | Local dev + CI                           |

---

## Data Flow: Full Pipeline Run

```
POST /api/v1/pipeline/run
  { "repo_url": "https://github.com/org/repo", "branch": "main" }

  1.  Orchestrator creates RunContext(run_id="20241120-143200", ...)
      └─ checkpoint → context_store.set(run_id, ctx)

  2.  analyzer.service.run_analysis(ctx)
      └─ PyDriller traverses commits → ctx.analysis = [FileRecord, ...]
      └─ checkpoint

  3.  complexity.service.run_complexity(ctx)
      └─ Radon cc_visit + mi_visit on ctx.analysis files
      └─ ctx.complexity = [ComplexityRecord, ...]
      └─ checkpoint

  4.  risk.service.run_risk(ctx)
      └─ merge analysis + complexity → feature matrix
      └─ IsolationForest.fit_predict → raw scores
      └─ normalise scores to [0,1] via (score + 1) / 2
      └─ threshold → RiskLevel (HIGH ≥ 0.7, MEDIUM ≥ 0.4, LOW < 0.4)
      └─ ctx.risk = [RiskRecord, ...]
      └─ checkpoint

  5.  For each file where risk_level == HIGH:
      └─ healer.service.heal_file(ctx, file)  [retries ≤ MAX_HEAL_RETRIES]
          └─ Anthropic claude-opus-4-6, temp=0.2, strict system prompt
          └─ returns HealResult(fixed_code, summary)
          └─ checkpoint after each healed file

  6.  For each HealResult:
      └─ validation.service.validate_fix(ctx, fix)
          └─ ast.parse → syntax check
          └─ flake8 subprocess → style check
          └─ pytest subprocess → regression check
          └─ radon cc_visit → complexity delta check
          └─ if any FAIL: discard fix, log, try next retry
          └─ checkpoint after each validation

  7.  pr.service.create_pr(ctx)
      └─ check for existing open PR from same branch (idempotency)
      └─ PyGitHub: create branch → commit each validated fix → push → open PR
      └─ ctx.pr_url = "https://github.com/org/repo/pull/42"
      └─ final checkpoint

  Response: { "run_id": "...", "pr_url": "...", "files_healed": 3 }
```

---

## Context Checkpoint Strategy

Checkpoints are written to the context store **after every meaningful state mutation**. The
checkpoint interval is as granular as per-file for the healer and validation stages, because
those are the most expensive (LLM API calls + subprocess test runs). This means:

- If the server crashes mid-heal, the next run can skip already-healed files.
- The frontend can poll `GET /api/v1/pipeline/{run_id}` and show live progress.
- Operators can inspect any partial run state without re-running the pipeline.

Without Redis, the in-memory store is lost on restart. The default development config uses
in-memory. For any production or CI environment, `REDIS_URL` must be set.

---

## Error Handling Strategy

Every module service raises typed exceptions that inherit from `app/core/exceptions.py`:

```
RepoHealerError (base)
  ├── AnalysisError      — PyDriller / git failure
  ├── ComplexityError    — Radon parse failure
  ├── RiskError          — feature matrix shape mismatch
  ├── HealError          — Anthropic API failure or max retries exceeded
  ├── ValidationError    — subprocess failure or flake8 error
  └── PRError            — GitHub API failure or auth error
```

The pipeline orchestrator catches these, writes `stage_flags[stage] = FAILED` into the context,
and continues where possible (non-fatal) or aborts (fatal). All errors are logged with
`structlog` at ERROR level with full context.

---

## Deployment

### Docker (single container, development)

```bash
docker build -t repo-healer .
docker run -p 8000:8000 --env-file .env repo-healer
```

### Docker Compose (with Redis)

```bash
docker-compose up
```

`docker-compose.yml` spins up:

- `api` — the FastAPI app
- `redis` — context store
- `frontend` — Next.js dev server

### CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every PR:

1. `ruff check .` + `mypy app/`
2. `pytest tests/ --cov=app --cov-fail-under=80`
3. Docker build smoke test

---

## Security Considerations

- `ANTHROPIC_API_KEY` and `GITHUB_TOKEN` are never logged or included in API responses.
- The Healer Agent runs with `temperature=0.2` and a hard system prompt that disallows
  external network calls, file system access, or shell commands in generated code.
- The Validation Agent runs `pytest` and `flake8` as subprocesses in a **temporary directory**
  with the fixed file. It does not execute the fixed code in the main process.
- PR branches are namespaced `repo-healer/<run_id>` — they never push to `main` directly.
- Rate limiting is applied at the `/heal/file` endpoint (10 req/min) to prevent runaway
  LLM API spend.
