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


# --- 重试端点 ---------------------------------------------------------------

def test_retry_uses_the_batch_real_owner_not_the_admin(client, admin_headers):
    """本文件最要紧的一条。

    管理员重试**别人的**批任务时，底层写函数必须收到该批任务真实归属用户的 id。
    传成管理员自己的 id 会让重试静默失效（按归属过滤查不到行，retried 恒为 0），
    而一个只断言 200 的测试对此完全无感。
    """
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reset_failed_to_pending', return_value=2) as rf, \
         patch('routes.ai_batch_admin.admin_get_batch_detail',
               return_value={'batch': {'id': 'b-1', 'name': 'n'}, 'sessions': []}), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/retry-failed', headers=admin_headers)

    assert resp.status_code == 200
    assert resp.get_json() == {'retried': 2}
    assert rf.call_args[0][0] == 'u-owner'      # 归属用户，不是 user-admin
    assert rf.call_args[0][1] == 'b-1'


def test_retry_missing_batch_is_404(client, admin_headers):
    """不同于内部端点的 200 {"retried": 0}：管理工具里静默返回 0 会让 batchId
    打错看起来像"没有失败的子任务"。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value=None), \
         patch('routes.ai_batch_admin.reset_failed_to_pending') as rf:
        resp = client.post(f'{BASE}/b-nope/retry-failed', headers=admin_headers)
    assert resp.status_code == 404
    rf.assert_not_called()


def test_retry_allowed_on_running_batch(client, admin_headers):
    """刻意不设终态门（与对外 API 的 409 相反）。这条正向测试是为了防止日后有人
    照抄对外实现把 409 加回来，悄悄改掉管理场景的语义。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reset_failed_to_pending', return_value=1), \
         patch('routes.ai_batch_admin.admin_get_batch_detail',
               return_value={'batch': {'id': 'b-1', 'name': 'n', 'status': 'running'},
                             'sessions': []}), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/retry-failed', headers=admin_headers)
    assert resp.status_code == 200


def test_retry_notifies_worker_only_when_something_was_reset(client, admin_headers):
    """没重置任何东西就不必唤醒 worker。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reset_failed_to_pending', return_value=0), \
         patch('routes.ai_batch_admin.admin_get_batch_detail',
               return_value={'batch': {'id': 'b-1', 'name': 'n'}, 'sessions': []}), \
         patch('routes.ai_batch_admin.get_worker') as gw, \
         patch('routes.ai_batch_admin.log_operation'):
        client.post(f'{BASE}/b-1/retry-failed', headers=admin_headers)
    gw.return_value.notify.assert_not_called()


def test_retry_writes_operation_log(client, admin_headers):
    """管理员代替他人重跑任务却不留痕，出问题无法追溯是谁动的。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reset_failed_to_pending', return_value=3), \
         patch('routes.ai_batch_admin.admin_get_batch_detail',
               return_value={'batch': {'id': 'b-1', 'name': '报告批'}, 'sessions': []}), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation') as lg:
        client.post(f'{BASE}/b-1/retry-failed', headers=admin_headers)
    args = lg.call_args[0]
    assert args[1] == 'ai_chat_batch'      # target_type
    assert args[2] == 'b-1'                # target_id


def test_reexecute_uses_the_batch_real_owner(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reexecute_child',
               return_value={'batch': {}, 'sessions': []}) as rx, \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/sessions/s-1/reexecute', headers=admin_headers)
    assert resp.status_code == 200
    assert rx.call_args[0] == ('u-owner', 'b-1', 's-1')


def test_reexecute_non_terminal_child_is_409_not_500(client, admin_headers):
    """repo 对非终态子任务抛 ValueError；必须落成 409，不能裸 500。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reexecute_child',
               side_effect=ValueError('only completed/failed children can be re-executed')), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/sessions/s-1/reexecute', headers=admin_headers)
    assert resp.status_code == 409
    assert 're-executed' in resp.get_json()['error']


def test_reexecute_missing_child_is_404(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reexecute_child', return_value=None), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/sessions/s-1/reexecute', headers=admin_headers)
    assert resp.status_code == 404
