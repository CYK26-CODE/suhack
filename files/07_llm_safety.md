# Module 07 — LLM Safety

## Purpose

LLM Safety is not a standalone agent — it is a set of enforced rules, runtime guards, and
prompt engineering practices that apply to every call made by the Repo Healer Agent. Without
these controls, the LLM will occasionally produce plausible-looking code that subtly alters
logic, introduces new imports, or adds functionality that was never requested.

---

## Tech Stack

| Component       | Tool / Version         | Role                                         |
|-----------------|------------------------|----------------------------------------------|
| Prompt layer    | Anthropic SDK ≥0.25    | System prompt injection, temperature control |
| Output parser   | Python `json` + `re`   | Strip markdown, validate JSON keys           |
| Rate limiter    | `slowapi` ≥0.1.9       | 10 req/min on `/heal/file`                   |
| Retry           | `tenacity` ≥8.3        | Transient API error retry with backoff       |
| Diff validator  | `difflib`              | Detect unexpected semantic changes in patch  |

---

## Enforced Controls

### 1. Temperature = 0.2

Claude's temperature is set to `0.2` (configurable via `LLM_TEMPERATURE` env var, max allowed
value: `0.4`). Low temperature produces deterministic, conservative output. Values above `0.4`
are blocked by a config validator:

```python
@field_validator("llm_temperature")
@classmethod
def temperature_cap(cls, v: float) -> float:
    if v > 0.4:
        raise ValueError("LLM temperature above 0.4 is not permitted — risk of logic mutation")
    return v
```

### 2. Hard-Constraint System Prompt

The system prompt (documented in full in `04_repo_healer_agent.md`) enumerates eight explicit
prohibitions and six permitted improvements. Importantly:

- The prompt ends with: `"Return ONLY valid JSON. Do not include markdown code fences, preamble,
  or explanation outside the JSON object."` — this eliminates prose responses that can't be parsed.
- All permitted improvements are **structural only**: type hints, docstrings, guard clauses,
  private helper extraction, private variable renaming.
- The prompt says: `"If you are uncertain whether a change is safe, DO NOT make it."` — making
  inaction the path of least resistance.

### 3. Output Schema Validation

Every LLM response is validated against a fixed JSON schema before the fixed code is used:

```python
REQUIRED_KEYS = {"fixed_code", "summary"}

def validate_llm_output(parsed: dict, original_source: str) -> None:
    missing = REQUIRED_KEYS - set(parsed.keys())
    if missing:
        raise HealError(f"LLM JSON missing keys: {missing}")
    if not isinstance(parsed["fixed_code"], str):
        raise HealError("fixed_code must be a string")
    if not isinstance(parsed["summary"], str):
        raise HealError("summary must be a string")
    if not parsed["fixed_code"].strip():
        raise HealError("LLM returned empty fixed_code")
    # Unchanged detection
    if parsed["fixed_code"].strip() == original_source.strip():
        if parsed["summary"] != "No safe fix identified.":
            parsed["summary"] = "No safe fix identified."  # correct inconsistent summary
```

### 4. Diff-Based Anomaly Detection

After parsing the LLM response, the service computes a unified diff between the original and
fixed code using `difflib.unified_diff`. If the diff exceeds `MAX_DIFF_LINES` (default: 50),
it is treated as suspicious and the fix is rejected:

```python
import difflib

MAX_DIFF_LINES = 50

def diff_guard(original: str, fixed: str) -> None:
    diff = list(difflib.unified_diff(
        original.splitlines(), fixed.splitlines(), lineterm=""
    ))
    if len(diff) > MAX_DIFF_LINES:
        raise HealError(
            f"LLM produced a diff of {len(diff)} lines — exceeds MAX_DIFF_LINES={MAX_DIFF_LINES}. "
            "This suggests a logic rewrite rather than a targeted fix."
        )
```

### 5. Rate Limiting

The `/heal/file` endpoint is rate-limited to prevent runaway API spend:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/heal/file")
@limiter.limit("10/minute")
async def heal_file_endpoint(request: Request, body: HealRequest, ...):
    ...
```

### 6. Secrets Redaction in Logs

The structured logger filters `ANTHROPIC_API_KEY` and `GITHUB_TOKEN` from all log output:

```python
import structlog

def redact_secrets(logger, method, event_dict):
    for key in ("api_key", "token", "secret", "password"):
        if key in event_dict:
            event_dict[key] = "***REDACTED***"
    return event_dict

structlog.configure(processors=[redact_secrets, ...])
```

---

## LLM Safety Tests: `tests/test_07_llm_safety.py`

```python
import pytest
from app.modules.healer.service import validate_llm_output, diff_guard
from app.core.exceptions import HealError

ORIGINAL = "def add(a, b):\n    return a + b\n"

class TestValidateLLMOutput:

    def test_valid_output_passes(self):
        validate_llm_output({"fixed_code": "def add(a: int, b: int):\n    return a + b\n", "summary": "typed"}, ORIGINAL)

    def test_missing_fixed_code_raises(self):
        with pytest.raises(HealError, match="missing keys"):
            validate_llm_output({"summary": "ok"}, ORIGINAL)

    def test_empty_fixed_code_raises(self):
        with pytest.raises(HealError, match="empty fixed_code"):
            validate_llm_output({"fixed_code": "", "summary": "ok"}, ORIGINAL)

    def test_unchanged_code_corrects_summary(self):
        result = {"fixed_code": ORIGINAL, "summary": "I made it better"}
        validate_llm_output(result, ORIGINAL)
        assert result["summary"] == "No safe fix identified."

    def test_non_string_fixed_code_raises(self):
        with pytest.raises(HealError):
            validate_llm_output({"fixed_code": 42, "summary": "ok"}, ORIGINAL)


class TestDiffGuard:

    def test_small_diff_passes(self):
        fixed = "def add(a: int, b: int) -> int:\n    return a + b\n"
        diff_guard(ORIGINAL, fixed)  # should not raise

    def test_large_diff_raises(self):
        large_rewrite = "\n".join(f"line_{i} = {i}" for i in range(200))
        with pytest.raises(HealError, match="exceeds MAX_DIFF_LINES"):
            diff_guard(ORIGINAL, large_rewrite)

    def test_identical_code_no_diff(self):
        diff_guard(ORIGINAL, ORIGINAL)  # zero diff lines — should not raise


class TestTemperatureCap:

    def test_temperature_above_04_rejected(self):
        import pytest
        from app.core.config import Settings
        with pytest.raises(Exception, match="temperature"):
            Settings(
                anthropic_api_key="sk-test",
                github_token="ghp_test",
                target_repo_url="https://github.com/test/repo",
                llm_temperature=0.9,
            )

    def test_temperature_04_accepted(self):
        from app.core.config import Settings
        s = Settings(
            anthropic_api_key="sk-test",
            github_token="ghp_test",
            target_repo_url="https://github.com/test/repo",
            llm_temperature=0.4,
        )
        assert s.llm_temperature == 0.4
```
