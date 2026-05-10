# 系统设置与首页定制设计文档

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现系统名称全局定制和首页内容完整定制功能，支持管理员配置首页区块的显示、排序、内容编辑。

**Architecture:** 采用双表分离设计，`system_config` 表存储全局配置（系统名称），`home_widgets` 表存储首页区块配置。前端新增系统设置管理页面，首页改为动态渲染模式。

**Tech Stack:** PostgreSQL JSONB, Vue 3 + TypeScript, Element Plus, Pinia

---

## 1. 数据库设计

### 1.1 system_config 表

存储全局系统配置：

```sql
CREATE TABLE system_config (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    system_name     VARCHAR(200) NOT NULL DEFAULT '巡检用例管理系统',
    system_short_name VARCHAR(50) NOT NULL DEFAULT '巡检管理',
    logo_url        VARCHAR(500),          -- 可选自定义Logo URL
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_by      VARCHAR(100)           -- 操作人用户名
);

INSERT INTO system_config (id) VALUES (1) ON CONFLICT DO NOTHING;
```

### 1.2 home_widgets 表

存储首页区块配置：

```sql
CREATE TABLE home_widgets (
    id              VARCHAR(100) PRIMARY KEY,   -- 区块唯一标识
    widget_type     VARCHAR(50) NOT NULL,       -- 区块类型
    title           VARCHAR(200),               -- 区块标题（可选）
    content         JSONB,                      -- 区块内容配置
    enabled         BOOLEAN DEFAULT TRUE,       -- 是否启用
    order           INTEGER DEFAULT 0,          -- 显示顺序
    visible_roles   JSONB DEFAULT '["admin","developer","guest"]', -- 可见角色列表
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### 1.3 widget_type 类型说明

| 类型 | 说明 | content 结构 |
|------|------|-------------|
| `welcome` | 欢迎卡片 | `{ heading, description }` |
| `stats` | 统计卡片 | `{ items: [{ type, label, icon }] }` |
| `quick-links` | 快捷入口 | `{ links: [{ name, path, icon }] }` |
| `system-info` | 系统说明 | `{ markdown: "..." }` |
| `custom-markdown` | 自定义Markdown | `{ markdown: "..." }` |
| `data-card` | 数据卡片 | 见 4.1 详细设计 |

### 1.4 默认数据初始化

```sql
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
 true, 4);
```

---

## 2. API 设计

### 2.1 系统配置 API

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/system-config` | 获取系统配置 | 所有角色 |
| PUT | `/system-config` | 更新系统配置 | 仅管理员 |

**GET `/system-config` 响应：**
```json
{
  "systemName": "巡检用例管理系统",
  "systemShortName": "巡检管理",
  "logoUrl": null
}
```

**PUT `/system-config` 请求体：**
```json
{
  "systemName": "新系统名称",
  "systemShortName": "新简称",
  "logoUrl": "/uploads/logo.png"
}
```

### 2.2 首页区块 API

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/home-widgets` | 获取首页区块列表 | 所有角色 |
| PUT | `/home-widgets` | 批量更新区块配置 | 仅管理员 |
| POST | `/home-widgets` | 新增自定义区块 | 仅管理员 |
| DELETE | `/home-widgets/:id` | 删除自定义区块 | 仅管理员 |
| PUT | `/home-widgets/order` | 更新区块排序 | 仅管理员 |

**GET `/home-widgets` 响应：**
```json
[
  {
    "id": "welcome",
    "widgetType": "welcome",
    "title": "欢迎",
    "content": {
      "heading": "欢迎使用巡检用例管理系统",
      "description": "本系统支持动态配置..."
    },
    "enabled": true,
    "order": 1,
    "visibleRoles": ["admin", "developer", "guest"]
  }
]
```

**PUT `/home-widgets/order` 请求体：**
```json
{
  "orders": [
    { "id": "welcome", "order": 1 },
    { "id": "stats", "order": 2 }
  ]
}
```

---

## 3. 前端组件设计

### 3.1 管理页面

`src/views/admin/SystemSettings.vue` - 系统设置管理页面

**布局：** 两个 Tab

1. **基本设置** Tab
   - 系统名称输入框（完整名称，用于首页标题、浏览器标题）
   - 系统简称输入框（用于侧边栏Logo）
   - Logo 上传组件（可选，支持图片上传）

2. **首页配置** Tab
   - 区块列表（使用 vuedraggable 支持拖拽排序）
   - 每个区块行：启用开关、标题、类型标签、编辑按钮、删除按钮
   - 新增区块按钮（下拉选择类型：custom-markdown / data-card）
   - 编辑对话框（根据类型显示不同表单）

### 3.2 首页改造

`src/views/home/HomeView.vue` 改为动态渲染：

- 从 API 获取 `home_widgets` 配置
- 根据用户角色过滤 `visibleRoles` 包含当前角色的区块
- 按 `order` 排序后动态渲染各区块组件
- 使用 `<component :is="...">` 动态组件模式

### 3.3 区块组件

| 组件路径 | 说明 |
|----------|------|
| `src/components/home/WelcomeWidget.vue` | 欢迎卡片，显示 heading 和 description |
| `src/components/home/StatsWidget.vue` | 统计卡片，根据 content.items 渲染统计项 |
| `src/components/home/QuickLinksWidget.vue` | 快捷入口，根据 content.links 渲染链接 |
| `src/components/home/SystemInfoWidget.vue` | 系统说明，渲染 Markdown 内容 |
| `src/components/home/MarkdownWidget.vue` | 自定义 Markdown 区块 |
| `src/components/home/DataCardWidget.vue` | 数据卡片，根据数据源配置渲染 |

### 3.4 系统名称应用位置

| 位置 | 使用的字段 |
|------|-----------|
| 侧边栏 Logo 文字 | `systemShortName` |
| 首页欢迎卡片标题 | `systemName`（或 welcome 区块的 content.heading） |
| 浏览器页面标题 | `systemName` |
| 登录页面标题 | `systemName` |

实现方式：
- 创建 `useSystemConfig` composable 或 store，在应用初始化时获取配置
- 在各组件中引用配置值
- 使用 Vue watch 监听配置变化，动态更新 document.title

---

## 4. 数据卡片区块详细设计

### 4.1 数据卡片配置结构

`data-card` 类型区块的 `content` 字段：

```json
{
  "dataSource": {
    "collection": "inspection-case",
    "branchId": "main",
    "filter": { "status": "pending" },
    "limit": 5
  },
  "displayType": "list",
  "columns": ["taskName", "status", "startDate"],
  "titleField": "taskName",
  "linkToDetail": true
}
```

### 4.2 displayType 说明

| 类型 | 渲染效果 | 使用场景 |
|------|----------|----------|
| `count` | 显示统计数字（如"待处理：12条"） | 快速概览 |
| `list` | 卡片列表，每条记录一个卡片 | 少量重点数据 |
| `table` | 简化表格，显示多列数据 | 需要看多字段 |

### 4.3 统计类型（stats 区块）

`stats` 区块的 `content.items` 支持的统计类型：

| type | 说明 | 计算方式 |
|------|------|----------|
| `menuCount` | 菜单数量 | menuStore.menuList.length |
| `pageCount` | 页面配置数量 | pageConfigStore.pageConfigs.length |
| `fieldCount` | 字段配置总数 | 累加所有 pageConfig.fields.length |
| `recordCount` | 某集合记录数 | 需指定 collection，调用 API 统计 |

扩展统计类型配置：
```json
{
  "type": "recordCount",
  "label": "待处理任务",
  "icon": "Clock",
  "collection": "task-calendar",
  "filter": { "status": "pending" }
}
```

---

## 5. 权限设计

- **读取配置**：所有角色均可读取系统配置和首页区块
- **修改配置**：仅 `admin` 角色可以修改
- **区块可见性**：通过 `visibleRoles` 字段控制不同角色看到的不同区块

---

## 6. 路由与菜单

新增路由和菜单项：

```typescript
// router/index.ts
{ path: '/admin/system-settings', component: SystemSettings, meta: { title: '系统设置' } }
```

在 menus 表中添加管理菜单项（parent_id 指向管理菜单组）。

---

## 7. 文件结构

### 7.1 新增文件

**后端：**
- `server/routes/system_config.py` - 系统配置 API
- `server/routes/home_widgets.py` - 首页区块 API

**前端：**
- `src/views/admin/SystemSettings.vue` - 系统设置管理页面
- `src/components/home/WelcomeWidget.vue`
- `src/components/home/StatsWidget.vue`
- `src/components/home/QuickLinksWidget.vue`
- `src/components/home/SystemInfoWidget.vue`
- `src/components/home/MarkdownWidget.vue`
- `src/components/home/DataCardWidget.vue`
- `src/components/home/index.ts` - 组件导出
- `src/stores/systemConfig.ts` - 系统配置 store
- `src/api/systemConfig.ts` - API 调用
- `src/types/systemConfig.ts` - 类型定义

### 7.2 修改文件

- `server/init_db.py` - 新增表初始化
- `server/app.py` - 注册新蓝图
- `src/views/home/HomeView.vue` - 改为动态渲染
- `src/components/layout/SideMenu.vue` - 使用系统简称
- `src/views/login/LoginView.vue` - 使用系统名称
- `src/router/index.ts` - 新增路由
- `src/components/common/index.ts` - 导出区块组件

---

## 8. 测试要点

- 系统名称修改后，侧边栏、首页、浏览器标题同步更新
- 首页区块拖拽排序后保存，刷新页面顺序正确
- 区块启用/禁用切换后，首页显示正确
- 不同角色用户看到不同区块（visibleRoles 过滤）
- 数据卡片正确获取数据并渲染
- 管理员可新增/删除自定义区块
- 非管理员访问修改 API 返回 403