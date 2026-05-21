# 数据页自定义列视图功能设计

## 概述

当数据页字段较多（50+）时，不同用户关注的字段不同。本功能支持自定义视图，每个视图可配置显示的字段、顺序、宽度、排序、筛选和分组。用户可在数据页上自由切换视图。

## 需求总结

| 需求项 | 决定 |
|--------|------|
| 共享范围 | 混合模式：公共视图 + 用户私人视图 |
| 创建权限 | developer/admin 可创建私人视图，guest 只能使用公共视图；公共视图仅 admin 可创建 |
| 视图选择器位置 | 独立下拉 + 管理按钮（位于视图切换区右侧） |
| 视图配置内容 | 列显示/隐藏、顺序、宽度、默认排序、默认筛选条件、分组显示 |
| 列配置界面 | 可拖拽列表 + 勾选 + 宽度输入 |
| 默认视图 | 页面公共默认视图（管理员设置），所有用户打开时显示 |
| 管理界面 | 左侧视图列表 + 右侧编辑面板 |

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                     DynamicPage.vue                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 视图切换区                                            │   │
│  │ [表格] [Excel] [看板] │ [下拉: 选择视图] [管理视图]   │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ DataTable.vue                                         │   │
│  │ 根据 columnConfig 渲染表格列                          │   │
│  │ 应用 sortConfig、filterConfig、groupConfig            │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              ViewManageDialog.vue (管理弹窗)                 │
│  ┌────────────────────┐  ┌────────────────────────────┐    │
│  │ ViewList.vue       │  │ ViewEditPanel.vue          │    │
│  │ - 视图列表         │  │ - 名称、类型、默认设置      │    │
│  │ - 新建/删除/复制   │  │ - [编辑列配置] 按钮         │    │
│  │ - 点击选中编辑     │  │                            │    │
│  └────────────────────┘  └────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              ColumnConfigDialog.vue (列配置弹窗)             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 可拖拽字段列表                                        │   │
│  │ [⋮⋮] [✓] 订单编号      [宽度: 120px]                 │   │
│  │ [⋮⋮] [✓] 客户名称      [宽度: 150px]                 │   │
│  │ [⋮⋮] [ ] 创建时间      [禁用]                        │   │
│  │ ...                                                   │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 排序/筛选/分组配置区                                  │   │
│  │ 默认排序: [状态 ▼] [升序 ▼]                          │   │
│  │ 默认筛选: [状态] [=] [进行中]                         │   │
│  │ 分组字段: [状态 ▼]                                   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**核心组件**：
- `DynamicPage.vue` - 加载视图配置，传递给 DataTable
- `ViewManageDialog.vue` - 视图管理弹窗（列表 + 编辑面板）
- `ColumnConfigDialog.vue` - 列配置弹窗（拖拽列表 + 排序/筛选/分组）
- `DataTable.vue` - 接收 columnConfig，渲染表格

## 数据库设计

### `column_views` 表

```sql
CREATE TABLE column_views (
  id SERIAL PRIMARY KEY,
  page_id VARCHAR(100) NOT NULL REFERENCES page_configs(id) ON DELETE CASCADE,
  name VARCHAR(100) NOT NULL,
  is_public BOOLEAN DEFAULT false,
  creator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  is_default BOOLEAN DEFAULT false,

  -- 列配置
  columns JSONB NOT NULL DEFAULT '[]',
  -- 格式: [{"fieldId": "f1", "visible": true, "order": 0, "width": "120px"}, ...]

  -- 默认排序配置
  sort_config JSONB DEFAULT '[]',
  -- 格式: [{"field": "createdAt", "direction": "desc"}]

  -- 默认筛选配置
  filter_config JSONB DEFAULT '[]',
  -- 格式: [{"field": "status", "operator": "=", "value": "进行中"}]

  -- 分组配置
  group_config JSONB DEFAULT NULL,
  -- 格式: {"field": "status", "order": ["进行中", "已完成", "已取消"]}

  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 索引优化查询
CREATE INDEX idx_column_views_page ON column_views(page_id);
CREATE INDEX idx_column_views_creator ON column_views(creator_id);
CREATE INDEX idx_column_views_public ON column_views(is_public) WHERE is_public = true;
```

### 数据关系说明

| 字段 | 说明 |
|------|------|
| `page_id` | 关联数据页，一个数据页可以有多个视图 |
| `is_public` | `true` = 公共视图，`false` = 私人视图 |
| `creator_id` | 私人视图的创建者，公共视图可为 null（系统创建） |
| `is_default` | 每个数据页最多一个公共默认视图 |
| `columns` | 字段 ID 来自 `page_configs.fields`，存储显示状态、顺序、宽度 |

### 约束逻辑

- 同一 `page_id` 下，公共视图名称唯一
- 同一 `page_id` 下，同一用户的私人视图名称唯一
- 同一 `page_id` 下，最多一个 `is_default=true` 的公共视图

## 后端 API 设计

### 新增路由文件: `server/routes/column_views.py`

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/<page_id>/views` | 获取页面所有视图（公共 + 当前用户私人） | login_required |
| POST | `/<page_id>/views` | 创建新视图 | developer/admin |
| PUT | `/<page_id>/views/<view_id>` | 更新视图 | creator 或 admin |
| DELETE | `/<page_id>/views/<view_id>` | 删除视图 | creator 或 admin |
| PUT | `/<page_id>/views/<view_id>/default` | 设置为默认视图 | admin |
| POST | `/<page_id>/views/<view_id>/copy` | 复制视图 | developer/admin |

### API 请求/响应示例

**GET /api/page-products/views**
```json
{
  "views": [
    {
      "id": 1,
      "name": "默认视图",
      "isPublic": true,
      "isDefault": true,
      "columns": [
        {"fieldId": "f1", "visible": true, "order": 0, "width": "120px"},
        {"fieldId": "f2", "visible": true, "order": 1, "width": "auto"}
      ],
      "sortConfig": [{"field": "createdAt", "direction": "desc"}],
      "filterConfig": [],
      "groupConfig": null,
      "creatorId": null,
      "createdAt": "2026-05-21T10:00:00Z"
    },
    {
      "id": 2,
      "name": "我的视角",
      "isPublic": false,
      "isDefault": false,
      "columns": [...],
      "sortConfig": [...],
      "creatorId": 5,
      ...
    }
  ],
  "defaultViewId": 1
}
```

**POST /api/page-products/views**
```json
// Request
{
  "name": "财务视角",
  "isPublic": true,
  "columns": [...],
  "sortConfig": [...],
  "filterConfig": [...],
  "groupConfig": null
}

// Response
{
  "id": 3,
  "name": "财务视角",
  ...
}
```

### 权限控制逻辑

```python
# 获取视图列表：返回公共视图 + 当前用户的私人视图
def get_views(page_id):
    public_views = query("SELECT * FROM column_views WHERE page_id=%s AND is_public=true")
    user_views = query("SELECT * FROM column_views WHERE page_id=%s AND creator_id=%s",
                       page_id, current_user.id)
    return merge_and_sort(public_views, user_views)

# 创建视图：developer/admin 才能创建
@write_required
def create_view(page_id):
    if current_user.role == 'guest':
        abort(403)
    # 公共视图只能 admin 创建
    if request.json.get('isPublic') and current_user.role != 'admin':
        abort(403)

# 更新/删除视图：创建者或 admin
def update_view(page_id, view_id):
    view = get_view(view_id)
    if view.creator_id != current_user.id and current_user.role != 'admin':
        abort(403)

# 设置默认视图：只有 admin
@admin_required
def set_default_view(page_id, view_id):
    # 先清除该页面的其他默认标记
    update("UPDATE column_views SET is_default=false WHERE page_id=%s", page_id)
    # 再设置新的默认
    update("UPDATE column_views SET is_default=true WHERE id=%s", view_id)
```

## 前端组件设计

### 新增文件结构

```
src/
├── types/
│   └── columnView.ts              # 视图类型定义
├── api/
│   └── columnViews.ts             # 视图 API 封装
├── stores/
│   └── columnView.ts              # 视图 Store（Pinia）
├── components/
│   ├── common/
│   │   └── DataTable.vue         # 修改：接收 columnConfig 参数
│   └── column-view/
│       ├── ViewSelector.vue      # 视图选择下拉 + 管理按钮
│       ├── ViewManageDialog.vue  # 视图管理弹窗
│       ├── ViewEditPanel.vue     # 编辑面板（右侧）
│       └── ColumnConfigDialog.vue # 列配置弹窗（拖拽列表）
```

### 类型定义 (`src/types/columnView.ts`)

```typescript
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
  fieldId: string      // 对应 FieldConfig.id
  visible: boolean
  order: number
  width: string        // '120px' | 'auto' | '200'
}

export interface SortConfigItem {
  field: string        // fieldName
  direction: 'asc' | 'desc'
}

export interface FilterConfigItem {
  field: string        // fieldName
  operator: '=' | '!=' | 'contains' | '>' | '<'
  value: any
}

export interface GroupConfig {
  field: string        // fieldName
  order?: string[]     // 分组值的显示顺序
}
```

### Store 设计 (`src/stores/columnView.ts`)

```typescript
export const useColumnViewStore = defineStore('columnView', () => {
  // 状态
  const views = ref<ColumnView[]>([])
  const currentViewId = ref<number | null>(null)
  const defaultViewId = ref<number | null>(null)

  // 计算属性
  const currentView = computed(() =>
    views.value.find(v => v.id === currentViewId.value)
  )

  const publicViews = computed(() =>
    views.value.filter(v => v.isPublic)
  )

  const myViews = computed(() =>
    views.value.filter(v => !v.isPublic)
  )

  // 方法
  async function loadViews(pageId: string) {
    const res = await getColumnViews(pageId)
    views.value = res.views
    defaultViewId.value = res.defaultViewId
    // 优先使用上次选择的视图，无则用默认
    const lastViewId = localStorage.getItem(`view:${pageId}`)
    currentViewId.value = lastViewId ? Number(lastViewId) : defaultViewId.value
  }

  async function createView(pageId: string, data: Partial<ColumnView>) {
    const newView = await createColumnView(pageId, data)
    views.value.push(newView)
    return newView
  }

  async function updateView(viewId: number, data: Partial<ColumnView>) {
    const updated = await updateColumnView(viewId, data)
    const index = views.value.findIndex(v => v.id === viewId)
    if (index !== -1) views.value[index] = updated
  }

  async function deleteView(viewId: number) {
    await deleteColumnView(viewId)
    views.value = views.value.filter(v => v.id !== viewId)
    if (currentViewId.value === viewId) {
      currentViewId.value = defaultViewId.value
    }
  }

  function selectView(viewId: number) {
    currentViewId.value = viewId
    localStorage.setItem(`view:${pageId}`, String(viewId))
  }

  // 生成用于 DataTable 的列配置
  function getTableColumns(allFields: FieldConfig[]): FieldConfig[] {
    if (!currentView.value) return allFields.filter(f => !f.hidden)

    const columnMap = new Map(currentView.value.columns.map(c => [c.fieldId, c]))

    return allFields
      .map(field => {
        const colConfig = columnMap.get(field.id)
        if (!colConfig) return null
        if (!colConfig.visible) return null

        return {
          ...field,
          order: colConfig.order,
          width: colConfig.width !== 'auto' ? colConfig.width : field.width
        }
      })
      .filter(Boolean)
      .sort((a, b) => a!.order - b!.order)
  }

  return {
    views, currentViewId, currentView, publicViews, myViews,
    loadViews, createView, updateView, deleteView, selectView, getTableColumns
  }
})
```

### DataTable.vue 修改

```vue
<!-- 接收 columnConfig 参数，替代原有的 visibleFields 计算 -->
<template>
  <el-table
    :data="tableData"
    :default-sort="currentView?.sortConfig?.[0]"
  >
    <el-table-column
      v-for="col in displayColumns"
      :key="col.fieldName"
      :prop="col.fieldName"
      :width="col.width"
      :sortable="col.controlType !== 'relation'"
    >
      ...
    </el-table-column>
  </el-table>
</template>

<script setup>
const props = defineProps<{
  fields: FieldConfig[]
  columnConfig?: ColumnConfigItem[]  // 新增：视图列配置
  sortConfig?: SortConfigItem[]      // 新增：默认排序
  filterConfig?: FilterConfigItem[]  // 新增：默认筛选
}>()

const displayColumns = computed(() => {
  if (!props.columnConfig) {
    // 无视图配置时，使用默认逻辑
    return props.fields.filter(f => !f.hidden).sort((a, b) => a.order - b.order)
  }

  // 有视图配置时，按配置筛选和排序
  const configMap = new Map(props.columnConfig.map(c => [c.fieldId, c]))
  return props.fields
    .filter(f => configMap.get(f.id)?.visible)
    .map(f => ({ ...f, width: configMap.get(f.id)?.width || f.width }))
    .sort((a, b) => configMap.get(a.id)?.order - configMap.get(b.id)?.order)
})
</script>
```

## 错误处理与边界情况

### 后端验证

| 场景 | 处理方式 |
|------|----------|
| 创建公共视图但非 admin | 返回 403，提示"只有管理员可创建公共视图" |
| guest 创建私人视图 | 返回 403，提示"guest 用户无法创建视图" |
| 视图名称重复（同页面同类型） | 返回 400，提示"视图名称已存在" |
| 更新/删除他人私人视图 | 返回 403，提示"只能修改自己的私人视图" |
| 删除默认视图 | 返回 400，提示"请先取消默认设置或指定其他默认视图" |
| page_id 不存在 | 返回 404 |
| columns 中 fieldId 不存在 | 返回 400，过滤无效字段或提示"字段不存在" |

### 前端边界情况

| 场景 | 处理方式 |
|------|----------|
| 无视图配置时打开数据页 | 使用 page_configs.fields 默认显示（hidden=false） |
| 所有视图被删除 | 自动创建"默认视图"，包含所有可见字段 |
| 当前选择的视图被他人删除 | 自动切换到默认视图，提示"视图已被删除" |
| 字段被管理员删除后视图失效 | 过滤无效 fieldId，保留有效配置 |
| 网络请求失败 | 显示错误提示，保持当前状态不变 |
| 拖拽排序时快速点击保存 | 防抖处理，避免频繁请求 |

### 数据同步

```typescript
// 字段变更时检查视图配置有效性
async function validateViewColumns(pageId: string, newFields: FieldConfig[]) {
  const views = await getViews(pageId)
  const validFieldIds = new Set(newFields.map(f => f.id))

  for (const view of views) {
    const invalidColumns = view.columns.filter(c => !validFieldIds.has(c.fieldId))
    if (invalidColumns.length > 0) {
      // 自动清理无效配置
      view.columns = view.columns.filter(c => validFieldIds.has(c.fieldId))
      await updateView(view.id, { columns: view.columns })
    }
  }
}
```

## 测试策略

### 后端测试 (`server/tests/test_column_views.py`)

| 测试场景 | 测试内容 |
|----------|----------|
| 获取视图列表 | 公共视图 + 用户私人视图正确返回 |
| 创建公共视图 | admin 成功，developer/guest 返回 403 |
| 创建私人视图 | developer/admin 成功，guest 返回 403 |
| 更新视图 | 创建者成功，非创建者返回 403 |
| 删除视图 | 创建者成功，非创建者返回 403 |
| 设置默认视图 | admin 成功，其他返回 403 |
| 复制视图 | 正确复制所有配置，生成新 ID |
| 名称重复 | 同页面同类型返回 400 |
| 删除默认视图 | 返回 400 |
| 关联字段不存在 | 过滤无效 fieldId 或返回 400 |

### 前端测试 (`src/components/column-view/__tests__/`)

| 测试场景 | 测试内容 |
|----------|----------|
| ViewSelector.vue | 下拉正确显示公共/私人视图，切换触发 selectView |
| ViewManageDialog.vue | 列表显示正确，新建/删除/复制按钮触发对应 action |
| ColumnConfigDialog.vue | 拖拽改变顺序，勾选改变 visible，宽度输入更新 |
| Store (columnView.ts) | loadViews/createView/updateView/deleteView 正确更新状态 |
| DataTable 接收视图配置 | displayColumns 按配置正确筛选和排序 |
| 无视图配置时 | 使用默认字段显示 |
| 网络失败 | 显示错误提示，状态不变 |

### 测试优先级

1. **高优先级**：创建/更新/删除视图的权限控制，Store 的核心方法
2. **中优先级**：列配置拖拽排序，DataTable 正确渲染
3. **低优先级**：复制视图功能，名称重复校验