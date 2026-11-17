"""评测比较器：数值容差不能粗到把口径错误放过。"""
from __future__ import annotations

from eval import run_eval


def test_ratio_error_is_not_masked() -> None:
    # 0.63% vs 1.24% 是项目最想区分的口径差异，必须判为不等。
    assert not run_eval._values_equal(0.006285, 0.012409)


def test_float_rounding_noise_is_tolerated() -> None:
    assert run_eval._values_equal(945456.2900000019, 945456.29)


def test_unordered_rows_match_by_value() -> None:
    gold = [("SP", 1.0), ("RJ", 2.0)]
    actual = [("RJ", 2.0), ("SP", 1.0)]
    assert run_eval._rows_equal(gold, actual, ordered=False)


def test_ordered_series_keeps_row_order() -> None:
    gold = [("2017-01", 1.0), ("2017-02", 2.0)]
    actual = [("2017-02", 2.0), ("2017-01", 1.0)]
    assert not run_eval._rows_equal(gold, actual, ordered=True)
    assert run_eval._rows_equal(gold, actual, ordered=False)


def test_compare_uses_tolerance(monkeypatch) -> None:
    class FakeResult:
        rows = [(0.006285,)]

    monkeypatch.setattr(run_eval.duck, "run_sql", lambda sql, params: FakeResult())
    assert run_eval.compare("SELECT 0.006285", [[0.006285]])
    assert not run_eval.compare("SELECT 0.006285", [[0.012409]])
