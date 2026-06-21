"""Thin urllib client for Flask's internal memory endpoints. Stdlib only (the
MCP server's venv has no `requests`)."""
import os
import json
import urllib.request
import urllib.error

FLASK_INTERNAL_URL = os.getenv('FLASK_INTERNAL_URL', 'http://127.0.0.1:3002')
MCP_INTERNAL_TOKEN = os.getenv('MCP_INTERNAL_TOKEN', '')


def _post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        FLASK_INTERNAL_URL + path, data=data, method='POST',
        headers={'Content-Type': 'application/json', 'X-Internal-Token': MCP_INTERNAL_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'memory endpoint {path} failed ({e.code})')
    except urllib.error.URLError as e:
        raise RuntimeError(f'memory endpoint {path} unreachable: {e.reason}')


def search(user_id: str, query: str, limit: int = 5) -> list:
    return _post('/ai/memory/internal/search', {'userId': user_id, 'query': query, 'limit': limit}).get('results', [])


def add(user_id: str, text: str) -> None:
    _post('/ai/memory/internal/add', {'userId': user_id, 'messages': [{'role': 'user', 'content': text}]})


def delete(memory_id: str) -> None:
    _post('/ai/memory/internal/delete', {'memoryId': memory_id})
