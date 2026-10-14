"""基础算术工具。

⚠️ 本模块是「修复靶场」的一部分：**故意留了缺陷**，用于验证 agent 的修复闭环。
   缺陷由 test_calc.py 暴露。修完两个模块后 `pytest` 应当全绿。
"""


def safe_divide(a: float, b: float) -> float:
    """返回 a / b。

    契约：b 为 0 时抛 **ValueError**（调用方需要能区分「除零」和「类型错」，
    所以不能让它漏出 ZeroDivisionError）。
    """
    return a / b


def clamp(value: float, low: float, high: float) -> float:
    """把 value 夹到闭区间 [low, high]。low > high 时抛 ValueError。"""
    if low > high:
        raise ValueError(f"low({low}) 不能大于 high({high})")
    return max(low, min(value, high))
