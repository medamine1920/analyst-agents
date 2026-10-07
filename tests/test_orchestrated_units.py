"""Unit tests for the orchestrated agent's deterministic parts: no database or model needed."""

import pytest

from app.agents.orchestrated.analysis import compare, evaluate
from app.agents.orchestrated.glossary import resolve_terms
from app.agents.orchestrated.plan import Plan, QueryStep, validate_plan
from app.agents.orchestrated.review import review_answer


def metrics_of(items):
    return [item["metrics"] for item in items]


def test_ambiguous_term_is_detected():
    result = resolve_terms("Which store had the higher income?")
    assert metrics_of(result["ambiguities"]) == [["revenue_pre_tax", "revenue_with_tax"]]


def test_qualifiers_resolve_the_term():
    assert metrics_of(resolve_terms("What was revenue including tax for Brooklyn?")["resolved"]) == [["revenue_with_tax"]]
    assert metrics_of(resolve_terms("Pre-tax revenue jumped in March.")["resolved"]) == [["revenue_pre_tax"]]


def test_longer_phrase_wins():
    result = resolve_terms("How much item revenue came from beverages?")
    assert result["ambiguities"] == [] and metrics_of(result["resolved"]) == [["item_revenue"]]


def test_expressions_are_arithmetic_only():
    assert evaluate("a / b", {"a": 8478.72, "b": 211977.0}) == pytest.approx(0.04, abs=1e-4)
    assert evaluate("a / b", {"a": 1.0, "b": 0.0}) is None
    for unsafe in ("__import__('os')", "a.real", "[a]", "a ** 2"):
        with pytest.raises(ValueError):
            evaluate(unsafe, {"a": 1.0})


def test_new_group_counts_from_zero_in_contributions():
    step = QueryStep(id="feb", purpose="", metrics=["revenue_pre_tax"], group_by=["store__store_name"])
    before = [{"store__store_name": "Philadelphia", "revenue_pre_tax": 37023.0}]
    after = [{"store__store_name": "Philadelphia", "revenue_pre_tax": 44396.0},
             {"store__store_name": "Brooklyn", "revenue_pre_tax": 19610.0}]
    rows = {r["store__store_name"]: r for r in compare(step, before, after, {"revenue_pre_tax"})}
    assert rows["Brooklyn"]["change"] == 19610.0
    assert rows["Brooklyn"]["share_of_total_change_pct"] == pytest.approx(72.68, abs=0.01)


def test_plan_validation_names_the_problem():
    plan = Plan(question_type="lookup", steps=[
        QueryStep(id="a", purpose="", metrics=["revenue"]),
        QueryStep(id="b", purpose="", metrics=["revenue_pre_tax"], group_by=["product__product_type"]),
    ])
    errors = validate_plan(plan, {"revenue_pre_tax"}, lambda metrics: ["store__store_name", "metric_time__day"])
    assert any("unknown metric" in e for e in errors)
    assert any("product__product_type" in e and "store__store_name" in e for e in errors)


CATALOG = {"revenue_pre_tax": {"label": "Revenue (pre-tax)"}, "revenue_with_tax": {"label": "Revenue (with tax)"}}
FACTS = {"steps": [{"rows": [{"revenue_pre_tax": 425467.0, "revenue_with_tax": 450969.65, "rate": 0.0599}]}]}
AMBIGUOUS = [{"term": "income", "metrics": ["revenue_pre_tax", "revenue_with_tax"]}]


def test_review_flags_numbers_not_in_the_facts():
    issues = review_answer("Revenue was 425,467, up 12.5% on last year.", FACTS, "q", [], CATALOG)
    assert issues and "12.5" in issues[0]


def test_review_accepts_roundings_and_percentages():
    answer = "Philadelphia: 425,467 pre-tax, 450,969.65 with tax, an effective rate of 5.99% in 2025."
    assert review_answer(answer, FACTS, "q", [], CATALOG) == []


def test_review_requires_every_definition_of_an_ambiguous_term():
    issues = review_answer("Philadelphia earned 425,467 (Revenue (pre-tax)).", FACTS, "q", AMBIGUOUS, CATALOG)
    assert any("Revenue (with tax)" in issue for issue in issues)


def test_full_range_dates_are_dropped_and_store_counts_are_never_date_filtered():
    from app.agents.orchestrated.plan import normalize_dates

    plan = Plan(question_type="listing", steps=[
        QueryStep(id="full", purpose="", metrics=["order_count"], start_date="2024-09-01", end_date="2025-08-31"),
        QueryStep(id="july", purpose="", metrics=["store_count", "order_count"], group_by=["store__store_name"],
                  start_date="2025-07-01", end_date="2025-07-31"),
    ])
    plan = normalize_dates(plan, "2024-09-01", "2025-08-31")
    steps = {s.id: s for s in plan.steps}
    assert steps["full"].start_date is None and steps["full"].end_date is None
    assert steps["july"].metrics == ["order_count"] and steps["july"].start_date == "2025-07-01"
    assert steps["july_all_time"].metrics == ["store_count"] and steps["july_all_time"].start_date is None


def test_cross_step_formula_is_rejected_at_planning_time_with_a_pointer_to_comparisons():
    """Reproduces eval error m09: a formula mixing two steps crashed the run."""
    from app.agents.orchestrated.plan import Calculation

    plan = Plan(question_type="comparison", steps=[
        QueryStep(id="q1_2025", purpose="", metrics=["revenue_pre_tax"], start_date="2025-01-01", end_date="2025-03-31"),
        QueryStep(id="q2_2025", purpose="", metrics=["revenue_pre_tax"], start_date="2025-04-01", end_date="2025-06-30"),
    ], calculations=[Calculation(name="growth", step_id="q2_2025",
                                 expression="q2_2025.revenue_pre_tax / q1_2025.revenue_pre_tax - 1")])
    errors = validate_plan(plan, {"revenue_pre_tax"}, lambda metrics: ["metric_time__day"])
    assert any("add a comparison" in e for e in errors)
