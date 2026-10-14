"""税费计算。

**税在折扣之后计算** —— 计税基数是「小计 − 折扣」，不是小计。
这是本模块唯一需要和 orders.py 保持一致的约定。
"""

from __future__ import annotations

from .models import REGIONS
from .money import round_cents

# 简化税率表：不按品类细分，只按地区。
TAX_RATES: dict[str, float] = {
    "CN": 0.13,
    "US": 0.07,
    "EU": 0.20,
}


def tax_rate(region: str) -> float:
    """查地区税率。未知地区抛 ValueError。"""
    try:
        return TAX_RATES[region]
    except KeyError:
        raise ValueError(f"未知地区：{region!r}（支持 {REGIONS}）") from None


def tax_cents(taxable_cents: int, region: str) -> int:
    """对**已折扣后**的金额计税，返回整数分。

    计税基数为负抛 ValueError（正常流程下不该发生 ——
    `pricing.discount_cents` 保证折扣不超过小计）。
    """
    if taxable_cents < 0:
        raise ValueError("计税基数不能为负")
    return round_cents(taxable_cents * tax_rate(region))
