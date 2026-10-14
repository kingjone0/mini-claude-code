"""领域模型。

所有金额字段都是**整数分**（见 money.py 的约定）。
模型本身不做任何金额计算 —— 计算在 orders / pricing / tax 里。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 支持的客户等级。加新等级要同时更新 pricing.TIER_BONUS。
TIERS = ("basic", "silver", "gold")

# 支持的地区。加新地区要同时更新 tax.TAX_RATES。
REGIONS = ("CN", "US", "EU")


@dataclass(frozen=True)
class Item:
    """一行商品。`qty` 是数量，`unit_price_cents` 是**单价**（分）。"""

    sku: str
    name: str
    unit_price_cents: int
    qty: int = 1
    category: str = "general"

    @property
    def line_total_cents(self) -> int:
        """本行小计（分）= 单价 × 数量。"""
        return self.unit_price_cents * self.qty


@dataclass
class Order:
    """一张订单。

    `tier` 是客户等级（见 TIERS），`coupon_cents` 是**额外**优惠券金额（分），
    它在折扣率计算**之后**叠加，不参与折扣率本身。
    """

    order_id: str
    tier: str
    items: list[Item] = field(default_factory=list)
    region: str = "CN"
    coupon_cents: int = 0

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"未知客户等级：{self.tier!r}（支持 {TIERS}）")
        if self.region not in REGIONS:
            raise ValueError(f"未知地区：{self.region!r}（支持 {REGIONS}）")
        if self.coupon_cents < 0:
            raise ValueError("优惠券金额不能为负")

    @property
    def item_count(self) -> int:
        """总件数（不是行数）。"""
        return sum(it.qty for it in self.items)
