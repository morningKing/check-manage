# 版本删除 API 文档

## 概述

本文档描述了版本删除功能的两阶段删除流程及相关 API 端点。该功能用于安全删除版本（快照或分支）及其关联数据。

## 两阶段删除流程

版本删除采用两阶段确认机制，确保用户清楚了解删除操作的影响范围，避免误删数据。

### 流程说明

1. **第一阶段：获取影响报告**
   - 前端调用 `GET /api/versions/<id>/delete-impact` 获取删除影响范围
   - 后端返回版本信息、涉及的 Collection、数据条数、关联关系数量等
   - 前端展示确认对话框，提示用户将删除的数据

2. **第二阶段：确认删除**
   - 用户确认后，前端调用 `DELETE /api/versions/<id>?confirmed=true`
   - 后端执行删除操作，删除版本元数据、动态数据、关联关系等
   - 返回删除成功消息

3. **可选：分页查询详情**
   - 当某个 Collection 数据量较大时，前端可调用 `GET /api/versions/<id>/delete-detail` 分页查询详情
   - 支持分页、排序，便于用户查看具体将删除哪些记录

---

## API 端点

### 1. 获取删除影响报告

**端点**：`GET /api/versions/<version_id>/delete-impact`

**描述**：获取删除指定版本的影响范围报告，用于前端展示确认对话框。

**认证**：需要登录（`login_required`）

**权限**：所有已登录用户

**请求参数**：
- `version_id` (路径参数): 版本 ID

**响应示例**：

```json
{
  "success": true,
  "data": {
    "versionInfo": {
      "id": "ver-001",
      "name": "测试版本",
      "collection": "inspection-case",
      "versionType": "branch",
      "recordsCount": 5,
      "relationsCount": 3
    },
    "affectedCollections": [
      {
        "collection": "inspection-case",
        "recordCount": 5,
        "records": [
          {
            "id": "case-001",
            "displayName": "巡检用例A",
            "createdAt": "2026-01-05T10:30:00",
            "updatedAt": "2026-01-06T14:20:00"
          },
          {
            "id": "case-002",
            "displayName": "巡检用例B",
            "createdAt": "2026-01-05T11:15:00",
            "updatedAt": "2026-01-05T11:15:00"
          }
        ],
        "hasMore": false
      }
    ],
    "totalRecords": 5,
    "totalRelations": 3,
    "hasCrossCollectionData": false,
    "warningMessage": "将删除 inspection-case 的 5 条数据"
  }
}
```

**跨 Collection 版本响应示例**：

```json
{
  "success": true,
  "data": {
    "versionInfo": {
      "id": "ver-002",
      "name": "跨Collection版本",
      "collection": "inspection-plan",
      "versionType": "branch",
      "recordsCount": 10,
      "relationsCount": 5
    },
    "affectedCollections": [
      {
        "collection": "inspection-plan",
        "recordCount": 2,
        "records": [...],
        "hasMore": false
      },
      {
        "collection": "inspection-case",
        "recordCount": 8,
        "records": [...],
        "hasMore": false
      }
    ],
    "totalRecords": 10,
    "totalRelations": 5,
    "hasCrossCollectionData": true,
    "warningMessage": "该版本涉及 2 个 Collection 的数据：\ninspection-plan(2条), inspection-case(8条)\n删除将同时清理这些数据及 5 条关联关系。"
  }
}
```

**响应字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `versionInfo` | Object | 版本基本信息 |
| `versionInfo.id` | String | 版本 ID |
| `versionInfo.name` | String | 版本名称 |
| `versionInfo.collection` | String | 版本所属 Collection |
| `versionInfo.versionType` | String | 版本类型（`snapshot` 或 `branch`） |
| `versionInfo.recordsCount` | Number | 版本记录的记录总数 |
| `versionInfo.relationsCount` | Number | 版本记录的关联关系总数 |
| `affectedCollections` | Array | 受影响的 Collection 列表 |
| `affectedCollections[].collection` | String | Collection 名称 |
| `affectedCollections[].recordCount` | Number | 该 Collection 的数据条数 |
| `affectedCollections[].records` | Array | 前 100 条记录详情（用于预览） |
| `affectedCollections[].records[].id` | String | 记录 ID |
| `affectedCollections[].records[].displayName` | String | 显示名称（优先级：name > title > caseName > planName > id） |
| `affectedCollections[].records[].createdAt` | String | 创建时间（ISO 8601 格式） |
| `affectedCollections[].records[].updatedAt` | String | 更新时间（ISO 8601 格式） |
| `affectedCollections[].hasMore` | Boolean | 是否有更多数据（totalCount > 100） |
| `totalRecords` | Number | 所有 Collection 的记录总数 |
| `totalRelations` | Number | 关联关系总数 |
| `hasCrossCollectionData` | Boolean | 是否涉及跨 Collection 数据 |
| `warningMessage` | String | 警告信息（用于前端展示） |

**错误响应**：

```json
{
  "success": false,
  "error": "版本不存在"
}
```

**状态码**：
- `200`: 成功
- `404`: 版本不存在
- `500`: 服务器错误

---

### 2. 确认删除版本

**端点**：`DELETE /api/versions/<version_id>`

**描述**：删除指定版本。支持两阶段确认机制。

**认证**：需要登录且写权限（`write_required`）

**权限**：admin 或 developer 角色（guest 角色无权限）

**请求参数**：
- `version_id` (路径参数): 版本 ID
- `confirmed` (查询参数, 可选):
  - `false` (默认): 返回影响报告，不执行删除（等同于调用 `/delete-impact`）
  - `true`: 执行删除操作（用户已确认）

**请求示例**：

```http
DELETE /api/versions/ver-001?confirmed=true HTTP/1.1
Authorization: Bearer <token>
```

**成功响应示例**：

```json
{
  "success": true,
  "message": "版本已删除"
}
```

**错误响应示例**：

```json
{
  "error": "版本不存在"
}
```

```json
{
  "error": "无法删除系统保留的主分支"
}
```

**状态码**：
- `200`: 成功删除（`confirmed=true`）
- `400`: 参数错误或业务规则限制
- `404`: 版本不存在（`confirmed=true` 时）
- `500`: 服务器错误

**删除操作说明**：

当 `confirmed=true` 时，后端将执行以下操作（事务保护）：

1. 删除 `collection_versions` 表中的版本元数据
2. 删除 `version_snapshots` 表中的快照数据（如果是快照类型）
3. 删除 `dynamic_data` 表中 `branch_id = version_id` 的所有动态数据
4. 删除 `data_relations` 表中 `branch_id = version_id` 的所有关联关系
5. 删除 `version_collections` 表中的版本追踪记录
6. 删除 `user_branches` 表中引用该分支的用户状态记录

**注意事项**：
- 主分支（`main`）无法删除，系统会返回错误
- 删除操作不可逆，务必在前端展示确认对话框
- 建议在删除前提示用户创建备份快照

---

### 3. 分页查询删除数据详情

**端点**：`GET /api/versions/<version_id>/delete-detail`

**描述**：分页查询指定版本下某个 Collection 的数据详情，用于展示详细的删除列表。

**认证**：需要登录（`login_required`）

**权限**：所有已登录用户

**请求参数**：
- `version_id` (路径参数): 版本 ID
- `collection` (查询参数, 必填): 查询哪个 Collection
- `page` (查询参数, 可选): 页码，默认 `1`
- `pageSize` (查询参数, 可选): 每页数量，默认 `20`，可选值：`10, 20, 50, 100`
- `sortBy` (查询参数, 可选): 排序字段，默认 `createdAt`，可选值：`createdAt, updatedAt, id`
- `sortOrder` (查询参数, 可选): 排序方向，默认 `desc`，可选值：`asc, desc`

**请求示例**：

```http
GET /api/versions/ver-001/delete-detail?collection=inspection-case&page=1&pageSize=20&sortBy=createdAt&sortOrder=desc HTTP/1.1
Authorization: Bearer <token>
```

**成功响应示例**：

```json
{
  "success": true,
  "data": {
    "collection": "inspection-case",
    "versionId": "ver-001",
    "totalCount": 512,
    "totalPages": 26,
    "currentPage": 1,
    "pageSize": 20,
    "records": [
      {
        "id": "case-001",
        "displayName": "巡检用例A",
        "createdAt": "2026-01-05T10:30:00",
        "updatedAt": "2026-01-06T14:20:00"
      },
      {
        "id": "case-002",
        "displayName": "巡检用例B",
        "createdAt": "2026-01-05T11:15:00",
        "updatedAt": "2026-01-05T11:15:00"
      }
    ],
    "hasMore": true
  }
}
```

**响应字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `collection` | String | Collection 名称 |
| `versionId` | String | 版本 ID |
| `totalCount` | Number | 总记录数 |
| `totalPages` | Number | 总页数 |
| `currentPage` | Number | 当前页码 |
| `pageSize` | Number | 每页数量 |
| `records` | Array | 当前页的记录列表 |
| `records[].id` | String | 记录 ID |
| `records[].displayName` | String | 显示名称 |
| `records[].createdAt` | String | 创建时间（ISO 8601 格式） |
| `records[].updatedAt` | String | 更新时间（ISO 8601 格式） |
| `hasMore` | Boolean | 是否有下一页 |

**错误响应示例**：

```json
{
  "error": "collection 是必填项"
}
```

**状态码**：
- `200`: 成功
- `400`: 参数错误（缺少 collection）
- `500`: 服务器错误

---

## 前端集成指南

### 典型使用场景

#### 场景 1：删除普通版本

```typescript
// 1. 用户点击删除按钮
async function handleDeleteVersion(versionId: string) {
  // 2. 获取影响报告
  const impactResponse = await fetch(`/api/versions/${versionId}/delete-impact`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  const impactData = await impactResponse.json()

  // 3. 展示确认对话框
  const confirmed = await showConfirmDialog({
    title: '确认删除版本',
    message: impactData.data.warningMessage,
    details: impactData.data.affectedCollections
  })

  if (!confirmed) return

  // 4. 执行删除
  const deleteResponse = await fetch(`/api/versions/${versionId}?confirmed=true`, {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` }
  })

  if (deleteResponse.ok) {
    showMessage('版本已删除')
    refreshVersionList()
  }
}
```

#### 场景 2：分页查看详情

```typescript
// 当用户想查看某个 Collection 的完整删除列表时
async function loadDeleteDetail(versionId: string, collection: string, page: number = 1) {
  const response = await fetch(
    `/api/versions/${versionId}/delete-detail?collection=${collection}&page=${page}&pageSize=20`,
    {
      headers: { 'Authorization': `Bearer ${token}` }
    }
  )
  const data = await response.json()

  // 渲染分页列表
  renderDeleteDetailList(data.data)
}
```

#### 场景 3：跨 Collection 版本删除

```typescript
// 跨 Collection 版本需要特别提示
async function handleCrossCollectionDelete(versionId: string) {
  const impactData = await getDeleteImpact(versionId)

  if (impactData.data.hasCrossCollectionData) {
    // 使用更醒目的提示
    const confirmed = await showWarningDialog({
      title: '警告：跨 Collection 删除',
      message: impactData.data.warningMessage,
      affectedCollections: impactData.data.affectedCollections.map(c => ({
        name: c.collection,
        count: c.recordCount
      }))
    })

    if (!confirmed) return
  }

  // 执行删除
  await deleteVersionConfirmed(versionId)
}
```

### UI 展示建议

1. **确认对话框设计**：
   - 标题：`确认删除版本 "${versionName}"`
   - 内容：展示 `warningMessage`
   - 详情：折叠面板展示 `affectedCollections` 列表
   - 按钮：`取消`（默认） / `确认删除`（危险样式，红色）

2. **跨 Collection 警告**：
   - 当 `hasCrossCollectionData = true` 时，使用警告图标和醒目颜色
   - 列出所有涉及的 Collection 及数据条数
   - 提示用户删除不可逆

3. **分页查看详情**：
   - 提供链接或按钮"查看详细数据"
   - 使用表格展示 `records` 列表
   - 支持分页和排序

4. **加载状态**：
   - 获取影响报告时显示加载中
   - 删除操作进行中禁用确认按钮，显示进度

### 错误处理

```typescript
try {
  const response = await fetch(`/api/versions/${versionId}?confirmed=true`, {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` }
  })

  if (!response.ok) {
    const error = await response.json()

    if (response.status === 400) {
      showError(error.error) // 业务规则限制，如删除主分支
    } else if (response.status === 404) {
      showError('版本不存在或已被删除')
    } else {
      showError('删除失败：' + error.error)
    }
    return
  }

  showMessage('版本已删除')
} catch (error) {
  showError('网络错误，请重试')
}
```

---

## 注意事项

### 安全性

1. **权限控制**：
   - `GET /delete-impact`: 需要登录
   - `DELETE`: 需要登录 + 写权限（guest 角色无法删除）
   - `GET /delete-detail`: 需要登录

2. **事务保护**：
   - 删除操作在数据库事务中执行
   - 失败时自动回滚，保证数据一致性

3. **主分支保护**：
   - 系统保留的主分支（`main`）无法删除
   - 后端会返回错误：`无法删除系统保留的主分支`

### 性能优化

1. **影响报告限制**：
   - `delete-impact` 只返回前 100 条记录预览
   - 如需查看完整列表，使用 `delete-detail` 分页查询

2. **分页查询**：
   - `delete-detail` 支持分页（10/20/50/100 条）
   - 支持排序（按创建时间、更新时间、ID）

3. **索引建议**：
   - `dynamic_data(collection, branch_id)` 复合索引
   - `data_relations(branch_id)` 索引
   - `version_collections(version_id)` 索引

### 数据一致性

删除版本会级联删除：
- 版本元数据（`collection_versions`）
- 快照数据（`version_snapshots`，如果是快照）
- 动态数据（`dynamic_data`，分支数据）
- 关联关系（`data_relations`）
- 版本追踪记录（`version_collections`）
- 用户分支状态（`user_branches`）

所有删除操作在同一事务中完成，确保数据一致性。

---

## 变更历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0.0 | 2026-04-11 | 初始版本，支持两阶段删除流程 |