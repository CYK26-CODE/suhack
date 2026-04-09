# Module 04 — Repo Healer Agent

## Purpose

The Repo Healer Agent is the **fourth stage** of the pipeline and the only stage that calls an
external LLM. For each file flagged as HIGH-risk by the Risk Agent, it submits the source code
to Claude with a carefully constrained system prompt and receives a patched version with a
plain-English summary of changes.

The core design contract is: **no logic changes, only safe structural improvements.**

---

## Tech Stack

| Dependency    | Version | Role                                                        |
|---------------|---------|-------------------------------------------------------------|
| FastAPI       | ≥0.111  | HTTP router                                                 |
| Pydantic v2   | ≥2.7    | `HealRequest`, `HealResult` schemas                         |
| anthropic     | ≥0.25   | Anthropic Python SDK — messages API                         |
| tenacity      | ≥8.3    | Retry logic for transient API failures                      |
| structlog     | ≥24.1   | Structured logging                                          |
| pytest        | ≥8.0    | Test runner                                                 |
| pytest-mock   | ≥3.12   | Stub Anthropic API responses                                |
| respx         | ≥0.21   | HTTP-level mock for Anthropic SDK                           |

---

## API Endpoint

### `POST /api/v1/heal/file`

**Request Body:**

```json
{
  "run_id": "20241120-143200",
  "file": "src/utils.py",
  "source_code": "<raw file contents>",
  "risk_summary": "HIGH — churn 142, complexity 8.4, maintainability 52.3"
}
```

**Success Response — `200 OK`:**

```json
{
  "run_id": "20241120-143200",
  "file": "src/utils.py",
  "fixed_code": "<patched source>",
  "summary": "Extracted nested loop into helper `_process_batch`. Added missing None-check on line 47. Added type hints to 3 functions.",
  "changed": true,
  "no_fix_reason": null
}
```

**If no safe fix is identified:**

```json
{
  "fixed_code": "<original source unchanged>",
  "summary": "No safe fix identified.",
  "changed": false,
  "no_fix_reason": "All complexity is load-bearing business logic with no safe refactor path."
}
```

**Error Responses:**

| Status | Condition                                        |
|--------|--------------------------------------------------|
| 422    | Missing required fields                          |
| 502    | Anthropic API unreachable                        |
| 429    | Anthropic API rate limit exceeded                |
| 503    | Max retries exceeded                             |

---

## System Prompt: `app/modules/healer/prompt.py`

The system prompt is the primary enforcement mechanism for the "no logic change" rule. Low
temperature alone is not sufficient — without explicit instructions, Claude will make
semantic improvements that feel correct but alter behaviour.

```python
SYSTEM_PROMPT = """\
You are a code refactoring assistant. Your task is to improve the structure and readability
of a Python source file WITHOUT changing its observable behaviour.

## HARD CONSTRAINTS — VIOLATION IS UNACCEPTABLE

1. Do NOT change function signatures, parameter names, or return types.
2. Do NOT alter the logic, algorithm, or control flow of any function.
3. Do NOT add new imports that are not in the original file.
4. Do NOT remove any existing functionality, even if unused.
5. Do NOT add new public functions, classes, or methods.
6. Do NOT make calls to external services, databases, or the file system.
7. Do NOT write shell commands or subprocess calls.
8. If you are uncertain whether a change is safe, DO NOT make it.

## PERMITTED IMPROVEMENTS

- Add or improve type hints on existing function signatures.
- Add or improve docstrings on existing functions/classes.
- Extract a repeated code block (3+ identical occurrences) into a private helper function.
- Add a missing None-check or empty-list-check that prevents an obvious AttributeError or
  IndexError that would occur on the most common unhappy path.
- Rename a single-letter or ambiguous private variable to a descriptive name (private only —
  do not rename public API identifiers).
- Simplify an overly-nested condition that can be flattened with an early return (guard clause)
  without changing the set of reachable states.

## OUTPUT FORMAT

Return a JSON object with exactly two keys:
- "fixed_code": the complete modified (or unchanged) Python source as a string.
- "summary": a one-paragraph plain-English description of every change made, or the string
  "No safe fix identified." if no changes were made.

Return ONLY valid JSON. Do not include markdown code fences, preamble, or explanation outside
the JSON object.
"""
```

### Why This Prompt Design Works

The system prompt uses a two-section structure: hard constraints (enumerated prohibitions) and
permitted improvements (enumerated allowances). This is more robust than a single instruction
like "only safe fixes" because:

1. Claude cannot hallucinate an action that isn't on the permitted list.
2. The explicit JSON output format prevents prose responses that can't be parsed.
3. The "If uncertain, do not" rule makes conservatism the path of least resistance.

---

## Retry Logic

Transient Anthropic API failures (timeouts, 529 overload) are retried using `tenacity`:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import anthropic

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((anthropic.APITimeoutError, anthropic.InternalServerError)),
    reraise=True,
)
def call_llm(source_code: str, risk_summary: str) -> dict:
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=settings.llm_model,
        max_tokens=4096,
        temperature=settings.llm_temperature,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Risk context: {risk_summary}\n\nSource code:\n```python\n{source_code}\n```",
            }
        ],
    )
    raw = message.content[0].text
    return _parse_llm_response(raw)
```

### Response Parsing

```python
import json, re

def _parse_llm_response(raw: str) -> dict:
    # Strip accidental markdown fences
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise HealError(f"LLM returned non-JSON response: {exc}\nRaw: {raw[:200]}")
    if "fixed_code" not in parsed or "summary" not in parsed:
        raise HealError(f"LLM JSON missing required keys: {list(parsed.keys())}")
    return parsed
```

---

## Context Store Integration

```python
async def heal_file(ctx: RunContext, file: str, store: ContextStore) -> HealResult:
    source_code = await read_source(ctx.local_repo_path, file)
    risk_rec = next((r for r in ctx.risk if r.file == file), None)
    risk_summary = f"{risk_rec.risk_level} — score {risk_rec.risk_score}" if risk_rec else "HIGH"

    for attempt in range(settings.max_heal_retries + 1):
        try:
            result_dict = call_llm(source_code, risk_summary)
            fixed_code = result_dict["fixed_code"]
            summary = result_dict["summary"]
            changed = fixed_code.strip() != source_code.strip()
            heal_result = HealResult(
                run_id=ctx.run_id, file=file,
                fixed_code=fixed_code, summary=summary, changed=changed,
                attempt=attempt + 1,
            )
            ctx.fixes.append(heal_result)
            ctx.last_updated = datetime.utcnow()
            await store.set(ctx.run_id, ctx)  # checkpoint after each file
            return heal_result
        except HealError as exc:
            if attempt == settings.max_heal_retries:
                raise
            log.warning("heal_retry", file=file, attempt=attempt + 1, error=str(exc))
```

---

## Testing Module: `tests/test_04_healer.py`

### LLM Stubbing Strategy

Tests must never make real Anthropic API calls. Two strategies are used:

1. **`pytest-mock` patch on `call_llm`**: Fast unit tests that never touch the SDK.
2. **`respx` HTTP mock**: Integration tests that stub the actual HTTPS request for more
   realistic coverage of the parsing and retry logic.

```python
import pytest
import json
from fastapi.testclient import TestClient
from app.main import app
from app.modules.healer.service import call_llm, _parse_llm_response, heal_file
from app.modules.healer.schemas import HealResult
from app.core.exceptions import HealError

client = TestClient(app)

VALID_LLM_RESPONSE = json.dumps({
    "fixed_code": "def add(a: int, b: int) -> int:\n    return a + b\n",
    "summary": "Added type hints to `add` function.",
})

NO_FIX_RESPONSE = json.dumps({
    "fixed_code": "def add(a, b):\n    return a + b\n",
    "summary": "No safe fix identified.",
})

BROKEN_JSON_RESPONSE = "Here's the fixed code: ```python\ndef add(a, b): return a + b```"

MISSING_KEYS_RESPONSE = json.dumps({"code": "...", "note": "oops"})


# ── Unit Tests: Response Parsing ─────────────────────────────────────────────

class TestParseLLMResponse:

    def test_valid_json_parsed_correctly(self):
        result = _parse_llm_response(VALID_LLM_RESPONSE)
        assert "fixed_code" in result
        assert "summary" in result

    def test_markdown_fences_stripped(self):
        fenced = "```json\n" + VALID_LLM_RESPONSE + "\n```"
        result = _parse_llm_response(fenced)
        assert result["summary"] == "Added type hints to `add` function."

    def test_non_json_raises_heal_error(self):
        with pytest.raises(HealError, match="non-JSON"):
            _parse_llm_response(BROKEN_JSON_RESPONSE)

    def test_missing_keys_raises_heal_error(self):
        with pytest.raises(HealError, match="missing required keys"):
            _parse_llm_response(MISSING_KEYS_RESPONSE)

    def test_whitespace_trimmed_before_parse(self):
        padded = "  \n  " + VALID_LLM_RESPONSE + "  \n  "
        result = _parse_llm_response(padded)
        assert result["fixed_code"] != ""

    def test_extra_keys_in_response_tolerated(self):
        extra = json.dumps({"fixed_code": "x=1\n", "summary": "minor", "debug": "ignored"})
        result = _parse_llm_response(extra)
        assert result["fixed_code"] == "x=1\n"


# ── Unit Tests: call_llm ──────────────────────────────────────────────────────

class TestCallLLM:

    def test_returns_dict_on_success(self, mocker):
        mocker.patch(
            "app.modules.healer.service.anthropic.Anthropic",
            return_value=_make_mock_anthropic(VALID_LLM_RESPONSE),
        )
        result = call_llm("def add(a, b): return a + b", "HIGH")
        assert isinstance(result, dict)
        assert "fixed_code" in result

    def test_retries_on_timeout(self, mocker):
        import anthropic
        mock_client = _make_mock_anthropic_with_error(
            anthropic.APITimeoutError, VALID_LLM_RESPONSE, fail_count=2
        )
        mocker.patch("app.modules.healer.service.anthropic.Anthropic", return_value=mock_client)
        result = call_llm("def f(): pass", "HIGH")
        assert result["fixed_code"] != ""
        assert mock_client.messages.create.call_count == 3  # 2 failures + 1 success

    def test_raises_after_max_retries(self, mocker):
        import anthropic
        mock_client = _make_mock_anthropic_always_error(anthropic.APITimeoutError)
        mocker.patch("app.modules.healer.service.anthropic.Anthropic", return_value=mock_client)
        with pytest.raises(anthropic.APITimeoutError):
            call_llm("def f(): pass", "HIGH")

    def test_low_temperature_passed_to_api(self, mocker):
        mock_create = mocker.patch(
            "app.modules.healer.service.anthropic.Anthropic",
            return_value=_make_mock_anthropic(VALID_LLM_RESPONSE),
        )
        call_llm("def f(): pass", "HIGH")
        call_kwargs = mock_create.return_value.messages.create.call_args.kwargs
        assert call_kwargs["temperature"] <= 0.3

    def test_system_prompt_included(self, mocker):
        mock_create = mocker.patch(
            "app.modules.healer.service.anthropic.Anthropic",
            return_value=_make_mock_anthropic(VALID_LLM_RESPONSE),
        )
        call_llm("def f(): pass", "HIGH")
        call_kwargs = mock_create.return_value.messages.create.call_args.kwargs
        assert "HARD CONSTRAINTS" in call_kwargs["system"]

    def test_no_fix_response_returns_unchanged_code(self, mocker):
        mocker.patch(
            "app.modules.healer.service.anthropic.Anthropic",
            return_value=_make_mock_anthropic(NO_FIX_RESPONSE),
        )
        result = call_llm("def add(a, b):\n    return a + b\n", "HIGH")
        assert result["summary"] == "No safe fix identified."


# ── Integration: heal_file ────────────────────────────────────────────────────

class TestHealFile:

    @pytest.mark.asyncio
    async def test_healed_file_appended_to_context(
        self, run_context, context_store, mocker
    ):
        mocker.patch(
            "app.modules.healer.service.call_llm",
            return_value={"fixed_code": "def add(a: int, b: int) -> int:\n    return a + b\n",
                          "summary": "Added type hints."},
        )
        mocker.patch("app.modules.healer.service.read_source", return_value="def add(a, b):\n    return a + b\n")
        file = run_context.risk[0].file if run_context.risk else "src/utils.py"
        result = await heal_file(run_context, file, context_store)
        assert result.changed is True
        assert any(f.file == file for f in run_context.fixes)

    @pytest.mark.asyncio
    async def test_checkpoint_written_after_each_file(
        self, run_context, context_store, mocker
    ):
        mocker.patch(
            "app.modules.healer.service.call_llm",
            return_value={"fixed_code": "x = 1\n", "summary": "minor"},
        )
        mocker.patch("app.modules.healer.service.read_source", return_value="x = 1\n")
        await heal_file(run_context, "src/x.py", context_store)
        stored = await context_store.get(run_context.run_id)
        assert any(f.file == "src/x.py" for f in stored.fixes)

    @pytest.mark.asyncio
    async def test_heal_error_retried_up_to_max(
        self, run_context, context_store, mocker
    ):
        call_count = {"n": 0}
        def fail_twice(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise HealError("transient")
            return {"fixed_code": "x=1\n", "summary": "ok"}
        mocker.patch("app.modules.healer.service.call_llm", side_effect=fail_twice)
        mocker.patch("app.modules.healer.service.read_source", return_value="x=1\n")
        result = await heal_file(run_context, "src/x.py", context_store)
        assert result.changed is False or result.fixed_code == "x=1\n"
        assert call_count["n"] == 3


# ── Router Integration Tests ──────────────────────────────────────────────────

class TestHealerRouter:

    def test_missing_fields_returns_422(self):
        resp = client.post("/api/v1/heal/file", json={})
        assert resp.status_code == 422

    def test_valid_request_returns_200(self, mocker):
        mocker.patch(
            "app.modules.healer.service.call_llm",
            return_value={"fixed_code": "def f(): pass\n", "summary": "ok"},
        )
        resp = client.post("/api/v1/heal/file", json={
            "run_id": "test-run",
            "file": "src/f.py",
            "source_code": "def f(): pass\n",
            "risk_summary": "HIGH",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "fixed_code" in body
        assert "summary" in body
        assert "changed" in body

    def test_anthropic_502_on_api_failure(self, mocker):
        import anthropic
        mocker.patch(
            "app.modules.healer.service.call_llm",
            side_effect=anthropic.APIConnectionError(request=None),
        )
        resp = client.post("/api/v1/heal/file", json={
            "run_id": "test-run",
            "file": "src/f.py",
            "source_code": "def f(): pass\n",
            "risk_summary": "HIGH",
        })
        assert resp.status_code == 502


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_anthropic(response_text: str):
    from unittest.mock import MagicMock
    content = MagicMock()
    content.text = response_text
    message = MagicMock()
    message.content = [content]
    client = MagicMock()
    client.messages.create.return_value = message
    return client

def _make_mock_anthropic_with_error(exc_class, success_text, fail_count=1):
    from unittest.mock import MagicMock
    content = MagicMock(); content.text = success_text
    message = MagicMock(); message.content = [content]
    client = MagicMock()
    calls = {"n": 0}
    def create(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= fail_count:
            raise exc_class(message="transient")
        return message
    client.messages.create.side_effect = create
    return client

def _make_mock_anthropic_always_error(exc_class):
    from unittest.mock import MagicMock
    client = MagicMock()
    client.messages.create.side_effect = exc_class(message="always fails")
    return client
```

---

## Running Tests

```bash
pytest tests/test_04_healer.py -v
pytest tests/test_04_healer.py --cov=app/modules/healer --cov-report=term-missing
```

---

## Common Issues & Resolutions

**Issue:** LLM occasionally returns code that changes a function signature.
**Resolution:** The system prompt explicitly forbids this. If it occurs, the Validation Agent
will catch the regression via `pytest` (existing tests will fail). The healer will retry with
an additional constraint injected into the next user message.

**Issue:** `json.JSONDecodeError` on valid-looking responses.
**Resolution:** Claude sometimes wraps JSON in markdown fences. `_parse_llm_response` strips
these. For persistent failures, log the raw response and add a specific fence pattern to the
strip regex.

**Issue:** `anthropic.RateLimitError` in production.
**Resolution:** The `/heal/file` endpoint has a rate limiter (10 req/min). For burst usage,
implement a token bucket or leaky bucket in the healer service using `slowapi`.

**Issue:** `fixed_code` is empty string in LLM response.
**Resolution:** Guard after parsing: `if not result_dict["fixed_code"].strip(): raise HealError("LLM returned empty fixed_code")`.
