"""统计工具。

⚠️ 本模块是「修复靶场」的一部分：**故意留了缺陷**，用于验证 agent 的修复闭环。
   缺陷由 test_stats.py 暴露。
"""


def mean(nums: list[float]) -> float:
    """算术平均。空序列抛 ValueError。"""
    if not nums:
        raise ValueError("空序列没有平均值")
    return sum(nums) / len(nums)


def median(nums: list[float]) -> float:
    """中位数。

    契约：长度为偶数时，取**中间两个数的平均**；奇数时取正中间那个。
    空序列抛 ValueError。
    """
    if not nums:
        raise ValueError("空序列没有中位数")
    ordered = sorted(nums)
    return ordered[len(ordered) // 2]
