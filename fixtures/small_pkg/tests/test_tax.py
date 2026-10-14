"""tax 的契约测试。

⚠️ 不要修改本文件。
"""

import pytest

from inventory import tax


def test_tax_rate_table():
    assert tax.tax_rate("CN") == 0.13
    assert tax.tax_rate("US") == 0.07
    assert tax.tax_rate("EU") == 0.20


def test_tax_rate_unknown_region():
    with pytest.raises(ValueError):
        tax.tax_rate("MARS")


def test_tax_cents_basic():
    assert tax.tax_cents(10_000, "CN") == 1300
    assert tax.tax_cents(10_000, "US") == 700
    assert tax.tax_cents(10_000, "EU") == 2000


def test_tax_cents_rounds_to_nearest_cent():
    """9500 × 13% = 1235.0；999 × 7% = 69.93 → 70。"""
    assert tax.tax_cents(9_500, "CN") == 1235
    assert tax.tax_cents(999, "US") == 70


def test_tax_cents_zero():
    assert tax.tax_cents(0, "CN") == 0


def test_tax_cents_negative_taxable_raises():
    with pytest.raises(ValueError):
        tax.tax_cents(-1, "CN")
