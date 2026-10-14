"""⚠️ 旧版实现（2019 年的规则）。

**本模块已不再被任何地方引用。** 保留只是为了对照历史行为，
不要在新代码里 import 它，**也不需要改它**。

为什么留着而不删掉：
  1. 历史对账要用当时的口径复算旧账单；
  2. 它的规则和现在**不一样** —— 旧版没有客户等级加成，
     阶梯门槛的边界处理也不同。**照它改现在的代码会改错。**

如果你是为了修 bug 而翻到这里：这里的 `old_amount_rate` 不是现行实现。
现行的折扣逻辑在 `pricing.py`。
"""

from __future__ import annotations

# 2019 年的阶梯。和现在的 AMOUNT_TIERS 相比：
#   · 少了最低一档（0, 0.00）—— 旧版默认就是 0
#   · 顺序是高的在前，靠提前 return 退出
LEGACY_TIERS: list[tuple[int, float]] = [
    (100_000, 0.15),
    (50_000, 0.10),
    (10_000, 0.05),
]

# 旧版固定汇率（对账历史账单还要用，不要删）
LEGACY_FX: dict[str, float] = {
    "USD": 6.90,
    "EUR": 7.75,
    "JPY": 0.064,
    "HKD": 0.88,
}


def old_amount_rate(subtotal_cents: int) -> float:
    """2019 口径的金额阶梯折扣率。

    注意：旧版**没有**客户等级加成，也**没有** 25% 上限。
    """
    for threshold, rate in LEGACY_TIERS:
        if subtotal_cents >= threshold:
            return rate
    return 0.0


def old_discount_cents(subtotal_cents: int, coupon_cents: int = 0) -> int:
    """2019 口径的折扣金额（没有等级参数）。"""
    rate = old_amount_rate(subtotal_cents)
    off = int(subtotal_cents * rate + 0.5) + coupon_cents
    return min(off, subtotal_cents)


def old_tax_cents(subtotal_cents: int, region: str) -> int:
    """2019 口径的税。旧版对 CN 用的是 0.16（后来才降到 0.13）。"""
    legacy_rates = {"CN": 0.16, "US": 0.07, "EU": 0.20}
    return int(subtotal_cents * legacy_rates.get(region, 0.0) + 0.5)


def old_total_cents(subtotal_cents: int, region: str, coupon_cents: int = 0) -> int:
    """2019 口径的应付总额。"""
    disc = old_discount_cents(subtotal_cents, coupon_cents)
    taxable = subtotal_cents - disc
    return taxable + old_tax_cents(taxable, region)


def to_cny(amount: float, currency: str) -> float:
    """按旧汇率把外币折成人民币。未知币种抛 KeyError。"""
    return amount * LEGACY_FX[currency]


def legacy_rate_table() -> list[tuple[str, float]]:
    """旧汇率的可打印视图（按币种排序）。"""
    return sorted(LEGACY_FX.items())


def describe_legacy() -> str:
    """给运维看的一段说明。"""
    lines = [
        "=== 2019 口径说明 ===",
        "1) 折扣只看金额，不看客户等级",
        "2) 门槛含下界（>=）",
        "3) CN 税率当时是 16%",
        "4) 没有 25% 折扣上限",
    ]
    for cur, rate in legacy_rate_table():
        lines.append(f"   {cur} = {rate}")
    return "\n".join(lines)


def compare_with_current(subtotal_cents: int, region: str = "CN") -> dict:
    """把旧口径和新口径并排列出来 —— 仅供人工核对历史账单用。

    ⚠️ 这里**故意 import 了现行实现**，但只在函数体内 import，
    避免模块级循环依赖。它不参与任何结算路径。
    """
    from . import pricing
    from . import tax

    old = old_total_cents(subtotal_cents, region)
    new_rate = pricing.amount_rate(subtotal_cents)
    new_disc = pricing.discount_cents(subtotal_cents, "basic")
    new_taxable = subtotal_cents - new_disc
    new_total = new_taxable + tax.tax_cents(new_taxable, region)
    return {
        "subtotal_cents": subtotal_cents,
        "legacy_total_cents": old,
        "current_total_cents": new_total,
        "legacy_amount_rate": old_amount_rate(subtotal_cents),
        "current_amount_rate": new_rate,
        "delta_cents": new_total - old,
    }


# ── 以下为历史遗留的常量表，已无引用，保留备查 ──────────────────────────

LEGACY_CATEGORY_CODES: dict[str, str] = {
    "general": "00",
    "stationery": "01",
    "home": "02",
    "electronics": "03",
    "apparel": "04",
    "food": "05",
    "books": "06",
    "sports": "07",
}

LEGACY_REGION_CODES: dict[str, str] = {
    "CN": "156",
    "US": "840",
    "EU": "978",
}

LEGACY_STATUS_CODES: dict[str, str] = {
    "draft": "D",
    "confirmed": "C",
    "shipped": "S",
    "cancelled": "X",
}


def legacy_sku_prefix(category: str) -> str:
    """旧版 SKU 前缀规则（已废弃）。"""
    return LEGACY_CATEGORY_CODES.get(category, "99")
