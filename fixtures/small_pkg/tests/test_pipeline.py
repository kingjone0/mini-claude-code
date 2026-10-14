"""pipeline 的契约测试（端到端）。

⚠️ 不要修改本文件。

这里是从**外部入口**走的：校验 → 计算。断言的是最终金额，
所以它同时覆盖 orders / pricing / tax 三层 ——
失败时根因可能在任何一层。
"""

import pytest

from inventory.catalog import Catalog
from inventory.models import Item, Order
from inventory.pipeline import OrderError, preview, process, process_many


def _cat() -> Catalog:
    return Catalog(
        [
            Item("A1", "笔记本", 5_000, category="stationery"),
            Item("B2", "台灯", 8_000, category="home"),
        ]
    )


def test_process_basic_order():
    order = Order("o-1", "basic", [Item("A1", "笔记本", 5_000, qty=2)])
    t = process(order, _cat())
    assert t.subtotal_cents == 10_000
    assert t.discount_cents == 500
    assert t.taxable_cents == 9_500
    assert t.tax_cents == 1_235
    assert t.total_cents == 10_735


def test_process_gold_tier():
    """黄金客户：10000 小计 → 5% 阶梯 + 5% 等级 = 10% 折扣。"""
    order = Order("o-2", "gold", [Item("A1", "笔记本", 5_000, qty=2)])
    t = process(order, _cat())
    assert t.discount_cents == 1_000
    assert t.taxable_cents == 9_000
    assert t.tax_cents == 1_170
    assert t.total_cents == 10_170


def test_process_below_first_threshold():
    """小计 9999（差 1 分不到门槛）→ 无金额折扣，basic 等级也无加成。"""
    order = Order("o-3", "basic", [Item("A1", "笔记本", 3_333, qty=3)])
    t = process(order, _cat())
    assert t.subtotal_cents == 9_999
    assert t.discount_cents == 0


def test_process_rejects_empty_order():
    with pytest.raises(OrderError):
        process(Order("o-4", "basic"), _cat())


def test_process_rejects_non_positive_qty():
    with pytest.raises(OrderError):
        process(Order("o-5", "basic", [Item("A1", "笔记本", 5_000, qty=0)]), _cat())


def test_process_rejects_negative_price():
    with pytest.raises(OrderError):
        process(Order("o-6", "basic", [Item("A1", "笔记本", -1)]), _cat())


def test_process_rejects_unknown_sku():
    with pytest.raises(OrderError):
        process(Order("o-7", "basic", [Item("ZZ", "幽灵", 100)]), _cat())


def test_process_many_partial_failure():
    """一张脏订单不该让整批都进不来。"""
    good = Order("g-1", "basic", [Item("A1", "笔记本", 5_000, qty=2)])
    bad = Order("b-1", "basic", [Item("ZZ", "幽灵", 100)])
    ok, errs = process_many([good, bad], _cat())
    assert len(ok) == 1
    assert len(errs) == 1
    assert "ZZ" in errs[0]


def test_preview_does_not_validate():
    """preview 不校验，所以脏订单也能出预览（用于排查）。"""
    s = preview(Order("o-8", "basic", [Item("ZZ", "幽灵", 100)]), _cat())
    assert "o-8" in s
