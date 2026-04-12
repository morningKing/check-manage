# 跨 Collection 版本删除设计方案

## 文档信息

- **设计日期**: 2026-04-11
- **设计者**: Claude
- **问题文档**: `docs/cross-collection-version-issue.md`
- **严重程度**: 中等风险
- **优先级**: 数据安全性优先

---

## 一、问题背景与现状分析

### 1.1 系统架构概览

当前系统采用**配置驱动的动态数据管理平台**架构，核心特点：

```
┌─────────────────────────────────────────────────────────┐
│                  版本管理系统架构                         │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐        ┌──────────────┐              │
│  │ Collection   │        │   Version    │              │
│  │  Versions    │───────▶│  Snapshots   │              │
│  │   (元数据)    │        │  (快照数据)   │              │
│  └──────────────┘        └──────────────┘              │
│         │                        │                      │
│         │ collection             │ version_id           │
│         │ (单个)                 │                      │
│         ▼                        ▼                      │
│  ┌──────────────┐        ┌──────────────┐              │
│  │ Dynamic Data │◀───────│ Data Relations│             │
│  │  (业务数据)   │        │  (关联关系)    │             │
│  └──────────────┘        └──────────────┘              │
│         │                        │                      │
│         │ branch_id              │ branch_id            │
│         │                        │                      │
│         ▼                        ▼                      │
│                                                          │
│  分支隔离机制：PRIMARY KEY (id, branch_id)               │
│                                                          │
└─────────────────────────────────────────────────────────┘

关键约束：
- 版本创建时绑定单个 Collection
- 数据关联可以跨多个 Collection
- 分支隔离通过复合主键保证
```

**关键技术点**：

1. **单表动态数据**: 所有业务数据存储在 `dynamic_data` 表，使用 PostgreSQL JSONB
2. **分支隔离**: 复合主键 `PRIMARY KEY (id, branch_id)` 实现数据分支化
3. **跨Collection关联**: 通过 `data_relations` 表实现 M:N 关系

---

### 1.2 版本管理的设计初衷

**设计假设**：
```
版本管理的作用域 = 单个 Collection
```

**设计理由**：
- 每个 Collection 有独立的业务逻辑和字段定义
- 版本快照记录单个 Collection 的完整状态
- 用户在单个 Collection 上进行版本化工作

**数据结构体现**：

```sql
-- collection_versions 表
CREATE TABLE collection_versions (
    id              VARCHAR(100) PRIMARY KEY,
    collection      VARCHAR(200) NOT NULL,  -- ← 单个 Collection
    name            VARCHAR(200) NOT NULL,
    version_type    VARCHAR(20),            -- 'snapshot' | 'branch'
    ...
);
```

---

### 1.3 数据关联的实际场景

**现实业务需求**：
```
数据关联的作用域 = 跨多个 Collection
```

**典型场景**：

```
巡检管理系统：
  ├─ inspection-case (巡检用例)
  │   └─ 关联字段: relatedPlan → inspection-plan
  │
  ├─ inspection-plan (巡检计划)
  │   └─ 关联字段: relatedCases → inspection-case
  │
  └─ inspection-task (巡检任务)
      └─ 关联字段: relatedCase → inspection-case
```

**关联关系表结构**：

```sql
-- data_relations 表
CREATE TABLE data_relations (
    collection          VARCHAR(200) NOT NULL,
    record_id           VARCHAR(100) NOT NULL,
    field_name          VARCHAR(200) NOT NULL,
    related_collection  VARCHAR(200) NOT NULL,  -- ← 可以跨 Collection
    related_id          VARCHAR(100) NOT NULL,
    branch_id           VARCHAR(100) NOT NULL,
    PRIMARY KEY (collection, record_id, field_name, related_id, branch_id)
);
```

---

## 二、核心矛盾与问题分析

### 2.1 作用域不匹配的矛盾

```
┌─────────────────────────────────────────────┐
│           作用域不匹配示意图                 │
├─────────────────────────────────────────────┤
│                                             │
│  版本管理作用域:                             │
│  ┌─────────────┐                            │
│  │ Collection  │                            │
│  │ inspection- │                            │
│  │    case     │                            │
│  └─────────────┘                            │
│       ▽                                     │
│    单个边界                                  │
│                                             │
│  数据关联作用域:                             │
│  ┌─────────────┐  ┌─────────────┐          │
│  │ inspection- │  │ inspection- │          │
│  │    case     │──│    plan     │          │
│  └─────────────┘  └─────────────┘          │
│       ▽                  ▽                 │
│    跨越边界            跨越边界              │
│                                             │
│  ❌ 不匹配！                                 │
│                                             │
└─────────────────────────────────────────────┘
```

**矛盾点**：
- 版本管理认为：版本 = 单个 Collection 的数据快照
- 实际业务中：版本 = 包含跨 Collection 关联数据的工作副本

---

### 2.2 问题触发场景

**具体案例**：

```
时间线：巡检用例版本管理流程

[步骤1] 创建版本
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用户操作：创建 inspection-case 的版本分支
系统记录：
  ✓ version_id: ver-test-case-branch
  ✓ collection: inspection-case  ← 只记录单个
  ✓ version_type: branch
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[步骤2] 在版本中添加数据
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用户操作：在版本分支中添加用例和计划

数据1：巡检用例 A
  - id: case-001
  - collection: inspection-case
  - branch_id: ver-test-case-branch ✓
  
数据2：巡检计划 P ← 关键！跨Collection
  - id: plan-001
  - collection: inspection-plan
  - branch_id: ver-test-case-branch ✓

关联关系：
  - case-001 → plan-001
  - branch_id: ver-test-case-branch ✓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[步骤3] 删除版本
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用户操作：删除版本 ver-test-case-branch
当前系统行为 (version.py 第513-520行)：

  ✓ DELETE FROM dynamic_data
      WHERE collection = 'inspection-case'  ← 使用版本元数据
      AND branch_id = 'ver-test-case-branch'
      → 删除 case-001
      
  ✓ DELETE FROM data_relations
      WHERE branch_id = 'ver-test-case-branch'
      → 删除关联关系
      
  ✗ 保留 inspection-plan 数据！
      → plan-001 残留在数据库中
      
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[结果] 孤立数据产生
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
孤立数据特征：
  - id: plan-001
  - collection: inspection-plan
  - branch_id: ver-test-case-branch ← 指向不存在的版本
  
孤立状态：
  ✓ 数据存在于数据库
  ✗ 无法通过版本管理访问
  ✗ 无法通过业务逻辑查询（branch_id 过滤）
  ✗ 占用存储但无实际意义
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 2.3 根因分析

**代码层面的根因**：

```python
# server/utils/version.py 第513-520行

if version_type == 'branch':
    # ❌ 问题：只删除当前 collection 的数据
    cur.execute(
        'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
        (collection, version_id)  # ← collection = 版本创建时的单个Collection
    )
    
    # ✓ 已修复：清理正向和反向关联
    cur.execute(
        'DELETE FROM data_relations WHERE (collection = %s OR related_collection = %s) AND branch_id = %s',
        (collection, collection, version_id)
    )
```

**问题分析**：

| 层面 | 问题描述 |
|------|----------|
| **数据模型** | `collection_versions.collection` 只记录单个 Collection |
| **追踪机制** | 缺乏记录版本实际涉及的 Collection 列表 |
| **删除逻辑** | 只根据元数据的单个 Collection 进行清理 |
| **用户行为** | 用户可以在版本分支中自由添加跨 Collection 数据 |

---

### 2.4 影响评估

#### 2.4.1 数据层面影响

**孤立数据累积**：

```
实际发现（问题文档记录）：

SQL查询：
  SELECT collection, branch_id
  FROM dynamic_data
  WHERE branch_id NOT IN (
      SELECT id FROM collection_versions WHERE version_type = 'branch'
  )
  AND branch_id != 'main'

结果：
  inspection-plan (branch: ver-9c69b7b0): 1条孤立数据
  
系统现状：
  - 5个活跃分支版本
  - 存在 inspection-case <-> inspection-plan 跨Collection关联
  - 如果频繁创建/删除版本 → 孤立数据累积
```

**累积趋势预测**：

```
使用频率分析：
  - 版本创建频率：低（不频繁）
  - 版本删除频率：低（不频繁）
  - 跨Collection数据：存在（inspection-case关联inspection-plan）
  
累积模型：
  每次跨Collection版本删除 → 产生 N 条孤立数据
  N = 其他Collection的数据量
  
风险等级：中等
  - 不是高频场景
  - 影响范围有限
  - 但累积效应明显
```

#### 2.4.2 系统层面影响

**违反数据库设计原则**：

```
违反第一范式（原子性）：
  ✓ 数据库中存在无意义的行
  ✓ 这些行无法被业务逻辑访问
  ✗ 数据原子性被破坏
  
违反第三范式（参照完整性）：
  ✓ branch_id 字段指向不存在的版本
  ✓ 缺少外键约束检查（branch_id 无外键）
  ✗ 数据完整性被破坏
  
违反数据一致性：
  ✓ 动态数据表包含无效引用
  ✓ 关联关系已清理但数据残留
  ✗ 数据状态不一致
```

**系统稳定性影响**：

| 影响维度 | 具体表现 | 严重程度 |
|----------|----------|----------|
| **存储空间** | 孤立数据占用磁盘空间 | 低 |
| **查询性能** | 孤立数据不影响正常查询（branch_id过滤） | 无 |
| **数据备份** | 备份包含孤立数据，浪费存储 | 低 |
| **数据恢复** | 恢复后依然存在孤立数据 | 低 |
| **用户信任** | 用户发现数据残留，质疑系统稳定性 | 中 |

---

## 三、解决方案设计

### 3.1 设计目标与原则

**设计目标**：
1. ✅ **数据安全性优先**：确保删除版本时完整清理所有相关数据
2. ✅ **用户知情权**：删除前展示完整影响范围和数据详情
3. ✅ **零风险部署**：不修改现有表结构，只新增独立追踪表
4. ✅ **向后兼容**：现有功能不受影响，平滑升级

**设计原则**：
1. **增量式设计**：新增独立组件，不干扰现有系统
2. **精确追踪**：记录版本实际涉及的 Collection 列表
3. **两阶段确认**：先展示影响，用户确认后执行
4. **分级展示**：大数据量时智能调整UI展示策略

---

### 3.2 方案选择过程

**方案对比矩阵**：

| 方案 | 核心思路 | 优点 | 缺点 | 推荐度 |
|------|----------|------|------|--------|
| **方案A**<br>精确追踪+用户确认 | 新增`version_collections`表<br>记录涉及的所有Collection<br>删除前展示影响范围<br>用户确认后精确清理 | ✅ 精确控制<br>✅ 数据安全<br>✅ 可追溯<br>✅ 长期收益 | ❌ 实现中等复杂<br>❌ 需要新表<br>❌ 需要前端交互 | ⭐⭐⭐⭐⭐<br>推荐 |
| **方案B**<br>保守清理+用户确认 | 不新增表<br>删除时查询branch_id数据<br>按Collection统计<br>用户确认后清理 | ✅ 实现简单<br>✅ 用户知情<br>✅ 立即生效 | ❌ 无法预防<br>❌ 可能误删有意数据<br>❌ 无法追溯 | ⭐⭐⭐⭐<br>备选 |
| **方案C**<br>预防为主+强制禁止 | 创建关联时检查<br>跨Collection分支禁止<br>从根本上避免 | ✅ 实现最简单<br>✅ 从根本上避免<br>✅ 无需交互 | ❌ 功能限制<br>❌ 灵活性差<br>❌ 限制业务场景 | ⭐⭐<br>不推荐 |

**最终选择**：**方案A - 精确追踪 + 用户确认**

**选择理由**：
1. ✅ 完全符合用户的三个关键选择：
   - 数据安全性优先
   - 用户确认机制
   - 愿意修改数据库结构
2. ✅ 精确追踪不会误删数据
3. ✅ 用户确认给了充分的知情权和控制权
4. ✅ 一次投入长期收益，未来可支持更复杂场景

---

### 3.3 核心设计方案

#### 3.3.1 数据库设计

**新增表：`version_collections`**

```sql
CREATE TABLE version_collections (
    version_id  VARCHAR(100) NOT NULL,
    collection  VARCHAR(200) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (version_id, collection),
    FOREIGN KEY (version_id) REFERENCES collection_versions(id) ON DELETE CASCADE
);

CREATE INDEX idx_version_collections_version ON version_collections(version_id);
CREATE INDEX idx_version_collections_collection ON version_collections(collection);
```

**设计考虑**：

```
┌────────────────────────────────────────────┐
│     version_collections 表设计要点          │
├────────────────────────────────────────────┤
│                                            │
│  PRIMARY KEY (version_id, collection)      │
│  ├─ 确保一个版本不会重复记录同一个Collection │
│  ├─ 支持快速查询某个版本涉及的Collection    │
│  └─ 支持快速查询某个Collection涉及的版本    │
│                                            │
│  FOREIGN KEY ... ON DELETE CASCADE         │
│  ├─ 版本删除时自动清理追踪元数据            │
│  ├─ 无需手动维护数据一致性                  │
│  └─ 减少代码复杂度                          │
│                                            │
│  紧凑设计：只需要两个字段                    │
│  ├─ 存储开销小                              │
│  ├─ 查询效率高                              │
│  └─ 维护成本低                              │
│                                            │
└────────────────────────────────────────────┘
```

**数据关系图**：

```
┌──────────────┐
│ collection_  │
│  versions    │
│  (id, ...)   │
└──────┬───────┘
       │
       │ version_id (FK)
       │
       ▼
┌──────────────┐         ┌──────────────┐
│ version_     │         │ dynamic_data │
│ collections  │◀────────│ (id,         │
│ (version_id, │  推断    │  collection, │
│  collection) │         │  branch_id)  │
└──────────────┘         └──────────────┘
       │                         │
       │                         │
       │ 追踪关系                 │ 实际数据
       │                         │
       ▼                         ▼
  精确控制清理范围         分支隔离机制保护
```

**关键优势**：
- ✅ **零风险**：不修改现有表结构
- ✅ **独立组件**：不影响现有功能
- ✅ **自动维护**：CASCADE 自动清理元数据

---

#### 3.3.2 追踪机制设计

**追踪时机**：版本创建后立即追踪

```
版本创建流程：

[阶段1] 创建版本快照
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
create_version_snapshot()
  ├─ 插入 collection_versions 元数据
  ├─ 复制数据到 version_snapshots
  ├─ 复制关联到 version_relations
  └─ 计算数据哈希
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[阶段2] 追踪涉及的Collection
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
track_version_collections()
  ├─ 第一层：扫描直接数据
  │   SELECT DISTINCT collection 
  │   FROM dynamic_data 
  │   WHERE branch_id = version_id
  │
  ├─ 第二层：扫描关联数据
  │   SELECT DISTINCT related_collection 
  │   FROM data_relations 
  │   WHERE branch_id = version_id
  │
  ├─ 合并去重
  │   all_collections = direct + related
  │
  └─ 记录追踪数据
      INSERT INTO version_collections
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**追踪策略流程图**：

```
                开始追踪
                    │
                    ▼
        ┌──────────────────────┐
        │ 扫描 dynamic_data     │
        │ WHERE branch_id = X   │
        └──────────────┬───────┘
                       │
                       ▼
          得到 direct_collections
          例如: [inspection-case]
                       │
                       ▼
        ┌──────────────────────┐
        │ 扫描 data_relations   │
        │ WHERE branch_id = X   │
        └──────────────┬───────┘
                       │
                       ▼
          得到 related_collections
          例如: [inspection-plan]
                       │
                       ▼
        ┌──────────────────────┐
        │ 合并去重             │
        │ set(direct + related)│
        └──────────────┬───────┘
                       │
                       ▼
          all_collections
          例如: [inspection-case, 
                 inspection-plan]
                       │
                       ▼
        ┌──────────────────────┐
        │ INSERT INTO          │
        │ version_collections  │
        └──────────────┬───────┘
                       │
                       ▼
                  追踪完成
```

**核心函数实现**：

```python
def track_version_collections(version_id, collection, branch_id):
    """
    追踪版本涉及的所有 Collection
    
    Parameters:
        version_id: 版本 ID
        collection: 版本创建时的主 Collection
        branch_id: 分支 ID
    """
    with get_db() as conn:
        cur = conn.cursor()
        
        # 第一层：直接数据扫描
        cur.execute(
            'SELECT DISTINCT collection FROM dynamic_data WHERE branch_id = %s',
            (branch_id,)
        )
        direct_collections = [row[0] for row in cur.fetchall()]
        
        # 第二层：关联数据扫描
        cur.execute(
            'SELECT DISTINCT related_collection FROM data_relations WHERE branch_id = %s',
            (branch_id,)
        )
        related_collections = [row[0] for row in cur.fetchall()]
        
        # 合并去重
        all_collections = set(direct_collections + related_collections)
        
        # 记录追踪数据
        now = datetime.now(timezone.utc)
        for coll in all_collections:
            cur.execute(
                'INSERT INTO version_collections (version_id, collection, created_at) '
                'VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                (version_id, coll, now)
            )
```

---

#### 3.3.3 删除流程设计

**两阶段删除机制**：

```
删除版本流程图：

┌─────────────────────────────────────┐
│        用户点击"删除版本"            │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  前端调用 DELETE /api/versions/<id>  │
│  参数: confirmed=false               │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│      后端：delete_version()          │
│      confirmed=False                 │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│   调用 get_version_delete_impact()   │
└──────────────┬──────────────────────┘
               │
               ├─ 查询 version_collections
               │  获取涉及的 Collection 列表
               │
               ├─ 计算每个 Collection 数据量
               │  SELECT COUNT(*) FROM dynamic_data
               │  WHERE collection=X AND branch_id=id
               │
               ├─ 查询数据详情（前100条预览）
               │  SELECT id, data FROM dynamic_data
               │  LIMIT 100
               │
               ├─ 生成影响报告
               │  {
               │    versionInfo: {...},
               │    affectedCollections: [...],
               │    totalRecords: N,
               │    warningMessage: "..."
               │  }
               │
               ▼
┌─────────────────────────────────────┐
│      返回影响报告给前端              │
│      (不执行删除)                    │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│   前端展示删除确认对话框             │
│   ├─ 版本信息                        │
│   ├─ 涉及的Collection列表            │
│   ├─ 数据详情（分级展示）            │
│   ├─ 警告信息                        │
│   └─ [取消] [确认删除]               │
└──────────────┬──────────────────────┘
               │
         ┌─────┴─────┐
         │           │
    [取消]       [确认删除]
         │           │
         ▼           ▼
    关闭对话框   调用 DELETE
         │      confirmed=true
         │           │
         │           ▼
         │  ┌─────────────────────────┐
         │  │ delete_version()         │
         │  │ confirmed=True           │
         │  └────────────┬────────────┘
         │               │
         │               ├─ 查询 version_collections
         │               │  获取所有涉及的 Collection
         │               │
         │               ├─ 精确清理每个 Collection
         │               │  DELETE FROM dynamic_data
         │               │  WHERE collection=X
         │               │  AND branch_id=id
         │               │
         │               ├─ 清理关联关系
         │               │  DELETE FROM data_relations
         │               │  WHERE branch_id=id
         │               │
         │               ├─ 清理版本元数据
         │               │  DELETE FROM collection_versions
         │               │  (CASCADE 清理 version_collections)
         │               │
         │               ▼
         │  ┌─────────────────────────┐
         │  │    返回删除成功          │
         │  └─────────────────────────┘
         │               │
         │               ▼
         │          前端显示成功消息
         │               │
         └───────────────┴─────▶ 结束
```

**核心函数改造**：

```python
def delete_version(version_id, confirmed=False):
    """
    删除版本（改造版：支持用户确认机制）
    
    Parameters:
        version_id: 版本 ID
        confirmed: 是否已确认删除
        
    Returns:
        如果 confirmed=False: 返回影响报告 dict
        如果 confirmed=True: 返回删除成功 bool
    """
    # 未确认：返回影响报告
    if not confirmed:
        return get_version_delete_impact(version_id)
    
    # 已确认：执行删除
    with get_db() as conn:
        cur = conn.cursor()
        
        # 1. 检查版本状态
        cur.execute(
            'SELECT is_protected, version_type FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            return False
        if row[0]:
            raise ValueError('无法删除受保护的版本')
        version_type = row[1]
        
        # 2. 检查子版本
        cur.execute(
            'SELECT COUNT(*) FROM collection_versions WHERE parent_version = %s',
            (version_id,)
        )
        if cur.fetchone()[0] > 0:
            raise ValueError('无法删除：存在子版本')
        
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

---

### 3.4 业务场景生效原理

#### 3.4.1 场景1：单Collection版本（正常场景）

**场景描述**：
用户创建 `inspection-case` 的版本分支，只包含 `inspection-case` 的数据。

```
数据状态：
  dynamic_data:
    - case-001 (inspection-case, branch_id=ver-001)
    - case-002 (inspection-case, branch_id=ver-001)
  
  version_collections:
    - (ver-001, inspection-case) ← 只追踪到1个Collection
```

**删除生效原理**：

```
[步骤1] 查询影响报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
查询 version_collections:
  → [(ver-001, inspection-case)]
  
计算数据量:
  SELECT COUNT(*) FROM dynamic_data
  WHERE collection='inspection-case' AND branch_id='ver-001'
  → 2条
  
生成影响报告:
  {
    affectedCollections: [
      {collection: 'inspection-case', recordCount: 2}
    ],
    totalRecords: 2,
    hasCrossCollectionData: false, ← 单Collection
    warningMessage: "将删除 inspection-case 的2条数据"
  }
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[步骤2] 用户确认并删除
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
查询 version_collections:
  → [inspection-case]
  
精确清理:
  DELETE FROM dynamic_data
  WHERE collection='inspection-case' AND branch_id='ver-001'
  → 删除 case-001, case-002
  
清理关联:
  DELETE FROM data_relations WHERE branch_id='ver-001'
  
删除版本元数据:
  DELETE FROM collection_versions WHERE id='ver-001'
  → CASCADE 删除 version_collections
  
结果:
  ✓ 所有数据正确清理
  ✓ 无孤立数据产生
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

#### 3.4.2 场景2：跨Collection版本（问题场景）

**场景描述**：
用户在 `inspection-case` 版本中添加了跨Collection关联数据。

```
数据状态：
  dynamic_data:
    - case-001 (inspection-case, branch_id=ver-002)
    - plan-001 (inspection-plan, branch_id=ver-002) ← 跨Collection
    - plan-002 (inspection-plan, branch_id=ver-002) ← 跨Collection
  
  data_relations:
    - (inspection-case, case-001, relatedPlan, inspection-plan, plan-001, ver-002)
    - (inspection-case, case-001, relatedPlan, inspection-plan, plan-002, ver-002)
  
  version_collections:
    - (ver-002, inspection-case)  ← 直接数据
    - (ver-002, inspection-plan)  ← 关联数据（新增追踪！）
```

**追踪生效原理**：

```
[创建版本时追踪]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
第一层扫描（直接数据）:
  SELECT DISTINCT collection FROM dynamic_data
  WHERE branch_id='ver-002'
  → [inspection-case, inspection-plan]
  
第二层扫描（关联数据）:
  SELECT DISTINCT related_collection FROM data_relations
  WHERE branch_id='ver-002'
  → [inspection-plan] ← 已包含
  
合并去重:
  all_collections = {inspection-case, inspection-plan}
  
插入追踪:
  INSERT INTO version_collections VALUES
    ('ver-002', 'inspection-case', NOW()),
    ('ver-002', 'inspection-plan', NOW())
  
关键点:
  ✓ 不仅追踪主Collection
  ✓ 还追踪关联的Collection
  ✓ 确保删除时完整清理
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**删除生效原理**：

```
[步骤1] 查询影响报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
查询 version_collections:
  → [(ver-002, inspection-case), (ver-002, inspection-plan)]
  
计算数据量:
  inspection-case: SELECT COUNT(*) → 1条
  inspection-plan: SELECT COUNT(*) → 2条
  
生成影响报告:
  {
    affectedCollections: [
      {collection: 'inspection-case', recordCount: 1, 
       records: [{id: 'case-001', displayName: '巡检用例A'}]},
      {collection: 'inspection-plan', recordCount: 2,
       records: [{id: 'plan-001', displayName: '巡检计划1'},
                 {id: 'plan-002', displayName: '巡检计划2'}]}
    ],
    totalRecords: 3,
    hasCrossCollectionData: true, ← 跨Collection警告！
    warningMessage: "该版本涉及2个Collection，删除将同时清理这些数据"
  }
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[步骤2] 前端展示确认对话框
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
删除版本确认对话框
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
版本信息：
  版本名称：测试巡检用例版本
  涉及Collection数：2个
  总记录数：3条

将要删除的数据：
  ▼ inspection-case (1条记录)
    • case-001 - 巡检用例A
    
  ▼ inspection-plan (2条记录) ← 跨Collection
    • plan-001 - 巡检计划1
    • plan-002 - 巡检计划2

⚠️ 警告：
该版本涉及2个Collection，删除将同时清理
这些数据。请确认是否继续。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
      [取消]  [确认删除]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[步骤3] 用户确认并精确清理
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
查询 version_collections:
  → [inspection-case, inspection-plan]
  
精确清理（关键！）:
  for collection in [inspection-case, inspection-plan]:
    DELETE FROM dynamic_data
    WHERE collection=collection AND branch_id='ver-002'
  
  具体执行:
    DELETE FROM dynamic_data
    WHERE collection='inspection-case' AND branch_id='ver-002'
    → 删除 case-001 ✓
    
    DELETE FROM dynamic_data
    WHERE collection='inspection-plan' AND branch_id='ver-002'
    → 删除 plan-001, plan-002 ✓ ← 关键！清理跨Collection数据
  
清理关联:
  DELETE FROM data_relations WHERE branch_id='ver-002'
  → 删除所有关联关系 ✓
  
删除版本元数据:
  DELETE FROM collection_versions WHERE id='ver-002'
  → CASCADE 删除 version_collections ✓
  
结果:
  ✓ inspection-case 数据清理
  ✓ inspection-plan 数据清理 ← 解决孤立数据问题！
  ✓ 关联关系清理
  ✓ 版本元数据清理
  ✓ 无孤立数据产生
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**对比：未修复前 vs 修复后**

```
┌──────────────────────────────────────────┐
│          未修复前（孤立数据产生）         │
├──────────────────────────────────────────┤
│                                          │
│  删除前：                                 │
│    case-001 (inspection-case) ✓          │
│    plan-001 (inspection-plan) ✓          │
│                                          │
│  删除后：                                 │
│    case-001 删除 ✓                        │
│    plan-001 保留 ✗ ← 孤立数据！           │
│                                          │
│  问题：                                   │
│    plan-001.branch_id指向不存在的版本     │
│                                          │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│          修复后（完整清理）               │
├──────────────────────────────────────────┤
│                                          │
│  删除前：                                 │
│    case-001 (inspection-case) ✓          │
│    plan-001 (inspection-plan) ✓          │
│                                          │
│  删除后：                                 │
│    case-001 删除 ✓                        │
│    plan-001 删除 ✓ ← 正确清理！           │
│                                          │
│  结果：                                   │
│    无孤立数据产生                         │
│    数据一致性保持                         │
│                                          │
└──────────────────────────────────────────┘
```

---

#### 3.4.3 场景3：大数据量版本

**场景描述**：
版本包含大量数据（如500条巡检用例 + 200条巡检计划）。

**前端分级展示策略**：

```
数据量判断逻辑：

if totalRecords <= 50:
  策略 = "直接展开"
  ├─ 显示所有数据详情
  ├─ 无折叠控制
  └─ 适合小数据量
  
elif totalRecords <= 200:
  策略 = "预览模式"
  ├─ 每个Collection显示前10条
  ├─ 折叠控制（用户可展开）
  ├─ 提供"查看完整列表"按钮
  └─ 适合中等数据量
  
else:
  策略 = "摘要模式"
  ├─ 只显示总数和Collection列表
  ├─ 不显示具体数据
  ├─ 强制跳转到详情页查看
  ├─ 支持分页、搜索、排序
  └─ 适合大数据量
```

**大数据量展示示例**：

```
删除版本确认对话框（摘要模式）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

版本信息：
  版本名称：2026年第一季度巡检用例版本
  涉及Collection数：3个
  总记录数：628条
  总关联关系：856条

将要删除的数据概览：
  ┌─────────────────────────────┐
  │ inspection-case (512条)    ▼│
  ├─────────────────────────────┤
  │ • 前10条预览：               │
  │   case-001 - 巡检用例A      │
  │   case-002 - 巡检用例B      │
  │   ...                       │
  │                             │
  │ [查看完整列表] ← 跳转详情页 │
  └─────────────────────────────┘
  
  ┌─────────────────────────────┐
  │ inspection-plan (98条)     ▼│
  ├─────────────────────────────┤
  │ • 前10条预览：               │
  │   plan-001 - 巡检计划       │
  │   ...                       │
  │                             │
  │ [查看完整列表]              │
  └─────────────────────────────┘

⚠️ 警告：
该版本涉及3个Collection，共628条数据。
删除将同时清理这些数据及856条关联关系。
此操作不可撤销，请谨慎确认。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
          [取消]  [确认删除]
```

**详情页分页查询**：

```
用户点击"查看完整列表" → 跳转到详情页

删除数据详情页 - inspection-case
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

版本：2026年第一季度巡检用例版本
Collection：inspection-case
总记录数：512条

搜索/筛选：
  [搜索框] [按创建时间排序 ▼]

数据列表：
  ┌────────────────────────────┐
  │ #  ID       名称    创建时间│
  ├────────────────────────────┤
  │ 1  case-001 用例A   01-05  │
  │ 2  case-002 用例B   01-06  │
  │ ...                        │
  │ 512 case-512 用例Z  03-31  │
  └────────────────────────────┘

  [上一页] [1] [2] ... [26] [下一页]
           每页显示：[20▼]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
          [返回确认对话框]
```

**API支持**：

```python
@versions_bp.route('/versions/<version_id>/delete-detail', methods=['GET'])
@login_required
def get_delete_detail(version_id):
    """
    获取删除数据详情（支持分页）
    
    Query参数：
      - collection: 查询哪个Collection
      - page: 页码（默认1）
      - pageSize: 每页数量（默认20）
      - sortBy: 排序字段
      - sortOrder: 排序方向
    """
    # ... 分页查询逻辑
```

---

## 四、实施计划

### 4.1 实施阶段划分

```
实施时间线（总计：4-6天）

第1-2天：数据库和核心逻辑
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
任务：
  ├─ 创建 version_collections 表
  ├─ 实现 track_version_collections()
  ├─ 改造 delete_version()
  └─ 实现 get_version_delete_impact()
  
依赖：无
  
验收：
  ✓ 单元测试通过
  ✓ 数据库表创建成功
  ✓ 跨Collection追踪正确
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

第2-3天：API层
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
任务：
  ├─ 新增 DELETE /api/versions/<id>/delete-impact
  ├─ 新增 GET /api/versions/<id>/delete-detail
  └─ 改造 DELETE /api/versions/<id>
  
依赖：第一阶段完成
  
验收：
  ✓ API端点正确响应
  ✓ 两阶段流程正确
  ✓ 分页查询正确
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

第3天：数据迁移
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
任务：
  ├─ 编写迁移脚本
  ├─ 运行迁移（补充现有版本追踪数据）
  ├─ 验证迁移结果
  └─ 清理孤立数据（如有）
  
依赖：第一、二阶段完成
  
验收：
  ✓ 所有版本有追踪数据
  ✓ 无孤立数据残留
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

第3-5天：前端UI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
任务：
  ├─ 改造 VersionManager.vue 删除逻辑
  ├─ 实现分级展示策略
  ├─ 实现数据详情页（可选）
  └─ 适配两阶段API调用
  
依赖：第二阶段完成
  
验收：
  ✓ 删除前展示影响报告
  ✓ 用户确认后删除
  ✓ 大数据量UI友好
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

第5-6天：测试和文档
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
任务：
  ├─ 运行单元测试、集成测试
  ├─ 性能测试
  ├─ 手动测试边界情况
  ├─ 更新用户手册、API文档
  └─ 部署到生产环境
  
依赖：所有阶段完成
  
验收：
  ✓ 所有测试通过
  ✓ 性能达标
  ✓ 文档完整
  ✓ 生产环境验证
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 4.2 数据迁移策略

**迁移目标**：为所有现有的 `branch` 类型版本补充 `version_collections` 数据。

**迁移脚本核心逻辑**：

```python
def migrate_version_collections():
    """为现有版本补充追踪数据"""
    
    # 1. 查询所有活跃的分支版本
    cur.execute(
        'SELECT id, collection FROM collection_versions '
        'WHERE version_type = \'branch\' AND status != \'merged\''
    )
    versions = cur.fetchall()
    
    # 2. 为每个版本扫描数据并追踪
    for version_id, collection in versions:
        # 检查是否已有追踪数据（避免重复）
        if has_tracking_data(version_id):
            continue
        
        # 扫描直接数据
        direct = scan_direct_data(version_id)
        
        # 扫描关联数据
        related = scan_related_data(version_id)
        
        # 合并去重并插入
        all_collections = set(direct + related)
        insert_tracking_data(version_id, all_collections)
```

**执行步骤**：

```bash
# 1. 部署新功能后运行迁移
cd server
python scripts/migrate_version_collections.py

# 2. 验证迁移结果
python scripts/migrate_version_collections.py
# 输出：
#   成功迁移: X 个版本
#   已有数据跳过: Y 个版本
#   ✓ 无孤立数据

# 3. 如需清理孤立数据（可选）
python scripts/migrate_version_collections.py --cleanup
```

---

### 4.3 测试策略

**测试覆盖矩阵**：

| 测试类型 | 测试场景 | 验收标准 |
|----------|----------|----------|
| **单元测试** | 单Collection追踪<br>跨Collection追踪<br>影响报告生成<br>确认删除流程 | ✓ 追踪数据正确<br>✓ 影响报告完整<br>✓ 删除清理完整 |
| **集成测试** | 两阶段API流程<br>前后端联调 | ✓ API正确响应<br>✓ 流程顺序正确 |
| **性能测试** | 大数据量追踪<br>大数据量查询<br>大数据量删除 | ✓ 追踪<2s<br>✓ 影响<3s<br>✓ 删除<5s |
| **边界测试** | 版本不存在<br>版本受保护<br>有子版本<br>数据不一致 | ✓ 错误提示正确<br>✓ 降级处理正确 |

---

## 五、风险评估与缓解

### 5.1 风险清单

| 风险 | 描述 | 严重程度 | 缓解措施 |
|------|------|----------|----------|
| **数据迁移发现孤立数据** | 历史遗留孤立数据影响用户信任 | 中 | ✓ 提供清理功能<br>✓ 清理前确认<br>✓ 详细日志 |
| **前端大数据量性能** | 上千条数据渲染卡顿 | 中 | ✓ 分级展示<br>✓ 默认限制预览<br>✓ 分页查询 |
| **用户不理解跨Collection** | 不理解为什么删除case会影响plan | 低 | ✓ 清晰警告信息<br>✓ 展示数据详情<br>✓ 用户手册说明 |
| **数据库结构变更** | 修改现有表结构风险 | 无（已消除） | ✓ 只新增表<br>✅ 不修改现有表 |

---

## 六、文档更新清单

### 6.1 需要更新的文档

**用户手册**：
- 新增章节：版本删除注意事项
- 说明：跨Collection版本的删除影响范围

**API文档**：
- 新增：`GET /api/versions/<id>/delete-impact`
- 新增：`GET /api/versions/<id>/delete-detail`
- 更新：`DELETE /api/versions/<id>` (confirmed参数)

**开发文档**：
- 新增：version_collections 表说明
- 新增：追踪机制说明
- 新增：两阶段删除流程
- 新增：数据迁移步骤

---

## 七、总结

### 7.1 方案核心

**一句话总结**：
通过新增 `version_collections` 表精确追踪版本涉及的所有Collection，配合两阶段用户确认机制，确保删除版本时完整清理所有相关数据，避免孤立数据产生。

### 7.2 关键优势

```
┌────────────────────────────────────────┐
│        方案关键优势一览                 │
├────────────────────────────────────────┤
│                                        │
│  ✅ 数据安全性优先                      │
│     精确追踪，不会误删数据              │
│                                        │
│  ✅ 用户知情权                          │
│     删除前展示完整影响范围和数据详情    │
│                                        │
│  ✅ 零风险部署                          │
│     只新增表，不修改现有表结构          │
│                                        │
│  ✅ 向后兼容                            │
│     现有功能不受影响                    │
│                                        │
│  ✅ 性能优化                            │
│     大数据量分级展示和分页查询          │
│                                        │
│  ✅ 测试覆盖                            │
│     单元、集成、性能、边界测试完整      │
│                                        │
└────────────────────────────────────────┘
```

### 7.3 实施预期

**时间投入**：4-6天

**收益评估**：
- ✅ 解决中等风险的数据一致性问题
- ✅ 避免孤立数据累积
- ✅ 提升用户对系统稳定性的信任
- ✅ 为未来更复杂的版本管理场景奠定基础

---

**设计文档结束**