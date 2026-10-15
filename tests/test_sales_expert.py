"""The sales expert: recognising measures, explaining sales words to the planner, choosing charts,
knowing which way is good, and suggesting follow-ups. Pure code, no model."""

import pytest

from app.agents.sales_expert.expert import (
    advise,
    choose_chart,
    measure_of,
    planner_notes,
    writer_notes,
)

CATALOG = {
    "revenue_pre_tax": {"type": "simple", "label": "Revenue (pre-tax)", "group_by": ["store__store_name"]},
    "order_count": {"type": "simple", "label": "Orders", "group_by": ["store__store_name"]},
    "average_order_value": {"type": "ratio", "label": "Average order value (pre-tax)", "group_by": []},
    "active_customers": {"type": "simple", "label": "Active customers", "group_by": []},
    "sales_cost_total": {"type": "simple", "label": "sales: total cost", "group_by": []},
    "tax_collected": {"type": "simple", "label": "Tax collected", "group_by": []},
}


@pytest.mark.parametrize(("metric", "measure"), [
    ("revenue_pre_tax", "revenue"), ("revenue_with_tax", "revenue"), ("tax_collected", "tax"),
    ("order_count", "orders"), ("sales_order_id_distinct", "orders"), ("sales_customer_id_distinct", "customers"),
    ("average_order_value", "average_order_value"), ("sales_gross_margin", "gross_margin"),
    ("sales_cost_total", "cost"), ("sales_discount_rate", "discount_rate"), ("sales_unit_price_average", "average_selling_price"),
])
def test_measures_are_recognised_by_name(metric, measure):
    assert measure_of(metric)["id"] == measure
    assert measure_of("rfam_family_count") is None  # not a sales measure: no advice rather than wrong advice


def test_sales_words_in_a_question_are_explained_to_the_planner():
    notes = planner_notes("What was the average basket in Brooklyn compared with the panier moyen in Philly?", CATALOG)
    assert '"average basket" is average order value' in notes and "Measured here by average_order_value." in notes
    assert planner_notes("How many stores opened?", {"order_count": {}}) == ""  # no sales word, no extra tokens
    missing = planner_notes("What is our gross margin?", {"revenue": {"type": "simple"}})
    assert "compute it with a calculation" in missing and "(revenue - cost) / revenue" in missing


def test_each_kind_of_result_gets_the_chart_that_answers_it():
    rows = [{"store__store_name": s, "revenue_pre_tax": v} for s, v in (("A", 5.0), ("B", 3.0), ("C", 1.0))]
    assert choose_chart([], [], "revenue_pre_tax", "lookup", CATALOG)[0] == "tiles"
    assert choose_chart(["store__store_name"], rows, "revenue_pre_tax", "ranking", CATALOG)[0] == "bar"
    assert choose_chart(["store__store_name"], rows, "revenue_pre_tax", "share", CATALOG)[0] == "treemap"
    assert choose_chart(["store__store_name"], rows, "average_order_value", "share", CATALOG)[0] == "bar"  # no shares of a ratio
    assert choose_chart(["metric_time__month"], [], "revenue_pre_tax", "lookup", CATALOG)[0] == "area"
    assert choose_chart(["metric_time__month"], [], "average_order_value", "lookup", CATALOG)[0] == "line"
    many = [{"metric_time__month": "2025-01-01", "product": p} for p in "ABCDEF"]
    assert choose_chart(["metric_time__month", "product"], many, "revenue_pre_tax", "lookup", CATALOG)[0] == "heatmap"
    assert choose_chart(["metric_time__month", "product"], many[:3], "revenue_pre_tax", "share", CATALOG)[0] == "stacked_area"


def test_advice_carries_direction_definitions_and_follow_ups():
    steps = [{"id": "s", "metrics": ["revenue_pre_tax", "sales_cost_total"], "group_by": ["store__store_name"]}]
    facts = {"steps": [{"id": "s", "rows": []}], "comparisons": [
        {"before": "feb", "after": "mar", "rows": [
            {"store__store_name": "A", "metric": "revenue_pre_tax", "before": 1, "after": 3, "change": 2},
            {"store__store_name": "B", "metric": "revenue_pre_tax", "before": 2, "after": 1, "change": -1}]}]}
    advice = advise("Which store sold the most?", "ranking", steps, facts, CATALOG)
    direction = {d["metric"]: d["higher_is_better"] for d in advice["definitions"]}
    assert direction == {"revenue_pre_tax": True, "sales_cost_total": False}  # costs going up is bad news
    assert advice["charts"]["s"]["kind"] == "bar" and advice["charts"]["feb->mar"]["kind"] == "waterfall"
    assert "Which store contributed most to the change in revenue (pre-tax)?" in advice["follow_ups"]
    both = [{"id": "s", "metrics": ["revenue_pre_tax", "revenue_with_tax"], "group_by": ["store__store_name"]}]
    catalog = {**CATALOG, "revenue_with_tax": {"type": "simple", "label": "Revenue (with tax)"}}
    questions = advise("Revenue by store?", "ranking", both, {"steps": []}, catalog)["follow_ups"]
    assert not any("with tax" in q for q in questions)  # one revenue, not one per definition
    notes = writer_notes(["sales_cost_total", "tax_collected"], CATALOG)
    assert "lower is better" in notes["sales_cost_total"] and "neither" in notes["tax_collected"]
