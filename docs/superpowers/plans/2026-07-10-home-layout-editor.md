# 首页自由布局编辑器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让管理员在「系统设置 → 首页配置」用自由拖拽/缩放的 12 列网格编辑首页区块布局（并排、不同宽高），全局生效；移动端自动降级为现有单列纵向堆叠。

**Architecture:** `home_widgets` 表新增 `layout_x/y/w/h` 四列（12 列网格坐标）。后端新增 `PUT /home-widgets/layout` 批量保存坐标，并按 `(y, x)` 重算 `order`（供移动端沿用现有排序渲染）。管理端用 `grid-layout-plus` 做可拖拽/缩放的网格编辑器；访客首页桌面态用纯 CSS Grid 静态渲染坐标（不引入拖拽库），移动态忽略坐标、按 `order` 单列堆叠（现有逻辑不变）。

**Tech Stack:** Vue 3 + TypeScript + Element Plus + Pinia，`grid-layout-plus`（新依赖，仅管理端 chunk 引入）；Flask + psycopg2；Vitest + Pytest + Playwright。

## Global Constraints

- 12 列网格，行高固定 30px/行（spec: 数据模型）。
- 布局是全局的（管理员编辑，所有用户看到同一份），不做每用户个性化（spec: 背景与目标）。
- 移动端（<768px）不做自由布局，永远单列纵向堆叠，复用现有 `order` 排序（spec: 首页渲染）。
- 迁移后现有首页视觉必须与升级前完全一致（spec: 数据模型 · 迁移）。
- `grid-layout-plus` 只在 `SystemSettings.vue` 路由 chunk 引入，不进入访客首页 bundle（spec: 管理端编辑体验）。
- 任何 UI 改动完成后必须用 Playwright 实测验证（CLAUDE.md 强制要求），并同步更新 `docs/user-guide/admin/system-settings.md`。

---

## File Structure

**Backend:**
- Modify `server/init_db.py` — DDL（新装库）+ 幂等迁移（存量库）+ 默认区块种子数据的坐标。
- Modify `server/routes/home_widgets.py` — `_row_to_json` 返回 `layout`；新增 `PUT /home-widgets/layout`；`POST` 默认坐标；删除 `PUT /home-widgets/order`。
- Modify `server/tests/test_routes_home_widgets.py` — 新增布局端点测试类，删除排序端点测试类。

**Frontend:**
- Modify `src/types/systemConfig.ts` — 新增 `WidgetLayout`、`WidgetLayoutUpdateItem`；`WidgetConfig` 新增 `layout` 字段；删除 `OrderUpdateItem`。
- Modify `src/api/systemConfig.ts` — 删除 `updateWidgetsOrder`，新增 `updateWidgetsLayout`。
- Modify `src/stores/systemConfig.ts` — 删除 `reorderWidgets`，新增 `updateLayout`。
- Modify `src/stores/__tests__/systemConfig.test.ts` — mock 与断言同步更新。
- Create `src/utils/homeGridLayout.ts` — 纯函数：坐标→CSS Grid 样式、自定义区块判断。
- Create `src/utils/__tests__/homeGridLayout.test.ts`。
- Modify `src/views/home/HomeView.vue` — 桌面态 CSS Grid 定位 / 移动态回退纵向堆叠。
- Create `src/components/admin/HomeLayoutEditor.vue` — `grid-layout-plus` 拖拽/缩放编辑器（承接原 `SystemSettings.vue` 里区块列表的展示与操作职责）。
- Modify `src/views/admin/SystemSettings.vue` — 移除 `vuedraggable` 列表，接入 `HomeLayoutEditor` + 「保存布局」按钮 + 未保存离开确认。
- Modify `package.json` — 新增 `grid-layout-plus` 依赖。
- Modify `docs/user-guide/admin/system-settings.md` — 首页配置章节文案 + 截图。

---

### Task 1: 数据库 — layout 坐标列 + 迁移 + 默认数据回填

**Files:**
- Modify: `server/init_db.py:187-213`（新装库 DDL + 默认种子数据）
- Modify: `server/init_db.py:1177-1206`（存量库 fallback：home_widgets 表不存在时的建表 + 种子数据）
- Modify: `server/init_db.py`（在第 1206 行 `print("Created home_widgets table...")` 之后插入新的幂等迁移块）

**Interfaces:**
- Produces: `home_widgets` 表新增列 `layout_x INTEGER NOT NULL DEFAULT 0`、`layout_y INTEGER NOT NULL DEFAULT 0`、`layout_w INTEGER NOT NULL DEFAULT 12`、`layout_h INTEGER NOT NULL DEFAULT 4`。Task 2 的路由代码依赖这四列存在。

- [ ] **Step 1: 更新新装库 DDL（`CREATE TABLE home_widgets`）加入四列**

在 `server/init_db.py` 第 187-197 行，把：

```sql
CREATE TABLE IF NOT EXISTS home_widgets (
    id              VARCHAR(100) PRIMARY KEY,
    widget_type     VARCHAR(50) NOT NULL,
    title           VARCHAR(200),
    content         JSONB,
    enabled         BOOLEAN DEFAULT TRUE,
    "order"         INTEGER DEFAULT 0,
    visible_roles   JSONB DEFAULT '["admin","developer","guest"]',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

改为：

```sql
CREATE TABLE IF NOT EXISTS home_widgets (
    id              VARCHAR(100) PRIMARY KEY,
    widget_type     VARCHAR(50) NOT NULL,
    title           VARCHAR(200),
    content         JSONB,
    enabled         BOOLEAN DEFAULT TRUE,
    "order"         INTEGER DEFAULT 0,
    visible_roles   JSONB DEFAULT '["admin","developer","guest"]',
    layout_x        INTEGER NOT NULL DEFAULT 0,
    layout_y        INTEGER NOT NULL DEFAULT 0,
    layout_w        INTEGER NOT NULL DEFAULT 12,
    layout_h        INTEGER NOT NULL DEFAULT 4,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

- [ ] **Step 2: 更新新装库默认种子数据，显式指定每个默认区块的 `layout_y`**

同一段落，第 199-213 行，把：

```sql
-- 默认首页区块数据
INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order") VALUES
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
ON CONFLICT (id) DO NOTHING;
```

改为（新增 `layout_y` 列，值 0/4/8/12，让四个默认区块保持全宽纵向堆叠——x/w/h 不显式列出，自动取表 DEFAULT 的 0/12/4）：

```sql
-- 默认首页区块数据
INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order", layout_y) VALUES
('welcome', 'welcome', '欢迎',
 '{"heading": "欢迎使用巡检用例管理系统", "description": "本系统支持动态配置菜单和页面，实现灵活的数据管理。"}',
 true, 1, 0),
('stats', 'stats', '系统概览',
 '{"items": [{"type": "menuCount", "label": "菜单数量", "icon": "Document"}, {"type": "pageCount", "label": "页面配置", "icon": "Files"}, {"type": "fieldCount", "label": "字段配置", "icon": "Setting"}]}',
 true, 2, 4),
('quick-links', 'quick-links', '快捷入口',
 '{"links": [{"name": "菜单管理", "path": "/admin/menu", "icon": "Menu"}, {"name": "页面配置", "path": "/admin/page-config", "icon": "Files"}, {"name": "批量导出", "path": "", "icon": "Download", "action": "batchExport"}]}',
 true, 3, 8),
('system-info', 'system-info', '系统说明',
 '{"markdown": "**技术栈：** Vue 3 + TypeScript + Element Plus + Pinia\\n\\n**主要功能：**\\n- 支持 1-3 级嵌套菜单配置\\n- 页面字段可视化配置\\n- 多种表单控件类型支持\\n- 动态数据页面渲染"}',
 true, 4, 12)
ON CONFLICT (id) DO NOTHING;
```

- [ ] **Step 3: 同步更新存量库 fallback 建表块（`home_widgets` 表不存在时执行的分支）**

第 1176-1204 行（`if not cur.fetchone():` 分支内），把：

```python
            cur.execute("""
                CREATE TABLE home_widgets (
                    id              VARCHAR(100) PRIMARY KEY,
                    widget_type     VARCHAR(50) NOT NULL,
                    title           VARCHAR(200),
                    content         JSONB,
                    enabled         BOOLEAN DEFAULT TRUE,
                    "order"         INTEGER DEFAULT 0,
                    visible_roles   JSONB DEFAULT '["admin","developer","guest"]',
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            # Insert default widgets
            cur.execute("""
                INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order") VALUES
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
            """)
            conn.commit()
            print("Created home_widgets table with default widgets.")
```

改为：

```python
            cur.execute("""
                CREATE TABLE home_widgets (
                    id              VARCHAR(100) PRIMARY KEY,
                    widget_type     VARCHAR(50) NOT NULL,
                    title           VARCHAR(200),
                    content         JSONB,
                    enabled         BOOLEAN DEFAULT TRUE,
                    "order"         INTEGER DEFAULT 0,
                    visible_roles   JSONB DEFAULT '["admin","developer","guest"]',
                    layout_x        INTEGER NOT NULL DEFAULT 0,
                    layout_y        INTEGER NOT NULL DEFAULT 0,
                    layout_w        INTEGER NOT NULL DEFAULT 12,
                    layout_h        INTEGER NOT NULL DEFAULT 4,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            # Insert default widgets
            cur.execute("""
                INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order", layout_y) VALUES
                ('welcome', 'welcome', '欢迎',
                 '{"heading": "欢迎使用巡检用例管理系统", "description": "本系统支持动态配置菜单和页面，实现灵活的数据管理。"}',
                 true, 1, 0),
                ('stats', 'stats', '系统概览',
                 '{"items": [{"type": "menuCount", "label": "菜单数量", "icon": "Document"}, {"type": "pageCount", "label": "页面配置", "icon": "Files"}, {"type": "fieldCount", "label": "字段配置", "icon": "Setting"}]}',
                 true, 2, 4),
                ('quick-links', 'quick-links', '快捷入口',
                 '{"links": [{"name": "菜单管理", "path": "/admin/menu", "icon": "Menu"}, {"name": "页面配置", "path": "/admin/page-config", "icon": "Files"}, {"name": "批量导出", "path": "", "icon": "Download", "action": "batchExport"}]}',
                 true, 3, 8),
                ('system-info', 'system-info', '系统说明',
                 '{"markdown": "**技术栈：** Vue 3 + TypeScript + Element Plus + Pinia\\n\\n**主要功能：**\\n- 支持 1-3 级嵌套菜单配置\\n- 页面字段可视化配置\\n- 多种表单控件类型支持\\n- 动态数据页面渲染"}',
                 true, 4, 12)
            """)
            conn.commit()
            print("Created home_widgets table with default widgets.")
```

- [ ] **Step 4: 新增存量库幂等迁移块（已存在 `home_widgets` 表但没有 layout 列的情况）**

紧接着上面 `print("Created home_widgets table with default widgets.")` 那一行之后（原第 1206 行之后，即将开始 `# Migration: add backup_scope...` 之前），插入：

```python
        # Migration: add layout_x/y/w/h columns to home_widgets (grid layout editor)
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'home_widgets' AND column_name = 'layout_x'
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE home_widgets
                    ADD COLUMN layout_x INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN layout_y INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN layout_w INTEGER NOT NULL DEFAULT 12,
                    ADD COLUMN layout_h INTEGER NOT NULL DEFAULT 4;
            """)
            # Backfill: preserve today's vertical stacking order as full-width rows
            # (x=0, w=12, h=4 from the column DEFAULTs above; only y needs computing)
            cur.execute('SELECT id FROM home_widgets ORDER BY "order"')
            ids_in_order = [row[0] for row in cur.fetchall()]
            for idx, widget_id in enumerate(ids_in_order):
                cur.execute(
                    'UPDATE home_widgets SET layout_y = %s WHERE id = %s',
                    (idx * 4, widget_id)
                )
            conn.commit()
            print("Added layout_x/y/w/h columns to home_widgets and backfilled positions.")
```

- [ ] **Step 5: 针对本地开发库运行迁移并验证**

```bash
cd server && python init_db.py
```

Expected: 输出包含（存量库场景）`Added layout_x/y/w/h columns to home_widgets and backfilled positions.`，或（全新库场景）`Created home_widgets table with default widgets.`，无报错退出。

用 `psql` 或任意 SQL 客户端确认列已存在且默认区块坐标正确：

```sql
SELECT id, "order", layout_x, layout_y, layout_w, layout_h FROM home_widgets ORDER BY "order";
```

Expected: 每行 `layout_x=0, layout_w=12, layout_h=4`，`layout_y` 依次为 `0, 4, 8, 12, ...`（按 `order` 递增）。

- [ ] **Step 6: Commit**

```bash
git add server/init_db.py
git commit -m "feat(home): 首页区块表新增网格布局坐标列，迁移保留现有纵向堆叠"
```

---

### Task 2: 后端 API — 布局端点、行 JSON、默认坐标、删除旧排序端点

**Files:**
- Modify: `server/routes/home_widgets.py`
- Modify: `server/tests/test_routes_home_widgets.py`

**Interfaces:**
- Consumes: Task 1 的 `layout_x/y/w/h` 列。
- Produces: `GET /home-widgets`（含 `?all=true`）、`PUT /home-widgets`、`POST /home-widgets` 返回体新增 `layout: {x,y,w,h}` 字段；新增 `PUT /home-widgets/layout`（body `{"layout": [{"id","x","y","w","h"}, ...]}`，返回同 `GET ?all=true` 结构的数组）。Task 3 的前端 API 层依赖此返回结构与端点路径。

- [ ] **Step 1: `_row_to_json` 返回体新增 `layout` 字段**

`server/routes/home_widgets.py:13-25`，把：

```python
def _row_to_json(row):
    """Convert database row to frontend JSON format (snake_case -> camelCase)."""
    return {
        'id': row['id'],
        'widgetType': row['widget_type'],
        'title': row['title'],
        'content': row['content'] if isinstance(row['content'], dict) else json.loads(row['content'] or '{}'),
        'enabled': row['enabled'],
        'order': row['order'],
        'visibleRoles': row['visible_roles'] if isinstance(row['visible_roles'], list) else json.loads(row['visible_roles'] or '[]'),
        'createdAt': row['created_at'].isoformat() if row['created_at'] else None,
        'updatedAt': row['updated_at'].isoformat() if row['updated_at'] else None,
    }
```

改为（`layout_*` 用 `.get()` 取值、缺失时回退默认值——生产环境迁移后必有这些列，`.get()` 只是为了不强制所有既有测试 fixture 都补齐这四个键）：

```python
def _row_to_json(row):
    """Convert database row to frontend JSON format (snake_case -> camelCase)."""
    return {
        'id': row['id'],
        'widgetType': row['widget_type'],
        'title': row['title'],
        'content': row['content'] if isinstance(row['content'], dict) else json.loads(row['content'] or '{}'),
        'enabled': row['enabled'],
        'order': row['order'],
        'visibleRoles': row['visible_roles'] if isinstance(row['visible_roles'], list) else json.loads(row['visible_roles'] or '[]'),
        'layout': {
            'x': row.get('layout_x', 0),
            'y': row.get('layout_y', 0),
            'w': row.get('layout_w', 12),
            'h': row.get('layout_h', 4),
        },
        'createdAt': row['created_at'].isoformat() if row['created_at'] else None,
        'updatedAt': row['updated_at'].isoformat() if row['updated_at'] else None,
    }
```

- [ ] **Step 2: `list_home_widgets` 的两条 SELECT 语句补上 layout 列**

`server/routes/home_widgets.py:43-54`，把：

```python
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if admin_all:
            cur.execute(
                'SELECT id, widget_type, title, content, enabled, "order", visible_roles, created_at, updated_at '
                'FROM home_widgets ORDER BY "order"'
            )
        else:
            cur.execute(
                'SELECT id, widget_type, title, content, enabled, "order", visible_roles, created_at, updated_at '
                'FROM home_widgets WHERE enabled = TRUE ORDER BY "order"'
            )
        rows = cur.fetchall()
```

改为：

```python
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if admin_all:
            cur.execute(
                'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
                'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
                'FROM home_widgets ORDER BY "order"'
            )
        else:
            cur.execute(
                'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
                'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
                'FROM home_widgets WHERE enabled = TRUE ORDER BY "order"'
            )
        rows = cur.fetchall()
```

- [ ] **Step 3: `batch_update_home_widgets` 末尾 SELECT 补上 layout 列**

`server/routes/home_widgets.py:112-117`，把：

```python
        # Return updated list
        cur.execute(
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, created_at, updated_at '
            'FROM home_widgets ORDER BY "order"'
        )
        rows = cur.fetchall()
        conn.commit()
```

改为：

```python
        # Return updated list
        cur.execute(
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
            'FROM home_widgets ORDER BY "order"'
        )
        rows = cur.fetchall()
        conn.commit()
```

- [ ] **Step 4: `create_home_widget` 用「网格底部」计算默认坐标，INSERT/RETURNING 补上 layout 列**

`server/routes/home_widgets.py:140-166`，把：

```python
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get max order
        cur.execute('SELECT COALESCE(MAX("order"), 0) + 1 AS max_order FROM home_widgets')
        max_order_row = cur.fetchone()
        new_order = max_order_row['max_order']

        # Insert new widget
        cur.execute(
            'INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order", visible_roles) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s) '
            'RETURNING id, widget_type, title, content, enabled, "order", visible_roles, created_at, updated_at',
            (
                widget_id,
                widget_type,
                body.get('title', ''),
                psycopg2.extras.Json(body.get('content', {})),
                body.get('enabled', True),
                new_order,
                psycopg2.extras.Json(body.get('visibleRoles', ['admin', 'developer', 'guest']))
            )
        )
        new_row = cur.fetchone()
        conn.commit()

    return jsonify(_row_to_json(new_row)), 201
```

改为：

```python
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get max order
        cur.execute('SELECT COALESCE(MAX("order"), 0) + 1 AS max_order FROM home_widgets')
        max_order_row = cur.fetchone()
        new_order = max_order_row['max_order']

        # New widget is appended to the bottom of the grid, full width
        cur.execute('SELECT COALESCE(MAX(layout_y + layout_h), 0) AS max_bottom FROM home_widgets')
        max_bottom_row = cur.fetchone()
        new_y = max_bottom_row['max_bottom']

        # Insert new widget
        cur.execute(
            'INSERT INTO home_widgets (id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
            'RETURNING id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h, created_at, updated_at',
            (
                widget_id,
                widget_type,
                body.get('title', ''),
                psycopg2.extras.Json(body.get('content', {})),
                body.get('enabled', True),
                new_order,
                psycopg2.extras.Json(body.get('visibleRoles', ['admin', 'developer', 'guest'])),
                0, new_y, 12, 4,
            )
        )
        new_row = cur.fetchone()
        conn.commit()

    return jsonify(_row_to_json(new_row)), 201
```

- [ ] **Step 5: 删除旧的 `PUT /home-widgets/order` 端点，新增 `PUT /home-widgets/layout`**

`server/routes/home_widgets.py:188-211`（文件末尾的 `update_home_widgets_order` 函数），整段删除，替换为：

```python
@home_widgets_bp.route('/home-widgets/layout', methods=['PUT'])
@require_permission('admin.home_widgets')
def update_home_widgets_layout():
    """Batch update widget grid positions (x/y/w/h). Admin only.

    Recomputes and persists `order` from the new (y, x) reading order in the
    same transaction, so mobile rendering (which still sorts by `order`)
    stays in sync with the grid without any extra writes elsewhere.
    """
    body = request.get_json(force=True)
    layout = body.get('layout', [])

    if not layout:
        return jsonify({"error": "layout array is required"}), 400

    for item in layout:
        widget_id = item.get('id')
        x, y, w, h = item.get('x'), item.get('y'), item.get('w'), item.get('h')
        if not widget_id:
            return jsonify({"error": "each layout item requires an id"}), 400
        if not isinstance(x, int) or not isinstance(y, int) or not isinstance(w, int) or not isinstance(h, int):
            return jsonify({"error": "x/y/w/h must be integers"}), 400
        if not (0 <= x <= 11):
            return jsonify({"error": f"x must be within 0-11, got {x}"}), 400
        if not (1 <= w <= 12) or x + w > 12:
            return jsonify({"error": f"w must be within 1-12 and x+w<=12, got x={x} w={w}"}), 400
        if y < 0:
            return jsonify({"error": f"y must be >= 0, got {y}"}), 400
        if h < 1:
            return jsonify({"error": f"h must be >= 1, got {h}"}), 400

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        for item in layout:
            cur.execute(
                'UPDATE home_widgets SET layout_x = %s, layout_y = %s, layout_w = %s, layout_h = %s, updated_at = NOW() '
                'WHERE id = %s',
                (item['x'], item['y'], item['w'], item['h'], item['id'])
            )

        # Recompute `order` from the new reading order (y, x)
        cur.execute('SELECT id FROM home_widgets ORDER BY layout_y, layout_x')
        ids_in_order = [row['id'] for row in cur.fetchall()]
        for idx, widget_id in enumerate(ids_in_order):
            cur.execute('UPDATE home_widgets SET "order" = %s WHERE id = %s', (idx + 1, widget_id))

        cur.execute(
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
            'FROM home_widgets ORDER BY "order"'
        )
        rows = cur.fetchall()
        conn.commit()

    return jsonify([_row_to_json(row) for row in rows])
```

- [ ] **Step 6: 删除旧排序端点的测试类，新增布局端点测试类**

`server/tests/test_routes_home_widgets.py:366-395`（`class TestUpdateWidgetOrder`），整段删除，替换为：

```python
class TestUpdateHomeWidgetsLayout:
    def test_update_layout_success(self, setup):
        """测试批量保存网格坐标，并按新阅读顺序重算 order"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.side_effect = [
            [{'id': 'stats'}, {'id': 'welcome'}],  # ORDER BY layout_y, layout_x -> 新阅读顺序
            [
                {
                    'id': 'stats', 'widget_type': 'stats', 'title': '统计', 'content': {},
                    'enabled': True, 'order': 1, 'visible_roles': ['admin', 'developer', 'guest'],
                    'layout_x': 0, 'layout_y': 0, 'layout_w': 6, 'layout_h': 4,
                    'created_at': now, 'updated_at': now,
                },
                {
                    'id': 'welcome', 'widget_type': 'welcome', 'title': '欢迎', 'content': {},
                    'enabled': True, 'order': 2, 'visible_roles': ['admin', 'developer', 'guest'],
                    'layout_x': 6, 'layout_y': 0, 'layout_w': 6, 'layout_h': 4,
                    'created_at': now, 'updated_at': now,
                },
            ],
        ]
        resp = client.put('/home-widgets/layout',
            data=json.dumps({'layout': [
                {'id': 'stats', 'x': 0, 'y': 0, 'w': 6, 'h': 4},
                {'id': 'welcome', 'x': 6, 'y': 0, 'w': 6, 'h': 4},
            ]}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data[0]['id'] == 'stats'
        assert data[0]['layout'] == {'x': 0, 'y': 0, 'w': 6, 'h': 4}
        assert data[1]['id'] == 'welcome'

    def test_update_layout_empty(self, setup):
        """测试空 layout 数组返回错误"""
        client, _, admin_h, _ = setup
        resp = client.put('/home-widgets/layout',
            data=json.dumps({'layout': []}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_update_layout_x_out_of_range(self, setup):
        """测试 x 超出 0-11 范围返回错误"""
        client, _, admin_h, _ = setup
        resp = client.put('/home-widgets/layout',
            data=json.dumps({'layout': [{'id': 'welcome', 'x': 12, 'y': 0, 'w': 1, 'h': 4}]}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_update_layout_w_exceeds_grid(self, setup):
        """测试 x+w 超过 12 列返回错误"""
        client, _, admin_h, _ = setup
        resp = client.put('/home-widgets/layout',
            data=json.dumps({'layout': [{'id': 'welcome', 'x': 6, 'y': 0, 'w': 12, 'h': 4}]}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_update_layout_missing_id(self, setup):
        """测试缺少 id 返回错误"""
        client, _, admin_h, _ = setup
        resp = client.put('/home-widgets/layout',
            data=json.dumps({'layout': [{'x': 0, 'y': 0, 'w': 12, 'h': 4}]}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_update_layout_non_admin(self, setup):
        """测试非管理员无法保存布局"""
        client, _, _, dev_h = setup
        resp = client.put('/home-widgets/layout',
            data=json.dumps({'layout': [{'id': 'welcome', 'x': 0, 'y': 0, 'w': 12, 'h': 4}]}),
            content_type='application/json',
            headers=dev_h
        )
        assert resp.status_code == 403
```

- [ ] **Step 7: 运行后端测试确认全绿**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_home_widgets.py -v
```

Expected: 所有用例 PASS（含新增的 `TestUpdateHomeWidgetsLayout` 6 个用例，不再有 `TestUpdateWidgetOrder`）；已有的 `TestListHomeWidgets`/`TestCreateHomeWidget`/`TestDeleteHomeWidget`/`TestBatchUpdateHomeWidgets` 无需改动、原样通过（`_row_to_json` 用 `.get()` 兼容了没有 layout 键的旧 fixture）。

- [ ] **Step 8: Commit**

```bash
git add server/routes/home_widgets.py server/tests/test_routes_home_widgets.py
git commit -m "feat(home): 新增首页区块网格布局保存端点，移除旧的纯排序端点"
```

---

### Task 3: 前端 — 类型、API、Store

**Files:**
- Modify: `src/types/systemConfig.ts`
- Modify: `src/api/systemConfig.ts`
- Modify: `src/stores/systemConfig.ts`
- Modify: `src/stores/__tests__/systemConfig.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `PUT /home-widgets/layout`、`layout` 返回字段。
- Produces: `WidgetLayout { x: number; y: number; w: number; h: number }`、`WidgetLayoutUpdateItem { id: string; x: number; y: number; w: number; h: number }`（导出自 `@/types`）；`updateWidgetsLayout(layout: WidgetLayoutUpdateItem[]): Promise<WidgetConfig[]>`（导出自 `@/api/systemConfig`）；`systemConfigStore.updateLayout(layout: WidgetLayoutUpdateItem[]): Promise<void>`。Task 6/7 的组件依赖这些类型与 store action。

- [ ] **Step 1: 类型定义 — 新增 `WidgetLayout`/`WidgetLayoutUpdateItem`，`WidgetConfig` 加 `layout` 字段，删除 `OrderUpdateItem`**

`src/types/systemConfig.ts:116-127`，把：

```ts
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
```

改为：

```ts
/** 区块网格布局坐标（12 列网格，行高固定） */
export interface WidgetLayout {
  x: number
  y: number
  w: number
  h: number
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
  layout: WidgetLayout
  createdAt?: string
  updatedAt?: string
}
```

再把文件末尾（`src/types/systemConfig.ts:152-156`）：

```ts
/** 排序更新参数 */
export interface OrderUpdateItem {
  id: string
  order: number
}
```

改为：

```ts
/** 布局更新参数 */
export interface WidgetLayoutUpdateItem {
  id: string
  x: number
  y: number
  w: number
  h: number
}
```

- [ ] **Step 2: API 层 — 删除 `updateWidgetsOrder`，新增 `updateWidgetsLayout`**

`src/api/systemConfig.ts`，把顶部 import：

```ts
import type {
  SystemConfig,
  SystemConfigUpdate,
  WidgetConfig,
  OrderUpdateItem
} from '@/types'
```

改为：

```ts
import type {
  SystemConfig,
  SystemConfigUpdate,
  WidgetConfig,
  WidgetLayoutUpdateItem
} from '@/types'
```

把文件末尾：

```ts
/** 更新区块排序 */
export function updateWidgetsOrder(orders: OrderUpdateItem[]) {
  return put<{ success: boolean }>('/home-widgets/order', { orders })
}
```

改为：

```ts
/** 批量保存区块网格布局（x/y/w/h），后端会按新阅读顺序重算 order */
export function updateWidgetsLayout(layout: WidgetLayoutUpdateItem[]) {
  return put<WidgetConfig[]>('/home-widgets/layout', { layout })
}
```

- [ ] **Step 3: Store — 删除 `reorderWidgets`，新增 `updateLayout`**

`src/stores/systemConfig.ts`，把顶部 import（第 9-17 行）：

```ts
import {
  getSystemConfig as fetchConfigApi,
  updateSystemConfig as updateConfigApi,
  getHomeWidgets as fetchWidgetsApi,
  batchUpdateHomeWidgets,
  createHomeWidget,
  deleteHomeWidget,
  updateWidgetsOrder
} from '@/api/systemConfig'
import type { SystemConfig, SystemConfigUpdate, WidgetConfig, OrderUpdateItem } from '@/types'
```

改为：

```ts
import {
  getSystemConfig as fetchConfigApi,
  updateSystemConfig as updateConfigApi,
  getHomeWidgets as fetchWidgetsApi,
  batchUpdateHomeWidgets,
  createHomeWidget,
  deleteHomeWidget,
  updateWidgetsLayout
} from '@/api/systemConfig'
import type { SystemConfig, SystemConfigUpdate, WidgetConfig, WidgetLayoutUpdateItem } from '@/types'
```

把 `reorderWidgets` action（第 170-186 行）：

```ts
  /**
   * 更新排序
   */
  async function reorderWidgets(orders: OrderUpdateItem[]): Promise<void> {
    try {
      await updateWidgetsOrder(orders)
      // 更新本地状态
      const orderMap = new Map(orders.map(item => [item.id, item.order]))
      widgets.value = widgets.value.map(widget => ({
        ...widget,
        order: orderMap.get(widget.id) ?? widget.order
      }))
    } catch (error) {
      console.error('Failed to reorder widgets:', error)
      throw error
    }
  }
```

改为：

```ts
  /**
   * 批量保存网格布局（管理端拖拽/缩放后调用）
   */
  async function updateLayout(layout: WidgetLayoutUpdateItem[]): Promise<void> {
    try {
      const result = await updateWidgetsLayout(layout)
      widgets.value = result
    } catch (error) {
      console.error('Failed to update layout:', error)
      throw error
    }
  }
```

把 `return { ... }`（第 201-220 行）里的 `reorderWidgets` 换成 `updateLayout`：

```ts
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
    fetchSystemConfig,
    fetchWidgets,
    initialize,
    updateConfig,
    updateWidgets,
    createWidget,
    removeWidget,
    updateLayout
  }
```

- [ ] **Step 4: 更新 store 单测的 mock 并新增 `updateLayout` 用例**

`src/stores/__tests__/systemConfig.test.ts:5-13`，把：

```ts
vi.mock('@/api/systemConfig', () => ({
  getSystemConfig: vi.fn(),
  updateSystemConfig: vi.fn(),
  getHomeWidgets: vi.fn(),
  batchUpdateHomeWidgets: vi.fn(),
  createHomeWidget: vi.fn(),
  deleteHomeWidget: vi.fn(),
  updateWidgetsOrder: vi.fn(),
}))
```

改为：

```ts
vi.mock('@/api/systemConfig', () => ({
  getSystemConfig: vi.fn(),
  updateSystemConfig: vi.fn(),
  getHomeWidgets: vi.fn(),
  batchUpdateHomeWidgets: vi.fn(),
  createHomeWidget: vi.fn(),
  deleteHomeWidget: vi.fn(),
  updateWidgetsLayout: vi.fn(),
}))
```

在文件末尾 `describe('computed properties', ...)` 块之后（`src/stores/__tests__/systemConfig.test.ts:315` 的 `})` 之后、文件最末的 `})` 之前），新增一个 `describe` 块。先把顶部 import（第 22-28 行）加上 `updateWidgetsLayout`：

```ts
import {
  getSystemConfig,
  updateSystemConfig,
  getHomeWidgets,
  updateWidgetsLayout,
} from '@/api/systemConfig'
import { useAuthStore } from '@/stores/auth'
import { useSystemConfigStore } from '../systemConfig'

const mockGetSystemConfig = vi.mocked(getSystemConfig)
const mockUpdateSystemConfig = vi.mocked(updateSystemConfig)
const mockGetHomeWidgets = vi.mocked(getHomeWidgets)
const mockUpdateWidgetsLayout = vi.mocked(updateWidgetsLayout)
const mockUseAuthStore = vi.mocked(useAuthStore)
```

然后在文件末尾新增：

```ts
  describe('updateLayout', () => {
    it('批量保存布局后用返回值刷新 widgets', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)

      const updated = [
        {
          id: 'welcome',
          widgetType: 'welcome' as const,
          title: 'Welcome',
          content: {},
          enabled: true,
          order: 1,
          visibleRoles: [],
          layout: { x: 0, y: 0, w: 6, h: 4 },
        },
      ]
      mockUpdateWidgetsLayout.mockResolvedValueOnce(updated as any)

      const store = useSystemConfigStore()
      await store.initialize()
      await store.updateLayout([{ id: 'welcome', x: 0, y: 0, w: 6, h: 4 }])

      expect(mockUpdateWidgetsLayout).toHaveBeenCalledWith([{ id: 'welcome', x: 0, y: 0, w: 6, h: 4 }])
      expect(store.widgets).toEqual(updated)
    })

    it('保存失败时抛出错误', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)
      mockUpdateWidgetsLayout.mockRejectedValueOnce(new Error('Network error'))

      const store = useSystemConfigStore()
      await store.initialize()

      await expect(
        store.updateLayout([{ id: 'welcome', x: 0, y: 0, w: 6, h: 4 }])
      ).rejects.toThrow('Network error')
    })
  })
```

- [ ] **Step 5: 运行前端单测确认全绿**

```bash
npx vitest run src/stores/__tests__/systemConfig.test.ts
```

Expected: 所有用例 PASS（含新增的 `updateLayout` 2 个用例）。

- [ ] **Step 6: 全量类型检查（因为类型改动波及全局 `@/types` 导出）**

```bash
npx vue-tsc --noEmit
```

Expected: 无与 `WidgetConfig`/`OrderUpdateItem` 相关的类型错误（`OrderUpdateItem` 已确认仅在 `systemConfig.ts`/`stores/systemConfig.ts`/`api/systemConfig.ts` 三个文件内使用，此步骤用于兜底确认没有遗漏引用）。

- [ ] **Step 7: Commit**

```bash
git add src/types/systemConfig.ts src/api/systemConfig.ts src/stores/systemConfig.ts src/stores/__tests__/systemConfig.test.ts
git commit -m "feat(home): 前端新增区块网格布局类型/API/Store action，移除排序 action"
```

---

### Task 4: 前端 — 网格坐标纯函数工具

**Files:**
- Create: `src/utils/homeGridLayout.ts`
- Create: `src/utils/__tests__/homeGridLayout.test.ts`

**Interfaces:**
- Consumes: `WidgetLayout`、`WidgetConfig`（来自 `@/types`，Task 3 已定义）。
- Produces: `layoutToGridStyle(layout: WidgetLayout): { gridColumn: string; gridRow: string }`；`isCustomHomeWidget(widget: Pick<WidgetConfig, 'id' | 'widgetType'>): boolean`。Task 5（`HomeView.vue`）与 Task 6（`HomeLayoutEditor.vue`）都依赖这两个函数。

- [ ] **Step 1: 写失败的测试**

创建 `src/utils/__tests__/homeGridLayout.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { layoutToGridStyle, isCustomHomeWidget } from '../homeGridLayout'

describe('layoutToGridStyle', () => {
  it('将起点在 0 的坐标转换为 CSS Grid 定位（CSS Grid 从 1 开始计数）', () => {
    expect(layoutToGridStyle({ x: 0, y: 0, w: 12, h: 4 })).toEqual({
      gridColumn: '1 / span 12',
      gridRow: '1 / span 4',
    })
  })

  it('非零起点正确偏移一位', () => {
    expect(layoutToGridStyle({ x: 6, y: 4, w: 6, h: 2 })).toEqual({
      gridColumn: '7 / span 6',
      gridRow: '5 / span 2',
    })
  })
})

describe('isCustomHomeWidget', () => {
  it('id 以 custom- 开头视为自定义区块', () => {
    expect(isCustomHomeWidget({ id: 'custom-chart-abc123', widgetType: 'chart' })).toBe(true)
  })

  it('内置区块（welcome/stats/quick-links/system-info）不是自定义区块', () => {
    expect(isCustomHomeWidget({ id: 'welcome', widgetType: 'welcome' })).toBe(false)
    expect(isCustomHomeWidget({ id: 'stats', widgetType: 'stats' })).toBe(false)
  })

  it('widgetType 属于可新增类型集合也视为自定义区块（兜底旧数据 id 不带 custom- 前缀的情况）', () => {
    expect(isCustomHomeWidget({ id: 'legacy-todo-1', widgetType: 'todo' })).toBe(true)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
npx vitest run src/utils/__tests__/homeGridLayout.test.ts
```

Expected: FAIL（`Cannot find module '../homeGridLayout'`）。

- [ ] **Step 3: 实现**

创建 `src/utils/homeGridLayout.ts`：

```ts
import type { WidgetConfig, WidgetLayout, WidgetType } from '@/types'

/** 12 列网格坐标 → CSS Grid 定位样式（首页桌面态静态渲染用） */
export function layoutToGridStyle(layout: WidgetLayout): { gridColumn: string; gridRow: string } {
  return {
    gridColumn: `${layout.x + 1} / span ${layout.w}`,
    gridRow: `${layout.y + 1} / span ${layout.h}`,
  }
}

/** 可通过「首页配置」新增区块的类型（对应后端 allowed_types 白名单） */
const CREATABLE_WIDGET_TYPES: WidgetType[] = [
  'custom-markdown', 'data-card', 'quick-form',
  'chart', 'todo', 'activity', 'announcement',
]

/** 判断是否为「自定义区块」（可编辑标题/内容/可见性、可被删除，区别于四个内置区块） */
export function isCustomHomeWidget(widget: Pick<WidgetConfig, 'id' | 'widgetType'>): boolean {
  return widget.id.startsWith('custom-') || CREATABLE_WIDGET_TYPES.includes(widget.widgetType)
}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
npx vitest run src/utils/__tests__/homeGridLayout.test.ts
```

Expected: PASS（5 个用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add src/utils/homeGridLayout.ts src/utils/__tests__/homeGridLayout.test.ts
git commit -m "feat(home): 新增网格坐标转换与自定义区块判断的纯函数工具"
```

---

### Task 5: 前端 — `HomeView.vue` 桌面态网格渲染 / 移动态回退

**Files:**
- Modify: `src/views/home/HomeView.vue`

**Interfaces:**
- Consumes: `layoutToGridStyle`（Task 4）、`WidgetConfig.layout`（Task 3）。

- [ ] **Step 1: 整体替换 `HomeView.vue`**

把整个文件内容替换为：

```vue
<!--
 * 首页视图
 *
 * 职责：
 * - 动态渲染首页区块组件
 * - 从 systemConfigStore 获取可见区块配置
 * - 桌面态（>=768px）按管理员配置的网格坐标定位；移动态忽略坐标，单列纵向堆叠
 * - 支持快捷入口的批量导出功能
-->
<template>
  <div class="home-view" :class="{ 'home-view--grid': isDesktopLayout }">
    <template v-for="widget in visibleWidgets" :key="widget.id">
      <component
        :is="getWidgetComponent(widget.widgetType)"
        :content="widget.content"
        :title="widget.title"
        :widget-id="widget.id"
        class="widget-wrapper"
        :style="isDesktopLayout ? layoutToGridStyle(widget.layout) : undefined"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * HomeView 组件脚本
 *
 * 动态渲染首页区块，支持配置化管理
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useSystemConfigStore, usePageConfigStore } from '@/stores'
import { widgetComponentMap } from '@/components/home'
import { layoutToGridStyle } from '@/utils/homeGridLayout'
import type { WidgetType } from '@/types'

// ==================== Store ====================

const systemConfigStore = useSystemConfigStore()
const pageConfigStore = usePageConfigStore()

// ==================== 计算属性 ====================

/**
 * 可见区块列表
 */
const visibleWidgets = computed(() => systemConfigStore.visibleWidgets)

// ==================== 响应式布局 ====================

const DESKTOP_QUERY = '(min-width: 768px)'
let mediaQuery: MediaQueryList | null = null
const isDesktopLayout = ref(true)

function handleMediaChange(e: MediaQueryListEvent) {
  isDesktopLayout.value = e.matches
}

// ==================== 方法 ====================

/**
 * 根据区块类型获取对应组件
 */
function getWidgetComponent(type: WidgetType) {
  return widgetComponentMap[type]
}

// ==================== 生命周期 ====================

onMounted(async () => {
  await systemConfigStore.initialize()
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }

  if (typeof window !== 'undefined' && window.matchMedia) {
    mediaQuery = window.matchMedia(DESKTOP_QUERY)
    isDesktopLayout.value = mediaQuery.matches
    mediaQuery.addEventListener('change', handleMediaChange)
  }
})

onUnmounted(() => {
  mediaQuery?.removeEventListener('change', handleMediaChange)
})
</script>

<style scoped lang="scss">
.home-view {
  padding: 0;
}

.widget-wrapper {
  margin-bottom: 20px;

  &:last-child {
    margin-bottom: 0;
  }
}

.home-view--grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 16px;

  .widget-wrapper {
    margin-bottom: 0;
    min-width: 0;
  }
}
</style>
```

- [ ] **Step 2: 前端类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无错误。

- [ ] **Step 3: Commit**

```bash
git add src/views/home/HomeView.vue
git commit -m "feat(home): 首页桌面态按网格坐标渲染，移动态保持单列堆叠"
```

（此任务的可视化效果留到 Task 8 用 Playwright 统一验证，因为要等 Task 6/7 把布局真正可编辑之后才有非默认坐标可看。）

---

### Task 6: 前端 — 安装 `grid-layout-plus` + 管理端网格编辑器组件

**Files:**
- Modify: `package.json`
- Create: `src/components/admin/HomeLayoutEditor.vue`

**Interfaces:**
- Consumes: `WidgetConfig`（含 `layout`）、`WidgetLayoutUpdateItem`（Task 3）、`isCustomHomeWidget`（Task 4）。
- Produces: `HomeLayoutEditor` 组件，props `widgets: WidgetConfig[]`；emits `edit(widget: WidgetConfig)`、`delete(widget: WidgetConfig)`、`toggle(widget: WidgetConfig)`、`layout-change(items: WidgetLayoutUpdateItem[])`。Task 7 的 `SystemSettings.vue` 依赖这个 props/emits 签名。

- [ ] **Step 1: 安装依赖**

```bash
npm install grid-layout-plus
```

Expected: `package.json` 的 `dependencies` 新增一行 `"grid-layout-plus": "^x.y.z"`（版本号由 npm 解析写入，不手工指定）。

- [ ] **Step 2: 创建 `HomeLayoutEditor.vue`**

创建 `src/components/admin/HomeLayoutEditor.vue`：

```vue
<template>
  <div class="home-layout-editor">
    <GridLayout
      v-model:layout="items"
      :col-num="12"
      :row-height="30"
      :margin="[12, 12]"
      :is-draggable="true"
      :is-resizable="true"
      :vertical-compact="true"
      :use-css-transforms="true"
      @layout-updated="handleLayoutUpdated"
    >
      <GridItem
        v-for="item in items"
        :key="item.i"
        :x="item.x"
        :y="item.y"
        :w="item.w"
        :h="item.h"
        :i="item.i"
        drag-allow-from=".grid-item-drag-handle"
      >
        <div v-if="widgetById(item.i)" class="grid-item-card">
          <div class="grid-item-drag-handle">
            <el-icon class="grid-item-rank"><Rank /></el-icon>
            <el-switch
              v-model="widgetById(item.i)!.enabled"
              size="small"
              @click.stop
              @change="emit('toggle', widgetById(item.i)!)"
            />
            <span class="grid-item-title">
              {{ widgetById(item.i)!.title || getDefaultTitle(widgetById(item.i)!.widgetType) }}
            </span>
            <el-tag size="small" :type="getTagType(widgetById(item.i)!.widgetType)">
              {{ getWidgetTypeLabel(widgetById(item.i)!.widgetType) }}
            </el-tag>
          </div>
          <div class="grid-item-actions">
            <el-button
              type="primary"
              link
              size="small"
              @click.stop="emit('edit', widgetById(item.i)!)"
            >
              <el-icon><Edit /></el-icon>
            </el-button>
            <el-button
              v-if="isCustomHomeWidget(widgetById(item.i)!)"
              type="danger"
              link
              size="small"
              @click.stop="emit('delete', widgetById(item.i)!)"
            >
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
        </div>
      </GridItem>
    </GridLayout>
  </div>
</template>

<script setup lang="ts">
/**
 * 首页网格布局编辑器
 *
 * 用 grid-layout-plus 渲染可拖拽/缩放的 12 列网格，每个区块用一张卡片展示
 * 标题/类型/启用开关/编辑删除按钮。拖拽或缩放结束后通过 layout-change 事件
 * 把最新坐标交给父组件（SystemSettings.vue）统一保存。
 */
import { computed, ref, watch } from 'vue'
import { GridLayout, GridItem } from 'grid-layout-plus'
import { Rank, Edit, Delete } from '@element-plus/icons-vue'
import { isCustomHomeWidget } from '@/utils/homeGridLayout'
import type { WidgetConfig, WidgetLayoutUpdateItem, WidgetType } from '@/types'

const props = defineProps<{
  widgets: WidgetConfig[]
}>()

const emit = defineEmits<{
  edit: [widget: WidgetConfig]
  delete: [widget: WidgetConfig]
  toggle: [widget: WidgetConfig]
  'layout-change': [items: WidgetLayoutUpdateItem[]]
}>()

// ==================== 网格状态 ====================

interface GridLayoutItem { i: string; x: number; y: number; w: number; h: number }

function toGridItems(widgets: WidgetConfig[]): GridLayoutItem[] {
  return widgets.map(w => ({ i: w.id, x: w.layout.x, y: w.layout.y, w: w.layout.w, h: w.layout.h }))
}

const items = ref<GridLayoutItem[]>(toGridItems(props.widgets))

// 只在区块集合变化（新增/删除）时重新同步坐标，避免覆盖用户正在拖拽的状态
watch(
  () => props.widgets.map(w => w.id).join(','),
  () => {
    items.value = toGridItems(props.widgets)
  }
)

const widgetMap = computed(() => new Map(props.widgets.map(w => [w.id, w])))
function widgetById(id: string): WidgetConfig | undefined {
  return widgetMap.value.get(id)
}

function handleLayoutUpdated() {
  emit('layout-change', items.value.map(it => ({ id: it.i, x: it.x, y: it.y, w: it.w, h: it.h })))
}

// ==================== 展示文案 ====================

const WIDGET_TYPE_LABELS: Record<string, string> = {
  welcome: '欢迎卡片',
  stats: '统计概览',
  'quick-links': '快捷入口',
  'system-info': '系统说明',
  'custom-markdown': 'Markdown',
  'data-card': '数据卡片',
  'quick-form': '快速录入',
  chart: '图表',
  todo: '我的待办',
  activity: '最近动态',
  announcement: '公告'
}

const WIDGET_DEFAULT_TITLES: Record<string, string> = { ...WIDGET_TYPE_LABELS }

function getWidgetTypeLabel(type: WidgetType): string {
  return WIDGET_TYPE_LABELS[type] || type
}

function getDefaultTitle(type: WidgetType): string {
  return WIDGET_DEFAULT_TITLES[type] || type
}

function getTagType(type: WidgetType): 'success' | 'warning' | 'info' | '' {
  if (type.startsWith('custom-')) return 'warning'
  if (type === 'welcome') return 'success'
  if (type === 'stats') return 'info'
  return ''
}
</script>

<style scoped lang="scss">
.home-layout-editor {
  :deep(.vgl-layout) {
    background: var(--el-fill-color-lighter);
    border-radius: 4px;
  }

  :deep(.vgl-item) {
    background: var(--el-bg-color);
  }
}

.grid-item-card {
  height: 100%;
  padding: 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-bg-color);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  overflow: hidden;

  .grid-item-drag-handle {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: grab;
    overflow: hidden;

    &:active {
      cursor: grabbing;
    }
  }

  .grid-item-rank {
    color: var(--el-text-color-secondary);
    flex-shrink: 0;
  }

  .grid-item-title {
    flex: 1;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .grid-item-actions {
    display: flex;
    justify-content: flex-end;
    gap: 4px;
  }
}
</style>
```

- [ ] **Step 3: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无错误。（`grid-layout-plus` 自带 TS 类型定义；若报 `Cannot find module 'grid-layout-plus'` 则确认 Step 1 的 `npm install` 已成功写入 `node_modules`。）

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json src/components/admin/HomeLayoutEditor.vue
git commit -m "feat(home): 新增 grid-layout-plus 拖拽/缩放首页网格编辑器组件"
```

---

### Task 7: 前端 — `SystemSettings.vue` 接入网格编辑器

**Files:**
- Modify: `src/views/admin/SystemSettings.vue`

**Interfaces:**
- Consumes: `HomeLayoutEditor`（Task 6）、`systemConfigStore.updateLayout`（Task 3）、`WidgetLayoutUpdateItem`（Task 3）。

- [ ] **Step 1: 模板 — 替换 `<draggable>` 列表为 `<HomeLayoutEditor>` + 保存布局按钮**

`src/views/admin/SystemSettings.vue`，把第 130-179 行的：

```html
        <draggable
          v-else
          v-model="widgetsList"
          item-key="id"
          handle=".drag-handle"
          animation="200"
          @end="handleDragEnd"
          class="widgets-list"
        >
          <template #item="{ element }">
            <div class="widget-item">
              <el-icon class="drag-handle"><Rank /></el-icon>
              <el-switch
                v-model="element.enabled"
                @change="handleWidgetChange(element)"
              />
              <span class="widget-title">
                {{ element.title || getDefaultTitle(element.widgetType) }}
              </span>
              <el-tag size="small" :type="getTagType(element.widgetType)">
                {{ getWidgetTypeLabel(element.widgetType) }}
              </el-tag>
              <div class="widget-actions">
                <el-button
                  type="primary"
                  link
                  size="small"
                  @click="handleEditWidget(element)"
                >
                  <el-icon><Edit /></el-icon>
                </el-button>
                <el-button
                  v-if="isCustomWidget(element)"
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
```

改为：

```html
        <template v-else>
          <div class="layout-toolbar">
            <el-button
              type="success"
              :disabled="!layoutDirty"
              :loading="savingLayout"
              @click="handleSaveLayout"
            >
              <el-icon><Check /></el-icon>
              保存布局
            </el-button>
            <span v-if="layoutDirty" class="layout-dirty-hint">有未保存的修改</span>
          </div>

          <HomeLayoutEditor
            :widgets="widgetsList"
            @edit="handleEditWidget"
            @delete="handleDeleteWidget"
            @toggle="handleWidgetChange"
            @layout-change="handleLayoutChange"
          />
        </template>

        <WidgetEditDialog
          v-model="editDialogVisible"
          :widget="currentWidget"
          @save="handleSaveWidget"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
```

同时给 `<el-tabs>`（第 10 行）加上未保存离开确认：把

```html
    <el-tabs v-model="activeTab" type="border-card">
```

改为：

```html
    <el-tabs v-model="activeTab" type="border-card" :before-leave="handleTabBeforeLeave">
```

- [ ] **Step 2: 脚本 — 更新 import，删除拖拽排序相关代码，新增布局保存逻辑**

把 import 区（第 193-199 行）：

```ts
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, ArrowDown, Rank, Edit, Delete } from '@element-plus/icons-vue'
import draggable from 'vuedraggable'
import { useSystemConfigStore } from '@/stores'
import WidgetEditDialog from './components/WidgetEditDialog.vue'
import type { WidgetConfig, WidgetType } from '@/types'
```

改为：

```ts
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, ArrowDown, Check } from '@element-plus/icons-vue'
import { useSystemConfigStore } from '@/stores'
import WidgetEditDialog from './components/WidgetEditDialog.vue'
import HomeLayoutEditor from '@/components/admin/HomeLayoutEditor.vue'
import type { WidgetConfig, WidgetType, WidgetLayoutUpdateItem } from '@/types'
```

在 State 区（第 221-224 行 `widgetsList`/`editDialogVisible`/`currentWidget` 之后）新增布局保存相关的 state：

```ts
// 区块列表（网格编辑）
const widgetsList = ref<WidgetConfig[]>([])
const editDialogVisible = ref(false)
const currentWidget = ref<WidgetConfig | null>(null)
const pendingLayout = ref<WidgetLayoutUpdateItem[]>([])
const layoutDirty = ref(false)
const savingLayout = ref(false)
```

删除 `getTagType`/`isCustomWidget`/`CUSTOM_WIDGET_TYPES`（第 266-280 行，这些渲染逻辑已经搬到 `HomeLayoutEditor.vue`；`getDefaultTitle`/`WIDGET_DEFAULT_TITLES`/`getWidgetTypeLabel`/`WIDGET_TYPE_LABELS` 因为 `handleAddWidget`/`handleDeleteWidget` 还在用，保留不动）：

把：

```ts
function getTagType(type: WidgetType): 'success' | 'warning' | 'info' | '' {
  if (type.startsWith('custom-')) return 'warning'
  if (type === 'welcome') return 'success'
  if (type === 'stats') return 'info'
  return ''
}

const CUSTOM_WIDGET_TYPES = [
  'custom-markdown', 'data-card', 'quick-form',
  'chart', 'todo', 'activity', 'announcement',
]

function isCustomWidget(widget: WidgetConfig): boolean {
  return widget.id.startsWith('custom-') || CUSTOM_WIDGET_TYPES.includes(widget.widgetType)
}
```

整段删除（不再需要，`WidgetType` 类型 import 仍保留，因为 `getDefaultTitle`/`getWidgetTypeLabel`/`handleAddWidget` 的参数类型还在用）。

把 `handleDragEnd`（第 315-325 行）：

```ts
async function handleDragEnd() {
  const orders = widgetsList.value.map((w, idx) => ({
    id: w.id,
    order: idx + 1
  }))
  try {
    await systemConfigStore.reorderWidgets(orders)
  } catch {
    ElMessage.error('排序保存失败')
  }
}
```

改为：

```ts
function handleLayoutChange(items: WidgetLayoutUpdateItem[]) {
  pendingLayout.value = items
  layoutDirty.value = true
}

async function handleSaveLayout() {
  if (!layoutDirty.value) return
  savingLayout.value = true
  try {
    await systemConfigStore.updateLayout(pendingLayout.value)
    widgetsList.value = [...systemConfigStore.widgets]
    layoutDirty.value = false
    ElMessage.success('布局已保存')
  } catch {
    ElMessage.error('布局保存失败')
  } finally {
    savingLayout.value = false
  }
}

async function handleTabBeforeLeave(_activeName: string, oldActiveName: string): Promise<boolean> {
  if (oldActiveName === 'widgets' && layoutDirty.value) {
    try {
      await ElMessageBox.confirm('布局有未保存的修改，确定要离开吗？', '提示', { type: 'warning' })
      layoutDirty.value = false
      return true
    } catch {
      return false
    }
  }
  return true
}
```

在 `handleAddWidget`（第 368-387 行）里，新增区块推入 `widgetsList` 之后本地布局也需要跟着刷新——把：

```ts
    // 从 store 获取最新创建的 widget（store 已 push）
    const newWidget = systemConfigStore.widgets[systemConfigStore.widgets.length - 1]
    if (newWidget) {
      widgetsList.value.push(newWidget)
    }
    ElMessage.success('创建成功')
```

保持不动即可——`HomeLayoutEditor` 内部的 `watch(() => props.widgets.map(w => w.id).join(','), ...)`（Task 6）已经会在 `widgetsList` 增加一个 id 时自动重新同步 `items`（含新区块用后端算好的 `layout`），不需要在这里额外处理坐标。

- [ ] **Step 3: 样式 — 删除旧的 `.widgets-list` 样式块，新增 `.layout-toolbar` 样式**

把 `<style>` 区（第 454-492 行）的：

```scss
.widgets-list {
  .widget-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    border: 1px solid var(--el-border-color);
    border-radius: 4px;
    margin-bottom: 8px;
    background: var(--el-bg-color);
    transition: box-shadow 0.2s;

    &:hover {
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    }

    .drag-handle {
      cursor: grab;
      color: var(--el-text-color-secondary);

      &:active {
        cursor: grabbing;
      }
    }

    .widget-title {
      flex: 1;
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .widget-actions {
      display: flex;
      gap: 4px;
    }
  }
}
```

改为：

```scss
.layout-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.layout-dirty-hint {
  font-size: 12px;
  color: var(--el-color-warning);
}
```

- [ ] **Step 4: 前端类型检查 + 全量单测**

```bash
npx vue-tsc --noEmit
npm run test
```

Expected: 类型检查无错误；`npm run test` 全绿（`SystemSettings.vue` 目前没有专门的组件单测文件，此步骤主要确认没有破坏其它套件，例如 `systemConfig.test.ts`）。

- [ ] **Step 5: Commit**

```bash
git add src/views/admin/SystemSettings.vue
git commit -m "feat(home): 系统设置首页配置改用自由拖拽/缩放网格编辑器"
```

---

### Task 8: 文档同步 + Playwright 端到端验证

**Files:**
- Modify: `docs/user-guide/admin/system-settings.md`
- Create: `docs/user-guide/_images/home-layout-editor.png`（Playwright 截图）

- [ ] **Step 1: 启动前后端服务**

```bash
npm run dev:all
```

Expected: 前端 `http://localhost:5173`、后端 `http://localhost:3002` 均正常启动（backend 需已跑过 Task 1 的 `python init_db.py` 迁移）。

- [ ] **Step 2: Playwright 驱动管理端拖拽/缩放并保存**

用 Playwright MCP 工具：
1. `browser_navigate` 到 `http://localhost:5173/login`，用 `admin`/`admin123` 登录。
2. 导航到「设置中心 → 通用设置」，切到「首页配置」Tab。
3. 用 `browser_snapshot` 确认页面渲染出 `HomeLayoutEditor` 的网格卡片（而不是旧的纵向列表）。
4. 用 `browser_drag` 把某个区块（如「快捷入口」）拖到「欢迎卡片」右侧，形成并排布局；用鼠标拖拽区块右下角把宽度缩小到网格一半左右。
5. 确认「保存布局」按钮从禁用变为可点击（`layoutDirty` 生效），点击它。
6. 用 `browser_snapshot`/`browser_network_requests` 确认 `PUT /home-widgets/layout` 请求返回 200。
7. `browser_take_screenshot`，保存到 `.playwright-mcp/home-layout-editor-admin.png`。

- [ ] **Step 3: Playwright 验证首页实际渲染位置生效**

1. `browser_navigate` 到首页 `http://localhost:5173/`。
2. `browser_snapshot` 确认「欢迎卡片」与「快捷入口」在同一行并排显示（而不是各占一整行）。
3. `browser_take_screenshot`，保存到 `.playwright-mcp/home-desktop-grid.png`。

- [ ] **Step 4: Playwright 验证移动端回退单列堆叠**

1. `browser_resize` 窗口宽度到 `375`（<768px）。
2. `browser_navigate` 刷新首页（或重新导航到 `/`）。
3. `browser_snapshot` 确认所有区块变为单列、从上到下纵向排列，顺序与桌面态阅读顺序一致（Task 2 的 `order` 重算保证了这一点）。
4. `browser_take_screenshot`，保存到 `.playwright-mcp/home-mobile-stack.png`。
5. `browser_resize` 把窗口宽度改回 `1280`，确认桌面态网格恢复。

- [ ] **Step 5: 更新用户文档**

把 `docs/user-guide/admin/system-settings.md:38-40` 的：

```markdown
## 首页配置 · 区块类型

在 **设置中心 → 通用设置 → 首页配置** 中点击「**新增区块**」可添加以下区块（支持拖拽排序、按角色可见、启用/停用）：
```

改为：

```markdown
## 首页配置 · 区块类型与布局

在 **设置中心 → 通用设置 → 首页配置** 中点击「**新增区块**」可添加以下区块（按角色可见、启用/停用）：
```

在该章节的表格与提示块之后（原第 56 行 `![首页区块示例](../_images/home-new-widgets.png)` 之后）新增一节：

```markdown
### 自由布局（拖拽 / 缩放）

「首页配置」Tab 用一个 12 列网格编辑器展示所有区块：

- **拖拽**卡片的空白区域可移动位置，让多个区块并排显示；**拖拽卡片右下角**可调整宽高。
- 调整后点击顶部的「**保存布局**」按钮才会生效（未保存时切换到「基本设置」Tab 会弹出确认提示）。
- 布局是**全局**的：管理员保存后，所有用户打开首页看到的都是同一份排布（仍受每个区块的「可见角色」/启用开关控制是否显示）。
- **移动端**（窄屏浏览器）不支持自由布局，会自动按阅读顺序（从左到右、从上到下）回退为单列纵向堆叠，无需额外配置。

![首页网格布局编辑器](../_images/home-layout-editor.png)
```

把 Step 2 截图（`.playwright-mcp/home-layout-editor-admin.png`）拷贝为 `docs/user-guide/_images/home-layout-editor.png`。

- [ ] **Step 6: Commit**

```bash
git add docs/user-guide/admin/system-settings.md docs/user-guide/_images/home-layout-editor.png
git commit -m "docs(home): 首页配置文档补充自由布局编辑器说明与截图"
```

---

## Self-Review Notes

- **Spec coverage**：数据模型/迁移 → Task 1；后端 API（layout 字段、`PUT /home-widgets/layout`、`POST` 默认坐标、删除旧端点）→ Task 2；管理端编辑体验（grid-layout-plus、保存按钮、离开确认）→ Task 6/7；首页渲染（桌面 CSS Grid / 移动回退）→ Task 5；测试（后端 pytest、前端 vitest、Playwright）→ Task 2/3/4/8；文档同步 → Task 8。范围之外的三项（每用户个性化、移动端自由布局、与仪表盘编辑器合并）本计划未涉及，与 spec 一致。
- **Placeholder scan**：已核对，无 TBD/TODO，所有步骤含完整代码或明确操作序列。
- **Type consistency**：`WidgetLayout`/`WidgetLayoutUpdateItem`（Task 3 定义）→ `layoutToGridStyle`/`isCustomHomeWidget`（Task 4 消费）→ `HomeView.vue`/`HomeLayoutEditor.vue`（Task 5/6 消费）→ `SystemSettings.vue`（Task 7 消费 `HomeLayoutEditor` 的 `layout-change` emit 签名 `WidgetLayoutUpdateItem[]`）全程一致；后端 `layout: {x,y,w,h}` 字段名与前端 `WidgetLayout` 字段名一致。
