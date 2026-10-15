# MCP_INTERNAL_TOKEN 未配置：内部通道 403 的诊断与修复

适用：所有部署实例（尤其生产）｜首次定位：2026-10-15｜相关提交：门禁日志补全
`191cde9`、插件 stdio 落盘 `b7a76fa`、运行日志查看器（`/admin/logs`）

## 1. 症状

- `/admin/logs` 的 `ai-chat.log` 页签持续出现：

  ```
  WARNING [routes.ai_gate_internal] gate pre-check unauthorized:
  X-Internal-Token mismatch, from=127.0.0.1
  ```

- `oc-serve.log` 页签对应出现：`[baize-trace] gate pre-check http 403 → allow tool=…`
- 门禁策略「命中」列恒为 `0 / M`，但被监控的动作正常完成、终态断言可 passed
  （fail-open 放行的典型特征——不是"规则没配上"，是请求被 403 拒绝后静默放行）

## 2. 根因

`MCP_INTERNAL_TOKEN` 是 backend 与 OC 插件 / MCP 服务之间的**共享密钥**（请求头
`X-Internal-Token`），由部署方自行生成，**没有自动生成逻辑**。`server/.env.example`
的注释即为设计口径：

```
# MCP <-> Flask internal memory channel (M2). Set a random secret to enable the
# agent's memory_* tools; leave empty to disable the internal endpoints (403).
MCP_INTERNAL_TOKEN=
```

鉴权实现（`config.py` + 各 internal 路由）：

```python
MCP_INTERNAL_TOKEN = os.getenv('MCP_INTERNAL_TOKEN', '')
def _authorized():
    return bool(MCP_INTERNAL_TOKEN) and token == MCP_INTERNAL_TOKEN
```

**留空时 `bool('')` 恒 False：无论请求带什么 token 一律 403**。日志文案
"X-Internal-Token mismatch" 在"未配置"场景下略有误导——实际是服务端没有 token、
一律拒绝，而非值不匹配。

## 3. 影响面（未配置时全部静默失效）

| 通道 | 端点 | 未配置时的表现 |
| --- | --- | --- |
| 门禁过程检查 / 拦截 | `/ai/gate/internal/*` | 命中恒 0，拦截与提示词注入失效（fail-open 放行） |
| 运行时事件上报（SkillOpt 轨迹/技能使用证据） | `/ai/memory/internal/runtime-events` | 轨迹缺行、拟合证据缺失 |
| 记忆工具（memory_*） | `/ai/memory/internal` | 不可用 |
| MCP data 转发 | `/ai/data/internal` | data-ops 类工具不可用 |
| 子代理 pins | `/ai/subagent-internal` | 降级到三级兜底 |

所有消费方均为 fail-open 设计（可用性优先），因此**漏配不会以任何报错暴露**——
2026-10-15 门禁日志补全之前，该问题在生产上表现为"门禁从未命中"且无从定位。

## 4. 修复步骤（严格按顺序）

1. **生成密钥**（在部署服务器上执行，输出不要写入文档/截图/聊天记录）：

   ```bash
   python -c "import secrets;print(secrets.token_urlsafe(48))"
   ```

2. **写入 `server/.env`**：

   ```
   MCP_INTERNAL_TOKEN=<生成值>
   ```

   注意与 `JWT_SECRET` 分开生成、不要复用。

3. **重启后端**——启动时以当前 token 重写插件文件（`<OPENCODE_GLOBAL_DIR>/plugin/
   baize-trace.js` 嵌入值），并记录供"下次拉起 OC serve 时"传递的环境变量。

4. **重启 OC serve**——**必须在后端之后**。token 的优先链是
   `serve 进程 env（BAIZE_INTERNAL_TOKEN）> 插件文件嵌入值`；已在运行的 serve 进程
   揣着启动那一刻的旧状态，只重启后端改不了它。

5. **验证**（见下节）。

## 5. 验证

- 跑探针批：`python e2e/manual/gate_probe_run.py https://<部署地址>`（或在管理页按
  `e2e/manual/gate-task-subagent-probe.md` 手工建策略+建批）。
- 通过标准（双日志同时到位）：
  - `ai-chat.log`：`gate checkpoint-before hit rule=… tool=write fired=1/1`
    （不再是 `unauthorized`）
  - `oc-serve.log`：`[baize-trace] gate DENY tool=write session=…: <注入提示词>`
- 阴性对照（可选）：把 token 改回空/错误值并重启，403 应复现。

## 6. 排查速查

**token 三级链**：serve 进程 env `BAIZE_INTERNAL_TOKEN`（最高）→ 插件文件嵌入值
（回落）→ 服务端 `config.MCP_INTERNAL_TOKEN`（比对基准，来自 `.env`）。

**403 的两种情形**：

| 情形 | 判据 | 修复 |
| --- | --- | --- |
| 未配置 | `.env` 无 `MCP_INTERNAL_TOKEN` 或为空 | 按 §4 配置 |
| 值不匹配 | 两侧都非空但不相等（典型：改过 token 但 serve 未重启） | 按 §4 顺序重启对齐 |

**端到端实测**（不打印完整 token）：

```bash
E=$(grep -E "^MCP_INTERNAL_TOKEN" <后端目录>/.env | cut -d= -f2 | tr -d '\r')
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:<FLASK_PORT>/ai/gate/internal/pre-check \
  -H "X-Internal-Token: $E" -H "Content-Type: application/json" \
  -d '{"sessionId":"probe-sess","tool":"write","argsText":"filePath=x"}'
# 200=服务端正常；403=后端进程 token 与 .env 不符（后端未重启加载新值）
```

**插件文件核对点**：`<OPENCODE_GLOBAL_DIR>/plugin/baize-trace.js` 头部
`const TOKEN = process.env.BAIZE_INTERNAL_TOKEN || '<嵌入值>'`——若还是字面
`__TOKEN__` 说明后端从未成功重写（查后端启动日志 `SkillOpt: runtime plugin ensured`）。

## 7. 相关入口

- 日志查看：管理后台 → AI 能力组 →「运行日志」（`/admin/logs`，`admin.ai_chat_admin`）
- 探针脚本：`e2e/manual/gate_probe_run.py`；手工套件：`e2e/manual/gate-task-subagent-probe.md`
- 命中可见性：批详情子任务列表「门禁」徽标 + 「门禁」弹窗（命中 N/M、核对结果）
- 双日志矩阵速查：服务端命中/403 看 `ai-chat.log`；插件侧 DENY/http 失败看 `oc-serve.log`
