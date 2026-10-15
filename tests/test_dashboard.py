"""The dashboard: which numbers it shows for a source, and that they are right (checked with raw SQL)."""

import shutil
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.api.dashboard import breakdown_dimensions, grain_for, tile_metrics
from app.api.main import create_app
from app.mcp_server.server import server

WAREHOUSE = Path(__file__).resolve().parents[1] / "warehouse" / "analyst.duckdb"


def test_the_headline_numbers_are_the_ones_a_sales_analyst_reads_first():
    generated = ["sales_count", "sales_average_order_value", "sales_gross_margin", "sales_amount_total",
                 "sales_order_id_distinct", "sales_customer_id_distinct", "sales_cost_total"]
    assert tile_metrics({name: {} for name in generated}) == [
        "sales_amount_total", "sales_order_id_distinct", "sales_average_order_value", "sales_customer_id_distinct"]
    assert tile_metrics({"readings_count": {}, "readings_level_total": {}}) == ["readings_count"]
    assert tile_metrics({"cumulative_revenue": {"type": "cumulative"}, "revenue": {}})[0] == "revenue"
    assert breakdown_dimensions(["metric_time__week", "sales__order_date__month", "sales__product", "sales__region",
                                 "sales__placed_at"]) == ["sales__region", "sales__product"]
    assert grain_for("2024-09-01", "2025-08-31") == "week" and grain_for("2023-01-01", "2025-08-31") == "month"


@pytest.mark.integration
def test_the_sample_dashboard_matches_the_warehouse(tmp_path):
    app = create_app(mcp_target=server, traces_dir=tmp_path / "traces", checkpoint_db=tmp_path / "c.sqlite",
                     results_dir=tmp_path, auth=False, security_db=tmp_path / "console.sqlite")
    with TestClient(app) as client:
        board = client.get("/api/dashboard").json()
    assert board["live"] is False and board["window"] == {"from": "2025-08-02", "to": "2025-08-31", "days": 30}
    tiles = {t["metric"]: t for t in board["tiles"]}
    assert list(tiles) == ["revenue_pre_tax", "order_count", "average_order_value", "active_customers"]

    shutil.copy(WAREHOUSE, tmp_path / "w.duckdb")  # the in-process MCP server holds the original
    with duckdb.connect(str(tmp_path / "w.duckdb"), read_only=True) as db:
        def sums(start, end):
            return db.execute("select sum(subtotal), count(*), count(distinct customer_id) from fct_orders "
                              "where cast(ordered_at as date) between ? and ?", [start, end]).fetchone()
        revenue, orders, customers = sums("2025-08-02", "2025-08-31")
        before = sums("2025-07-03", "2025-08-01")
    assert tiles["revenue_pre_tax"]["value"] == pytest.approx(float(revenue))
    assert tiles["order_count"]["value"] == orders and tiles["active_customers"]["value"] == customers
    assert tiles["average_order_value"]["value"] == pytest.approx(float(revenue) / orders)
    assert tiles["revenue_pre_tax"]["change_pct"] == round(100 * (float(revenue) - float(before[0])) / float(before[0]), 1)

    assert board["grain"] == "week" and len(board["trend"]["points"]) >= 52
    stores = board["breakdowns"][0]
    assert stores["dimension"] == "store__store_name" and stores["points"][0]["x"] == "Philadelphia"
    assert sum(p["share_pct"] for p in stores["points"]) == pytest.approx(100, abs=0.2)


def test_periods_without_data_are_shown_not_skipped():
    """Reproduces a live trend that went from June 2025 straight to October 2026: the empty months were
    missing, so the line hid the gap. Totals count an empty period as 0; averages leave it empty."""
    from app.api.dashboard import fill_periods

    points = [{"x": "2025-05-01T00:00:00", "value": 10.0}, {"x": "2025-08-01T00:00:00", "value": 4.0}]
    filled = fill_periods(points, "2025-05-14", "2025-08-02", "month", additive=True)
    assert [(p["x"][:10], p["value"]) for p in filled] == [
        ("2025-05-01", 10.0), ("2025-06-01", 0), ("2025-07-01", 0), ("2025-08-01", 4.0)]
    weeks = fill_periods([], "2025-03-05", "2025-03-18", "week", additive=False)
    assert [(p["x"][:10], p["value"]) for p in weeks] == [("2025-03-03", None), ("2025-03-10", None), ("2025-03-17", None)]
