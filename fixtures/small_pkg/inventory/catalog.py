"""商品目录：按 SKU 查商品。

目录在构造时建索引，之后是只读的。结算流程用它来校验订单里的 SKU
**是否真的存在** —— 校验失败应该报错，而不是静默跳过。
"""

from __future__ import annotations

from .models import Item


class Catalog:
    """不可变商品目录。重复 SKU 会以后来者覆盖（构造时不做去重报错）。"""

    def __init__(self, entries: list[Item] | None = None) -> None:
        self._by_sku: dict[str, Item] = {}
        for it in entries or []:
            self._by_sku[it.sku] = it

    def get(self, sku: str) -> Item | None:
        """查一个 SKU；不存在返回 None。"""
        return self._by_sku.get(sku)

    def require(self, sku: str) -> Item:
        """查一个 SKU；不存在抛 KeyError。"""
        it = self._by_sku.get(sku)
        if it is None:
            raise KeyError(f"目录里没有 SKU {sku!r}")
        return it

    def has(self, sku: str) -> bool:
        return sku in self._by_sku

    def skus(self) -> list[str]:
        """全部 SKU，已排序。"""
        return sorted(self._by_sku)

    def by_category(self, category: str) -> list[Item]:
        """某个分类下的全部商品（按 SKU 排序）。"""
        return sorted(
            (it for it in self._by_sku.values() if it.category == category),
            key=lambda it: it.sku,
        )

    def categories(self) -> list[str]:
        return sorted({it.category for it in self._by_sku.values()})

    def __len__(self) -> int:
        return len(self._by_sku)

    def __contains__(self, sku: object) -> bool:
        return isinstance(sku, str) and sku in self._by_sku
