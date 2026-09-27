"""Tests for MongoDB-style query translator (utils/mongo_query.py)."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.mongo_query import translate, remap_labels, MongoQueryError


class TestBasicEquality:
    def test_empty_query(self):
        sql, params = translate({})
        assert sql == 'TRUE'
        assert params == []

    def test_none_query(self):
        sql, params = translate(None)
        assert sql == 'TRUE'
        assert params == []

    def test_simple_eq(self):
        sql, params = translate({'name': 'test'})
        assert sql == "data->>'name' = %s"
        assert params == ['test']

    def test_multiple_fields(self):
        sql, params = translate({'name': 'test', 'status': 'active'})
        assert "data->>'name' = %s" in sql
        assert "data->>'status' = %s" in sql
        assert ' AND ' in sql
        assert params == ['test', 'active']

    def test_numeric_eq(self):
        sql, params = translate({'age': 25})
        assert "(data->>'age')::numeric = %s" in sql
        assert params == [25]

    def test_null_eq(self):
        sql, params = translate({'name': None})
        assert "IS NULL" in sql or "= 'null'" in sql

    def test_bool_eq(self):
        sql, params = translate({'active': True})
        assert "::text" in sql
        assert params == ['true']


class TestComparisonOperators:
    def test_gt(self):
        sql, params = translate({'age': {'$gt': 18}})
        assert "(data->>'age')::numeric > %s" in sql
        assert params == [18]

    def test_gte(self):
        sql, params = translate({'age': {'$gte': 18}})
        assert "(data->>'age')::numeric >= %s" in sql
        assert params == [18]

    def test_lt(self):
        sql, params = translate({'age': {'$lt': 65}})
        assert "(data->>'age')::numeric < %s" in sql
        assert params == [65]

    def test_lte(self):
        sql, params = translate({'age': {'$lte': 65}})
        assert "(data->>'age')::numeric <= %s" in sql
        assert params == [65]

    def test_ne_string(self):
        sql, params = translate({'status': {'$ne': 'deleted'}})
        assert "data->>'status' != %s" in sql
        assert params == ['deleted']

    def test_ne_null(self):
        sql, params = translate({'name': {'$ne': None}})
        assert "IS NOT NULL" in sql

    def test_range_combined(self):
        sql, params = translate({'age': {'$gte': 18, '$lt': 65}})
        assert '>= %s' in sql
        assert '< %s' in sql
        assert params == [18, 65]

    def test_string_comparison(self):
        sql, params = translate({'name': {'$gt': 'A'}})
        assert "data->>'name' > %s" in sql
        assert params == ['A']


class TestArrayOperators:
    def test_in(self):
        sql, params = translate({'status': {'$in': ['active', 'pending']}})
        assert 'IN' in sql
        assert params == ['active', 'pending']

    def test_nin(self):
        sql, params = translate({'status': {'$nin': ['deleted']}})
        assert 'NOT IN' in sql
        assert params == ['deleted']

    def test_in_empty(self):
        sql, params = translate({'status': {'$in': []}})
        assert sql == 'FALSE'

    def test_nin_empty(self):
        sql, params = translate({'status': {'$nin': []}})
        assert sql == 'TRUE'

    def test_in_not_array(self):
        with pytest.raises(MongoQueryError, match='array'):
            translate({'status': {'$in': 'oops'}})


class TestStringOperators:
    def test_regex(self):
        sql, params = translate({'name': {'$regex': 'test'}})
        assert '~*' in sql
        assert params == ['test']

    def test_like(self):
        sql, params = translate({'name': {'$like': 'test'}})
        assert 'ILIKE' in sql
        assert params == ['%test%']

    def test_regex_not_string(self):
        with pytest.raises(MongoQueryError, match='string'):
            translate({'name': {'$regex': 123}})


class TestElementOperators:
    def test_exists_true(self):
        sql, params = translate({'phone': {'$exists': True}})
        assert 'IS NOT NULL' in sql
        assert params == []

    def test_exists_false(self):
        sql, params = translate({'phone': {'$exists': False}})
        assert 'IS NULL' in sql
        assert params == []

    def test_size(self):
        sql, params = translate({'tags': {'$size': 3}})
        assert 'jsonb_array_length' in sql
        assert params == [3]


class TestLogicalOperators:
    def test_and(self):
        sql, params = translate({
            '$and': [{'name': 'test'}, {'age': {'$gt': 18}}]
        })
        assert 'AND' in sql
        assert len(params) == 2

    def test_or(self):
        sql, params = translate({
            '$or': [{'status': 'active'}, {'status': 'pending'}]
        })
        assert 'OR' in sql
        assert params == ['active', 'pending']

    def test_nor(self):
        sql, params = translate({
            '$nor': [{'status': 'deleted'}, {'status': 'archived'}]
        })
        assert 'NOT' in sql
        assert 'OR' in sql
        assert params == ['deleted', 'archived']

    def test_not_field(self):
        sql, params = translate({'age': {'$not': {'$gt': 65}}})
        assert 'NOT' in sql
        assert params == [65]

    def test_top_level_not(self):
        sql, params = translate({'$not': {'status': 'deleted'}})
        assert 'NOT' in sql
        assert params == ['deleted']

    def test_nested_or_and(self):
        sql, params = translate({
            '$or': [
                {'$and': [{'name': 'a'}, {'age': 1}]},
                {'name': 'b'},
            ]
        })
        assert 'OR' in sql
        assert 'AND' in sql
        assert len(params) == 3


class TestNestedFields:
    def test_dotted_field(self):
        sql, params = translate({'address.city': 'Beijing'})
        assert "data->'address'->>'city'" in sql
        assert params == ['Beijing']

    def test_deep_nested(self):
        sql, params = translate({'a.b.c': 'val'})
        assert "data->'a'->'b'->>'c'" in sql


class TestValidation:
    def test_invalid_query_type(self):
        with pytest.raises(MongoQueryError, match='JSON object'):
            translate('not a dict')

    def test_invalid_query_array(self):
        with pytest.raises(MongoQueryError, match='JSON object'):
            translate([1, 2])

    def test_unknown_operator(self):
        with pytest.raises(MongoQueryError, match='Unknown operator'):
            translate({'name': {'$weird': 1}})

    def test_unknown_top_level_operator(self):
        with pytest.raises(MongoQueryError, match='Unknown top-level'):
            translate({'$weird': []})

    def test_invalid_field_name(self):
        with pytest.raises(MongoQueryError, match='Invalid field'):
            translate({"'; DROP TABLE--": 'val'})

    def test_and_not_array(self):
        with pytest.raises(MongoQueryError, match='array'):
            translate({'$and': 'oops'})

    def test_or_not_array(self):
        with pytest.raises(MongoQueryError, match='array'):
            translate({'$or': 'oops'})

    def test_not_not_object(self):
        with pytest.raises(MongoQueryError, match='object'):
            translate({'$not': 'oops'})

    def test_mixed_ops_and_fields(self):
        with pytest.raises(MongoQueryError, match='mix'):
            translate({'name': {'$gt': 1, 'other': 2}})


class TestEdgeCases:
    def test_float_comparison(self):
        sql, params = translate({'price': {'$lte': 99.9}})
        assert '::numeric' in sql
        assert params == [99.9]

    def test_implicit_eq_dict_no_ops(self):
        """Dict value without $ keys treated as implicit eq."""
        sql, params = translate({'meta': {'key': 'val'}})
        # Should treat as $eq with a dict value (unusual but handled)
        assert '%s' in sql

    def test_in_with_numbers(self):
        sql, params = translate({'code': {'$in': [1, 2, 3]}})
        assert 'IN' in sql
        assert params == ['1', '2', '3']


class TestRemapLabels:
    FIELDS = [
        {'fieldName': 'caseid', 'label': '用例ID'},
        {'fieldName': 'caseName', 'label': '用例名称'},
        {'fieldName': 'status', 'label': '状态'},
        {'fieldName': 'age', 'label': '年龄'},
    ]

    def test_remap_simple(self):
        q = {'用例ID': 'IC-001'}
        result = remap_labels(q, self.FIELDS)
        assert result == {'caseid': 'IC-001'}

    def test_remap_multiple(self):
        q = {'用例ID': 'IC-001', '状态': 'active'}
        result = remap_labels(q, self.FIELDS)
        assert result == {'caseid': 'IC-001', 'status': 'active'}

    def test_keep_fieldname(self):
        q = {'caseid': 'IC-001'}
        result = remap_labels(q, self.FIELDS)
        assert result == {'caseid': 'IC-001'}

    def test_remap_in_or(self):
        q = {'$or': [{'用例ID': 'IC-001'}, {'用例名称': 'test'}]}
        result = remap_labels(q, self.FIELDS)
        assert result == {'$or': [{'caseid': 'IC-001'}, {'caseName': 'test'}]}

    def test_remap_in_and(self):
        q = {'$and': [{'用例ID': 'IC-001'}, {'年龄': {'$gt': 18}}]}
        result = remap_labels(q, self.FIELDS)
        assert result == {'$and': [{'caseid': 'IC-001'}, {'age': {'$gt': 18}}]}

    def test_remap_nested_not(self):
        q = {'$not': {'用例ID': 'IC-001'}}
        result = remap_labels(q, self.FIELDS)
        assert result == {'$not': {'caseid': 'IC-001'}}

    def test_operators_preserved(self):
        q = {'用例ID': {'$regex': 'IC'}}
        result = remap_labels(q, self.FIELDS)
        assert result == {'caseid': {'$regex': 'IC'}}

    def test_empty_query(self):
        assert remap_labels({}, self.FIELDS) == {}
        assert remap_labels(None, self.FIELDS) is None

    def test_no_fields(self):
        q = {'用例ID': 'IC-001'}
        assert remap_labels(q, []) == q
        assert remap_labels(q, None) == q

    def test_unknown_label_kept(self):
        q = {'unknown_field': 'val'}
        result = remap_labels(q, self.FIELDS)
        assert result == {'unknown_field': 'val'}
