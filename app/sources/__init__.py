"""Data sources users add themselves: CSV files, database files, or live databases attached read-only.

Each source becomes a small governed semantic layer of its own (a generated dbt + MetricFlow project
over a DuckDB workspace), so the agent asks it questions exactly as it does the sample shop.
"""
