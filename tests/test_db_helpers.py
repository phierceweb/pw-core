"""Tests for pw_core.db.helpers."""

import json

from pw_core.db.helpers import coerce_json_col, dumps_json, row_to_dict


def test_coerce_json_col_none():
    assert coerce_json_col(None) == []


def test_coerce_json_col_list_passthrough():
    assert coerce_json_col([1, 2, 3]) == [1, 2, 3]


def test_coerce_json_col_json_string():
    assert coerce_json_col('[{"a": 1}]') == [{"a": 1}]


def test_coerce_json_col_empty_string():
    assert coerce_json_col("") == []


def test_coerce_json_col_iterable():
    assert coerce_json_col((1, 2)) == [1, 2]


def test_coerce_json_col_json_object_string():
    """A JSON object string wraps into a single-element list."""
    assert coerce_json_col('{"a": 1}') == [{"a": 1}]


def test_dumps_json_no_ascii_escape():
    result = dumps_json({"name": "Müller"})
    assert "Müller" in result
    parsed = json.loads(result)
    assert parsed["name"] == "Müller"


def test_row_to_dict_with_mapping():
    assert row_to_dict({"a": 1, "b": 2}) == {"a": 1, "b": 2}


def test_row_to_dict_none():
    assert row_to_dict(None) is None
