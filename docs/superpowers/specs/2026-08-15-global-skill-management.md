# 全局 Skill 管理 — 设计方案

**日期**：2026-08-15
**状态**：Draft

---

## 1. 背景与目标

当前 skill 管理是**会话级别**的：

| 问题 | 影响 |
|------|------|
| 上传到 A 会话的 skill 对 B 会话不可见 | 用户每次开新会话都要重新上传 |
| 批任务只能通过 `provision_repo`（git 仓库）注入 skill | 外部 API 调用方需要维护一个 git 仓库 |
| 没有管理员视角的 skill 管理 | 无法统一管控哪些 skill 可用 |
| 没有 skill 版本/描述元数据 | 用户只能看到 skill 名，不知道用途 |

**目标**：

| 能力 | 说明 |
|------|------|
| 全局 skill 存储 | 上传一次，所有会话自动可用 |
| 管理员 CRUD | 上传、编辑描述、删除、启用/禁用 |
| 会话自动注入 | 新会话创建时，全局 skill 自动出现在命令面板 |
| 批任务可用 | 创建批任务时可选择全局 skill（替代 provision_repo 的简单场景） |
| 外部 API 可发现 | `GET /v1/ai-batches/skills` 列出可用 skill 供外部系统选择 |

---

## 2. 核心设计

### 2.1 存储方案

**中心存储目录**：`<AI_CHAT_WORKSPACE_ROOT>/global-skills/`

```
global-skills/
├── inspection/
│   ├── SKILL.md
│   ├── knowledge-base/
│   └── ...
├── report-analysis/
│   ├── SKILL.md
│   └── ...
└── code-review/
    ├── SKILL.md
    └── ...
```

**数据库表**：`global_skills`（只存元数据，文件在磁盘）

```sql
CREATE TABLE IF NOT EXISTS global_skills (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    uploaded_by VARCHAR(100) REFERENCES users(id),
    file_size   INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
```

> `name` 唯一，同时作为磁盘目录名和 OpenCode skill 名。`enabled` 允许禁用而不删除。

### 2.2 会话注入机制

**方案：符号链接（symlink）**

新会话创建时，在 `<workspace>/.opencode/skills/` 下为每个**启用的**全局 skill 创建符号链接指向 `global-skills/<name>/`。

```
<workspace>/.opencode/skills/
├── my-local-skill/          ← 用户手动上传的（实体目录）
├── inspection -> ../../global-skills/inspection  ← 全局 skill（符号链接）
└── report-analysis -> ../../global-skills/report-analysis
```

**优势**：
- 零拷贝，不占用额外磁盘
- 全局 skill 更新后所有会话立即生效（符号链接指向同一个物理目录）
- OpenCode 通过 `list_skills(directory)` 自动发现，无需改 OpenCode 代码

**降级策略**：如果 OS 不支持 symlink（Windows 需要权限），fallback 到复制目录。

### 2.3 Windows 兼容性

Windows 上创建 symlink 需要管理员权限（或开发者模式）。降级方案：

1. 优先尝试 `os.symlink()`
2. 如果 `OSError`，fallback 到 `shutil.copytree()`（复制到会话工作区）
3. 复制的 skill 是快照，全局更新不会自动同步——在 UI 上标注「已复制，需重新打开会话获取最新版本」

---

## 3. 端点设计

### 3.1 管理端点（JWT，`admin.ai_settings`）

| # | 方法 | 路径 | 用途 |
|---|------|------|------|
| G1 | GET | `/ai/skills` | 列出所有全局 skill |
| G2 | GET | `/ai/skills/<id>` | 获取单个 skill 详情 |
| G3 | POST | `/ai/skills` | 上传新 skill（zip） |
| G4 | PUT | `/ai/skills/<id>` | 更新描述/启用状态 |
| G5 | DELETE | `/ai/skills/<id>` | 删除 skill（磁盘 + DB） |
| G6 | GET | `/ai/skills/<id>/files` | 列出 skill 内的文件 |
| G7 | GET | `/ai/skills/<id>/files/<path>` | 读取 skill 内的文件内容 |

### 3.2 对外端点（API Key）

| # | 方法 | 路径 | 用途 |
|---|------|------|------|
| H1 | GET | `/v1/ai-batches/skills` | 列出可用的全局 skill（供创建批任务时选择） |

### 3.3 G1 列出全局 skill

**响应**

```json
{
  "skills": [
    {
      "id": "gs-abc123",
      "name": "inspection",
      "description": "巡检任务专用 skill，包含巡检流程和报告模板",
      "enabled": true,
      "uploadedBy": "admin",
      "fileSize": 15360,
      "createdAt": "2026-08-10T03:20:00.000Z",
      "updatedAt": "2026-08-10T03:20:00.000Z"
    }
  ]
}
```

### 3.4 G3 上传 skill

**请求**：`POST /ai/skills`，`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | 是 | zip 文件（≤ 5 MB） |
| `description` | string | 否 | skill 描述 |

**处理流程**：
1. 校验 zip（复用 `skill_upload.py::extract_skill_zip` 的逻辑）
2. 解压到临时目录，校验 `SKILL.md` 存在且有 `name` frontmatter
3. 检查 `name` 唯一性（DB + 磁盘目录）
4. 移动到 `global-skills/<name>/`
5. INSERT 到 `global_skills` 表
6. 返回创建的 skill 元数据

### 3.5 H1 对外列出可用 skill

**响应**

```json
{
  "skills": [
    { "name": "inspection", "description": "巡检任务专用 skill" },
    { "name": "report-analysis", "description": "报告分析 skill" }
  ]
}
```

只返回 `enabled = true` 的 skill。外部系统可将 `name` 作为创建批任务时的参数（需要配合 batch 端点支持 skill 选择）。

---

## 4. 批任务集成

### 4.1 批任务创建时选择全局 skill

创建批任务时新增可选参数 `skills`（字符串数组）：

```json
{
  "name": "季度报告分析",
  "prompt": "请分析每份报告...",
  "files": [...],
  "skills": ["inspection", "report-analysis"]
}
```

**处理**：`batch_engine._prepare_workspace` 后、`create_session` 前，将指定的全局 skill 符号链接到子会话的 `.opencode/skills/`。

### 4.2 与 `provision_repo` 的关系

| 机制 | 适用场景 | 优先级 |
|------|---------|--------|
| `skills` 参数 | 使用已有的全局 skill | 简单，推荐 |
| `provision_repo` | 需要从外部 git 仓库注入 agent/skill/config | 复杂，高级 |

两者可以同时使用。`provision_repo` clone 到 `.opencode/` 后，`skills` 的符号链接追加到 `.opencode/skills/`，不会冲突。

---

## 5. 前端

### 5.1 管理页面 `/admin/ai-skills`

注册到 `settingsCatalog.ts` 的 AI 配置分组：

```typescript
{ id: 'ai-skills', label: 'AI 技能管理', perm: 'admin.ai_settings',
  component: () => import('@/views/admin/AiSkillManager.vue') }
```

**页面结构**：

```
┌─────────────────────────────────────────────────────────────┐
│ AI 技能管理                                      [上传技能]  │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────┬──────────┬──────┬────────┬────────┬──────────┐ │
│ │ 名称    │ 描述     │ 状态 │ 大小   │ 上传者 │ 操作     │ │
│ ├─────────┼──────────┼──────┼────────┼────────┼──────────┤ │
│ │ inspect │ 巡检任务 │ 启用 │ 15 KB  │ admin  │ 编辑 删除│ │
│ │ report  │ 报告分析 │ 启用 │ 8 KB   │ admin  │ 编辑 删除│ │
│ └─────────┴──────────┴──────┴────────┴────────┴──────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**上传 dialog**：
- 文件选择（.zip）
- 描述输入
- 上传按钮

**编辑 dialog**：
- 描述修改
- 启用/禁用开关

### 5.2 批任务创建集成

在 `CreateBatchDialog.vue` 中新增「全局技能」多选下拉框：
- 调用 `GET /ai/skills` 获取可用 skill 列表
- 选中的 skill 名称传入 `skills` 参数

### 5.3 文件清单

| 文件 | 说明 |
|------|------|
| `src/views/admin/AiSkillManager.vue` | 管理页面 |
| `src/api/aiSkills.ts` | API 函数 |
| `src/stores/aiSkills.ts` | Pinia store |

---

## 6. 关键设计决策

### D1：为什么用符号链接而不是数据库存储文件

- skill 是**目录结构**（SKILL.md + 辅助文件），不适合拆成 DB 记录
- OpenCode 通过文件系统发现 skill，不走 DB
- 符号链接零拷贝，更新即时生效
- 磁盘存储简单可靠，备份/还原容易

### D2：为什么 `name` 唯一而不是 `id`

- OpenCode 用目录名作为 skill 名，磁盘目录名必须唯一
- 用户在命令面板看到的是 `name`，不是 DB `id`
- 外部 API 用 `name` 引用 skill

### D3：权限用 `admin.ai_settings` 而不是新建权限键

- skill 管理是 AI 配置的一部分，与 AI 配置页语义一致
- 不需要单独的权限粒度
- 减少权限管理复杂度

### D4：会话注入时机

在 `create_session` 之前（`_prepare_workspace` 之后）。此时工作区目录已创建，`.opencode/skills/` 目录已存在（OpenCode 约定），可以安全创建符号链接。

---

## 7. 实施步骤

| 步 | 内容 | 文件 |
|----|------|------|
| S1 | DB：`global_skills` 表迁移 | `server/init_db.py` |
| S2 | 工具层：skill 目录管理（创建/删除/符号链接） | `server/utils/global_skills.py` |
| S3 | 路由：管理端点 G1-G7 | `server/routes/ai_skills.py` |
| S4 | 路由：对外端点 H1 | `server/routes/open_api_batches.py` |
| S5 | app.py 注册蓝图 | `server/app.py` |
| S6 | batch_engine：会话创建时注入全局 skill | `server/utils/batch_engine.py` |
| S7 | 前端：API 函数 | `src/api/aiSkills.ts` |
| S8 | 前端：管理页面 | `src/views/admin/AiSkillManager.vue` |
| S9 | 前端：settingsCatalog 注册 | `src/views/admin/hub/settingsCatalog.ts` |
| S10 | 前端：批任务创建集成 | `src/components/ai-chat/CreateBatchDialog.vue` |
| S11 | 测试 | `server/tests/test_global_skills.py` |
| S12 | 文档 | `docs/user-guide/admin/ai-skills.md` |
