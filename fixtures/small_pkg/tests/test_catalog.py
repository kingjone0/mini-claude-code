"""catalog 的契约测试。

⚠️ 不要修改本文件。
"""

import pytest

from inventory.catalog import Catalog
from inventory.models import Item


def _cat() -> Catalog:
    return Catalog(
        [
            Item("A1", "笔记本", 500, category="stationery"),
            Item("B2", "台灯", 8000, category="home"),
            Item("C3", "钢笔", 300, category="stationery"),
        ]
    )


def test_get_and_require():
    c = _cat()
    assert c.get("A1").name == "笔记本"
    assert c.require("B2").name == "台灯"


def test_get_missing_returns_none():
    assert _cat().get("ZZ") is None


def test_require_missing_raises():
    with pytest.raises(KeyError):
        _cat().require("ZZ")


def test_skus_sorted():
    assert _cat().skus() == ["A1", "B2", "C3"]


def test_by_category():
    assert [it.sku for it in _cat().by_category("stationery")] == ["A1", "C3"]
    assert _cat().by_category("nope") == []


def test_categories():
    assert _cat().categories() == ["home", "stationery"]


def test_contains_and_len():
    c = _cat()
    assert "A1" in c
    assert "ZZ" not in c
    assert len(c) == 3


def test_empty_catalog():
    c = Catalog()
    assert len(c) == 0
    assert c.skus() == []
    assert c.categories() == []
