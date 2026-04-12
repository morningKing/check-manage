# 跨 Collection 版本删除实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过新增 `version_collections` 表精确追踪版本涉及的Collection，实现两阶段删除确认机制，避免孤立数据产生。

**Architecture:** 新增独立追踪表记录版本涉及的所有Collection，改造删除流程为先查询影响报告→用户确认→精确清理，前端分级展示数据详情。

**Tech Stack:** Python Flask + psycopg2 (后端), Vue 3 + Element Plus (前端), PostgreSQL (数据库)

---

## Task 1: 创建 version_collections 数据库表

**Files:**
- Modify: `server/init_db.py:209-235` (新增表定义)
- Test: `server/tests/test_version_collections.py` (验证表创建)

- [ ] **Step 1: 编写表创建验证测试**

```python
"""
验证 version_collections 表创建
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db


def test_version_collections_table_exists():
    """验证 version_collections 表存在"""
    with get_db() as conn:
        cur = conn.cursor()
        
        # 查询表是否存在
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT FROM information_schema.tables "
            "  WHERE table_schema = 'public' "
            "  AND table_name = 'version_collections'"
            ")"
        )
        exists = cur.fetchone()[0]
        
        assert exists, 'version_collections 表应该存在'
        
        # 验证表结构
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'version_collections' "
            "ORDER BY ordinal_position"
        )
        columns = cur.fetchall()
        
        expected_columns = [
            ('version_id', 'character varying', 'NO'),
            ('collection', 'character varying', 'NO'),
            ('created_at', 'timestamp with time zone', 'YES'),
        ]
        
        assert len(columns) == 3, f'应该有3列，实际{len(columns)}列'
        for i, (col_name, col_type, nullable) in enumerate(expected_columns):
            assert columns[i][0] == col_name, f'列名应为{col_name}'
            assert columns[i][1] == col_type, f'列类型应为{col_type}'
        
    print('[OK] version_collections 表结构验证通过')


if __name__ == '__main__':
    test_version_collections_table_exists()
    print('\n表创建测试通过！')
```

- [ ] **Step 2: 运行测试确认表不存在**

Run: `cd server && python tests/test_version_collections.py`
Expected: FAIL with "version_collections 表应该存在"

- [ ] **Step 3: 在 init_db.py 中添加表定义**

在 `server/init_db.py` 第235行后添加：

```python
# version_collections 表：追踪版本涉及的Collection
CREATE TABLE IF NOT EXISTS version_collections (
    version_id  VARCHAR(100) NOT NULL,
    collection  VARCHAR(200) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (version_id, collection),
    FOREIGN KEY (version_id) REFERENCES collection_versions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_version_collections_version ON version_collections(version_id);
CREATE INDEX IF NOT EXISTS idx_version_collections_collection ON version_collections(collection);
```

- [ ] **Step 4: 运行 init_db.py 创建表**

Run: `cd server && python init_db.py`
Expected: 表创建成功，无错误输出

- [ ] **Step 5: 运行测试验证表创建**

Run: `cd server && python tests/test_version_collections.py`
Expected: PASS

- [ ] **Step 6: 提交表创建**

```bash
git add server/init_db.py server/tests/test_version_collections.py
git commit -m "feat: add version_collections table for tracking cross-collection versions"
```

---

## Task 2: 实现 Collection 追踪函数

**Files:**
- Modify: `server/utils/version.py` (新增 track_version_collections 函数)
- Test: `server/tests/test_version_collections.py` (扩展测试)

- [ ] **Step 1: 编写单Collection追踪测试**

在 `server/tests/test_version_collections.py` 中添加：

```python
def test_track_single_collection():
    """测试单Collection版本的追踪"""
    from utils.version import create_version_snapshot, delete_version, track_version_collections
    import psycopg2.extras
    from datetime import datetime, timezone
    
    collection = 'inspection-case'
    test_user = 'test_user_track_single'
    
    # 1. 创建版本分支
    version_info = create_version_snapshot(
        collection=collection,
        name='单Collection追踪测试',
        description='测试单Collection追踪',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']
    
    # 2. 在版本分支中添加测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-track-001', collection, psycopg2.extras.Json({'caseName': '测试用例'}), version_id, 1)
        )
        conn.commit()
    
    # 3. 手动调用追踪（模拟创建版本后的追踪）
    track_version_collections(version_id, collection, version_id)
    
    # 4. 验证追踪结果
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        tracked = [row[0] for row in cur.fetchall()]
        
        assert len(tracked) == 1, f'应追踪到1个Collection，实际{len(tracked)}'
        assert tracked[0] == collection, f'应为{collection}'
    
    # 5. 清理
    delete_version(version_id, confirmed=True)
    print('[OK] 单Collection追踪测试通过')
```

- [ ] **Step 2: 运行测试确认函数不存在**

Run: `cd server && python tests/test_version_collections.py::test_track_single_collection`
Expected: FAIL with "cannot import name 'track_version_collections'"

- [ ] **Step 3: 实现 track_version_collections 函数**

在 `server/utils/version.py` 第288行后添加：

```python
def track_version_collections(version_id, collection, branch_id):
    """
    追踪版本涉及的所有 Collection
    
    Parameters
    ----------
    version_id : str
        版本 ID
    collection : str
        版本创建时的主 Collection
    branch_id : str
        分支 ID
    """
    now = datetime.now(timezone.utc)
    
    with get_db() as conn:
        cur = conn.cursor()
        
        # 1. 扫描直接数据（dynamic_data）
        cur.execute(
            'SELECT DISTINCT collection FROM dynamic_data WHERE branch_id = %s',
            (branch_id,)
        )
        direct_collections = [row[0] for row in cur.fetchall()]
        
        # 2. 扫描关联数据（data_relations）
        cur.execute(
            'SELECT DISTINCT related_collection FROM data_relations WHERE branch_id = %s',
            (branch_id,)
        )
        related_collections = [row[0] for row in cur.fetchall()]
        
        # 3. 合并去重
        all_collections = set(direct_collections + related_collections)
        
        # 如果没有任何数据，至少记录主Collection
        if not all_collections:
            all_collections = {collection}
        
        # 4. 插入追踪数据
        for coll in all_collections:
            cur.execute(
                'INSERT INTO version_collections (version_id, collection, created_at) '
                'VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                (version_id, coll, now)
            )
```

- [ ] **Step 4: 运行测试验证单Collection追踪**

Run: `cd server && python tests/test_version_collections.py`
Expected: PASS（包括 test_version_collections_table_exists 和 test_track_single_collection）

- [ ] **Step 5: 编写跨Collection追踪测试**

在 `server/tests/test_version_collections.py` 中添加：

```python
def test_track_cross_collection():
    """测试跨Collection版本的追踪"""
    from utils.version import create_version_snapshot, delete_version, track_version_collections
    import psycopg2.extras
    
    collection = 'inspection-case'
    test_user = 'test_user_track_cross'
    
    # 1. 创建版本
    version_info = create_version_snapshot(
        collection=collection,
        name='跨Collection追踪测试',
        description='测试跨Collection追踪',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']
    
    # 2. 添加跨Collection数据
    with get_db() as conn:
        cur = conn.cursor()
        
        # inspection-case 数据
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-track-case-001', collection, psycopg2.extras.Json({'caseName': '测试用例'}), version_id, 1)
        )
        
        # inspection-plan 数据（跨Collection）
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-track-plan-001', 'inspection-plan', psycopg2.extras.Json({'planName': '测试计划'}), version_id, 1)
        )
        
        # 关联关系
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection, 'test-track-case-001', 'relatedPlan', 'inspection-plan', 'test-track-plan-001', version_id)
        )
        
        conn.commit()
    
    # 3. 追踪
    track_version_collections(version_id, collection, version_id)
    
    # 4. 验证追踪到2个Collection
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        tracked = [row[0] for row in cur.fetchall()]
        
        assert len(tracked) == 2, f'应追踪到2个Collection，实际{len(tracked)}'
        assert 'inspection-case' in tracked
        assert 'inspection-plan' in tracked
    
    # 5. 清理
    delete_version(version_id, confirmed=True)
    print('[OK] 跨Collection追踪测试通过')
```

- [ ] **Step 6: 运行跨Collection追踪测试**

Run: `cd server && python tests/test_version_collections.py`
Expected: PASS（3个测试全部通过）

- [ ] **Step 7: 提交追踪函数**

```bash
git add server/utils/version.py server/tests/test_version_collections.py
git commit -m "feat: implement track_version_collections function with tests"
```

---

## Task 3: 实现获取删除影响报告函数

**Files:**
- Modify: `server/utils/version.py` (新增 get_version_delete_impact 函数)
- Test: `server/tests/test_version_collections.py` (扩展测试)

- [ ] **Step 1: 编写影响报告测试**

在 `server/tests/test_version_collections.py` 中添加：

```python
def test_get_delete_impact():
    """测试删除影响报告生成"""
    from utils.version import create_version_snapshot, delete_version, track_version_collections, get_version_delete_impact
    import psycopg2.extras
    
    collection = 'inspection-case'
    test_user = 'test_user_impact'
    
    # 1. 创建版本并添加数据
    version_info = create_version_snapshot(
        collection=collection,
        name='影响报告测试版本',
        description='测试影响报告',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']
    
    with get_db() as conn:
        cur = conn.cursor()
        
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-impact-case-001', collection, psycopg2.extras.Json({'caseName': '测试用例A'}), version_id, 1)
        )
        
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-impact-plan-001', 'inspection-plan', psycopg2.extras.Json({'planName': '测试计划B'}), version_id, 1)
        )
        
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection, 'test-impact-case-001', 'relatedPlan', 'inspection-plan', 'test-impact-plan-001', version_id)
        )
        
        conn.commit()
    
    track_version_collections(version_id, collection, version_id)
    
    # 2. 获取影响报告
    impact = get_version_delete_impact(version_id)
    
    # 3. 验证报告结构
    assert 'versionInfo' in impact
    assert 'affectedCollections' in impact
    assert 'totalRecords' in impact
    assert 'hasCrossCollectionData' in impact
    assert 'warningMessage' in impact
    
    assert impact['totalRecords'] == 2
    assert impact['hasCrossCollectionData'] == True
    assert len(impact['affectedCollections']) == 2
    
    # 4. 验证数据详情
    for item in impact['affectedCollections']:
        assert 'collection' in item
        assert 'recordCount' in item
        assert 'records' in item
        
        if item['collection'] == 'inspection-case':
            assert item['recordCount'] == 1
            assert len(item['records']) == 1
            assert item['records'][0]['displayName'] == '测试用例A'
        
        if item['collection'] == 'inspection-plan':
            assert item['recordCount'] == 1
            assert len(item['records']) == 1
            assert item['records'][0]['displayName'] == '测试计划B'
    
    # 5. 清理
    delete_version(version_id, confirmed=True)
    print('[OK] 影响报告测试通过')
```

- [ ] **Step 2: 运行测试确认函数不存在**

Run: `cd server && python tests/test_version_collections.py::test_get_delete_impact`
Expected: FAIL with "cannot import name 'get_version_delete_impact'"

- [ ] **Step 3: 实现 get_version_delete_impact 函数**

在 `server/utils/version.py` 的 `track_version_collections` 函数后添加：

```python
def get_version_delete_impact(version_id):
    """
    获取删除版本的影响范围报告
    
    Parameters
    ----------
    version_id : str
        版本 ID
        
    Returns
    -------
    dict
        {
            'versionInfo': {...},
            'affectedCollections': [...],
            'totalRecords': N,
            'totalRelations': M,
            'hasCrossCollectionData': bool,
            'warningMessage': str
        }
    """
    with get_db() as conn:
        cur = conn.cursor()
        
        # 1. 获取版本基本信息
        cur.execute(
            'SELECT id, name, collection, version_type, records_count, relations_count '
            'FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        
        version_info = {
            'id': row[0],
            'name': row[1],
            'collection': row[2],
            'versionType': row[3],
            'recordsCount': row[4],
            'relationsCount': row[5]
        }
        
        # 2. 查询涉及的 Collection
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        collections = [row[0] for row in cur.fetchall()]
        
        # 3. 查询每个 Collection 的数据详情
        affected_collections = []
        for coll in collections:
            # 统计总数
            cur.execute(
                'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )
            total_count = cur.fetchone()[0]
            
            # 查询数据详情（前100条）
            cur.execute(
                'SELECT id, data, created_at, updated_at '
                'FROM dynamic_data '
                'WHERE collection = %s AND branch_id = %s '
                'ORDER BY created_at DESC LIMIT 100',
                (coll, version_id)
            )
            data_rows = cur.fetchall()
            
            records = []
            for data_row in data_rows:
                record_id = data_row[0]
                data_json = data_row[1] or {}
                
                display_name = (
                    data_json.get('name') or 
                    data_json.get('title') or 
                    data_json.get('caseName') or
                    data_json.get('planName') or
                    record_id
                )
                
                records.append({
                    'id': record_id,
                    'displayName': display_name,
                    'createdAt': data_row[2].isoformat() if data_row[2] else None,
                    'updatedAt': data_row[3].isoformat() if data_row[3] else None
                })
            
            affected_collections.append({
                'collection': coll,
                'recordCount': total_count,
                'records': records,
                'hasMore': total_count > 100
            })
        
        # 4. 统计关联关系数量
        cur.execute(
            'SELECT COUNT(*) FROM data_relations WHERE branch_id = %s',
            (version_id,)
        )
        total_relations = cur.fetchone()[0]
        
        # 5. 生成警告信息
        has_cross = len(collections) > 1
        warning_msg = ''
        if has_cross:
            collection_list = ', '.join([
                f"{item['collection']}({item['recordCount']}条)" 
                for item in affected_collections
            ])
            warning_msg = (
                f'该版本涉及 {len(collections)} 个 Collection 的数据：\n'
                f'{collection_list}\n'
                f'删除将同时清理这些数据及 {total_relations} 条关联关系。'
            )
        else:
            warning_msg = f'将删除 {affected_collections[0]["collection"]} 的 {affected_collections[0]["recordCount"]} 条数据'
        
        return {
            'versionInfo': version_info,
            'affectedCollections': affected_collections,
            'totalRecords': sum(item['recordCount'] for item in affected_collections),
            'totalRelations': total_relations,
            'hasCrossCollectionData': has_cross,
            'warningMessage': warning_msg
        }
```

- [ ] **Step 4: 运行影响报告测试**

Run: `cd server && python tests/test_version_collections.py`
Expected: PASS（4个测试全部通过）

- [ ] **Step 5: 提交影响报告函数**

```bash
git add server/utils/version.py server/tests/test_version_collections.py
git commit -m "feat: implement get_version_delete_impact function with tests"
```

---

## Task 4: 改造 delete_version 函数支持确认机制

**Files:**
- Modify: `server/utils/version.py:480-524` (改造 delete_version)
- Test: `server/tests/test_version_collections.py` (扩展测试)

- [ ] **Step 1: 编写两阶段删除测试**

在 `server/tests/test_version_collections.py` 中添加：

```python
def test_delete_with_confirmation():
    """测试两阶段确认删除"""
    from utils.version import create_version_snapshot, delete_version, track_version_collections
    import psycopg2.extras
    
    collection = 'inspection-case'
    test_user = 'test_user_confirm'
    
    # 1. 创建版本并添加跨Collection数据
    version_info = create_version_snapshot(
        collection=collection,
        name='确认删除测试版本',
        description='测试确认删除',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-confirm-case-001', collection, psycopg2.extras.Json({'caseName': '测试'}), version_id, 1)
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-confirm-plan-001', 'inspection-plan', psycopg2.extras.Json({'planName': '测试'}), version_id, 1)
        )
        conn.commit()
    
    track_version_collections(version_id, collection, version_id)
    
    # 2. 测试未确认时返回影响报告
    result = delete_version(version_id, confirmed=False)
    
    assert isinstance(result, dict), '未确认时应返回dict'
    assert result['totalRecords'] == 2
    assert result['hasCrossCollectionData'] == True
    
    # 3. 验证数据仍然存在
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM dynamic_data WHERE branch_id = %s', (version_id,))
        count = cur.fetchone()[0]
        assert count == 2, '未确认时数据应保留'
    
    # 4. 测试确认后删除
    success = delete_version(version_id, confirmed=True)
    assert success == True
    
    # 5. 验证所有数据被清理（关键：跨Collection清理）
    with get_db() as conn:
        cur = conn.cursor()
        
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, version_id)
        )
        case_count = cur.fetchone()[0]
        assert case_count == 0, 'inspection-case应被删除'
        
        # 关键验证：inspection-plan也应该被删除
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            ('inspection-plan', version_id)
        )
        plan_count = cur.fetchone()[0]
        assert plan_count == 0, 'inspection-plan应被删除（跨Collection清理）'
        
        cur.execute(
            'SELECT COUNT(*) FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        vc_count = cur.fetchone()[0]
        assert vc_count == 0, 'version_collections应被CASCADE清理'
    
    print('[OK] 两阶段确认删除测试通过')
```

- [ ] **Step 2: 改造 delete_version 函数**

修改 `server/utils/version.py` 第480-524行的 `delete_version` 函数：

```python
def delete_version(version_id, confirmed=False):
    """
    删除版本（改造版：支持用户确认机制）
    
    Parameters
    ----------
    version_id : str
        版本 ID
    confirmed : bool
        是否已确认删除（前端确认后传入 True）
        
    Returns
    -------
    dict | bool
        如果 confirmed=False，返回影响报告 dict
        如果 confirmed=True，返回删除成功 bool
    """
    # 未确认：返回影响报告
    if not confirmed:
        return get_version_delete_impact(version_id)
    
    # 已确认：执行删除
    with get_db() as conn:
        cur = conn.cursor()
        
        # 1. 检查版本状态
        cur.execute(
            'SELECT is_protected, collection, version_type FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            return False
        if row[0]:
            raise ValueError('无法删除受保护的版本')
        collection = row[1]
        version_type = row[2]
        
        # 2. 检查子版本
        cur.execute(
            'SELECT COUNT(*) FROM collection_versions WHERE parent_version = %s',
            (version_id,)
        )
        child_count = cur.fetchone()[0]
        if child_count > 0:
            raise ValueError(f'无法删除：存在 {child_count} 个子版本')
        
        # 3. 如果是分支，精确清理数据
        if version_type == 'branch':
            # 查询涉及的 Collection
            cur.execute(
                'SELECT collection FROM version_collections WHERE version_id = %s',
                (version_id,)
            )
            collections = [row[0] for row in cur.fetchall()]
            
            # 精确清理每个 Collection
            for coll in collections:
                cur.execute(
                    'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                    (coll, version_id)
                )
            
            # 清理关联关系
            cur.execute(
                'DELETE FROM data_relations WHERE branch_id = %s',
                (version_id,)
            )
            
            # 清理用户分支设置
            cur.execute(
                'DELETE FROM user_current_branch WHERE branch_id = %s',
                (version_id,)
            )
        
        # 4. 删除版本元数据（CASCADE 清理 version_collections）
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        
    return True
```

- [ ] **Step 3: 运行两阶段删除测试**

Run: `cd server && python tests/test_version_collections.py`
Expected: PASS（5个测试全部通过）

- [ ] **Step 4: 提交改造后的删除函数**

```bash
git add server/utils/version.py server/tests/test_version_collections.py
git commit -m "feat: refactor delete_version with two-phase confirmation"
```

---

## Task 5: 新增删除影响报告 API

**Files:**
- Modify: `server/routes/versions.py` (新增 API 端点)

- [ ] **Step 1: 新增 GET /api/versions/<id>/delete-impact API**

在 `server/routes/versions.py` 第95行后添加：

```python
@versions_bp.route('/versions/<version_id>/delete-impact', methods=['GET'])
@login_required
def get_delete_impact_route(version_id):
    """
    获取删除版本的影响范围报告
    
    前端调用此接口获取影响范围，展示确认对话框
    """
    try:
        from utils.version import get_version_delete_impact
        
        impact = get_version_delete_impact(version_id)
        return jsonify({
            'success': True,
            'data': impact
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except Exception as e:
        logger.error(f'获取删除影响报告失败: {str(e)}')
        return jsonify({'success': False, 'error': f'服务器错误: {str(e)}'}), 500
```

- [ ] **Step 2: 新增 GET /api/versions/<id>/delete-detail API**

继续在 `versions.py` 中添加：

```python
@versions_bp.route('/versions/<version_id>/delete-detail', methods=['GET'])
@login_required
def get_delete_detail_route(version_id):
    """
    获取删除数据详情（支持分页）
    
    Query参数：
      - collection: 查询哪个Collection
      - page: 页码（默认1）
      - pageSize: 每页数量（默认20）
      - sortBy: 排序字段（默认createdAt）
      - sortOrder: 排序方向（默认desc）
    """
    from db import get_db
    
    collection = request.args.get('collection')
    if not collection:
        return jsonify({'error': 'collection 是必填项'}), 400
    
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))
    sort_by = request.args.get('sortBy', 'createdAt')
    sort_order = request.args.get('sortOrder', 'desc')
    
    # 验证分页参数
    if page < 1:
        page = 1
    if page_size not in (10, 20, 50, 100):
        page_size = 20
    
    with get_db() as conn:
        cur = conn.cursor()
        
        # 统计总数
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, version_id)
        )
        total_count = cur.fetchone()[0]
        
        # 计算偏移量
        offset = (page - 1) * page_size
        
        # 排序字段验证
        allowed_sort_fields = {'createdAt': 'created_at', 'updatedAt': 'updated_at', 'id': 'id'}
        sort_column = allowed_sort_fields.get(sort_by, 'created_at')
        sort_direction = 'DESC' if sort_order == 'desc' else 'ASC'
        
        # 查询数据（分页）
        cur.execute(
            f'SELECT id, data, created_at, updated_at '
            f'FROM dynamic_data '
            f'WHERE collection = %s AND branch_id = %s '
            f'ORDER BY {sort_column} {sort_direction} '
            f'LIMIT %s OFFSET %s',
            (collection, version_id, page_size, offset)
        )
        data_rows = cur.fetchall()
        
        records = []
        for data_row in data_rows:
            record_id = data_row[0]
            data_json = data_row[1] or {}
            
            display_name = (
                data_json.get('name') or 
                data_json.get('title') or 
                data_json.get('caseName') or
                data_json.get('planName') or
                record_id
            )
            
            records.append({
                'id': record_id,
                'displayName': display_name,
                'createdAt': data_row[2].isoformat() if data_row[2] else None,
                'updatedAt': data_row[3].isoformat() if data_row[3] else None
            })
        
        total_pages = (total_count + page_size - 1) // page_size
    
    return jsonify({
        'success': True,
        'data': {
            'collection': collection,
            'versionId': version_id,
            'totalCount': total_count,
            'totalPages': total_pages,
            'currentPage': page,
            'pageSize': page_size,
            'records': records,
            'hasMore': page < total_pages
        }
    })
```

- [ ] **Step 3: 提交新增API**

```bash
git add server/routes/versions.py
git commit -m "feat: add delete-impact and delete-detail API endpoints"
```

---

## Task 6: 改造删除版本 API

**Files:**
- Modify: `server/routes/versions.py:95-106` (改造 delete_version_route)

- [ ] **Step 1: 改造 DELETE /api/versions/<id> API**

修改 `server/routes/versions.py` 第95-106行的 `delete_version_route` 函数：

```python
@versions_bp.route('/versions/<version_id>', methods=['DELETE'])
@write_required
def delete_version_route(version_id):
    """
    删除版本（改造版：支持确认机制）
    
    Query参数：
      confirmed: bool
        - false: 返回影响报告（不执行删除）
        - true: 执行删除（用户已确认）
    """
    confirmed = request.args.get('confirmed', 'false').lower() == 'true'
    
    try:
        result = delete_version(version_id, confirmed=confirmed)
        
        if confirmed:
            # 已确认并执行删除
            return jsonify({
                'success': True,
                'message': '版本已删除'
            })
        else:
            # 未确认，返回影响报告
            return jsonify({
                'success': True,
                'data': result,
                'requiresConfirmation': True
            })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f'删除版本失败: {str(e)}')
        return jsonify({'error': f'服务器错误: {str(e)}'}), 500
```

- [ ] **Step 2: 提交API改造**

```bash
git add server/routes/versions.py
git commit -m "feat: refactor DELETE /api/versions/<id> with confirmed parameter"
```

---

## Task 7: 编写数据迁移脚本

**Files:**
- Create: `server/scripts/migrate_version_collections.py`

- [ ] **Step 1: 编写迁移脚本**

创建 `server/scripts/migrate_version_collections.py`：

```python
"""
数据迁移脚本：为现有版本补充 version_collections 数据

执行时机：部署新功能后一次性运行
影响范围：所有 branch 类型的版本
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
from datetime import datetime, timezone
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_version_collections():
    """为现有版本补充 Collection 追踪数据"""
    logger.info('开始迁移 version_collections 数据...')
    
    with get_db() as conn:
        cur = conn.cursor()
        
        # 1. 查询所有活跃的分支版本
        cur.execute(
            'SELECT id, collection FROM collection_versions '
            'WHERE version_type = %s AND status != %s',
            ('branch', 'merged')
        )
        versions = cur.fetchall()
        
        logger.info(f'发现 {len(versions)} 个活跃的分支版本需要迁移')
        
        migrated_count = 0
        skipped_count = 0
        
        for version_id, collection in versions:
            logger.info(f'处理版本: {version_id} ({collection})')
            
            # 2. 检查是否已有追踪数据
            cur.execute(
                'SELECT COUNT(*) FROM version_collections WHERE version_id = %s',
                (version_id,)
            )
            existing = cur.fetchone()[0]
            
            if existing > 0:
                logger.info(f'  已有 {existing} 条追踪数据，跳过')
                skipped_count += 1
                continue
            
            # 3. 扫描直接数据
            cur.execute(
                'SELECT DISTINCT collection FROM dynamic_data WHERE branch_id = %s',
                (version_id,)
            )
            direct = [row[0] for row in cur.fetchall()]
            
            # 4. 扫描关联数据
            cur.execute(
                'SELECT DISTINCT related_collection FROM data_relations WHERE branch_id = %s',
                (version_id,)
            )
            related = [row[0] for row in cur.fetchall()]
            
            # 5. 合并去重
            all_collections = set(direct + related)
            
            if not all_collections:
                all_collections = {collection}
                logger.info(f'  未找到数据，使用主Collection: {collection}')
            
            # 6. 插入追踪数据
            now = datetime.now(timezone.utc)
            for coll in all_collections:
                cur.execute(
                    'INSERT INTO version_collections (version_id, collection, created_at) '
                    'VALUES (%s, %s, %s)',
                    (version_id, coll, now)
                )
            
            logger.info(f'  追踪到 {len(all_collections)} 个Collection: {sorted(all_collections)}')
            migrated_count += 1
        
        conn.commit()
    
    logger.info(f'\n迁移完成！')
    logger.info(f'  成功迁移: {migrated_count} 个版本')
    logger.info(f'  已有数据跳过: {skipped_count} 个版本')
    logger.info(f'  总处理: {len(versions)} 个版本')


def verify_migration():
    """验证迁移结果"""
    logger.info('\n验证迁移结果...')
    
    with get_db() as conn:
        cur = conn.cursor()
        
        # 检查孤立数据
        cur.execute(
            'SELECT collection, branch_id, COUNT(*) '
            'FROM dynamic_data '
            'WHERE branch_id NOT IN ('
            '    SELECT id FROM collection_versions WHERE version_type = \'branch\''
            ') '
            'AND branch_id != \'main\' '
            'GROUP BY collection, branch_id'
        )
        orphaned = cur.fetchall()
        
        if orphaned:
            logger.warning('⚠️ 发现孤立数据：')
            for row in orphaned:
                logger.warning(f'  {row[0]} (branch: {row[1]}): {row[2]} 条')
            logger.warning('建议：运行 python scripts/migrate_version_collections.py --cleanup')
        else:
            logger.info('✅ 无孤立数据')
        
        # 检查追踪数据完整性
        cur.execute(
            'SELECT cv.id, cv.collection, COUNT(vc.collection) '
            'FROM collection_versions cv '
            'LEFT JOIN version_collections vc ON cv.id = vc.version_id '
            'WHERE cv.version_type = \'branch\' AND cv.status != \'merged\' '
            'GROUP BY cv.id, cv.collection '
            'HAVING COUNT(vc.collection) = 0'
        )
        missing = cur.fetchall()
        
        if missing:
            logger.warning('⚠️ 发现缺少追踪数据的版本：')
            for row in missing:
                logger.warning(f'  版本 {row[0]} ({row[1]})')
        else:
            logger.info('✅ 所有活跃分支版本都有追踪数据')


def cleanup_orphaned_data():
    """清理孤立数据"""
    logger.info('\n准备清理孤立数据...')
    
    with get_db() as conn:
        cur = conn.cursor()
        
        cur.execute(
            'SELECT collection, branch_id, COUNT(*) '
            'FROM dynamic_data '
            'WHERE branch_id NOT IN ('
            '    SELECT id FROM collection_versions WHERE version_type = \'branch\''
            ') '
            'AND branch_id != \'main\' '
            'GROUP BY collection, branch_id'
        )
        orphaned = cur.fetchall()
        
        if not orphaned:
            logger.info('无需清理，没有孤立数据')
            return
        
        total = sum(row[2] for row in orphaned)
        logger.info(f'发现 {total} 条孤立数据：')
        for row in orphaned:
            logger.info(f'  {row[0]} (branch: {row[1]}): {row[2]} 条')
        
        response = input('\n确认清理这些孤立数据吗？(y/n): ')
        if response.lower() not in ('y', 'yes'):
            logger.info('取消清理')
            return
        
        for collection, branch_id, count in orphaned:
            cur.execute(
                'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (collection, branch_id)
            )
            logger.info(f'  已清理 {collection} (branch: {branch_id}): {cur.rowcount} 条')
        
        for _, branch_id, _ in orphaned:
            cur.execute(
                'DELETE FROM data_relations WHERE branch_id = %s',
                (branch_id,)
            )
        
        conn.commit()
        logger.info('✅ 清理完成')


if __name__ == '__main__':
    logger.info('========================================')
    logger.info('version_collections 数据迁移脚本')
    logger.info('========================================\n')
    
    migrate_version_collections()
    verify_migration()
    
    if '--cleanup' in sys.argv:
        cleanup_orphaned_data()
    
    logger.info('\n迁移脚本执行完毕')
```

- [ ] **Step 2: 提交迁移脚本**

```bash
git add server/scripts/migrate_version_collections.py
git commit -m "feat: add migration script for version_collections data"
```

---

## Task 8: 运行迁移脚本验证功能

**Files:**
- 无文件修改，仅执行验证

- [ ] **Step 1: 运行迁移脚本**

Run: `cd server && python scripts/migrate_version_collections.py`
Expected: 输出迁移统计，显示成功迁移的版本数量

- [ ] **Step 2: 运行完整测试套件**

Run: `cd server && python tests/test_version_collections.py`
Expected: 所有5个测试通过

- [ ] **Step 3: 验证孤立数据清理**

Run: `cd server && python scripts/migrate_version_collections.py`
Expected: 输出 "✅ 无孤立数据"

- [ ] **Step 4: 提交验证结果**

记录验证通过的日志输出，确保功能正常工作。

---

## Task 9: 改造前端删除逻辑

**Files:**
- Modify: `src/components/common/VersionManager.vue` (改造 handleDelete)

- [ ] **Step 1: 修改 VersionManager.vue 的删除逻辑**

在 `src/components/common/VersionManager.vue` 的 `handleDelete` 函数处修改：

```typescript
async function handleDelete(row: CollectionVersion) {
  try {
    // 第一步：获取影响报告
    const response = await axios.delete(`/api/versions/${row.id}?confirmed=false`)
    
    if (response.data.requiresConfirmation) {
      const impact = response.data.data
      
      // 展示确认对话框
      ElMessageBox.confirm(
        generateDeleteWarningMessage(impact),
        '删除版本确认',
        {
          confirmButtonText: '确认删除',
          cancelButtonText: '取消',
          type: 'warning',
          customClass: 'delete-version-dialog'
        }
      ).then(async () => {
        // 第二步：用户确认后执行删除
        await axios.delete(`/api/versions/${row.id}?confirmed=true`)
        ElMessage.success('版本已删除')
        loadVersions()
      }).catch(() => {
        ElMessage.info('已取消删除')
      })
    }
  } catch (e: any) {
    const msg = e?.response?.data?.error || '删除失败'
    ElMessage.error(msg)
  }
}

function generateDeleteWarningMessage(impact: any): string {
  let message = impact.warningMessage + '\n\n'
  
  if (impact.hasCrossCollectionData) {
    message += '将要删除的数据详情：\n'
    impact.affectedCollections.forEach((coll: any) => {
      message += `\n${coll.collection} (${coll.recordCount}条记录):\n`
      coll.records.slice(0, 5).forEach((rec: any) => {
        message += `  • ${rec.displayName}\n`
      })
      if coll.recordCount > 5 {
        message += `  ...还有 ${coll.recordCount - 5} 条\n`
      }
    })
  }
  
  return message
}
```

- [ ] **Step 2: 提交前端改造**

```bash
git add src/components/common/VersionManager.vue
git commit -m "feat: implement two-phase delete confirmation in frontend"
```

---

## Task 10: 更新文档

**Files:**
- Modify: `CLAUDE.md` (可选，更新说明)
- Create: `docs/api-version-delete.md` (API文档更新)

- [ ] **Step 1: 编写 API 文档**

创建 `docs/api-version-delete.md`：

```markdown
# 版本删除 API 文档

## 两阶段删除流程

### 第一步：获取影响报告

**端点**：`GET /api/versions/<id>/delete-impact`

**描述**：获取删除版本的影响范围报告，前端展示确认对话框。

**响应示例**：
```json
{
  "success": true,
  "data": {
    "versionInfo": {
      "id": "ver-001",
      "name": "测试版本",
      "collection": "inspection-case",
      "versionType": "branch"
    },
    "affectedCollections": [
      {
        "collection": "inspection-case",
        "recordCount": 5,
        "records": [
          {"id": "case-001", "displayName": "巡检用例A"}
        ]
      }
    ],
    "totalRecords": 5,
    "hasCrossCollectionData": false,
    "warningMessage": "将删除 inspection-case 的5条数据"
  }
}
```

### 第二步：确认删除

**端点**：`DELETE /api/versions/<id>?confirmed=true`

**描述**：用户确认后执行删除。

**响应示例**：
```json
{
  "success": true,
  "message": "版本已删除"
}
```

### 分页查询详情

**端点**：`GET /api/versions/<id>/delete-detail`

**参数**：
- collection: 查询哪个Collection
- page: 页码（默认1）
- pageSize: 每页数量（默认20）

**响应示例**：
```json
{
  "success": true,
  "data": {
    "collection": "inspection-case",
    "totalCount": 512,
    "totalPages": 26,
    "currentPage": 1,
    "pageSize": 20,
    "records": [
      {"id": "case-001", "displayName": "巡检用例A", "createdAt": "2026-01-05"}
    ]
  }
}
```
```

- [ ] **Step 2: 提交文档**

```bash
git add docs/api-version-delete.md
git commit -m "docs: add version delete API documentation"
```

---

## Task 11: 最终验收和部署准备

**Files:**
- 无文件修改，仅验证

- [ ] **Step 1: 运行所有后端测试**

Run: `cd server && python tests/test_version_collections.py`
Expected: 所有5个测试通过

- [ ] **Step 2: 验证API端点可用**

Run: 启动服务器，手动测试 API 端点可访问

- [ ] **Step 3: 运行数据迁移**

Run: `cd server && python scripts/migrate_version_collections.py`
Expected: 迁移成功，无孤立数据

- [ ] **Step 4: 验证前端功能**

手动测试前端删除流程：
1. 点击删除版本
2. 查看确认对话框显示影响范围
3. 点击确认删除
4. 验证数据清理完整

- [ ] **Step 5: 准备部署清单**

编写部署步骤：
1. 拉取最新代码
2. 运行 init_db.py 创建新表
3. 运行迁移脚本补充数据
4. 重启后端服务
5. 测试功能正常

- [ ] **Step 6: 最终提交**

```bash
git add -A
git commit -m "feat: complete cross-collection version delete implementation"

# 推送到远程
git push origin main
```

---

## 验收标准

完成所有任务后，系统应满足：

1. ✅ `version_collections` 表正确创建
2. ✅ 追踪函数正确记录跨Collection版本
3. ✅ 影响报告准确展示数据范围
4. ✅ 两阶段删除流程完整实现
5. ✅ 跨Collection数据正确清理（无孤立数据）
6. ✅ 现有版本数据迁移完成
7. ✅ 前端UI展示友好确认对话框
8. ✅ 所有测试通过
9. ✅ 文档完整更新

---

**实施计划结束**