"""报表：把一批订单汇总成摘要。

只读 —— 本模块不修改任何订单，也不参与结算。
"""

from __future__ import annotations

from .models import Order
from .money import format_cents
from .orders import build_totals, subtotal


def summarize(orders: list[Order]) -> dict:
    """把一批订单汇总。空列表返回全 0 的摘要。"""
    totals = [build_totals(o) for o in orders]
    return {
        "orders": len(orders),
        "subtotal_cents": sum(t.subtotal_cents for t in totals),
        "discount_cents": sum(t.discount_cents for t in totals),
        "taxable_cents": sum(t.taxable_cents for t in totals),
        "tax_cents": sum(t.tax_cents for t in totals),
        "total_cents": sum(t.total_cents for t in totals),
    }


def render(summary: dict) -> str:
    """把摘要渲染成多行文本。"""
    return "\n".join(
        [
            f"订单数：{summary['orders']}",
            f"小计：{format_cents(summary['subtotal_cents'])}",
            f"折扣：-{format_cents(summary['discount_cents'])}",
            f"计税基数：{format_cents(summary['taxable_cents'])}",
            f"税：{format_cents(summary['tax_cents'])}",
            f"应付：{format_cents(summary['total_cents'])}",
        ]
    )


def by_tier(orders: list[Order]) -> dict[str, int]:
    """按客户等级统计应付金额。"""
    out: dict[str, int] = {}
    for o in orders:
        out[o.tier] = out.get(o.tier, 0) + build_totals(o).total_cents
    return out


def top_orders(orders: list[Order], n: int = 5) -> list[tuple[str, int]]:
    """应付金额最高的 n 张订单，降序。返回 (order_id, total_cents)。"""
    pairs = [(o.order_id, build_totals(o).total_cents) for o in orders]
    pairs.sort(key=lambda p: (-p[1], p[0]))
    return pairs[:n]


def average_order_cents(orders: list[Order]) -> int:
    """客单价（分）。空列表返回 0。"""
    if not orders:
        return 0
    return sum(t.total_cents for t in (build_totals(o) for o in orders)) // len(orders)
