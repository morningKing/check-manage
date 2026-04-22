# 跨项目数据依赖版本管理设计

## 一、问题定义

### 1.1 本项目的"跨项目"概念

本项目采用**工作空间 → 项目 → 数据菜单**的三层层级结构：
- **工作空间(Workspace)**：一级菜单，可动态创建
- **项目(Project)**：二级菜单，挂载在工作空间下
- **数据菜单(Data)**：三级菜单，挂载在项目下，对应一个 collection

**跨项目依赖**指：项目A中的数据（collection X）引用项目B中的数据（collection Y），例如：
```
项目A「巡检执行」
  └─ 数据「巡检记录」(collection: inspection-record)
       └─ 字段「巡检用例」(relation) → 引用项目B的「巡检用例」(collection: inspection-case)

项目B「巡检配置」
  └─ 数据「巡检用例」(collection: inspection-case)
       └─ 字段「设备信息」(reference) → 引用项目C的「设备信息」(collection: device-info)
```

### 1.2 二维矩阵问题

| 项目B分支 \ 项目A分支 | main | feat-优化巡检 | hotfix-修复BUG |
|---|---|---|---|
| main | ✓ 合法 | ❌ 外键断裂风险 | ⚠ 需验证 |
| feat-新增用例 | ❌ 版本不匹配 | ✓ 配套分支 | ❌ 数据冲突 |
| archive-v1 | ⚠ 只读引用 | ❓ 未声明 | ❌ 不合法 |

**风险类型**：
- ❌ **硬错误**：外键指向的记录在目标分支不存在
- ⚠ **软风险**：目标分支数据结构变化，引用可能失效
- ❓ **未知状态**：未显式声明依赖，系统无法检测问题

---

## 二、核心设计：依赖声明层 (Dependency Manifest)

### 2.1 设计理念

借鉴软件包管理（npm/cargo）的思想，但针对**有状态数据依赖**进行特殊处理：
- **显式声明**：依赖关系必须通过 manifest 显式声明，不能仅靠外键值隐含
- **版本锁定**：每个项目分支声明依赖其他项目的哪个版本/分支
- **双向登记**：被依赖方必须知道谁依赖了自己，才能在变更时通知下游

### 2.2 数据结构设计

#### 表：project_dependencies（项目依赖声明）

```sql
CREATE TABLE project_dependencies (
    id              VARCHAR(100) PRIMARY KEY,
    source_project  VARCHAR(100) NOT NULL,    -- 声明方项目ID
    source_branch   VARCHAR(100) NOT NULL,    -- 声明方分支ID ('main' 或版本ID)
    target_project  VARCHAR(100) NOT NULL,    -- 被依赖方项目ID
    target_branch   VARCHAR(100) NOT NULL,    -- 被依赖方分支ID
    relation_type   VARCHAR(20) NOT NULL,     -- 'read-write' | 'read-only' | 'track-main'
    pinned_version  VARCHAR(100),             -- 精确钉住的版本ID（可选）
    is_validated    BOOLEAN DEFAULT FALSE,    -- 是否已通过合法性校验
    validation_error TEXT,                    -- 校验失败原因
    declared_by     VARCHAR(100),             -- 声明者用户名
    declared_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_project, source_branch, target_project)
);
```

#### 表：project_dependency_relations（依赖涉及的关联关系）

```sql
CREATE TABLE project_dependency_relations (
    id              VARCHAR(100) PRIMARY KEY,
    dependency_id   VARCHAR(100) NOT NULL,    -- 关联 project_dependencies.id
    source_collection VARCHAR(100) NOT NULL,  -- 源 collection
    source_field    VARCHAR(100) NOT NULL,    -- 关联字段名
    target_collection VARCHAR(100) NOT NULL,  -- 目标 collection
    estimated_records INTEGER,                -- 预估关联记录数
    validation_status VARCHAR(20),            -- 'valid' | 'broken' | 'warning' | 'unknown'
    validation_detail TEXT,                   -- 校验详情
    FOREIGN KEY (dependency_id) REFERENCES project_dependencies(id)
);
```

#### 表：project_dependency_events（变更事件记录）

```sql
CREATE TABLE project_dependency_events (
    id              VARCHAR(100) PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,     -- 'schema_change' | 'branch_delete' | 'version_merge' | 'validation_fail'
    source_project  VARCHAR(100),             -- 事件源项目
    source_branch   VARCHAR(100),             -- 事件源分支
    affected_dependencies VARCHAR[],          -- 受影响的依赖ID列表
    severity        VARCHAR(20),              -- 'critical' | 'warning' | 'info'
    message         TEXT,                     -- 事件描述
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,              -- 解决时间
    resolved_by     VARCHAR(100),             -- 解决者
);
```

---

## 三、三种依赖模式

### 3.1 跟随主干 (Track Main)

**适用场景**：项目A对项目B的数据只做只读引用，容忍B的变更

```
项目依赖声明：
  target_project: 项目B-巡检配置
  target_branch: main           # 跟随主干最新
  relation_type: track-main
  pinned_version: null          # 不钉住特定版本
```

**规则**：
- 自动接收 target main 分支的数据更新
- target 发生破坏性 Schema 变更时，收到**阻塞性告警**
- 告警期间禁止源项目分支的新提交

### 3.2 配套分支 (Coordinated Branch)

**适用场景**：项目A的分支 feat-X 与项目B的分支 feat-X 配套开发

```
项目依赖声明：
  target_project: 项目B-巡检配置
  target_branch: feat-新增用例   # 明确指定配套分支
  relation_type: read-write
  pinned_version: prj-ver-abc123  # 可选：钉住创建时的快照
```

**规则**：
- 双方分支必须同时存在才能工作
- 合并时需要**联合合并**：先合并 target，校验完整性，再合并 source
- target 分支删除前必须解除依赖

### 3.3 精确钉住 (Pinned Version)

**适用场景**：项目A需要引用项目B某个特定历史版本的数据

```
项目依赖声明：
  target_project: 项目B-巡检配置
  target_branch: archive-v1     # 历史归档版本
  relation_type: read-only
  pinned_version: prj-ver-old123  # 精确钉住版本ID
```

**规则**：
- 数据完全隔离，不受 target 任何变更影响
- 适合审计、历史数据分析场景
- pinned_version 被删除时需要通知

---

## 四、依赖生命周期管理

### 4.1 创建依赖声明

```
用户操作：在项目A创建分支时，声明依赖项目B的某个版本

流程：
1. 用户选择依赖模式（track-main / 配套分支 / 精确钉住）
2. 系统写入 project_dependencies 记录
3. 系统扫描 source collection 的关联字段，写入 project_dependency_relations
4. 系统执行依赖合法性校验（见 4.4）
5. 在 target 项目标注「被项目A/feat-X 依赖」，target 维护者收到通知
```

### 4.2 依赖合法性校验

**校验维度**：
1. **分支存在性**：target 分支是否存在
2. **数据可达性**：外键指向的记录在 target 分支是否存在
3. **Schema 兼容性**：关联字段的类型是否匹配
4. **环检测**：是否存在 A→B→A 的循环依赖

**校验时机**：
- 创建依赖声明时
- 切换分支时
- target 发生变更时（触发重新校验）

### 4.3 变更通知与阻塞

**场景：target 主干发生破坏性 Schema 变更**

```
事件：项目B.main 将 customer_id 从 int 改为 UUID

系统响应：
1. 查询 project_dependencies，找出所有 track-main 模式的依赖方
2. 通过 project_dependency_events 记录事件
3. 向依赖方负责人发送阻塞性告警
4. 将依赖方分支标记「上游有破坏性变更待处理」
5. 阻止依赖方分支的新数据提交
```

**处理选项**：
- **升级适配**：修改 source 的字段类型，重新校验
- **钉住旧版本**：将依赖改为精确钉住变更前的版本
- **临时解除**：解除依赖声明（风险自负）

### 4.4 跨项目联合合并

**流程**：
```
用户请求：合并项目A.feat-X 到 main

系统检测：
1. 查询 project_dependencies，发现依赖项目B.feat-X
2. 检查 target 分支状态：
   - 若 target 未合并 → 阻止合并，提示「依赖方尚未就绪」
   - 若 target 已合并 → 继续流程
3. 编排联合合并会话：
   a. 切换到 main 分支
   b. 合并 target 分支数据
   c. 执行外键完整性校验
   d. 合并 source 分支数据
   e. 更新依赖声明：target_branch 改为 'main'
4. 任意步骤失败 → 整体回滚
```

### 4.5 分支删除保护

**规则**：
```
用户请求：删除项目B.feat-X 分支

系统检测：
1. 查询 project_dependencies，检查是否有依赖方
2. 有依赖方 → 拒绝删除，列出所有依赖方项目/分支
3. 依赖方解除声明或迁移后 → 方可删除
```

---

## 五、三层架构设计

### 5.1 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     全局协调层 (Global Coordinator)              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ 依赖图解析  │  │ 合法性校验 │  │ 跨项目合并编排          │  │
│  │ Dependency  │  │ Validator   │  │ Merge Orchestrator     │  │
│  │ Graph       │  │             │  │                         │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              变更通知总线 (Change Notification Bus)         ││
│  │  Schema变更 · 分支删除 · 版本合并 · 校验失败               ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   中间层 (Manifest Registry)                    │
│  ┌─────────────────────┐  ┌──────────────────────────────────┐ │
│  │ project_dependencies │  │ project_dependency_relations    │ │
│  │ 依赖声明存储         │  │ 关联关系详情                    │ │
│  └─────────────────────┘  └──────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              project_dependency_events (事件记录)          ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   底层 (Independent Projects)                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │   项目A     │  │   项目B     │  │   项目C     │             │
│  │ dynamic_data│  │ dynamic_data│  │ dynamic_data│             │
│  │ data_relations│ │ data_relations│ │ data_relations│         │
│  │ project_versions│ │ project_versions│ │ project_versions│   │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│  各项目不感知彼此存在，通过 manifest 声明依赖                   │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 核心模块设计

#### utils/cross_project_dependency.py

```python
"""
跨项目依赖管理核心逻辑

职责：
- 依赖声明管理（创建、更新、解除）
- 依赖合法性校验
- 变更通知与阻塞
- 联合合并编排
"""

def declare_project_dependency(
    source_project, source_branch,
    target_project, target_branch,
    relation_type, pinned_version,
    declared_by
):
    """创建项目依赖声明"""
    pass

def validate_dependency(dependency_id):
    """校验依赖合法性"""
    pass

def check_upstream_changes(target_project, target_branch):
    """检查上游变更，触发通知"""
    pass

def orchestrate_cross_project_merge(source_project, source_branch):
    """编排跨项目联合合并"""
    pass

def check_branch_delete_protection(project, branch):
    """检查分支删除保护"""
    pass
```

#### routes/cross_project_dependencies.py

```python
"""
跨项目依赖 API 路由

端点：
- GET    /projects/:projectId/dependencies         # 获取项目的依赖列表
- POST   /projects/:projectId/dependencies         # 创建依赖声明
- PUT    /projects/:projectId/dependencies/:depId  # 更新依赖声明
- DELETE /projects/:projectId/dependencies/:depId  # 解除依赖声明
- POST   /dependencies/:depId/validate             # 触发依赖校验
- GET    /projects/:projectId/dependency-events    # 获取依赖事件列表
- POST   /projects/:projectId/merge-with-dependencies # 联合合并
"""
```

---

## 六、与现有系统的集成

### 6.1 与 project_versions 的关系

| 现有概念 | 新增概念 | 关系 |
|---|---|---|
| project_versions | project_dependencies | 项目分支通过 dependencies 声明对其他项目的依赖 |
| project_version_snapshots | pinned_version | 精确钉住模式引用具体的快照版本ID |
| user_current_project_branch | - | 切换分支时需检查依赖合法性 |

### 6.2 与 data_relations 的关系

`data_relations` 记录**行级外键关系**，`project_dependency_relations` 记录**项目级依赖涉及的关联关系**：

```sql
-- data_relations (行级)
(collection, record_id, field_name) → (related_collection, related_id)

-- project_dependency_relations (项目级)
(source_collection, source_field) → (target_collection)
```

**集成方式**：
- 创建依赖声明时，从 page_configs.fields 扫描所有 relation/reference/quoteSelect 字段
- 校验时，遍历 data_relations 检查外键完整性

### 6.3 前端集成

#### 依赖声明管理界面

在 `ProjectVersionManager.vue` 中新增：
- 创建分支时可选择依赖其他项目
- 显示当前分支的依赖状态
- 变更告警提示与处理选项

#### 依赖关系可视化

新增 `CrossProjectDependencyGraph.vue`：
- 展示跨项目依赖关系图
- 显示依赖合法性状态
- Schema 变更传播路径

---

## 七、实施计划

### Phase 1：基础设施（预计 3 天）

1. 创建数据库表结构
   - project_dependencies
   - project_dependency_relations
   - project_dependency_events

2. 实现依赖声明基础 API
   - CRUD 操作
   - 关联关系扫描

### Phase 2：校验与通知（预计 4 天）

1. 实现依赖合法性校验
   - 分支存在性
   - 数据可达性
   - Schema 兼容性

2. 实现变更通知机制
   - Schema 变更检测
   - 分支删除保护
   - 告警与阻塞

### Phase 3：联合合并（预计 3 天）

1. 实现跨项目联合合并
   - 合并前检查
   - 原子性编排
   - 回滚机制

### Phase 4：前端集成（预计 2 天）

1. 依赖声明管理界面
2. 依赖关系可视化
3. 变更告警提示

---

## 八、核心设计原则总结

| 原则 | 说明 | 实现方式 |
|---|---|---|
| **显式性** | 依赖必须显式声明，不能仅靠外键隐含 | project_dependencies 表强制登记 |
| **版本化** | 依赖指向特定版本/分支，声明修改也被版本化 | source_branch + target_branch + pinned_version |
| **双向登记** | 被依赖方知道谁依赖自己 | 查询 project_dependencies by target_project |
| **强制校验** | 切换分支、合并前必须校验 | validate_dependency() 阻塞式调用 |
| **变更传播** | 上游变更强制通知下游 | project_dependency_events + 阻塞机制 |
| **删除保护** | 有依赖的分支不能删除 | check_branch_delete_protection() |

---

## 九、与软件包管理的对比

| 维度 | 软件包管理 | 本项目的跨项目依赖 |
|---|---|---|
| 依赖方向 | 单向（消费者调用库） | 双向（数据行互相引用） |
| 状态 | 无状态（代码） | 有状态（数据行存在性） |
| 变更影响 | 软降级（行为变化） | 硬错误（外键立刻失效） |
| 通知方式 | 建议升级提示 | 强制阻塞性通知 |
| 版本描述 | 版本号即可 | 版本号 + 合法行集合 |

**关键差异处理**：
- 引入 `validation_status` 记录数据行可达性状态
- Schema 变更采用**阻塞性告警**而非提示
- 合并采用**联合编排**而非独立操作