"""reporting 的契约测试。

⚠️ 不要修改本文件。

本文件只断言**汇总层面的形状与恒等关系**，不钉具体金额 ——
金额的契约在 test_orders / test_pipeline 里。
这样 reporting 的测试不会因为上游折扣规则变化而连带失败。
"""

from inventory.models import Item, Order
from inventory import reporting


def _orders() -> list[Order]:
    return [
        Order("o-1", "basic", [Item("A1", "笔记本", 5_000, qty=2)]),
        Order("o-2", "gold", [Item("B2", "台灯", 8_000, qty=1)]),
    ]


def test_summarize_order_count_and_subtotal():
    s = reporting.summarize(_orders())
    assert s["orders"] == 2
    assert s["subtotal_cents"] == 18_000


def test_summarize_identity():
    """恒等式：小计 − 折扣 = 计税基数；计税基数 + 税 = 应付。"""
    s = reporting.summarize(_orders())
    assert s["subtotal_cents"] - s["discount_cents"] == s["taxable_cents"]
    assert s["taxable_cents"] + s["tax_cents"] == s["total_cents"]


def test_summarize_empty():
    s = reporting.summarize([])
    assert s["orders"] == 0
    assert s["total_cents"] == 0


def test_render_contains_lines():
    text = reporting.render(reporting.summarize(_orders()))
    assert "订单数：2" in text
    assert "小计：" in text
    assert "应付：" in text


def test_render_empty():
    assert "订单数：0" in reporting.render(reporting.summarize([]))


def test_by_tier_covers_all_tiers():
    d = reporting.by_tier(_orders())
    assert set(d) == {"basic", "gold"}
    assert all(v > 0 for v in d.values())


def test_top_orders_descending():
    tops = reporting.top_orders(_orders(), n=2)
    assert len(tops) == 2
    assert tops[0][1] >= tops[1][1]


def test_top_orders_limits_n():
    assert len(reporting.top_orders(_orders(), n=1)) == 1


def test_average_order_cents():
    assert reporting.average_order_cents(_orders()) > 0


def test_average_empty():
    assert reporting.average_order_cents([]) == 0
