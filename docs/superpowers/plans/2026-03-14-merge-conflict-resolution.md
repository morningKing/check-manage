# 版本合并冲突解决功能实现计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现类似 Git Merge 的版本合并功能，支持记录级和字段级合并选择。

**Architecture:** 新建独立 MergeConflictDialog 组件，采用三步向导流程（概览→记录选择→字段选择）。后端新增 partial-merge API，使用事务包装保证原子性，同步处理关系数据。

**Tech Stack:** Vue 3 Composition API, Element Plus, TypeScript, Flask, PostgreSQL

---

## 文件结构

```
src/components/common/
├── MergeConflictDialog.vue     # 主对话框容器
├── StepOverview.vue            # 步骤1：变更概览
├── StepRecordSelect.vue        # 步骤2：记录选择
├── StepFieldSelect.vue         # 步骤3：字段选择
├── composables/
│   └── useMergeState.ts        # 合并状态管理
└── VersionManager.vue          # 修改：集成合并对话框

src/types/version.ts            # 修改：添加类型
src/api/version.ts              # 修改：添加 API

server/utils/errors.py          # 新建：MergeError 异常类
server/routes/versions.py       # 修改：添加路由
server/utils/version.py         # 修改：添加函数
server/tests/test_version.py    # 修改：添加测试
```

---

## Chunk 1: 后端类型和 API

### Task 1: 类型定义

**Files:**
- Modify: `src/types/version.ts` (在文件末尾追加)

- [ ] **Step 1: 添加部分合并相关类型**

在 `src/types/version.ts` 文件末尾添加：

```typescript
/**
 * 部分合并决策
 */
export interface PartialMergeDecisions {
  added_record_ids: string[]
  removed_record_ids: string[]
  modified_records: ModifiedRecordDecision[]
}

/**
 * 修改记录的字段决策
 */
export interface ModifiedRecordDecision {
  record_id: string
  field_values: Record<string, any>
}

/**
 * 部分合并请求
 */
export interface PartialMergeRequest {
  source_version_id: string
  target_branch: string
  decisions: PartialMergeDecisions
}

/**
 * 部分合并响应
 */
export interface PartialMergeResponse {
  success: boolean
  merged_count: number
  message: string
}

/**
 * 合并状态步骤
 */
export type MergeStep = 'overview' | 'records' | 'fields'

/**
 * 合并决策状态
 */
export interface MergeDecisions {
  addedRecords: Set<string>
  removedRecords: Set<string>
  modifiedRecords: Map<string, {
    recordId: string
    fieldDecisions: Map<string, 'source' | 'target'>
  }>
}

/**
 * 合并对话框状态
 */
export interface MergeState {
  step: MergeStep
  sourceVersion: CollectionVersion | null
  targetBranch: string
  diffResult: DiffResult | null
  decisions: MergeDecisions
}
```

- [ ] **Step 2: 验证类型文件编译**

Run: `cd E:/wsl/check/check-manage && npx tsc --noEmit src/types/version.ts`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add src/types/version.ts
git commit -m "feat: 添加部分合并相关类型定义

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: 前端 API 函数

**Files:**
- Modify: `src/api/version.ts:100-101`

- [ ] **Step 1: 添加部分合并 API 函数**

在 `src/api/version.ts` 文件末尾添加：

```typescript
/**
 * 部分合并版本
 */
export function partialMergeVersion(data: PartialMergeRequest) {
  return post<PartialMergeResponse>('/versions/partial-merge', data)
}
```

同时在文件顶部 import 中添加类型：

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
} from '@/types'
```

- [ ] **Step 2: 验证 API 文件编译**

Run: `cd E:/wsl/check/check-manage && npx tsc --noEmit src/api/version.ts`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add src/api/version.ts
git commit -m "feat: 添加部分合并 API 函数

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 2: 后端实现

### Task 3: MergeError 异常类

**Files:**
- Create: `server/utils/errors.py`

- [ ] **Step 1: 创建 MergeError 异常类**

创建 `server/utils/errors.py`：

```python
"""
自定义异常类
"""


class MergeError(Exception):
    """合并操作异常"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# 错误码常量
VERSION_NOT_FOUND = 'VERSION_NOT_FOUND'
BRANCH_NOT_FOUND = 'BRANCH_NOT_FOUND'
PERMISSION_DENIED = 'PERMISSION_DENIED'
MERGE_FAILED = 'MERGE_FAILED'
VERSION_ALREADY_MERGED = 'VERSION_ALREADY_MERGED'
```

- [ ] **Step 2: Commit**

```bash
git add server/utils/errors.py
git commit -m "feat: 添加 MergeError 异常类

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: apply_partial_merge 函数

**Files:**
- Modify: `server/utils/version.py:766-767`
- Test: `server/tests/test_version.py`

- [ ] **Step 1: 编写后端测试**

在 `server/tests/test_version.py` 添加测试：

```python
import pytest
from unittest.mock import patch, MagicMock
from utils.version import apply_partial_merge
from utils.errors import MergeError, VERSION_NOT_FOUND


class TestPartialMerge:
    """部分合并测试"""

    @pytest.fixture
    def mock_db_setup(self):
        """模拟数据库设置"""
        with patch('utils.version.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_conn.cursor.return_value = mock_cur
            mock_conn.__enter__ = lambda self: self
            mock_conn.__exit__ = lambda self, *args: None
            mock_get_db.return_value = mock_conn

            # 模拟版本查询结果
            mock_cur.fetchone.side_effect = [
                ('test_collection', 'active', 'snapshot'),  # 版本信息
                ({'name': 'test'},),  # 当前记录数据
            ]
            mock_cur.fetchall.return_value = []

            yield {'conn': mock_conn, 'cur': mock_cur}

    def test_partial_merge_add_records(self, mock_db_setup):
        """测试部分合并新增记录"""
        decisions = {
            'added_record_ids': ['record-1'],
            'removed_record_ids': [],
            'modified_records': []
        }

        # 模拟源数据
        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = (
                [{'id': 'record-1', 'name': '新增记录'}],
                []
            )

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is True
        assert result['merged_count'] == 1

    def test_partial_merge_remove_records(self, mock_db_setup):
        """测试部分合并删除记录"""
        decisions = {
            'added_record_ids': [],
            'removed_record_ids': ['record-2'],
            'modified_records': []
        }

        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = ([], [])

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is True
        assert result['merged_count'] == 1

    def test_partial_merge_modified_records(self, mock_db_setup):
        """测试部分合并修改记录"""
        decisions = {
            'added_record_ids': [],
            'removed_record_ids': [],
            'modified_records': [{
                'record_id': 'record-3',
                'field_values': {'name': '新名称'}
            }]
        }

        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = ([], [])

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is True

    def test_partial_merge_with_relations(self, mock_db_setup):
        """测试部分合并同步处理关系数据"""
        decisions = {
            'added_record_ids': ['record-1'],
            'removed_record_ids': [],
            'modified_records': []
        }

        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = (
                [{'id': 'record-1', 'name': '关联记录'}],
                [{'record_id': 'record-1', 'field_name': 'tags',
                  'related_collection': 'tags', 'related_id': 'tag-1'}]
            )

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is True

    def test_partial_merge_rollback_on_error(self, mock_db_setup):
        """测试部分合并失败时回滚"""
        mock_db_setup['cur'].execute.side_effect = Exception('DB Error')

        decisions = {
            'added_record_ids': ['record-1'],
            'removed_record_ids': [],
            'modified_records': []
        }

        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = (
                [{'id': 'record-1', 'name': '测试'}],
                []
            )

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is False
        assert '失败' in result['message']

    def test_partial_merge_version_not_found(self):
        """测试版本不存在"""
        with patch('utils.version.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_cur
            mock_conn.__enter__ = lambda self: self
            mock_conn.__exit__ = lambda self, *args: None
            mock_get_db.return_value = mock_conn

            result = apply_partial_merge(
                source_version_id='nonexistent',
                target_branch='main',
                decisions={},
                merged_by='admin'
            )

        assert result['success'] is False
        assert result['message'] == '源版本不存在'

    def test_partial_merge_version_already_merged(self):
        """测试版本已合并"""
        with patch('utils.version.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = ('test', 'merged', 'snapshot')
            mock_conn.cursor.return_value = mock_cur
            mock_conn.__enter__ = lambda self: self
            mock_conn.__exit__ = lambda self, *args: None
            mock_get_db.return_value = mock_conn

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions={},
                merged_by='admin'
            )

        assert result['success'] is False
        assert '已合并' in result['message']
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd E:/wsl/check/check-manage/server && python -m pytest tests/test_version.py::TestPartialMerge -v`
Expected: FAIL with "module has no attribute 'apply_partial_merge'"

- [ ] **Step 3: 实现 apply_partial_merge 函数**

在 `server/utils/version.py` 文件 `merge_version_to_current` 函数后添加：

```python
def apply_partial_merge(source_version_id, target_branch, decisions, merged_by, user_id=None):
    """
    应用部分合并决策（事务包装，部分失败则回滚）

    Parameters
    ----------
    source_version_id : str
        源版本 ID
    target_branch : str
        目标分支 ID
    decisions : dict
        合并决策 {
            added_record_ids: [],
            removed_record_ids: [],
            modified_records: [{record_id, field_values}]
        }
    merged_by : str
        合并者
    user_id : str | None
        用户 ID

    Returns
    -------
    dict
        {success, merged_count, message}
    """
    from utils.db import get_db
    import psycopg2.extras

    with get_db() as conn:
        cur = conn.cursor()

        try:
            # 获取版本信息
            cur.execute(
                'SELECT collection, status, version_type FROM collection_versions WHERE id = %s',
                (source_version_id,),
            )
            row = cur.fetchone()
            if not row:
                return {'success': False, 'merged_count': 0, 'message': '源版本不存在'}
            collection, status, version_type = row

            if status == 'merged':
                return {'success': False, 'merged_count': 0, 'message': '该版本已被合并'}

            # 加载源数据
            if version_type == 'branch':
                source_records, source_rels = load_current_data(collection, branch_id=source_version_id)
            else:
                source_records, source_rels = load_version_data(source_version_id)

            # 构建源记录查找字典
            source_records_map = {r['id']: r for r in source_records}

            # 构建源关系查找字典
            source_rels_map = {}
            for rel in source_rels:
                record_id = rel['record_id']
                if record_id not in source_rels_map:
                    source_rels_map[record_id] = []
                source_rels_map[record_id].append(rel)

            merged_count = 0

            # 1. 处理新增记录
            for record_id in decisions.get('added_record_ids', []):
                if record_id not in source_records_map:
                    continue
                record = source_records_map[record_id]
                data = {k: v for k, v in record.items() if k != 'id'}

                # 插入记录
                cur.execute(
                    'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s) '
                    'ON CONFLICT (id) DO NOTHING',
                    (record_id, collection, psycopg2.extras.Json(data), target_branch),
                )

                # 复制关系数据
                if record_id in source_rels_map:
                    for rel in source_rels_map[record_id]:
                        cur.execute(
                            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
                            'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                            (collection, record_id, rel['field_name'], rel['related_collection'], rel['related_id'], target_branch),
                        )

                merged_count += 1

            # 2. 处理删除记录
            for record_id in decisions.get('removed_record_ids', []):
                # 删除记录
                cur.execute(
                    'DELETE FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                    (collection, record_id, target_branch),
                )

                # 删除关系数据
                cur.execute(
                    'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                    (collection, record_id, target_branch),
                )

                merged_count += 1

            # 3. 处理修改记录
            for mod in decisions.get('modified_records', []):
                record_id = mod['record_id']
                field_values = mod.get('field_values', {})

                if not field_values:
                    continue

                # 获取当前记录
                cur.execute(
                    'SELECT data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                    (collection, record_id, target_branch),
                )
                current_row = cur.fetchone()
                if not current_row:
                    continue

                current_data = current_row[0] or {}

                # 合并字段值
                for field, value in field_values.items():
                    current_data[field] = value

                # 更新记录
                cur.execute(
                    'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 '
                    'WHERE collection = %s AND id = %s AND branch_id = %s',
                    (psycopg2.extras.Json(current_data), collection, record_id, target_branch),
                )

                merged_count += 1

            # 4. 更新 data_hash（重新计算）
            cur.execute(
                'SELECT id, data FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (collection, target_branch),
            )
            records = cur.fetchall()
            import hashlib
            import json
            data_str = json.dumps([r[1] for r in records], sort_keys=True, ensure_ascii=False)
            new_hash = hashlib.sha256(data_str.encode()).hexdigest()

            cur.execute(
                'UPDATE collection_versions SET data_hash = %s WHERE id = %s',
                (new_hash, target_branch),
            )

            conn.commit()
            return {
                'success': True,
                'merged_count': merged_count,
                'message': f'成功合并 {merged_count} 条记录'
            }

        except Exception as e:
            conn.rollback()
            return {
                'success': False,
                'merged_count': 0,
                'message': f'合并失败: {str(e)}'
            }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd E:/wsl/check/check-manage/server && python -m pytest tests/test_version.py::TestPartialMerge -v`
Expected: 所有测试 PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/version.py server/tests/test_version.py
git commit -m "feat: 实现部分合并函数 apply_partial_merge

- 支持新增/删除/修改记录的部分合并
- 同步处理关系数据
- 事务包装保证原子性

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: 部分合并 API 路由

**Files:**
- Modify: `server/routes/versions.py:23-24`
- Test: `server/tests/test_version.py`

- [ ] **Step 1: 编写路由测试**

在 `server/tests/test_version.py` 添加：

```python
class TestPartialMergeRoute:
    """部分合并路由测试"""

    @pytest.fixture
    def client(self, app):
        """创建测试客户端"""
        return app.test_client()

    @pytest.fixture
    def mock_auth(self, monkeypatch):
        """模拟认证"""
        from flask import g as flask_g

        def mock_get_user():
            class MockUser:
                username = 'testuser'
                userId = 'user-1'
            flask_g.current_user = MockUser()

        monkeypatch.setattr('auth.get_current_user', mock_get_user)

    def test_partial_merge_route_success(self, client, mock_auth):
        """测试部分合并路由成功"""
        with patch('routes.versions.apply_partial_merge') as mock_merge:
            mock_merge.return_value = {
                'success': True,
                'merged_count': 2,
                'message': '成功'
            }

            response = client.post('/api/versions/partial-merge', json={
                'source_version_id': 'version-1',
                'target_branch': 'main',
                'decisions': {
                    'added_record_ids': ['r1'],
                    'removed_record_ids': [],
                    'modified_records': []
                }
            })

        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True

    def test_partial_merge_route_unauthorized(self, client):
        """测试未授权访问"""
        response = client.post('/api/versions/partial-merge', json={
            'source_version_id': 'version-1',
            'target_branch': 'main',
            'decisions': {}
        })

        assert response.status_code == 401

    def test_partial_merge_route_version_not_found(self, client, mock_auth):
        """测试版本不存在"""
        with patch('routes.versions.apply_partial_merge') as mock_merge:
            mock_merge.return_value = {
                'success': False,
                'merged_count': 0,
                'message': '源版本不存在'
            }

            response = client.post('/api/versions/partial-merge', json={
                'source_version_id': 'nonexistent',
                'target_branch': 'main',
                'decisions': {}
            })

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_partial_merge_route_missing_params(self, client, mock_auth):
        """测试缺少必填参数"""
        response = client.post('/api/versions/partial-merge', json={
            'target_branch': 'main'
        })

        assert response.status_code == 400
        assert 'error' in response.get_json()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd E:/wsl/check/check-manage/server && python -m pytest tests/test_version.py::TestPartialMergeRoute -v`
Expected: FAIL with 404 或路由不存在

- [ ] **Step 3: 添加路由**

在 `server/routes/versions.py` 的 import 中添加：

```python
from utils.version import (
    # ... existing imports ...
    apply_partial_merge,
)
```

在文件末尾添加路由：

```python
@versions_bp.route('/versions/partial-merge', methods=['POST'])
@write_required
def partial_merge_version():
    """部分合并版本到当前数据"""
    body = request.get_json(force=True)
    source_version_id = body.get('source_version_id')
    target_branch = body.get('target_branch')
    decisions = body.get('decisions', {})

    if not source_version_id:
        return jsonify({'error': 'source_version_id 是必填项'}), 400
    if not target_branch:
        return jsonify({'error': 'target_branch 是必填项'}), 400

    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    merged_by = user.get('username', 'unknown')
    user_id = user.get('userId')

    try:
        result = apply_partial_merge(
            source_version_id=source_version_id,
            target_branch=target_branch,
            decisions=decisions,
            merged_by=merged_by,
            user_id=user_id,
        )
        if result['success']:
            return jsonify(result)
        else:
            return jsonify({'error': result['message']}), 400
    except Exception as e:
        return jsonify({'error': f'合并失败: {str(e)}'}), 500
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd E:/wsl/check/check-manage/server && python -m pytest tests/test_version.py::TestPartialMergeRoute -v`
Expected: 所有测试 PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/versions.py server/tests/test_version.py
git commit -m "feat: 添加部分合并 API 路由

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 3: 前端状态管理

### Task 6: useMergeState composable

**Files:**
- Create: `src/components/common/composables/useMergeState.ts`
- Create: `src/components/common/composables/index.ts`
- Test: `src/components/common/__tests__/useMergeState.test.ts`

- [ ] **Step 1: 编写 composable 测试**

创建 `src/components/common/__tests__/useMergeState.test.ts`：

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { useMergeState } from '../composables/useMergeState'
import type { DiffResult } from '@/types'

describe('useMergeState', () => {
  let mergeState: ReturnType<typeof useMergeState>

  const mockDiffResult: DiffResult = {
    added: [{ id: '1', name: '新增记录1' }],
    removed: [{ id: '2', name: '删除记录1' }],
    modified: [{
      id: '3',
      record: { id: '3', name: '修改后' },
      oldRecord: { id: '3', name: '修改前' },
      fields: [{ fieldName: 'name', oldValue: '修改前', newValue: '修改后' }]
    }],
    unchangedCount: 5,
    fields: []
  }

  beforeEach(() => {
    mergeState = useMergeState()
  })

  it('初始化状态正确', () => {
    expect(mergeState.state.step).toBe('overview')
    expect(mergeState.state.diffResult).toBeNull()
    expect(mergeState.hasChanges.value).toBe(false)
  })

  it('acceptAllSource 正确填充决策', () => {
    mergeState.state.diffResult = mockDiffResult
    mergeState.acceptAllSource()

    expect(mergeState.state.decisions.addedRecords.has('1')).toBe(true)
    expect(mergeState.state.decisions.removedRecords.has('2')).toBe(true)
    expect(mergeState.state.decisions.modifiedRecords.has('3')).toBe(true)
    expect(mergeState.hasChanges.value).toBe(true)
  })

  it('acceptAllTarget 正确清空决策', () => {
    mergeState.state.diffResult = mockDiffResult
    mergeState.acceptAllSource()
    mergeState.acceptAllTarget()

    expect(mergeState.state.decisions.addedRecords.size).toBe(0)
    expect(mergeState.state.decisions.removedRecords.size).toBe(0)
    expect(mergeState.hasChanges.value).toBe(false)
  })

  it('canSubmit 在有变更时为 true', () => {
    mergeState.state.diffResult = mockDiffResult
    mergeState.acceptAllSource()

    expect(mergeState.canSubmit.value).toBe(true)
  })

  it('canSubmit 在无变更时为 false', () => {
    expect(mergeState.canSubmit.value).toBe(false)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd E:/wsl/check/check-manage && npx vitest run src/components/common/__tests__/useMergeState.test.ts`
Expected: FAIL with "Cannot find module"

- [ ] **Step 3: 创建 composables 目录**

Run: `mkdir -p E:/wsl/check/check-manage/src/components/common/composables`

- [ ] **Step 4: 实现 useMergeState composable**

创建 `src/components/common/composables/useMergeState.ts`：

```typescript
/**
 * 版本合并状态管理 composable
 */
import { reactive, computed } from 'vue'
import type {
  CollectionVersion,
  DiffResult,
  MergeStep,
  MergeDecisions,
  MergeState
} from '@/types'
import { partialMergeVersion } from '@/api/version'
import type { PartialMergeRequest, PartialMergeResponse } from '@/types'

export function useMergeState() {
  const state = reactive<MergeState>({
    step: 'overview',
    sourceVersion: null,
    targetBranch: '',
    diffResult: null,
    decisions: {
      addedRecords: new Set(),
      removedRecords: new Set(),
      modifiedRecords: new Map()
    }
  })

  // 计算属性
  const hasChanges = computed(() =>
    state.decisions.addedRecords.size > 0 ||
    state.decisions.removedRecords.size > 0 ||
    state.decisions.modifiedRecords.size > 0
  )

  const canSubmit = computed(() => hasChanges.value)

  const addedCount = computed(() => state.diffResult?.added?.length ?? 0)
  const removedCount = computed(() => state.diffResult?.removed?.length ?? 0)
  const modifiedCount = computed(() => state.diffResult?.modified?.length ?? 0)

  // 快捷操作：全部接受源版本
  function acceptAllSource() {
    const diff = state.diffResult
    if (!diff) return

    // 新增记录：全部接受
    diff.added.forEach(r => state.decisions.addedRecords.add(r.id))

    // 删除记录：全部接受
    diff.removed.forEach(r => state.decisions.removedRecords.add(r.id))

    // 修改记录：全部选源版本
    diff.modified.forEach(r => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      r.fields.forEach(f => fieldDecisions.set(f.fieldName, 'source'))
      state.decisions.modifiedRecords.set(r.id, {
        recordId: r.id,
        fieldDecisions
      })
    })
  }

  // 快捷操作：全部接受目标版本
  function acceptAllTarget() {
    // 新增记录：全部拒绝（不合并）
    state.decisions.addedRecords.clear()

    // 删除记录：全部拒绝（保留）
    state.decisions.removedRecords.clear()

    // 修改记录：全部选目标版本
    state.diffResult?.modified.forEach(r => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      r.fields.forEach(f => fieldDecisions.set(f.fieldName, 'target'))
      state.decisions.modifiedRecords.set(r.id, {
        recordId: r.id,
        fieldDecisions
      })
    })
  }

  // 切换记录选择
  function toggleAddedRecord(id: string) {
    if (state.decisions.addedRecords.has(id)) {
      state.decisions.addedRecords.delete(id)
    } else {
      state.decisions.addedRecords.add(id)
    }
  }

  function toggleRemovedRecord(id: string) {
    if (state.decisions.removedRecords.has(id)) {
      state.decisions.removedRecords.delete(id)
    } else {
      state.decisions.removedRecords.add(id)
    }
  }

  function toggleModifiedRecord(id: string) {
    if (state.decisions.modifiedRecords.has(id)) {
      state.decisions.modifiedRecords.delete(id)
    } else {
      const record = state.diffResult?.modified.find(m => m.id === id)
      if (record) {
        const fieldDecisions = new Map<string, 'source' | 'target'>()
        record.fields.forEach(f => fieldDecisions.set(f.fieldName, 'source'))
        state.decisions.modifiedRecords.set(id, {
          recordId: id,
          fieldDecisions
        })
      }
    }
  }

  // 设置字段决策
  function setFieldDecision(recordId: string, fieldName: string, choice: 'source' | 'target') {
    const mod = state.decisions.modifiedRecords.get(recordId)
    if (mod) {
      mod.fieldDecisions.set(fieldName, choice)
    }
  }

  // 构建提交 payload
  function buildMergePayload(): PartialMergeRequest {
    const modifiedRecords: { record_id: string; field_values: Record<string, any> }[] = []

    state.decisions.modifiedRecords.forEach((mod, recordId) => {
      const record = state.diffResult?.modified.find(m => m.id === recordId)
      if (!record) return

      const fieldValues: Record<string, any> = {}
      mod.fieldDecisions.forEach((choice, fieldName) => {
        const field = record.fields.find(f => f.fieldName === fieldName)
        if (field) {
          fieldValues[fieldName] = choice === 'source' ? field.newValue : field.oldValue
        }
      })

      if (Object.keys(fieldValues).length > 0) {
        modifiedRecords.push({ record_id: recordId, field_values: fieldValues })
      }
    })

    return {
      source_version_id: state.sourceVersion!.id,
      target_branch: state.targetBranch,
      decisions: {
        added_record_ids: Array.from(state.decisions.addedRecords),
        removed_record_ids: Array.from(state.decisions.removedRecords),
        modified_records: modifiedRecords
      }
    }
  }

  // 提交合并
  async function submitMerge(): Promise<PartialMergeResponse> {
    const payload = buildMergePayload()
    return await partialMergeVersion(payload)
  }

  // 重置状态
  function reset() {
    state.step = 'overview'
    state.sourceVersion = null
    state.targetBranch = ''
    state.diffResult = null
    state.decisions.addedRecords.clear()
    state.decisions.removedRecords.clear()
    state.decisions.modifiedRecords.clear()
  }

  return {
    state,
    hasChanges,
    canSubmit,
    addedCount,
    removedCount,
    modifiedCount,
    acceptAllSource,
    acceptAllTarget,
    toggleAddedRecord,
    toggleRemovedRecord,
    toggleModifiedRecord,
    setFieldDecision,
    submitMerge,
    reset
  }
}
```

- [ ] **Step 5: 创建 index.ts 导出**

创建 `src/components/common/composables/index.ts`：

```typescript
export * from './useMergeState'
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd E:/wsl/check/check-manage && npx vitest run src/components/common/__tests__/useMergeState.test.ts`
Expected: 所有测试 PASS

- [ ] **Step 7: Commit**

```bash
git add src/components/common/composables src/components/common/__tests__/useMergeState.test.ts
git commit -m "feat: 实现 useMergeState composable

- 状态管理：步骤、diff 结果、用户决策
- 快捷操作：全部接受源/目标版本
- 字段级决策支持

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 4: 前端组件

### Task 7: StepOverview 组件

**Files:**
- Create: `src/components/common/StepOverview.vue`

- [ ] **Step 1: 实现 StepOverview 组件**

创建 `src/components/common/StepOverview.vue`：

```vue
/**
 * 合并向导步骤1：变更概览
 *
 * 职责：
 * - 显示变更统计（新增、删除、修改）
 * - 提供快捷操作按钮
 */
<template>
  <div class="step-overview">
    <h3 class="overview-title">变更概览</h3>

    <!-- 变更统计卡片 -->
    <div class="stats-cards">
      <div class="stat-card stat-added">
        <div class="stat-icon">
          <el-icon><Plus /></el-icon>
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ addedCount }}</span>
          <span class="stat-label">新增记录</span>
        </div>
      </div>

      <div class="stat-card stat-removed">
        <div class="stat-icon">
          <el-icon><Minus /></el-icon>
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ removedCount }}</span>
          <span class="stat-label">删除记录</span>
        </div>
      </div>

      <div class="stat-card stat-modified">
        <div class="stat-icon">
          <el-icon><Edit /></el-icon>
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ modifiedCount }}</span>
          <span class="stat-label">修改记录</span>
        </div>
      </div>
    </div>

    <!-- 快捷操作 -->
    <div class="quick-actions">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        <template #title>
          选择合并策略，或进入下一步逐条选择要合并的内容
        </template>
      </el-alert>

      <el-button-group>
        <el-button type="primary" @click="$emit('accept-source')">
          <el-icon><Check /></el-icon>
          全部接受源版本
        </el-button>
        <el-button @click="$emit('accept-target')">
          <el-icon><Close /></el-icon>
          全部保留当前版本
        </el-button>
      </el-button-group>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Plus, Minus, Edit, Check, Close } from '@element-plus/icons-vue'

defineProps<{
  addedCount: number
  removedCount: number
  modifiedCount: number
}>()

defineEmits<{
  'accept-source': []
  'accept-target': []
}>()
</script>

<style scoped>
.step-overview {
  padding: 20px;
}

.overview-title {
  margin: 0 0 20px;
  font-size: 16px;
  font-weight: 500;
}

.stats-cards {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  flex: 1;
  display: flex;
  align-items: center;
  padding: 16px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
}

.stat-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  margin-right: 12px;
  font-size: 24px;
}

.stat-added .stat-icon {
  background: rgba(103, 194, 58, 0.1);
  color: var(--el-color-success);
}

.stat-removed .stat-icon {
  background: rgba(245, 108, 108, 0.1);
  color: var(--el-color-danger);
}

.stat-modified .stat-icon {
  background: rgba(230, 162, 60, 0.1);
  color: var(--el-color-warning);
}

.stat-info {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.quick-actions {
  margin-top: 16px;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add src/components/common/StepOverview.vue
git commit -m "feat: 实现 StepOverview 组件

- 变更统计卡片展示
- 快捷操作按钮

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 8: StepRecordSelect 组件

**Files:**
- Create: `src/components/common/StepRecordSelect.vue`

- [ ] **Step 1: 实现 StepRecordSelect 组件**

创建 `src/components/common/StepRecordSelect.vue`：

```vue
/**
 * 合并向导步骤2：记录选择
 *
 * 职责：
 * - 显示新增/删除/修改记录列表
 * - 支持勾选要合并的记录
 */
<template>
  <div class="step-record-select">
    <el-tabs v-model="activeTab">
      <!-- 新增记录 -->
      <el-tab-pane label="新增记录" :name="'added'">
        <template #label>
          <span>
            新增
            <el-badge :value="addedRecords.length" type="success" />
          </span>
        </template>
        <el-table :data="addedRecords" stripe size="small" max-height="300">
          <el-table-column width="55">
            <template #default="{ row }">
              <el-checkbox
                :model-value="selectedAdded.has(row.id)"
                @change="$emit('toggle-added', row.id)"
              />
            </template>
          </el-table-column>
          <el-table-column prop="id" label="ID" width="180" />
          <el-table-column label="数据">
            <template #default="{ row }">
              <span class="record-preview">{{ formatRecord(row) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 删除记录 -->
      <el-tab-pane label="删除记录" :name="'removed'">
        <template #label>
          <span>
            删除
            <el-badge :value="removedRecords.length" type="danger" />
          </span>
        </template>
        <el-table :data="removedRecords" stripe size="small" max-height="300">
          <el-table-column width="55">
            <template #default="{ row }">
              <el-checkbox
                :model-value="selectedRemoved.has(row.id)"
                @change="$emit('toggle-removed', row.id)"
              />
            </template>
          </el-table-column>
          <el-table-column prop="id" label="ID" width="180" />
          <el-table-column label="数据">
            <template #default="{ row }">
              <span class="record-preview">{{ formatRecord(row) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 修改记录 -->
      <el-tab-pane label="修改记录" :name="'modified'">
        <template #label>
          <span>
            修改
            <el-badge :value="modifiedRecords.length" type="warning" />
          </span>
        </template>
        <el-table :data="modifiedRecords" stripe size="small" max-height="300">
          <el-table-column width="55">
            <template #default="{ row }">
              <el-checkbox
                :model-value="selectedModified.has(row.id)"
                @change="$emit('toggle-modified', row.id)"
              />
            </template>
          </el-table-column>
          <el-table-column prop="id" label="ID" width="180" />
          <el-table-column label="变更字段">
            <template #default="{ row }">
              <el-tag
                v-for="f in row.fields"
                :key="f.fieldName"
                size="small"
                style="margin-right: 4px"
              >
                {{ f.fieldName }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { DiffResult } from '@/types'

const props = defineProps<{
  diffResult: DiffResult | null
  selectedAdded: Set<string>
  selectedRemoved: Set<string>
  selectedModified: Set<string>
}>()

defineEmits<{
  'toggle-added': [id: string]
  'toggle-removed': [id: string]
  'toggle-modified': [id: string]
}>()

const activeTab = ref('added')

const addedRecords = computed(() => props.diffResult?.added ?? [])
const removedRecords = computed(() => props.diffResult?.removed ?? [])
const modifiedRecords = computed(() => props.diffResult?.modified ?? [])

function formatRecord(record: Record<string, any>): string {
  const entries = Object.entries(record)
    .filter(([k]) => k !== 'id')
    .slice(0, 3)
  return entries.map(([k, v]) => `${k}: ${v}`).join(', ')
}
</script>

<style scoped>
.step-record-select {
  padding: 0 20px;
}

.record-preview {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add src/components/common/StepRecordSelect.vue
git commit -m "feat: 实现 StepRecordSelect 组件

- 新增/删除/修改记录分 Tab 展示
- 支持勾选要合并的记录

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 9: StepFieldSelect 组件

**Files:**
- Create: `src/components/common/StepFieldSelect.vue`

- [ ] **Step 1: 实现 StepFieldSelect 组件**

创建 `src/components/common/StepFieldSelect.vue`：

```vue
/**
 * 合并向导步骤3：字段选择
 *
 * 职责：
 * - 显示修改记录的字段级差异
 * - 支持逐字段选择保留源/目标版本
 */
<template>
  <div class="step-field-select">
    <el-alert
      v-if="modifiedRecords.length === 0"
      type="info"
      :closable="false"
      show-icon
    >
      没有需要选择字段的修改记录
    </el-alert>

    <template v-else>
      <div class="record-list">
        <el-collapse v-model="activeRecords">
          <el-collapse-item
            v-for="record in modifiedRecords"
            :key="record.id"
            :name="record.id"
          >
            <template #title>
              <div class="record-header">
                <span class="record-id">ID: {{ record.id }}</span>
                <el-tag size="small" type="warning">
                  {{ record.fields.length }} 个字段变更
                </el-tag>
              </div>
            </template>

            <el-table :data="record.fields" stripe size="small">
              <el-table-column prop="fieldName" label="字段" width="120" />
              <el-table-column label="当前值" width="150">
                <template #default="{ row }">
                  <div class="field-value current">
                    {{ formatValue(row.oldValue) }}
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="源版本值" width="150">
                <template #default="{ row }">
                  <div class="field-value source">
                    {{ formatValue(row.newValue) }}
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="选择" width="180">
                <template #default="{ row }">
                  <el-radio-group
                    :model-value="getFieldDecision(record.id, row.fieldName)"
                    @change="(v: 'source' | 'target') => $emit('set-field', record.id, row.fieldName, v)"
                    size="small"
                  >
                    <el-radio-button value="target">保留当前</el-radio-button>
                    <el-radio-button value="source">使用源</el-radio-button>
                  </el-radio-group>
                </template>
              </el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { DiffResult, MergeDecisions } from '@/types'

const props = defineProps<{
  diffResult: DiffResult | null
  decisions: MergeDecisions
}>()

defineEmits<{
  'set-field': [recordId: string, fieldName: string, choice: 'source' | 'target']
}>()

const activeRecords = ref<string[]>([])

const modifiedRecords = computed(() => {
  const selected = Array.from(props.decisions.modifiedRecords.keys())
  return (props.diffResult?.modified ?? []).filter(r => selected.includes(r.id))
})

function getFieldDecision(recordId: string, fieldName: string): 'source' | 'target' {
  const mod = props.decisions.modifiedRecords.get(recordId)
  return mod?.fieldDecisions.get(fieldName) ?? 'target'
}

function formatValue(value: any): string {
  if (value === null || value === undefined) return '(空)'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
</script>

<style scoped>
.step-field-select {
  padding: 0 20px;
}

.record-list {
  max-height: 400px;
  overflow-y: auto;
}

.record-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.record-id {
  font-family: monospace;
  font-size: 12px;
}

.field-value {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.field-value.current {
  background: rgba(64, 158, 255, 0.1);
  color: var(--el-color-primary);
}

.field-value.source {
  background: rgba(103, 194, 58, 0.1);
  color: var(--el-color-success);
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add src/components/common/StepFieldSelect.vue
git commit -m "feat: 实现 StepFieldSelect 组件

- 折叠面板展示修改记录
- 字段级差异对比
- 单选按钮选择源/目标值

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 10: MergeConflictDialog 主组件

**Files:**
- Create: `src/components/common/MergeConflictDialog.vue`
- Modify: `src/components/common/index.ts`

- [ ] **Step 1: 实现 MergeConflictDialog 组件**

创建 `src/components/common/MergeConflictDialog.vue`：

```vue
/**
 * 合并冲突对话框
 *
 * 职责：
 * - 三步向导流程：概览 → 记录选择 → 字段选择
 * - 管理步骤流转
 * - 提交合并决策
 */
<template>
  <el-dialog
    v-model="visible"
    title="合并版本"
    width="800px"
    :close-on-click-modal="false"
    destroy-on-close
    @closed="handleClosed"
  >
    <!-- 步骤指示器 -->
    <div class="steps-container">
      <el-steps :active="stepIndex" align-center>
        <el-step title="概览" description="变更统计" />
        <el-step title="记录选择" description="选择要合并的记录" />
        <el-step title="字段选择" description="选择字段值" />
      </el-steps>
    </div>

    <!-- 步骤内容 -->
    <div class="step-content">
      <StepOverview
        v-if="mergeState.step === 'overview'"
        :added-count="addedCount"
        :removed-count="removedCount"
        :modified-count="modifiedCount"
        @accept-source="handleAcceptSource"
        @accept-target="handleAcceptTarget"
      />

      <StepRecordSelect
        v-else-if="mergeState.step === 'records'"
        :diff-result="mergeState.diffResult"
        :selected-added="mergeState.decisions.addedRecords"
        :selected-removed="mergeState.decisions.removedRecords"
        :selected-modified="mergeState.decisions.modifiedRecords"
        @toggle-added="toggleAddedRecord"
        @toggle-removed="toggleRemovedRecord"
        @toggle-modified="toggleModifiedRecord"
      />

      <StepFieldSelect
        v-else-if="mergeState.step === 'fields'"
        :diff-result="mergeState.diffResult"
        :decisions="mergeState.decisions"
        @set-field="setFieldDecision"
      />
    </div>

    <!-- 底部按钮 -->
    <template #footer>
      <div class="dialog-footer">
        <el-button @click="handleCancel">取消</el-button>
        <el-button
          v-if="mergeState.step !== 'overview'"
          @click="handlePrev"
        >
          上一步
        </el-button>
        <el-button
          v-if="mergeState.step !== 'fields'"
          type="primary"
          @click="handleNext"
        >
          下一步
        </el-button>
        <el-button
          v-else
          type="primary"
          :disabled="!canSubmit"
          :loading="submitting"
          @click="handleSubmit"
        >
          完成合并
        </el-button>
        <span v-if="mergeState.step === 'fields' && !canSubmit" class="submit-hint">
          请至少选择一项变更
        </span>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import StepOverview from './StepOverview.vue'
import StepRecordSelect from './StepRecordSelect.vue'
import StepFieldSelect from './StepFieldSelect.vue'
import { useMergeState } from './composables/useMergeState'
import { diffVersions } from '@/api/version'
import type { CollectionVersion, DiffResult } from '@/types'

const props = defineProps<{
  modelValue: boolean
  sourceVersion: CollectionVersion | null
  targetBranch: string
  collection: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'success': []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v)
})

const mergeState = useMergeState()
const submitting = ref(false)
const loading = ref(false)

const stepIndex = computed(() => {
  const steps = ['overview', 'records', 'fields']
  return steps.indexOf(mergeState.state.step)
})

// 步骤跳转
const stepOrder = ['overview', 'records', 'fields'] as const

function goToStep(step: typeof stepOrder[number]) {
  mergeState.state.step = step
}

function handleNext() {
  const idx = stepOrder.indexOf(mergeState.state.step)
  if (idx < stepOrder.length - 1) {
    goToStep(stepOrder[idx + 1])
  }
}

function handlePrev() {
  const idx = stepOrder.indexOf(mergeState.state.step)
  if (idx > 0) {
    goToStep(stepOrder[idx - 1])
  }
}

// 快捷操作
function handleAcceptSource() {
  mergeState.acceptAllSource()
  goToStep('fields')
}

function handleAcceptTarget() {
  mergeState.acceptAllTarget()
  goToStep('fields')
}

// 记录选择
function toggleAddedRecord(id: string) {
  mergeState.toggleAddedRecord(id)
}

function toggleRemovedRecord(id: string) {
  mergeState.toggleRemovedRecord(id)
}

function toggleModifiedRecord(id: string) {
  mergeState.toggleModifiedRecord(id)
}

// 字段选择
function setFieldDecision(recordId: string, fieldName: string, choice: 'source' | 'target') {
  mergeState.setFieldDecision(recordId, fieldName, choice)
}

// 取消
function handleCancel() {
  visible.value = false
}

// 关闭后重置
function handleClosed() {
  mergeState.reset()
}

// 提交
async function handleSubmit() {
  submitting.value = true
  try {
    const result = await mergeState.submitMerge()
    if (result.success) {
      ElMessage.success(result.message)
      visible.value = false
      emit('success')
    } else {
      ElMessage.error(result.message)
    }
  } catch (e: any) {
    ElMessage.error(e.message || '合并失败')
  } finally {
    submitting.value = false
  }
}

// 初始化：加载 diff
watch(visible, async (v) => {
  if (v && props.sourceVersion) {
    loading.value = true
    mergeState.state.sourceVersion = props.sourceVersion
    mergeState.state.targetBranch = props.targetBranch

    try {
      const diffResult = await diffVersions({
        collection: props.collection,
        baseVersion: 'current',
        targetVersion: props.sourceVersion.id
      })
      mergeState.state.diffResult = diffResult
    } catch (e: any) {
      ElMessage.error('加载差异信息失败')
      visible.value = false
    } finally {
      loading.value = false
    }
  }
})

const { canSubmit, addedCount, removedCount, modifiedCount } = mergeState
</script>

<style scoped>
.steps-container {
  margin-bottom: 24px;
}

.step-content {
  min-height: 300px;
}

.dialog-footer {
  display: flex;
  align-items: center;
  gap: 8px;
}

.submit-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
```

- [ ] **Step 2: 更新 index.ts 导出**

修改 `src/components/common/index.ts`：

```typescript
// 在文件末尾添加
export { default as MergeConflictDialog } from './MergeConflictDialog.vue'
export { default as StepOverview } from './StepOverview.vue'
export { default as StepRecordSelect } from './StepRecordSelect.vue'
export { default as StepFieldSelect } from './StepFieldSelect.vue'
```

- [ ] **Step 3: Commit**

```bash
git add src/components/common/MergeConflictDialog.vue src/components/common/index.ts
git commit -m "feat: 实现 MergeConflictDialog 主组件

- 三步向导流程
- 步骤跳转支持
- 集成 diff 加载和合并提交

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 5: 集成到 VersionManager

### Task 11: 集成合并对话框

**Files:**
- Modify: `src/components/common/VersionManager.vue`

- [ ] **Step 1: 修改 VersionManager 集成新对话框**

在 `VersionManager.vue` 的 `<script setup>` 部分添加：

```typescript
import MergeConflictDialog from './MergeConflictDialog.vue'

// 合并对话框状态
const showMergeDialog = ref(false)
const mergeSourceVersion = ref<CollectionVersion | null>(null)

// 打开合并对话框
function openMergeDialog(version: CollectionVersion) {
  mergeSourceVersion.value = version
  showMergeDialog.value = true
}

// 合并成功后刷新
function handleMergeSuccess() {
  loadVersions()
  emit('data-changed')
}
```

修改合并按钮的处理，将原来的直接调用 `mergeVersion` 改为打开对话框：

```vue
<!-- 在模板中替换原有的合并按钮逻辑 -->
<el-button
  type="primary"
  size="small"
  @click="openMergeDialog(row)"
>
  合并
</el-button>

<!-- 在模板末尾添加合并对话框 -->
<MergeConflictDialog
  v-model="showMergeDialog"
  :source-version="mergeSourceVersion"
  :target-branch="currentBranch?.branchId || 'main'"
  :collection="collection"
  @success="handleMergeSuccess"
/>
```

- [ ] **Step 2: 运行前端测试**

Run: `cd E:/wsl/check/check-manage && npx vitest run`
Expected: 所有测试 PASS

- [ ] **Step 3: Commit**

```bash
git add src/components/common/VersionManager.vue
git commit -m "feat: 集成 MergeConflictDialog 到 VersionManager

- 替换原有合并逻辑为新的冲突解决对话框
- 合并成功后刷新版本列表

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 12: 集成测试

**Files:**
- Create: `src/components/common/__tests__/MergeConflictDialog.test.ts`

- [ ] **Step 1: 编写集成测试**

创建 `src/components/common/__tests__/MergeConflictDialog.test.ts`：

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import MergeConflictDialog from '../MergeConflictDialog.vue'
import type { CollectionVersion, DiffResult } from '@/types'

vi.mock('@/api/version', () => ({
  diffVersions: vi.fn().mockResolvedValue({
    added: [{ id: '1', name: '新增1' }],
    removed: [{ id: '2', name: '删除1' }],
    modified: [],
    unchangedCount: 5,
    fields: []
  } as DiffResult),
  partialMergeVersion: vi.fn().mockResolvedValue({
    success: true,
    merged_count: 2,
    message: '成功合并 2 条记录'
  })
}))

describe('MergeConflictDialog', () => {
  const mockVersion: CollectionVersion = {
    id: 'version-1',
    collection: 'test',
    name: '测试版本',
    versionType: 'snapshot',
    status: 'active',
    dataHash: 'hash123',
    recordsCount: 10,
    relationsCount: 5,
    createdBy: 'admin',
    createdAt: '2024-01-01T00:00:00Z',
    isProtected: false
  }

  it('正确渲染步骤指示器', async () => {
    const wrapper = mount(MergeConflictDialog, {
      props: {
        modelValue: true,
        sourceVersion: mockVersion,
        targetBranch: 'main',
        collection: 'test'
      },
      global: {
        plugins: [createTestingPinia()],
        stubs: {
          StepOverview: true,
          StepRecordSelect: true,
          StepFieldSelect: true
        }
      }
    })

    expect(wrapper.find('.el-steps').exists()).toBe(true)
  })

  it('步骤流转正确', async () => {
    const wrapper = mount(MergeConflictDialog, {
      props: {
        modelValue: true,
        sourceVersion: mockVersion,
        targetBranch: 'main',
        collection: 'test'
      },
      global: {
        plugins: [createTestingPinia()],
        stubs: {
          StepOverview: true,
          StepRecordSelect: true,
          StepFieldSelect: true
        }
      }
    })

    // 初始步骤为概览
    expect(wrapper.vm.mergeState.state.step).toBe('overview')
  })
})
```

- [ ] **Step 2: 运行集成测试**

Run: `cd E:/wsl/check/check-manage && npx vitest run src/components/common/__tests__/MergeConflictDialog.test.ts`
Expected: 所有测试 PASS

- [ ] **Step 3: Commit**

```bash
git add src/components/common/__tests__/MergeConflictDialog.test.ts
git commit -m "test: 添加 MergeConflictDialog 集成测试

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 13: 最终验证

- [ ] **Step 1: 运行所有前端测试**

Run: `cd E:/wsl/check/check-manage && npm run test`
Expected: 所有测试 PASS

- [ ] **Step 2: 运行所有后端测试**

Run: `cd E:/wsl/check/check-manage && npm run test:server`
Expected: 所有测试 PASS

- [ ] **Step 3: 启动开发服务器验证**

Run: `cd E:/wsl/check/check-manage && npm run dev:all`

手动验证步骤：
1. 打开任意动态数据页
2. 打开版本管理抽屉
3. 选择一个版本点击"合并"
4. 验证合并对话框正常打开
5. 验证步骤流转正常
6. 验证快捷操作正常
7. 验证记录选择正常
8. 验证字段选择正常
9. 验证提交合并正常

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "feat: 完成版本合并冲突解决功能

- 支持记录级和字段级合并选择
- 三步向导流程（概览→记录选择→字段选择）
- 快捷操作：全部接受源/目标版本
- 事务原子性保证
- 同步处理关系数据

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```