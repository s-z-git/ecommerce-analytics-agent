"""从当前 metrics.yaml 重算文档里的 5 个参考数字。

用途：避免文档抄旧快照/不同切片，现场跑出来的数和 README 对不上。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.exec import duck, guard  # noqa: E402
from src.plan.schema import QueryPlan  # noqa: E402
from src.semantic import compiler  # noqa: E402


def metric_value(metric_id: str):
    compiled = compiler.compile_plan(QueryPlan(metrics=[metric_id]))
    return duck.run_sql(guard.check_sql(compiled.sql), compiled.params).rows


def main() -> None:
    gmv = metric_value("gmv")[0][0]
    freight_sql = """
        SELECT SUM(oi.price + oi.freight_value)
        FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
        WHERE o.order_status NOT IN ('canceled', 'unavailable')
    """
    gmv_with_freight = duck.run_sql(guard.check_sql(freight_sql), []).rows[0][0]
    freight_increase = gmv_with_freight / gmv - 1

    cancel = metric_value("cancel_rate")[0][0]
    delay = metric_value("delay_rate")[0][0]
    repeat = metric_value("repeat_rate")[0][0]
    payments = metric_value("payment_share")
    pay = {row[0]: row[1] for row in payments}

    print("| 口径 | 当前值 |")
    print("|---|---:|")
    print(f"| GMV 含运费增幅（有效订单） | {freight_increase:.2%} |")
    print(f"| 取消率（canceled / 全部订单） | {cancel:.2%} |")
    print(f"| 延迟率（已送达） | {delay:.2%} |")
    print(f"| 复购率（有有效订单客户） | {repeat:.2%} |")
    print(f"| 支付占比-信用卡（按金额） | {pay.get('credit_card', 0):.2%} |")
    print(f"| 支付占比-boleto（按金额） | {pay.get('boleto', 0):.2%} |")


if __name__ == "__main__":
    main()
