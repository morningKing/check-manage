"""
Dashboard aggregation route tests

Ensures relation/reference/quoteSelect grouping return readable labels.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List

import routes.dashboards as dashboards


def _mock_page_fields(monkeypatch, fields: List[Dict[str, Any]], target_fields: Dict[str, Any] | None = None):
    monkeypatch.setattr(dashboards, '_load_page_fields', lambda cur, collection: fields)
    monkeypatch.setattr(
        dashboards,
        '_resolve_target_fields',
        lambda cur, target_cols: target_fields or {},
    )


def _mock_get_db(monkeypatch, mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn

    monkeypatch.setattr(dashboards, 'get_db', fake_get_db)


def _mock_where(monkeypatch, clause=None, params=None):
    def fake_build_where(collection, filter_query, table_alias='dd', branch_id='main'):
        alias = f"{table_alias}." if table_alias else ''
        final_clause = clause or f"{alias}collection = %s AND {alias}branch_id = %s"
        return final_clause, params or [collection, branch_id]

    monkeypatch.setattr(dashboards, '_build_where', fake_build_where)


def _mock_branch(monkeypatch, branch_id='main'):
    monkeypatch.setattr(dashboards, 'get_user_current_branch', lambda user_id, collection: branch_id)
def test_relation_grouping_uses_display_names(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    fields = [
        {
            'fieldName': 'assignedTemplates',
            'label': '关联模板',
            'controlType': 'relation',
            'relationConfig': {
                'targetCollection': 'demo-template',
                'displayField': 'templateName',
                'targetField': 'tasks',
            },
        },
    ]
    target_configs = {
        'page-demo-template': [{'fieldName': 'templateName', 'controlType': 'text'}],
    }
    _mock_page_fields(monkeypatch, fields, target_configs)
    _mock_where(monkeypatch)

    mock_cursor.fetchall.return_value = [
        ('巡检模板 A', 3),
        ('巡检模板 B', 1),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'count'}],
            'groupBy': {'field': 'assignedTemplates', 'type': 'terms'},
            'limit': 5,
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'grouped'
    assert payload['data'] == [
        {'key': '巡检模板 A', 'value': 3.0},
        {'key': '巡检模板 B', 'value': 1.0},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'JOIN data_relations' in executed_sql
    assert 'JOIN dynamic_data tgt' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    assert params[:6] == ['templateName', 'dashboard-demo', 'assignedTemplates', 'main', 'demo-template', 'main']
    assert params[-3:-1] == ['dashboard-demo', 'main']
    assert params[-1] == 5


def test_reference_grouping_left_joins_parent_record(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    fields = [
        {
            'fieldName': 'parentPlan',
            'label': '父计划',
            'controlType': 'reference',
            'referenceConfig': {
                'targetCollection': 'demo-plan',
                'displayField': 'planName',
                'inheritFields': [],
            },
        },
    ]
    target_configs = {
        'page-demo-plan': [{'fieldName': 'planName', 'controlType': 'text'}],
    }
    _mock_page_fields(monkeypatch, fields, target_configs)
    _mock_where(monkeypatch)

    mock_cursor.fetchall.return_value = [
        ('计划一', 2),
        ('计划二', 1),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'count'}],
            'groupBy': {'field': 'parentPlan', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'grouped'
    assert payload['data'] == [
        {'key': '计划一', 'value': 2.0},
        {'key': '计划二', 'value': 1.0},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'LEFT JOIN dynamic_data tgt ON tgt.id = dd.data->>%s' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    assert params[:4] == ['planName', 'parentPlan', 'demo-plan', 'main']
    assert params[-3:-1] == ['dashboard-demo', 'main']


def test_quote_select_grouping_unwinds_array(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    fields = [
        {
            'fieldName': 'quotedCases',
            'label': '引用用例',
            'controlType': 'quoteSelect',
            'quoteConfig': {
                'targetCollection': 'demo-case',
                'displayField': 'caseName',
            },
        },
    ]
    target_configs = {
        'page-demo-case': [{'fieldName': 'caseName', 'controlType': 'text'}],
    }
    _mock_page_fields(monkeypatch, fields, target_configs)
    _mock_where(monkeypatch)

    mock_cursor.fetchall.return_value = [
        ('网络连通性', 4),
        ('CPU 检查', 2),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'count'}],
            'groupBy': {'field': 'quotedCases', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'grouped'
    assert payload['data'] == [
        {'key': '网络连通性', 'value': 4.0},
        {'key': 'CPU 检查', 'value': 2.0},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'jsonb_array_elements_text' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    assert params[:4] == ['caseName', 'quotedCases', 'demo-case', 'main']
    assert params[-3:-1] == ['dashboard-demo', 'main']
