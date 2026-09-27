"""Tests for external MCP server CRUD + opencode.json merge."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture(autouse=True)
def _clean(db_conn):
    """Remove any rows our tests create (names are prefixed mcp-test-)."""
    yield
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_mcp_servers WHERE name LIKE 'mcp-test-%'")
    db_conn.commit()


def test_create_list_update_delete_remote():
    from utils.mcp_servers import (
        create_server, list_servers, update_server, delete_server)
    row = create_server(name='mcp-test-remote', type='remote',
                        url='https://example.com/mcp', headers={'Authorization': 'Bearer x'})
    assert row['name'] == 'mcp-test-remote'
    assert row['type'] == 'remote'
    assert row['headers'] == {'Authorization': 'Bearer x'}
    assert row['enabled'] is True

    names = {s['name'] for s in list_servers()}
    assert 'mcp-test-remote' in names

    upd = update_server(row['id'], name='mcp-test-remote', type='remote',
                        url='https://example.com/mcp2', enabled=False)
    assert upd['url'] == 'https://example.com/mcp2'
    assert upd['enabled'] is False

    assert delete_server(row['id']) is True
    assert delete_server(row['id']) is False


def test_create_local_server():
    from utils.mcp_servers import create_server
    row = create_server(name='mcp-test-local', type='local',
                        command=['npx', '-y', 'some-mcp'], environment={'TOKEN': 't'})
    assert row['type'] == 'local'
    assert row['command'] == ['npx', '-y', 'some-mcp']
    assert row['environment'] == {'TOKEN': 't'}


def test_validation_errors():
    from utils.mcp_servers import create_server, McpServerError
    with pytest.raises(McpServerError):
        create_server(name='', type='remote', url='https://x')          # no name
    with pytest.raises(McpServerError):
        create_server(name='mcp-test-x', type='remote', url='')         # remote needs url
    with pytest.raises(McpServerError):
        create_server(name='mcp-test-y', type='local', command=[])      # local needs command
    with pytest.raises(McpServerError):
        create_server(name='mcp-test-z', type='bogus', url='https://x') # bad type


def test_duplicate_name_rejected():
    from utils.mcp_servers import create_server, McpServerError
    create_server(name='mcp-test-dup', type='remote', url='https://a')
    with pytest.raises(McpServerError):
        create_server(name='mcp-test-dup', type='remote', url='https://b')


def test_enabled_mcp_config_shapes_and_filters():
    from utils.mcp_servers import create_server, enabled_mcp_config
    create_server(name='mcp-test-on', type='remote', url='https://on', headers={'H': '1'})
    create_server(name='mcp-test-off', type='remote', url='https://off', enabled=False)
    create_server(name='mcp-test-loc', type='local', command=['run'], environment={'E': '2'})

    cfg = enabled_mcp_config(reserved_names=['check-manage'])
    assert cfg['mcp-test-on'] == {
        'type': 'remote', 'url': 'https://on', 'enabled': True, 'headers': {'H': '1'}}
    assert cfg['mcp-test-loc'] == {
        'type': 'local', 'command': ['run'], 'enabled': True, 'environment': {'E': '2'}}
    assert 'mcp-test-off' not in cfg                 # disabled skipped


def test_reserved_name_skipped():
    from utils.mcp_servers import create_server, enabled_mcp_config
    create_server(name='mcp-test-reserved', type='remote', url='https://x')
    cfg = enabled_mcp_config(reserved_names=['mcp-test-reserved'])
    assert 'mcp-test-reserved' not in cfg


def test_write_opencode_config_merges_extra_mcp(tmp_path):
    """External MCP entries are merged but can never shadow the platform entry."""
    import json
    from utils.workspace import write_opencode_config
    extra = {
        'ext': {'type': 'remote', 'url': 'https://ext', 'enabled': True},
        'check-manage': {'type': 'remote', 'url': 'https://EVIL', 'enabled': True},
    }
    path = write_opencode_config(str(tmp_path), mcp_name='check-manage',
                                 mcp_url='https://platform/mcp?token=t', extra_mcp=extra)
    cfg = json.loads(open(path, encoding='utf-8').read())
    assert cfg['mcp']['ext'] == {'type': 'remote', 'url': 'https://ext', 'enabled': True}
    # platform entry kept ours, not the external override
    assert cfg['mcp']['check-manage']['url'] == 'https://platform/mcp?token=t'
