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


def test_single_aggregate_supports_multiple_metrics(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    mock_cursor.fetchone.return_value = (7, 42.5)

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [
                {'type': 'count', 'name': 'records'},
                {'type': 'sum', 'field': 'cost', 'name': 'totalCost'},
            ],
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload == {
        'type': 'single',
        'value': 7.0,
        'metrics': {
            'records': 7.0,
            'totalCost': 42.5,
        },
    }

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'COUNT(*) AS agg_value' in executed_sql
    assert 'SUM((data->>%s)::numeric) AS metric_1' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    assert params == ['cost', 'dashboard-demo', 'main']


def test_histogram_grouping_returns_numeric_buckets(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        (0, 2),
        (10, 5),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'count'}],
            'groupBy': {'field': 'score', 'type': 'histogram', 'interval': 10},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['data'] == [
        {'key': 0, 'value': 2.0},
        {'key': 10, 'value': 5.0},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'FLOOR' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    assert params[:6] == ['score', 'score', 0.0, 10.0, 10.0, 0.0]


def test_range_grouping_supports_multiple_metrics(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('low', 2, 15),
        ('high', 1, 30),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [
                {'type': 'count', 'name': 'records'},
                {'type': 'sum', 'field': 'score', 'name': 'totalScore'},
            ],
            'groupBy': {
                'field': 'score',
                'type': 'range',
                'ranges': [
                    {'key': 'low', 'to': 20},
                    {'key': 'high', 'from': 20},
                ],
            },
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'grouped'
    assert payload['data'] == [
        {'key': 'low', 'value': 2.0, 'metrics': {'records': 2.0, 'totalScore': 15.0}},
        {'key': 'high', 'value': 1.0, 'metrics': {'records': 1.0, 'totalScore': 30.0}},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'CASE WHEN' in executed_sql
    assert 'SUM((data->>%s)::numeric) AS metric_1' in executed_sql


def test_relation_grouping_uses_source_data_for_sum_metric(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
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
    _mock_page_fields(monkeypatch, fields, {'page-demo-template': [{'fieldName': 'templateName'}]})
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('模板A', 23),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'sum', 'field': 'cost'}],
            'groupBy': {'field': 'assignedTemplates', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['data'] == [{'key': '模板A', 'value': 23.0}]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'SUM((dd.data->>%s)::numeric) AS agg_value' in executed_sql


def test_matrix_aggregate_supports_secondary_breakdown(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('open', 'high', 3, 15),
        ('open', 'low', 2, 6),
        ('closed', 'high', 1, 8),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [
                {'type': 'count', 'name': 'records'},
                {'type': 'sum', 'field': 'score', 'name': 'totalScore'},
            ],
            'groupBy': {'field': 'status', 'type': 'terms'},
            'breakdownBy': {'field': 'severity', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'matrix'
    assert payload['rows'] == ['open', 'closed']
    assert payload['columns'] == ['high', 'low']
    assert payload['data'] == [
        {'rowKey': 'open', 'columnKey': 'high', 'value': 3.0, 'metrics': {'records': 3.0, 'totalScore': 15.0}},
        {'rowKey': 'open', 'columnKey': 'low', 'value': 2.0, 'metrics': {'records': 2.0, 'totalScore': 6.0}},
        {'rowKey': 'closed', 'columnKey': 'high', 'value': 1.0, 'metrics': {'records': 1.0, 'totalScore': 8.0}},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'GROUP BY row_key, column_key' in executed_sql
    assert 'SUM((data->>%s)::numeric) AS metric_1' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    assert params[:3] == ['status', 'severity', 'score']


def test_matrix_aggregate_rejects_relation_dimensions(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [
        {
            'fieldName': 'assignedTemplates',
            'controlType': 'relation',
            'relationConfig': {'targetCollection': 'demo-template', 'displayField': 'templateName'},
        },
        {'fieldName': 'status', 'controlType': 'text'},
    ])

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'count'}],
            'groupBy': {'field': 'assignedTemplates', 'type': 'terms'},
            'breakdownBy': {'field': 'status', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 400
    assert '二维交叉统计当前仅支持普通字段' in resp.get_json()['error']


def test_array_length_sum_metric(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test arrayLengthSum aggregates the count of array elements."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('V1.0', 5, 12),
        ('V2.0', 3, 8),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'requirements',
            'metrics': [
                {'type': 'count', 'name': '需求数'},
                {'type': 'arrayLengthSum', 'field': 'issues', 'name': '问题数'},
            ],
            'groupBy': {'field': 'version', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'grouped'
    assert payload['data'] == [
        {'key': 'V1.0', 'value': 5.0, 'metrics': {'需求数': 5.0, '问题数': 12.0}},
        {'key': 'V2.0', 'value': 3.0, 'metrics': {'需求数': 3.0, '问题数': 8.0}},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'COUNT(*) AS agg_value' in executed_sql
    # jsonb_array_length requires JSONB access (data->field), not text access (data->>field)
    assert 'SUM(COALESCE(jsonb_array_length(data->%s), 0)) AS metric_1' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    # First param is group field (version), then metric field (issues)
    assert params[0] == 'version'
    assert params[1] == 'issues'


def test_array_length_avg_metric(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test arrayLengthAvg calculates average array length per record."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('V1.0', 2.4),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'requirements',
            'metrics': [{'type': 'arrayLengthAvg', 'field': 'issues', 'name': '平均问题数'}],
            'groupBy': {'field': 'version', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    # Single metric does not include 'metrics' field in response
    assert payload['data'] == [{'key': 'V1.0', 'value': 2.4}]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'AVG(COALESCE(jsonb_array_length(data->%s), 0)) AS agg_value' in executed_sql


def test_relation_count_sum_metric(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test relationCountSum aggregates relation counts via data_relations subquery."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    # Need to mock fields to have relation field definition
    fields = [
        {'fieldName': 'version', 'controlType': 'select'},
        {'fieldName': 'relatedBugs', 'controlType': 'relation',
         'relationConfig': {'targetCollection': 'quality-bug', 'displayField': 'bugNo', 'targetField': 'relatedReq'}},
    ]
    _mock_page_fields(monkeypatch, fields)
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('V1.0', 5, 7),
        ('V2.0', 4, 6),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'quality-project',
            'metrics': [
                {'type': 'count', 'name': '需求数'},
                {'type': 'relationCountSum', 'field': 'relatedBugs', 'name': '问题数'},
            ],
            'groupBy': {'field': 'version', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'grouped'
    assert payload['data'] == [
        {'key': 'V1.0', 'value': 5.0, 'metrics': {'需求数': 5.0, '问题数': 7.0}},
        {'key': 'V2.0', 'value': 4.0, 'metrics': {'需求数': 4.0, '问题数': 6.0}},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'COUNT(*) AS agg_value' in executed_sql
    # relationCountSum uses subquery with data_relations
    assert 'SUM((SELECT COUNT(*) FROM data_relations dr' in executed_sql
    assert 'dr.field_name = %s AND dr.branch_id = %s' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    # Check that relation field name is in params
    assert 'relatedBugs' in params


def test_array_length_sum_on_relation_field(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test arrayLengthSum on relation field automatically uses data_relations subquery."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    fields = [
        {'fieldName': 'version', 'controlType': 'select'},
        {'fieldName': 'relatedBugs', 'controlType': 'relation',
         'relationConfig': {'targetCollection': 'quality-bug', 'displayField': 'bugNo', 'targetField': 'relatedReq'}},
    ]
    _mock_page_fields(monkeypatch, fields)
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('V1.0', 7),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'quality-project',
            'metrics': [{'type': 'arrayLengthSum', 'field': 'relatedBugs', 'name': '问题数'}],
            'groupBy': {'field': 'version', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['data'] == [{'key': 'V1.0', 'value': 7.0}]

    executed_sql = mock_cursor.execute.call_args[0][0]
    # When field is relation, arrayLengthSum should use data_relations subquery
    assert 'SUM((SELECT COUNT(*) FROM data_relations dr' in executed_sql


def test_relation_count_avg_metric(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test relationCountAvg calculates average relation count per record."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    fields = [
        {'fieldName': 'version', 'controlType': 'select'},
        {'fieldName': 'relatedBugs', 'controlType': 'relation',
         'relationConfig': {'targetCollection': 'quality-bug', 'displayField': 'bugNo', 'targetField': 'relatedReq'}},
    ]
    _mock_page_fields(monkeypatch, fields)
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('V1.0', 5, 1.4),  # 5 requirements, avg 1.4 bugs each
        ('V2.0', 4, 1.5),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'quality-project',
            'metrics': [
                {'type': 'count', 'name': '需求数'},
                {'type': 'relationCountAvg', 'field': 'relatedBugs', 'name': '平均问题数'},
            ],
            'groupBy': {'field': 'version', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['data'] == [
        {'key': 'V1.0', 'value': 5.0, 'metrics': {'需求数': 5.0, '平均问题数': 1.4}},
        {'key': 'V2.0', 'value': 4.0, 'metrics': {'需求数': 4.0, '平均问题数': 1.5}},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'AVG((SELECT COUNT(*) FROM data_relations dr' in executed_sql


def test_relation_count_max_min_metrics(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test relationCountMax and relationCountMin metrics."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    fields = [
        {'fieldName': 'relatedBugs', 'controlType': 'relation',
         'relationConfig': {'targetCollection': 'quality-bug', 'displayField': 'bugNo', 'targetField': 'relatedReq'}},
    ]
    _mock_page_fields(monkeypatch, fields)
    _mock_where(monkeypatch)
    mock_cursor.fetchone.return_value = (5, 3)  # max 5, min 3

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'quality-project',
            'metrics': [
                {'type': 'relationCountMax', 'field': 'relatedBugs', 'name': '最大问题数'},
                {'type': 'relationCountMin', 'field': 'relatedBugs', 'name': '最小问题数'},
            ],
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'single'
    assert payload['metrics'] == {'最大问题数': 5.0, '最小问题数': 3.0}

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'MAX((SELECT COUNT(*) FROM data_relations dr' in executed_sql
    assert 'MIN((SELECT COUNT(*) FROM data_relations dr' in executed_sql


def test_exists_grouping(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test exists grouping returns 'empty' and 'nonEmpty' keys."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('empty', 3),
        ('nonEmpty', 7),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'count'}],
            'groupBy': {'field': 'description', 'type': 'exists'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'grouped'
    assert payload['data'] == [
        {'key': 'empty', 'value': 3.0},
        {'key': 'nonEmpty', 'value': 7.0},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'CASE WHEN NULLIF(BTRIM(COALESCE' in executed_sql
    assert 'THEN \'empty\' ELSE \'nonEmpty\' END' in executed_sql


def test_date_histogram_grouping(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test dateHistogram grouping with various intervals."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    fields = [
        {'fieldName': 'createdAt', 'controlType': 'datetime'},
    ]
    _mock_page_fields(monkeypatch, fields)
    _mock_where(monkeypatch)
    # Return datetime objects that will be formatted
    from datetime import datetime
    mock_cursor.fetchall.return_value = [
        (datetime(2024, 1, 1), 5),  # January 2024
        (datetime(2024, 2, 1), 3),  # February 2024
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'count'}],
            'groupBy': {'field': 'createdAt', 'type': 'dateHistogram', 'interval': 'month'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'grouped'
    # Keys should be formatted as YYYY-MM
    assert payload['data'][0]['key'] == '2024-01'
    assert payload['data'][1]['key'] == '2024-02'

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'date_trunc(\'month\'' in executed_sql


def test_date_histogram_with_day_interval(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test dateHistogram with day interval formats keys as YYYY-MM-DD."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    fields = [
        {'fieldName': 'createdAt', 'controlType': 'datetime'},
    ]
    _mock_page_fields(monkeypatch, fields)
    _mock_where(monkeypatch)
    from datetime import datetime
    mock_cursor.fetchall.return_value = [
        (datetime(2024, 1, 15), 5),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'count'}],
            'groupBy': {'field': 'createdAt', 'type': 'dateHistogram', 'interval': 'day'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['data'][0]['key'] == '2024-01-15'

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'date_trunc(\'day\'' in executed_sql


def test_unique_count_metric(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test uniqueCount returns count of distinct values."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('张三', 3),
        ('李四', 2),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'dashboard-demo',
            'metrics': [{'type': 'uniqueCount', 'field': 'assignee', 'name': '处理人数'}],
            'groupBy': {'field': 'version', 'type': 'terms'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['data'] == [
        {'key': '张三', 'value': 3.0},
        {'key': '李四', 'value': 2.0},
    ]

    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'COUNT(DISTINCT data->>%s) AS agg_value' in executed_sql


def test_matrix_aggregate_with_key_asc_sort(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test matrix query uses row_key for key-based sorting (not group_key)."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    _mock_where(monkeypatch)
    mock_cursor.fetchall.return_value = [
        ('V1.0', 'released', 5),
        ('V1.1', 'released', 3),
        ('V2.0', 'testing', 2),
    ]

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'quality-project',
            'metrics': [{'type': 'count', 'name': '需求数'}],
            'groupBy': {'type': 'terms', 'field': 'version'},
            'breakdownBy': {'type': 'terms', 'field': 'status'},
            'sort': 'key_asc',
            'limit': 20,
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['type'] == 'matrix'
    assert payload['rows'] == ['V1.0', 'V1.1', 'V2.0']

    # Verify SQL uses row_key (not group_key) for ORDER BY
    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'AS row_key' in executed_sql
    assert 'ORDER BY row_key ASC' in executed_sql
    # Should NOT have group_key in ORDER BY
    assert 'ORDER BY group_key' not in executed_sql


def test_filter_parameter_in_aggregation(client, mock_cursor, admin_headers, mock_conn, monkeypatch):
    """Test filter parameter adds WHERE conditions."""
    _mock_get_db(monkeypatch, mock_conn)
    _mock_branch(monkeypatch, 'main')
    _mock_page_fields(monkeypatch, [])
    mock_cursor.fetchone.return_value = (7,)

    resp = client.post(
        '/dashboards/aggregate',
        json={
            'collection': 'quality-bug',
            'metrics': [{'type': 'count'}],
            'filter': {'severity': 'fatal', 'status': 'new'},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['value'] == 7.0

    # Filter conditions should be in the SQL
    executed_sql = mock_cursor.execute.call_args[0][0]
    assert 'data->>%s = %s' in executed_sql
    params = mock_cursor.execute.call_args[0][1]
    assert 'severity' in params
    assert 'fatal' in params
    assert 'status' in params
    assert 'new' in params
