# 系统设置与首页定制实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现系统名称全局定制和首页内容完整定制功能，支持管理员配置首页区块的显示、排序、内容编辑。

**Architecture:** 采用双表分离设计，`system_config` 表存储全局配置，`home_widgets` 表存储首页区块配置。前端新增系统设置管理页面，首页改为动态渲染模式。

**Tech Stack:** PostgreSQL JSONB, Flask, Vue 3 + TypeScript, Element Plus, Pinia

---

## File Structure

### 新增文件

**后端：**
- `server/routes/system_config.py` - 系统配置 API
- `server/routes/home_widgets.py` - 首页区块 API
- `server/tests/test_routes_system_config.py` - 系统配置 API 测试
- `server/tests/test_routes_home_widgets.py` - 首页区块 API 测试

**前端：**
- `src/types/systemConfig.ts` - 类型定义
- `src/api/systemConfig.ts` - API 调用
- `src/stores/systemConfig.ts` - 系统配置 store
- `src/components/home/WelcomeWidget.vue` - 欢迎卡片组件
- `src/components/home/StatsWidget.vue` - 统计卡片组件
- `src/components/home/QuickLinksWidget.vue` - 快捷入口组件
- `src/components/home/SystemInfoWidget.vue` - 系统说明组件
- `src/components/home/MarkdownWidget.vue` - Markdown 区块组件
- `src/components/home/DataCardWidget.vue` - 数据卡片组件
- `src/components/home/index.ts` - 组件导出
- `src/views/admin/SystemSettings.vue` - 系统设置管理页面
- `src/views/admin/components/WidgetEditDialog.vue` - 区块编辑对话框

### 修改文件

- `server/init_db.py` - 新增表和默认数据
- `server/app.py` - 注册新蓝图
- `src/types/index.ts` - 导出新类型
- `src/views/home/HomeView.vue` - 改为动态渲染
- `src/components/layout/SideMenu.vue` - 使用系统简称
- `src/views/login/LoginView.vue` - 使用系统名称
- `src/router/index.ts` - 新增路由
- `src/stores/index.ts` - 导出新 store

---

## Task 1: 数据库表初始化

**Files:**
- Modify: `server/init_db.py`

- [ ] **Step 1: 在 init_db.py 中添加 system_config 表定义**

在 `init_db.py` 的表创建区域（约 line 135，ai_settings 表之后）添加：

```python
# ==================== system_config 表 ====================
CREATE TABLE IF NOT EXISTS system_config (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    system_name     VARCHAR(200) NOT NULL DEFAULT '巡检用例管理系统',
    system_short_name VARCHAR(50) NOT NULL DEFAULT '巡检管理',
    logo_url        VARCHAR(500),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_by      VARCHAR(100)
);

INSERT INTO system_config (id) VALUES (1) ON CONFLICT DO NOTHING;
```

- [ ] **Step 2: 在 init_db.py 中添加 home_widgets 表定义**

在 system_config 表之后添加：

```python
# ==================== home_widgets 表 ====================
CREATE TABLE IF NOT EXISTS home_widgets (
    id              VARCHAR(100) PRIMARY KEY,
    widget_type     VARCHAR(50) NOT NULL,
    title           VARCHAR(200),
    content         JSONB,
    enabled         BOOLEAN DEFAULT TRUE,
    order           INTEGER DEFAULT 0,
    visible_roles   JSONB DEFAULT '["admin","developer","guest"]',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

- [ ] **Step 3: 在 init_db.py 中添加默认 widgets 数据**

在 home_widgets 表创建之后添加：

```python
# 默认首页区块数据
cursor.execute("""
INSERT INTO home_widgets (id, widget_type, title, content, enabled, order) VALUES
('welcome', 'welcome', '欢迎',
 '{"heading": "欢迎使用巡检用例管理系统", "description": "本系统支持动态配置菜单和页面，实现灵活的数据管理。"}',
 true, 1),
('stats', 'stats', '系统概览',
 '{"items": [{"type": "menuCount", "label": "菜单数量", "icon": "Document"}, {"type": "pageCount", "label": "页面配置", "icon": "Files"}, {"type": "fieldCount", "label": "字段配置", "icon": "Setting"}]}',
 true, 2),
('quick-links', 'quick-links', '快捷入口',
 '{"links": [{"name": "菜单管理", "path": "/admin/menu", "icon": "Menu"}, {"name": "页面配置", "path": "/admin/page-config", "icon": "Files"}, {"name": "批量导出", "path": "", "icon": "Download", "action": "batchExport"}]}',
 true, 3),
('system-info', 'system-info', '系统说明',
 '{"markdown": "**技术栈：** Vue 3 + TypeScript + Element Plus + Pinia\\n\\n**主要功能：**\\n- 支持 1-3 级嵌套菜单配置\\n- 页面字段可视化配置\\n- 多种表单控件类型支持\\n- 动态数据页面渲染"}',
 true, 4)
ON CONFLICT (id) DO NOTHING
""")
```

- [ ] **Step 4: 在 Migration 区域添加表检查逻辑**

在 init_db.py 的 migration 区域（约 line 740）添加：

```python
# Migration: create system_config and home_widgets tables if missing
cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'system_config'
""")
if not cur.fetchone():
    cur.execute("""
        CREATE TABLE system_config (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            system_name VARCHAR(200) NOT NULL DEFAULT '巡检用例管理系统',
            system_short_name VARCHAR(50) NOT NULL DEFAULT '巡检管理',
            logo_url VARCHAR(500),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            updated_by VARCHAR(100)
        );
        INSERT INTO system_config (id) VALUES (1);
    """)
    print("Created system_config table.")

cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'home_widgets'
""")
if not cur.fetchone():
    cur.execute("""
        CREATE TABLE home_widgets (
            id VARCHAR(100) PRIMARY KEY,
            widget_type VARCHAR(50) NOT NULL,
            title VARCHAR(200),
            content JSONB,
            enabled BOOLEAN DEFAULT TRUE,
            order INTEGER DEFAULT 0,
            visible_roles JSONB DEFAULT '["admin","developer","guest"]',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    # 插入默认数据
    cur.execute("""
    INSERT INTO home_widgets (id, widget_type, title, content, enabled, order) VALUES
    ('welcome', 'welcome', '欢迎', '{"heading": "欢迎使用巡检用例管理系统", "description": "本系统支持动态配置菜单和页面，实现灵活的数据管理。"}', true, 1),
    ('stats', 'stats', '系统概览', '{"items": [{"type": "menuCount", "label": "菜单数量", "icon": "Document"}, {"type": "pageCount", "label": "页面配置", "icon": "Files"}, {"type": "fieldCount", "label": "字段配置", "icon": "Setting"}]}', true, 2),
    ('quick-links', 'quick-links', '快捷入口', '{"links": [{"name": "菜单管理", "path": "/admin/menu", "icon": "Menu"}, {"name": "页面配置", "path": "/admin/page-config", "icon": "Files"}, {"name": "批量导出", "path": "", "icon": "Download", "action": "batchExport"}]}', true, 3),
    ('system-info', 'system-info', '系统说明', '{"markdown": "**技术栈：** Vue 3 + TypeScript + Element Plus + Pinia\\n\\n**主要功能：**\\n- 支持 1-3 级嵌套菜单配置\\n- 页面字段可视化配置\\n- 多种表单控件类型支持\\n- 动态数据页面渲染"}', true, 4)
    """)
    print("Created home_widgets table with default data.")
```

- [ ] **Step 5: 运行数据库初始化验证**

Run: `cd server && python init_db.py`

Expected: 输出包含 "Created system_config table" 和 "Created home_widgets table"（如果是首次运行），或无错误退出（表已存在）

- [ ] **Step 6: Commit**

```bash
git add server/init_db.py
git commit -m "feat(db): add system_config and home_widgets tables for system customization"
```

---

## Task 2: 系统配置 API

**Files:**
- Create: `server/routes/system_config.py`
- Modify: `server/app.py`

- [ ] **Step 1: 创建 system_config.py API 路由文件**

```python
"""系统配置 API 路由

GET  /system-config — 获取系统配置（所有角色可读）
PUT  /system-config — 更新系统配置（仅管理员）
"""

from flask import Blueprint, request, jsonify
import psycopg2.extras
from db import get_db
from auth import login_required, admin_required

system_config_bp = Blueprint('system_config', __name__, url_prefix='/system-config')


@system_config_bp.route('', methods=['GET'])
@login_required
def get_system_config():
    """获取系统配置"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('SELECT system_name, system_short_name, logo_url FROM system_config WHERE id = 1')
        row = cur.fetchone()

    if not row:
        return jsonify({'error': '系统配置不存在'}), 404

    return jsonify({
        'systemName': row['system_name'],
        'systemShortName': row['system_short_name'],
        'logoUrl': row['logo_url']
    })


@system_config_bp.route('', methods=['PUT'])
@admin_required
def update_system_config():
    """更新系统配置（仅管理员）"""
    body = request.get_json(force=True)

    system_name = body.get('systemName', '').strip()
    system_short_name = body.get('systemShortName', '').strip()
    logo_url = body.get('logoUrl')

    if not system_name:
        return jsonify({'error': '系统名称不能为空'}), 400
    if not system_short_name:
        return jsonify({'error': '系统简称不能为空'}), 400

    # 获取当前用户名作为更新人
    from flask import g
    updated_by = g.user.get('username', '')

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE system_config
            SET system_name = %s, system_short_name = %s, logo_url = %s, updated_at = NOW(), updated_by = %s
            WHERE id = 1
        """, (system_name, system_short_name, logo_url, updated_by))
        conn.commit()

    return jsonify({
        'systemName': system_name,
        'systemShortName': system_short_name,
        'logoUrl': logo_url
    })
```

- [ ] **Step 2: 在 app.py 中注册蓝图**

在 `server/app.py` 的蓝图注册区域（约 line 30）添加：

```python
from routes.system_config import system_config_bp
app.register_blueprint(system_config_bp)
```

- [ ] **Step 3: 验证 API 可访问**

Run: `cd server && python -c "from app import app; print('Blueprints:', [b.name for b in app.blueprints.values()])"`

Expected: 输出包含 'system_config'

- [ ] **Step 4: Commit**

```bash
git add server/routes/system_config.py server/app.py
git commit -m "feat(api): add system_config API for global system settings"
```

---

## Task 3: 首页区块 API

**Files:**
- Create: `server/routes/home_widgets.py`

- [ ] **Step 1: 创建 home_widgets.py API 路由文件**

```python
"""首页区块 API 路由

GET    /home-widgets — 获取首页区块列表（所有角色可读）
PUT    /home-widgets — 批量更新区块配置（仅管理员）
POST   /home-widgets — 新增自定义区块（仅管理员）
DELETE /home-widgets/:id — 删除自定义区块（仅管理员）
PUT    /home-widgets/order — 更新区块排序（仅管理员）
"""

from flask import Blueprint, request, jsonify
import psycopg2.extras
import json
from db import get_db
from auth import login_required, admin_required

home_widgets_bp = Blueprint('home_widgets', __name__, url_prefix='/home-widgets')


def _row_to_json(row: dict) -> dict:
    """将数据库行转换为前端格式"""
    return {
        'id': row['id'],
        'widgetType': row['widget_type'],
        'title': row['title'],
        'content': row['content'] or {},
        'enabled': row['enabled'],
        'order': row['order'],
        'visibleRoles': row['visible_roles'] or ['admin', 'developer', 'guest'],
        'createdAt': row['created_at'].isoformat() if row['created_at'] else None,
        'updatedAt': row['updated_at'].isoformat() if row['updated_at'] else None
    }


@home_widgets_bp.route('', methods=['GET'])
@login_required
def get_home_widgets():
    """获取首页区块列表（按 order 排序）"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('SELECT * FROM home_widgets ORDER BY order')
        rows = cur.fetchall()

    return jsonify([_row_to_json(row) for row in rows])


@home_widgets_bp.route('', methods=['PUT'])
@admin_required
def batch_update_home_widgets():
    """批量更新区块配置（仅管理员）"""
    body = request.get_json(force=True)
    widgets = body.get('widgets', [])

    if not isinstance(widgets, list):
        return jsonify({'error': 'widgets 必须是数组'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        for w in widgets:
            widget_id = w.get('id')
            if not widget_id:
                continue

            # 构建更新字段
            updates = []
            params = []

            if 'title' in w:
                updates.append('title = %s')
                params.append(w['title'])
            if 'content' in w:
                updates.append('content = %s')
                params.append(json.dumps(w['content']))
            if 'enabled' in w:
                updates.append('enabled = %s')
                params.append(w['enabled'])
            if 'visibleRoles' in w:
                updates.append('visible_roles = %s')
                params.append(json.dumps(w['visibleRoles']))

            if updates:
                updates.append('updated_at = NOW()')
                params.append(widget_id)
                cur.execute(f"UPDATE home_widgets SET {', '.join(updates)} WHERE id = %s", params)

        conn.commit()

    # 返回更新后的列表
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM home_widgets ORDER BY order')
    rows = cur.fetchall()
    return jsonify([_row_to_json(row) for row in rows])


@home_widgets_bp.route('', methods=['POST'])
@admin_required
def create_home_widget():
    """新增自定义区块（仅管理员）"""
    body = request.get_json(force=True)

    widget_type = body.get('widgetType', '').strip()
    title = body.get('title', '').strip()
    content = body.get('content', {})
    visible_roles = body.get('visibleRoles', ['admin', 'developer', 'guest'])

    # 自定义区块只允许 custom-markdown 和 data-card 类型
    if widget_type not in ['custom-markdown', 'data-card']:
        return jsonify({'error': '自定义区块类型只能是 custom-markdown 或 data-card'}), 400

    # 生成 ID: custom-{type}-{timestamp}
    import time
    widget_id = f'custom-{widget_type}-{int(time.time() * 1000)}'

    # 获取当前最大 order
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COALESCE(MAX(order), 0) FROM home_widgets')
        max_order = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO home_widgets (id, widget_type, title, content, enabled, order, visible_roles)
            VALUES (%s, %s, %s, %s, true, %s, %s)
            RETURNING id, widget_type, title, content, enabled, order, visible_roles, created_at, updated_at
        """, (widget_id, widget_type, title, json.dumps(content), max_order + 1, json.dumps(visible_roles)))
        row = cur.fetchone()
        conn.commit()

    return jsonify(_row_to_json({
        'id': row[0], 'widget_type': row[1], 'title': row[2],
        'content': row[3], 'enabled': row[4], 'order': row[5],
        'visible_roles': row[6], 'created_at': row[7], 'updated_at': row[8]
    }))


@home_widgets_bp.route('/<widget_id>', methods=['DELETE'])
@admin_required
def delete_home_widget(widget_id: str):
    """删除自定义区块（仅管理员）"""
    # 只允许删除自定义区块（ID 以 custom- 开头）
    if not widget_id.startswith('custom-'):
        return jsonify({'error': '只能删除自定义区块'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM home_widgets WHERE id = %s', (widget_id,))
        if cur.rowcount == 0:
            return jsonify({'error': '区块不存在'}), 404
        conn.commit()

    return jsonify({'success': True})


@home_widgets_bp.route('/order', methods=['PUT'])
@admin_required
def update_home_widgets_order():
    """更新区块排序（仅管理员）"""
    body = request.get_json(force=True)
    orders = body.get('orders', [])

    if not isinstance(orders, list):
        return jsonify({'error': 'orders 必须是数组'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        for item in orders:
            widget_id = item.get('id')
            new_order = item.get('order')
            if widget_id and isinstance(new_order, int):
                cur.execute('UPDATE home_widgets SET order = %s, updated_at = NOW() WHERE id = %s',
                           (new_order, widget_id))
        conn.commit()

    return jsonify({'success': True})
```

- [ ] **Step 2: 在 app.py 中注册蓝图**

在 `server/app.py` 的蓝图注册区域添加：

```python
from routes.home_widgets import home_widgets_bp
app.register_blueprint(home_widgets_bp)
```

- [ ] **Step 3: Commit**

```bash
git add server/routes/home_widgets.py server/app.py
git commit -m "feat(api): add home_widgets API for home page customization"
```

---

## Task 4: 后端 API 测试

**Files:**
- Create: `server/tests/test_routes_system_config.py`
- Create: `server/tests/test_routes_home_widgets.py`

- [ ] **Step 1: 创建 system_config API 测试**

```python
"""系统配置 API 测试"""

import pytest
from tests.conftest import client, auth_header, admin_header


def test_get_system_config(client):
    """测试获取系统配置"""
    resp = client.get('/system-config', headers=auth_header())
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'systemName' in data
    assert 'systemShortName' in data


def test_update_system_config_admin(client):
    """测试管理员更新系统配置"""
    resp = client.put('/system-config',
        headers=admin_header(),
        json={'systemName': '新系统名称', 'systemShortName': '新简称'}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['systemName'] == '新系统名称'
    assert data['systemShortName'] == '新简称'


def test_update_system_config_non_admin(client):
    """测试非管理员无法更新系统配置"""
    resp = client.put('/system-config',
        headers=auth_header(),  # developer 角色
        json={'systemName': '测试', 'systemShortName': '测试'}
    )
    assert resp.status_code == 403


def test_update_system_config_empty_name(client):
    """测试空名称返回错误"""
    resp = client.put('/system-config',
        headers=admin_header(),
        json={'systemName': '', 'systemShortName': '简称'}
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: 创建 home_widgets API 测试**

```python
"""首页区块 API 测试"""

import pytest
from tests.conftest import client, auth_header, admin_header


def test_get_home_widgets(client):
    """测试获取首页区块列表"""
    resp = client.get('/home-widgets', headers=auth_header())
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 4  # 默认有4个区块


def test_create_custom_widget(client):
    """测试创建自定义区块"""
    resp = client.post('/home-widgets',
        headers=admin_header(),
        json={'widgetType': 'custom-markdown', 'title': '测试区块', 'content': {'markdown': '# 测试'}}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['widgetType'] == 'custom-markdown'
    assert data['title'] == '测试区块'
    assert data['id'].startswith('custom-')


def test_create_widget_non_admin(client):
    """测试非管理员无法创建区块"""
    resp = client.post('/home-widgets',
        headers=auth_header(),
        json={'widgetType': 'custom-markdown', 'title': '测试'}
    )
    assert resp.status_code == 403


def test_delete_custom_widget(client):
    """测试删除自定义区块"""
    # 先创建
    create_resp = client.post('/home-widgets',
        headers=admin_header(),
        json={'widgetType': 'custom-markdown', 'title': '待删除'}
    )
    widget_id = create_resp.get_json()['id']

    # 再删除
    resp = client.delete(f'/home-widgets/{widget_id}', headers=admin_header())
    assert resp.status_code == 200


def test_delete_builtin_widget_forbidden(client):
    """测试删除内置区块返回错误"""
    resp = client.delete('/home-widgets/welcome', headers=admin_header())
    assert resp.status_code == 400


def test_update_widget_order(client):
    """测试更新排序"""
    resp = client.put('/home-widgets/order',
        headers=admin_header(),
        json={'orders': [{'id': 'welcome', 'order': 10}, {'id': 'stats', 'order': 5}]}
    )
    assert resp.status_code == 200

    # 验证排序生效
    list_resp = client.get('/home-widgets', headers=auth_header())
    widgets = list_resp.get_json()
    welcome = next(w for w in widgets if w['id'] == 'welcome')
    assert welcome['order'] == 10
```

- [ ] **Step 3: 运行测试验证**

Run: `cd server && python -m pytest tests/test_routes_system_config.py tests/test_routes_home_widgets.py -v`

Expected: 所有测试 PASS

- [ ] **Step 4: Commit**

```bash
git add server/tests/test_routes_system_config.py server/tests/test_routes_home_widgets.py
git commit -m "test: add tests for system_config and home_widgets APIs"
```

---

## Task 5: 前端类型定义

**Files:**
- Create: `src/types/systemConfig.ts`
- Modify: `src/types/index.ts`

- [ ] **Step 1: 创建 systemConfig.ts 类型定义**

```typescript
/**
 * 系统配置相关类型定义
 */

/** Widget 类型枚举 */
export type WidgetType =
  | 'welcome'
  | 'stats'
  | 'quick-links'
  | 'system-info'
  | 'custom-markdown'
  | 'data-card'

/** 统计项类型 */
export interface StatsItem {
  type: 'menuCount' | 'pageCount' | 'fieldCount' | 'recordCount'
  label: string
  icon: string
  collection?: string
  filter?: Record<string, any>
}

/** 快捷链接项 */
export interface QuickLinkItem {
  name: string
  path: string
  icon: string
  action?: string
}

/** 数据卡片数据源配置 */
export interface DataSourceConfig {
  collection: string
  branchId?: string
  filter?: Record<string, any>
  limit?: number
}

/** 数据卡片内容配置 */
export interface DataCardContent {
  dataSource: DataSourceConfig
  displayType: 'count' | 'list' | 'table'
  columns?: string[]
  titleField?: string
  linkToDetail?: boolean
}

/** Widget 内容类型映射 */
export interface WidgetContentMap {
  welcome: { heading: string; description: string }
  stats: { items: StatsItem[] }
  quick-links: { links: QuickLinkItem[] }
  system-info: { markdown: string }
  custom-markdown: { markdown: string }
  data-card: DataCardContent
}

/** Widget 配置 */
export interface WidgetConfig {
  id: string
  widgetType: WidgetType
  title?: string
  content: WidgetContentMap[WidgetType]
  enabled: boolean
  order: number
  visibleRoles: string[]
  createdAt?: string
  updatedAt?: string
}

/** 系统配置 */
export interface SystemConfig {
  systemName: string
  systemShortName: string
  logoUrl?: string | null
}

/** 系统配置更新参数 */
export interface SystemConfigUpdate {
  systemName: string
  systemShortName: string
  logoUrl?: string | null
}

/** 排序更新参数 */
export interface OrderUpdateItem {
  id: string
  order: number
}
```

- [ ] **Step 2: 在 types/index.ts 中导出类型**

在 `src/types/index.ts` 末尾添加：

```typescript
// 系统配置相关类型
export * from './systemConfig'
```

- [ ] **Step 3: 验证类型编译**

Run: `cd E:/Code/check-manage && npx vue-tsc --noEmit 2>&1 | head -20`

Expected: 无类型错误（可能有其他文件的错误，但 systemConfig.ts 应无错误）

- [ ] **Step 4: Commit**

```bash
git add src/types/systemConfig.ts src/types/index.ts
git commit -m "feat(types): add SystemConfig and WidgetConfig type definitions"
```

---

## Task 6: 前端 API 客户端

**Files:**
- Create: `src/api/systemConfig.ts`

- [ ] **Step 1: 创建 API 客户端**

```typescript
/**
 * 系统配置 API 接口
 */
import { get, put, post, del } from '@/utils/request'
import type {
  SystemConfig,
  SystemConfigUpdate,
  WidgetConfig,
  OrderUpdateItem
} from '@/types'

/** 获取系统配置 */
export function getSystemConfig() {
  return get<SystemConfig>('/system-config')
}

/** 更新系统配置 */
export function updateSystemConfig(data: SystemConfigUpdate) {
  return put<SystemConfig>('/system-config', data)
}

/** 获取首页区块列表 */
export function getHomeWidgets() {
  return get<WidgetConfig[]>('/home-widgets')
}

/** 批量更新区块配置 */
export function batchUpdateHomeWidgets(widgets: Partial<WidgetConfig>[]) {
  return put<WidgetConfig[]>('/home-widgets', { widgets })
}

/** 创建自定义区块 */
export function createHomeWidget(data: {
  widgetType: 'custom-markdown' | 'data-card'
  title?: string
  content: Record<string, any>
  visibleRoles?: string[]
}) {
  return post<WidgetConfig>('/home-widgets', data)
}

/** 删除区块 */
export function deleteHomeWidget(id: string) {
  return del<{ success: boolean }>(`/home-widgets/${id}`)
}

/** 更新区块排序 */
export function updateWidgetsOrder(orders: OrderUpdateItem[]) {
  return put<{ success: boolean }>('/home-widgets/order', { orders })
}
```

- [ ] **Step 2: Commit**

```bash
git add src/api/systemConfig.ts
git commit -m "feat(api): add systemConfig API client functions"
```

---

## Task 7: 系统配置 Store

**Files:**
- Create: `src/stores/systemConfig.ts`
- Modify: `src/stores/index.ts`

- [ ] **Step 1: 创建 systemConfig store**

```typescript
/**
 * 系统配置状态管理 Store
 *
 * 管理：
 * - 系统名称配置
 * - 首页区块配置
 */

import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useAuthStore } from './auth'
import {
  getSystemConfig,
  updateSystemConfig,
  getHomeWidgets,
  batchUpdateHomeWidgets,
  createHomeWidget,
  deleteHomeWidget,
  updateWidgetsOrder
} from '@/api/systemConfig'
import type { SystemConfig, WidgetConfig } from '@/types'

export const useSystemConfigStore = defineStore('systemConfig', () => {
  // ==================== State ====================

  const systemConfig = ref<SystemConfig>({
    systemName: '巡检用例管理系统',
    systemShortName: '巡检管理',
    logoUrl: null
  })

  const widgets = ref<WidgetConfig[]>([])
  const loading = ref(false)
  const initialized = ref(false)

  // ==================== Getters ====================

  /** 根据当前用户角色过滤可见区块 */
  const visibleWidgets = computed(() => {
    const authStore = useAuthStore()
    const userRole = authStore.userRole
    if (!userRole) return []

    return widgets.value
      .filter(w => w.enabled && w.visibleRoles.includes(userRole))
      .sort((a, b) => a.order - b.order)
  })

  /** 系统名称（用于标题） */
  const systemName = computed(() => systemConfig.value.systemName)

  /** 系统简称（用于 Logo） */
  const systemShortName = computed(() => systemConfig.value.systemShortName)

  // ==================== Actions ====================

  /** 加载系统配置 */
  async function fetchSystemConfig(): Promise<void> {
    try {
      const config = await getSystemConfig()
      systemConfig.value = config
    } catch (error) {
      console.error('加载系统配置失败:', error)
    }
  }

  /** 加载首页区块 */
  async function fetchWidgets(): Promise<void> {
    try {
      const list = await getHomeWidgets()
      widgets.value = list
    } catch (error) {
      console.error('加载首页区块失败:', error)
    }
  }

  /** 初始化（加载所有配置） */
  async function initialize(): Promise<void> {
    if (initialized.value) return

    loading.value = true
    try {
      await Promise.all([fetchSystemConfig(), fetchWidgets()])
      initialized.value = true
    } finally {
      loading.value = false
    }
  }

  /** 更新系统配置 */
  async function updateConfig(data: { systemName: string; systemShortName: string; logoUrl?: string | null }): Promise<void> {
    const config = await updateSystemConfig(data)
    systemConfig.value = config
  }

  /** 批量更新区块 */
  async function updateWidgets(changes: Partial<WidgetConfig>[]): Promise<void> {
    const list = await batchUpdateHomeWidgets(changes)
    widgets.value = list
  }

  /** 创建自定义区块 */
  async function createWidget(data: {
    widgetType: 'custom-markdown' | 'data-card'
    title?: string
    content: Record<string, any>
    visibleRoles?: string[]
  }): Promise<WidgetConfig> {
    const widget = await createHomeWidget(data)
    widgets.value.push(widget)
    return widget
  }

  /** 删除区块 */
  async function removeWidget(id: string): Promise<void> {
    await deleteHomeWidget(id)
    widgets.value = widgets.value.filter(w => w.id !== id)
  }

  /** 更新排序 */
  async function reorderWidgets(orders: { id: string; order: number }[]): Promise<void> {
    await updateWidgetsOrder(orders)
    // 本地更新 order
    for (const item of orders) {
      const widget = widgets.value.find(w => w.id === item.id)
      if (widget) widget.order = item.order
    }
  }

  // ==================== 文档标题同步 ====================

  // 监听系统名称变化，更新文档标题
  watch(systemName, (name) => {
    document.title = name
  }, { immediate: true })

  return {
    // State
    systemConfig,
    widgets,
    loading,
    initialized,
    // Getters
    visibleWidgets,
    systemName,
    systemShortName,
    // Actions
    initialize,
    fetchSystemConfig,
    fetchWidgets,
    updateConfig,
    updateWidgets,
    createWidget,
    removeWidget,
    reorderWidgets
  }
})
```

- [ ] **Step 2: 在 stores/index.ts 中导出 store**

在 `src/stores/index.ts` 中添加导出：

```typescript
export { useSystemConfigStore } from './systemConfig'
```

- [ ] **Step 3: Commit**

```bash
git add src/stores/systemConfig.ts src/stores/index.ts
git commit -m "feat(store): add systemConfig store for global settings management"
```

---

## Task 8: 首页区块组件（基础四组件）

**Files:**
- Create: `src/components/home/WelcomeWidget.vue`
- Create: `src/components/home/StatsWidget.vue`
- Create: `src/components/home/QuickLinksWidget.vue`
- Create: `src/components/home/SystemInfoWidget.vue`
- Create: `src/components/home/index.ts`

- [ ] **Step 1: 创建 WelcomeWidget.vue**

```vue
<template>
  <el-card class="welcome-card">
    <div class="welcome-content">
      <el-icon class="welcome-icon"><Monitor /></el-icon>
      <div class="welcome-text">
        <h1>{{ content.heading }}</h1>
        <p>{{ content.description }}</p>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { Monitor } from '@element-plus/icons-vue'
import type { WidgetContentMap } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['welcome']
}>()
</script>

<style scoped lang="scss">
.welcome-card {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);

  .welcome-content {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 20px;
  }

  .welcome-icon {
    font-size: 64px;
    color: rgba(255, 255, 255, 0.9);
  }

  .welcome-text {
    h1 {
      margin: 0 0 8px 0;
      font-size: 28px;
      color: #fff;
    }

    p {
      margin: 0;
      font-size: 16px;
      color: rgba(255, 255, 255, 0.85);
    }
  }
}
</style>
```

- [ ] **Step 2: 创建 StatsWidget.vue**

```vue
<template>
  <el-row :gutter="20">
    <el-col v-for="item in content.items" :key="item.type + item.label" :span="8">
      <el-card class="stat-card">
        <div class="stat-content">
          <el-icon class="stat-icon" :class="getIconClass(item.type)">
            <component :is="getIconComponent(item.icon)" />
          </el-icon>
          <div class="stat-info">
            <div class="stat-value">{{ getStatValue(item) }}</div>
            <div class="stat-label">{{ item.label }}</div>
          </div>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Document, Files, Setting, Clock } from '@element-plus/icons-vue'
import { useMenuStore, usePageConfigStore } from '@/stores'
import type { WidgetContentMap, StatsItem } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['stats']
}>()

const menuStore = useMenuStore()
const pageConfigStore = usePageConfigStore()

const iconMap: Record<string, any> = {
  Document,
  Files,
  Setting,
  Clock
}

function getIconComponent(icon: string) {
  return iconMap[icon] || Document
}

function getIconClass(type: string) {
  const classes: Record<string, string> = {
    menuCount: 'stat-icon-primary',
    pageCount: 'stat-icon-success',
    fieldCount: 'stat-icon-warning',
    recordCount: 'stat-icon-info'
  }
  return classes[type] || 'stat-icon-primary'
}

function getStatValue(item: StatsItem): number {
  switch (item.type) {
    case 'menuCount':
      return menuStore.menuList.length
    case 'pageCount':
      return pageConfigStore.pageConfigs.length
    case 'fieldCount':
      return pageConfigStore.pageConfigs.reduce((total, config) => total + config.fields.length, 0)
    case 'recordCount':
      // TODO: 需要调用 API 统计
      return 0
    default:
      return 0
  }
}
</script>

<style scoped lang="scss">
.stat-card {
  .stat-content {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 0;
  }

  .stat-icon {
    font-size: 48px;
    padding: 12px;
    border-radius: 12px;

    &.stat-icon-primary {
      color: #409eff;
      background-color: #ecf5ff;
    }

    &.stat-icon-success {
      color: #67c23a;
      background-color: #f0f9eb;
    }

    &.stat-icon-warning {
      color: #e6a23c;
      background-color: #fdf6ec;
    }

    &.stat-icon-info {
      color: #909399;
      background-color: #f4f4f5;
    }
  }

  .stat-info {
    .stat-value {
      font-size: 32px;
      font-weight: 600;
      color: #303133;
    }

    .stat-label {
      font-size: 14px;
      color: #909399;
      margin-top: 4px;
    }
  }
}
</style>
```

- [ ] **Step 3: 创建 QuickLinksWidget.vue**

```vue
<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>{{ title || '快捷入口' }}</span>
      </div>
    </template>
    <div class="quick-links">
      <template v-for="link in content.links" :key="link.name">
        <router-link v-if="link.path" :to="link.path" class="quick-link">
          <el-icon><component :is="getIconComponent(link.icon)" /></el-icon>
          <span>{{ link.name }}</span>
        </router-link>
        <div v-else class="quick-link" @click="handleAction(link.action)">
          <el-icon><component :is="getIconComponent(link.icon)" /></el-icon>
          <span>{{ link.name }}</span>
        </div>
      </template>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Menu, Files, Download, Setting } from '@element-plus/icons-vue'
import { BatchExportDialog } from '@/components/common'
import type { WidgetContentMap, QuickLinkItem } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['quick-links']
  title?: string
}>()

const emit = defineEmits<{
  'batch-export': []
}>()

const batchExportVisible = ref(false)

const iconMap: Record<string, any> = {
  Menu,
  Files,
  Download,
  Setting
}

function getIconComponent(icon: string) {
  return iconMap[icon] || Setting
}

function handleAction(action?: string) {
  if (action === 'batchExport') {
    batchExportVisible.value = true
  }
}
</script>

<style scoped lang="scss">
.card-header {
  font-weight: 600;
}

.quick-links {
  display: flex;
  gap: 16px;

  .quick-link {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 20px 32px;
    background-color: #f5f7fa;
    border-radius: 8px;
    color: #606266;
    text-decoration: none;
    transition: all 0.3s ease;
    cursor: pointer;

    &:hover {
      background-color: #ecf5ff;
      color: #409eff;
      transform: translateY(-2px);
    }

    .el-icon {
      font-size: 32px;
    }

    span {
      font-size: 14px;
    }
  }
}
</style>
```

- [ ] **Step 4: 创建 SystemInfoWidget.vue**

```vue
<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>{{ title || '系统说明' }}</span>
      </div>
    </template>
    <div class="system-info" v-html="renderedMarkdown"></div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { WidgetContentMap } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['system-info']
  title?: string
}>()

// 简单的 Markdown 渲染（仅支持基础格式）
const renderedMarkdown = computed(() => {
  let md = props.content.markdown || ''

  // **bold** → <strong>
  md = md.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')

  // 段落分隔
  md = md.replace(/\n\n/g, '</p><p>')

  // 单行换行
  md = md.replace(/\n/g, '<br>')

  // - list item
  md = md.replace(/^- (.+)$/gm, '<li>$1</li>')
  md = md.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')

  return `<p>${md}</p>`
})
</script>

<style scoped lang="scss">
.card-header {
  font-weight: 600;
}

.system-info {
  font-size: 14px;
  color: #606266;
  line-height: 1.8;

  p {
    margin: 8px 0;
  }

  ul {
    margin: 8px 0;
    padding-left: 20px;

    li {
      margin: 4px 0;
    }
  }
}
</style>
```

- [ ] **Step 5: 创建 components/home/index.ts 导出**

```typescript
/**
 * 首页区块组件统一导出
 */

export { default as WelcomeWidget } from './WelcomeWidget.vue'
export { default as StatsWidget } from './StatsWidget.vue'
export { default as QuickLinksWidget } from './QuickLinksWidget.vue'
export { default as SystemInfoWidget } from './SystemInfoWidget.vue'
export { default as MarkdownWidget } from './MarkdownWidget.vue'
export { default as DataCardWidget } from './DataCardWidget.vue'

// 区块类型到组件的映射
import WelcomeWidget from './WelcomeWidget.vue'
import StatsWidget from './StatsWidget.vue'
import QuickLinksWidget from './QuickLinksWidget.vue'
import SystemInfoWidget from './SystemInfoWidget.vue'
import MarkdownWidget from './MarkdownWidget.vue'
import DataCardWidget from './DataCardWidget.vue'

export const widgetComponentMap: Record<string, any> = {
  welcome: WelcomeWidget,
  stats: StatsWidget,
  'quick-links': QuickLinksWidget,
  'system-info': SystemInfoWidget,
  'custom-markdown': MarkdownWidget,
  'data-card': DataCardWidget
}
```

- [ ] **Step 6: Commit**

```bash
git add src/components/home/WelcomeWidget.vue src/components/home/StatsWidget.vue src/components/home/QuickLinksWidget.vue src/components/home/SystemInfoWidget.vue src/components/home/index.ts
git commit -m "feat(components): add basic home widget components (welcome, stats, quick-links, system-info)"
```

---

## Task 9: 首页区块组件（高级两组件）

**Files:**
- Create: `src/components/home/MarkdownWidget.vue`
- Create: `src/components/home/DataCardWidget.vue`

- [ ] **Step 1: 创建 MarkdownWidget.vue**

```vue
<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>{{ title || '内容区块' }}</span>
      </div>
    </template>
    <div class="markdown-content" v-html="renderedMarkdown"></div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { WidgetContentMap } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['custom-markdown']
  title?: string
}>()

// 基础 Markdown 渲染
const renderedMarkdown = computed(() => {
  let md = props.content.markdown || ''

  // Headers: # ## ### → h1 h2 h3
  md = md.replace(/^### (.+)$/gm, '<h3>$1</h3>')
  md = md.replace(/^## (.+)$/gm, '<h2>$1</h2>')
  md = md.replace(/^# (.+)$/gm, '<h1>$1</h1>')

  // **bold**
  md = md.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')

  // *italic*
  md = md.replace(/\*(.+?)\*/g, '<em>$1</em>')

  // `code`
  md = md.replace(/`(.+?)`/g, '<code>$1</code>')

  // Links: [text](url)
  md = md.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank">$1</a>')

  // 段落
  md = md.replace(/\n\n/g, '</p><p>')

  // 段行换行
  md = md.replace(/\n/g, '<br>')

  // Lists
  md = md.replace(/^- (.+)$/gm, '<li>$1</li>')
  md = md.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')

  return `<div class="markdown-body"><p>${md}</p></div>`
})
</script>

<style scoped lang="scss">
.card-header {
  font-weight: 600;
}

.markdown-content {
  font-size: 14px;
  color: #606266;
  line-height: 1.8;

  :deep(h1) {
    font-size: 24px;
    margin: 16px 0 8px;
  }

  :deep(h2) {
    font-size: 20px;
    margin: 14px 0 6px;
  }

  :deep(h3) {
    font-size: 16px;
    margin: 12px 0 4px;
  }

  :deep(ul) {
    margin: 8px 0;
    padding-left: 24px;
  }

  :deep(li) {
    margin: 4px 0;
  }

  :deep(code) {
    background: #f5f7fa;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: monospace;
  }

  :deep(a) {
    color: #409eff;
    text-decoration: none;
    &:hover {
      text-decoration: underline;
    }
  }
}
</style>
```

- [ ] **Step 2: 创建 DataCardWidget.vue**

```vue
<template>
  <el-card v-loading="loading">
    <template #header>
      <div class="card-header">
        <span>{{ title || '数据概览' }}</span>
      </div>
    </template>

    <!-- count 模式 -->
    <div v-if="content.displayType === 'count'" class="count-display">
      <el-icon class="count-icon"><Document /></el-icon>
      <div class="count-info">
        <div class="count-value">{{ count }}</div>
        <div class="count-label">条记录</div>
      </div>
    </div>

    <!-- list 模式 -->
    <div v-else-if="content.displayType === 'list'" class="list-display">
      <div v-for="record in records" :key="record.id" class="record-item" @click="handleClick(record)">
        <div class="record-title">{{ getFieldValue(record, content.titleField) }}</div>
        <div class="record-meta">
          <span v-for="col in content.columns?.slice(1)" :key="col">
            {{ getFieldValue(record, col) }}
          </span>
        </div>
      </div>
      <el-empty v-if="records.length === 0" :image-size="60" description="暂无数据" />
    </div>

    <!-- table 模式 -->
    <el-table v-else-if="content.displayType === 'table'" :data="records" size="small" stripe>
      <el-table-column v-for="col in content.columns" :key="col" :prop="col" :label="getFieldLabel(col)" />
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Document } from '@element-plus/icons-vue'
import { getDynamicData } from '@/api/dynamic'
import { usePageConfigStore } from '@/stores'
import type { WidgetContentMap, DynamicRecord } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['data-card']
  title?: string
}>()

const router = useRouter()
const pageConfigStore = usePageConfigStore()

const records = ref<DynamicRecord[]>([])
const loading = ref(false)

const count = computed(() => records.value.length)

onMounted(async () => {
  await fetchData()
})

async function fetchData() {
  const { collection, branchId, filter, limit } = props.content.dataSource
  loading.value = true

  try {
    // 构建查询参数
    const params: Record<string, any> = { all: true }
    if (branchId) params.branchId = branchId
    if (filter) params.q = JSON.stringify(filter)
    if (limit) params.limit = limit

    const data = await getDynamicData(collection, params)
    records.value = data
  } catch (error) {
    console.error('获取数据失败:', error)
    records.value = []
  } finally {
    loading.value = false
  }
}

function getFieldValue(record: DynamicRecord, field?: string): string {
  if (!field) return ''
  return record[field] || ''
}

function getFieldLabel(field: string): string {
  // 尝试从 pageConfig 获取字段标签
  const pageId = `page-${props.content.dataSource.collection}`
  const config = pageConfigStore.getPageConfigById(pageId)
  if (config) {
    const fieldConfig = config.fields.find(f => f.fieldName === field)
    if (fieldConfig) return fieldConfig.label
  }
  return field
}

function handleClick(record: DynamicRecord) {
  if (!props.content.linkToDetail) return

  const collection = props.content.dataSource.collection
  router.push(`/dynamic/${collection}/${record.id}`)
}
</script>

<style scoped lang="scss">
.card-header {
  font-weight: 600;
}

.count-display {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;

  .count-icon {
    font-size: 48px;
    color: #409eff;
  }

  .count-value {
    font-size: 36px;
    font-weight: 600;
    color: #303133;
  }

  .count-label {
    font-size: 14px;
    color: #909399;
  }
}

.list-display {
  .record-item {
    padding: 12px;
    border-bottom: 1px solid #ebeef5;
    cursor: pointer;
    transition: background 0.2s;

    &:hover {
      background: #f5f7fa;
    }

    &:last-child {
      border-bottom: none;
    }

    .record-title {
      font-weight: 500;
      color: #303133;
    }

    .record-meta {
      font-size: 12px;
      color: #909399;
      margin-top: 4px;

      span {
        margin-right: 8px;
      }
    }
  }
}
</style>
```

- [ ] **Step 3: Commit**

```bash
git add src/components/home/MarkdownWidget.vue src/components/home/DataCardWidget.vue src/components/home/index.ts
git commit -m "feat(components): add advanced home widget components (markdown, data-card)"
```

---

## Task 10: 系统设置管理页面

**Files:**
- Create: `src/views/admin/SystemSettings.vue`
- Create: `src/views/admin/components/WidgetEditDialog.vue`

- [ ] **Step 1: 创建 WidgetEditDialog.vue**

```vue
<template>
  <el-dialog v-model="visible" :title="dialogTitle" width="600px" :close-on-click-modal="false">
    <!-- welcome 类型 -->
    <el-form v-if="widget?.widgetType === 'welcome'" label-width="80px">
      <el-form-item label="标题">
        <el-input v-model="form.content.heading" placeholder="欢迎标题" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="form.content.description" type="textarea" :rows="3" placeholder="欢迎描述" />
      </el-form-item>
    </el-form>

    <!-- stats 类型 -->
    <el-form v-if="widget?.widgetType === 'stats'" label-width="80px">
      <el-form-item label="统计项">
        <div v-for="(item, idx) in form.content.items" :key="idx" class="stats-item-row">
          <el-select v-model="item.type" style="width: 120px">
            <el-option label="菜单数量" value="menuCount" />
            <el-option label="页面配置" value="pageCount" />
            <el-option label="字段总数" value="fieldCount" />
          </el-select>
          <el-input v-model="item.label" placeholder="标签" style="width: 100px" />
          <el-select v-model="item.icon" style="width: 100px">
            <el-option label="Document" value="Document" />
            <el-option label="Files" value="Files" />
            <el-option label="Setting" value="Setting" />
          </el-select>
          <el-button type="danger" link @click="form.content.items.splice(idx, 1)">删除</el-button>
        </div>
        <el-button type="primary" link @click="addStatsItem">+ 添加统计项</el-button>
      </el-form-item>
    </el-form>

    <!-- quick-links 类型 -->
    <el-form v-if="widget?.widgetType === 'quick-links'" label-width="80px">
      <el-form-item label="链接列表">
        <div v-for="(link, idx) in form.content.links" :key="idx" class="link-item-row">
          <el-input v-model="link.name" placeholder="名称" style="width: 100px" />
          <el-input v-model="link.path" placeholder="路径" style="width: 150px" />
          <el-select v-model="link.icon" style="width: 100px">
            <el-option label="Menu" value="Menu" />
            <el-option label="Files" value="Files" />
            <el-option label="Download" value="Download" />
          </el-select>
          <el-button type="danger" link @click="form.content.links.splice(idx, 1)">删除</el-button>
        </div>
        <el-button type="primary" link @click="addLinkItem">+ 添加链接</el-button>
      </el-form-item>
    </el-form>

    <!-- system-info / custom-markdown 类型 -->
    <el-form v-if="widget?.widgetType === 'system-info' || widget?.widgetType === 'custom-markdown'" label-width="80px">
      <el-form-item label="内容">
        <el-input v-model="form.content.markdown" type="textarea" :rows="8" placeholder="Markdown 内容" />
      </el-form-item>
    </el-form>

    <!-- data-card 类型 -->
    <el-form v-if="widget?.widgetType === 'data-card'" label-width="80px">
      <el-form-item label="数据集合">
        <el-input v-model="form.content.dataSource.collection" placeholder="如：task-calendar" />
      </el-form-item>
      <el-form-item label="显示类型">
        <el-radio-group v-model="form.content.displayType">
          <el-radio value="count">统计数</el-radio>
          <el-radio value="list">列表</el-radio>
          <el-radio value="table">表格</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="显示字段">
        <el-input v-model="columnsStr" placeholder="字段名逗号分隔，如：taskName,status" />
      </el-form-item>
      <el-form-item label="标题字段">
        <el-input v-model="form.content.titleField" placeholder="如：taskName" />
      </el-form-item>
      <el-form-item label="点击跳转">
        <el-switch v-model="form.content.linkToDetail" />
      </el-form-item>
    </el-form>

    <!-- 通用配置 -->
    <el-divider content-position="left">显示配置</el-divider>
    <el-form label-width="80px">
      <el-form-item label="区块标题">
        <el-input v-model="form.title" placeholder="可选" />
      </el-form-item>
      <el-form-item label="可见角色">
        <el-checkbox-group v-model="form.visibleRoles">
          <el-checkbox label="admin">管理员</el-checkbox>
          <el-checkbox label="developer">开发者</el-checkbox>
          <el-checkbox label="guest">访客</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="handleSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { WidgetConfig, WidgetContentMap } from '@/types'

const props = defineProps<{
  widget: WidgetConfig | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'save': [widget: Partial<WidgetConfig>]
}>()

const visible = ref(false)

const dialogTitle = computed(() => {
  if (!props.widget) return '编辑区块'
  const typeLabels: Record<string, string> = {
    welcome: '欢迎卡片',
    stats: '统计卡片',
    'quick-links': '快捷入口',
    'system-info': '系统说明',
    'custom-markdown': 'Markdown区块',
    'data-card': '数据卡片'
  }
  return `编辑${typeLabels[props.widget.widgetType] || '区块'}`
})

// 表单数据
const form = ref<{
  title: string
  content: any
  visibleRoles: string[]
}>({
  title: '',
  content: {},
  visibleRoles: ['admin', 'developer', 'guest']
})

// data-card 的 columns 字符串
const columnsStr = computed({
  get: () => (form.value.content.columns || []).join(','),
  set: (val: string) => {
    form.value.content.columns = val.split(',').map(s => s.trim()).filter(Boolean)
  }
})

// 监听 widget 变化，初始化表单
watch(() => props.widget, (w) => {
  if (w) {
    form.value = {
      title: w.title || '',
      content: JSON.parse(JSON.stringify(w.content || {})),
      visibleRoles: [...(w.visibleRoles || ['admin', 'developer', 'guest'])]
    }
    visible.value = true
  }
}, { immediate: true })

function addStatsItem() {
  if (!form.value.content.items) form.value.content.items = []
  form.value.content.items.push({ type: 'menuCount', label: '', icon: 'Document' })
}

function addLinkItem() {
  if (!form.value.content.links) form.value.content.links = []
  form.value.content.links.push({ name: '', path: '', icon: 'Menu' })
}

function handleSave() {
  emit('save', {
    id: props.widget?.id,
    title: form.value.title,
    content: form.value.content,
    visibleRoles: form.value.visibleRoles
  })
  visible.value = false
}

watch(visible, (v) => emit('update:modelValue', v))
</script>

<style scoped lang="scss">
.stats-item-row, .link-item-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
</style>
```

- [ ] **Step 2: 创建 SystemSettings.vue 主页面**

```vue
<template>
  <div class="system-settings">
    <el-tabs v-model="activeTab" type="border-card">
      <!-- 基本设置 Tab -->
      <el-tab-pane label="基本设置" name="basic">
        <el-form label-width="100px" class="settings-form">
          <el-form-item label="系统名称">
            <el-input v-model="systemName" placeholder="用于首页标题、浏览器标题" maxlength="200" />
          </el-form-item>
          <el-form-item label="系统简称">
            <el-input v-model="systemShortName" placeholder="用于侧边栏Logo" maxlength="50" />
          </el-form-item>
          <el-form-item label="Logo图片">
            <el-input v-model="logoUrl" placeholder="可选，Logo URL" clearable />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="handleSaveConfig">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 首页配置 Tab -->
      <el-tab-pane label="首页配置" name="widgets">
        <div class="widgets-toolbar">
          <el-dropdown @command="handleAddWidget">
            <el-button type="primary">
              <el-icon><Plus /></el-icon>
              新增区块
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="custom-markdown">Markdown 区块</el-dropdown-item>
                <el-dropdown-item command="data-card">数据卡片</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <draggable
          v-model="widgetsList"
          item-key="id"
          handle=".drag-handle"
          @end="handleDragEnd"
          class="widgets-list"
        >
          <template #item="{ element }">
            <div class="widget-item">
              <el-icon class="drag-handle"><Rank /></el-icon>
              <el-switch v-model="element.enabled" @change="handleWidgetChange(element)" />
              <span class="widget-title">{{ element.title || getDefaultTitle(element.widgetType) }}</span>
              <el-tag size="small" :type="getTagType(element.widgetType)">{{ element.widgetType }}</el-tag>
              <div class="widget-actions">
                <el-button type="primary" link size="small" @click="handleEditWidget(element)">
                  <el-icon><Edit /></el-icon>
                </el-button>
                <el-button
                  v-if="element.id.startsWith('custom-')"
                  type="danger"
                  link
                  size="small"
                  @click="handleDeleteWidget(element)"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
            </div>
          </template>
        </draggable>

        <WidgetEditDialog
          v-model="editDialogVisible"
          :widget="currentWidget"
          @save="handleSaveWidget"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, ArrowDown, Rank, Edit, Delete } from '@element-plus/icons-vue'
import draggable from 'vuedraggable.umd'
import { useSystemConfigStore } from '@/stores'
import WidgetEditDialog from './components/WidgetEditDialog.vue'
import type { WidgetConfig, WidgetType } from '@/types'

const systemConfigStore = useSystemConfigStore()

const activeTab = ref('basic')
const saving = ref(false)

// 系统配置表单
const systemName = ref('')
const systemShortName = ref('')
const logoUrl = ref('')

// 区块列表（可拖拽）
const widgetsList = ref<WidgetConfig[]>([])
const editDialogVisible = ref(false)
const currentWidget = ref<WidgetConfig | null>(null)

// 加载配置
onMounted(async () => {
  await systemConfigStore.initialize()
  systemName.value = systemConfigStore.systemName
  systemShortName.value = systemConfigStore.systemShortName
  logoUrl.value = systemConfigStore.systemConfig.logoUrl || ''
  widgetsList.value = [...systemConfigStore.widgets].sort((a, b) => a.order - b.order)
})

// 保存系统配置
async function handleSaveConfig() {
  if (!systemName.value.trim()) {
    ElMessage.warning('系统名称不能为空')
    return
  }
  if (!systemShortName.value.trim()) {
    ElMessage.warning('系统简称不能为空')
    return
  }

  saving.value = true
  try {
    await systemConfigStore.updateConfig({
      systemName: systemName.value.trim(),
      systemShortName: systemShortName.value.trim(),
      logoUrl: logoUrl.value.trim() || null
    })
    ElMessage.success('保存成功')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

// 区块默认标题
function getDefaultTitle(type: WidgetType): string {
  const labels: Record<string, string> = {
    welcome: '欢迎卡片',
    stats: '统计概览',
    'quick-links': '快捷入口',
    'system-info': '系统说明',
    'custom-markdown': 'Markdown',
    'data-card': '数据卡片'
  }
  return labels[type] || type
}

// 标签类型
function getTagType(type: WidgetType): string {
  if (type.startsWith('custom-')) return 'warning'
  return ''
}

// 拖拽结束
async function handleDragEnd() {
  const orders = widgetsList.value.map((w, idx) => ({ id: w.id, order: idx + 1 }))
  await systemConfigStore.reorderWidgets(orders)
}

// 区块变更
async function handleWidgetChange(widget: WidgetConfig) {
  await systemConfigStore.updateWidgets([{ id: widget.id, enabled: widget.enabled }])
}

// 编辑区块
function handleEditWidget(widget: WidgetConfig) {
  currentWidget.value = widget
  editDialogVisible.value = true
}

// 保存区块编辑
async function handleSaveWidget(data: Partial<WidgetConfig>) {
  await systemConfigStore.updateWidgets([data])
  // 更新本地列表
  const idx = widgetsList.value.findIndex(w => w.id === data.id)
  if (idx >= 0) {
    widgetsList.value[idx] = { ...widgetsList.value[idx], ...data }
  }
  ElMessage.success('保存成功')
}

// 新增区块
async function handleAddWidget(type: 'custom-markdown' | 'data-card') {
  const widget = await systemConfigStore.createWidget({
    widgetType: type,
    title: getDefaultTitle(type),
    content: type === 'custom-markdown' ? { markdown: '' } : {
      dataSource: { collection: '' },
      displayType: 'count'
    }
  })
  widgetsList.value.push(widget)
  ElMessage.success('创建成功')
}

// 删除区块
async function handleDeleteWidget(widget: WidgetConfig) {
  await ElMessageBox.confirm(`确定删除区块「${widget.title || getDefaultTitle(widget.widgetType)}」？`, '删除确认', { type: 'warning' })
  await systemConfigStore.removeWidget(widget.id)
  widgetsList.value = widgetsList.value.filter(w => w.id !== widget.id)
  ElMessage.success('删除成功')
}
</script>

<style scoped lang="scss">
.system-settings {
  padding: 0;
}

.settings-form {
  max-width: 500px;
}

.widgets-toolbar {
  margin-bottom: 16px;
}

.widgets-list {
  .widget-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    border: 1px solid #e4e7ed;
    border-radius: 4px;
    margin-bottom: 8px;
    background: #fff;

    .drag-handle {
      cursor: grab;
      color: #909399;
    }

    .widget-title {
      flex: 1;
      font-weight: 500;
    }

    .widget-actions {
      display: flex;
      gap: 4px;
    }
  }
}
</style>
```

- [ ] **Step 3: Commit**

```bash
git add src/views/admin/SystemSettings.vue src/views/admin/components/WidgetEditDialog.vue
git commit -m "feat(admin): add SystemSettings page for system config and home widgets management"
```

---

## Task 11: 首页动态渲染

**Files:**
- Modify: `src/views/home/HomeView.vue`

- [ ] **Step 1: 改造 HomeView.vue 为动态渲染**

将整个文件改为：

```vue
<template>
  <div class="home-view">
    <template v-for="widget in visibleWidgets" :key="widget.id">
      <component
        :is="getWidgetComponent(widget.widgetType)"
        :content="widget.content"
        :title="widget.title"
        class="widget-wrapper"
        :class="{ 'mt-lg': widget.order > 1 && widget.widgetType !== 'stats' }"
      />
      <!-- stats 类型单独处理为 row -->
      <el-row v-if="widget.widgetType === 'stats'" :gutter="20" class="mt-lg" />
    </template>

    <BatchExportDialog v-model="batchExportVisible" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useSystemConfigStore, usePageConfigStore } from '@/stores'
import { BatchExportDialog } from '@/components/common'
import { widgetComponentMap } from '@/components/home'
import type { WidgetConfig } from '@/types'

const systemConfigStore = useSystemConfigStore()
const pageConfigStore = usePageConfigStore()

const batchExportVisible = ref(false)

// 监听快捷入口的批量导出事件
watch(batchExportVisible, (v) => {
  if (v) {
    // 由 QuickLinksWidget 触发
  }
})

onMounted(async () => {
  // 初始化系统配置和区块
  await systemConfigStore.initialize()
  // 加载页面配置（用于 stats 统计和 data-card 字段标签）
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }
})

// 可见区块列表
const visibleWidgets = computed(() => systemConfigStore.visibleWidgets)

// 获取区块组件
function getWidgetComponent(type: string) {
  return widgetComponentMap[type]
}
</script>

<style scoped lang="scss">
.home-view {
  padding: 0;
}

.widget-wrapper {
  margin-bottom: 24px;
}

.mt-lg {
  margin-top: 24px;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add src/views/home/HomeView.vue
git commit -m "feat(home): refactor HomeView to dynamic widget rendering"
```

---

## Task 12: 系统名称全局应用

**Files:**
- Modify: `src/components/layout/SideMenu.vue`
- Modify: `src/views/login/LoginView.vue`
- Modify: `src/stores/app.ts`（或在 AppLayout 初始化时调用）

- [ ] **Step 1: SideMenu.vue 使用系统简称**

在 SideMenu.vue 的 logo 区域（约 line 17-20），修改为：

```vue
<!-- Logo 区域 -->
<div class="menu-logo" :class="{ collapsed: sidebarCollapsed }">
  <el-icon class="logo-icon"><Monitor /></el-icon>
  <span v-if="!sidebarCollapsed" class="logo-text">{{ systemShortName }}</span>
</div>
```

在 script 部分添加：

```typescript
import { useSystemConfigStore } from '@/stores'

const systemConfigStore = useSystemConfigStore()
const systemShortName = computed(() => systemConfigStore.systemShortName)
```

- [ ] **Step 2: LoginView.vue 使用系统名称**

在 LoginView.vue 找到标题位置，修改为：

```vue
<h1 class="login-title">{{ systemName }}</h1>
```

在 script 部分添加：

```typescript
import { useSystemConfigStore } from '@/stores'

const systemConfigStore = useSystemConfigStore()
const systemName = computed(() => systemConfigStore.systemName)

// 在登录页预加载配置（不依赖登录态）
onMounted(async () => {
  await systemConfigStore.fetchSystemConfig()
})
```

- [ ] **Step 3: 在 AppLayout 初始化时加载配置**

在 `src/components/layout/AppLayout.vue` 的 onMounted 中添加：

```typescript
import { useSystemConfigStore } from '@/stores'

const systemConfigStore = useSystemConfigStore()

onMounted(async () => {
  // 应用主题
  appStore.applyTheme()

  // 加载系统配置（用于标题）
  await systemConfigStore.initialize()

  // 初始化应用
  await appStore.initializeApp()
  // ... 其他逻辑
})
```

- [ ] **Step 4: Commit**

```bash
git add src/components/layout/SideMenu.vue src/views/login/LoginView.vue src/components/layout/AppLayout.vue
git commit -m "feat: apply system name to sidebar logo, login page, and document title"
```

---

## Task 13: 路由和菜单配置

**Files:**
- Modify: `src/router/index.ts`
- Modify: 数据库 menus 表（通过脚本或 init_db.py）

- [ ] **Step 1: 在 router/index.ts 添加路由**

在管理路由区域添加：

```typescript
import SystemSettings from '@/views/admin/SystemSettings.vue'

// 在 admin 路径下添加
{ path: 'system-settings', component: SystemSettings, meta: { title: '系统设置' } }
```

- [ ] **Step 2: 在 init_db.py 添加管理菜单项**

在 init_db.py 的菜单初始化区域添加：

```python
# 系统设置菜单
cursor.execute("""
INSERT INTO menus (id, name, icon, page_id, parent_id, order, path, roles, menu_type)
VALUES ('menu-system-settings', '系统设置', 'Setting', NULL, 'menu-admin', 90, '/admin/system-settings', '["admin"]', 'link')
ON CONFLICT (id) DO NOTHING
""")
```

- [ ] **Step 3: Commit**

```bash
git add src/router/index.ts server/init_db.py
git commit -m "feat: add SystemSettings route and menu item"
```

---

## Task 14: 前端测试

**Files:**
- Create: `src/stores/__tests__/systemConfig.test.ts`

- [ ] **Step 1: 创建 systemConfig store 测试**

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useSystemConfigStore } from '../systemConfig'

// Mock API
vi.mock('@/api/systemConfig', () => ({
  getSystemConfig: vi.fn().mockResolvedValue({
    systemName: '测试系统',
    systemShortName: '测试',
    logoUrl: null
  }),
  getHomeWidgets: vi.fn().mockResolvedValue([
    { id: 'welcome', widgetType: 'welcome', enabled: true, order: 1, visibleRoles: ['admin', 'developer', 'guest'], content: { heading: '欢迎', description: '' } },
    { id: 'stats', widgetType: 'stats', enabled: true, order: 2, visibleRoles: ['admin', 'developer', 'guest'], content: { items: [] } }
  ]),
  updateSystemConfig: vi.fn().mockResolvedValue({ systemName: '更新', systemShortName: 'UP', logoUrl: null }),
  batchUpdateHomeWidgets: vi.fn().mockResolvedValue([])
}))

// Mock auth store
vi.mock('../auth', () => ({
  useAuthStore: vi.fn().mockReturnValue({ userRole: 'admin' })
}))

describe('systemConfig store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('初始化后加载配置', async () => {
    const store = useSystemConfigStore()
    await store.initialize()

    expect(store.systemName).toBe('测试系统')
    expect(store.systemShortName).toBe('测试')
    expect(store.widgets.length).toBe(2)
  })

  it('visibleWidgets 根据角色过滤', async () => {
    const store = useSystemConfigStore()
    await store.initialize()

    expect(store.visibleWidgets.length).toBe(2)
  })

  it('updateConfig 更新系统名称', async () => {
    const store = useSystemConfigStore()
    await store.updateConfig({ systemName: '更新', systemShortName: 'UP' })

    expect(store.systemName).toBe('更新')
  })
})
```

- [ ] **Step 2: 运行测试验证**

Run: `npx vitest run src/stores/__tests__/systemConfig.test.ts`

Expected: 测试 PASS

- [ ] **Step 3: Commit**

```bash
git add src/stores/__tests__/systemConfig.test.ts
git commit -m "test: add systemConfig store unit tests"
```

---

## Summary

完成后，系统将支持：

1. **系统名称定制**：管理员可设置系统名称和简称，自动应用到侧边栏、首页、浏览器标题、登录页
2. **首页区块定制**：
   - 启用/禁用区块
   - 拖拽排序
   - 编辑区块内容（标题、显示字段、可见角色）
   - 新增自定义 Markdown 或数据卡片区块
   - 删除自定义区块
3. **角色可见性**：不同角色可看到不同的首页区块组合
4. **数据卡片**：支持展示特定数据集的统计或列表