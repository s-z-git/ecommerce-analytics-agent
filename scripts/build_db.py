"""把 Olist 的 CSV 建成一个 DuckDB 单文件库。

为什么要显式写列类型：Olist 的时间字段在 CSV 里是字符串，
让 DuckDB 自动推断会变成 VARCHAR，后续 date_diff 全部报错。
显式 CAST 也让"数据字典"和"建库脚本"是同一份事实来源。
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import DB_PATH, RAW_DIR  # noqa: E402

# 表名 -> (CSV 文件名, 建表 SELECT)
TABLES: dict[str, tuple[str, str]] = {
    "orders": ("olist_orders_dataset.csv", """
        SELECT order_id, customer_id, order_status,
               CAST(order_purchase_timestamp AS TIMESTAMP)     AS order_purchase_timestamp,
               CAST(order_approved_at AS TIMESTAMP)            AS order_approved_at,
               CAST(order_delivered_carrier_date AS TIMESTAMP) AS order_delivered_carrier_date,
               CAST(order_delivered_customer_date AS TIMESTAMP) AS order_delivered_customer_date,
               CAST(order_estimated_delivery_date AS TIMESTAMP) AS order_estimated_delivery_date
        FROM read_csv_auto(?)
    """),
    "order_items": ("olist_order_items_dataset.csv", """
        SELECT order_id, order_item_id, product_id, seller_id,
               CAST(shipping_limit_date AS TIMESTAMP) AS shipping_limit_date,
               CAST(price AS DOUBLE)         AS price,
               CAST(freight_value AS DOUBLE) AS freight_value
        FROM read_csv_auto(?)
    """),
    "products": ("olist_products_dataset.csv", """
        SELECT product_id, product_category_name,
               CAST(product_name_lenght AS INTEGER) AS product_name_lenght,
               CAST(product_description_lenght AS INTEGER) AS product_description_lenght,
               CAST(product_photos_qty AS INTEGER) AS product_photos_qty
        FROM read_csv_auto(?)
    """),
    "customers": ("olist_customers_dataset.csv", """
        SELECT customer_id, customer_unique_id, customer_zip_code_prefix,
               customer_city, customer_state
        FROM read_csv_auto(?)
    """),
    "order_payments": ("olist_order_payments_dataset.csv", """
        SELECT order_id, CAST(payment_sequential AS INTEGER) AS payment_sequential,
               payment_type,
               CAST(payment_installments AS INTEGER) AS payment_installments,
               CAST(payment_value AS DOUBLE) AS payment_value
        FROM read_csv_auto(?)
    """),
    "order_reviews": ("olist_order_reviews_dataset.csv", """
        SELECT review_id, order_id, CAST(review_score AS INTEGER) AS review_score,
               review_comment_title, review_comment_message,
               CAST(review_creation_date AS TIMESTAMP) AS review_creation_date,
               CAST(review_answer_timestamp AS TIMESTAMP) AS review_answer_timestamp
        FROM read_csv_auto(?)
    """),
}


def main() -> None:
    missing = [f for f, _ in TABLES.values() if not (RAW_DIR / f).exists()]
    if missing:
        print("缺少 CSV：" + ", ".join(missing))
        print(f"请把 Olist 的 6 个 CSV 放到 {RAW_DIR}")
        raise SystemExit(1)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    for table, (filename, select_sql) in TABLES.items():
        path = str(RAW_DIR / filename)
        con.execute(f"CREATE OR REPLACE TABLE {table} AS {select_sql}", [path])
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table:<16} {count:>8} 行")

    as_of = con.execute("SELECT MAX(order_purchase_timestamp) FROM orders").fetchone()[0]
    print(f"\n数据截止：{as_of}")
    print(f"数据库：{DB_PATH}")
    con.close()


if __name__ == "__main__":
    main()
