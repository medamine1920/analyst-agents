"""Unit tests for the eval scorer: pure logic, no database or model needed."""

from evals.scoring import numbers_in, score


def test_numbers_are_parsed_with_separators_and_decimals():
    assert numbers_in("Revenue was $220,455.72 from 61,948 orders (98.5%).") == [220455.72, 61948.0, 98.5]


def test_number_check_respects_tolerance():
    assert score("about 10.29 per order", [{"number": 10.29, "rel_tol": 0.005}])[0]
    assert not score("about 10.50 per order", [{"number": 10.29, "rel_tol": 0.005}])[0]


def test_number_any_accepts_fraction_or_percent():
    checks = [{"number_any": [0.9849, 98.49], "rel_tol": 0.005}]
    assert score("98.49% of orders", checks)[0]
    assert score("a share of 0.985", checks)[0]


def test_every_check_must_pass():
    checks = [{"text_any": ["philadelphia"]}, {"number": 425467}]
    assert score("Philadelphia led with 425,467.", checks)[0]
    passed, failed = score("Philadelphia led.", checks)
    assert not passed and failed == [{"number": 425467}]


def test_typographic_characters_are_normalized():
    answer = "No sales at Chicago, Los\u00a0Angeles, New\u202fOrleans and San\u00a0Francisco; pre\u2011tax fell by \u22121,619."
    checks = [{"text_any": ["los angeles"]}, {"text_any": ["new orleans"]}, {"text_any": ["san francisco"]},
              {"text_any": ["pre-tax"]}, {"number": 1619}]
    assert score(answer, checks) == (True, [])
