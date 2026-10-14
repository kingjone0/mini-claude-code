"""stats.py 的测试。

⚠️ **不要修改本文件。** 修改测试来让它通过不算修好 —— 验收会检查测试文件未被改动。
"""

import pytest

from stats import mean, median


def test_mean_basic():
    assert mean([1, 2, 3]) == 2


def test_mean_empty_raises():
    with pytest.raises(ValueError):
        mean([])


def test_median_odd_length():
    assert median([3, 1, 2]) == 2


def test_median_even_length():
    """契约：偶数长度取中间两个数的平均。"""
    assert median([1, 2, 3, 4]) == 2.5
    assert median([1, 2, 3, 4, 5, 6]) == 3.5


def test_median_empty_raises():
    with pytest.raises(ValueError):
        median([])
