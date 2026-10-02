"""Integration tests: the semantic layer must return numbers that agree with each other.

These run after `dbt build`, because they query the real DuckDB warehouse.
"""

import pytest

from app.semantic.layer import SemanticLayer

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def layer() -> SemanticLayer:
    return SemanticLayer()


def total(layer: SemanticLayer, metric: str) -> float:
    return layer.query([metric])["rows"][0][0]


def test_all_metrics_are_registered(layer):
    names = {metric["name"] for metric in layer.list_metrics()}
    assert len(names) == 12
    assert {"revenue_pre_tax", "revenue_with_tax", "cumulative_revenue", "store_count"} <= names


def test_revenue_search_surfaces_both_definitions(layer):
    names = [metric["name"] for metric in layer.search_metrics("revenue")]
    assert {"revenue_pre_tax", "revenue_with_tax"} <= set(names)


def test_every_customer_has_exactly_one_first_order(layer):
    assert total(layer, "new_customers") == total(layer, "active_customers")


def test_item_list_prices_explain_pre_tax_revenue(layer):
    assert total(layer, "item_revenue") == pytest.approx(total(layer, "revenue_pre_tax"))


def test_revenue_with_tax_is_pre_tax_plus_tax(layer):
    expected = total(layer, "revenue_pre_tax") + total(layer, "tax_collected")
    assert total(layer, "revenue_with_tax") == pytest.approx(expected)


def test_cumulative_revenue_ends_at_total_revenue(layer):
    last_date = layer.data_time_range()["last_date"]
    result = layer.query(
        ["cumulative_revenue"],
        group_by=["metric_time__month"],
        end_time=last_date,
        order_by=["metric_time__month"],
    )
    assert result["rows"][-1][1] == pytest.approx(total(layer, "revenue_pre_tax"))


def test_order_revenue_cannot_be_split_by_product(layer):
    # MetricFlow refuses this join: it would fan out order totals across items.
    with pytest.raises(Exception, match="group-by-items"):
        layer.query(["revenue_pre_tax"], group_by=["product__product_type"])


def test_unknown_metric_gives_a_helpful_error(layer):
    with pytest.raises(ValueError, match="search_metrics"):
        layer.query(["revenue"])


def test_store_count_includes_stores_without_orders(layer):
    assert total(layer, "store_count") == 6
    result = layer.query(["store_count", "order_count"], group_by=["store__store_name"])
    without_orders = sorted(row[0] for row in result["rows"] if row[2] is None)
    assert without_orders == ["Chicago", "Los Angeles", "New Orleans", "San Francisco"]
