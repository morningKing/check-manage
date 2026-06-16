# 跨项目依赖功能说明

## 概述

跨项目依赖功能用于管理多个项目之间的协作关系。当一个项目（下游项目）依赖另一个项目（上游项目）的数据时，可以通过依赖声明来：
- 跟踪上游项目的变更
- 在合并前检查依赖状态
- 防止误删被依赖的分支
- 自动通知依赖断裂

## 依赖类型

系统支持三种依赖类型，适应不同的协作场景：

| 类型 | 名称 | 特点 | 合并阻塞 | 自动更新 |
|------|------|------|----------|----------|
| `track-main` | 跟随主干 | 自动跟随上游main分支 | ❌ 不阻塞 | ✅ 自动更新 |
| `read-write` | 配套分支 | 分支间配对协作 | ✅ 目标未合并则阻塞 | ❌ 手动管理 |
| `read-only` | 精确钉住 | 引用特定历史版本 | ❌ 不阻塞 | ❌ 固定版本 |

### 1. track-main（跟随主干）

**适用场景**：下游项目始终需要使用上游项目的最新稳定版本。

**行为特点**：
- 下游项目自动跟随上游项目的 main 分支
- 上游 main 分支合并后，下游依赖声明自动更新
- 下游项目合并时**不会阻塞**（允许随时合并）
- 上游 main 分支变更会触发下游项目的依赖校验和通知

**示例**：
```
项目B[main] → track-main → 项目A[main]
```
- 项目B 始终使用项目A 的最新稳定数据
- 项目A 合并新功能后，项目B 收到通知并自动更新依赖引用

### 2. read-write（配套分支）

**适用场景**：多个项目并行开发，需要协调发布顺序。

**行为特点**：
- 下游分支与上游分支配对，形成开发依赖链
- 下游项目合并时**会被阻塞**，必须等待上游分支先合并
- 系统计算联合合并顺序，确保依赖链正确执行
- 上游分支合并后，下游依赖状态变为"就绪"，可以合并

**示例**：
```
项目B[feature-y] → read-write → 项目A[feature-x]
项目C[feature-z] → read-write → 项目A[v2.0]
```

**阻塞与就绪状态**：
| 上游分支状态 | 下游分支状态 | 下游可合并 |
|--------------|--------------|------------|
| `active`（未合并） | `active` | ❌ 阻塞 |
| `merged`（已合并） | `active` | ✅ 就绪 |
| 不存在 | `active` | ❌ 阻塞（断裂） |

**联合合并流程**：
1. 系统检测到项目B[feature-y] 依赖 项目A[feature-x]
2. 项目A[feature-x] 未合并 → 项目B 被阻塞
3. 先合并项目A[feature-x]
4. 项目A 合并成功 → 项目B 依赖变为"就绪"
5. 项目B[feature-y] 可以合并

### 3. read-only（精确钉住）

**适用场景**：下游项目需要引用上游项目的特定历史版本，不关心后续变更。

**行为特点**：
- 下游项目钉住上游项目的特定版本（如 v1.0）
- 上游项目的任何变更**不影响**此依赖
- 下游项目合并时**不会阻塞**
- 被钉住的版本不能删除（删除保护）

**示例**：
```
项目C[main] → read-only → 项目A[v1.0]
```
- 项目C 固定使用项目A 的 v1.0 版本数据
- 即使项目A 发布了 v2.0、v3.0，项目C 仍使用 v1.0
- 尝试删除项目A 的 v1.0 分支会被阻止

---

## 测试数据说明

运行测试数据脚本后，会创建以下测试场景：

```bash
cd server && python create_test_dependency_data.py
```

### 菜单结构

```
测试工作空间 (menu-test-workspace)
├── 项目A（上游） (test-project-A)
│   ├── 测试客户
│   └── 测试产品
├── 项目B（下游） (test-project-B)
│   └── 测试订单
└── 项目C（依赖方） (test-project-C)
│   └── 测试任务
```

### 版本数据

| 项目 | 版本ID | 名称 | 状态 | 说明 |
|------|--------|------|------|------|
| 项目A | ver-a-v1.0 | v1.0 | merged | 初始版本（已合并） |
| 项目A | ver-a-v2.0 | v2.0 | merged | 功能更新（已合并） |
| 项目A | ver-a-feat-x | feature-x | active | 特性X开发（未合并） |
| 项目B | ver-b-v1.0 | v1.0 | merged | 初始版本（已合并） |
| 项目B | ver-b-feat-y | feature-y | active | 特性Y开发（未合并） |
| 项目C | ver-c-v1.0 | v1.0 | merged | 初始版本（已合并） |
| 项目C | ver-c-feat-z | feature-z | active | 特性Z开发（未合并） |

### 依赖声明

| ID | 源项目[分支] | 目标项目[分支] | 类型 | 状态 |
|----|--------------|----------------|------|------|
| dep-1-track | 项目B[main] | 项目A[main] | track-main | 就绪 |
| dep-2-rw-active | 项目B[feature-y] | 项目A[feature-x] | read-write | **阻塞** |
| dep-3-rw-ready | 项目C[feature-z] | 项目A[v2.0] | read-write | 就绪 |
| dep-4-ro | 项目C[main] | 项目A[v1.0] | read-only | 就绪 |

---

## 功能详解

### 1. 依赖声明管理

**入口**：系统管理 → 依赖管理

**操作**：
- 选择项目查看其依赖列表
- 新增依赖：选择目标项目、目标分支、依赖类型
- 编辑依赖：修改目标分支、依赖类型、钉住版本
- 删除依赖：解除依赖关系
- 校验依赖：手动触发依赖状态校验

### 2. 合并依赖检查

当项目分支准备合并时，系统自动检查依赖状态：

**API**：`GET /projects/{projectMenuId}/merge-check?sourceBranch={branchId}`

**返回结果**：
```json
{
  "canMerge": true/false,
  "blockingDependencies": [...],  // 阻塞的依赖列表
  "readyDependencies": [...],     // 就绪的依赖列表
  "trackMainDependencies": [...], // track-main 类型依赖
  "readOnlyDependencies": [...]   // read-only 类型依赖
}
```

**阻塞示例**（dep-2-rw-active）：
```json
{
  "canMerge": false,
  "blockingDependencies": [
    {
      "id": "dep-2-rw-active",
      "targetProject": "test-project-A",
      "targetBranch": "ver-a-feat-x",
      "targetProjectName": "项目A（上游）",
      "reason": "目标分支 ver-a-feat-x 状态为 active，尚未合并"
    }
  ]
}
```

### 3. 联合合并顺序

获取依赖链的正确合并顺序：

**API**：`GET /projects/{projectMenuId}/merge-order?sourceBranch={branchId}`

**返回示例**：
```json
{
  "mergeOrder": [
    { "order": 1, "projectMenuId": "test-project-A", "projectName": "项目A", "sourceBranch": "ver-a-feat-x" },
    { "order": 2, "projectMenuId": "test-project-B", "projectName": "项目B", "sourceBranch": "ver-b-feat-y" }
  ]
}
```

**执行流程**：按顺序依次合并，确保依赖链正确。

### 4. 分支删除保护

防止删除被其他项目依赖的分支：

**API**：`GET /projects/{projectMenuId}/branches/{branchId}/delete-check`

**返回结果**：
```json
{
  "canDelete": false,
  "dependentProjects": [
    { "projectId": "test-project-B", "projectName": "项目B", "branchId": "main" }
  ]
}
```

**尝试删除项目A 的 feature-x 分支**：
- 系统检测到项目B 依赖此分支
- 删除被阻止，提示："无法删除：以下项目依赖此分支: 项目B"

### 5. 依赖校验与通知

系统自动校验依赖状态，并在状态变更时发送通知：

**校验触发时机**：
1. 创建/更新依赖声明时
2. 上游项目合并后
3. 定期调度（每小时）
4. 手动触发

**通知类型**：

| 类型 | 触发条件 | 通知对象 |
|------|----------|----------|
| `dependencyBroken` | 目标分支不存在/循环依赖 | 源项目管理员 |
| `dependencyWarning` | 外键断裂/数据可达性问题 | 源项目管理员 |
| `dependencyResolved` | 依赖从断裂变为正常 | 源项目管理员 |

**通知示例**：
```
【依赖断裂】「项目B」依赖的「项目A」分支 feature-x 已不存在，请检查依赖配置。
```

---

## 使用指南

### 场景1：新建依赖声明

1. 进入 依赖管理 页面
2. 选择源项目（如 项目B）
3. 点击"新增依赖"
4. 选择：
   - 目标项目：项目A
   - 目标分支：main 或具体版本
   - 依赖类型：根据协作模式选择
5. 保存后系统自动校验

### 场景2：合并前的依赖检查

1. 项目B 准备合并 feature-y 分支
2. 进入版本管理，点击"合并"
3. 系统自动检查依赖：
   - 发现 read-write 依赖阻塞（项目A[feature-x] 未合并）
4. 提示阻塞原因，建议合并顺序
5. 先合并项目A[feature-x]
6. 再合并项目B[feature-y]

### 场景3：查看依赖通知

1. 点击顶部通知铃铛
2. 查看依赖相关通知：
   - 🔴 断裂：需要立即处理
   - 🟡 警告：建议检查
   - 🟢 已恢复：问题已解决

---

## API 接口汇总

| 接口 | 方法 | 说明 |
|------|------|------|
| `/projects/{id}/dependencies` | GET | 获取项目的依赖列表 |
| `/projects/{id}/dependencies` | POST | 创建依赖声明 |
| `/projects/{id}/dependencies/{depId}` | PUT | 更新依赖声明 |
| `/projects/{id}/dependencies/{depId}` | DELETE | 删除依赖声明 |
| `/dependencies/{depId}/validate` | POST | 触发依赖校验 |
| `/projects/{id}/dependents` | GET | 查看依赖此项目的项目 |
| `/projects/{id}/merge-check` | GET | 合并前依赖检查 |
| `/projects/{id}/merge-order` | GET | 获取联合合并顺序 |
| `/projects/{id}/branches/{branchId}/delete-check` | GET | 分支删除保护检查 |

---

## 数据模型

### project_dependencies 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 依赖声明ID |
| source_project | string | 源项目ID |
| source_branch | string | 源分支ID |
| target_project | string | 目标项目ID |
| target_branch | string | 目标分支ID |
| relation_type | enum | 依赖类型 |
| pinned_version | string | 钉住版本ID（read-only类型） |
| is_validated | boolean | 校验状态 |
| validation_error | string | 校验错误信息 |
| declared_by | string | 声明者 |
| declared_at | datetime | 声明时间 |

### notifications 表（依赖相关）

| 字段 | 类型 | 说明 |
|------|------|------|
| type | string | dependencyBroken/warning/resolved |
| title | string | 通知标题 |
| content | string | 通知内容 |
| project_id | string | 相关项目 |
| user_id | string | 接收用户 |

---

## 最佳实践

1. **规划依赖链**：在创建分支前，先规划好跨项目的依赖关系
2. **使用 track-main**：对于稳定依赖，推荐使用 track-main 减少管理成本
3. **及时合并**：read-write 依赖的上游分支应尽快合并，避免阻塞下游
4. **监控通知**：关注依赖断裂通知，及时修复
5. **版本钉住**：需要固定版本时使用 read-only，避免意外变更

---

## 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 合并被阻塞 | read-write 依赖的目标分支未合并 | 先合并上游分支 |
| 依赖断裂通知 | 目标分支被删除 | 恢复分支或修改依赖声明 |
| 分支无法删除 | 被其他项目依赖 | 先解除依赖或修改目标分支 |
| 外键断裂警告 | 数据关联丢失 | 检查 relation 字段数据完整性 |