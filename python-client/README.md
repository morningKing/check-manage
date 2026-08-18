# check-manage Open API Python 客户端

`check-manage` Open API 的官方 Python 客户端。封装了认证、集合读写、分支选择、文件上传/下载、
"上传文件 + 写入 file/image 字段"的一步式便捷方法，以及 AI 批任务、AI 单会话、行操作触发、AI
定时任务、Prompt 模板、长期记忆管理这几套 AI 相关的对外端点，方便直接在你自己的代码里集成。

接口行为的权威说明分散在几份文档里，本客户端只是对其中接口的薄封装，不引入额外行为：
- 数据集合/分支/文件：[`docs/user-guide/integration/open-api.md`](../docs/user-guide/integration/open-api.md)
- AI 批任务：[`docs/user-guide/integration/ai-batch-api.md`](../docs/user-guide/integration/ai-batch-api.md)
- AI 单会话（可选带文件）：[`docs/user-guide/integration/ai-session-api.md`](../docs/user-guide/integration/ai-session-api.md)
- Row Actions 对外触发：[`docs/user-guide/data/row-actions.md`](../docs/user-guide/data/row-actions.md) §11
- AI 定时扫描任务：[`docs/user-guide/ai/scan-tasks.md`](../docs/user-guide/ai/scan-tasks.md) §10
- Prompt 模板对外 API：[`docs/user-guide/ai/batch-tasks.md`](../docs/user-guide/ai/batch-tasks.md)「Prompt 模板」节
- 长期记忆对外 API：[`docs/user-guide/ai/long-term-memory.md`](../docs/user-guide/ai/long-term-memory.md)「对外 API」节

## 安装

```bash
cd python-client
pip install -e .
```

## 快速开始

```python
from checkmanage_openapi import OpenApiClient

with OpenApiClient(
    api_key="cm_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2",
    base_url="http://localhost:7001/api/v1",
) as client:
    # 列出所有已开放的集合
    for c in client.list_collections():
        print(c["collection"], c["name"], "读写" if c["writable"] else "只读")

    # 自动翻页遍历一个集合的全部数据
    for record in client.iter_records("inspection-cases"):
        print(record["id"])

    # 新增一条记录
    new_record = client.create_record("inspection-cases", {
        "caseName": "API 新增用例",
        "priority": "high",
    })

    # 部分更新（只传要改的字段），并做乐观锁并发检测
    client.update_record(
        "inspection-cases", new_record["id"],
        {"status": "inactive"},
        version=new_record.get("_version"),
    )
```

## 文件上传与文件字段录入

`file` / `image` 类型的字段存的是"已上传文件的引用对象数组"，不是二进制内容本身。
分两步：先上传文件本体拿到 `uid`，再把包含 `uid` 的对象数组写进记录字段。

**一步到位（推荐）：**

```python
client.attach_files(
    "devices",           # 集合
    "附件",               # 目标 file/image 字段名
    "./report.pdf",       # 单个路径，或路径列表（多文件字段）
    {"名称": "外部记录1"},  # 记录的其他字段
)
```

`attach_files` 已知目标字段名，会自动带上，若该字段在管理端配置了「允许的文件类型」，
类型不符时这里会抛 `ValidationError`。

**分步调用（需要更细粒度控制时）：**

```python
uploaded = client.upload_file("devices", "./report.pdf", field_name="附件")   # -> {"uid": ..., "name": ..., ...}
file_field = client.to_file_field(uploaded)                # -> {"uid", "name", "size", "type"}

client.create_record("devices", {
    "名称": "外部记录1",
    "附件": [file_field],
})
```

`field_name` 是可选参数：传了会按该字段配置的「允许的文件类型」做服务端校验（不传则不限制，
向后兼容旧调用）。

**下载文件**（记录里读到的文件对象，`apiUrl` 是 Open API 专用下载地址）：

```python
data = client.download_file(file_id, dest="./downloaded.pdf")  # 返回 bytes，dest 可选
```

## 错误处理

所有失败请求都会抛出 `OpenApiError`（或其子类），而不是让你手动检查状态码：

```python
from checkmanage_openapi import (
    OpenApiClient, AuthenticationError, WriteNotAllowedError,
    NotFoundError, ValidationError, VersionConflictError,
)

try:
    client.create_record("inspection-cases", {})
except ValidationError as e:
    print("校验失败:", e.details)       # ["名称 is required", ...]
except WriteNotAllowedError:
    print("该集合未开启「允许写入」")
except AuthenticationError:
    print("API Key 无效或已停用")
```

| 异常 | 对应状态码 | 触发场景 |
|------|-----------|---------|
| `AuthenticationError` | 401 | API Key 缺失 / 无效 / 已停用 |
| `WriteNotAllowedError` | 403 | 集合未开启「允许写入」 |
| `NotFoundError` | 404 | 集合 / 记录 / 分支 / 文件不存在 |
| `ValidationError` | 400 | 请求体为空或必填字段缺失（`.details` 为具体字段列表） |
| `ConflictError` | 409 | 记录 ID 冲突 / 主键冲突 |
| `VersionConflictError`（`ConflictError` 子类） | 409 | 乐观锁并发冲突，需重新 GET 最新 `_version` 后重试 |
| `MemoryUnavailableError`（`ConflictError` 子类） | 409 | `add_memory` 时记忆功能未配置（缺 AI API Key 或 mem0 未启用） |

> AI 相关端点的 `error` 文案有中文也有英文（批任务/Prompt 模板是英文，行操作/AI 定时任务是中文——服务端历史决定，不代表本客户端做了翻译），异常判定纯看状态码 + `code`，跟文案语言无关。

## 分支（Branch）

所有读写方法都接受 `branch_id` 关键字参数（默认 `"main"`）：

```python
branches = client.list_branches()
client.list_records("devices", branch_id="pv-abc123")
client.create_record("devices", {"名称": "x"}, branch_id="pv-abc123")
```

## AI 相关能力

除数据集合外，本客户端还覆盖了 6 套 AI 相关的对外端点。所有新增方法的返回值都是响应体
`resp.json()` 的原样 dict（文件下载方法返回 `bytes`，204 的删除方法返回 `None`），不做拆包改形。

**AI 批任务**（每个文件对应一个子任务/AI 会话，异步处理）：

```python
uploaded = client.upload_batch_files(["./report1.pdf", "./report2.pdf"])
batch = client.create_batch(
    "季度报告分析", "请总结每份文档的核心结论，并列出风险点。",
    uploaded["files"], agent="build",
)
# 轮询，或创建时传 callback_url 改为等待完成回调（见 ai-batch-api.md §4.2b）
detail = client.get_batch(batch["batchId"])
if detail["status"] in ("completed", "partial", "failed"):
    results = client.get_batch_results(batch["batchId"])["results"]
```

**AI 单会话**（一句 prompt 要一个答案，可选带上几个文件——全部读进同一个会话，不像批任务那样按文件拆分）：

```python
session = client.create_ai_session("帮我用三句话总结一下敏捷开发的核心理念。")
detail = client.get_ai_session(session["sessionId"])
if detail["status"] in ("completed", "failed"):
    print(detail["output"] or detail["error"])

# 带文件：复用批任务的上传接口暂存，再传给单会话
uploaded = client.upload_batch_files(["./report1.pdf", "./report2.pdf"])
session = client.create_ai_session(
    "请总结这两份报告的共同风险点。", files=uploaded["files"],
)
```

**Row Actions 对外触发**（触发页面上已配置好的行操作按钮）：

```python
client.run_row_action("orders", "rec-123", "act-1", params={"note": "外部系统触发"})
```

**AI 定时任务**（立即触发一次已配置的扫描任务）：

```python
for task in client.list_scan_tasks()["tasks"]:
    print(task["id"], task["name"], task["enabled"])

result = client.run_scan_task_now("scan-abc12345")
print("本次认领记录数:", result["claimedCount"])
```

**Prompt 模板**（按密钥所属用户维护的常用提示词库）：

```python
tpl = client.create_prompt_template("周报总结", "请总结以下文档的核心结论与风险点。")
client.update_prompt_template(tpl["id"], "周报总结v2", "请总结核心结论、风险点与下一步建议。")
client.delete_prompt_template(tpl["id"])
```

**长期记忆**：

```python
from checkmanage_openapi import MemoryUnavailableError

try:
    client.add_memory("偏好简洁的代码风格，不要多余注释", verbatim=True)
except MemoryUnavailableError:
    print("记忆功能未配置：去设置中心把「AI 设置」的 API Key 和记忆开关配完整")

for m in client.list_memories()["memories"]:
    print(m["id"], m["memory"])
```

## 运行测试

```bash
cd python-client
pip install -e ".[test]"
pytest
```

测试全部基于 `unittest.mock` 模拟 HTTP 层，不依赖真实服务器。
