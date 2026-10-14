"""pricing 的契约测试。

⚠️ 不要修改本文件。改测试让它通过不算修好 —— 验收会检查测试文件未被改动。

本文件里的**门槛边界**用例是刻意写的：契约规定阶梯门槛**含下界**，
恰好等于门槛时要进入该档。
"""

import pytest

from inventory import pricing


# ── 金额阶梯 ────────────────────────────────────────────────────────────
def test_amount_rate_below_first_threshold():
    assert pricing.amount_rate(9_999) == 0.00


def test_amount_rate_exactly_at_first_threshold():
    """门槛含下界：恰好 ¥100.00 就进 5% 档。"""
    assert pricing.amount_rate(10_000) == 0.05


def test_amount_rate_exactly_at_second_threshold():
    assert pricing.amount_rate(50_000) == 0.10


def test_amount_rate_exactly_at_third_threshold():
    assert pricing.amount_rate(100_000) == 0.15


def test_amount_rate_between_thresholds():
    assert pricing.amount_rate(10_001) == 0.05
    assert pricing.amount_rate(99_999) == 0.10


def test_amount_rate_above_top_threshold():
    assert pricing.amount_rate(500_000) == 0.15


def test_amount_rate_zero_and_negative():
    assert pricing.amount_rate(0) == 0.00
    assert pricing.amount_rate(-1) == 0.00


# ── 等级加成 ────────────────────────────────────────────────────────────
def test_tier_rate():
    assert pricing.tier_rate("basic") == 0.00
    assert pricing.tier_rate("silver") == 0.02
    assert pricing.tier_rate("gold") == 0.05


def test_tier_rate_unknown():
    with pytest.raises(ValueError):
        pricing.tier_rate("platinum")


# ── 合成折扣率 ──────────────────────────────────────────────────────────
def test_discount_rate_combines_amount_and_tier():
    """10000 小计 + 黄金 = 5% 阶梯 + 5% 等级 = 10%。"""
    assert pricing.discount_rate(10_000, "gold") == pytest.approx(0.10)


def test_discount_rate_basic_tier_equals_amount_rate():
    assert pricing.discount_rate(10_000, "basic") == pytest.approx(0.05)


def test_discount_rate_capped():
    """顶档 15% + 黄金 5% = 20%，未触 25% 上限。"""
    assert pricing.discount_rate(200_000, "gold") == pytest.approx(0.20)


def test_discount_rate_negative_subtotal_raises():
    with pytest.raises(ValueError):
        pricing.discount_rate(-1, "basic")


# ── 折扣金额 ────────────────────────────────────────────────────────────
def test_discount_cents_basic():
    """10000 × 5% = 500 分。"""
    assert pricing.discount_cents(10_000, "basic") == 500


def test_discount_cents_with_coupon():
    """折扣率算完后，优惠券额外叠加。"""
    assert pricing.discount_cents(10_000, "basic", coupon_cents=300) == 800


def test_discount_cents_never_exceeds_subtotal():
    assert pricing.discount_cents(1_000, "gold", coupon_cents=99_999) == 1_000


def test_discount_cents_negative_inputs_raise():
    with pytest.raises(ValueError):
        pricing.discount_cents(-1, "basic")
    with pytest.raises(ValueError):
        pricing.discount_cents(1_000, "basic", coupon_cents=-1)
