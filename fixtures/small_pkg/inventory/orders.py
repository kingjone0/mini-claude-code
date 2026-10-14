"""订单级计算：小计 → 折扣 → 税 → 应付。

这是结算的**中间层**：它把 pricing（折扣）与 tax（税）串起来，
向上给 pipeline 一个 `Totals`。它自己不做校验 —— 校验在 pipeline 里。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Order
from . import pricing
from . import tax


@dataclass(frozen=True)
class Totals:
    """一张订单的结算结果。所有字段都是整数分。"""

    subtotal_cents: int
    """小计：各行 line_total 之和。"""

    discount_cents: int
    """折扣金额（含优惠券）。"""

    taxable_cents: int
    """计税基数 = 小计 − 折扣。"""

    tax_cents: int
    """税额。"""

    total_cents: int
    """应付 = 计税基数 + 税额。"""

    @property
    def discount_ratio(self) -> float:
        """折扣占小计的比例。小计为 0 时返回 0.0。"""
        if self.subtotal_cents == 0:
            return 0.0
        return self.discount_cents / self.subtotal_cents


def subtotal(order: Order) -> int:
    """订单小计（分）。空订单返回 0。"""
    return sum(it.line_total_cents for it in order.items)


def build_totals(order: Order) -> Totals:
    """把一张订单算成 `Totals`。**不校验**订单合法性（pipeline 负责）。"""
    sub = subtotal(order)
    disc = pricing.discount_cents(sub, order.tier, order.coupon_cents)
    taxable = sub - disc
    tx = tax.tax_cents(taxable, order.region)
    return Totals(
        subtotal_cents=sub,
        discount_cents=disc,
        taxable_cents=taxable,
        tax_cents=tx,
        total_cents=taxable + tx,
    )


def effective_discount_rate(order: Order) -> float:
    """实际折扣率（相对小计，**不含优惠券**）。给报表和对比用。"""
    sub = subtotal(order)
    if sub == 0:
        return 0.0
    rate = pricing.discount_rate(sub, order.tier)
    return rate
