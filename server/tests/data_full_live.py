"""数据管理全量 E2E —— L2 live-server 公共助手。

与 server/tests/ 既有 mock-DB 单测不同：本模块打真实 dev 栈——
经 Vite :5173 代理访问 /api（Flask :3002 本体不挂 /api 前缀，代理把 /api
rewrite 掉再转发；故默认 BASE=:5173，可用 env DATA_FULL_BASE_URL 覆盖）。
服务未启动时由 fixture skip，不影响 `npm run test:server` 离线跑。
"""
import os
import time

import requests

BASE = os.environ.get('DATA_FULL_BASE_URL', 'http://localhost:5173')


def server_up() -> bool:
    try:
        return requests.get(f'{BASE}/api/auth/login', timeout=3).status_code in (200, 401, 405)
    except requests.ConnectionError:
        return False


def login() -> dict:
    r = requests.post(f'{BASE}/api/auth/login',
                      json={'username': 'admin', 'password': 'admin123'}, timeout=10)
    assert r.status_code == 200, f'login failed: {r.status_code} {r.text}'
    return {'Authorization': f"Bearer {r.json()['token']}"}


def api(method: str, path: str, headers: dict | None = None, json=None) -> requests.Response:
    return requests.request(method, f'{BASE}/api{path}',
                            headers=headers, json=json, timeout=30)


def upload_file(headers: dict, content: bytes, filename: str,
                collection=None, field_name=None) -> requests.Response:
    """POST /api/data-files/upload（multipart）→ 201 {id,name,size,mimeType,url}。

    collection/fieldName 为可选业务锚点（服务端仅记录，不校验 collection 存在）。
    """
    data = {}
    if collection is not None:
        data['collection'] = collection
    if field_name is not None:
        data['fieldName'] = field_name
    return requests.post(
        f'{BASE}/api/data-files/upload', headers=headers, data=data,
        files={'file': (filename, content, 'application/octet-stream')},
        timeout=30)


CRUD_FIELDS = [
    {'id': 'f1', 'label': '名称', 'fieldName': 'name', 'controlType': 'text',
     'required': True, 'order': 1, 'placeholder': '请输入名称'},
    {'id': 'f2', 'label': '数量', 'fieldName': 'qty', 'controlType': 'number',
     'required': False, 'order': 2, 'placeholder': '请输入数量'},
    {'id': 'f3', 'label': '状态', 'fieldName': 'status', 'controlType': 'select',
     'required': False, 'order': 3, 'placeholder': '请选择状态',
     'options': [{'label': '待处理', 'value': 'todo'},
                 {'label': '进行中', 'value': 'doing'},
                 {'label': '已完成', 'value': 'done'}]},
]


def make_page(headers: dict, family: str, purpose: str,
              fields=None, view_config=None) -> dict:
    """建 PageConfig(+viewConfig) + Menu，返回句柄 dict。数据页名称全局唯一。

    产品约束（routes/menus.py MENU_TYPES）：data 菜单是 level-3，父级必须是
    project 菜单，而 project 的父级必须是 workspace——所以这里按
    workspace → project → data 三级建链；祖先菜单 id 由 collection 确定性
    推导（menu-ws-*/menu-proj-*），句柄键保持 brief 约定的 5 个不变，
    drop_page 据此回收整条链。
    """
    ts = int(time.time() * 1000)
    collection = f'DTEST-{family}-{purpose}-{ts}'
    page_id = f'page-{collection}'
    body = {'id': page_id, 'name': collection,
            'description': f'数据管理 e2e {family}/{purpose}',
            'apiEndpoint': f'/{collection}',
            'fields': CRUD_FIELDS if fields is None else fields}
    r = api('POST', '/pageConfigs', headers, body)
    assert r.status_code == 201, f'pageConfig create failed: {r.status_code} {r.text}'
    if view_config is not None:
        r2 = api('PUT', f'/pageConfigs/{page_id}', headers,
                 {'viewConfig': view_config})
        assert r2.status_code < 300, f'viewConfig PUT failed: {r2.status_code} {r2.text}'
    ws_id = f'menu-ws-{collection}'
    r_ws = api('POST', '/menus', headers,
               {'id': ws_id, 'name': f'{collection}-ws', 'menuType': 'workspace',
                'path': f'/dtest-ws/{collection}', 'order': 9999})
    assert r_ws.status_code == 201, f'workspace menu create failed: {r_ws.status_code} {r_ws.text}'
    proj_id = f'menu-proj-{collection}'
    r_proj = api('POST', '/menus', headers,
                 {'id': proj_id, 'name': f'{collection}-proj', 'menuType': 'project',
                  'parentId': ws_id, 'path': f'/dtest-proj/{collection}', 'order': 9999})
    assert r_proj.status_code == 201, f'project menu create failed: {r_proj.status_code} {r_proj.text}'
    r3 = api('POST', '/menus', headers,
             {'id': f'menu-{collection}', 'name': collection, 'pageId': page_id,
              'parentId': proj_id,
              'path': f'/dtest/{collection}', 'menuType': 'data',
              'roles': ['admin', 'developer', 'guest'], 'order': 9999})
    assert r3.status_code == 201, f'menu create failed: {r3.status_code} {r3.text}'
    return {'collection': collection, 'page_id': page_id,
            'menu_id': r3.json().get('id', f'menu-{collection}'),
            'path': f'/dtest/{collection}', 'name': collection}


def drop_page(headers: dict, page: dict) -> None:
    """二段删：记录 → pageConfig → menu 链（menu 删除不级联）。

    data 菜单挂在 make_page 建的 workspace/project 祖先链下，这里一并删；
    删除顺序 data → project → workspace（无级联依赖，仅按层级习惯）。
    """
    r = api('GET', f"/{page['collection']}?all=true", headers)
    for rec in (r.json() or {}).get('data') or []:
        api('DELETE', f"/{page['collection']}/{rec['id']}", headers)
    api('DELETE', f"/pageConfigs/{page['page_id']}", headers)
    api('DELETE', f"/menus/{page['menu_id']}", headers)
    collection = page['collection']
    api('DELETE', f"/menus/menu-proj-{collection}", headers)
    api('DELETE', f"/menus/menu-ws-{collection}", headers)
