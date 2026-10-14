# 生产验证套件：task 委派子代理的 write 被 checkpoint(before) 命中

> 目的：验证「根会话用 task 工具委派子代理 → 子代理在自己的会话里用 write 写文件
> → 过程检查(before) 规则按文件名命中」。产出可核对的三重证据：命中 1/1、
> 被拦气泡显示注入提示词、门禁终态 ✓。
>
> 设计原则照抄已跑通的 E2E（`e2e/ai-full/agent-action-gate.spec.ts` 场景 1）：
> 裸文件名正则、强制 write 工具、max_checks=1、assert_spec 让终态有结论
> （无 assert 的 checkpoint 行结果列恒为「待核对」，属设计内，别误判成故障）。

## 0. 前置条件（生产最容易翻车的两条，先查再跑）

1. **OC serve 已重启加载当前插件**。后端升级只重写插件文件，OC serve 不重启
   就还是老钩子——checkpoint 命中会全是 0。重启方式：`POST /ai/opencode/restart`
   或直接重启 OC serve 进程。
2. **批通过 AI 聊天页/管理界面创建并引用策略**。OpenAPI/扫描建批路由不透传
   gatePolicyId（已知缺口），走那条路策略是静默丢的。

## 1. 门禁策略

管理页 → AI 能力组「门禁策略」→ 新建：

| 字段 | 值 |
| --- | --- |
| 策略名称 | `PROBE-task子代理write前置检查` |
| 描述 | task 委派子代理 write 命中验证（生产探针，验证完可停用） |
| 启用 | 是 |

规则（一行，checkpoint）：

| 字段 | 值 | 说明 |
| --- | --- | --- |
| 名称 | `报告写入前确认` | |
| 类型 | 拦截/终态核对选 **过程检查**（checkpoint） | |
| 触发工具 | `write` | 必须与模型实际用的工具名一字不差 |
| 匹配模式 | `gate-probe-report\.md` | 裸文件名正则，**无 ^$ 锚点、无引号** |
| 时机 | **执行前**（before） | |
| 检查次数上限 | `1` | 首次写入被拦，重写放行 |
| 检查提示词 | 见下 | 被拦时模型看到的原文 |
| 终态兜底断言 | 勾选：工具 `write`，模式 `gate-probe-report\.md`，至少 `1` 次 | 有它门禁终态才会变 ✓ |
| subagents 名单 | 留空 | 空 = 整棵树全量（含 task 委派的子代理） |

检查提示词（原文粘贴）：

```text
过程检查：写入 gate-probe-report.md 前，必须先用 read 工具完整读取 uploads/in.txt，
逐行核对你将写入的三行要点与该文件原文一致；确认无误后重新执行本次写入。
不要改用 bash/echo/edit 等其他工具，也不要更改目标文件名。
```

## 2. 批任务

AI 聊天页 → 批任务 → 新建批，**引用上面的策略**，上传输入文件 `in.txt`，粘贴提示词。

输入文件 `in.txt`（三行，内容固定便于核对）：

```text
验证要点一：task 工具委派的子代理内，write 工具调用会触发门禁过程检查。
验证要点二：checkpoint(before) 命中后注入提示词并消耗预算。
验证要点三：终态断言以服务端动作账本为唯一证据来源。
```

批任务提示词（原文粘贴）：

```text
用 task 工具委派一个子代理完成下面的任务，不要在根会话里自己写文件。委派提示词必须原样传达这三条要求：
(1) 先用 read 工具读取 uploads/in.txt，取出其中三行要点；
(2) 只用 write 工具（禁止用 bash/echo/edit/patch 写文件）在 outputs/ 目录下创建
    gate-probe-report.md，内容为这三行要点，一行一条；
(3) 写完后回复「已写入 outputs/gate-probe-report.md」。
```

> 提示词里「只用 write 工具」是命中前提：模型用 bash 或 edit 写文件时
> 工具名对不上，规则天然不命中。E2E 同款措辞。

## 3. 执行与核对

跑批（约 1–2 分钟），完成后按顺序核对：

| # | 核对点 | 预期 | 在哪看 |
| --- | --- | --- | --- |
| 1 | 门禁徽标 | ✓（passed） | 批详情子任务列表「门禁」列 |
| 2 | 命中 | `1 / 1` | 门禁弹窗「命中」列 |
| 3 | 结果 | 已通过（证据 1） | 门禁弹窗「结果」列 |
| 4 | 被拦实锤 | 子代理第一次 write 的气泡显示上面的注入提示词原文 | 子任务会话消息 |
| 5 | 产物 | `outputs/gate-probe-report.md` 存在、内容 = 三行要点 | 工作区 |

**判定**：2、3、4 同时成立 ⇒ 场景验证通过（task 委派子代理的 write 被钩到且注入）。

## 4. 不命中时按概率对照排查

1. **命中 0/1 且无被拦气泡** → 九成是 OC serve 老插件：重启 OC serve 重跑。
2. **命中 0/1 但任务完成** → 看子任务「工具调用」轨迹里写文件那条的 `tool`
   是不是 `write`（bash/edit 都不算）；args 原文里文件名是否含
   `gate-probe-report.md`（含路径转义/斜杠方向问题就对照正则）。
3. **命中 >0 但结果「待核对」** → 本套件配了 assert 不应出现；出现说明断言
   没配上（assert 的工具/模式与实际写入不一致）。
4. **弹窗提示「该子任务没有登记门禁期望」** → 批没引用上策略（走了不透传
   gatePolicyId 的建批路径），换 AI 聊天页重建。

## 5. API 附录（可选，管理界面操作的等价 curl）

```bash
# 建策略（admin 权限 token）
curl -X POST https://<生产地址>/ai/chat/gate-policies \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{
    "name": "PROBE-task子代理write前置检查",
    "description": "task 委派子代理 write 命中验证",
    "enabled": true,
    "checks": [{
      "name": "报告写入前确认", "check_type": "tool", "mode": "checkpoint",
      "tool": "write", "args_pattern": "gate-probe-report\\.md",
      "timing": "before", "max_checks": 1, "scope": "tree",
      "prompt": "过程检查：写入 gate-probe-report.md 前，必须先用 read 工具完整读取 uploads/in.txt，逐行核对你将写入的三行要点与该文件原文一致；确认无误后重新执行本次写入。不要改用 bash/echo/edit 等其他工具，也不要更改目标文件名。",
      "assert_spec": {"tool": "write", "args_pattern": "gate-probe-report\\.md", "min_count": 1}
    }]
  }'

# 建批（引用策略；files 走 staging 上传接口 /ai/chat/batches/staging/upload）
curl -X POST https://<生产地址>/ai/chat/batches \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"name":"gate-probe-task-subagent","prompt":"<上面的批提示词>","files":["<staged>"],"gatePolicyId":"<返回的 gpol_…>"}'

# 查子任务门禁（管理面只读端点）
curl https://<生产地址>/ai/chat/admin/batches/<bid>/sessions/<sid>/gate \
  -H "Authorization: Bearer <token>"
```

自证正则小抄：write 调用的匹配对象（argsText）形如
`filePath=<工作区绝对路径>\outputs\gate-probe-report.md` + 换行 + `content=<全文>`，
`SELECT '<argsText原文>' ~ 'gate-probe-report\.md'` 应为 true。
