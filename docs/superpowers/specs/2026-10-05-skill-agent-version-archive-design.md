# skill/agent 定义版本归档与对比设计（2026-10-05）

SkillOpt 定义版本（`ai_skill_def_versions`）目前只存内容指纹（`content_hash`）、
人工标注（`version_label`/`note`）与首见时间。执行侧 `ai_execution_manifests`
冻结的也只是 path+hash 指针。正文不落档的后果：

- 版本间无法做内容 diff（只有按 hash 聚合的效果差）；
- 无法回滚到任一历史版本；
- 定义文件一旦改版，旧正文不可恢复（历史版本成匿名指纹）。

本设计给每个版本归档主定义文件正文，补齐内容 diff 与回滚能力，并给系统内
写路径加上轻量的版本发布语义。

## 1. 目标 / 非目标

**目标**

- 版本实体归档主定义文件正文（SKILL.md / agent md），随版本行同库存储；
- 版本间 unified diff 的 API 与 UI；
- 回滚到任一已归档版本（前滚式，见 §4.4）；
- steps 回写自动登记版本并填默认 `version_label`。

**非目标**

- skill 附属脚本文件（SKILL.md 之外的目录内容）的归档/diff/回滚（二期）；
- 语义化版本号、发布审批流、多分支版本树——线性 hash 链即可；
- 历史存量版本正文的追溯回填（正文已不存在，不可能）；
- `guidance`（AGENTS.md）等其他 manifest kind 不纳入版本归档。

## 2. 决策记录

| 决策点 | 取值 | 理由 |
|---|---|---|
| 归档内容 | 仅主定义文件正文，DB 加 TEXT 列（方案 A） | 与单文件 hash 模型对齐；diff/回滚主诉求即可满足；独立文件快照目录（方案 B）引入 DB/文件两套事实源与备份分裂 |
| 归档触发 | 双轨：steps 回写主动归档 + manifest 扫描被动兜底 | 主动覆盖系统内唯一正文写路径；被动覆盖手工改文件；两次执行之间的手工改动接受漏档 |
| 回滚语义 | 前滚式：写回归档正文，hash 复原即回到该版本 | 不引入"当前版本指针"新概念，保持 hash 链模型；见 §4.4 不变性 |
| 版本发布语义 | 轻量：steps 回写自动填默认 label | 不搞语义化版本号；被动兜底归档的 label 留空，沿用现有 PATCH 人工补标 |

## 3. 数据模型

迁移文件 `server/migrations/2026_10_05_def_version_content_archive.py`
（幂等，经 `db_schema/global_skills.py::_run_dated_migrations` 自动执行）：

```sql
ALTER TABLE ai_skill_def_versions
  ADD COLUMN IF NOT EXISTS content TEXT,
  ADD COLUMN IF NOT EXISTS content_captured_at TIMESTAMPTZ;
```

- `content IS NULL` = **未归档**（历史存量行、或归档时读文件失败只登记了
  hash 的行）。UI 标「未归档」，内容类动作置灰。不做伪回填。
- `content_hash = ''` 的异常行（manifest 无 hash 按空串注册）不参与
  diff/回滚（hash 无法自证，写回无法复原版本身份）。
- `content_captured_at` 为正文定格时间，与 `first_seen_at`（版本首见）
  语义分离。

## 4. 归档写入

### 4.1 统一注册函数

`utils/skill_fit.py` 现私有 `_register_def_version` 重构为公开的
`register_def_version(cur, def_kind, def_name, content_hash, content=None)`：

```sql
INSERT INTO ai_skill_def_versions
  (id, def_kind, def_name, content_hash, content, content_captured_at)
VALUES (%s, %s, %s, %s, %s, CASE WHEN %s IS NULL THEN NULL ELSE NOW() END)
ON CONFLICT (def_kind, def_name, content_hash) DO UPDATE SET
  content            = COALESCE(ai_skill_def_versions.content, EXCLUDED.content),
  content_captured_at = COALESCE(ai_skill_def_versions.content_captured_at,
                                EXCLUDED.content_captured_at)
```

- **后到补齐**：先被拟合路径注册（无正文）的版本，之后被扫描路径遇到时
  补上正文；已有正文永不覆盖。
- 幂等性保持：重复注册同 hash 不翻倍、不刷新 `first_seen_at`。
- 拟合路径原调用点（`compute_attempt_fit`）签名适配，行为不变。

### 4.2 主动触发：steps 回写（系统内唯一正文写路径）

`ai_session_admin.py::skill_def_steps_apply` 在 `apply_steps` 写盘成功后：

1. 读回 `out_path` 文件 bytes 一次，`sha256(bytes)` 即 hash——与刚落盘的
   内容自洽；
2. `def_kind`/`def_name` 从 path 推导：basename 为 `SKILL.md` → kind=skill、
   def_name=父目录名；其余（含各级 agent md）→ kind=agent、def_name=文件名
   去 `.md`；
3. 调 `register_def_version(..., content=正文)`；
4. 默认 label「AI步骤优化 <YYYY-MM-DD>」，请求 body 可传 `versionLabel`
   显式覆盖。

> 核实过的事实（2026-10-05）：`install_skill_from_zip` 对同名技能直接拒绝
> （"已存在"），zip **没有更新路径**；`PUT /ai/skills/<id>` 只改
> description/enabled；`ensure_builtin_skills` 只插缺失不覆盖。除 steps
> 回写外，系统内不存在其他定义正文写端点。

### 4.3 被动兜底：manifest 扫描

`utils/execution_audit.py::collect_and_save_workspace_manifests` 落
manifest 行之后追加注册步骤（同一连接事务内）：

1. 取本次 manifest 中 kind∈(skill, agent) 且 path/content_hash 均非空的行；
2. 一条 `SELECT ... WHERE (def_kind, def_name, content_hash) IN (...)` 查
   `ai_skill_def_versions` 已见版本（check-first，已知 hash 不重读文件、
   不重复注册）；
3. 对未登记的 hash：读文件 bytes 一次，`sha256(bytes)` 与 manifest hash
   比对——**一致才带 content 注册**；不一致（两次读之间文件被改）只注册
   hash 不归档，`log.warning` 留痕。

覆盖范围：手工直接改文件的版本由"下次执行时的扫描"归档。已知限制：两次
执行之间被连续改过又未参与执行的手工改动，中间版本漏档（每次执行只定格
当次扫描所见）。

### 4.4 回滚的不变性

写回的正文就是归档时校验过 hash 的那份 bytes，因此写回后文件 sha256 ==
该版本 `content_hash`，下次扫描注册时 `UNIQUE (def_kind, def_name,
content_hash)` 命中**同一版本行**——时间线不产生虚假的"回滚版本"，该版本
自然再次生效。回滚动作本身经 `log_operation` 留痕（操作日志可查），不新增
版本行。

删除技能后重装同名技能：同 hash 版本行被 UNIQUE 复用，归因连续，
`first_seen_at` 不变（注明行为，不做特殊处理）。

## 5. API（挂 `ai_execution_admin_bp`，权限 `admin.ai_chat_admin`）

| 端点 | 行为 |
|---|---|
| `GET /skill-def-versions/<id>/content` | 版本正文。返回 `{id, defKind, defName, contentHash, versionLabel, contentCapturedAt, content}`；版本不存在 404；存在但未归档 400 |
| `GET /skill-def-versions/compare?defKind=&defName=&fromId=&toId=` | 两版本 unified diff。from/to 须属同一 `(defKind, defName)`（否则 400）；任一端未归档 400；版本不存在 404。服务端 `difflib.unified_diff` 生成，`fromfile`/`tofile` 用 `name@hash前12位`，返回 `{from, to, diff}`（diff 为文本） |
| `POST /skill-def-versions/<id>/rollback` | 回滚。① 版本存在且已归档、hash 非空，否则 404/400；② 定位文件：`ai_execution_manifests` 中 `kind=def_kind AND name=def_name AND path` 非空的最新一行，无则 404「无法定位定义文件」；③ `_path_in_allowed_roots` 约束校验，逃逸 400；④ 以 utf-8 写回归档正文；⑤ `log_operation('update','ai_skill_def_versions',id,None,'SkillOpt 定义版本回滚')`；⑥ 返回 `{ok, path, contentHash}`。写文件 OSError 按 500 返回错误信息 |

前端不引 diff 渲染依赖：统一 diff 文本由前端按行解析，`+`/`-`/`@@` 行着色，
等宽呈现。

## 6. UI（`DefVersionTimeline.vue` 扩展）

每个版本节点行内新增动作：

- **查看内容**：只读弹窗（等宽 pre），调 content 端点；
- **与上一版对比**：diff 弹窗，调 compare 端点（首个版本无上一版，置灰）；
- **回滚到此版**：确认对话框（写明「将把该版本正文写回当前定义文件」），
  成功后刷新时间线。

未归档版本（content 为 NULL）三个动作置灰，tooltip「历史版本未归档正文」。
现有版本标注行内编辑、相邻版本 Δ 徽标保持不变。

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| 版本不存在 | 404 |
| 版本未归档 / content_hash 为空 | 400「版本未归档」 |
| compare 两端非同一定义 | 400 |
| rollback 定位不到定义文件 path | 404「无法定位定义文件」 |
| rollback path 逃出允许根 | 400「path escapes allowed roots」 |
| rollback 写文件 OSError | 500 带错误信息 |
| 扫描兜底读文件失败/两次读 hash 不一致 | 只注册 hash 不归档，log.warning，不阻断执行 |

## 8. 测试策略

- **单测**：`register_def_version` upsert 幂等、content 后到补齐、已有
  content 不被覆盖；扫描兜底（新 hash 归档、已知 hash 不重读、hash 不一致
  降级为只注册）；steps 回写后版本注册与默认 label（body 覆盖生效）。
- **路由测试（真实开发 PG，沿仓库惯例）**：三端点权限/404/400 路径；
  compare 的 diff 文本形状与同定义校验；rollback 写回后文件 sha256 ==
  `content_hash`、版本行数不膨胀、`log_operation` 落账。
- **E2E（`e2e/ai-full` 真实链路）**：带 fit 声明的技能跑批 → steps 回写
  → 版本时间线出现带默认 label 的新节点 → 与上一版对比出 diff → 回滚 →
  文件内容复原且时间线无新增版本行。

## 9. 已知限制

- 历史存量版本正文不可恢复，永久标「未归档」。
- 两次执行之间的连续手工改动会漏中间版本。
- skill 附属脚本文件不在归档/diff/回滚范围（二期候选：正文 + 附属文件
  hash 清单）。
- 跨库恢复（`server/backups` 体系）不在本设计范围。
