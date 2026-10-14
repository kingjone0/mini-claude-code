"""money 的契约测试。

⚠️ 不要修改本文件。改测试让它通过不算修好 —— 验收会检查测试文件未被改动。
"""

from inventory import money


def test_round_cents_is_half_up_not_bankers():
    """必须四舍五入，不能用 Python 内置 `round()` 的银行家舍入。

    `round(2.5)` 在内置实现里是 2（round-half-to-even），结算场景会让人对不上账。
    """
    assert money.round_cents(2.5) == 3
    assert money.round_cents(3.5) == 4
    assert money.round_cents(0.5) == 1
    assert money.round_cents(-2.5) == -3
    assert money.round_cents(0.0) == 0


def test_to_cents():
    assert money.to_cents(1.00) == 100
    assert money.to_cents(12.34) == 1234
    assert money.to_cents(0.00) == 0


def test_to_yuan():
    assert money.to_yuan(1234) == 12.34
    assert money.to_yuan(0) == 0.0


def test_format_cents():
    assert money.format_cents(0) == "¥0.00"
    assert money.format_cents(100) == "¥1.00"
    assert money.format_cents(1234) == "¥12.34"
    assert money.format_cents(5) == "¥0.05"


def test_format_cents_negative():
    assert money.format_cents(-5) == "-¥0.05"
    assert money.format_cents(-1234) == "-¥12.34"


def test_split_cents():
    assert money.split_cents(1234) == (12, 34)
    assert money.split_cents(100) == (1, 0)
    assert money.split_cents(-1234) == (-12, 34)
