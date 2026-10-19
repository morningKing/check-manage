# 首页区块面板拖拽新增 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「系统设置 → 首页配置」新增一个常驻区块面板（类似 WordPress 区块库），点击卡片＝加到网格底部（现状不变），拖拽卡片到网格任意位置＝按落点坐标直接创建。

**Architecture:** 后端 `POST /home-widgets` 新增可选 `layout: {x,y,w,h}` 字段（不传保持现有"追加到底部"行为，传了就用并复用 `PUT /home-widgets/layout` 已有的校验/`order` 重算逻辑）。前端新增 `HomeBlockPalette.vue`（拖拽源，原生 HTML5 DnD）+ `HomeLayoutEditor.vue` 新增 drop 处理（把像素坐标换算成网格坐标，公式镜像自 `grid-layout-plus` 内部真实实现，已在 `node_modules` 中验证）+ `SystemSettings.vue` 接线。

**Tech Stack:** Vue 3 + TypeScript + Element Plus + Pinia；Flask + psycopg2；Vitest + Pytest + Playwright。不引入新依赖（拖拽用浏览器原生 HTML5 Drag and Drop API）。

## Global Constraints

- 不新增区块类型：面板只展示现有 7 种可创建类型（`custom-markdown`/`data-card`/`quick-form`/`chart`/`todo`/`activity`/`announcement`），与后端 `allowed_types` 白名单一致。
- 拖拽创建默认尺寸 `w=6, h=4`；点击创建默认尺寸保持 `w=12, h=4`（现状不变）。
- 拖到的位置允许与现有区块重叠，不做碰撞校验/自动避让（与已上线的 `PUT /home-widgets/layout` 行为一致）。
- 像素→网格坐标换算公式必须与 `grid-layout-plus` 内部实现一致：`colWidth = (containerWidth - marginX*(colNum+1)) / colNum`，`x = round((left-marginX)/(colWidth+marginX))`，`y = round((top-marginY)/(rowHeight+marginY))`，其中 `colNum=12`、`rowHeight=30`、`marginX=marginY=12`（与 `HomeLayoutEditor.vue` 里 `GridLayout` 的固定 props 一致）。`x` clamp 到 `[0, 12-w]`，`y` clamp 到 `>=0`。
- 带显式 `layout` 创建区块后，必须复用 `PUT /home-widgets/layout` 现有的"按 `(layout_y, layout_x)` 重算并写回全部区块 `order`"逻辑（保证移动端单列回退顺序与桌面阅读顺序一致）；不带 `layout` 的创建路径（点击加到底部）不触发这个重算，保持现状。
- 任何 UI 改动完成后必须用 Playwright 实测验证（CLAUDE.md 强制要求），并同步更新 `docs/user-guide/admin/system-settings.md`。

---

## File Structure

**Backend:**
- Modify `server/routes/home_widgets.py` — 提取 `_validate_layout_item`/`_recompute_order` 两个共享辅助函数；`create_home_widget` 支持可选 `layout` 覆盖；`update_home_widgets_layout` 改为调用这两个共享函数（内部行为不变，只是消除重复代码）。
- Modify `server/tests/test_routes_home_widgets.py` — 新增 `POST /home-widgets` 带 `layout` 覆盖的成功/校验失败用例。

**Frontend:**
- Modify `src/types/systemConfig.ts` — 新增导出 `CreatableWidgetType`（消除 `api/systemConfig.ts`/`stores/systemConfig.ts`/`SystemSettings.vue` 里重复的同一个联合类型字面量）。
- Modify `src/api/systemConfig.ts` — `createHomeWidget` 的 `data` 参数新增可选 `layout?: WidgetLayout`。
- Modify `src/stores/systemConfig.ts` — `createWidget` 的 `data` 参数新增可选 `layout?: WidgetLayout`，透传给 API。
- Modify `src/stores/__tests__/systemConfig.test.ts` — 新增 `createWidget` 透传 `layout` 的单测。
- Modify `src/utils/homeGridLayout.ts` — 新增 `pixelToGridPosition` 纯函数（像素坐标→网格坐标）。
- Modify `src/utils/__tests__/homeGridLayout.test.ts` — 新增 `pixelToGridPosition` 单测。
- Create `src/components/admin/HomeBlockPalette.vue` — 区块面板（拖拽源 + 点击加到底部）。
- Modify `src/components/admin/HomeLayoutEditor.vue` — 网格容器新增 `dragover`/`drop` 处理，emit `create-at`。
- Modify `src/views/admin/SystemSettings.vue` — 移除旧的下拉菜单，接入 `HomeBlockPalette` + 两栏布局，新增 `handleCreateAt`（与既有 `handleAddWidget` 共用一个内部 `createWidgetOfType` 辅助函数，消除重复）。
- Modify `docs/user-guide/admin/system-settings.md` — 补拖拽新增的说明和截图。

---

### Task 1: 后端 — 提取共享校验/重算逻辑 + `POST /home-widgets` 支持 `layout` 覆盖

**Files:**
- Modify: `server/routes/home_widgets.py:13-25`（`_row_to_json` 附近新增两个模块级辅助函数）
- Modify: `server/routes/home_widgets.py:132-183`（`create_home_widget`）
- Modify: `server/routes/home_widgets.py:205-259`（`update_home_widgets_layout`，改为调用共享函数）
- Test: `server/tests/test_routes_home_widgets.py`

**Interfaces:**
- Produces: `POST /home-widgets` 请求体新增可选字段 `layout: {x: number, y: number, w: number, h: number}`；响应体结构不变（仍是 `_row_to_json` 的输出，`layout` 字段反映实际写入的坐标）。Task 3（前端 API 层）依赖这个字段名与校验错误的 400 行为。

- [ ] **Step 1: 在 `_row_to_json` 定义之后新增两个共享辅助函数**

`server/routes/home_widgets.py`，在 `_row_to_json` 函数（第 13-25 行）结束之后、`list_home_widgets` 路由（第 28 行）之前，插入：

```python
def _validate_layout_item(x, y, w, h):
    """Validate a single grid position. Returns an error message string, or None if valid."""
    if not isinstance(x, int) or not isinstance(y, int) or not isinstance(w, int) or not isinstance(h, int):
        return "x/y/w/h must be integers"
    if not (0 <= x <= 11):
        return f"x must be within 0-11, got {x}"
    if not (1 <= w <= 12) or x + w > 12:
        return f"w must be within 1-12 and x+w<=12, got x={x} w={w}"
    if y < 0:
        return f"y must be >= 0, got {y}"
    if h < 1:
        return f"h must be >= 1, got {h}"
    return None


def _recompute_order(cur):
    """Recompute and persist `order` from the (layout_y, layout_x) reading order.

    Must run inside the caller's existing transaction (does not commit).
    """
    cur.execute('SELECT id FROM home_widgets ORDER BY layout_y, layout_x')
    ids_in_order = [row['id'] for row in cur.fetchall()]
    for idx, widget_id in enumerate(ids_in_order):
        cur.execute('UPDATE home_widgets SET "order" = %s WHERE id = %s', (idx + 1, widget_id))
```

- [ ] **Step 2: `update_home_widgets_layout` 改用共享函数（行为完全不变，只是去重）**

`server/routes/home_widgets.py:205-259`，把整个函数体：

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

改为：

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
        if not widget_id:
            return jsonify({"error": "each layout item requires an id"}), 400
        error = _validate_layout_item(item.get('x'), item.get('y'), item.get('w'), item.get('h'))
        if error:
            return jsonify({"error": error}), 400

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        for item in layout:
            cur.execute(
                'UPDATE home_widgets SET layout_x = %s, layout_y = %s, layout_w = %s, layout_h = %s, updated_at = NOW() '
                'WHERE id = %s',
                (item['x'], item['y'], item['w'], item['h'], item['id'])
            )

        _recompute_order(cur)

        cur.execute(
            'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
            'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
            'FROM home_widgets ORDER BY "order"'
        )
        rows = cur.fetchall()
        conn.commit()

    return jsonify([_row_to_json(row) for row in rows])
```

- [ ] **Step 3: `create_home_widget` 支持可选 `layout` 覆盖**

`server/routes/home_widgets.py:132-183`，把整个函数体：

```python
@home_widgets_bp.route('/home-widgets', methods=['POST'])
@require_permission('admin.home_widgets')
def create_home_widget():
    """Create a custom widget. Admin only."""
    body = request.get_json(force=True)
    widget_type = body.get('widgetType', '')

    allowed_types = (
        'custom-markdown', 'data-card', 'quick-form',
        'chart', 'todo', 'activity', 'announcement',
    )
    if widget_type not in allowed_types:
        return jsonify({"error": f"Widget type must be one of: {', '.join(allowed_types)}"}), 400

    # Generate ID: custom-{type}-{uuid8}
    widget_id = f'custom-{widget_type}-{uuid.uuid4().hex[:8]}'

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

改为：

```python
@home_widgets_bp.route('/home-widgets', methods=['POST'])
@require_permission('admin.home_widgets')
def create_home_widget():
    """Create a custom widget. Admin only.

    Optional body field `layout: {x,y,w,h}` places the widget at an explicit
    grid position (used by drag-to-create from the block palette). When
    omitted, the widget is appended full-width to the bottom of the grid
    (unchanged default behavior for the "click to add" path).
    """
    body = request.get_json(force=True)
    widget_type = body.get('widgetType', '')

    allowed_types = (
        'custom-markdown', 'data-card', 'quick-form',
        'chart', 'todo', 'activity', 'announcement',
    )
    if widget_type not in allowed_types:
        return jsonify({"error": f"Widget type must be one of: {', '.join(allowed_types)}"}), 400

    layout = body.get('layout')
    if layout is not None:
        error = _validate_layout_item(layout.get('x'), layout.get('y'), layout.get('w'), layout.get('h'))
        if error:
            return jsonify({"error": error}), 400

    # Generate ID: custom-{type}-{uuid8}
    widget_id = f'custom-{widget_type}-{uuid.uuid4().hex[:8]}'

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get max order
        cur.execute('SELECT COALESCE(MAX("order"), 0) + 1 AS max_order FROM home_widgets')
        max_order_row = cur.fetchone()
        new_order = max_order_row['max_order']

        if layout is not None:
            new_x, new_y, new_w, new_h = layout['x'], layout['y'], layout['w'], layout['h']
        else:
            # New widget is appended to the bottom of the grid, full width
            cur.execute('SELECT COALESCE(MAX(layout_y + layout_h), 0) AS max_bottom FROM home_widgets')
            max_bottom_row = cur.fetchone()
            new_x, new_y, new_w, new_h = 0, max_bottom_row['max_bottom'], 12, 4

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
                new_x, new_y, new_w, new_h,
            )
        )
        new_row = cur.fetchone()

        if layout is not None:
            # Explicit position was placed somewhere other than "the bottom" —
            # recompute order from (y, x) so mobile stacking matches the grid.
            _recompute_order(cur)
            cur.execute(
                'SELECT id, widget_type, title, content, enabled, "order", visible_roles, '
                'layout_x, layout_y, layout_w, layout_h, created_at, updated_at '
                'FROM home_widgets WHERE id = %s',
                (widget_id,)
            )
            new_row = cur.fetchone()

        conn.commit()

    return jsonify(_row_to_json(new_row)), 201
```

- [ ] **Step 4: 新增测试**

`server/tests/test_routes_home_widgets.py` 的 `class TestCreateHomeWidget` 内（可放在现有 `test_create_announcement_widget` 方法之后），新增：

```python
    def test_create_widget_with_layout_override(self, setup):
        """测试拖拽创建：带显式 layout 时跳过"追加到底部"，并重算 order"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.side_effect = [
            {'max_order': 3},  # COALESCE(MAX("order")) 查询
            {  # INSERT ... RETURNING（插入时的坐标）
                'id': 'custom-chart-ddee1122', 'widget_type': 'chart', 'title': '图表',
                'content': {}, 'enabled': True, 'order': 4,
                'visible_roles': ['admin', 'developer', 'guest'],
                'layout_x': 3, 'layout_y': 2, 'layout_w': 6, 'layout_h': 4,
                'created_at': now, 'updated_at': now,
            },
            {  # _recompute_order 之后重新查询同一行（order 可能已变化）
                'id': 'custom-chart-ddee1122', 'widget_type': 'chart', 'title': '图表',
                'content': {}, 'enabled': True, 'order': 1,
                'visible_roles': ['admin', 'developer', 'guest'],
                'layout_x': 3, 'layout_y': 2, 'layout_w': 6, 'layout_h': 4,
                'created_at': now, 'updated_at': now,
            },
        ]
        mock_cursor.fetchall.return_value = [{'id': 'custom-chart-ddee1122'}]  # ORDER BY layout_y, layout_x
        resp = client.post('/home-widgets',
            data=json.dumps({
                'widgetType': 'chart', 'title': '图表', 'content': {},
                'layout': {'x': 3, 'y': 2, 'w': 6, 'h': 4},
            }),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['layout'] == {'x': 3, 'y': 2, 'w': 6, 'h': 4}
        assert data['order'] == 1  # 重算后的值，不是插入时算的 4

        # 未再查询 max_bottom（"追加到底部"的那条 SQL 不应被执行）
        sql_calls = ' '.join(str(c.args[0]) for c in mock_cursor.execute.call_args_list if c.args)
        assert 'MAX(layout_y + layout_h)' not in sql_calls

    def test_create_widget_invalid_layout_override(self, setup):
        """测试拖拽创建：layout 越界返回 400"""
        client, _, admin_h, _ = setup
        resp = client.post('/home-widgets',
            data=json.dumps({
                'widgetType': 'chart', 'title': '图表', 'content': {},
                'layout': {'x': 10, 'y': 0, 'w': 6, 'h': 4},  # x+w=16 > 12
            }),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400
```

- [ ] **Step 5: 运行后端测试确认全绿**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_home_widgets.py -v
```

Expected: 全部 PASS，含新增的 2 个用例；`TestCreateHomeWidget` 里原有的 6 个用例（不带 `layout`）保持通过（它们的 `fetchone.side_effect` 仍是 3 项：`max_order`、`max_bottom`、插入返回行，因为 `layout is None` 时代码路径与改动前完全一致）。

- [ ] **Step 6: Commit**

```bash
git add server/routes/home_widgets.py server/tests/test_routes_home_widgets.py
git commit -m "feat(home): POST /home-widgets 支持可选 layout 覆盖，用于区块面板拖拽创建"
```

---

### Task 2: 前端 — 类型定义去重（`CreatableWidgetType`）

**Files:**
- Modify: `src/types/systemConfig.ts`
- Modify: `src/api/systemConfig.ts`
- Modify: `src/stores/systemConfig.ts`

**Interfaces:**
- Produces: `CreatableWidgetType`（`'custom-markdown' | 'data-card' | 'quick-form' | 'chart' | 'todo' | 'activity' | 'announcement'`，导出自 `@/types`）。Task 5（`HomeBlockPalette.vue`）、Task 7（`SystemSettings.vue`）都用这个类型。

- [ ] **Step 1: `src/types/systemConfig.ts` 新增导出类型**

在文件末尾（`WidgetLayoutUpdateItem` 定义之后）新增：

```ts

/** 可通过「首页配置」新增的区块类型（对应后端 create_home_widget 的 allowed_types 白名单） */
export type CreatableWidgetType =
  | 'custom-markdown'
  | 'data-card'
  | 'quick-form'
  | 'chart'
  | 'todo'
  | 'activity'
  | 'announcement'
```

- [ ] **Step 2: `src/api/systemConfig.ts` 复用该类型，`createHomeWidget` 新增可选 `layout`**

把：

```ts
import type {
  SystemConfig,
  SystemConfigUpdate,
  WidgetConfig,
  WidgetLayoutUpdateItem
} from '@/types'
```

改为：

```ts
import type {
  SystemConfig,
  SystemConfigUpdate,
  WidgetConfig,
  WidgetLayout,
  WidgetLayoutUpdateItem,
  CreatableWidgetType
} from '@/types'
```

把：

```ts
/** 创建自定义区块 */
export function createHomeWidget(data: {
  widgetType: 'custom-markdown' | 'data-card' | 'quick-form' | 'chart' | 'todo' | 'activity' | 'announcement'
  title?: string
  content: Record<string, any>
  visibleRoles?: string[]
}) {
  return post<WidgetConfig>('/home-widgets', data)
}
```

改为：

```ts
/** 创建自定义区块。传 layout 时按指定网格坐标创建（区块面板拖拽新增用），不传则追加到底部 */
export function createHomeWidget(data: {
  widgetType: CreatableWidgetType
  title?: string
  content: Record<string, any>
  visibleRoles?: string[]
  layout?: WidgetLayout
}) {
  return post<WidgetConfig>('/home-widgets', data)
}
```

- [ ] **Step 3: `src/stores/systemConfig.ts` 的 `createWidget` action 同步透传 `layout`**

把 import（`WidgetConfig, WidgetLayoutUpdateItem` 那一行）改为也引入 `WidgetLayout, CreatableWidgetType`：

把：

```ts
import type { SystemConfig, SystemConfigUpdate, WidgetConfig, WidgetLayoutUpdateItem } from '@/types'
```

改为：

```ts
import type { SystemConfig, SystemConfigUpdate, WidgetConfig, WidgetLayout, WidgetLayoutUpdateItem, CreatableWidgetType } from '@/types'
```

把：

```ts
  /**
   * 创建自定义区块
   */
  async function createWidget(data: {
    widgetType: 'custom-markdown' | 'data-card' | 'quick-form' | 'chart' | 'todo' | 'activity' | 'announcement'
    title?: string
    content: Record<string, any>
    visibleRoles?: string[]
  }): Promise<void> {
    try {
      const widget = await createHomeWidget(data)
      widgets.value.push(widget)
    } catch (error) {
      console.error('Failed to create widget:', error)
      throw error
    }
  }
```

改为：

```ts
  /**
   * 创建自定义区块。传 layout 时按指定网格坐标创建（区块面板拖拽新增用），不传则追加到底部
   */
  async function createWidget(data: {
    widgetType: CreatableWidgetType
    title?: string
    content: Record<string, any>
    visibleRoles?: string[]
    layout?: WidgetLayout
  }): Promise<void> {
    try {
      const widget = await createHomeWidget(data)
      widgets.value.push(widget)
    } catch (error) {
      console.error('Failed to create widget:', error)
      throw error
    }
  }
```

- [ ] **Step 4: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无新增错误（`SystemSettings.vue` 里仍有它自己的本地 `CreatableWidgetType` 类型别名，此时和 `@/types` 里的同名类型结构相同、暂不冲突——Task 7 会把它替换为直接引用 `@/types` 的版本）。

- [ ] **Step 5: Commit**

```bash
git add src/types/systemConfig.ts src/api/systemConfig.ts src/stores/systemConfig.ts
git commit -m "feat(home): 前端 createWidget/createHomeWidget 支持可选 layout 参数"
```

---

### Task 3: 前端 — `createWidget` store action 的 `layout` 透传单测

**Files:**
- Modify: `src/stores/__tests__/systemConfig.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `systemConfigStore.createWidget(data)`（`data.layout?: WidgetLayout`）。

- [ ] **Step 1: 补充 mock 导入**

把顶部：

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

改为：

```ts
import {
  getSystemConfig,
  updateSystemConfig,
  getHomeWidgets,
  updateWidgetsLayout,
  createHomeWidget,
} from '@/api/systemConfig'
import { useAuthStore } from '@/stores/auth'
import { useSystemConfigStore } from '../systemConfig'

const mockGetSystemConfig = vi.mocked(getSystemConfig)
const mockUpdateSystemConfig = vi.mocked(updateSystemConfig)
const mockGetHomeWidgets = vi.mocked(getHomeWidgets)
const mockUpdateWidgetsLayout = vi.mocked(updateWidgetsLayout)
const mockCreateHomeWidget = vi.mocked(createHomeWidget)
const mockUseAuthStore = vi.mocked(useAuthStore)
```

- [ ] **Step 2: 在文件末尾（`updateLayout` describe 块之后）新增测试**

```ts
  describe('createWidget', () => {
    it('传入 layout 时透传给 createHomeWidget，并把返回的区块 push 进 widgets', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)

      const created = {
        id: 'custom-chart-abc123',
        widgetType: 'chart' as const,
        title: '图表',
        content: {},
        enabled: true,
        order: 1,
        visibleRoles: ['admin', 'developer', 'guest'],
        layout: { x: 3, y: 2, w: 6, h: 4 },
      }
      mockCreateHomeWidget.mockResolvedValueOnce(created as any)

      const store = useSystemConfigStore()
      await store.initialize()
      await store.createWidget({
        widgetType: 'chart',
        title: '图表',
        content: {},
        layout: { x: 3, y: 2, w: 6, h: 4 },
      })

      expect(mockCreateHomeWidget).toHaveBeenCalledWith({
        widgetType: 'chart',
        title: '图表',
        content: {},
        layout: { x: 3, y: 2, w: 6, h: 4 },
      })
      expect(store.widgets).toContainEqual(created)
    })
  })
```

- [ ] **Step 3: 运行测试**

```bash
npx vitest run src/stores/__tests__/systemConfig.test.ts
```

Expected: 全部 PASS（含新增的 `createWidget` 用例）。

- [ ] **Step 4: Commit**

```bash
git add src/stores/__tests__/systemConfig.test.ts
git commit -m "test(home): 补 createWidget 透传 layout 参数的单测"
```

---

### Task 4: 前端 — 像素坐标→网格坐标纯函数

**Files:**
- Modify: `src/utils/homeGridLayout.ts`
- Modify: `src/utils/__tests__/homeGridLayout.test.ts`

**Interfaces:**
- Produces: `pixelToGridPosition(left: number, top: number, containerWidth: number, w: number): { x: number; y: number }`。Task 6（`HomeLayoutEditor.vue` 的 drop 处理）依赖这个函数名与签名。

- [ ] **Step 1: 写失败的测试**

在 `src/utils/__tests__/homeGridLayout.test.ts` 末尾新增：

```ts
import { pixelToGridPosition } from '../homeGridLayout'

describe('pixelToGridPosition', () => {
  // containerWidth=900, colNum=12, marginX=12 => colWidth = (900 - 12*13) / 12 = 62
  it('容器左上角 (0,0) 换算为网格 (0,0)', () => {
    expect(pixelToGridPosition(0, 0, 900, 6)).toEqual({ x: 0, y: 0 })
  })

  it('落在第 2 列（一个 colWidth+marginX 之外）换算为 x=1', () => {
    // left = colWidth + marginX = 62 + 12 = 74
    expect(pixelToGridPosition(74, 0, 900, 6)).toEqual({ x: 1, y: 0 })
  })

  it('落在第 2 行（一个 rowHeight+marginY 之外）换算为 y=1', () => {
    // top = rowHeight + marginY = 30 + 12 = 42
    expect(pixelToGridPosition(0, 42, 900, 6)).toEqual({ x: 0, y: 1 })
  })

  it('超出右边界时 x 被 clamp 到 12-w', () => {
    expect(pixelToGridPosition(10000, 0, 900, 6)).toEqual({ x: 6, y: 0 })
  })

  it('负坐标时 x/y 被 clamp 到 0', () => {
    expect(pixelToGridPosition(-100, -100, 900, 6)).toEqual({ x: 0, y: 0 })
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
npx vitest run src/utils/__tests__/homeGridLayout.test.ts
```

Expected: FAIL（`pixelToGridPosition` 不存在）。

- [ ] **Step 3: 实现**

在 `src/utils/homeGridLayout.ts` 末尾新增：

```ts
/** 与 HomeLayoutEditor.vue 里 GridLayout 的 col-num/row-height/margin 固定值保持一致 */
const GRID_COL_NUM = 12
const GRID_ROW_HEIGHT = 30
const GRID_MARGIN_X = 12
const GRID_MARGIN_Y = 12

/**
 * 像素坐标（相对网格容器左上角）→ 网格坐标（列/行）。
 *
 * 公式镜像自 grid-layout-plus 内部的 calcXY 实现
 * （node_modules/grid-layout-plus/es/components/grid-item.vue.mjs 约第 264-270 行），
 * 保证换算结果与库自身拖拽/缩放时的定位完全一致。
 *
 * @param left 相对网格容器左边缘的像素横坐标
 * @param top 相对网格容器上边缘的像素纵坐标
 * @param containerWidth 网格容器的实际像素宽度
 * @param w 待放置区块的网格列宽（用于把 x clamp 在 [0, 12-w] 内）
 */
export function pixelToGridPosition(
  left: number,
  top: number,
  containerWidth: number,
  w: number
): { x: number; y: number } {
  const colWidth = (containerWidth - GRID_MARGIN_X * (GRID_COL_NUM + 1)) / GRID_COL_NUM
  const rawX = Math.round((left - GRID_MARGIN_X) / (colWidth + GRID_MARGIN_X))
  const rawY = Math.round((top - GRID_MARGIN_Y) / (GRID_ROW_HEIGHT + GRID_MARGIN_Y))
  const x = Math.max(0, Math.min(rawX, GRID_COL_NUM - w))
  const y = Math.max(0, rawY)
  return { x, y }
}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
npx vitest run src/utils/__tests__/homeGridLayout.test.ts
```

Expected: PASS（10 个用例全绿：Task 4 原有 5 个 + 本任务新增 5 个）。

- [ ] **Step 5: Commit**

```bash
git add src/utils/homeGridLayout.ts src/utils/__tests__/homeGridLayout.test.ts
git commit -m "feat(home): 新增像素坐标转网格坐标的纯函数（镜像 grid-layout-plus 内部实现）"
```

---

### Task 5: 前端 — 区块面板组件 `HomeBlockPalette.vue`

**Files:**
- Create: `src/components/admin/HomeBlockPalette.vue`

**Interfaces:**
- Consumes: `CreatableWidgetType`（Task 2）。
- Produces: `HomeBlockPalette` 组件，无 props；emits `add-at-bottom(type: CreatableWidgetType)`（点击卡片）。拖拽卡片时把 `widgetType` 写入原生 `DragEvent.dataTransfer`（`text/plain`），供 Task 6 的 `HomeLayoutEditor.vue` drop 处理读取——这不是一个 Vue emit，是浏览器原生拖拽协议。

- [ ] **Step 1: 创建组件**

```vue
<template>
  <div class="home-block-palette">
    <div class="palette-title">添加区块</div>
    <div
      v-for="type in PALETTE_TYPES"
      :key="type"
      class="palette-card"
      draggable="true"
      @dragstart="handleDragStart($event, type)"
      @click="emit('add-at-bottom', type)"
    >
      <el-icon class="palette-icon">
        <component :is="PALETTE_ICONS[type]" />
      </el-icon>
      <span class="palette-label">{{ PALETTE_LABELS[type] }}</span>
    </div>
    <div class="palette-hint">拖到右侧网格可放到指定位置；点击默认加到底部</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 首页区块面板
 *
 * 展示现有 7 种可新增区块类型；点击卡片=加到网格底部（emit add-at-bottom），
 * 拖拽卡片到网格=由 HomeLayoutEditor 的 drop 处理接收并计算落点坐标。
 */
import type { Component } from 'vue'
import { Document, Files, EditPen, PieChart, List, Clock, Bell } from '@element-plus/icons-vue'
import type { CreatableWidgetType } from '@/types'

const emit = defineEmits<{
  'add-at-bottom': [type: CreatableWidgetType]
}>()

const PALETTE_TYPES: CreatableWidgetType[] = [
  'custom-markdown', 'data-card', 'quick-form', 'chart', 'todo', 'activity', 'announcement',
]

const PALETTE_LABELS: Record<CreatableWidgetType, string> = {
  'custom-markdown': 'Markdown 区块',
  'data-card': '数据卡片',
  'quick-form': '快速录入表单',
  chart: '图表区块',
  todo: '我的待办',
  activity: '最近动态',
  announcement: '公告',
}

const PALETTE_ICONS: Record<CreatableWidgetType, Component> = {
  'custom-markdown': Document,
  'data-card': Files,
  'quick-form': EditPen,
  chart: PieChart,
  todo: List,
  activity: Clock,
  announcement: Bell,
}

function handleDragStart(event: DragEvent, type: CreatableWidgetType) {
  if (!event.dataTransfer) return
  event.dataTransfer.setData('text/plain', type)
  event.dataTransfer.effectAllowed = 'copy'
}
</script>

<style scoped lang="scss">
.home-block-palette {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.palette-title {
  font-weight: 500;
  margin-bottom: 4px;
}

.palette-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-bg-color);
  cursor: grab;
  user-select: none;
  transition: box-shadow 0.2s;

  &:hover {
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  }

  &:active {
    cursor: grabbing;
  }
}

.palette-icon {
  color: var(--el-color-primary);
  flex-shrink: 0;
}

.palette-label {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.palette-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
  line-height: 1.4;
}
</style>
```

- [ ] **Step 2: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无错误（若 `@element-plus/icons-vue` 里 `Document`/`Files`/`EditPen`/`PieChart`/`List`/`Clock`/`Bell` 某个图标名不存在，这里会直接报模块导入错误——如果发生，换成该包里实际存在的同类图标即可，不影响其余实现）。

- [ ] **Step 3: Commit**

```bash
git add src/components/admin/HomeBlockPalette.vue
git commit -m "feat(home): 新增首页区块面板组件（拖拽/点击新增区块）"
```

---

### Task 6: 前端 — `HomeLayoutEditor.vue` 接收 drop 并计算落点坐标

**Files:**
- Modify: `src/components/admin/HomeLayoutEditor.vue`

**Interfaces:**
- Consumes: `pixelToGridPosition`（Task 4）。
- Produces: `HomeLayoutEditor` 新增 emit `create-at(payload: { widgetType: CreatableWidgetType; x: number; y: number; w: number; h: number })`。Task 7 的 `SystemSettings.vue` 依赖这个 emit 签名。

- [ ] **Step 1: 模板 — 网格容器加 drop 处理 + 模板引用**

把：

```html
<template>
  <div class="home-layout-editor">
    <GridLayout
```

改为：

```html
<template>
  <div
    ref="gridContainerRef"
    class="home-layout-editor"
    @dragover.prevent
    @drop="handleDrop"
  >
    <GridLayout
```

（`</div>` 收尾标签已存在于文件末尾，无需改动。）

- [ ] **Step 2: 脚本 — 新增 `create-at` emit、模板引用、drop 处理函数**

把 import 区：

```ts
import { computed, ref, watch } from 'vue'
import { GridLayout, GridItem } from 'grid-layout-plus'
import { Rank, Edit, Delete } from '@element-plus/icons-vue'
import { isCustomHomeWidget } from '@/utils/homeGridLayout'
import type { WidgetConfig, WidgetLayoutUpdateItem, WidgetType } from '@/types'
```

改为：

```ts
import { computed, ref, watch } from 'vue'
import { GridLayout, GridItem } from 'grid-layout-plus'
import { Rank, Edit, Delete } from '@element-plus/icons-vue'
import { isCustomHomeWidget, pixelToGridPosition } from '@/utils/homeGridLayout'
import type { WidgetConfig, WidgetLayoutUpdateItem, WidgetType, CreatableWidgetType } from '@/types'
```

把 `emit` 定义：

```ts
const emit = defineEmits<{
  edit: [widget: WidgetConfig]
  delete: [widget: WidgetConfig]
  toggle: [widget: WidgetConfig]
  'layout-change': [items: WidgetLayoutUpdateItem[]]
}>()
```

改为：

```ts
const emit = defineEmits<{
  edit: [widget: WidgetConfig]
  delete: [widget: WidgetConfig]
  toggle: [widget: WidgetConfig]
  'layout-change': [items: WidgetLayoutUpdateItem[]]
  'create-at': [payload: { widgetType: CreatableWidgetType; x: number; y: number; w: number; h: number }]
}>()
```

在 `handleLayoutUpdated` 函数（脚本里已有）之后新增：

```ts
// ==================== 区块面板拖拽落点 ====================

const gridContainerRef = ref<HTMLElement | null>(null)

/** 从区块面板拖入创建的默认尺寸——半行宽，方便落点两侧还能再放一个区块 */
const DROP_DEFAULT_W = 6
const DROP_DEFAULT_H = 4

function handleDrop(event: DragEvent) {
  const widgetType = event.dataTransfer?.getData('text/plain') as CreatableWidgetType | ''
  if (!widgetType) return

  const container = gridContainerRef.value
  if (!container) return

  const rect = container.getBoundingClientRect()
  const left = event.clientX - rect.left
  const top = event.clientY - rect.top
  const { x, y } = pixelToGridPosition(left, top, rect.width, DROP_DEFAULT_W)

  emit('create-at', { widgetType, x, y, w: DROP_DEFAULT_W, h: DROP_DEFAULT_H })
}
```

- [ ] **Step 3: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无新增错误。

- [ ] **Step 4: Commit**

```bash
git add src/components/admin/HomeLayoutEditor.vue
git commit -m "feat(home): HomeLayoutEditor 接收区块面板 drop 事件并换算网格落点坐标"
```

---

### Task 7: 前端 — `SystemSettings.vue` 接入区块面板

**Files:**
- Modify: `src/views/admin/SystemSettings.vue`

**Interfaces:**
- Consumes: `HomeBlockPalette`（Task 5）的 `add-at-bottom` emit；`HomeLayoutEditor`（Task 6）新增的 `create-at` emit；`systemConfigStore.createWidget`（Task 2）的可选 `layout` 参数。

- [ ] **Step 1: 模板 — 移除旧下拉菜单，改为两栏布局（面板 + 编辑器）**

把（第 86-120 行，「首页配置」Tab 开头到旧的 `widgets-toolbar` 结束）：

```html
      <!-- 馅页配置 Tab -->
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
                <el-dropdown-item command="custom-markdown">
                  Markdown 区块
                </el-dropdown-item>
                <el-dropdown-item command="data-card">
                  数据卡片
                </el-dropdown-item>
                <el-dropdown-item command="quick-form">
                  快速录入表单
                </el-dropdown-item>
                <el-dropdown-item command="chart" divided>
                  图表区块
                </el-dropdown-item>
                <el-dropdown-item command="todo">
                  我的待办
                </el-dropdown-item>
                <el-dropdown-item command="activity">
                  最近动态
                </el-dropdown-item>
                <el-dropdown-item command="announcement">
                  公告
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <div v-if="widgetsLoading" class="widgets-loading">
          <el-skeleton :rows="3" animated />
        </div>

        <div v-else-if="widgetsList.length === 0" class="widgets-empty">
          <el-empty description="暂无首页区块配置" />
        </div>

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
```

改为：

```html
      <!-- 首页配置 Tab -->
      <el-tab-pane label="首页配置" name="widgets">
        <div v-if="widgetsLoading" class="widgets-loading">
          <el-skeleton :rows="3" animated />
        </div>

        <div v-else-if="widgetsList.length === 0" class="widgets-empty">
          <el-empty description="暂无首页区块配置" />
        </div>

        <div v-else class="home-config-layout">
          <HomeBlockPalette @add-at-bottom="handleAddWidget" />

          <div class="home-config-main">
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
              @create-at="handleCreateAt"
            />
          </div>
        </div>

        <WidgetEditDialog
          v-model="editDialogVisible"
          :widget="currentWidget"
          @save="handleSaveWidget"
        />
      </el-tab-pane>
```

- [ ] **Step 2: 脚本 — 更新 import，移除本地重复类型，重构新增区块逻辑**

把 import 区：

```ts
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, ArrowDown, Check } from '@element-plus/icons-vue'
import { useSystemConfigStore } from '@/stores'
import WidgetEditDialog from './components/WidgetEditDialog.vue'
import HomeLayoutEditor from '@/components/admin/HomeLayoutEditor.vue'
import type { WidgetConfig, WidgetType, WidgetLayoutUpdateItem } from '@/types'
```

改为（移除不再使用的 `Plus`/`ArrowDown` 图标导入，新增 `HomeBlockPalette` 组件与 `WidgetLayout`/`CreatableWidgetType` 类型）：

```ts
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import { useSystemConfigStore } from '@/stores'
import WidgetEditDialog from './components/WidgetEditDialog.vue'
import HomeLayoutEditor from '@/components/admin/HomeLayoutEditor.vue'
import HomeBlockPalette from '@/components/admin/HomeBlockPalette.vue'
import type { WidgetConfig, WidgetType, WidgetLayout, WidgetLayoutUpdateItem, CreatableWidgetType } from '@/types'
```

把（既有 `WIDGET_DEFAULT_CONTENT` 常量 + 本地 `CreatableWidgetType` 类型别名 + `handleAddWidget` 函数）：

```ts
const WIDGET_DEFAULT_CONTENT: Record<string, Record<string, any>> = {
  'custom-markdown': { markdown: '' },
  'quick-form': { targetCollection: '', buttonLabel: '快速录入' },
  'data-card': { dataSource: { collection: '' }, displayType: 'count' },
  chart: { collection: '', chartType: 'bar', groupField: '', limit: 20 },
  todo: { limit: 5 },
  activity: { limit: 8 },
  announcement: { title: '公告', body: '', level: 'info', closable: false },
}

type CreatableWidgetType = 'custom-markdown' | 'data-card' | 'quick-form' | 'chart' | 'todo' | 'activity' | 'announcement'

async function handleAddWidget(type: CreatableWidgetType) {
  try {
    const defaultContent = WIDGET_DEFAULT_CONTENT[type] || {}

    await systemConfigStore.createWidget({
      widgetType: type,
      title: getDefaultTitle(type),
      content: defaultContent,
      visibleRoles: ['admin', 'developer', 'guest']
    })
    // 从 store 获取最新创建的 widget（store 已 push）
    const newWidget = systemConfigStore.widgets[systemConfigStore.widgets.length - 1]
    if (newWidget) {
      widgetsList.value.push(newWidget)
    }
    ElMessage.success('创建成功')
  } catch {
    ElMessage.error('创建失败')
  }
}
```

改为（`CreatableWidgetType` 改为直接用 `@/types` 里的版本，不再本地重复定义；`handleAddWidget`/`handleCreateAt` 共用同一个 `createWidgetOfType` 辅助函数）：

```ts
const WIDGET_DEFAULT_CONTENT: Record<string, Record<string, any>> = {
  'custom-markdown': { markdown: '' },
  'quick-form': { targetCollection: '', buttonLabel: '快速录入' },
  'data-card': { dataSource: { collection: '' }, displayType: 'count' },
  chart: { collection: '', chartType: 'bar', groupField: '', limit: 20 },
  todo: { limit: 5 },
  activity: { limit: 8 },
  announcement: { title: '公告', body: '', level: 'info', closable: false },
}

/** 创建区块的公共逻辑；不传 layout 时后端按"追加到底部"处理（点击面板卡片走这条） */
async function createWidgetOfType(type: CreatableWidgetType, layout?: WidgetLayout) {
  const defaultContent = WIDGET_DEFAULT_CONTENT[type] || {}

  await systemConfigStore.createWidget({
    widgetType: type,
    title: getDefaultTitle(type),
    content: defaultContent,
    visibleRoles: ['admin', 'developer', 'guest'],
    ...(layout ? { layout } : {})
  })
  // 从 store 获取最新创建的 widget（store 已 push）
  const newWidget = systemConfigStore.widgets[systemConfigStore.widgets.length - 1]
  if (newWidget) {
    widgetsList.value.push(newWidget)
  }
}

async function handleAddWidget(type: CreatableWidgetType) {
  try {
    await createWidgetOfType(type)
    ElMessage.success('创建成功')
  } catch {
    ElMessage.error('创建失败')
  }
}

async function handleCreateAt(payload: { widgetType: CreatableWidgetType; x: number; y: number; w: number; h: number }) {
  try {
    await createWidgetOfType(payload.widgetType, { x: payload.x, y: payload.y, w: payload.w, h: payload.h })
    ElMessage.success('创建成功')
  } catch {
    ElMessage.error('创建失败')
  }
}
```

- [ ] **Step 3: 样式 — 新增两栏布局样式**

在 `<style>` 区（`.widgets-toolbar { margin-bottom: 16px; }` 那一块）之后新增：

```scss
.home-config-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.home-config-main {
  flex: 1;
  min-width: 0;
}
```

`.widgets-toolbar` 样式块（现有的 `margin-bottom: 16px`）不再被模板引用（旧的下拉菜单外层 div 已删除），可以保留（不影响任何东西，无需专门清理）也可以删除；本步骤不强制要求删除，实施时任选，只要模板/脚本改动落地即可。

- [ ] **Step 4: 类型检查 + 全量单测**

```bash
npx vue-tsc --noEmit
npm run test
```

Expected: 类型检查无错误；`npm run test` 全绿。

- [ ] **Step 5: Commit**

```bash
git add src/views/admin/SystemSettings.vue
git commit -m "feat(home): 首页配置接入区块面板，替换旧的新增区块下拉菜单"
```

---

### Task 8: 文档同步 + Playwright 端到端验证

**Files:**
- Modify: `docs/user-guide/admin/system-settings.md`
- Create: `docs/user-guide/_images/home-block-palette.png`（Playwright 截图）

- [ ] **Step 1: 启动前后端服务**

```bash
npm run dev:all
```

- [ ] **Step 2: Playwright 驱动「拖拽创建」流程**

1. 登录 `admin`/`admin123`，进入「设置中心 → 通用设置 → 首页配置」。
2. `browser_snapshot` 确认左侧出现区块面板（7 张卡片），右侧是网格编辑器（不再有旧的"新增区块"下拉按钮）。
3. 把面板里的某张卡片（如"图表区块"）拖到网格中某个空白/半空位置（不是网格最底部）。
4. `browser_snapshot`/`browser_network_requests` 确认发出 `POST /api/home-widgets`，请求体带 `layout` 字段且坐标与拖放位置吻合（非默认的 `{x:0,y:maxBottom,w:12,h:4}`）。
5. 确认新创建的区块卡片出现在网格里拖放的位置附近，而不是网格最底部。
6. `browser_take_screenshot`，保存到 `.playwright-mcp/home-block-palette-drag-create.png`。

- [ ] **Step 3: Playwright 验证「点击创建」回归不受影响**

1. 点击面板上另一张卡片（如"公告"）。
2. 确认 `POST /api/home-widgets` 请求体**不带** `layout` 字段。
3. 确认新区块出现在网格最底部（与拖拽创建的位置明显不同）。

- [ ] **Step 4: 更新用户文档**

在 `docs/user-guide/admin/system-settings.md` 的「首页配置 · 区块类型与布局」章节（`## 首页配置 · 区块类型与布局` 标题之下）新增一小节：

```markdown
### 新增区块（拖拽 / 点击）

「首页配置」左侧是一个常驻的区块面板，展示所有可新增的区块类型：

- **拖拽**卡片到右侧网格的任意位置，区块会直接创建在松手的位置（默认半行宽，方便旁边再放一个）。
- **点击**卡片则按老规矩追加到网格最底部，不想精确定位时更快捷。

两种方式创建后都可以用已有的拖拽/缩放功能继续调整。

![首页区块面板拖拽新增](../_images/home-block-palette.png)
```

把 Step 2 的截图拷贝为 `docs/user-guide/_images/home-block-palette.png`。

- [ ] **Step 5: Commit**

```bash
git add docs/user-guide/admin/system-settings.md docs/user-guide/_images/home-block-palette.png
git commit -m "docs(home): 首页配置文档补充区块面板拖拽新增说明与截图"
```

---

## Self-Review Notes

- **Spec coverage**：面板组件与交互（点击/拖拽两种创建方式）→ Task 5/6/7；后端 `layout` 覆盖 + 校验 + `order` 重算 → Task 1；像素→网格坐标换算（已用真实 `grid-layout-plus` 源码验证公式，非猜测）→ Task 4；测试（后端 pytest、前端 vitest、Playwright）→ Task 1/3/4/8；文档同步 → Task 8；范围之外的三项（新增区块类型、拖拽创建时自动避让、面板可折叠）本计划未涉及，与 spec 一致。
- **Placeholder scan**：已核对，无 TBD/TODO；`grid-layout-plus` 外部拖入支持的技术选型在写计划前已直接查证（无该 API），计划里的实现是确定方案，不是占位调研任务。
- **Type consistency**：`CreatableWidgetType`（Task 2 定义于 `@/types`）→ `HomeBlockPalette.vue`（Task 5 消费，emit `add-at-bottom`）→ `HomeLayoutEditor.vue`（Task 6 消费，emit `create-at` 用同一个类型）→ `SystemSettings.vue`（Task 7 消费两个 emit，调用 `createWidgetOfType`）全程一致；`pixelToGridPosition`（Task 4 定义）签名与 Task 6 的调用方式一致；后端 `layout: {x,y,w,h}` 字段名与前端 `WidgetLayout` 字段名一致，与已上线的 `PUT /home-widgets/layout` 端点保持同一套字段命名/校验规则。
