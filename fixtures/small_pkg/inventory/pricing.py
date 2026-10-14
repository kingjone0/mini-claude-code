"""折扣计算。

总折扣率由两部分**相加**：

  ① **金额阶梯** —— 按订单小计落在哪一档（见 AMOUNT_TIERS）
  ② **客户等级加成** —— 白银 +2%，黄金 +5%（见 TIER_BONUS）

两者相加后上限 `MAX_DISCOUNT`（避免叠加出离谱的折扣）。
优惠券不参与折扣率，它在折扣金额算完之后叠加（见 `discount_cents`）。
"""

from __future__ import annotations

from .models import TIERS
from .money import round_cents

# 折扣率上限。金额阶梯最高 15% + 黄金 5% = 20%，所以 25% 是安全上界。
MAX_DISCOUNT = 0.25

# 金额阶梯：(最低小计(分), 折扣率)。
#
# ⚠️ 门槛是**含下界**的 —— 小计**恰好等于**门槛，就算进这一档。
#    例：小计 10000 分（¥100.00）应当拿到 5%，不是 0%。
AMOUNT_TIERS: tuple[tuple[int, float], ...] = (
    (0, 0.00),
    (10_000, 0.05),      # 满 ¥100
    (50_000, 0.10),      # 满 ¥500
    (100_000, 0.15),     # 满 ¥1000
)

# 客户等级加成
TIER_BONUS: dict[str, float] = {
    "basic": 0.00,
    "silver": 0.02,
    "gold": 0.05,
}


def amount_rate(subtotal_cents: int) -> float:
    """按小计取**金额阶梯**折扣率。

    契约：门槛**含下界** —— `subtotal_cents == 门槛` 时进入该档。
    小计为负时返回 0.0（不报错，交给上层校验去管）。
    """
    if subtotal_cents < 0:
        return 0.00
    rate = 0.00
    for threshold, r in AMOUNT_TIERS:
        if subtotal_cents > threshold:
            rate = r
    return rate


def tier_rate(tier: str) -> float:
    """客户等级加成。未知等级抛 ValueError。"""
    if tier not in TIERS:
        raise ValueError(f"未知客户等级：{tier!r}（支持 {TIERS}）")
    return TIER_BONUS[tier]


def discount_rate(subtotal_cents: int, tier: str) -> float:
    """总折扣率 = 金额阶梯 + 等级加成，上限 MAX_DISCOUNT。

    小计为负抛 ValueError。
    """
    if subtotal_cents < 0:
        raise ValueError("小计不能为负")
    return min(amount_rate(subtotal_cents) + tier_rate(tier), MAX_DISCOUNT)


def discount_cents(subtotal_cents: int, tier: str, coupon_cents: int = 0) -> int:
    """折扣**金额**（分）。

    先按折扣率算，再把优惠券加上去（优惠券是固定金额，不打折）。
    结果不会超过小计本身 —— 否则应付会变成负数。
    """
    if subtotal_cents < 0:
        raise ValueError("小计不能为负")
    if coupon_cents < 0:
        raise ValueError("优惠券金额不能为负")

    rate = discount_rate(subtotal_cents, tier)
    from_rate = round_cents(subtotal_cents * rate)
    total = from_rate + coupon_cents
    return min(total, subtotal_cents)
