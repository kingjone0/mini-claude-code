"""calc.py 的测试。

⚠️ **不要修改本文件。** 修改测试来让它通过不算修好 —— 验收会检查测试文件未被改动。
"""

import pytest

from calc import clamp, safe_divide


def test_safe_divide_normal():
    assert safe_divide(6, 3) == 2
    assert safe_divide(-9, 3) == -3


def test_safe_divide_by_zero_raises_value_error():
    """契约：除零抛 ValueError，而不是漏出 ZeroDivisionError。"""
    with pytest.raises(ValueError):
        safe_divide(1, 0)


def test_clamp_within_range():
    assert clamp(5, 0, 10) == 5


def test_clamp_below_and_above():
    assert clamp(-1, 0, 10) == 0
    assert clamp(99, 0, 10) == 10


def test_clamp_invalid_range():
    with pytest.raises(ValueError):
        clamp(5, 10, 0)
