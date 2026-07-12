# check-manage Open API Python 客户端

`check-manage` Open API 的官方 Python 客户端。封装了认证、集合读写、分支选择、文件上传/下载，
以及"上传文件 + 写入 file/image 字段"的一步式便捷方法，方便直接在你自己的代码里集成。

接口行为的权威说明见 [`docs/user-guide/integration/open-api.md`](../docs/user-guide/integration/open-api.md)；
本客户端只是对文档中接口的薄封装，不引入额外行为。

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

**分步调用（需要更细粒度控制时）：**

```python
uploaded = client.upload_file("devices", "./report.pdf")   # -> {"uid": ..., "name": ..., ...}
file_field = client.to_file_field(uploaded)                # -> {"uid", "name", "size", "type"}

client.create_record("devices", {
    "名称": "外部记录1",
    "附件": [file_field],
})
```

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

## 分支（Branch）

所有读写方法都接受 `branch_id` 关键字参数（默认 `"main"`）：

```python
branches = client.list_branches()
client.list_records("devices", branch_id="pv-abc123")
client.create_record("devices", {"名称": "x"}, branch_id="pv-abc123")
```

## 运行测试

```bash
cd python-client
pip install -e ".[test]"
pytest
```

测试全部基于 `unittest.mock` 模拟 HTTP 层，不依赖真实服务器。
