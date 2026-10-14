"""订单结算包。

⚠️ 本包是「修复靶场」的一部分：**里面故意留了缺陷**，由 tests/ 暴露。

对外只暴露结算流水线 —— 结算逻辑分散在 pricing / tax / orders 里，
调用者不需要知道细节。
"""

from .models import Item, Order
from .catalog import Catalog
from .orders import Totals
from .pipeline import OrderError, process

__all__ = ["Item", "Order", "Catalog", "Totals", "OrderError", "process"]
