"""金额工具。

**约定：本包内所有金额一律用「整数分」表示**，绝不用浮点存钱。
浮点累加会产生 0.1 + 0.2 != 0.3 这类误差，在结算系统里就是对不上账。
需要乘折扣率（浮点）时，算完立刻四舍五入回整数分。
"""

from __future__ import annotations

CENT = 1
YUAN = 100


def to_cents(amount: float) -> int:
    """元 → 分。四舍五入到最近的分。"""
    return round_cents(amount * 100)


def to_yuan(cents: int) -> float:
    """分 → 元（仅用于展示与调试，**不要拿它继续参与计算**）。"""
    return cents / 100


def round_cents(value: float) -> int:
    """把浮点分值四舍五入成整数分。

    用「加 0.5 再取整」而不是 Python 内置的 `round()` ——
    内置的是**银行家舍入**（round-half-to-even），
    `round(2.5) == 2`、`round(3.5) == 4`，
    在结算场景里会让对账的人非常困惑。
    """
    if value >= 0:
        return int(value + 0.5)
    return -int(-value + 0.5)


def format_cents(cents: int) -> str:
    """把整数分格式化成 `¥123.45` 这种可读形式。"""
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}¥{c // 100}.{c % 100:02d}"


def split_cents(cents: int) -> tuple[int, int]:
    """拆成 (元, 分)。"""
    sign = -1 if cents < 0 else 1
    c = abs(cents)
    return sign * (c // 100), c % 100
