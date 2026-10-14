"""orders 的契约测试（订单级计算）。

⚠️ 不要修改本文件。

注意：这里断言的是**端到端金额**（小计 → 折扣 → 税 → 应付）。
如果某个上游的折扣率算错了，失败会出现在这里 ——
**但根因不一定在本模块**。
"""

import pytest

from inventory.models import Item, Order
from inventory import orders


def _order(tier: str = "basic", items: list[Item] | None = None, **kw) -> Order:
    return Order(
        "o-1",
        tier,
        items if items is not None else [Item("A1", "笔记本", 5_000, qty=2)],
        **kw,
    )


def test_subtotal():
    assert orders.subtotal(_order()) == 10_000


def test_subtotal_empty_order():
    assert orders.subtotal(Order("o-1", "basic")) == 0


def test_build_totals_basic_tier():
    """小计 10000（恰好 ¥100）→ 5% 折扣 → 9500 计税 → 13% 税。"""
    t = orders.build_totals(_order())
    assert t.subtotal_cents == 10_000
    assert t.discount_cents == 500
    assert t.taxable_cents == 9_500
    assert t.tax_cents == 1_235
    assert t.total_cents == 10_735


def test_build_totals_with_coupon():
    t = orders.build_totals(_order(coupon_cents=1_000))
    assert t.discount_cents == 1_500     # 500（率）+ 1000（券）
    assert t.taxable_cents == 8_500
    assert t.tax_cents == 1_105          # 8500 × 13%
    assert t.total_cents == 9_605


def test_build_totals_empty_order_is_all_zero():
    t = orders.build_totals(Order("o-1", "basic"))
    assert t.subtotal_cents == 0
    assert t.discount_cents == 0
    assert t.total_cents == 0


def test_discount_ratio():
    assert orders.build_totals(_order()).discount_ratio == pytest.approx(0.05)


def test_discount_ratio_zero_subtotal():
    assert orders.build_totals(Order("o-1", "basic")).discount_ratio == 0.0


def test_effective_discount_rate():
    assert orders.effective_discount_rate(_order()) == pytest.approx(0.05)
    assert orders.effective_discount_rate(_order("gold")) == pytest.approx(0.10)
    assert orders.effective_discount_rate(Order("o-1", "basic")) == 0.0
