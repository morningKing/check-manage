import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token
from utils.version import (
    _replace_collection_relations,
    apply_partial_merge,
    merge_version_to_current,
    restore_from_version,
    switch_to_version,
)


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


def test_replace_collection_relations_rebuilds_reverse_entries():
    cur = MagicMock()
    cur.fetchone.return_value = ([
        {
            'fieldName': 'tags',
            'controlType': 'relation',
            'relationConfig': {
                'targetCollection': 'tag',
                'targetField': 'items',
            },
        }
    ],)

    _replace_collection_relations(
        cur,
        'project',
        'branch-a',
        [('project', 'p-1', 'tags', 'tag', 't-1')],
    )

    sql_calls = [(call.args[0], call.args[1]) for call in cur.execute.call_args_list]
    assert (
        'DELETE FROM data_relations WHERE collection = %s AND branch_id = %s',
        ('project', 'branch-a'),
    ) in sql_calls
    assert (
        'DELETE FROM data_relations '
        'WHERE collection = %s AND field_name = %s AND related_collection = %s AND branch_id = %s',
        ('tag', 'items', 'project', 'branch-a'),
    ) in sql_calls
    assert (
        'INSERT INTO data_relations '
        '(collection, record_id, field_name, related_collection, related_id, branch_id) '
        'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
        ('project', 'p-1', 'tags', 'tag', 't-1', 'branch-a'),
    ) in sql_calls
    assert (
        'INSERT INTO data_relations '
        '(collection, record_id, field_name, related_collection, related_id, branch_id) '
        'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
        ('tag', 't-1', 'items', 'project', 'p-1', 'branch-a'),
    ) in sql_calls


def test_restore_from_version_rebuilds_relations():
    with patch('utils.version.get_db') as mock_get_db, \
            patch('utils.version.load_version_data') as mock_load_version_data, \
            patch('utils.version._replace_collection_relations') as mock_replace:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = lambda self, *args: None
        mock_get_db.return_value = mock_conn

        mock_cur.fetchone.return_value = ('project', 1, 1)
        mock_cur.fetchall.return_value = [('project', 'p-1', 'tags', 'tag', 't-1')]
        mock_load_version_data.return_value = ([{'id': 'p-1', 'name': 'Project 1'}], {})

        result = restore_from_version('ver-1', restored_by='admin')

    assert result['success'] is True
    mock_replace.assert_called_once_with(
        mock_cur,
        'project',
        'main',
        [('project', 'p-1', 'tags', 'tag', 't-1')],
    )


def test_switch_to_version_rebuilds_relations_on_initialize():
    with patch('utils.version.get_db') as mock_get_db, \
            patch('utils.version._replace_collection_relations') as mock_replace:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = lambda self, *args: None
        mock_get_db.return_value = mock_conn

        mock_cur.fetchone.side_effect = [
            ('project', 'Project Branch', 'active', 'branch'),
            (0,),
        ]
        mock_cur.fetchall.side_effect = [
            [('p-1', {'name': 'Project 1'})],
            [('project', 'p-1', 'tags', 'tag', 't-1')],
        ]

        result = switch_to_version('ver-1', switched_by='admin')

    assert result['success'] is True
    assert result['initialized'] is True
    mock_replace.assert_called_once_with(
        mock_cur,
        'project',
        'ver-1',
        [('project', 'p-1', 'tags', 'tag', 't-1')],
    )


def test_merge_version_to_current_rebuilds_relations():
    with patch('utils.version.get_db') as mock_get_db, \
            patch('utils.version.load_version_data') as mock_load_version_data, \
            patch('utils.version.load_current_data') as mock_load_current_data, \
            patch('utils.version._replace_collection_relations') as mock_replace:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = lambda self, *args: None
        mock_get_db.return_value = mock_conn

        mock_cur.fetchone.side_effect = [
            ('project', 'active', 'snapshot'),
            ([{'fieldName': 'name'}, {'fieldName': 'tags', 'controlType': 'relation'}],),
        ]
        mock_cur.fetchall.return_value = [('project', 'p-1', 'tags', 'tag', 't-1')]
        mock_load_version_data.return_value = ([{'id': 'p-1', 'name': 'Project 1', 'tags': ['t-1']}], {})
        mock_load_current_data.return_value = ([], {})

        result = merge_version_to_current('ver-1', 'theirs', 'admin')

    assert result['success'] is True
    mock_replace.assert_called_once_with(
        mock_cur,
        'project',
        'main',
        [('project', 'p-1', 'tags', 'tag', 't-1')],
    )


def test_apply_partial_merge_uses_branch_scoped_conflict_key_and_rebuilds_relations():
    with patch('utils.version.get_db') as mock_get_db, \
            patch('utils.version.load_version_data') as mock_load_version_data, \
            patch('utils.version._replace_collection_relations') as mock_replace:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = lambda self, *args: None
        mock_get_db.return_value = mock_conn

        mock_cur.fetchone.side_effect = [
            ('project', 'active', 'snapshot'),
        ]
        mock_cur.fetchall.return_value = []
        mock_load_version_data.return_value = (
            [{'id': 'p-1', 'name': 'Project 1'}],
            {},
        )

        result = apply_partial_merge(
            source_version_id='ver-1',
            target_branch='main',
            decisions={
                'added_record_ids': ['p-1'],
                'removed_record_ids': [],
                'modified_records': [],
            },
            merged_by='admin',
        )

    assert result['success'] is True
    executed_sql = ' '.join(
        call.args[0]
        for call in mock_cur.execute.call_args_list
        if call.args and isinstance(call.args[0], str)
    )
    assert 'ON CONFLICT (id, branch_id) DO NOTHING' in executed_sql
    mock_replace.assert_called_once()


@pytest.fixture
def relation_graph_setup(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.auth.get_db', fake_db),
        patch('routes.menus.get_db', fake_db),
        patch('routes.etl_tasks.get_db', fake_db),
        patch('routes.dynamic.get_db', fake_db),
        patch('routes.page_configs.get_db', fake_db),
        patch('routes.users.get_db', fake_db),
        patch('routes.relations.get_db', fake_db),
        patch('routes.relation_graph.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('routes.relation_graph.get_user_current_branch',
              side_effect=lambda _user_id, col: {'project': 'branch-project', 'tag': 'main'}.get(col, 'main')),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
    )

    for p in patches:
        p.stop()


def test_relation_graph_filters_cross_branch_relation_rows(relation_graph_setup):
    client, mock_cursor, admin_h = relation_graph_setup
    mock_cursor.fetchone.return_value = ('p-1', {'name': 'Project 1'})
    mock_cursor.fetchall.side_effect = [
        [
            ('page-project', 'Project', [{'fieldName': 'name', 'label': 'Name'}]),
            ('page-tag', 'Tag', [{'fieldName': 'name', 'label': 'Name'}]),
        ],
        [('tags', 'tag', 't-1')],
        [
            ('tag', 't-stale', 'items', 'old-branch'),
            ('tag', 't-2', 'items', 'main'),
        ],
        [('t-1', 'tag', {'name': 'Tag 1'}), ('t-2', 'tag', {'name': 'Tag 2'})],
    ]

    resp = client.get('/relation-graph/project/p-1', headers=admin_h)

    assert resp.status_code == 200
    data = resp.get_json()
    assert any(edge['source'] == 'p-1' and edge['target'] == 't-1' for edge in data['edges'])
    assert any(edge['source'] == 't-2' and edge['target'] == 'p-1' for edge in data['edges'])
    assert not any(edge['source'] == 't-stale' for edge in data['edges'])

    executed_sql = ' '.join(
        call.args[0]
        for call in mock_cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str)
    )
    assert 'branch_id = %s' in executed_sql
