"""Thin urllib client for Flask's internal memory endpoints. Stdlib only (the
MCP server's venv has no `requests`)."""
import os
import json
import urllib.request
import urllib.error

FLASK_INTERNAL_URL = os.getenv('FLASK_INTERNAL_URL', 'http://127.0.0.1:3002')
MCP_INTERNAL_TOKEN = os.getenv('MCP_INTERNAL_TOKEN', '')

# 内部端点只应指向本机 Flask；显式配置成远程地址需 MCP_ALLOW_REMOTE_INTERNAL=1，
# 防止错误 env 把内部 token 发到非预期主机（SSRF 加固）。
_ALLOW_REMOTE = os.getenv('MCP_ALLOW_REMOTE_INTERNAL', '') == '1'


def _check_url(url: str) -> None:
    if _ALLOW_REMOTE:
        return
    if not url.startswith(('http://127.0.0.1', 'http://localhost',
                           'https://127.0.0.1', 'https://localhost')):
        raise RuntimeError(
            f'FLASK_INTERNAL_URL must be loopback (got {url}); '
            'set MCP_ALLOW_REMOTE_INTERNAL=1 to override')


_check_url(FLASK_INTERNAL_URL)


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
