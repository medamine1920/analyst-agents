from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parents[1] / "warehouse" / "analyst.duckdb"

QUERIES = {
    "orders columns": "describe raw_orders",
    "items columns": "describe raw_items",
    "products columns": "describe raw_products",
    "stores columns": "describe raw_stores",
    "customers columns": "describe raw_customers",
    "row counts": """
        select 'orders' as tbl, count(*) as n from raw_orders
        union all select 'items', count(*) from raw_items
        union all select 'customers', count(*) from raw_customers
        union all select 'products', count(*) from raw_products
        union all select 'stores', count(*) from raw_stores
    """,
    "order date range": "select min(ordered_at), max(ordered_at) from raw_orders",
    "subtotal + tax != total": """
        select count(*) as mismatches
        from raw_orders
        where subtotal + tax_paid != order_total
    """,
    "null keys in orders": """
        select count(*) as null_keys
        from raw_orders
        where customer is null or store_id is null
    """,
    "product types": "select type, count(*) as n from raw_products group by type",
}


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        for title, sql in QUERIES.items():
            print(f"\n== {title} ==")
            con.sql(sql).show()
    finally:
        con.close()


if __name__ == "__main__":
    main()