"""Tests for pf_core.utils.yaml — YAML loading that refuses repeated mapping keys."""

from __future__ import annotations

import yaml
import pytest

from pf_core.utils.yaml import UniqueKeyLoader, safe_load


def test_repeated_top_level_key_raises():
    with pytest.raises(yaml.YAMLError, match="found duplicate key 'summarizer'"):
        safe_load("summarizer: {model: a}\nclassifier: {model: b}\nsummarizer: {model: c}\n")


def test_repeated_nested_key_raises_with_its_line():
    text = "agents:\n  summarizer:\n    model: a\n    model: b\n"
    with pytest.raises(yaml.YAMLError) as excinfo:
        safe_load(text)
    assert excinfo.value.problem_mark.line + 1 == 4


def test_keys_that_collapse_into_one_dict_entry_are_repeats():
    with pytest.raises(yaml.YAMLError, match="found duplicate key"):
        safe_load("true: a\n1: b\n")


def test_overriding_a_merged_key_is_not_a_repeat():
    text = "base: &base {model: a, temperature: 0.2}\nchild:\n  <<: *base\n  model: b\n"
    assert safe_load(text)["child"] == {"model": "b", "temperature": 0.2}


def test_reusing_an_anchored_mapping_that_merges_is_not_a_repeat():
    # PyYAML flattens a merged mapping in place; the second use must not read
    # the merged-in keys as repeats of the mapping's own.
    text = (
        "base: &base {model: a, temperature: 0.2}\n"
        "first:\n  <<: &mid\n    <<: *base\n    model: b\n"
        "second: *mid\n"
    )
    assert safe_load(text) == yaml.safe_load(text)


def test_matches_safe_load_when_no_key_repeats():
    text = (
        "defaults: &d {pass_threshold: 0.8, compare: structured_diff}\n"
        "agents:\n"
        "  summarizer:\n    <<: *d\n    tolerances: {score: 2}\n"
        "  classifier: {pass_threshold: 0.9}\n"
        "tags: [eval, nightly]\n"
        "when: 2026-10-03\n"
        "empty: ~\n"
    )
    assert safe_load(text) == yaml.safe_load(text)


def test_a_plain_equals_key_loads_as_safe_load_does():
    text = "=: a\nops: {=: eq, '<': lt}\n"
    assert safe_load(text) == yaml.safe_load(text)


def test_empty_document_loads_as_none():
    assert safe_load("") is None


def test_recursive_alias_terminates():
    loaded = safe_load("loop: &a [1, *a]\n")
    assert loaded["loop"][1] is loaded["loop"]


def test_loader_class_refuses_repeats_through_yaml_load():
    with pytest.raises(yaml.YAMLError, match="found duplicate key 'name'"):
        yaml.load("name: a\nname: b\n", Loader=UniqueKeyLoader)
