"""Tests for query_engine: count/run via mocked cursor, and xlsx writer."""
import os
from unittest.mock import MagicMock
from openpyxl import load_workbook


def test_count_rows_uses_filter():
    from query_engine import count_rows
    cur = MagicMock()
    cur.fetchone.return_value = (7,)
    n = count_rows(cur, "orders", {"status": "open"}, [{"fieldName": "status", "label": "状态"}])
    assert n == 7
    sql, params = cur.execute.call_args[0]
    assert "COUNT(*)" in sql and "orders" in params and "open" in params


def test_run_query_no_lookup_builds_rows_and_columns():
    from query_engine import run_query
    cur = MagicMock()
    cur.fetchone.side_effect = [(2,)]
    cur.fetchall.side_effect = [[
        ("id1", {"no": "A1", "status": "open"}, None, None),
        ("id2", {"no": "A2", "status": "done"}, None, None),
    ]]
    configs = {"orders": {"name": "订单", "fields": [
        {"fieldName": "no", "label": "单号", "controlType": "text"},
        {"fieldName": "status", "label": "状态", "controlType": "text"},
    ]}}
    res = run_query(cur, "orders", configs, {"no": {"$regex": "A"}}, [], [], {}, 0, 400)
    assert res["total"] == 2
    assert len(res["rows"]) == 2
    assert res["rows"][0]["no"] == "A1"
    keys = {c["key"] for c in res["columns"]}
    assert "no" in keys and "status" in keys


def test_order_clause_rejects_injection_in_sort_field():
    import pytest
    from query_engine import _order_clause
    from mongo_query import MongoQueryError
    with pytest.raises(MongoQueryError):
        _order_clause({"x'; DROP TABLE t--": 1}, {})


def test_write_xlsx_roundtrip(tmp_path):
    from query_engine import write_xlsx
    out = os.path.join(tmp_path, "o.xlsx")
    cols = [{"key": "no", "label": "单号"}, {"key": "tags", "label": "标签"}]
    rows = [{"no": "A1", "tags": ["x", "y"]}, {"no": "A2", "tags": None}]
    write_xlsx(rows, cols, out, sheet_title="订单")
    wb = load_workbook(out)
    sh = wb.active
    assert [c.value for c in sh[1]] == ["单号", "标签"]
    assert sh[2][0].value == "A1"
    assert sh[2][1].value == '["x", "y"]'
