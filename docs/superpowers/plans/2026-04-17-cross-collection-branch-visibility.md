# 跨 Collection 分支可见性实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现跨collection分支可见性共享，确保在数据A创建的分支能在关联数据B的版本管理中可见，跳转时自动切换到相同分支。

**Architecture:** 采用多记录方案：一个分支在collection_versions表中有多条记录（每个参与的collection一条），通过parent_version自引用标记non-primary collection。分支切换时同步更新所有参与的collections的用户当前分支设置。

**Tech Stack:** Python Flask + psycopg2（后端），Vue 3 + TypeScript + Element Plus（前端），PostgreSQL（数据库）

---

## 文件结构映射

**后端文件：**
- `server/utils/version.py`：核心分支管理逻辑（create_version_snapshot, switch_to_version, delete_version + 新增辅助函数）
- `server/routes/versions.py`：版本列表和详情API（添加collections字段）
- `server/tests/test_cross_collection_branch.py`：新增单元测试文件
- `server/migrations/migrate_cross_collection_branches.py`：新增迁移脚本

**前端文件：**
- `src/types/version.ts`：类型定义（添加collections和collectionStats字段）
- `src/stores/jump.ts`：跳转意图（添加branchId字段）
- `src/views/dynamic/DynamicPage.vue`：主数据页面（加载时自动切换分支）

**数据库：**
- 无表结构改动，仅数据迁移

---

## Phase 1: 后端核心函数改造

### Task 1: 添加辅助函数

**Files:**
- Modify: `server/utils/version.py:1-20`（在文件开头添加辅助函数）
- Test: `server/tests/test_cross_collection_branch.py`（新增测试文件）

- [ ] **Step 1: 编写辅助函数测试**

```python
# server/tests/test_cross_collection_branch.py
import pytest
from utils.version import get_version_collections, get_primary_collection

def test_get_version_collections():
    """测试获取版本涉及的collections"""
    # Setup: 创建测试分支涉及多个collection
    # （需要先有数据库fixture）
    pass  # placeholder将在Step 2完善

def test_get_primary_collection():
    """测试识别主collection"""
    pass
```

- [ ] **Step 2: 在version.py添加辅助函数实现**

```python
# server/utils/version.py (在get_branch_data_count函数后添加)

def get_version_collections(version_id):
    """
    获取版本涉及的所有collections

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    List[str]
        所有参与的collection列表
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        return [row[0] for row in cur.fetchall()]


def get_version_collection_stats(version_id):
    """
    获取每个collection的记录数统计

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    Dict[str, int]
        {collection: records_count}
    """
    stats = {}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection, records_count FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        for row in cur.fetchall():
            stats[row[0]] = row[1]
    return stats


def get_primary_collection(version_id):
    """
    获取版本的主collection（创建点）

    Primary collection的parent_version不自引用（null或指向其他版本）
    Non-primary collection的parent_version = version_id（自引用）

    Parameters
    ----------
    version_id : str
        版本 ID

    Returns
    -------
    str | None
        主collection名称，未找到返回None
    """
    with get_db() as conn:
        cur = conn.cursor()
        # Primary collection: parent_version IS NULL 或 parent_version != id
        cur.execute(
            'SELECT collection FROM collection_versions '
            'WHERE id = %s AND (parent_version IS NULL OR parent_version != %s)',
            (version_id, version_id)
        )
        row = cur.fetchone()
        return row[0] if row else None


def count_collection_relations(collection, branch_id, cur):
    """
    统计collection在指定分支的关联数量

    Parameters
    ----------
    collection : str
        Collection名称
    branch_id : str
        分支 ID
    cur : cursor
        数据库游标（避免多次创建连接）

    Returns
    -------
    int
        关联数量
    """
    cur.execute(
        'SELECT COUNT(*) FROM data_relations '
        'WHERE collection = %s AND branch_id = %s',
        (collection, branch_id)
    )
    return cur.fetchone()[0]
```

- [ ] **Step 3: 提交辅助函数**

```bash
git add server/utils/version.py server/tests/test_cross_collection_branch.py
git commit -m "feat(version): add helper functions for cross-collection branch support"
```

---

### Task 2: 改造create_version_snapshot函数

**Files:**
- Modify: `server/utils/version.py:185-300`（create_version_snapshot函数）
- Test: `server/tests/test_cross_collection_branch.py`

- [ ] **Step 1: 编写create_version_snapshot测试**

```python
# server/tests/test_cross_collection_branch.py (添加)

def test_create_branch_with_relations(db_cursor):
    """测试创建跨collection分支"""
    from utils.version import create_version_snapshot, get_version_collections

    # Setup: 创建测试数据和关联
    collection_a = 'test_collection_a'
    collection_b = 'test_collection_b'
    branch_id = 'test-branch-001'

    # 创建测试记录
    db_cursor.execute(
        'INSERT INTO dynamic_data (id, collection, data, branch_id) '
        'VALUES (%s, %s, %s, %s)',
        ('record-a-001', collection_a, '{"name": "Record A"}', branch_id)
    )
    db_cursor.execute(
        'INSERT INTO dynamic_data (id, collection, data, branch_id) '
        'VALUES (%s, %s, %s, %s)',
        ('record-b-001', collection_b, '{"name": "Record B"}', 'main')
    )

    # 创建关联
    db_cursor.execute(
        'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
        'VALUES (%s, %s, %s, %s, %s, %s)',
        (collection_a, 'record-a-001', 'relatedField', collection_b, 'record-b-001', branch_id)
    )

    # Action: 在collection_a创建分支
    result = create_version_snapshot(
        collection=collection_a,
        name='测试跨collection分支',
        description='包含关联的collection_b',
        version_type='branch',
        parent_version=None,
        created_by='test-user',
        branch_id=branch_id
    )

    # Assert: 验证返回结果包含两个collection
    assert 'collections' in result
    assert collection_a in result['collections']
    assert collection_b in result['collections']
    assert result['collection'] == collection_a  # Primary collection

    # Assert: 验证数据库中两个collection都有分支记录
    all_collections = get_version_collections(result['id'])
    assert collection_a in all_collections
    assert collection_b in all_collections
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py::test_create_branch_with_relations -v
```
Expected: FAIL（因为create_version_snapshot还未改造）

- [ ] **Step 3: 改造create_version_snapshot实现**

```python
# server/utils/version.py (替换create_version_snapshot函数，从line 185开始)

def create_version_snapshot(collection, name, description, version_type, parent_version, created_by, branch_id=None):
    """
    创建集合版本快照（支持跨collection）

    Parameters
    ----------
    collection : str
        集合名称（Primary collection，创建点）
    name : str
        版本名称
    description : str
        版本描述
    version_type : str
        'snapshot' 或 'branch'
    parent_version : str | None
        父版本 ID
    created_by : str
        创建者
    branch_id : str | None
        要快照的分支 ID，None 表示主分支

    Returns
    -------
    dict
        版本信息（包含collections列表）
    """
    version_id = f'ver-{uuid.uuid4().hex[:8]}'
    now = datetime.now(timezone.utc)
    actual_branch_id = branch_id or MAIN_BRANCH_ID

    with get_db() as conn:
        cur = conn.cursor()

        # 1. Recursively scan all related collections' data
        try:
            all_collections_data = scan_all_related_data(
                start_collection=collection,
                branch_id=actual_branch_id,
                max_records=10000
            )
        except ValueError as e:
            conn.rollback()
            raise ValueError(f'Failed to create version: {str(e)}')

        # 2. Query relations from ALL collections
        all_relations = []
        for coll in all_collections_data.keys():
            cur.execute(
                'SELECT collection, record_id, field_name, related_collection, related_id '
                'FROM data_relations WHERE collection = %s AND branch_id = %s',
                (coll, actual_branch_id),
            )
            all_relations.extend(cur.fetchall())

        # 3. Calculate hash and counts
        records_count = sum(len(records) for records in all_collections_data.values())
        relations_count = len(all_relations)
        data_hash = _compute_data_hash(all_collections_data, all_relations)

        # 4. Insert version metadata for EACH collection (多记录方案核心)
        for coll in all_collections_data.keys():
            coll_records_count = len(all_collections_data[coll])
            coll_relations_count = count_collection_relations(coll, actual_branch_id, cur)

            # Primary collection: parent_version = 用户传入值
            # Non-primary: parent_version = version_id (自引用标记)
            effective_parent = (
                parent_version if coll == collection else version_id
            )

            cur.execute(
                'INSERT INTO collection_versions '
                '(id, collection, name, description, version_type, parent_version, status, '
                'data_hash, records_count, relations_count, created_by, created_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (version_id, coll, name, description, version_type,
                 effective_parent, 'active', data_hash, coll_records_count,
                 coll_relations_count, created_by, now),
            )

        # 5. Insert all collections' data to version_snapshots
        for coll, records in all_collections_data.items():
            if records:
                snapshot_values = [
                    (version_id, coll, record['id'], psycopg2.extras.Json(record['data']), record['created_at'])
                    for record in records
                ]
                psycopg2.extras.execute_values(
                    cur,
                    'INSERT INTO version_snapshots (version_id, collection, record_id, record_data, created_at) '
                    'VALUES %s',
                    snapshot_values,
                )

        # 6. Insert all relations to version_relations
        if all_relations:
            rel_values = [
                (version_id, r[0], r[1], r[2], r[3], r[4])
                for r in all_relations
            ]
            psycopg2.extras.execute_values(
                cur,
                'INSERT INTO version_relations '
                '(version_id, collection, record_id, field_name, related_collection, related_id) '
                'VALUES %s',
                rel_values,
            )

        # 7. Track version涉及的所有 Collection
        track_version_collections(
            version_id,
            collection,
            actual_branch_id,
            conn,
            list(all_collections_data.keys())
        )

    return {
        'id': version_id,
        'collection': collection,  # Primary collection
        'collections': list(all_collections_data.keys()),  # 所有参与的collections
        'name': name,
        'description': description,
        'versionType': version_type,
        'parentVersion': parent_version,
        'status': 'active',
        'dataHash': data_hash,
        'recordsCount': records_count,
        'relationsCount': relations_count,
        'createdBy': created_by,
        'createdAt': now.isoformat(),
    }
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py::test_create_branch_with_relations -v
```
Expected: PASS

- [ ] **Step 5: 提交create_version_snapshot改造**

```bash
git add server/utils/version.py server/tests/test_cross_collection_branch.py
git commit -m "feat(version): support multi-collection records in create_version_snapshot"
```

---

### Task 3: 改造switch_to_version函数

**Files:**
- Modify: `server/utils/version.py`（switch_to_version函数，约在line 500附近）
- Test: `server/tests/test_cross_collection_branch.py`

- [ ] **Step 1: 编写switch_to_version测试**

```python
# server/tests/test_cross_collection_branch.py (添加)

def test_switch_cross_collection_branch(db_cursor):
    """测试切换跨collection分支时同步更新所有collections"""
    from utils.version import switch_to_version, get_user_current_branch, create_version_snapshot

    # Setup: 创建跨collection分支
    collection_a = 'test_collection_a'
    collection_b = 'test_collection_b'
    user_id = 'test-user-001'

    # 创建测试数据和关联
    db_cursor.execute(
        'INSERT INTO dynamic_data (id, collection, data, branch_id) '
        'VALUES (%s, %s, %s, %s)',
        ('record-a-001', collection_a, '{"name": "Record A"}', 'main')
    )
    db_cursor.execute(
        'INSERT INTO dynamic_data (id, collection, data, branch_id) '
        'VALUES (%s, %s, %s, %s)',
        ('record-b-001', collection_b, '{"name": "Record B"}', 'main')
    )
    db_cursor.execute(
        'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
        'VALUES (%s, %s, %s, %s, %s, %s)',
        (collection_a, 'record-a-001', 'relatedField', collection_b, 'record-b-001', 'main')
    )

    # 创建跨collection分支
    version_result = create_version_snapshot(
        collection=collection_a,
        name='测试跨collection分支',
        description='用于测试切换',
        version_type='branch',
        parent_version=None,
        created_by='test-user',
        branch_id='main'
    )
    version_id = version_result['id']

    # Action: 在collection_a切换到该分支
    result = switch_to_version(
        version_id,
        switched_by='test-user',
        user_id=user_id
    )

    # Assert: 返回结果包含所有受影响的collections
    assert 'affectedCollections' in result
    assert collection_a in result['affectedCollections']
    assert collection_b in result['affectedCollections']

    # Assert: 验证用户在两个collection的当前分支都已更新
    branch_a = get_user_current_branch(user_id, collection_a)
    branch_b = get_user_current_branch(user_id, collection_b)

    assert branch_a == version_id
    assert branch_b == version_id
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py::test_switch_cross_collection_branch -v
```
Expected: FAIL

- [ ] **Step 3: 改造switch_to_version实现**

```python
# server/utils/version.py (替换switch_to_version函数)

def switch_to_version(version_id, switched_by='unknown', user_id=None):
    """
    切换到指定分支（多collection同步切换）

    Parameters
    ----------
    version_id : str
        版本 ID
    switched_by : str
        切换操作者
    user_id : str | None
        用户 ID

    Returns
    -------
    dict
        切换结果，包含affectedCollections列表
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 1. 获取版本信息
        cur.execute(
            'SELECT collection, version_type FROM collection_versions WHERE id = %s LIMIT 1',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f'版本 {version_id} 不存在')

        version_type = row[1]
        if version_type != 'branch':
            raise ValueError('只能切换到分支类型版本')

        # 2. 获取所有参与的collections（核心改造）
        all_collections = get_version_collections(version_id)

        # 3. 为每个collection设置用户当前分支
        for coll in all_collections:
            set_user_current_branch(user_id, switched_by, coll, version_id)

        # 4. 检查分支是否有数据（首次切换需初始化）
        primary_collection = get_primary_collection(version_id)
        data_count = get_branch_data_count(primary_collection, version_id)

        initialized = data_count > 0
        if not initialized:
            # 首次切换：从快照初始化所有collection的数据
            for coll in all_collections:
                initialize_branch_from_snapshot(conn, version_id, coll)

        return {
            'success': True,
            'branchId': version_id,
            'branchName': get_version_name(version_id),
            'recordsInBranch': data_count,
            'initialized': initialized,
            'affectedCollections': all_collections,  # 新增：返回所有受影响的collections
        }


def initialize_branch_from_snapshot(conn, version_id, collection):
    """
    从快照初始化指定collection的分支数据

    Parameters
    ----------
    conn : connection
        数据库连接
    version_id : str
        版本 ID
    collection : str
        Collection名称
    """
    cur = conn.cursor()

    # 1. 加载快照数据
    cur.execute(
        'SELECT record_id, record_data, created_at FROM version_snapshots '
        'WHERE version_id = %s AND collection = %s',
        (version_id, collection)
    )
    records = cur.fetchall()

    if not records:
        return

    # 2. 批量插入到 dynamic_data
    for record_id, record_data, created_at in records:
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, created_at, updated_at, version, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s) '
            'ON CONFLICT (id, branch_id) DO NOTHING',
            (record_id, collection, record_data, created_at, created_at, 1, version_id)
        )

    # 3. 加载并插入关联数据
    cur.execute(
        'SELECT collection, record_id, field_name, related_collection, related_id '
        'FROM version_relations WHERE version_id = %s AND collection = %s',
        (version_id, collection)
    )
    relations = cur.fetchall()

    for rel in relations:
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s) '
            'ON CONFLICT DO NOTHING',
            (rel[0], rel[1], rel[2], rel[3], rel[4], version_id)
        )


def get_version_name(version_id):
    """获取版本名称"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT name FROM collection_versions WHERE id = %s LIMIT 1',
            (version_id,)
        )
        row = cur.fetchone()
        return row[0] if row else version_id
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py::test_switch_cross_collection_branch -v
```
Expected: PASS

- [ ] **Step 5: 提交switch_to_version改造**

```bash
git add server/utils/version.py server/tests/test_cross_collection_branch.py
git commit -m "feat(version): sync switch across all collections in branch"
```

---

### Task 4: 改造delete_version函数

**Files:**
- Modify: `server/utils/version.py`（delete_version函数）
- Test: `server/tests/test_cross_collection_branch.py`

- [ ] **Step 1: 编写delete_version测试**

```python
# server/tests/test_cross_collection_branch.py (添加)

def test_delete_cross_collection_branch(db_cursor):
    """测试删除跨collection分支时级联清理所有collection记录"""
    from utils.version import delete_version, get_version_collections

    # Setup: 创建跨collection分支
    version_id = 'ver-test-001'

    # Action: 未确认删除（获取影响报告）
    impact = delete_version(version_id, confirmed=False)

    # Assert: 影响报告包含所有collections
    assert 'collections' in impact
    assert len(impact['collections']) > 1
    assert 'totalRecords' in impact
    assert 'requiresConfirmation' in impact

    # Action: 确认删除
    result = delete_version(version_id, confirmed=True)

    # Assert: 删除成功
    assert result is True

    # Assert: 验证所有collection的记录都已删除
    remaining = get_version_collections(version_id)
    assert len(remaining) == 0
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py::test_delete_cross_collection_branch -v
```
Expected: FAIL

- [ ] **Step 3: 改造delete_version实现**

```python
# server/utils/version.py (替换delete_version函数)

def delete_version(version_id, confirmed=False):
    """
    删除版本（级联删除所有collection记录）

    Parameters
    ----------
    version_id : str
        版本 ID
    confirmed : bool
        是否已确认删除

    Returns
    -------
    dict | bool
        未确认：返回影响报告
        已确认：返回True
    """
    # 1. 获取所有参与的collections
    collections = get_version_collections(version_id)

    if not collections:
        return False  # 版本不存在

    # 2. 检查是否有用户在使用
    users_using = []
    for coll in collections:
        users = get_users_on_branch(coll, version_id)
        users_using.extend(users)

    if users_using:
        raise ValueError(
            f'有用户正在使用该分支：{", ".join(set(users_using))}。'
            f'请通知他们切换到其他分支后再删除。'
        )

    # 3. 未确认：返回影响报告
    if not confirmed:
        total_records = sum(
            get_branch_data_count(coll, version_id)
            for coll in collections
        )

        return {
            'versionId': version_id,
            'collections': collections,
            'totalRecords': total_records,
            'usersUsingBranch': [],
            'requiresConfirmation': True
        }

    # 4. 确认后执行删除
    with get_db() as conn:
        cur = conn.cursor()

        # 删除所有collection_versions记录
        cur.execute(
            'DELETE FROM collection_versions WHERE id = %s',
            (version_id,)
        )

        # 删除所有collection的分支数据
        for coll in collections:
            cur.execute(
                'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )
            cur.execute(
                'DELETE FROM data_relations WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )

        # 删除快照数据
        cur.execute(
            'DELETE FROM version_snapshots WHERE version_id = %s',
            (version_id,)
        )
        cur.execute(
            'DELETE FROM version_relations WHERE version_id = %s',
            (version_id,)
        )

        # 删除version_collections记录
        cur.execute(
            'DELETE FROM version_collections WHERE version_id = %s',
            (version_id,)
        )

        return True


def get_users_on_branch(collection, branch_id):
    """获取正在使用指定分支的用户列表"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT username FROM user_current_branch '
            'WHERE collection = %s AND branch_id = %s',
            (collection, branch_id)
        )
        return [row[0] for row in cur.fetchall()]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py::test_delete_cross_collection_branch -v
```
Expected: PASS

- [ ] **Step 5: 提交delete_version改造**

```bash
git add server/utils/version.py server/tests/test_cross_collection_branch.py
git commit -m "feat(version): cascade delete across all collections"
```

---

## Phase 2: 后端API改造

### Task 5: 改造版本列表和详情API

**Files:**
- Modify: `server/routes/versions.py:35-58`（list_versions函数）
- Modify: `server/routes/versions.py:101-108`（get_version函数）
- Test: `server/tests/test_cross_collection_branch.py`

- [ ] **Step 1: 编写API测试**

```python
# server/tests/test_cross_collection_branch.py (添加)

def test_list_versions_with_cross_collection(client, db_cursor):
    """测试版本列表API返回collections字段"""
    from utils.version import create_version_snapshot

    # Setup: 创建跨collection分支
    version_id = create_version_snapshot(
        collection='test_collection_a',
        name='测试分支',
        description='',
        version_type='branch',
        parent_version=None,
        created_by='test-user',
        branch_id='main'
    )['id']

    # Action: 获取collection_a的版本列表
    response = client.get('/api/versions?collection=test_collection_a')

    # Assert: 返回结果包含collections字段
    data = response.json
    assert len(data) > 0

    target_version = next(v for v in data if v['id'] == version_id)
    assert 'collections' in target_version
    assert 'collectionStats' in target_version
    assert 'test_collection_a' in target_version['collections']


def test_get_version_detail_with_collections(client, db_cursor):
    """测试版本详情API返回collections字段"""
    from utils.version import create_version_snapshot

    # Setup: 创建测试数据和关联
    collection_a = 'test_collection_a'
    collection_b = 'test_collection_b'

    db_cursor.execute(
        'INSERT INTO dynamic_data (id, collection, data, branch_id) '
        'VALUES (%s, %s, %s, %s)',
        ('record-a-001', collection_a, '{"name": "Record A"}', 'main')
    )
    db_cursor.execute(
        'INSERT INTO dynamic_data (id, collection, data, branch_id) '
        'VALUES (%s, %s, %s, %s)',
        ('record-b-001', collection_b, '{"name": "Record B"}', 'main')
    )
    db_cursor.execute(
        'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
        'VALUES (%s, %s, %s, %s, %s, %s)',
        (collection_a, 'record-a-001', 'relatedField', collection_b, 'record-b-001', 'main')
    )

    # 创建跨collection分支
    version_result = create_version_snapshot(
        collection=collection_a,
        name='测试分支',
        description='测试详情API',
        version_type='branch',
        parent_version=None,
        created_by='test-user',
        branch_id='main'
    )
    version_id = version_result['id']

    # Action: 获取版本详情
    response = client.get(f'/api/versions/{version_id}')

    # Assert: 返回结果包含collections列表
    data = response.json
    assert 'collections' in data
    assert 'collectionStats' in data
    assert len(data['collections']) > 1
    assert collection_a in data['collections']
    assert collection_b in data['collections']
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py::test_list_versions_with_cross_collection -v
cd server && python -m pytest tests/test_cross_collection_branch.py::test_get_version_detail_with_collections -v
```
Expected: FAIL

- [ ] **Step 3: 改造list_versions API**

```python
# server/routes/versions.py (替换list_versions函数，从line 35开始)

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
        # 聚合collections信息（新增）
        if 'data' in result:
            for v in result['data']:
                v['collections'] = get_version_collections(v['id'])
                v['collectionStats'] = get_version_collection_stats(v['id'])
        return jsonify(result)

    # Backward compatible: return full list
    versions = get_version_list(collection=collection, status=status)

    # 聚合collections信息（新增）
    for v in versions:
        v['collections'] = get_version_collections(v['id'])
        v['collectionStats'] = get_version_collection_stats(v['id'])

    return jsonify(versions)
```

- [ ] **Step 4: 改造get_version API**

```python
# server/routes/versions.py (替换get_version函数，从line 101开始)

@versions_bp.route('/versions/<version_id>', methods=['GET'])
@login_required
def get_version(version_id):
    """获取版本详情"""
    version = get_version_detail(version_id)
    if not version:
        return jsonify({'error': '版本不存在'}), 404

    # 添加跨collection信息（新增）
    version['collections'] = get_version_collections(version_id)
    version['collectionStats'] = get_version_collection_stats(version_id)

    return jsonify(version)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py::test_list_versions_with_cross_collection tests/test_cross_collection_branch.py::test_get_version_detail_with_collections -v
```
Expected: PASS

- [ ] **Step 6: 提交API改造**

```bash
git add server/routes/versions.py server/tests/test_cross_collection_branch.py
git commit -m "feat(api): add collections field to version list and detail APIs"
```

---

## Phase 3: 前端改造

### Task 6: 更新TypeScript类型定义

**Files:**
- Modify: `src/types/version.ts`

- [ ] **Step 1: 更新CollectionVersion类型**

```typescript
// src/types/version.ts (修改CollectionVersion接口)

export interface CollectionVersion {
  id: string
  collection: string          // Primary collection（创建点）
  collections: string[]       // 所有参与的collections（新增）
  collectionStats?: Record<string, number>  // 每个collection的记录数（新增）
  name: string
  description: string
  versionType: 'snapshot' | 'branch'
  status: 'active' | 'merged' | 'deleted'
  recordsCount: number        // 总记录数
  relationsCount: number
  createdAt: string
  createdBy: string
  parentVersion: string | null
  dataHash?: string
  mergedAt?: string
  mergedBy?: string
}
```

- [ ] **Step 2: 更新SwitchResult类型**

```typescript
// src/types/version.ts (修改SwitchResult接口)

export interface SwitchResult {
  success: boolean
  branchId: string
  branchName: string
  recordsInBranch: number
  initialized: boolean
  affectedCollections?: string[]  // 新增：受影响的collections列表
}
```

- [ ] **Step 3: 提交类型定义**

```bash
git add src/types/version.ts
git commit -m "feat(types): add collections field to CollectionVersion type"
```

---

### Task 7: 改造JumpStore携带分支上下文

**Files:**
- Modify: `src/stores/jump.ts`
- Test: `src/stores/__tests__/jump.test.ts`

- [ ] **Step 1: 编写JumpStore测试**

```typescript
// src/stores/__tests__/jump.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { useJumpStore } from '../jump'
import { useAuthStore } from '../auth'
import { createPinia, setActivePinia } from 'pinia'

describe('JumpStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('should include branchId in jump intent', () => {
    const jumpStore = useJumpStore()
    const authStore = useAuthStore()

    // Mock current branch
    authStore.currentBranch = { branchId: 'branch-001', branchName: '测试分支' }

    // Set jump intent
    jumpStore.setJump(
      {
        targetCollection: 'collectionB',
        targetRecordId: 'record-123',
        jumpType: 'relation',
        sourcePageId: 'page-collectionA',
        timestamp: Date.now(),
      },
      {
        pagePath: '/collectionA',
        pageName: '数据A',
        pageId: 'page-collectionA',
        filters: {},
      }
    )

    // Assert: branchId自动填充
    const intent = jumpStore.intent
    expect(intent?.branchId).toBe('branch-001')
  })

  it('should fallback to main when no branch', () => {
    const jumpStore = useJumpStore()
    const authStore = useAuthStore()

    // No current branch
    authStore.currentBranch = null

    jumpStore.setJump(
      {
        targetCollection: 'collectionB',
        targetRecordId: 'record-123',
        jumpType: 'relation',
        sourcePageId: 'page-collectionA',
        timestamp: Date.now(),
      },
      {
        pagePath: '/collectionA',
        pageName: '数据A',
        pageId: 'page-collectionA',
        filters: {},
      }
    )

    const intent = jumpStore.intent
    expect(intent?.branchId).toBe('main')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
npm run test src/stores/__tests__/jump.test.ts
```
Expected: FAIL（因为setJump还未改造）

- [ ] **Step 3: 改造JumpStore**

```typescript
// src/stores/jump.ts (修改JumpIntent接口和setJump方法)

export interface JumpIntent {
  targetCollection: string
  targetRecordId: string
  jumpType: 'relation' | 'reference' | 'quote'
  sourcePageId: string
  timestamp: number
  branchId?: string  // 新增：分支上下文
}

export const useJumpStore = defineStore('jump', {
  state: () => ({
    intent: null as JumpIntent | null,
    history: [] as JumpSource[],
  }),

  actions: {
    setJump(intent: Omit<JumpIntent, 'branchId'>, source: JumpSource) {
      // 自动填充当前分支信息
      const authStore = useAuthStore()
      const currentBranch = authStore.currentBranch

      this.intent = {
        ...intent,
        branchId: currentBranch?.branchId || 'main',  // 自动填充
      }

      this.history.push(source)
    },

    consumeJump(): JumpIntent | null {
      const intent = this.intent
      this.intent = null
      return intent
    },
  },
})
```

- [ ] **Step 4: 运行测试确认通过**

```bash
npm run test src/stores/__tests__/jump.test.ts
```
Expected: PASS

- [ ] **Step 5: 提交JumpStore改造**

```bash
git add src/stores/jump.ts src/stores/__tests__/jump.test.ts
git commit -m "feat(store): add branchId to jump intent"
```

---

### Task 8: DynamicPage自动切换分支

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`（onMounted逻辑）
- Test: `src/views/dynamic/__tests__/DynamicPage.test.ts`

- [ ] **Step 1: 编写DynamicPage测试**

```typescript
// src/views/dynamic/__tests__/DynamicPage.test.ts
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import DynamicPage from '../DynamicPage.vue'
import { useJumpStore } from '@/stores/jump'
import { useAuthStore } from '@/stores/auth'

vi.mock('@/api/version', () => ({
  switchToVersion: vi.fn().mockResolvedValue({
    success: true,
    branchId: 'branch-001',
    branchName: '测试分支',
  }),
}))

describe('DynamicPage', () => {
  it('should auto-switch to branch from jump intent', async () => {
    const jumpStore = useJumpStore()
    const authStore = useAuthStore()

    // Set jump intent with branchId
    jumpStore.intent = {
      targetCollection: 'collectionB',
      targetRecordId: 'record-123',
      jumpType: 'relation',
      sourcePageId: 'page-collectionA',
      timestamp: Date.now(),
      branchId: 'branch-001',
    }

    // Mount component
    const wrapper = mount(DynamicPage, {
      props: { pageId: 'page-collectionB' },
    })

    await wrapper.vm.$nextTick()

    // Assert: branch switched
    expect(authStore.currentBranch?.branchId).toBe('branch-001')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
npm run test src/views/dynamic/__tests__/DynamicPage.test.ts
```
Expected: FAIL

- [ ] **Step 3: 改造DynamicPage onMounted**

```typescript
// src/views/dynamic/DynamicPage.vue (修改onMounted逻辑)

onMounted(async () => {
  try {
    pageLoading.value = true

    await loadPageConfig()
    await loadCurrentBranch()

    // 处理跳转意图（新增）
    const intent = jumpStore.consumeJump()

    if (intent?.branchId && intent.branchId !== 'main') {
      // 自动切换到跳转携带的分支
      try {
        const result = await switchToVersion(intent.branchId)
        await loadCurrentBranch()
        ElMessage.success(`已切换到分支：${result.branchName}`)
      } catch (error) {
        console.error('分支切换失败:', error)
        ElMessage.warning('跳转携带的分支不存在或已失效，已切换到主分支')
      }
    }

    // 原有的定位记录逻辑
    if (intent?.targetRecordId) {
      intelligentLocateRecord(intent.targetRecordId)
    }

    // 加载数据
    await loadData()
  } catch (error) {
    console.error('页面加载失败:', error)
    ElMessage.error('页面加载失败')
  } finally {
    pageLoading.value = false
  }
})
```

- [ ] **Step 4: 运行测试确认通过**

```bash
npm run test src/views/dynamic/__tests__/DynamicPage.test.ts
```
Expected: PASS

- [ ] **Step 5: 提交DynamicPage改造**

```bash
git add src/views/dynamic/DynamicPage.vue src/views/dynamic/__tests__/DynamicPage.test.ts
git commit -m "feat(view): auto-switch branch on jump intent"
```

---

### Task 9: 版本管理对话框展示跨collection标记

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`（版本管理对话框部分，约在line 1000-1200）

- [ ] **Step 1: 在版本列表添加跨collection图标和Popover**

```vue
<!-- src/views/dynamic/DynamicPage.vue (在版本管理对话框的版本列表部分添加) -->

<el-table :data="branchVersions" class="version-table">
  <el-table-column prop="name" label="名称" width="200">
    <template #default="{ row }">
      <div class="version-name-cell">
        <!-- 跨collection图标 -->
        <el-icon
          v-if="row.collections && row.collections.length > 1"
          class="cross-collection-icon"
          :size="16"
        >
          <Link />
        </el-icon>
        <span>{{ row.name }}</span>
      </div>
    </template>
  </el-table-column>

  <el-table-column prop="collections" label="涉及Collection" width="200">
    <template #default="{ row }">
      <!-- 单collection -->
      <span v-if="!row.collections || row.collections.length === 1">
        {{ formatCollectionName(row.collection) }}
      </span>

      <!-- 多collection：Popover -->
      <el-popover
        v-else
        placement="right"
        :width="280"
        trigger="hover"
      >
        <template #reference>
          <el-button type="primary" link size="small">
            <el-icon><Link /></el-icon>
            {{ row.collections.length }} 个Collection
          </el-button>
        </template>

        <div class="collections-popover">
          <div class="popover-title">参与的Collection：</div>
          <div class="collection-list">
            <div
              v-for="coll in row.collections"
              :key="coll"
              class="collection-item"
            >
              <el-icon v-if="coll === row.collection" color="#409EFF">
                <Star />
              </el-icon>
              <span>{{ formatCollectionName(coll) }}</span>
              <span class="record-count">
                ({{ row.collectionStats?.[coll] || 0 }}条)
              </span>
            </div>
          </div>
        </div>
      </el-popover>
    </template>
  </el-table-column>

  <!-- 其他列保持不变 -->
</el-table>

<style scoped>
.cross-collection-icon {
  color: #409EFF;
  margin-right: 6px;
}

.collections-popover {
  .popover-title {
    font-weight: bold;
    margin-bottom: 8px;
    color: #303133;
  }

  .collection-item {
    display: flex;
    align-items: center;
    padding: 4px 0;
    gap: 6px;
  }

  .record-count {
    color: #909399;
    font-size: 12px;
  }
}
</style>
```

- [ ] **Step 2: 添加formatCollectionName辅助函数**

```typescript
// src/views/dynamic/DynamicPage.vue (在script部分添加)

function formatCollectionName(collection: string): string {
  // 从menuStore查找对应的菜单名称
  const menu = menuStore.menuList.find(m => m.pageId === `page-${collection}`)
  return menu?.name || collection
}
```

- [ ] **Step 3: 确保版本列表数据包含collections字段**

```typescript
// src/views/dynamic/DynamicPage.vue (修改loadBranchVersions函数)

async function loadBranchVersions(): Promise<void> {
  if (!collection.value) return

  try {
    const result = await getVersionsPaginated(collection.value, 'active', 1, 100)
    branchVersions.value = result.data || []

    // 确保每个version都有collections字段（API已返回，这里无需额外处理）
  } catch (error) {
    console.error('加载分支列表失败:', error)
  }
}
```

- [ ] **Step 4: 手动测试UI展示**

启动前端开发服务器，打开版本管理对话框，验证：
- 跨collection分支显示Link图标
- Hover显示Popover包含所有参与的collections
- Primary collection有Star图标标记
- 每个collection显示记录数

- [ ] **Step 5: 提交UI改造**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(ui): show cross-collection marker in version list"
```

---

## Phase 4: 数据迁移

### Task 10: 创建迁移脚本

**Files:**
- Create: `server/migrations/migrate_cross_collection_branches.py`
- Create: `server/scripts/run_migration_cross_collection.py`（执行脚本）

- [ ] **Step 1: 编写迁移脚本**

```python
# server/migrations/migrate_cross_collection_branches.py

"""
迁移现有分支到跨collection模式

功能：
1. 识别所有现有分支
2. 递归扫描每个分支涉及的关联数据
3. 为每个参与的collection补充collection_versions记录
"""

from db import get_db
from utils.version_scan import scan_all_related_data
from datetime import datetime, timezone


def migrate_existing_branches():
    """迁移现有分支到跨collection模式"""
    print('开始迁移现有分支...')

    with get_db() as conn:
        cur = conn.cursor()

        # 1. 获取所有现有分支
        cur.execute(
            "SELECT id, collection, branch_id, name, description, version_type, "
            "parent_version, status, data_hash, records_count, relations_count, "
            "created_by, created_at "
            "FROM collection_versions "
            "WHERE version_type = 'branch' AND status = 'active'"
        )
        branches = cur.fetchall()

        print(f'找到 {len(branches)} 个现有分支')

        migrated_count = 0
        skipped_count = 0

        for branch_data in branches:
            branch_id = branch_data[0]
            primary_collection = branch_data[1]
            actual_branch_id = branch_data[2]

            print(f'处理分支 {branch_id} ({primary_collection})...')

            # 2. 递归扫描关联数据
            try:
                all_data = scan_all_related_data(
                    start_collection=primary_collection,
                    branch_id=actual_branch_id,
                    max_records=5000
                )
            except ValueError as e:
                print(f'  跳过：{e}')
                skipped_count += 1
                continue

            # 3. 为每个参与的collection补充记录
            for coll in all_data.keys():
                if coll == primary_collection:
                    continue  # 已有记录，跳过

                # 检查是否已存在（避免重复插入）
                cur.execute(
                    'SELECT id FROM collection_versions '
                    'WHERE id = %s AND collection = %s',
                    (branch_id, coll)
                )
                if cur.fetchone():
                    print(f'  {coll} 已存在，跳过')
                    continue

                # 插入新记录（parent_version自引用）
                print(f'  为 {coll} 插入记录...')

                records_count = len(all_data[coll])

                cur.execute(
                    'INSERT INTO collection_versions '
                    '(id, collection, name, description, version_type, parent_version, '
                    'status, data_hash, records_count, relations_count, created_by, created_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (
                        branch_id,  # 相同的version_id
                        coll,  # 不同的collection
                        branch_data[3],  # name
                        branch_data[4],  # description
                        branch_data[5],  # version_type
                        branch_id,  # parent_version = version_id (自引用)
                        branch_data[7],  # status
                        branch_data[8],  # data_hash
                        records_count,  # records_count (重新计算)
                        0,  # relations_count (简化处理)
                        branch_data[11],  # created_by
                        branch_data[12],  # created_at
                    )
                )

                migrated_count += 1

        conn.commit()

    print('\n迁移完成！')
    print(f'补充了 {migrated_count} 条跨collection分支记录')
    print(f'跳过了 {skipped_count} 个分支（数据量过大）')

    return migrated_count


def rollback_cross_collection_migration():
    """回滚迁移（删除所有parent_version自引用的记录）"""
    print('开始回滚迁移...')

    with get_db() as conn:
        cur = conn.cursor()

        # 删除parent_version = id的记录（即迁移新增的）
        cur.execute(
            "DELETE FROM collection_versions WHERE parent_version = id"
        )

        deleted_count = cur.rowcount
        conn.commit()

    print(f'回滚完成：删除了 {deleted_count} 条迁移记录')
    return deleted_count


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == 'rollback':
        rollback_cross_collection_migration()
    else:
        migrate_existing_branches()
```

- [ ] **Step 2: 创建执行脚本**

```python
# server/scripts/run_migration_cross_collection.py

"""
执行迁移脚本的便捷入口
"""

import sys
sys.path.insert(0, '../migrations')

from migrate_cross_collection_branches import migrate_existing_branches, rollback_cross_collection_migration

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='迁移现有分支到跨collection模式')
    parser.add_argument('--rollback', action='store_true', help='回滚迁移')

    args = parser.parse_args()

    if args.rollback:
        rollback_cross_collection_migration()
    else:
        migrate_existing_branches()
```

- [ ] **Step 3: 提交迁移脚本**

```bash
git add server/migrations/migrate_cross_collection_branches.py server/scripts/run_migration_cross_collection.py
git commit -m "feat(migration): add cross-collection branch migration script"
```

---

### Task 11: 执行迁移和验证

**Files:**
- 无文件改动，执行和验证操作

- [ ] **Step 1: 备份数据库**

```bash
# 导出collection_versions表
pg_dump -t collection_versions casemanage > backup_collection_versions.sql
```

- [ ] **Step 2: 运行迁移脚本**

```bash
cd server/scripts && python run_migration_cross_collection.py
```
Expected: 输出迁移统计信息

- [ ] **Step 3: 验证迁移结果**

```python
# 手动验证SQL
SELECT id, collection, parent_version, name
FROM collection_versions
WHERE version_type = 'branch' AND status = 'active'
ORDER BY id, collection;
```

Expected: 看到跨collection分支有多个collection记录，parent_version自引用标记non-primary

- [ ] **Step 4: 测试前端展示**

启动前后端服务，验证：
- 版本管理页面显示跨collection标记
- 切换分支功能正常
- 跳转携带分支上下文

- [ ] **Step 5: 记录迁移完成**

创建迁移日志文档：

```bash
echo "迁移完成时间: $(date)" > docs/migration-cross-collection-log.txt
git add docs/migration-cross-collection-log.txt
git commit -m "docs: log cross-collection migration completion"
```

---

## Phase 5: 集成测试和文档更新

### Task 12: 运行完整测试套件

**Files:**
- 无文件改动

- [ ] **Step 1: 运行后端测试**

```bash
cd server && python -m pytest tests/test_cross_collection_branch.py -v
```
Expected: 所有测试PASS

- [ ] **Step 2: 运行前端测试**

```bash
npm run test
```
Expected: 所有测试PASS

- [ ] **Step 3: 运行完整测试套件**

```bash
npm run test:all
```
Expected: 前端和后端所有测试PASS

- [ ] **Step 4: 手动测试完整流程**

测试场景：
1. 在数据A创建分支（关联数据B）
2. 验证数据B的版本管理显示该分支
3. 切换到该分支
4. 从数据A跳转到数据B，验证自动切换分支
5. 删除该分支，验证级联删除

---

### Task 13: 更新用户文档

**Files:**
- Update: `CLAUDE.md`（添加跨collection分支说明）

- [ ] **Step 1: 在CLAUDE.md添加跨collection分支说明**

```markdown
## 跨 Collection 分支管理

### 特性说明

当在数据A创建分支时，如果数据A关联了数据B（通过relation字段），系统会自动：
1. 递归扫描所有关联的数据
2. 在所有参与的collection创建分支记录
3. 分支在数据A和数据B的版本管理中都可见

### 分支标记

版本管理页面会显示：
- 跨collection图标（Link图标）
- Hover显示所有参与的collection列表
- Primary collection（创建点）有Star图标标记

### 分支切换

切换到跨collection分支时，所有参与的collection会同步切换：
- 用户在每个collection的工作分支都会更新
- 跳转到关联数据时自动继承分支上下文

### 使用场景

适用于：
- 多表关联数据的统一版本管理
- 需要保持关联数据一致性的场景
- 跨表数据修改的隔离和合并
```

- [ ] **Step 2: 提交文档更新**

```bash
git add CLAUDE.md
git commit -m "docs: add cross-collection branch management documentation"
```

---

## 完成清单

完成所有任务后：

- [ ] 所有后端测试通过
- [ ] 所有前端测试通过
- [ ] 数据迁移成功
- [ ] 手动测试验证功能正常
- [ ] 文档已更新
- [ ] 所有改动已提交到git

---

## 回滚计划

如果发现严重问题需要回滚：

1. **运行回滚脚本**
```bash
cd server/scripts && python run_migration_cross_collection.py --rollback
```

2. **恢复数据库备份**
```bash
psql casemanage < backup_collection_versions.sql
```

3. **回滚代码**
```bash
git revert <commit-sha>
```

4. **重新部署前后端**