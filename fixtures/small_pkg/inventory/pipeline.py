"""处理流水线：校验 → 计算。

**这是外部调用者唯一该用的入口。** 结算细节（折扣、税）都在下层，
pipeline 只负责「先把订单校验干净，再交给 orders 去算」。

分层的理由：校验失败要在**动手算钱之前**报出来，
否则会得到一张金额看似正常、实际基于脏数据的订单。
"""

from __future__ import annotations

from .catalog import Catalog
from .models import Order
from .orders import Totals, build_totals, subtotal


class OrderError(ValueError):
    """订单不合法（缺商品、数量非正、SKU 不在目录里……）。"""


def validate(order: Order, catalog: Catalog) -> None:
    """校验订单。任何一条不过就抛 OrderError，消息里说明是哪一条。

    校验项：
      1. 至少有一行商品
      2. 每行数量 > 0
      3. 每行单价 >= 0
      4. 每个 SKU 都在目录里
    """
    if not order.items:
        raise OrderError(f"订单 {order.order_id!r} 没有任何商品")

    for it in order.items:
        if it.qty <= 0:
            raise OrderError(f"{it.sku} 的数量必须为正，实际 {it.qty}")
        if it.unit_price_cents < 0:
            raise OrderError(f"{it.sku} 的单价不能为负，实际 {it.unit_price_cents}")
        if catalog.get(it.sku) is None:
            raise OrderError(f"目录里没有 SKU {it.sku!r}")


def process(order: Order, catalog: Catalog) -> Totals:
    """校验并计算一张订单。

    抛 OrderError 表示订单不合法；正常情况下返回 `Totals`。
    """
    validate(order, catalog)
    return build_totals(order)


def process_many(orders: list[Order], catalog: Catalog) -> tuple[list[Totals], list[str]]:
    """批量处理。返回 (成功的 Totals 列表, 失败的说明列表)。

    单张订单失败**不影响**其余订单 —— 批量导入场景下，
    一张脏订单不该让整批都进不来。
    """
    ok: list[Totals] = []
    bad: list[str] = []
    for o in orders:
        try:
            ok.append(process(o, catalog))
        except OrderError as exc:
            bad.append(f"{o.order_id}: {exc}")
    return ok, bad


def preview(order: Order, catalog: Catalog) -> str:
    """给人工看的单行预览（调试用）。不校验，因此可以用于脏数据排查。"""
    sub = subtotal(order)
    t = build_totals(order)
    return (
        f"{order.order_id} [{order.tier}/{order.region}] "
        f"小计={sub} 折扣={t.discount_cents} 税={t.tax_cents} 应付={t.total_cents}"
    )
