"""External MCP servers: CRUD + the OpenCode `mcp` config they contribute.

An admin registers external MCP servers (remote URL or local command); every
AI-chat session's opencode.json then gets them merged in alongside the platform's
own MCP server, so OpenCode can reach those tools. The platform stays the only
writer of opencode.json — this module just supplies the extra config.
"""
import uuid

from psycopg2.extras import RealDictCursor

from db import get_db


class McpServerError(ValueError):
    """Invalid MCP server definition (bad type, missing url/command, dup name)."""


VALID_TYPES = ('remote', 'local')


def _normalize(*, name, type, url, command, headers, environment, enabled):
    name = (name or '').strip()
    if not name:
        raise McpServerError('name is required')
    type = (type or 'remote').strip()
    if type not in VALID_TYPES:
        raise McpServerError("type must be 'remote' or 'local'")
    url = (url or '').strip()
    command = command or []
    headers = headers or {}
    environment = environment or {}
    if not isinstance(command, list):
        raise McpServerError('command must be a list of strings')
    if not isinstance(headers, dict) or not isinstance(environment, dict):
        raise McpServerError('headers/environment must be objects')
    if type == 'remote' and not url:
        raise McpServerError('remote MCP requires a url')
    if type == 'local' and not [c for c in command if str(c).strip()]:
        raise McpServerError('local MCP requires a command')
    return {
        'name': name, 'type': type, 'url': url,
        'command': [str(c) for c in command],
        'headers': {str(k): str(v) for k, v in headers.items()},
        'environment': {str(k): str(v) for k, v in environment.items()},
        'enabled': bool(enabled),
    }


def list_servers():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM ai_mcp_servers ORDER BY created_at")
            return [dict(r) for r in cur.fetchall()]


def get_server(server_id):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM ai_mcp_servers WHERE id = %s", (server_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def create_server(*, name, type='remote', url='', command=None,
                  headers=None, environment=None, enabled=True):
    data = _normalize(name=name, type=type, url=url, command=command,
                      headers=headers, environment=environment, enabled=enabled)
    server_id = str(uuid.uuid4())
    import json as _json
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM ai_mcp_servers WHERE name = %s", (data['name'],))
            if cur.fetchone():
                raise McpServerError(f"an MCP server named '{data['name']}' already exists")
            cur.execute(
                "INSERT INTO ai_mcp_servers "
                "  (id, name, type, url, command, headers, environment, enabled) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s) RETURNING *",
                (server_id, data['name'], data['type'], data['url'],
                 _json.dumps(data['command']), _json.dumps(data['headers']),
                 _json.dumps(data['environment']), data['enabled']),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return row


def update_server(server_id, *, name, type='remote', url='', command=None,
                  headers=None, environment=None, enabled=True):
    data = _normalize(name=name, type=type, url=url, command=command,
                      headers=headers, environment=environment, enabled=enabled)
    import json as _json
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM ai_mcp_servers WHERE name = %s AND id <> %s",
                        (data['name'], server_id))
            if cur.fetchone():
                raise McpServerError(f"an MCP server named '{data['name']}' already exists")
            cur.execute(
                "UPDATE ai_mcp_servers SET name=%s, type=%s, url=%s, command=%s::jsonb, "
                "  headers=%s::jsonb, environment=%s::jsonb, enabled=%s, updated_at=NOW() "
                "WHERE id=%s RETURNING *",
                (data['name'], data['type'], data['url'], _json.dumps(data['command']),
                 _json.dumps(data['headers']), _json.dumps(data['environment']),
                 data['enabled'], server_id),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def delete_server(server_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_mcp_servers WHERE id = %s", (server_id,))
            deleted = cur.rowcount
        conn.commit()
    return deleted > 0


def enabled_mcp_config(reserved_names=()):
    """Return the OpenCode `mcp` map for all ENABLED external servers, ready to
    merge into opencode.json. Entries whose name collides with a reserved name
    (e.g. the platform's own MCP) are skipped so they can't shadow it."""
    reserved = set(reserved_names)
    out = {}
    for s in list_servers():
        if not s['enabled'] or s['name'] in reserved:
            continue
        if s['type'] == 'local':
            entry = {'type': 'local', 'command': list(s['command'] or []), 'enabled': True}
            if s['environment']:
                entry['environment'] = dict(s['environment'])
        else:
            entry = {'type': 'remote', 'url': s['url'], 'enabled': True}
            if s['headers']:
                entry['headers'] = dict(s['headers'])
        out[s['name']] = entry
    return out
