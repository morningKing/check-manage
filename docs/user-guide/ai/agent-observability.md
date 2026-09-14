# AI Agent 可观测性

Check-Manage 可将 AI 助手和 AI 批任务的执行轨迹导出到管理员部署的
Langfuse。Langfuse 用于查看一次 AI 回合的耗时、模型调用、工具调用和错误信息；
它不是 Check-Manage 的会话存储，也不会替代系统中的会话审计。

部署和运维说明见 [Langfuse 自托管](../../operations/langfuse-self-hosting.md)。

## 使用前提

管理员需要先完成以下配置：

1. 部署 Langfuse v4 Web、Worker、PostgreSQL、Redis/Valkey、ClickHouse 和
   S3/Blob Storage，并通过 HTTPS 暴露 Langfuse 控制台。
2. 在 Langfuse 中创建项目和项目 API key。
3. 在 Check-Manage 后端设置 `LANGFUSE_ENABLED=true`、`LANGFUSE_HOST`、
   `LANGFUSE_PROJECT_ID`、`LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY`。
4. 根据隐私政策设置 `LANGFUSE_CAPTURE_CONTENT` 和
   `LANGFUSE_SAMPLE_RATE`，然后重启后端。

Python SDK 依赖范围为 `langfuse>=3.63.0,<4.0.0`。Trace ID 由 Check-Manage
确定性生成，始终是 32 个字符的小写十六进制字符串；同一回合的导出和页面链接
使用同一个 ID。`LANGFUSE_SAMPLE_RATE` 按 Trace 采样，而不是按单个 observation
采样，因此一个 Trace 内的记录不会因采样再次分裂。

密钥只配置在后端环境中，不会发送到浏览器。不要把真实密钥写进文档、
截图、前端代码或提交记录。

## 在 AI 助手中查看 Trace

1. 打开顶部的 **AI 助手**，新建或切换到一个会话。
2. 发送一条消息，等待本轮回复至少产生一个可导出的执行轨迹。
3. 在对话抽屉的会话内容区域找到 **查看 Trace**。
4. 点击后，系统在新标签页打开该回合对应的 Langfuse Trace；如果浏览器
   阻止了新标签页，请允许当前站点打开外部链接。
5. 在 Langfuse 中查看模型、工具调用、耗时和错误信息。批任务子会话打开后也
   按同样方式查看其 Trace。

`查看 Trace` 只在后端返回有效的、已确定被采样的 Trace URL 时显示。Langfuse
未配置、该回合未被采样、导出仍在队列中或导出失败时，按钮可能不出现；这不影响 AI
会话本身继续运行。刷新会话或等待后台 Worker/Exporter 完成后再检查。

## 批任务和子智能体

- AI 批任务的每个子会话有独立的 Trace，点击对应子任务后查看该子任务的
  链接。
- 子智能体和工具调用作为父回合中的嵌套执行轨迹展示，具体层级取决于
  OpenCode 和 Langfuse 的映射结果。
- Trace URL 是外部 Langfuse 地址。能否打开它由 Langfuse 项目权限、网络
  访问策略和当前登录状态决定；Check-Manage 不代理 Langfuse 页面。

## 隐私提示

默认只导出用于排障的元数据（环节类型、调用名、状态、耗时、模型、应用会话/批任务/任务标识），不导出 Prompt、模型回复和工具内容。
`LANGFUSE_CAPTURE_CONTENT=false` 时不会向 Langfuse 请求发送原始 Prompt、模型回复
或工具内容。管理员启用 `LANGFUSE_CAPTURE_CONTENT=true` 后，相关内容可能包含用户输入、
文件名、工具参数和 AI 输出；系统会对常见密码、Token、Secret、API key、
Authorization 和 Cookie 字段做脱敏，但无法识别所有业务敏感信息。

请在启用内容采集前完成数据分类和授权评审，并在 Langfuse 中限制项目成员、
配置保留期限和删除流程。可用 `LANGFUSE_SAMPLE_RATE` 降低采集比例：`0` 为
关闭，`1` 为全部采集，介于两者之间时按稳定规则采样。采样或导出失败时，
AI 对话仍以 Check-Manage 的正常链路为准。

## 没有“查看 Trace”时

- 确认 `LANGFUSE_ENABLED=true` 且 public/secret key 均已设置。
- 确认 `LANGFUSE_PROJECT_ID` 已设置；它用于构造项目级 Trace URL。
- 确认 `LANGFUSE_HOST` 是可从 Check-Manage 后端访问的 HTTPS 地址。
- 确认采样率不是 `0`，并等待导出队列刷新。
- 查看后端日志中的 Langfuse export 错误；不要在日志中打印或粘贴密钥。
- 确认 Langfuse Web 和 Worker 均健康，Redis/Valkey、ClickHouse、Postgres
  和对象存储均可用。
