"""Tests for Module 05 — Validation Agent."""

from __future__ import annotations

import subprocess

import pytest

from app.core.schemas import ComplexityRecord
from app.modules.validation.service import (
    check_complexity,
    check_flake8,
    check_syntax,
)

VALID_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
SYNTAX_ERROR_CODE = "def broken(:\n    pass"
LONG_LINE_CODE = "def f():\n    x = 'a' * 200  # " + "x" * 80 + "\n"
COMPLEX_CODE = "\n".join(
    [
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
    ]
)


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


class TestCheckFlake8:

    def test_clean_code_passes(self):
        result = check_flake8(VALID_CODE)
        assert result.status == "PASS"

    def test_timeout_returns_fail(self, mocker):
        mocker.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("flake8", 30)
        )
        result = check_flake8(VALID_CODE)
        assert result.status == "FAIL"
        assert "timed out" in result.message


class TestCheckComplexity:

    def test_reduced_complexity_passes(self):
        baseline = ComplexityRecord(
            file="f.py", complexity=10.0, maintainability=50.0
        )
        result = check_complexity("f.py", VALID_CODE, baseline)
        assert result.status == "PASS"

    def test_large_increase_fails(self):
        baseline = ComplexityRecord(
            file="f.py", complexity=3.0, maintainability=80.0
        )
        result = check_complexity("f.py", COMPLEX_CODE, baseline)
        assert result.status == "FAIL"
        assert "increased" in result.message

    def test_no_baseline_skips(self):
        result = check_complexity("f.py", VALID_CODE, None)
        assert result.status == "SKIP"

    def test_parse_error_baseline_skips(self):
        baseline = ComplexityRecord(
            file="f.py",
            complexity=-1.0,
            maintainability=-1.0,
            parse_error=True,
        )
        result = check_complexity("f.py", VALID_CODE, baseline)
        assert result.status == "SKIP"
