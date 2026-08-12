"""管理员批任务端点的测试。

最重要的一条是权限门的**参数化遍历**：不挑单个端点测，而是遍历 url_map 里该前缀
下的全部规则逐个断言。此前有过教训——某个装饰器只在一个端点上有测试，其余端点漏挂
不会被任何测试发现。这样写之后，新增端点忘挂装饰器会立刻红。

注：`admin_headers` fixture 已由 `conftest.py` 提供（签发 role='admin' 的真实
JWT，经由 autouse 的 `_reset_and_prime_permission_cache` 把 admin 角色直接
预置为 is_superuser=True，绕开数据库），此处不再重复定义。
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/ai/chat/admin/batches'


def _admin_routes(app):
    """url_map 里属于本蓝图的全部规则，展开成 (method, url) 列表。
    路径参数用占位值填上，只关心是否被权限门拦住。"""
    out = []
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith(BASE):
            continue
        url = str(rule).replace('<batch_id>', 'b-x').replace('<sid>', 's-x')
        for m in (rule.methods or set()) & {'GET', 'POST'}:
            out.append((m, url))
    return sorted(out)


def test_blueprint_exposes_the_expected_routes(app):
    """先钉住"有哪些路由"，否则下一条遍历断言可能在零条规则上空转通过。"""
    routes = _admin_routes(app)
    assert len(routes) >= 3
    assert ('GET', BASE) in routes


@pytest.mark.parametrize('method,url', [
    pytest.param(m, u, id=f'{m} {u}')
    for m, u in [('GET', BASE),
                 ('GET', f'{BASE}/b-x'),
                 ('GET', f'{BASE}/b-x/sessions/s-x/messages')]
])
def test_read_routes_require_admin_capability(client, method, url):
    """无 token 一律不放行（401/403），绝不能返回 200。"""
    resp = client.open(url, method=method)
    assert resp.status_code in (401, 403)


def test_every_admin_route_is_permission_gated(app, client):
    """遍历式兜底：任何新增端点漏挂 require_permission 都会在这里红。"""
    leaked = []
    for method, url in _admin_routes(app):
        resp = client.open(url, method=method)
        if resp.status_code not in (401, 403):
            leaked.append((method, url, resp.status_code))
    assert leaked == []


def test_list_passes_filters_through(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_list_batches',
               return_value={'items': [], 'total': 0}) as lb:
        resp = client.get(f'{BASE}?status=failed&source=api&owner=alice&keyword=报告'
                          f'&page=2&pageSize=50', headers=admin_headers)
    assert resp.status_code == 200
    kw = lb.call_args[1]
    assert kw['status'] == 'failed'
    assert kw['source'] == 'api'
    assert kw['owner_keyword'] == 'alice'
    assert kw['name_keyword'] == '报告'
    assert kw['page'] == 2 and kw['page_size'] == 50


def test_list_caps_page_size_at_100(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_list_batches',
               return_value={'items': [], 'total': 0}) as lb:
        client.get(f'{BASE}?pageSize=9999', headers=admin_headers)
    assert lb.call_args[1]['page_size'] == 100


def test_list_returns_contract_fields_only(client, admin_headers):
    """不能把 prompt / workspace_path 这类内部字段吐给前端。"""
    row = {'id': 'b-1', 'name': 'n', 'status': 'failed', 'total': 2, 'done': 0,
           'failed': 2, 'agent': None, 'model': None, 'created_at': None,
           'completed_at': None, 'owner_username': 'alice', 'source': 'api',
           'user_id': 'u-1', 'api_key_id': 'ak-1', 'prompt': '内部 prompt'}
    with patch('routes.ai_batch_admin.admin_list_batches',
               return_value={'items': [row], 'total': 1}):
        resp = client.get(BASE, headers=admin_headers)
    body = resp.get_json()
    assert set(body['items'][0]) == {
        'batchId', 'name', 'status', 'total', 'done', 'failed', 'agent', 'model',
        'createdAt', 'completedAt', 'ownerUsername', 'source'}
    assert 'prompt' not in resp.get_data(as_text=True)
    assert 'api_key_id' not in resp.get_data(as_text=True)


def test_detail_not_found_is_404(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_get_batch_detail', return_value=None):
        resp = client.get(f'{BASE}/b-nope', headers=admin_headers)
    assert resp.status_code == 404


def test_messages_not_found_is_404(client, admin_headers):
    """子任务不属于该批任务时，仓储返回 None -> 端点必须 404。"""
    with patch('routes.ai_batch_admin.admin_get_child_messages', return_value=None):
        resp = client.get(f'{BASE}/b-1/sessions/s-x/messages', headers=admin_headers)
    assert resp.status_code == 404


def test_messages_pass_through_truncation_info(client, admin_headers):
    payload = {'messages': [], 'truncated': True, 'total': 900}
    with patch('routes.ai_batch_admin.admin_get_child_messages', return_value=payload):
        resp = client.get(f'{BASE}/b-1/sessions/s-1/messages', headers=admin_headers)
    body = resp.get_json()
    assert body['truncated'] is True and body['total'] == 900
