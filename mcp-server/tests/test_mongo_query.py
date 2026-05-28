"""Unit tests for the ported MongoDB-style -> SQL translator."""
from mongo_query import translate, remap_labels, MongoQueryError
import pytest


def test_equality():
    where, params = translate({"status": "open"})
    assert where == "data->>'status' = %s"
    assert params == ["open"]


def test_regex():
    where, params = translate({"name": {"$regex": "abc"}})
    assert "~*" in where and params == ["abc"]


def test_numeric_gte():
    where, params = translate({"age": {"$gte": 18}})
    assert "::numeric >= %s" in where and params == [18]


def test_in():
    where, params = translate({"s": {"$in": ["a", "b"]}})
    assert "IN (%s, %s)" in where and params == ["a", "b"]


def test_or():
    where, params = translate({"$or": [{"a": "1"}, {"b": "2"}]})
    assert " OR " in where and params == ["1", "2"]


def test_empty_is_true():
    assert translate({}) == ("TRUE", [])


def test_remap_labels():
    fields = [{"fieldName": "caseid", "label": "用例ID"}]
    assert remap_labels({"用例ID": "x"}, fields) == {"caseid": "x"}


def test_invalid_field_raises():
    with pytest.raises(MongoQueryError):
        translate({"bad field!": "x"})
