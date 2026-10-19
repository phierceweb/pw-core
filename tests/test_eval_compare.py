"""Tests for pw_core.eval._compare (comparator registry + structured_diff)."""

from __future__ import annotations

import pytest

from pw_core.eval._compare import (
    _field_score,
    get_comparator,
    list_comparators,
    register_comparator,
    split_comparator_result,
    structured_diff,
)
from pw_foundation.exceptions import ConfigurationError, InvalidInputError


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_structured_diff_registered():
    assert "structured_diff" in list_comparators()


def test_get_comparator_by_name():
    fn = get_comparator("structured_diff")
    assert callable(fn)


def test_get_comparator_custom_prefix():
    @register_comparator("my_custom")
    def _my(golden, replay, *, context):
        return 0.5

    fn = get_comparator("custom:my_custom")
    assert fn is _my

    fn2 = get_comparator("my_custom")
    assert fn2 is _my


def test_get_comparator_unknown():
    with pytest.raises(ConfigurationError, match="Unknown comparator"):
        get_comparator("no_such_comparator_xyz")


# ---------------------------------------------------------------------------
# _field_score
# ---------------------------------------------------------------------------


def test_field_score_both_none():
    assert _field_score(None, None, tolerance=None) == 1.0


def test_field_score_one_none():
    assert _field_score(1.0, None, tolerance=None) == 0.0
    assert _field_score(None, 1.0, tolerance=None) == 0.0


def test_field_score_type_mismatch():
    assert _field_score("hello", 42, tolerance=None) == 0.0


def test_field_score_exact_string():
    assert _field_score("foo", "foo", tolerance=None) == 1.0
    assert _field_score("foo", "bar", tolerance=None) == 0.0


def test_field_score_numeric_exact():
    assert _field_score(3.14, 3.14, tolerance=None) == 1.0
    assert _field_score(3.14, 3.15, tolerance=None) == 0.0


def test_field_score_numeric_with_tolerance():
    assert _field_score(10.0, 10.0, tolerance=2.0) == 1.0
    assert _field_score(10.0, 11.5, tolerance=2.0) == 1.0  # within tolerance
    score = _field_score(10.0, 15.0, tolerance=2.0)
    assert 0.0 <= score < 1.0


def test_field_score_int_float_coercion():
    assert _field_score(10, 10.0, tolerance=None) == 1.0
    assert _field_score(10.0, 10, tolerance=None) == 1.0


def test_field_score_int_int_with_tolerance():
    """LLM JSON numbers usually parse as ints — tolerance must apply to them."""
    assert _field_score(85, 87, tolerance=2.0) == 1.0
    score = _field_score(85, 95, tolerance=2.0)
    assert 0.0 <= score < 1.0


def test_field_score_bools_ignore_tolerance():
    """bool is an int subclass — tolerance must never make True ≈ False."""
    assert _field_score(True, False, tolerance=2.0) == 0.0
    assert _field_score(True, True, tolerance=2.0) == 1.0


def test_field_score_bool_vs_float_not_coerced():
    """The int↔float coercion must not make True ≈ 1.0 (bools compare exact)."""
    assert _field_score(True, 1.0, tolerance=None) == 0.0
    assert _field_score(False, 0.0, tolerance=None) == 0.0
    assert _field_score(1.0, True, tolerance=None) == 0.0
    assert _field_score(True, 1, tolerance=None) == 0.0


def test_field_score_list_iou():
    assert _field_score(["a", "b", "c"], ["a", "b", "c"], tolerance=None) == 1.0
    assert _field_score(["a", "b"], ["a", "b", "c"], tolerance=None) == pytest.approx(2 / 3)
    assert _field_score(["a"], ["z"], tolerance=None) == 0.0
    assert _field_score([], [], tolerance=None) == 1.0


# ---------------------------------------------------------------------------
# structured_diff
# ---------------------------------------------------------------------------


def test_structured_diff_perfect_match():
    golden = {"category": "analysis", "confidence": 0.9}
    replay = {"category": "analysis", "confidence": 0.9}
    score = structured_diff(golden, replay, context={})
    assert score == 1.0


def test_structured_diff_partial_match():
    golden = {"a": "x", "b": "y"}
    replay = {"a": "x", "b": "z"}
    score = structured_diff(golden, replay, context={})
    assert score == pytest.approx(0.5)


def test_structured_diff_specific_fields():
    golden = {"score": 85.0, "category": "analysis", "ignored": "foo"}
    replay = {"score": 85.0, "category": "wrong", "ignored": "bar"}
    score = structured_diff(golden, replay, context={"diff_fields": ["score"]})
    assert score == 1.0


def test_structured_diff_with_tolerance():
    golden = {"score": 85.0}
    replay = {"score": 87.0}
    score_exact = structured_diff(golden, replay, context={"diff_fields": ["score"]})
    assert score_exact == 0.0

    score_tolerant = structured_diff(
        golden, replay, context={"diff_fields": ["score"], "tolerances": {"score": 3.0}}
    )
    assert score_tolerant == 1.0


def test_structured_diff_int_tolerance():
    golden = {"score": 85}
    replay = {"score": 87}
    score = structured_diff(
        golden, replay, context={"diff_fields": ["score"], "tolerances": {"score": 2}}
    )
    assert score == 1.0


def test_structured_diff_missing_field_in_replay():
    golden = {"a": "x", "b": "y"}
    replay = {"a": "x"}  # b missing
    score = structured_diff(golden, replay, context={})
    assert score == pytest.approx(0.5)


def test_structured_diff_empty_golden():
    score = structured_diff({}, {}, context={})
    assert score == 1.0


def test_structured_diff_via_get_comparator():
    fn = get_comparator("structured_diff")
    score = fn({"x": 1}, {"x": 1}, context={})
    assert score == 1.0


def test_structured_diff_field_absent_from_both_scores_zero():
    """A typo in diff_fields must not certify a wrong replay as perfect."""
    golden = {"title": "A", "score": 10}
    replay = {"title": "X", "score": -999}
    score = structured_diff(
        golden, replay, context={"diff_fields": ["title", "titel"], "tolerances": {}}
    )
    assert score == pytest.approx(0.0)


def test_structured_diff_explicit_null_on_both_sides_matches():
    golden = {"note": None, "title": "A"}
    replay = {"note": None, "title": "A"}
    score = structured_diff(golden, replay, context={"diff_fields": ["note", "title"]})
    assert score == 1.0


def test_structured_diff_field_absent_from_golden_present_in_replay():
    golden = {"title": "A"}
    replay = {"title": "A", "extra": "surprise"}
    score = structured_diff(golden, replay, context={"diff_fields": ["title", "extra"]})
    assert score == pytest.approx(0.5)


def test_structured_diff_all_diff_fields_missing_from_golden():
    with pytest.raises(ConfigurationError, match="diff_fields"):
        structured_diff({"title": "A"}, {"title": "X"}, context={"diff_fields": ["titel", "scoer"]})


# ---------------------------------------------------------------------------
# split_comparator_result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("score", [0, 1, True, 0.25, "0.5"])
def test_split_comparator_result_accepts_a_score_in_0_to_1(score):
    assert split_comparator_result(score) == (float(score), {})


def test_split_comparator_result_keeps_finite_metrics_of_any_size():
    assert split_comparator_result((0.25, {"n_items": 40})) == (0.25, {"n_items": 40.0})


@pytest.mark.parametrize(
    "score", [85.0, 1.0001, -0.1, float("inf"), float("-inf"), float("nan"), "high", None]
)
def test_split_comparator_result_rejects_a_score_outside_0_to_1(score):
    with pytest.raises(InvalidInputError, match="comparator score"):
        split_comparator_result(score)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "many", None])
def test_split_comparator_result_rejects_a_metric_that_is_not_a_finite_number(value):
    with pytest.raises(InvalidInputError, match="field_ratio"):
        split_comparator_result((0.5, {"field_ratio": value}))


def test_split_comparator_result_clamps_float_rounding_at_a_bound():
    total = 0.0
    for weight in (0.65, 0.2, 0.05, 0.1):
        total += weight * 1.0
    assert total > 1.0
    assert split_comparator_result(total) == (1.0, {})
    assert split_comparator_result(-1e-12) == (0.0, {})


@pytest.mark.parametrize(
    "result", [10**400, (0.5, {"n": 10**400}), (0.5,), (0.5, {}, "extra"), (0.5, [1, 2])]
)
def test_split_comparator_result_raises_invalid_input_for_any_malformed_result(result):
    with pytest.raises(InvalidInputError, match="comparator"):
        split_comparator_result(result)
