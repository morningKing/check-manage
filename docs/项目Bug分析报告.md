# Check-Manage 项目 Bug 分析报告

## 文档概述

本文档记录在项目理解过程中发现的设计问题和功能缺陷，分为设计类问题和功能类问题。每个问题都附有详细分析、影响范围和建议修复方案。

---

## 一、设计类问题

### 1. [设计缺陷] 版本删除时跨 Collection 数据清理不完整

**问题描述**:
当删除一个涉及多个 Collection 的分支版本时，`version_collections` 表记录的 Collection 列表可能不完整，导致部分数据未被清理。

**发现位置**: `server/utils/version.py:delete_version`

**代码分析**:
```python
# 第 769-786 行
if version_type == 'branch':
    # 查询涉及的 Collection
    cur.execute(
        'SELECT collection FROM version_collections WHERE version_id = %s',
        (version_id,)
    )
    collections = [row[0] for row in cur.fetchall()]
    
    # 如果无追踪数据，使用旧方法（仅清理主Collection）
    # 兼容 Task 1-3 实施前创建的旧版本
    if not collections:
        collections = [collection]  # 仅清理创建版本时的主 Collection
```

**问题**:
- 如果 `version_collections` 表数据不完整或版本创建时未正确追踪，只清理主 Collection
- 其他关联 Collection 的分支数据会残留，造成数据孤立
- 影响：数据一致性风险，磁盘空间浪费

**建议修复**:
1. 当 `version_collections` 为空时，应通过 BFS 扫描 `version_snapshots` 表获取所有涉及的 Collection
2. 或在版本创建时强制确保 `track_version_collections` 正确执行
3. 添加数据清理验证日志

---

### 2. [设计缺陷] 分支切换时的数据初始化缺乏并发保护

**问题描述**:
当多个用户同时切换到一个未初始化的分支时，可能导致数据复制冲突。

**发现位置**: `server/utils/version.py:copy_data_to_branch`

**代码分析**:
```python
def copy_data_to_branch(collection, source_branch_id, target_branch_id):
    # 复制 dynamic_data
    cur.execute(
        "INSERT INTO dynamic_data (...) "
        "SELECT ... FROM dynamic_data WHERE ... "
        "ON CONFLICT (id, branch_id) DO NOTHING",  # 仅跳过，不报错
        ...
    )
```

**问题**:
- `ON CONFLICT DO NOTHING` 在并发情况下可能导致数据不完整
- 如果两个用户同时执行复制，部分数据可能被跳过
- 没有锁机制防止并发初始化

**建议修复**:
1. 添加分支初始化状态标记（`is_initialized` 字段）
2. 使用数据库锁或乐观锁机制防止并发初始化
3. 或在 `switch_to_version` 中添加初始化检查逻辑

---

### 3. [设计缺陷] 备份还原时 TRUNCATE CASCADE 可能删除不应删除的数据

**问题描述**:
在 Collection 级还原时，使用 `TRUNCATE CASCADE` 可能意外删除其他 Collection 的关联数据。

**发现位置**: `server/utils/backup.py:restore_backup`

**代码分析**:
```python
# 第 517-518 行
else:
    # 全表还原，使用 TRUNCATE
    cur.execute(f'TRUNCATE TABLE {table_name} CASCADE')
```

**问题**:
- `TRUNCATE CASCADE` 会级联删除所有外键依赖的数据
- 对于 `version_snapshots` 表，CASCADE 会删除 `collection_versions` 记录
- 可能导致意外的数据丢失

**建议修复**:
1. 对于有外键依赖的表，使用 `DELETE` 代替 `TRUNCATE`
2. 或在还原前禁用外键检查：`SET session_replication_role = replica`
3. 明确还原范围，避免级联影响

---

### 4. [设计风险] 触发器执行失败不阻塞数据操作

**问题描述**:
触发器执行失败时仅记录日志，不影响原始数据操作的成功状态。

**发现位置**: `server/utils/trigger_engine.py`

**代码分析**:
```python
try:
    _execute_action(...)
    _log_trigger(..., 'success', None)
except Exception as e:
    _log_trigger(..., 'error', str(e))
    # 异常被捕获，原始操作继续执行
```

**问题**:
- 用户可能不知道触发器执行失败
- 业务逻辑可能依赖触发器结果
- 数据不一致风险

**建议修复**:
1. 添加触发器执行状态通知机制
2. 对于关键触发器，允许配置为"失败阻塞"
3. 前端显示触发器执行状态

---

### 5. [设计缺陷] 用户分支状态缺乏并发同步机制

**问题描述**:
`user_current_branch` 表仅记录用户最后的分支设置，无版本控制。

**发现位置**: `server/utils/version.py`

**代码分析**:
```python
def set_user_current_branch(user_id, username, collection, branch_id):
    # Upsert，无版本控制
    cur.execute(
        'INSERT INTO user_current_branch ... '
        'ON CONFLICT (user_id, collection) DO UPDATE SET branch_id = %s',
        ...
    )
```

**问题**:
- 无法追溯分支切换历史
- 无法检测并发切换冲突
- 切换前无法验证数据一致性

**建议修复**:
1. 添加 `previous_branch_id` 字段记录切换历史
2. 添加切换时间戳和验证哈希
3. 实现切换前数据状态检查

---

### 6. [设计缺陷] 版本快照大小限制硬编码

**问题描述**:
快照大小限制 `max_records=10000` 硬编码，无法根据实际情况调整。

**发现位置**: `server/utils/version_scan.py`

**代码分析**:
```python
def scan_all_related_data(start_collection, branch_id, max_records=10000):
    # 硬编码限制
```

**问题**:
- 小型项目可能不需要限制
- 大型项目可能需要更大限制
- 无法根据 Collection 特性调整

**建议修复**:
1. 将限制值存储到配置表或环境变量
2. 允许管理员配置默认限制
3. 允许创建版本时指定限制

---

### 7. [设计风险] 脚本沙箱安全依赖正则检查

**问题描述**:
脚本沙箱安全检查依赖正则表达式匹配禁用语句，可能存在绕过风险。

**发现位置**: `server/utils/script_runner.py`

**代码分析**:
```python
# 正则检查禁用语句
if re.search(r'\bimport\b', script): raise ValueError('禁止 import')
if re.search(r'\bexec\b', script): raise ValueError('禁止 exec')
```

**问题**:
- 正则表达式可能无法覆盖所有绕过方式
- 例如：`__builtins__['exec']` 或 `getattr(__builtins__, 'exec')`
- 虽然后续有受限 globals，但正则检查不完整

**建议修复**:
1. 增强正则检查覆盖更多绕过方式
2. 考虑使用 AST 分析代替正则
3. 定期安全审计

---

### 8. [设计缺陷] 备份调度器单线程设计

**问题描述**:
备份调度器使用单线程轮询，可能在高负载时延迟。

**发现位置**: `server/utils/backup.py:start_backup_scheduler`

**代码分析**:
```python
def scheduler_loop():
    while True:
        time.sleep(60)  # 每分钟检查一次
```

**问题**:
- 单线程阻塞设计
- 如果备份耗时较长，下次检查可能延迟
- 无法并行处理多个备份任务

**建议修复**:
1. 使用多线程或异步调度器
2. 或集成到专业调度系统（如 APScheduler）
3. 添加备份队列管理

---

## 二、功能类问题

### 1. [功能缺陷] 版本对比不支持关联字段标签显示

**问题描述**:
版本对比时，关联字段显示的是 ID 列表，而非可读标签。

**发现位置**: `server/routes/versions.py:diff_versions`

**代码分析**:
```python
# 关联字段直接显示 ID
relation_fields = [f for f in fields if f.get('controlType') == 'relation']
# 未将 ID 转换为显示名称
```

**问题**:
- 用户看到关联字段变化时是一串 ID
- 无法直观理解关联关系变化
- 对比报告可读性差

**建议修复**:
1. 关联字段对比时查询目标 Collection 的显示名称
2. 显示格式：`[旧记录名1, 旧记录名2] → [新记录名1, 新记录名2]`
3. 参考 `backups.py:_resolve_relation_labels` 实现

---

### 2. [功能缺陷] 分支切换后前端未自动刷新关联 Collection

**问题描述**:
分支切换涉及多个 Collection，但前端仅刷新当前 Collection 的数据。

**发现位置**: `src/components/common/VersionManager.vue`

**代码分析**:
```typescript
// emit('refresh', result.affectedCollections)
// 父组件可能未处理 affectedCollections
```

**问题**:
- 用户在其他 Collection 页面可能看到旧分支数据
- 数据不一致风险
- 需手动刷新每个页面

**建议修复**:
1. 前端全局状态管理，通知所有涉及的 Collection
2. 或显示提示，让用户知道需要刷新其他页面
3. 自动刷新已打开的标签页

---

### 3. [功能缺陷] 备份列表未显示 Collection 分组详情

**问题描述**:
表级备份的 `backup_tables` 显示原始格式（如 `dynamic_data:inspection-case`），未转换为可读名称。

**发现位置**: `src/views/admin/BackupManager.vue`

**代码分析**:
```typescript
// 第 95-101 行
<el-tooltip v-if="row.backupScope === 'partial' && row.backupTables?.length">
  <template #content>
    {{ row.backupTables.map((t: string) => tableLabelMap[t] || t).join('、') }}
  </template>
```

**问题**:
- `tableLabelMap` 可能不包含所有 Collection
- 显示原始 Collection 名称而非页面名称
- 用户可能不理解备份范围

**建议修复**:
1. 后端返回时转换 `dynamic_data:xxx` 格式为页面名称
2. 前端增强映射逻辑
3. 显示更友好的备份范围描述

---

### 4. [功能缺陷] 版本删除详情分页不支持排序字段验证

**问题描述**:
删除详情分页查询的排序字段仅做了简单映射，可能存在 SQL 注入风险。

**发现位置**: `server/routes/versions.py:get_delete_detail_route`

**代码分析**:
```python
# 第 506-508 行
allowed_sort_fields = {'createdAt': 'created_at', 'updatedAt': 'updated_at', 'id': 'id'}
sort_column = allowed_sort_fields.get(sort_by, 'created_at')  # 默认值
# 安全，但未覆盖所有场景
```

**问题**:
- 映射表限制了字段，但可能遗漏
- 无显式白名单验证

**建议修复**:
1. 添加显式白名单验证
2. 如果字段不在允许列表，返回错误而非默认值
3. 或前端限制选项

---

### 5. [功能缺陷] 部分合并未处理关联字段变更

**问题描述**:
部分合并时，用户对记录的字段选择可能包含关联字段，但合并逻辑未正确处理关联关系更新。

**发现位置**: `server/utils/version.py:apply_partial_merge`

**代码分析**:
```python
# 第 1302-1307 行
# 重建关联关系
_replace_collection_relations(
    cur,
    collection,
    target_branch_id,
    _load_collection_relation_rows(cur, collection, target_branch_id),
)
```

**问题**:
- 如果用户选择合并某些关联字段，关联关系可能未正确更新
- `_replace_collection_relations` 重建所有关系，可能覆盖用户选择

**建议修复**:
1. 合并时需要处理关联字段的特殊逻辑
2. 或在 `modified_records` 中明确排除关联字段
3. 添加关联字段合并的独立处理流程

---

### 6. [功能缺陷] 版本状态未设置时默认行为不明确

**问题描述**:
创建版本时未显式设置 `status`，依赖数据库默认值。

**发现位置**: `server/utils/version.py:create_version_snapshot`

**代码分析**:
```python
# 第 245-252 行
cur.execute(
    'INSERT INTO collection_versions '
    '(id, collection, name, ..., status, ...) '
    'VALUES (%s, %s, %s, ..., %s, ...)',  # status 显式传入
    (..., 'active', ...),  # 正确传入
)
```

**说明**:
代码已正确传入 `'active'`，但其他函数可能依赖默认值，建议统一显式设置。

---

### 7. [功能缺陷] 备份还原时禁用触发器后恢复可能失败

**问题描述**:
还原后恢复触发器 `SET session_replication_role = DEFAULT` 在单独 try 中执行，可能被忽略。

**发现位置**: `server/utils/backup.py:restore_backup`

**代码分析**:
```python
# 第 571-576 行
finally:
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('SET session_replication_role = DEFAULT')
        except Exception:
            pass  # 异常被忽略
```

**问题**:
- 如果恢复触发器失败，数据库可能处于异常状态
- 后续操作可能绕过触发器检查
- 无日志记录

**建议修复**:
1. 添加恢复触发器失败日志
2. 或在主事务中执行恢复
3. 添加数据库状态检查机制

---

### 8. [功能缺陷] 关键字搜索未处理 Collection 分支隔离

**问题描述**:
关键字搜索时，关联字段搜索可能未正确应用分支隔离。

**发现位置**: `server/routes/dynamic.py:build_keyword_conditions`

**代码分析**:
```python
# 第 187-196 行
sql = '''
    SELECT DISTINCT dr.record_id
    FROM data_relations dr
    JOIN dynamic_data dd ON ...
    WHERE dr.collection = %s AND dr.field_name = %s AND dr.branch_id = %s
    AND dd.data->>%s ILIKE %s
'''
```

**说明**:
代码已正确传入 `branch_id` 参数，分支隔离正确实现。

---

## 三、潜在的改进建议

### 1. 版本管理功能增强

| 功能 | 当前状态 | 建议 |
|------|---------|------|
| 版本锁定 | 无 | 添加版本锁定机制，防止并发修改 |
| 版本标签 | 无 | 支持版本标签（如 v1.0, release） |
| 版本备注 | 有 | 支持富文本备注 |
| 版本对比导出 | 无 | 支持导出对比报告 |

### 2. 分支管理功能增强

| 功能 | 当前状态 | 建议 |
|------|---------|------|
| 分支合并历史 | 有 | 增强合并可视化 |
| 分支差异统计 | 有 | 添加差异趋势图 |
| 分支权限控制 | 无 | 支持分支级权限控制 |
| 分支命名规范 | 无 | 添加分支命名规范检查 |

### 3. 备份功能增强

| 功能 | 当前状态 | 建议 |
|------|---------|------|
| 备份压缩选项 | 无 | 支持不同压缩级别 |
| 备份加密 | 无 | 支持备份文件加密 |
| 远程备份存储 | 无 | 支持云存储备份 |
| 备份恢复预览 | 有 | 增强预览功能 |

---

## 四、总结

### 问题分类统计

| 类型 | 数量 | 优先级 |
|------|------|--------|
| 设计缺陷 | 8 | 中高 |
| 功能缺陷 | 8 | 中 |

### 关键修复建议优先级

1. **高优先级**：跨 Collection 版本删除数据清理
2. **高优先级**：分支切换并发保护
3. **中优先级**：备份还原级联删除风险
4. **中优先级**：触发器执行失败通知

---

*报告版本: 2026-04-16*
*审核状态: 待验证*