# 自定义列视图功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现数据页自定义列视图功能，支持50+字段的差异化展示

**Architecture:** 新建 `column_views` 表存储视图配置，后端提供 CRUD API，前端通过 Pinia Store 管理视图状态，DataTable 接收列配置渲染

**Tech Stack:** Vue 3 + TypeScript + Pinia, Flask + psycopg2, PostgreSQL JSONB

---

## Task 1: 数据库迁移

**Files:**
- Create: `server/migrations/0025_create_column_views.py`

- [ ] **Step 1: 创建迁移脚本**

```python
"""
Migration: Create column_views table for custom column views.

Adds support for user-defined views with different column visibility,
order, width, sort, filter, and group configurations.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from db import get_db


def up():
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS column_views (
                id SERIAL PRIMARY KEY,
                page_id VARCHAR(100) NOT NULL REFERENCES page_configs(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                is_public BOOLEAN DEFAULT false,
                creator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                is_default BOOLEAN DEFAULT false,
                columns JSONB NOT NULL DEFAULT '[]'::jsonb,
                sort_config JSONB DEFAULT '[]'::jsonb,
                filter_config JSONB DEFAULT '[]'::jsonb,
                group_config JSONB DEFAULT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX idx_column_views_page ON column_views(page_id);
            CREATE INDEX idx_column_views_creator ON column_views(creator_id);
            CREATE INDEX idx_column_views_public ON column_views(is_public) WHERE is_public = true;
        """)

        conn.commit()
        print("Migration 0025: column_views table created successfully")


def down():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS column_views CASCADE")
        conn.commit()
        print("Migration 0025: column_views table dropped")


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'down':
        down()
    else:
        up()
```

- [ ] **Step 2: 运行迁移验证**

Run: `cd server && python migrations/0025_create_column_views.py`
Expected: "Migration 0025: column_views table created successfully"

- [ ] **Step 3: 提交**

```bash
git add server/migrations/0025_create_column_views.py
git commit -m "feat: add column_views table migration"
```

---

## Task 2: 后端 API 路由

**Files:**
- Create: `server/routes/column_views.py`

- [ ] **Step 1: 创建路由文件**

```python
"""
列视图 API 路由

提供列视图的 CRUD 操作、复制、设置默认等功能。
"""

from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required, write_required, admin_required
from datetime import datetime, timezone

column_views_bp = Blueprint('column_views', __name__, url_prefix='/column-views')


def format_ts(dt):
    """Format datetime to ISO 8601 with trailing Z."""
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def row_to_dict(row):
    """Convert database row to JSON-serializable dict."""
    return {
        'id': row[0],
        'pageId': row[1],
        'name': row[2],
        'isPublic': row[3],
        'creatorId': row[4],
        'isDefault': row[5],
        'columns': row[6] or [],
        'sortConfig': row[7] or [],
        'filterConfig': row[8] or [],
        'groupConfig': row[9],
        'createdAt': format_ts(row[10]),
        'updatedAt': format_ts(row[11]),
    }


@column_views_bp.route('/<page_id>/views', methods=['GET'])
@login_required
def get_views(page_id):
    """获取页面的所有视图（公共 + 当前用户私人）"""
    user_id = g.current_user['userId']

    with get_db() as conn:
        cur = conn.cursor()

        # 验证 page_id 存在
        cur.execute('SELECT id FROM page_configs WHERE id = %s', (page_id,))
        if not cur.fetchone():
            return jsonify({'error': '页面配置不存在'}), 404

        # 获取公共视图
        cur.execute("""
            SELECT id, page_id, name, is_public, creator_id, is_default,
                   columns, sort_config, filter_config, group_config, created_at, updated_at
            FROM column_views
            WHERE page_id = %s AND is_public = true
            ORDER BY is_default DESC, created_at ASC
        """, (page_id,))
        public_views = [row_to_dict(r) for r in cur.fetchall()]

        # 获取用户私人视图
        cur.execute("""
            SELECT id, page_id, name, is_public, creator_id, is_default,
                   columns, sort_config, filter_config, group_config, created_at, updated_at
            FROM column_views
            WHERE page_id = %s AND is_public = false AND creator_id = %s
            ORDER BY created_at ASC
        """, (page_id, user_id))
        private_views = [row_to_dict(r) for r in cur.fetchall()]

        # 查找默认视图 ID
        default_view_id = None
        for v in public_views:
            if v['isDefault']:
                default_view_id = v['id']
                break

    return jsonify({
        'views': public_views + private_views,
        'defaultViewId': default_view_id,
    })


@column_views_bp.route('/<page_id>/views', methods=['POST'])
@write_required
def create_view(page_id):
    """创建新视图"""
    user_id = g.current_user['userId']
    user_role = g.current_user['role']
    body = request.get_json(force=True)

    is_public = body.get('isPublic', False)

    # 公共视图只能 admin 创建
    if is_public and user_role != 'admin':
        return jsonify({'error': '只有管理员可创建公共视图'}), 403

    name = body.get('name', '').strip()
    if not name:
        return jsonify({'error': '视图名称不能为空'}), 400

    with get_db() as conn:
        cur = conn.cursor()

        # 验证 page_id 存在
        cur.execute('SELECT id FROM page_configs WHERE id = %s', (page_id,))
        if not cur.fetchone():
            return jsonify({'error': '页面配置不存在'}), 404

        # 检查名称唯一性
        if is_public:
            cur.execute(
                'SELECT id FROM column_views WHERE page_id = %s AND is_public = true AND name = %s',
                (page_id, name)
            )
        else:
            cur.execute(
                'SELECT id FROM column_views WHERE page_id = %s AND is_public = false AND creator_id = %s AND name = %s',
                (page_id, user_id, name)
            )
        if cur.fetchone():
            return jsonify({'error': '视图名称已存在'}), 400

        # 创建视图
        now = datetime.now(timezone.utc)
        cur.execute("""
            INSERT INTO column_views (page_id, name, is_public, creator_id, is_default,
                                     columns, sort_config, filter_config, group_config,
                                     created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, page_id, name, is_public, creator_id, is_default,
                      columns, sort_config, filter_config, group_config, created_at, updated_at
        """, (
            page_id,
            name,
            is_public,
            None if is_public else user_id,
            body.get('isDefault', False) and is_public,
            __import__('psycopg2').extras.Json(body.get('columns', [])),
            __import__('psycopg2').extras.Json(body.get('sortConfig', [])),
            __import__('psycopg2').extras.Json(body.get('filterConfig', [])),
            __import__('psycopg2').extras.Json(body.get('groupConfig')),
            now,
            now,
        ))
        row = cur.fetchone()
        conn.commit()

    return jsonify(row_to_dict(row)), 201


@column_views_bp.route('/<page_id>/views/<int:view_id>', methods=['PUT'])
@login_required
def update_view(page_id, view_id):
    """更新视图"""
    user_id = g.current_user['userId']
    user_role = g.current_user['role']
    body = request.get_json(force=True)

    with get_db() as conn:
        cur = conn.cursor()

        # 获取视图
        cur.execute("""
            SELECT id, page_id, name, is_public, creator_id, is_default,
                   columns, sort_config, filter_config, group_config, created_at, updated_at
            FROM column_views WHERE id = %s AND page_id = %s
        """, (view_id, page_id))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '视图不存在'}), 404

        view = row_to_dict(row)

        # 权限检查：创建者或 admin
        if view['creatorId'] != user_id and user_role != 'admin':
            return jsonify({'error': '只能修改自己的私人视图'}), 403

        # 构建更新语句
        sets = []
        params = []

        if 'name' in body:
            name = body['name'].strip()
            if not name:
                return jsonify({'error': '视图名称不能为空'}), 400
            sets.append('name=%s')
            params.append(name)

        if 'columns' in body:
            sets.append('columns=%s')
            params.append(__import__('psycopg2').extras.Json(body['columns']))

        if 'sortConfig' in body:
            sets.append('sort_config=%s')
            params.append(__import__('psycopg2').extras.Json(body['sortConfig']))

        if 'filterConfig' in body:
            sets.append('filter_config=%s')
            params.append(__import__('psycopg2').extras.Json(body['filterConfig']))

        if 'groupConfig' in body:
            sets.append('group_config=%s')
            params.append(__import__('psycopg2').extras.Json(body['groupConfig']))

        if not sets:
            return jsonify({'error': '没有可更新的字段'}), 400

        now = datetime.now(timezone.utc)
        sets.append('updated_at=%s')
        params.append(now)
        params.append(view_id)
        params.append(page_id)

        cur.execute(f"""
            UPDATE column_views SET {', '.join(sets)}
            WHERE id=%s AND page_id=%s
        """, params)

        # 返回更新后的数据
        cur.execute("""
            SELECT id, page_id, name, is_public, creator_id, is_default,
                   columns, sort_config, filter_config, group_config, created_at, updated_at
            FROM column_views WHERE id = %s
        """, (view_id,))
        updated_row = cur.fetchone()
        conn.commit()

    return jsonify(row_to_dict(updated_row))


@column_views_bp.route('/<page_id>/views/<int:view_id>', methods=['DELETE'])
@login_required
def delete_view(page_id, view_id):
    """删除视图"""
    user_id = g.current_user['userId']
    user_role = g.current_user['role']

    with get_db() as conn:
        cur = conn.cursor()

        # 获取视图
        cur.execute("""
            SELECT id, creator_id, is_default
            FROM column_views WHERE id = %s AND page_id = %s
        """, (view_id, page_id))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '视图不存在'}), 404

        # 权限检查
        if row[1] != user_id and user_role != 'admin':
            return jsonify({'error': '只能删除自己的私人视图'}), 403

        # 不能删除默认视图
        if row[2]:
            return jsonify({'error': '请先取消默认设置再删除'}), 400

        cur.execute('DELETE FROM column_views WHERE id = %s', (view_id,))
        conn.commit()

    return jsonify({})


@column_views_bp.route('/<page_id>/views/<int:view_id>/default', methods=['PUT'])
@admin_required
def set_default_view(page_id, view_id):
    """设置为默认视图（仅 admin）"""
    with get_db() as conn:
        cur = conn.cursor()

        # 验证视图存在且为公共视图
        cur.execute("""
            SELECT id, is_public FROM column_views
            WHERE id = %s AND page_id = %s
        """, (view_id, page_id))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '视图不存在'}), 404
        if not row[1]:
            return jsonify({'error': '只能将公共视图设为默认'}), 400

        # 清除该页面其他默认
        cur.execute(
            'UPDATE column_views SET is_default = false WHERE page_id = %s',
            (page_id,)
        )

        # 设置新的默认
        cur.execute(
            'UPDATE column_views SET is_default = true WHERE id = %s',
            (view_id,)
        )
        conn.commit()

    return jsonify({'success': True})


@column_views_bp.route('/<page_id>/views/<int:view_id>/copy', methods=['POST'])
@write_required
def copy_view(page_id, view_id):
    """复制视图"""
    user_id = g.current_user['userId']
    user_role = g.current_user['role']
    body = request.get_json(force=True) or {}

    with get_db() as conn:
        cur = conn.cursor()

        # 获取源视图
        cur.execute("""
            SELECT id, page_id, name, is_public, creator_id, is_default,
                   columns, sort_config, filter_config, group_config
            FROM column_views WHERE id = %s AND page_id = %s
        """, (view_id, page_id))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '源视图不存在'}), 404

        source = row_to_dict(row)

        # 确定新视图名称
        new_name = body.get('name', f'{source["name"]}（副本）').strip()

        # 确定新视图类型
        new_is_public = body.get('isPublic', False)
        if new_is_public and user_role != 'admin':
            new_is_public = False

        # 检查名称唯一性
        if new_is_public:
            cur.execute(
                'SELECT id FROM column_views WHERE page_id = %s AND is_public = true AND name = %s',
                (page_id, new_name)
            )
        else:
            cur.execute(
                'SELECT id FROM column_views WHERE page_id = %s AND is_public = false AND creator_id = %s AND name = %s',
                (page_id, user_id, new_name)
            )
        if cur.fetchone():
            new_name = f'{new_name}_{int(datetime.now().timestamp())}'

        # 创建副本
        now = datetime.now(timezone.utc)
        cur.execute("""
            INSERT INTO column_views (page_id, name, is_public, creator_id, is_default,
                                     columns, sort_config, filter_config, group_config,
                                     created_at, updated_at)
            VALUES (%s, %s, %s, %s, false, %s, %s, %s, %s, %s, %s)
            RETURNING id, page_id, name, is_public, creator_id, is_default,
                      columns, sort_config, filter_config, group_config, created_at, updated_at
        """, (
            page_id,
            new_name,
            new_is_public,
            None if new_is_public else user_id,
            __import__('psycopg2').extras.Json(source['columns']),
            __import__('psycopg2').extras.Json(source['sortConfig']),
            __import__('psycopg2').extras.Json(source['filterConfig']),
            __import__('psycopg2').extras.Json(source['groupConfig']),
            now,
            now,
        ))
        new_row = cur.fetchone()
        conn.commit()

    return jsonify(row_to_dict(new_row)), 201
```

- [ ] **Step 2: 注册蓝图到 app.py**

修改 `server/app.py`，在 `dynamic_bp` 之前添加:

```python
from routes.column_views import column_views_bp
```

并在 `app.register_blueprint(dynamic_bp)` 之前添加:

```python
app.register_blueprint(column_views_bp)
```

- [ ] **Step 3: 启动服务器验证**

Run: `npm run dev:all`
Expected: 后端启动无报错

- [ ] **Step 4: 提交**

```bash
git add server/routes/column_views.py server/app.py
git commit -m "feat: add column views API routes"
```

---

## Task 3: 后端单元测试

**Files:**
- Create: `server/tests/test_routes_column_views.py`

- [ ] **Step 1: 创建测试文件**

```python
"""
列视图路由单元测试

测试列视图 CRUD、权限控制、复制等功能。
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.auth.get_db', fake_db),
        patch('routes.column_views.get_db', fake_db),
        patch('db.pool', MagicMock()),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
    dev = create_token({'id': 'u2', 'username': 'dev', 'role': 'developer'})
    guest = create_token({'id': 'u3', 'username': 'guest', 'role': 'guest'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
        {'Authorization': f'Bearer {dev}'},
        {'Authorization': f'Bearer {guest}'},
    )

    for p in patches:
        p.stop()


now = datetime(2024, 1, 1, tzinfo=timezone.utc)


class TestGetViews:
    def test_returns_views(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        # page exists
        mock_cursor.fetchone.side_effect = [
            ('page-1',),  # page exists
            None,  # no more fetchone
        ]
        mock_cursor.fetchall.side_effect = [
            [(1, 'page-1', '默认视图', True, None, True,
              [{'fieldId': 'f1', 'visible': True, 'order': 0, 'width': '120px'}],
              [], [], None, now, now)],
            [],
        ]
        resp = client.get('/column-views/page-1/views', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'views' in data
        assert 'defaultViewId' in data
        assert len(data['views']) == 1
        assert data['views'][0]['name'] == '默认视图'

    def test_not_found(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.get('/column-views/nonexistent/views', headers=admin_h)
        assert resp.status_code == 404

    def test_no_token_401(self, setup):
        client, _, _, _, _ = setup
        resp = client.get('/column-views/page-1/views')
        assert resp.status_code == 401


class TestCreateView:
    def test_admin_create_public(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.side_effect = [
            ('page-1',),  # page exists
            None,  # name not exists
            (1, 'page-1', '新视图', True, None, False, [], [], [], None, now, now),  # inserted
        ]
        resp = client.post('/column-views/page-1/views',
                           data=json.dumps({'name': '新视图', 'isPublic': True, 'columns': []}),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201
        assert resp.get_json()['name'] == '新视图'

    def test_developer_create_private(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        mock_cursor.fetchone.side_effect = [
            ('page-1',),  # page exists
            None,  # name not exists
            (2, 'page-1', '我的视图', False, 'u2', False, [], [], [], None, now, now),
        ]
        resp = client.post('/column-views/page-1/views',
                           data=json.dumps({'name': '我的视图', 'isPublic': False, 'columns': []}),
                           content_type='application/json',
                           headers=dev_h)
        assert resp.status_code == 201
        assert resp.get_json()['isPublic'] == False

    def test_developer_create_public_forbidden(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        mock_cursor.fetchone.return_value = ('page-1',)
        resp = client.post('/column-views/page-1/views',
                           data=json.dumps({'name': '新视图', 'isPublic': True}),
                           content_type='application/json',
                           headers=dev_h)
        assert resp.status_code == 403

    def test_guest_create_forbidden(self, setup):
        client, mock_cursor, _, _, guest_h = setup
        resp = client.post('/column-views/page-1/views',
                           data=json.dumps({'name': '新视图'}),
                           content_type='application/json',
                           headers=guest_h)
        assert resp.status_code == 403


class TestUpdateView:
    def test_owner_update(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        mock_cursor.fetchone.side_effect = [
            (1, 'page-1', '旧名称', False, 'u2', False, [], [], [], None, now, now),  # view
            (1, 'page-1', '新名称', False, 'u2', False, [], [], [], None, now, now),  # updated
        ]
        resp = client.put('/column-views/page-1/views/1',
                          data=json.dumps({'name': '新名称'}),
                          content_type='application/json',
                          headers=dev_h)
        assert resp.status_code == 200

    def test_other_user_forbidden(self, setup):
        client, mock_cursor, _, _, guest_h = setup
        mock_cursor.fetchone.return_value = (
            1, 'page-1', '视图', False, 'u2', False, [], [], [], None, now, now
        )
        resp = client.put('/column-views/page-1/views/1',
                          data=json.dumps({'name': '改名'}),
                          content_type='application/json',
                          headers=guest_h)
        assert resp.status_code == 403


class TestDeleteView:
    def test_owner_delete(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        mock_cursor.fetchone.return_value = (1, 'u2', False)
        resp = client.delete('/column-views/page-1/views/1', headers=dev_h)
        assert resp.status_code == 200

    def test_delete_default_forbidden(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.return_value = (1, 'u1', True)  # is_default=True
        resp = client.delete('/column-views/page-1/views/1', headers=admin_h)
        assert resp.status_code == 400


class TestSetDefaultView:
    def test_admin_set_default(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.return_value = (1, True)  # exists and is_public
        resp = client.put('/column-views/page-1/views/1/default', headers=admin_h)
        assert resp.status_code == 200

    def test_developer_forbidden(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        mock_cursor.fetchone.return_value = (1, True)
        resp = client.put('/column-views/page-1/views/1/default', headers=dev_h)
        assert resp.status_code == 403


class TestCopyView:
    def test_copy_success(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        mock_cursor.fetchone.side_effect = [
            (1, 'page-1', '源视图', False, 'u2', False, [], [], [], None),  # source
            None,  # name not exists
            (2, 'page-1', '源视图（副本）', False, 'u2', False, [], [], [], None, now, now),  # inserted
        ]
        resp = client.post('/column-views/page-1/views/1/copy',
                           content_type='application/json',
                           headers=dev_h)
        assert resp.status_code == 201
        assert resp.get_json()['name'] == '源视图（副本）'
```

- [ ] **Step 2: 运行测试**

Run: `cd server && python -m pytest tests/test_routes_column_views.py -v`
Expected: All tests PASS

- [ ] **Step 3: 提交**

```bash
git add server/tests/test_routes_column_views.py
git commit -m "test: add column views API tests"
```

---

## Task 4: 前端类型定义

**Files:**
- Create: `src/types/columnView.ts`
- Modify: `src/types/index.ts`

- [ ] **Step 1: 创建类型文件**

```typescript
/**
 * 列视图类型定义
 *
 * 定义自定义列视图的数据结构，包括：
 * - ColumnView: 视图主体
 * - ColumnConfigItem: 列显示/顺序/宽度配置
 * - SortConfigItem: 默认排序配置
 * - FilterConfigItem: 默认筛选配置
 * - GroupConfig: 分组配置
 */

export interface ColumnView {
  id: number
  pageId: string
  name: string
  isPublic: boolean
  isDefault: boolean
  creatorId: number | null
  columns: ColumnConfigItem[]
  sortConfig: SortConfigItem[]
  filterConfig: FilterConfigItem[]
  groupConfig: GroupConfig | null
  createdAt: string
  updatedAt: string
}

export interface ColumnConfigItem {
  fieldId: string
  visible: boolean
  order: number
  width: string
}

export interface SortConfigItem {
  field: string
  direction: 'asc' | 'desc'
}

export interface FilterConfigItem {
  field: string
  operator: '=' | '!=' | 'contains' | '>' | '<' | '>=' | '<='
  value: any
}

export interface GroupConfig {
  field: string
  order?: string[]
}

export interface GetViewsResponse {
  views: ColumnView[]
  defaultViewId: number | null
}

export interface CreateViewRequest {
  name: string
  isPublic: boolean
  columns: ColumnConfigItem[]
  sortConfig?: SortConfigItem[]
  filterConfig?: FilterConfigItem[]
  groupConfig?: GroupConfig | null
}

export interface UpdateViewRequest {
  name?: string
  columns?: ColumnConfigItem[]
  sortConfig?: SortConfigItem[]
  filterConfig?: FilterConfigItem[]
  groupConfig?: GroupConfig | null
}

export interface CopyViewRequest {
  name?: string
  isPublic?: boolean
}
```

- [ ] **Step 2: 修改 index.ts 导出类型**

修改 `src/types/index.ts`，添加导出:

```typescript
// ... existing exports ...
export * from './columnView'
```

- [ ] **Step 3: 提交**

```bash
git add src/types/columnView.ts src/types/index.ts
git commit -m "feat: add column view TypeScript types"
```

---

## Task 5: 前端 API 封装

**Files:**
- Create: `src/api/columnView.ts`

- [ ] **Step 1: 创建 API 文件**

```typescript
/**
 * 列视图 API 封装
 */

import { get, post, put, del } from '@/utils/request'
import type {
  GetViewsResponse,
  ColumnView,
  CreateViewRequest,
  UpdateViewRequest,
  CopyViewRequest
} from '@/types'

/**
 * 获取页面的列视图列表
 */
export function getColumnViews(pageId: string) {
  return get<GetViewsResponse>(`/column-views/${pageId}/views`)
}

/**
 * 创建列视图
 */
export function createColumnView(pageId: string, data: CreateViewRequest) {
  return post<ColumnView>(`/column-views/${pageId}/views`, data)
}

/**
 * 更新列视图
 */
export function updateColumnView(pageId: string, viewId: number, data: UpdateViewRequest) {
  return put<ColumnView>(`/column-views/${pageId}/views/${viewId}`, data)
}

/**
 * 删除列视图
 */
export function deleteColumnView(pageId: string, viewId: number) {
  return del(`/column-views/${pageId}/views/${viewId}`)
}

/**
 * 设置默认视图
 */
export function setDefaultColumnView(pageId: string, viewId: number) {
  return put(`/column-views/${pageId}/views/${viewId}/default`)
}

/**
 * 复制列视图
 */
export function copyColumnView(pageId: string, viewId: number, data?: CopyViewRequest) {
  return post<ColumnView>(`/column-views/${pageId}/views/${viewId}/copy`, data || {})
}
```

- [ ] **Step 2: 提交**

```bash
git add src/api/columnView.ts
git commit -m "feat: add column view API client"
```

---

## Task 6: Pinia Store

**Files:**
- Create: `src/stores/columnView.ts`

- [ ] **Step 1: 创建 Store 文件**

```typescript
/**
 * 列视图状态管理 Store
 *
 * 管理数据页的自定义列视图，包括：
 * - 视图列表加载和缓存
 * - 视图创建/更新/删除/复制
 * - 当前选中视图状态
 * - 生成 DataTable 使用的列配置
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { FieldConfig } from '@/types'
import type {
  ColumnView,
  ColumnConfigItem,
  CreateViewRequest,
  UpdateViewRequest
} from '@/types'
import {
  getColumnViews,
  createColumnView,
  updateColumnView,
  deleteColumnView,
  setDefaultColumnView,
  copyColumnView
} from '@/api/columnView'

export const useColumnViewStore = defineStore('columnView', () => {
  // ==================== State ====================

  const views = ref<ColumnView[]>([])
  const currentViewId = ref<number | null>(null)
  const defaultViewId = ref<number | null>(null)
  const loading = ref(false)

  // ==================== Getters ====================

  const currentView = computed(() =>
    views.value.find(v => v.id === currentViewId.value) || null
  )

  const publicViews = computed(() =>
    views.value.filter(v => v.isPublic)
  )

  const myViews = computed(() =>
    views.value.filter(v => !v.isPublic)
  )

  // ==================== Actions ====================

  async function loadViews(pageId: string) {
    loading.value = true
    try {
      const res = await getColumnViews(pageId)
      views.value = res.views
      defaultViewId.value = res.defaultViewId

      // 尝试恢复上次选择的视图
      const lastViewId = localStorage.getItem(`view:${pageId}`)
      if (lastViewId) {
        const id = Number(lastViewId)
        if (views.value.some(v => v.id === id)) {
          currentViewId.value = id
          return
        }
      }
      // 否则使用默认视图
      currentViewId.value = res.defaultViewId
    } catch (error) {
      console.error('加载列视图失败:', error)
      throw error
    } finally {
      loading.value = false
    }
  }

  async function createView(pageId: string, data: CreateViewRequest) {
    const newView = await createColumnView(pageId, data)
    views.value.push(newView)
    return newView
  }

  async function updateView(pageId: string, viewId: number, data: UpdateViewRequest) {
    const updated = await updateColumnView(pageId, viewId, data)
    const index = views.value.findIndex(v => v.id === viewId)
    if (index !== -1) {
      views.value[index] = updated
    }
    return updated
  }

  async function removeView(pageId: string, viewId: number) {
    await deleteColumnView(pageId, viewId)
    views.value = views.value.filter(v => v.id !== viewId)
    if (currentViewId.value === viewId) {
      currentViewId.value = defaultViewId.value
    }
  }

  async function setDefault(pageId: string, viewId: number) {
    await setDefaultColumnView(pageId, viewId)
    // 更新本地状态
    views.value.forEach(v => { v.isDefault = v.id === viewId })
    defaultViewId.value = viewId
  }

  async function copyView(pageId: string, viewId: number) {
    const newView = await copyColumnView(pageId, viewId)
    views.value.push(newView)
    return newView
  }

  function selectView(pageId: string, viewId: number | null) {
    currentViewId.value = viewId
    if (viewId !== null) {
      localStorage.setItem(`view:${pageId}`, String(viewId))
    } else {
      localStorage.removeItem(`view:${pageId}`)
    }
  }

  function clearState() {
    views.value = []
    currentViewId.value = null
    defaultViewId.value = null
  }

  // ==================== 生成表格列配置 ====================

  function getTableColumns(allFields: FieldConfig[]): FieldConfig[] {
    if (!currentView.value) {
      // 无视图配置时使用默认逻辑
      return allFields.filter(f => !f.hidden).sort((a, b) => a.order - b.order)
    }

    const configMap = new Map<string, ColumnConfigItem>(
      currentView.value.columns.map(c => [c.fieldId, c])
    )

    return allFields
      .filter(f => {
        const config = configMap.get(f.id)
        return config && config.visible
      })
      .map(f => {
        const config = configMap.get(f.id)!
        return {
          ...f,
          order: config.order,
          width: config.width !== 'auto' ? config.width : f.width
        }
      })
      .sort((a, b) => a.order - b.order)
  }

  return {
    // State
    views,
    currentViewId,
    defaultViewId,
    loading,
    // Getters
    currentView,
    publicViews,
    myViews,
    // Actions
    loadViews,
    createView,
    updateView,
    removeView,
    setDefault,
    copyView,
    selectView,
    clearState,
    // Helpers
    getTableColumns,
  }
})
```

- [ ] **Step 2: 提交**

```bash
git add src/stores/columnView.ts
git commit -m "feat: add column view Pinia store"
```

---

## Task 7: 视图选择器组件

**Files:**
- Create: `src/components/column-view/ViewSelector.vue`

- [ ] **Step 1: 创建组件**

```vue
<template>
  <div class="view-selector">
    <el-select
      :model-value="currentViewId"
      placeholder="选择视图"
      clearable
      style="width: 200px"
      @update:model-value="handleSelect"
    >
      <template #prefix>
        <el-icon><View /></el-icon>
      </template>

      <el-option-group label="公共视图" v-if="publicViews.length">
        <el-option
          v-for="view in publicViews"
          :key="view.id"
          :value="view.id"
          :label="view.name + (view.isDefault ? '（默认）' : '')"
        />
      </el-option-group>

      <el-option-group label="我的视图" v-if="myViews.length">
        <el-option
          v-for="view in myViews"
          :key="view.id"
          :value="view.id"
          :label="view.name"
        />
      </el-option-group>
    </el-select>

    <el-button v-if="!isGuest" @click="handleManage" title="管理视图">
      <el-icon><Setting /></el-icon>
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useColumnViewStore } from '@/stores/columnView'
import { useAuthStore } from '@/stores/auth'
import { View, Setting } from '@element-plus/icons-vue'

const columnViewStore = useColumnViewStore()
const authStore = useAuthStore()

const isGuest = computed(() => authStore.currentUser?.role === 'guest')
const currentViewId = computed(() => columnViewStore.currentViewId)
const publicViews = computed(() => columnViewStore.publicViews)
const myViews = computed(() => columnViewStore.myViews)

const emit = defineEmits<{
  manage: []
}>()

function handleSelect(viewId: number | null) {
  // 传入 pageId 需要从外部传入
  // 这里通过 emit 处理
  emit('select' as any, viewId)
}

function handleManage() {
  emit('manage')
}

defineExpose({
  selectView(viewId: number | null) {
    // 外部调用时传入 pageId
  }
})
</script>

<style scoped>
.view-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add src/components/column-view/ViewSelector.vue
git commit -m "feat: add ViewSelector component"
```

---

## Task 8: 视图管理弹窗

**Files:**
- Create: `src/components/column-view/ViewManageDialog.vue`
- Create: `src/components/column-view/ViewEditPanel.vue`

- [ ] **Step 1: 创建 ViewEditPanel.vue**

```vue
<template>
  <div class="view-edit-panel" v-if="view">
    <el-form label-position="top" :model="formData">
      <el-form-item label="视图名称">
        <el-input v-model="formData.name" placeholder="请输入视图名称" />
      </el-form-item>

      <el-form-item label="视图类型">
        <el-tag :type="view.isPublic ? 'success' : 'info'">
          {{ view.isPublic ? '公共视图' : '私人视图' }}
        </el-tag>
      </el-form-item>

      <el-form-item label="创建者" v-if="view.creatorId">
        <span>{{ view.creatorId }}</span>
      </el-form-item>

      <el-form-item>
        <el-checkbox
          v-if="view.isPublic && isAdmin"
          v-model="formData.isDefault"
          @change="handleSetDefault"
        >
          设为默认视图
        </el-checkbox>
      </el-form-item>

      <el-form-item label="列配置">
        <el-button type="primary" @click="handleEditColumns">
          编辑列配置
        </el-button>
        <span class="column-count" v-if="view.columns">
          {{ view.columns.filter(c => c.visible).length }} 列
        </span>
      </el-form-item>

      <el-divider />

      <div class="action-buttons">
        <el-button type="primary" @click="handleSave" :disabled="!hasChanges">
          保存
        </el-button>
        <el-button @click="handleCopy">复制</el-button>
        <el-button type="danger" @click="handleDelete" :disabled="view.isDefault">
          删除
        </el-button>
      </div>
    </el-form>
  </div>
  <div v-else class="empty-panel">
    选择一个视图进行编辑
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { ElMessageBox } from 'element-plus'
import type { ColumnView } from '@/types'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  view: ColumnView | null
}>()

const emit = defineEmits<{
  save: [name: string, isDefault: boolean]
  copy: []
  delete: []
  'edit-columns': []
}>()

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.currentUser?.role === 'admin')

const formData = ref({
  name: '',
  isDefault: false
})

const hasChanges = computed(() => {
  if (!props.view) return false
  return formData.value.name !== props.view.name
})

watch(() => props.view, (newView) => {
  if (newView) {
    formData.value.name = newView.name
    formData.value.isDefault = newView.isDefault
  }
}, { immediate: true })

function handleSave() {
  emit('save', formData.value.name, formData.value.isDefault)
}

function handleCopy() {
  emit('copy')
}

async function handleDelete() {
  await ElMessageBox.confirm('确定要删除此视图吗？', '确认删除', {
    type: 'warning'
  })
  emit('delete')
}

function handleSetDefault(value: boolean) {
  if (value) {
    emit('save', formData.value.name, true)
  }
}

function handleEditColumns() {
  emit('edit-columns')
}
</script>

<style scoped>
.view-edit-panel {
  padding: 16px;
}

.column-count {
  margin-left: 12px;
  color: #909399;
  font-size: 12px;
}

.action-buttons {
  display: flex;
  gap: 8px;
}

.empty-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: #909399;
}
</style>
```

- [ ] **Step 2: 创建 ViewManageDialog.vue**

```vue
<template>
  <el-dialog
    v-model="visible"
    title="管理视图"
    width="700px"
    :close-on-click-modal="false"
  >
    <div class="view-manage-container">
      <!-- 左侧：视图列表 -->
      <div class="view-list">
        <div class="view-list-header">
          <span>视图列表</span>
          <el-button size="small" type="primary" @click="handleCreate">
            + 新建
          </el-button>
        </div>
        <div class="view-list-content">
          <div
            v-for="view in views"
            :key="view.id"
            class="view-item"
            :class="{ active: selectedViewId === view.id }"
            @click="selectedViewId = view.id"
          >
            <div class="view-item-name">
              {{ view.name }}
              <el-tag v-if="view.isDefault" size="small" type="success">默认</el-tag>
            </div>
            <div class="view-item-meta">
              {{ view.isPublic ? '公共' : '私人' }}
              · {{ view.columns.filter(c => c.visible).length }}列
            </div>
          </div>
          <div v-if="views.length === 0" class="empty-list">
            暂无视图
          </div>
        </div>
      </div>

      <!-- 右侧：编辑面板 -->
      <div class="view-edit">
        <ViewEditPanel
          :view="selectedView"
          @save="handleSave"
          @copy="handleCopy"
          @delete="handleDelete"
          @edit-columns="handleEditColumns"
        />
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useColumnViewStore } from '@/stores/columnView'
import ViewEditPanel from './ViewEditPanel.vue'

const props = defineProps<{
  pageId: string
  fields: any[]
}>()

const emit = defineEmits<{
  'edit-columns': [view: any]
  close: []
}>()

const columnViewStore = useColumnViewStore()

const visible = ref(false)
const selectedViewId = ref<number | null>(null)

const views = computed(() => columnViewStore.views)
const selectedView = computed(() =>
  views.value.find(v => v.id === selectedViewId.value) || null
)

// 默认选中第一个视图
watch(views, (newViews) => {
  if (newViews.length > 0 && !selectedViewId.value) {
    selectedViewId.value = newViews[0].id
  }
}, { immediate: true })

function open() {
  visible.value = true
  columnViewStore.loadViews(props.pageId)
}

function close() {
  visible.value = false
  emit('close')
}

async function handleCreate() {
  try {
    const { value: name } = await ElMessageBox.prompt('请输入视图名称', '新建视图', {
      inputPattern: /^.{1,50}$/,
      inputErrorMessage: '名称长度1-50个字符'
    })

    // 生成默认列配置：所有可见字段
    const defaultColumns = props.fields
      .filter(f => !f.hidden)
      .map((f, i) => ({
        fieldId: f.id,
        visible: true,
        order: i,
        width: f.width || 'auto'
      }))

    const newView = await columnViewStore.createView(props.pageId, {
      name,
      isPublic: false,
      columns: defaultColumns
    })

    selectedViewId.value = newView.id
    ElMessage.success('创建成功')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('创建失败')
    }
  }
}

async function handleSave(name: string, isDefault: boolean) {
  if (!selectedView.value) return

  try {
    await columnViewStore.updateView(props.pageId, selectedView.value.id, { name })
    ElMessage.success('保存成功')
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

async function handleCopy() {
  if (!selectedView.value) return

  try {
    const newView = await columnViewStore.copyView(props.pageId, selectedView.value.id)
    selectedViewId.value = newView.id
    ElMessage.success('复制成功')
  } catch (error) {
    ElMessage.error('复制失败')
  }
}

async function handleDelete() {
  if (!selectedView.value) return

  try {
    await columnViewStore.removeView(props.pageId, selectedView.value.id)
    selectedViewId.value = views.value[0]?.id || null
    ElMessage.success('删除成功')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.error || '删除失败')
  }
}

function handleEditColumns() {
  if (!selectedView.value) return
  emit('edit-columns', selectedView.value)
  close()
}

defineExpose({ open, close })
</script>

<style scoped>
.view-manage-container {
  display: flex;
  gap: 16px;
  min-height: 400px;
}

.view-list {
  width: 250px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
}

.view-list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
  font-weight: 500;
}

.view-list-content {
  max-height: 350px;
  overflow-y: auto;
}

.view-item {
  padding: 12px;
  border-bottom: 1px solid #ebeef5;
  cursor: pointer;
  transition: background 0.2s;
}

.view-item:hover {
  background: #f5f7fa;
}

.view-item.active {
  background: #ecf5ff;
  border-left: 3px solid #409eff;
}

.view-item-name {
  font-size: 14px;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.view-item-meta {
  font-size: 12px;
  color: #909399;
}

.empty-list {
  padding: 40px;
  text-align: center;
  color: #909399;
}

.view-edit {
  flex: 1;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}
</style>
```

- [ ] **Step 3: 提交**

```bash
git add src/components/column-view/ViewManageDialog.vue src/components/column-view/ViewEditPanel.vue
git commit -m "feat: add ViewManageDialog and ViewEditPanel components"
```

---

## Task 9: 列配置弹窗

**Files:**
- Create: `src/components/column-view/ColumnConfigDialog.vue`

- [ ] **Step 1: 创建组件**

```vue
<template>
  <el-dialog
    v-model="visible"
    title="编辑列配置"
    width="600px"
    :close-on-click-modal="false"
  >
    <div class="column-config">
      <!-- 可拖拽列表 -->
      <draggable
        v-model="localColumns"
        item-key="fieldId"
        handle=".drag-handle"
        ghost-class="ghost"
        class="column-list"
      >
        <template #item="{ element }">
          <div class="column-item" :class="{ hidden: !element.visible }">
            <el-icon class="drag-handle"><Rank /></el-icon>
            <el-checkbox
              :model-value="element.visible"
              @change="(val: boolean) => toggleVisible(element.fieldId, val)"
            />
            <span class="field-label">{{ getFieldLabel(element.fieldId) }}</span>
            <el-input
              v-if="element.visible"
              :model-value="element.width"
              placeholder="auto"
              size="small"
              style="width: 80px"
              @update:model-value="(val: string) => updateWidth(element.fieldId, val)"
            />
            <span v-else class="width-disabled">-</span>
          </div>
        </template>
      </draggable>

      <el-divider />

      <!-- 排序配置 -->
      <div class="config-section">
        <h4>默认排序</h4>
        <div v-for="(sort, index) in localSortConfig" :key="index" class="sort-row">
          <el-select v-model="sort.field" placeholder="选择字段" style="width: 150px">
            <el-option
              v-for="f in sortableFields"
              :key="f.fieldName"
              :label="f.label"
              :value="f.fieldName"
            />
          </el-select>
          <el-select v-model="sort.direction" style="width: 100px">
            <el-option label="升序" value="asc" />
            <el-option label="降序" value="desc" />
          </el-select>
          <el-button text type="danger" @click="removeSort(index)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
        <el-button size="small" @click="addSort">+ 添加排序</el-button>
      </div>

      <!-- 分组配置 -->
      <div class="config-section">
        <h4>分组字段</h4>
        <el-select v-model="localGroupField" clearable placeholder="不分组" style="width: 200px">
          <el-option
            v-for="f in groupableFields"
            :key="f.fieldName"
            :label="f.label"
            :value="f.fieldName"
          />
        </el-select>
      </div>
    </div>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" @click="handleSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import draggable from 'vuedraggable'
import { Rank, Delete } from '@element-plus/icons-vue'
import type { FieldConfig, ColumnConfigItem, SortConfigItem } from '@/types'

const props = defineProps<{
  fields: FieldConfig[]
}>()

const emit = defineEmits<{
  save: [columns: ColumnConfigItem[], sortConfig: SortConfigItem[], groupField: string | null]
}>()

const visible = ref(false)
const localColumns = ref<ColumnConfigItem[]>([])
const localSortConfig = ref<SortConfigItem[]>([])
const localGroupField = ref<string | null>(null)

const sortableFields = computed(() =>
  props.fields.filter(f => !['relation', 'file', 'image'].includes(f.controlType))
)

const groupableFields = computed(() =>
  props.fields.filter(f => ['select', 'radio'].includes(f.controlType))
)

function getFieldLabel(fieldId: string): string {
  return props.fields.find(f => f.id === fieldId)?.label || fieldId
}

function toggleVisible(fieldId: string, visible: boolean) {
  const col = localColumns.value.find(c => c.fieldId === fieldId)
  if (col) col.visible = visible
}

function updateWidth(fieldId: string, width: string) {
  const col = localColumns.value.find(c => c.fieldId === fieldId)
  if (col) col.width = width || 'auto'
}

function addSort() {
  localSortConfig.value.push({ field: '', direction: 'asc' })
}

function removeSort(index: number) {
  localSortConfig.value.splice(index, 1)
}

function open(columns: ColumnConfigItem[], sortConfig: SortConfigItem[], groupField?: string | null) {
  localColumns.value = columns.map(c => ({ ...c }))
  localSortConfig.value = sortConfig.map(s => ({ ...s }))
  localGroupField.value = groupField || null
  visible.value = true
}

function close() {
  visible.value = false
}

function handleSave() {
  const validSorts = localSortConfig.value.filter(s => s.field)
  emit('save', localColumns.value, validSorts, localGroupField.value)
  close()
}

defineExpose({ open, close })
</script>

<style scoped>
.column-list {
  max-height: 300px;
  overflow-y: auto;
}

.column-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  margin-bottom: 4px;
  background: #fff;
  transition: all 0.2s;
}

.column-item.hidden {
  opacity: 0.5;
}

.column-item.ghost {
  opacity: 0.5;
  background: #ecf5ff;
}

.drag-handle {
  cursor: grab;
  color: #c0c4cc;
}

.drag-handle:active {
  cursor: grabbing;
}

.field-label {
  flex: 1;
  font-size: 14px;
}

.width-disabled {
  width: 80px;
  text-align: center;
  color: #c0c4cc;
}

.config-section {
  margin-bottom: 16px;
}

.config-section h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #606266;
}

.sort-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add src/components/column-view/ColumnConfigDialog.vue
git commit -m "feat: add ColumnConfigDialog with drag-and-drop"
```

---

## Task 10: 集成到 DynamicPage

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`
- Modify: `src/components/common/DataTable.vue`

- [ ] **Step 1: 修改 DynamicPage.vue**

在 `<template>` 中视图切换区后添加视图选择器:

```vue
<!-- 视图切换区 -->
<el-radio-group v-model="viewMode" size="small" class="view-toggle">
  <!-- existing radio buttons -->
</el-radio-group>

<!-- 列视图选择器 -->
<ViewSelector
  v-if="viewMode === 'table'"
  @select="handleViewSelect"
  @manage="handleViewManage"
/>
```

在 `<script setup>` 中添加:

```typescript
import { useColumnViewStore } from '@/stores/columnView'
import ViewSelector from '@/components/column-view/ViewSelector.vue'
import ViewManageDialog from '@/components/column-view/ViewManageDialog.vue'
import ColumnConfigDialog from '@/components/column-view/ColumnConfigDialog.vue'

const columnViewStore = useColumnViewStore()

// 在页面加载时加载列视图
async function loadPage() {
  // ... existing code ...
  // 加载列视图
  await columnViewStore.loadViews(pageId)
}

function handleViewSelect(viewId: number | null) {
  columnViewStore.selectView(pageId, viewId)
}

function handleViewManage() {
  // 打开管理弹窗
}
```

修改 DataTable 的 fields 传参:

```vue
<DataTable
  :fields="columnViewStore.getTableColumns(pageFields)"
  :default-sort="columnViewStore.currentView?.sortConfig?.[0]"
  <!-- ... other props -->
/>
```

- [ ] **Step 2: 修改 DataTable.vue**

在 `defineProps` 中添加:

```typescript
interface DefaultSort {
  field: string
  direction: 'asc' | 'desc'
}

const props = defineProps<{
  // ... existing props
  defaultSort?: DefaultSort
}>()
```

在 `el-table` 上绑定默认排序:

```vue
<el-table
  :default-sort="defaultSort ? { prop: defaultSort.field, order: defaultSort.direction === 'asc' ? 'ascending' : 'descending' } : undefined"
  <!-- ... -->
>
```

- [ ] **Step 3: 安装依赖**

Run: `npm install vuedraggable@next`

- [ ] **Step 4: 提交**

```bash
git add src/views/dynamic/DynamicPage.vue src/components/common/DataTable.vue package.json
git commit -m "feat: integrate column view into DynamicPage and DataTable"
```

---

## Task 11: 端到端测试

- [ ] **Step 1: 启动项目**

Run: `npm run dev:all`
Expected: 前后端启动成功

- [ ] **Step 2: 验证功能**

1. 登录 admin 账号
2. 打开一个数据页（50+ 字段的页面效果最佳）
3. 点击"管理视图"按钮，创建一个新视图
4. 编辑列配置，选择部分字段显示
5. 切换视图，验证表格列正确显示
6. 创建第二个视图，验证下拉切换
7. 设置一个视图为默认，刷新页面验证默认加载
8. 登录 developer 账号，验证可创建私人视图
9. 登录 guest 账号，验证只能看到公共视图和管理按钮不可用

- [ ] **Step 3: 最终提交**

```bash
git add -A
git commit -m "feat: complete column view feature implementation"
```
