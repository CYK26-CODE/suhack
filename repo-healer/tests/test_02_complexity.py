"""Tests for Module 02 — Complexity Agent."""

from __future__ import annotations

import pytest

from app.core.schemas import ComplexityRecord
from app.modules.complexity.service import compute_complexity

SIMPLE_SOURCE = """\
def add(a, b):
    return a + b
"""

COMPLEX_SOURCE = """\
def process(x, y, z):
    if x > 0:
        if y > 0:
            for i in range(z):
                if i % 2 == 0:
                    pass
                else:
                    pass
        elif y < 0:
            pass
    else:
        while x < 10:
            x += 1
    return x
"""

SYNTAX_ERROR_SOURCE = "def broken(:\n    pass"


class TestComputeComplexity:

    def test_simple_function_has_low_complexity(self):
        record = compute_complexity("src/add.py", SIMPLE_SOURCE)
        assert record.complexity >= 1.0
        assert record.complexity <= 3.0
        assert not record.parse_error

    def test_complex_function_has_higher_complexity(self):
        simple = compute_complexity("src/add.py", SIMPLE_SOURCE)
        complex_ = compute_complexity("src/process.py", COMPLEX_SOURCE)
        assert complex_.complexity > simple.complexity

    def test_syntax_error_returns_sentinel(self):
        record = compute_complexity("src/broken.py", SYNTAX_ERROR_SOURCE)
        assert record.parse_error is True
        assert record.complexity == -1.0
        assert record.maintainability == -1.0

    def test_empty_file_returns_zero_complexity(self):
        record = compute_complexity("src/empty.py", "")
        assert record.complexity == 0.0
        assert not record.parse_error

    def test_function_count_matches_radon(self):
        two_func_src = SIMPLE_SOURCE + "\ndef subtract(a, b):\n    return a - b\n"
        record = compute_complexity("src/two_funcs.py", two_func_src)
        assert record.function_count == 2

    def test_maintainability_in_valid_range(self):
        record = compute_complexity("src/add.py", SIMPLE_SOURCE)
        assert 0.0 <= record.maintainability <= 100.0

    def test_parse_error_does_not_raise(self):
        """Syntax errors must not propagate — pipeline must not stall."""
        record = compute_complexity("src/bad.py", SYNTAX_ERROR_SOURCE)
        assert record is not None

    def test_class_methods_counted(self):
        cls_source = "class Foo:\n    def bar(self):\n        if True:\n            pass\n"
        record = compute_complexity("src/foo.py", cls_source)
        assert record.function_count >= 1


class TestComplexityRecordSchema:

    def test_negative_complexity_rejected(self):
        with pytest.raises(Exception):
            ComplexityRecord(
                file="x.py", complexity=-5.0, maintainability=60.0, function_count=1
            )

    def test_sentinel_minus_one_allowed(self):
        r = ComplexityRecord(
            file="x.py",
            complexity=-1.0,
            maintainability=-1.0,
            function_count=0,
            parse_error=True,
        )
        assert r.complexity == -1.0

    def test_maintainability_above_100_rejected(self):
        with pytest.raises(Exception):
            ComplexityRecord(
                file="x.py", complexity=5.0, maintainability=101.0, function_count=2
            )
