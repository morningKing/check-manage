# 版本管理弹窗改进实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将版本管理从 Drawer 改为 80% 宽度 Dialog，添加搜索、筛选、分页功能，优化操作按钮布局。

**Architecture:** 后端 API 先添加分页支持，前端 API 适配，然后重构 VersionManager.vue 组件（Drawer→Dialog，添加工具栏和分页，操作按钮改为下拉菜单）。

**Tech Stack:** Python Flask + psycopg2（后端），Vue 3 + TypeScript + Element Plus（前端）

---

## Files to Modify

| File | Responsibility |
|------|----------------|
| `server/utils/version.py` | 添加 `keyword` 搜索参数到 `get_version_list` |
| `server/routes/versions.py` | `list_versions` 路由添加分页参数，返回 `{items, total}` |
| `server/tests/test_routes_versions.py` | 分页 API 测试 |
| `src/api/version.ts` | `getVersions` 添加分页参数和响应类型 |
| `src/types/version.ts` | 添加 `PaginatedVersionsResponse` 类型 |
| `src/components/common/VersionManager.vue` | 核心组件重构：Dialog、搜索筛选、分页、下拉菜单 |

---

## Task 1: Backend - Add Pagination to get_version_list

**Files:**
- Modify: `server/utils/version.py:613-670`
- Test: `server/tests/test_version_pagination.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_version_pagination.py
"""
测试版本列表分页功能
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras
from db import get_db
from utils.version import get_version_list, create_version_snapshot


def test_get_version_list_with_pagination():
    """测试 get_version_list 支持分页参数"""
    collection = 'test-pagination-coll'
    test_user = 'test_user'

    # Setup: Create multiple versions
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()

        # Create test data
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
            ('test-pag-001', collection, psycopg2.extras.Json({'name': 'Test'}), 'main')
        )
        conn.commit()

    # Create 15 versions
    version_ids = []
    for i in range(15):
        result = create_version_snapshot(
            collection=collection,
            name=f'v{i:02d}-test',
            description=f'Version {i}',
            version_type='snapshot',
            parent_version=None,
            created_by=test_user,
            branch_id='main'
        )
        version_ids.append(result['id'])

    # Test pagination
    page1 = get_version_list(collection=collection, page=1, pageSize=5)
    assert len(page1['items']) == 5
    assert page1['total'] == 15

    page2 = get_version_list(collection=collection, page=2, pageSize=5)
    assert len(page2['items']) == 5
    assert page2['total'] == 15

    # Verify ordering (DESC by created_at)
    assert page1['items'][0]['name'] == 'v14-test'

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        for vid in version_ids:
            cur.execute('DELETE FROM version_snapshots WHERE version_id = %s', (vid,))
            cur.execute('DELETE FROM version_relations WHERE version_id = %s', (vid,))
            cur.execute('DELETE FROM version_collections WHERE version_id = %s', (vid,))
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()


def test_get_version_list_with_keyword_search():
    """测试 get_version_list 支持关键词搜索"""
    collection = 'test-search-coll'
    test_user = 'test_user'

    # Setup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
            ('test-search-001', collection, psycopg2.extras.Json({'name': 'Test'}), 'main')
        )
        conn.commit()

    # Create versions with different names
    create_version_snapshot(collection, 'release-v1.0', 'Release version', 'snapshot', None, test_user, 'main')
    create_version_snapshot(collection, 'feature-abc', 'Feature branch', 'branch', None, test_user, 'main')
    create_version_snapshot(collection, 'hotfix-123', 'Hotfix', 'snapshot', None, test_user, 'main')

    # Test search
    result = get_version_list(collection=collection, keyword='release')
    assert len(result['items']) == 1
    assert result['items'][0]['name'] == 'release-v1.0'

    result2 = get_version_list(collection=collection, keyword='v')
    assert result2['total'] >= 1

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM version_snapshots WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM version_relations WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM version_collections WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()


if __name__ == '__main__':
    test_get_version_list_with_pagination()
    test_get_version_list_with_keyword_search()
    print('All pagination tests passed!')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_version_pagination.py -v`
Expected: FAIL with "TypeError: get_version_list() got unexpected keyword argument 'page'"

- [ ] **Step 3: Write minimal implementation**

Modify `server/utils/version.py` function `get_version_list`:

```python
def get_version_list(collection=None, status=None, page=None, pageSize=None, keyword=None):
    """
    获取版本列表（支持分页和搜索）

    Parameters
    ----------
    collection : str | None
        筛选集合，None 表示所有集合
    status : str | None
        筛选状态，None 表示所有状态
    page : int | None
        页码（从 1 开始），None 表示不分页
    pageSize : int | None
        每页条数，默认 10
    keyword : str | None
        搜索关键词（匹配名称和描述）

    Returns
    -------
    dict | list[dict]
        分页时返回 {items: [...], total: int}
        不分页时返回 list[dict]
    """
    with get_db() as conn:
        cur = conn.cursor()
        conditions = []
        params = []

        if collection:
            conditions.append('collection = %s')
            params.append(collection)
        if status:
            conditions.append('status = %s')
            params.append(status)
        if keyword:
            conditions.append('(name ILIKE %s OR description ILIKE %s)')
            params.extend([f'%{keyword}%', f'%{keyword}%'])

        where_clause = 'WHERE ' + ' AND '.join(conditions) if conditions else ''

        # Count total if pagination requested
        if page is not None:
            count_sql = f'SELECT COUNT(*) FROM collection_versions {where_clause}'
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            # Apply pagination
            actual_page_size = pageSize or 10
            offset = (page - 1) * actual_page_size

            sql = f'''
                SELECT id, collection, name, description, version_type, parent_version, status,
                       data_hash, records_count, relations_count, created_by, created_at,
                       merged_at, merged_by, merged_into, is_protected
                FROM collection_versions
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            '''
            cur.execute(sql, params + [actual_page_size, offset])
            rows = cur.fetchall()

            items = [{
                'id': r[0],
                'collection': r[1],
                'name': r[2],
                'description': r[3],
                'versionType': r[4],
                'parentVersion': r[5],
                'status': r[6],
                'dataHash': r[7],
                'recordsCount': r[8],
                'relationsCount': r[9],
                'createdBy': r[10],
                'createdAt': r[11].isoformat() if r[11] else None,
                'mergedAt': r[12].isoformat() if r[12] else None,
                'mergedBy': r[13],
                'mergedInto': r[14],
                'isProtected': r[15],
            } for r in rows]

            return {'items': items, 'total': total}

        # No pagination - return full list (backward compatible)
        sql = f'''
            SELECT id, collection, name, description, version_type, parent_version, status,
                   data_hash, records_count, relations_count, created_by, created_at,
                   merged_at, merged_by, merged_into, is_protected
            FROM collection_versions
            {where_clause}
            ORDER BY created_at DESC
        '''
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [{
        'id': r[0],
        'collection': r[1],
        'name': r[2],
        'description': r[3],
        'versionType': r[4],
        'parentVersion': r[5],
        'status': r[6],
        'dataHash': r[7],
        'recordsCount': r[8],
        'relationsCount': r[9],
        'createdBy': r[10],
        'createdAt': r[11].isoformat() if r[11] else None,
        'mergedAt': r[12].isoformat() if r[12] else None,
        'mergedBy': r[13],
        'mergedInto': r[14],
        'isProtected': r[15],
    } for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_version_pagination.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/version.py server/tests/test_version_pagination.py
git commit -m "$(cat <<'EOF'
feat(version): add pagination and keyword search to get_version_list

- Add page, pageSize, keyword parameters
- Return {items, total} when pagination requested
- Backward compatible: return list when no pagination params

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Backend - Update list_versions Route

**Files:**
- Modify: `server/routes/versions.py:35-43`
- Test: `server/tests/test_routes_versions.py` (modify existing)

- [ ] **Step 1: Write the failing test**

Add to `server/tests/test_routes_versions.py`:

```python
def test_list_versions_with_pagination(mock_conn, mock_cursor, client, admin_headers):
    """测试版本列表 API 支持分页"""
    # Mock paginated response
    mock_cursor.fetchall.return_value = [
        ('ver-1', 'project', 'v1.0', '', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
    ]
    mock_cursor.fetchone.return_value = (25,)  # total count

    response = client.get('/versions?collection=project&page=1&pageSize=10', headers=admin_headers)

    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert 'total' in data
    assert data['total'] == 25


def test_list_versions_with_keyword_search(mock_conn, mock_cursor, client, admin_headers):
    """测试版本列表 API 支持关键词搜索"""
    mock_cursor.fetchall.return_value = [
        ('ver-1', 'project', 'v1.0-release', 'Release version', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
    ]
    mock_cursor.fetchone.return_value = (1,)

    response = client.get('/versions?collection=project&keyword=release&page=1', headers=admin_headers)

    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_routes_versions.py::test_list_versions_with_pagination -v`
Expected: FAIL (route doesn't return paginated format yet)

- [ ] **Step 3: Write minimal implementation**

Modify `server/routes/versions.py` function `list_versions`:

```python
@versions_bp.route('/versions', methods=['GET'])
@login_required
def list_versions():
    """获取版本列表（支持分页和搜索）"""
    collection = request.args.get('collection')
    status = request.args.get('status')
    page = request.args.get('page', type=int)
    pageSize = request.args.get('pageSize', type=int)
    keyword = request.args.get('keyword')

    # If pagination params provided, return paginated response
    if page is not None:
        result = get_version_list(
            collection=collection,
            status=status,
            page=page,
            pageSize=pageSize or 10,
            keyword=keyword
        )
        return jsonify(result)

    # Backward compatible: return full list
    versions = get_version_list(collection=collection, status=status)
    return jsonify(versions)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_routes_versions.py::test_list_versions_with_pagination tests/test_routes_versions.py::test_list_versions_with_keyword_search -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/versions.py server/tests/test_routes_versions.py
git commit -m "$(cat <<'EOF'
feat(routes): add pagination and search to list_versions API

- Accept page, pageSize, keyword query params
- Return {items, total} for paginated requests
- Backward compatible with existing clients

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Frontend - Update API Types and Function

**Files:**
- Modify: `src/types/version.ts`
- Modify: `src/api/version.ts:22-27`

- [ ] **Step 1: Add PaginatedVersionsResponse type**

Modify `src/types/version.ts`, add new type:

```typescript
// Add after CollectionVersion interface

/**
 * 分页版本列表响应
 */
export interface PaginatedVersionsResponse {
  items: CollectionVersion[]
  total: number
}
```

- [ ] **Step 2: Update getVersions function**

Modify `src/api/version.ts`:

```typescript
import type {
  CollectionVersion,
  CreateVersionRequest,
  DiffVersionRequest,
  MergeVersionRequest,
  MergeResult,
  RestoreResult,
  PartialMergeRequest,
  PartialMergeResponse,
  PaginatedVersionsResponse,  // Add import
} from '@/types'
import type { DiffResult } from '@/types'

/**
 * 获取版本列表（支持分页和搜索）
 * @param collection 筛选集合
 * @param status 筛选状态
 * @param page 页码（从 1 开始）
 * @param pageSize 每页条数
 * @param keyword 搜索关键词
 */
export function getVersions(
  collection?: string,
  status?: string,
  page?: number,
  pageSize?: number,
  keyword?: string
) {
  const params: Record<string, string | number> = {}
  if (collection) params.collection = collection
  if (status) params.status = status
  if (page) params.page = page
  if (pageSize) params.pageSize = pageSize
  if (keyword) params.keyword = keyword

  // If pagination requested, expect paginated response
  if (page) {
    return get<PaginatedVersionsResponse>('/versions', params)
  }

  // Backward compatible: return array
  return get<CollectionVersion[]>('/versions', params)
}
```

- [ ] **Step 3: Run frontend type check**

Run: `npm run build`
Expected: No TypeScript errors

- [ ] **Step 4: Commit**

```bash
git add src/types/version.ts src/api/version.ts
git commit -m "$(cat <<'EOF'
feat(frontend): add pagination support to getVersions API

- Add PaginatedVersionsResponse type
- Update getVersions with page, pageSize, keyword params
- Backward compatible return type

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Frontend - Change Drawer to Dialog

**Files:**
- Modify: `src/components/common/VersionManager.vue:14-21`

- [ ] **Step 1: Replace el-drawer with el-dialog**

Change template section (lines 14-21):

```vue
<!-- BEFORE -->
<el-drawer
  v-model="visible"
  title="版本管理"
  direction="rtl"
  size="700px"
  :close-on-click-modal="false"
  destroy-on-close
>

<!-- AFTER -->
<el-dialog
  v-model="visible"
  :title="'版本管理 - ' + pageName"
  width="80%"
  top="5vh"
  :close-on-click-modal="false"
  destroy-on-close
>
```

And change closing tag at line 265:

```vue
<!-- BEFORE -->
</el-drawer>

<!-- AFTER -->
</el-dialog>
```

- [ ] **Step 2: Verify frontend builds**

Run: `npm run build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add src/components/common/VersionManager.vue
git commit -m "$(cat <<'EOF'
feat(ui): change version manager from Drawer to Dialog

- Replace el-drawer with el-dialog (80% width)
- Add top="5vh" for better positioning
- Include collection name in dialog title

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Frontend - Add Search and Filter Toolbar

**Files:**
- Modify: `src/components/common/VersionManager.vue`

- [ ] **Step 1: Add state variables for search/filter**

Add after line 319 (after `deletingVersionId`):

```typescript
// 搜索和筛选
const searchKeyword = ref('')
const filterStatus = ref('')
const filterType = ref('')
const currentPage = ref(1)
const pageSize = ref(10)
const totalVersions = ref(0)
```

- [ ] **Step 2: Add toolbar section in template**

Replace the toolbar section (lines 40-50):

```vue
<!-- 工具栏：搜索、筛选、创建 -->
<div class="toolbar">
  <div class="toolbar-left">
    <el-input
      v-model="searchKeyword"
      placeholder="搜索版本名称..."
      clearable
      style="width: 200px"
      @keyup.enter="handleSearch"
    />
    <el-select v-model="filterStatus" placeholder="状态" clearable style="width: 100px" @change="handleSearch">
      <el-option label="全部" value="" />
      <el-option label="活跃" value="active" />
      <el-option label="已合并" value="merged" />
      <el-option label="已归档" value="archived" />
    </el-select>
    <el-select v-model="filterType" placeholder="类型" clearable style="width: 100px" @change="handleSearch">
      <el-option label="全部" value="" />
      <el-option label="快照" value="snapshot" />
      <el-option label="分支" value="branch" />
    </el-select>
    <el-button @click="handleSearch" :loading="loading">
      <el-icon><Search /></el-icon>
      搜索
    </el-button>
  </div>
  <div class="toolbar-right">
    <el-button type="primary" @click="showCreateDialog = true">
      <el-icon><Plus /></el-icon>
      创建版本
    </el-button>
  </div>
</div>
```

- [ ] **Step 3: Add Search icon import**

Add to imports (line 271):

```typescript
import { Plus, Refresh, Sort, RefreshRight, Delete, Connection, Switch, FolderOpened, Document, Search } from '@element-plus/icons-vue'
```

- [ ] **Step 4: Update loadVersions function**

Replace the `loadVersions` function (lines 397-408):

```typescript
async function loadVersions() {
  loading.value = true
  try {
    const result = await getVersions(
      props.collection,
      filterStatus.value,
      currentPage.value,
      pageSize.value,
      searchKeyword.value
    )

    // Handle paginated response
    if (currentPage.value) {
      versions.value = result.items || result
      totalVersions.value = result.total || versions.value.length
    } else {
      // Backward compatible
      versions.value = result as CollectionVersion[]
      totalVersions.value = versions.value.length
    }

    currentBranch.value = await getCurrentBranch(props.collection)
  } catch {
    versions.value = []
    currentBranch.value = null
    totalVersions.value = 0
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  loadVersions()
}
```

- [ ] **Step 5: Add toolbar CSS**

Add to style section:

```scss
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;

  .toolbar-left {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .toolbar-right {
    display: flex;
    gap: 8px;
  }
}
```

- [ ] **Step 6: Verify frontend builds**

Run: `npm run build`
Expected: Build succeeds

- [ ] **Step 7: Commit**

```bash
git add src/components/common/VersionManager.vue
git commit -m "$(cat <<'EOF'
feat(ui): add search and filter toolbar to version manager

- Add keyword search input with enter key support
- Add status and type filter dropdowns
- Update loadVersions to use pagination API
- Add toolbar-left/right layout

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Frontend - Add Pagination Component

**Files:**
- Modify: `src/components/common/VersionManager.vue`

- [ ] **Step 1: Add pagination section after table**

Add after `</el-table>` (around line 158):

```vue
<!-- 分页 -->
<div class="pagination-wrapper" v-if="totalVersions > 0">
  <el-pagination
    v-model:current-page="currentPage"
    v-model:page-size="pageSize"
    :page-sizes="[10, 20, 50]"
    :total="totalVersions"
    layout="total, sizes, prev, pager, next, jumper"
    @current-change="loadVersions"
    @size-change="handlePageSizeChange"
  />
</div>
```

- [ ] **Step 2: Add handlePageSizeChange function**

Add after `handleSearch`:

```typescript
function handlePageSizeChange() {
  currentPage.value = 1
  loadVersions()
}
```

- [ ] **Step 3: Add pagination CSS**

Add to style section:

```scss
.pagination-wrapper {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}
```

- [ ] **Step 4: Remove old empty state**

Replace line 158:

```vue
<!-- BEFORE -->
<el-empty v-if="!loading && versions.length === 0" description="暂无版本快照" />

<!-- AFTER -->
<el-empty v-if="!loading && versions.length === 0 && !searchKeyword && !filterStatus && !filterType" description="暂无版本快照" />
<el-empty v-else-if="!loading && versions.length === 0" description="未找到匹配的版本" />
```

- [ ] **Step 5: Verify frontend builds**

Run: `npm run build`
Expected: Build succeeds

- [ ] **Step 6: Commit**

```bash
git add src/components/common/VersionManager.vue
git commit -m "$(cat <<'EOF'
feat(ui): add pagination to version manager

- Add el-pagination with 10/20/50 options
- Show total count and page jumper
- Reset to page 1 when pageSize changes
- Add empty state for search results

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Frontend - Change Action Buttons to Dropdown Menu

**Files:**
- Modify: `src/components/common/VersionManager.vue`

- [ ] **Step 1: Replace action column buttons with dropdown**

Replace the action column template (lines 103-153):

```vue
<el-table-column label="操作" width="140" fixed="right">
  <template #default="{ row }">
    <!-- 已合并状态：无操作 -->
    <span v-if="row.status === 'merged'" class="merged-label">已合并</span>

    <!-- 活跃状态：切换按钮 + 更多下拉 -->
    <div v-else class="action-buttons">
      <!-- 切换按钮：仅分支类型显示 -->
      <el-button
        v-if="row.versionType === 'branch'"
        size="small"
        type="success"
        @click="handleSwitch(row)"
        :disabled="isCurrentBranch(row)"
      >
        切换
      </el-button>

      <!-- 更多操作下拉菜单 -->
      <el-dropdown trigger="click" @command="(cmd: string) => handleAction(cmd, row)">
        <el-button size="small">
          更多 <el-icon class="el-icon--right"><ArrowDown /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="diff" :disabled="row.status === 'merged'">
              <el-icon><Sort /></el-icon> 对比
            </el-dropdown-item>
            <el-dropdown-item command="merge" :disabled="row.status === 'merged'">
              <el-icon><Connection /></el-icon> 合并
            </el-dropdown-item>
            <el-dropdown-item command="restore" :disabled="row.status === 'merged'">
              <el-icon><RefreshRight /></el-icon> 恢复
            </el-dropdown-item>
            <el-dropdown-item divided command="delete" :disabled="row.isProtected || row.status === 'merged'">
              <span style="color: #f56c6c"><el-icon><Delete /></el-icon> 删除</span>
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </template>
</el-table-column>
```

- [ ] **Step 2: Add ArrowDown icon import**

Add to imports:

```typescript
import { Plus, Refresh, Sort, RefreshRight, Delete, Connection, Switch, FolderOpened, Document, Search, ArrowDown } from '@element-plus/icons-vue'
```

- [ ] **Step 3: Add handleAction function**

Add after `handleSwitch`:

```typescript
function handleAction(command: string, row: CollectionVersion) {
  switch (command) {
    case 'diff':
      handleDiff(row)
      break
    case 'merge':
      handleMerge(row)
      break
    case 'restore':
      handleRestore(row)
      break
    case 'delete':
      handleDelete(row)
      break
  }
}
```

- [ ] **Step 4: Add action buttons CSS**

Add to style section:

```scss
.action-buttons {
  display: flex;
  gap: 8px;
  align-items: center;
}

.merged-label {
  color: #909399;
  font-size: 13px;
}
```

- [ ] **Step 5: Verify frontend builds**

Run: `npm run build`
Expected: Build succeeds

- [ ] **Step 6: Run frontend tests**

Run: `npm run test`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/components/common/VersionManager.vue
git commit -m "$(cat <<'EOF'
feat(ui): replace action buttons with dropdown menu

- Show "切换" button only for branch type
- Add dropdown menu: 对比, 合并, 恢复, 删除
- Mark delete action with red color and divider
- Show "已合并" label for merged status

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Integration Testing

**Files:**
- Test: Manual verification

- [ ] **Step 1: Start development servers**

Run: `npm run dev:all`
Expected: Frontend at localhost:5173, backend at localhost:3002

- [ ] **Step 2: Test basic functionality**

Open DynamicPage, click version management button:
- Dialog opens at 80% width ✓
- Search input visible ✓
- Status/type filters visible ✓
- Pagination visible ✓
- Table columns display correctly ✓

- [ ] **Step 3: Test search functionality**

- Enter keyword in search input
- Press Enter or click search button
- List updates with matching versions ✓
- Empty state shows when no match ✓

- [ ] **Step 4: Test pagination**

- Change pageSize to 20 ✓
- Click page 2 ✓
- Jumper input works ✓

- [ ] **Step 5: Test action buttons**

- Branch row shows "切换" button ✓
- Dropdown menu opens ✓
- Delete shows red text ✓
- Merged row shows "已合并" label ✓

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(version-manager): complete dialog-based version management

- 80% width dialog replaces drawer
- Search by keyword (name + description)
- Filter by status and type
- Pagination with 10/20/50 options
- Dropdown menu for actions
- "切换" button always visible for branches

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Verification Checklist

After implementation, verify each acceptance criterion:

1. [ ] 弹窗宽度 80%，信息完整展示
2. [ ] 搜索功能正常，可按名称/描述搜索
3. [ ] 状态/类型筛选正常工作
4. [ ] 分页显示正确，每页可选 10/20/50
5. [ ] 操作按钮布局清晰，下拉菜单正常
6. [ ] 切换、对比、合并、恢复、删除功能正常
7. [ ] 已合并状态隐藏操作按钮，显示「已合并」标签