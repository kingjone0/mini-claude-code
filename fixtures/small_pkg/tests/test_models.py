"""models 的契约测试。

⚠️ 不要修改本文件。
"""

import pytest

from inventory.models import Item, Order


def test_item_line_total():
    it = Item("A1", "笔记本", 500, qty=3)
    assert it.line_total_cents == 1500


def test_item_default_qty_is_one():
    assert Item("A1", "笔记本", 500).qty == 1


def test_item_default_category():
    assert Item("A1", "笔记本", 500).category == "general"


def test_order_rejects_unknown_tier():
    with pytest.raises(ValueError):
        Order("o-1", "platinum")


def test_order_rejects_unknown_region():
    with pytest.raises(ValueError):
        Order("o-1", "basic", region="MARS")


def test_order_rejects_negative_coupon():
    with pytest.raises(ValueError):
        Order("o-1", "basic", coupon_cents=-1)


def test_order_defaults():
    o = Order("o-1", "basic")
    assert o.region == "CN"
    assert o.coupon_cents == 0
    assert o.items == []


def test_order_item_count_sums_qty_not_lines():
    o = Order(
        "o-1",
        "basic",
        [Item("A1", "a", 100, qty=2), Item("B2", "b", 100, qty=3)],
    )
    assert o.item_count == 5
    assert len(o.items) == 2
