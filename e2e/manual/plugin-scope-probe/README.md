# OC 插件作用域探针 —— 平台插件不得影响非平台会话

> 目的：验证 `utils/oc_plugin_scope.py` 的作用域修复在**真实 OpenCode serve**
> 里成立。三个平台插件（baize-trace / baize-tool-timeout /
> baize-subagent-reuse）装在 OpenCode 全局目录，随本机**每一个** OpenCode
> 实例加载；修复前钩子对所有会话生效——每次工具调用前后打后端、task 委派
> 查后端、挂 abort 看门狗，后端没开时错误行刷进用户会话的 stderr。

## 场景（隔离栈，0 LLM）

模型大脑是脚本化 stub（`stub_llm.py`：第一步 bash `echo scope-probe`，
看到 tool 结果后收尾 done——没有终态会无限循环，stub 每次都回工具调用）。
其余链路全真：OC serve（V1 1.19.3）→ 三个平台插件 → sink（记录收到的
每个请求路径的本地 HTTP 服务，顶替平台后端内部端点）。

| # | 会话目录 | 预期 |
| --- | --- | --- |
| A | `<sandbox>/probe-workspaces/user-admin/s1`（在渲染进插件的平台根之下） | 工具回合触发 pre-check + post-check（>0 请求） |
| B | `<sandbox>/probe-other/project`（平台根之外） | 整个回合 **0** 个插件请求 |

## 运行

```bash
cd e2e/manual/plugin-scope-probe
python plugin_scope_probe.py           # 起栈→两场景→自动判定→拆栈
python plugin_scope_probe.py --keep    # 保留栈人工排查（须手工杀 serve/stub）
```

前置：`server/.env` 可用（`OPENCODE_BIN`）；:4198/:4199 空闲；首次跑 OC
serve 会对隔离全局目录自动装依赖（bun install ~15s，需网络；代理 flaky 时
可先在 `<sandbox>/xdg-config/opencode` 手工 `npm install`）。

## 已知坑（首次编写实测）

- **派发端点是 `/session/{id}/prompt_async`**（平台 send_prompt_async 同款），
  body 带 `model:{providerID,modelID}`，directory 走 query 参数。POST
  `/session/{id}/message` 用同样 body 不报错但**连 user 消息都不落**。
- 新目录首次实例创建会在线装插件依赖 + 拉 models.dev，可能远超 30s——
  创建会话要带重试（90s×3）；或首跑前手工 `npm install` 全局目录。
- 配置漂移会锁死维度：chroma 集合按首次初始化的嵌入维度建，换嵌入提供方
  后维度不匹配 → 清空 `~/.check-manage/mem0` 重建（记忆页提示同款）。
