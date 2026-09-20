# OpenCode 运行时依赖与部署指南

> **迁移说明**：专题细节已吸收到 [`09-AI智能助手.md`](./09-AI智能助手.md)；本文保留专题背景/实施细节；实现状态、表结构、接口以 09 和当前代码为准。

> 适用范围：BKB（check-manage）服务端与 OpenCode Agent 运行时（`opencode serve`）的
> 集成部署。事实核验基线：OpenCode **v1.15.1**（Windows / Linux 均已验证）。
>
> 本文回答三个问题：**平台在 OpenCode 端放了什么依赖、怎么安装、怎么确认在工作。**

---

## 1. 架构总览：平台与 OpenCode 的关系

```
┌────────────────────────── BKB Flask 服务端（默认 :3002） ──────────────────────────┐
│  proxy.py ──拉起/健康检查──▶ opencode serve（子进程，默认 :4096）                    │
│  app.py   ──启动时自动安装──▶ <OPENCODE_GLOBAL_DIR>/plugin/baize-trace.js           │
│  管理后台「OpenCode 运行时」──读写──▶ <OPENCODE_GLOBAL_DIR>/skill/ · agent/          │
└──────────────┬──────────────────────────────────────────────────────────────────────┘
               │ HTTP（OPENCODE_BASE_URL，代理所有会话请求）
               ▼
┌────────────────────────── OpenCode serve 宿主进程 ─────────────────────────────────┐
│  启动时一次性加载全局配置：skill/<name>/SKILL.md · agent/<name>.md · plugin/*.js     │
│  plugin/baize-trace.js ──监听 skill 工具调用──POST──▶ Flask /ai/memory/internal/…   │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

核心事实（决定了下文所有安装/使用行为）：

- OpenCode **没有 skill/agent 的 CRUD API，也没有热加载**。文件写入对运行中的
  serve 不可见，**重启 serve 是配置生效的唯一方式**。
- 平台会记录自己拉起的 serve 进程（ownership），管理界面重启只会杀平台自己的进程；
  手工拉起的 serve 平台拒绝代管，需要人工停止。
- OpenCode 全局配置目录在 Windows 上也是 `~/.config/opencode`（**不是** `~/.opencode`）。

---

## 2. 依赖清单总表

| # | 依赖 | 必需性 | 安装方式 | 作用 |
|---|------|--------|----------|------|
| 1 | OpenCode 本体（`opencode` CLI ≥ v1.15） | **必需** | npm / 官方安装脚本 | Agent 运行时本体，`opencode serve` 提供会话服务 |
| 2 | 全局配置目录（`OPENCODE_GLOBAL_DIR`） | **必需**（有默认值，一般无需配置） | 自动创建 | serve 读取 skill / agent / plugin 的唯一来源 |
| 3 | `baize-trace.js` 运行时插件 | 可选（强烈建议） | **服务端启动时自动安装**，无需人工 | SkillOpt「确证」数据通道：上报 skill 实际调用 |
| 4 | 全局 skill / agent 文件 | 可选 | 管理后台「OpenCode 运行时」页在线编辑/上传 | 给所有会话提供全局技能与子智能体 |
| 5 | `MCP_INTERNAL_TOKEN`（服务端 `.env`） | 插件通道需要 | 手动写入 `server/.env` | 内部事件上报鉴权（插件→Flask） |

工作区级依赖（平台自动生成，无需安装）：每个会话工作区内的 `AGENTS.md`、
`opencode.json`、`.gitignore`、`.opencode/skills/`（按需注入的平台技能）由服务端
在会话创建时自动写入，OpenCode 作为"项目级配置"自动读取。

---

## 3. 安装 OpenCode 本体

### 3.1 安装

```bash
# npm 方式（推荐，便于固定版本）
npm install -g opencode-ai@latest

# 或官方安装脚本
curl -fsSL https://opencode.ai/install | bash
```

验证：

```bash
opencode --version    # ≥ 1.15
```

### 3.2 让平台找到可执行文件

平台通过 `server/.env` 的以下变量拉起 serve（见 `server/utils/opencode_launch.py`）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `OPENCODE_BIN` | `opencode`（PATH 查找） | **Windows 必须写完整 .exe 路径**，例如
`OPENCODE_BIN=C:\Users\<you>\AppData\Roaming\npm\windows-x64\bin\opencode.exe`。
裸命令名在 Windows 上无法经 argv 解析 npm 的 `.cmd` 包装器 |
| `OPENCODE_SERVE_CMD` | （空） | 整条命令覆盖（wrapper 脚本 / 服务管理器场景），优先级高于 `OPENCODE_BIN` |
| `OPENCODE_SERVE_CWD` | `~` | serve 工作目录；保持默认（中性目录，避免误读项目级 `.opencode/`） |
| `OPENCODE_AUTOSTART` | 开 | `0` = 关闭自动拉起，仅探测告警 |
| `OPENCODE_BASE_URL` | `http://127.0.0.1:4096` | 平台访问 serve 的地址 |

启动 BKB 后端时，`proxy.py` 探测 `OPENCODE_BASE_URL` 不可达即自动执行
`opencode serve` 拉起（日志：`proxy-opencode.log`）。

---

## 4. 全局配置目录（OPENCODE_GLOBAL_DIR）

```bash
# 默认（Windows 同样是这个路径）
~/.config/opencode/
├── skill/<name>/SKILL.md     # 全局技能（主目录，管理界面写入这里）
├── skills/<name>/SKILL.md    # 复数目录，机器历史安装也会被扫描（只读合并展示）
├── agent/<name>.md           # 全局子智能体（frontmatter + Markdown 正文即提示词）
└── plugin/baize-trace.js     # SkillOpt 运行时插件（服务端自动安装）
```

自定义位置（可选）：`server/.env` 加一行

```ini
OPENCODE_GLOBAL_DIR=D:/oc-global
```

平台会把该值**显式注入 serve 子进程环境**（两侧永不漂移），管理界面读写与 serve
加载保证是同一个目录。修改后重启 BKB 后端即可，目录无需手工创建。

---

## 5. SkillOpt 运行时插件（baize-trace.js）

### 5.1 它做什么

- 监听 OpenCode 总线事件 `message.part.updated`，识别 **skill 工具调用**
  （加载/完成/失败），连同 `session.idle` 一起 POST 到 Flask 内部端点
  `POST /ai/memory/internal/runtime-events`（`x-internal-token` 头鉴权）。
- 服务端据此把 SkillOpt 的调用记录从 `inferred`（启发式推断）升级为
  `confirmed`（运行时确证）——这是"技能真的被用过"的证据链，没有插件时
  SkillOpt 仍有数据（inferred），但永远到不了 confirmed。

### 5.2 安装（自动，默认零操作）

BKB 后端**每次启动**都会幂等写入插件文件（`app.py` 启动钩子 → `ensure_runtime_plugin`），
把上报端点与内部 token 直接嵌入文件。启动日志出现即成功：

```
INFO [root] SkillOpt: runtime plugin ensured at C:\Users\<you>\.config\opencode; ...
```

### 5.3 安装（手动，特殊场景）

仅当部署策略禁止服务端写该目录时才需要手工安装。步骤：

1. 打开 `server/utils/skillopt.py`，复制 `PLUGIN_JS` 常量内容；
2. 手工替换其中的 `__ENDPOINT__` 为
   `http://127.0.0.1:3002/ai/memory/internal/runtime-events`（端口跟随 `FLASK_PORT`）、
   `__TOKEN__` 为 `server/.env` 里 `MCP_INTERNAL_TOKEN` 的值；
3. 保存为 `<OPENCODE_GLOBAL_DIR>/plugin/baize-trace.js`，重启 `opencode serve`。

> 升级提示：插件文件由服务端按内容比对自动覆写（不一致即重写），手工改动会在
> BKB 重启时被覆盖。要长期自定义请用下面的环境变量方式。

### 5.4 前置配置（必需，否则上报被 403 拒收）

`server/.env` 中配置内部 token（也是记忆内部通道共用的鉴权）：

```ini
MCP_INTERNAL_TOKEN=<任意足够长的随机串>
```

生成示例：`python -c "import secrets;print(secrets.token_urlsafe(24))"`

配置后重启 BKB 后端（让插件文件重新嵌入新 token）并重启 serve（让插件重新加载）。

可选环境变量（优先级高于嵌入值，一般不用）：

| 变量 | 作用 |
|------|------|
| `BAIZE_INTERNAL_TOKEN` | serve 子进程环境里的上报 token（平台拉起 serve 时自动从 `MCP_INTERNAL_TOKEN` 透传） |
| `BAIZE_RUNTIME_EVENT_URL` | 覆盖上报端点 URL |

### 5.5 验证插件在工作

三步自检（全过 = confirmed 通道健康）：

```bash
# ① 文件存在且端点正确
head -6 ~/.config/opencode/plugin/baize-trace.js
# ENDPOINT 一行应为 http://127.0.0.1:<FLASK_PORT>/ai/memory/internal/runtime-events

# ② 手动模拟一次上报（换成真实 .env 里的 token；预期返回 {"ok": true}）
curl -X POST http://127.0.0.1:3002/ai/memory/internal/runtime-events \
  -H "Content-Type: application/json" \
  -H "x-internal-token: <MCP_INTERNAL_TOKEN>" \
  -d '{"kind":"skill","skillName":"self-check","sessionID":"<某个真实 sess_id>",
       "messageID":"m","partID":"p","status":"completed","title":"t"}'

# ③ 数据库出现 runtime 行（SQL）
SELECT skill_name, source, evidence_level, outcome
FROM ai_skill_invocations
WHERE source = 'runtime' ORDER BY completed_at DESC LIMIT 5;
```

补充说明：插件上报的 `sessionID` 是 OpenCode 内部 id（`ses_…`），服务端会自动
映射回平台会话 id（`sess_…`）再落库——手动模拟时直接给平台 `sess_` id 也可以。

---

## 6. 全局 Skill / Agent 的安装与使用

都在管理后台 **设置中心 → AI 能力 → OpenCode 运行时** 页完成（写入上文的
`OPENCODE_GLOBAL_DIR`），不碰文件系统也能用：

- **技能**：在线新建（name + description + 正文）、上传 zip 包（≤5 MiB、≤200 文件、
  必须含 SKILL.md）、在线编辑辅助文件（脚本/模板/资源）、删除。
- **子智能体**：在线新建（name / description / mode=primary|subagent|all /
  model / temperature / top_p + 提示词正文）、禁用内置 agent（`disable: true` 遮罩文件）。

两条硬约束（OpenCode 侧规则，页面已做校验）：

1. skill 的 frontmatter `name` 必须与目录名一致；
2. `description` 必填——没有描述的 skill 会被 OpenCode **静默过滤**。

**生效语义（重点）**：所有文件写入立即落盘，但运行中的 serve 不会重载。页面会把
未生效条目标为 `pending`（已加载为 `loaded`，serve 不在线为 `serveOffline`），由管理
员点「重启 OpenCode」统一生效（`OPENCODE_RESTART_POLICY=auto` 可改为保存即重启，
代价是打断运行中的会话）。平台只能重启自己拉起的 serve；外部进程需人工停止。

---

## 7. 工作区级注入（平台自动，无需安装）

每个 AI 会话工作区由服务端自动准备，OpenCode 以"项目级配置"读取：

| 文件/目录 | 生成方 | 用途 |
|-----------|--------|------|
| `AGENTS.md` | 会话创建时 | 项目上下文与行为约束（git 操作规范、目录约定等） |
| `opencode.json` | 会话创建时 | 项目级 OpenCode 配置 |
| `.gitignore` | 会话创建时 | 静默 `uploads/ outputs/ .opencode/` 等噪声目录 |
| `.opencode/skills/<name>/` | 按需注入 | 平台技能库（`workspace_root/skills/`）里启用的技能按需复制进会话，如轨迹分析会话只注入 trace-analyzer 单技能（fail-closed） |

这部分完全由平台维护，升级平台即升级，无人工步骤。

---

## 8. 环境变量总表（server/.env）

| 变量 | 默认 | 必填 | 说明 |
|------|------|------|------|
| `OPENCODE_BASE_URL` | `http://127.0.0.1:4096` | 否 | serve 地址 |
| `OPENCODE_BIN` | `opencode` | Windows 建议 | serve 可执行文件完整路径 |
| `OPENCODE_SERVE_CMD` | 空 | 否 | 整条启动命令覆盖 |
| `OPENCODE_SERVE_CWD` | `~` | 否 | serve 工作目录 |
| `OPENCODE_AUTOSTART` | 开 | 否 | `0` 关闭自动拉起 |
| `OPENCODE_GLOBAL_DIR` | `~/.config/opencode` | 否 | 全局配置目录 |
| `OPENCODE_RESTART_POLICY` | `manual` | 否 | `auto`=保存即重启 serve |
| `OPENCODE_RESTART_TIMEOUT_SEC` | `60` | 否 | 重启健康检查窗口 |
| `OPENCODE_MODEL` | 空 | 否 | 默认对话模型（也可在系统设置页配置） |
| `MCP_INTERNAL_TOKEN` | 空 | **插件通道必填** | 内部事件上报鉴权 token |

---

## 9. 故障排查

| 现象 | 根因 | 处理 |
|------|------|------|
| SkillOpt 全是 inferred，无 runtime 行 | 插件未安装 / serve 未重启加载 / token 未配置 | 按 §5.5 三步自检；配置 `MCP_INTERNAL_TOKEN` 后重启 BKB + serve |
| 上报返回 403 | `MCP_INTERNAL_TOKEN` 未配置或与插件内嵌 token 不一致 | 重新配置后重启 BKB（重写插件）+ 重启 serve |
| 上报返回 404 | 端点路径/端口不对（历史版本插件指向 `/ai/chat/internal/...`） | 重启 BKB 后端，让 `ensure_runtime_plugin` 重写插件文件 |
| 日志出现 `upsert_invocation failed ... 违反外键约束` | 上报的 sessionID 无法映射到平台会话（会话已删除或伪造 id） | 无害，忽略；确认用真实会话模拟 |
| skill 改了不生效 | serve 不热加载（OpenCode 固有行为） | 管理页点「重启 OpenCode」，确认条目从 pending 变 loaded |
| 技能列表里看不到某个技能 | SKILL.md 缺 description 被 OpenCode 静默过滤 | 补 description；或 name 与目录名不一致 |
| 重启 OpenCode 报「外部进程」 | 端口上的 serve 不是平台拉起的 | 人工停止该进程后重试 |
| Windows 拉不起 serve、日志空 | `OPENCODE_BIN` 是裸命令名 | 配置完整 .exe 路径（§3.2） |
| serve 输出/工具输出中文乱码 | 子进程编码跟随 GBK | 平台已自动钉死 UTF-8（`PYTHONUTF8` 等）；确认未绕过平台启动方式 |

---

## 10. 一键自检清单

部署完成后按顺序执行：

```bash
# 1. opencode 可用
opencode --version

# 2. serve 在线（或由平台自动拉起）
curl http://127.0.0.1:4096/global/health

# 3. 插件文件就位且端点正确
head -6 ~/.config/opencode/plugin/baize-trace.js

# 4. .env 有内部 token
grep -c MCP_INTERNAL_TOKEN server/.env

# 5. 管理页技能状态全部 loaded（无 pending）
#    设置中心 → AI 能力 → OpenCode 运行时

# 6. 发起一次会话并使用技能后，确认确证数据
#    SELECT count(*) FROM ai_skill_invocations WHERE source='runtime';
```
