# Open API 批量创建记录 — 设计

## 背景

Open API（`server/routes/open_api.py`，API Key 鉴权，`@api_key_required`）目前只有单条创建：`POST /api/v1/collections/<collection>`（`create_collection_item`）。外部调用方需要一次性导入较多数据时，只能循环调用单条创建，HTTP 往返开销大。内部管理端已有 `POST /<collection>/batch-create`（`server/routes/dynamic.py`，JWT 会话鉴权），但走的是完全独立的鉴权路径，Open API 的 API Key 调用不了它。

## 目标

新增 `POST /api/v1/collections/<collection>/batch`，语义上是 `create_collection_item` 的批量版本（同一套校验规则，只是对 N 条记录做一次请求）。

## 关键语义（已与用户确认）

1. **create-only，不做 upsert**：与单条创建一致——批次里任何一条 `id` 已存在（无论是批次内部重复，还是数据库里已存在）都算该条失败，不会静默更新已有记录。这与内部 `batch-create`（upsert 语义）不同，是有意的：Open API 单条创建本来就是 create-only，批量版本不引入单条没有的能力。
2. **支持 `continueOnError`**：请求体 `options.continueOnError`（默认 `false`）。为 `false` 时，只要有一条记录校验失败，整个请求返回 `400`，列出所有失败记录，一条都不写入。为 `true` 时，跳过失败记录，把能写入的都写入，返回结果里同时带成功和失败明细。
3. **单次最多 1000 条**：超过直接 `400`，提示调用方分批提交。这是应用层保护（外部 API Key 调用方不像内部管理端那样受信任，需要防止单条超大 SQL 事务/内存占用拖垮服务）。

## 请求 / 响应

```
POST /api/v1/collections/<collection>/batch?branchId=main
Content-Type: application/json
X-API-Key: <key>

{
  "records": [
    { "id": "prod-001", "name": "产品A", "price": 99 },
    { "name": "产品B", "price": 199 }
  ],
  "options": { "continueOnError": true }
}
```

成功（HTTP 201，即使部分记录失败也是 201——是否算"这次调用成功"由 `failed` 字段体现，不是靠 HTTP 状态码区分）：

```json
{
  "data": [
    { "id": "prod-001", "name": "产品A", "price": 99, "createdAt": "...", "branchId": "main" },
    { "id": "api-a1b2c3d4e5f6", "name": "产品B", "price": 199, "createdAt": "...", "branchId": "main" }
  ],
  "created": 2,
  "failed": 0
}
```

`continueOnError=false` 且存在失败记录（HTTP 400，不写入任何记录）：

```json
{
  "error": "Validation failed for one or more records",
  "failed": 2,
  "errors": [
    { "index": 0, "error": "Record ID already exists", "record": { "id": "prod-001", "...": "..." } },
    { "index": 3, "error": "name is required", "record": { "...": "..." } }
  ]
}
```

`continueOnError=true` 且存在失败记录（HTTP 201，成功的已写入，失败的在 `errors` 里）：

```json
{
  "data": [ /* 只包含成功创建的记录 */ ],
  "created": 39,
  "failed": 3,
  "errors": [ { "index": 5, "error": "Record ID already exists", "record": { "...": "..." } } ]
}
```

超过 1000 条（HTTP 400，不做任何校验/写入）：

```json
{ "error": "Batch size exceeds maximum of 1000 records" }
```

## 后端实现

`server/routes/open_api.py` 新增 `create_batch_items(collection)`，复用 `create_collection_item` 已有的全部 helper（不新增校验逻辑，只是把单条逻辑套进一个循环 + 批量写入）：

1. `check_collection_writable` / `get_request_branch_id`（与单条一致）。
2. 读 `body['records']`（列表）+ `body.get('options', {})`；`len(records) > 1000` → 400；`records` 为空 → 400（"records is required"，与内部 batch-create 一致的错误文案）。
3. `fields = get_page_fields(cur, collection)`，`pk_fields = get_primary_key_fields(cur, collection)`（批次内所有记录共享，只查一次，不像单条创建那样每次请求查一次——这是批量版本相对单条循环调用的真实性能收益之一）。
4. 批次内重复 ID 检测（同批次两条记录用了同一个非空 `id` 视为该条失败），逻辑与内部 `batch-create` 的 duplicate-id 检测一致。
5. 已存在 ID 的批量检测：`SELECT id FROM dynamic_data WHERE collection = %s AND id = ANY(%s) AND branch_id = %s`（一次查询覆盖整批，而不是每条记录单独查一次——内部 batch-create 已经是这个模式，直接照搬）。
6. 逐条校验（复用 `validate_required_fields` + `check_primary_key_unique`，后者仍是每条一次查询，因为是按字段值查找、无法简单批量化——这一点与内部 batch-create 相同，是已知的、可接受的开销）。每条失败都记成 `{"index": i, "error": "...", "record": records[i]}`（回显原始记录，调用方不用自己对着 index 去翻自己传的数组）：
   - 必填字段校验失败 → 记入 `errors`。
   - `id` 为空 → 生成 `api-<uuid>`；`id` 在批次内重复或已存在于数据库 → 记入 `errors`（不做 upsert）。
   - 主键字段唯一性冲突（若该集合配置了主键字段）→ 记入 `errors`。
   - 都通过 → 加入待写入列表，同时套用单条创建里的 statusBadge 初始时间戳逻辑（`data[f'_statusBadge_{fieldName}_changedAt']`）、`compute_search_text`。
7. `continueOnError=false` 且 `errors` 非空 → 直接 400 返回，不写入任何记录（此时还没执行任何 INSERT）。
8. 待写入记录用 `psycopg2.extras.execute_values` 一次性 `INSERT INTO dynamic_data (id, collection, data, branch_id, search_text) VALUES %s RETURNING id, collection, data, created_at`（不带 `ON CONFLICT`，因为是 create-only；`RETURNING` 拿回真实写入的行用于响应体的 `data` 数组，行序会跟传入的 VALUES 顺序一致，可以直接按顺序配对，不需要额外映射）。
9. 写入后统一调用一次 `reseed_sequences(cur, collections=[collection])`（与单条创建、内部 batch-create 保持同一个不变式：任何绕过 create_item 的写入路径写完后都要重播种）。
10. 响应：`{"data": [...], "created": N, "failed": M}`，`failed > 0` 时加 `"errors": [...]`。始终 `201`（除了第 7 步的提前 400 和第 2 步的参数校验 400）。

**不做的事**（与单条创建 `create_collection_item` 现状保持一致，不额外增强）：
- 不处理 `relations` 字段——`create_collection_item` 本身就不支持 relation 字段写入（只有内部 `dynamic.py` 的 batch-create 支持），批量版本作为它的批量化版本，同样不支持，避免批量接口比单条接口能力还多、造成语义不一致。
- 不做 upsert（已在"关键语义"里说明）。
- 不做 webhook 触发（`create_collection_item` 单条创建本身也没有触发 webhook；批量版本保持一致，不额外引入）。

## 测试

`server/tests/test_open_api.py`：新增覆盖——
- 成功批量创建（多条，含未指定 id 的自动生成）。
- `continueOnError=false` + 存在一条已有 id → 整批 400，且验证数据库里确实一条都没写入。
- `continueOnError=true` + 部分失败 → 201，`created`/`failed` 计数正确，成功的记录确实落库，失败的没有落库。
- 批次内重复 id → 计入失败（区分"批次内重复"与"数据库已存在"两种，两个测试用例）。
- 超过 1000 条 → 400，且不查库不写库（可以用 `>1000` 条最小化测试数据本身的构造成本，比如 1001 条极简记录）。
- 集合非公开 / 只读 → 复用 `check_collection_writable` 已有的错误路径，加一个用例确认批量端点同样会被这个检查拦下。
- 主键唯一性冲突 → 至少一个用例覆盖配置了主键字段的集合。

## Python 客户端 + 文档同步

`python-client/checkmanage_openapi/client.py` 新增 `batch_create_records`，紧跟在 `create_record` 之后（同一个"5.6/5.7 — 记录写入"注释块下）：

```python
def batch_create_records(
    self,
    collection: str,
    records: list,
    *,
    branch_id: str = "main",
    continue_on_error: bool = False,
) -> dict:
    """批量创建记录（单次最多 1000 条）。

    返回 {"data": [...], "created": N, "failed": M, "errors"?: [...]}。
    continue_on_error=False（默认）时，只要有一条记录校验失败就不会写入
    任何记录，整个调用抛 ApiError；True 时跳过失败记录，成功的会写入，
    返回值里的 failed/errors 描述哪些没写进去。
    """
    resp = self._request(
        "POST",
        f"/collections/{collection}/batch",
        json_body={"records": records, "options": {"continueOnError": continue_on_error}},
        params={"branchId": branch_id},
    )
    return resp.json()
```

`python-client/tests/test_client.py` 加对应用例（mock `_request` 返回值，验证方法把 `records`/`options` 正确组进请求体）。

文档同步：
- `docs/user-guide/integration/open-api.md`：在"5.6 新增记录"之后插入新的"5.7 批量新增记录"小节（请求/响应示例照抄上面"请求 / 响应"一节），原 5.7/5.8/5.9 依次顺延为 5.8/5.9/5.10；同步修正第 645 行"（见 5.9）"这一处交叉引用为"（见 5.10）"（这是全文唯一引用旧编号的地方，其余"详见 5.6"处不受影响，因为 5.6 本身没有移动）。"6.1 cURL"/"6.2 Python" 示例区可以各加一行批量创建示例。
- `docs/user-guide/integration/open-api-reference.html`：仿照现有"5.6 新增记录"卡片结构（`id="ep-create"`），新增一张 `id="ep-batch-create"` 卡片，eyebrow 从 "5.7" 开始，原本 "5.7"/"5.8"/"5.9" 三张卡片的 eyebrow 文案依次顺延为 "5.8"/"5.9"/"5.10"（HTML 锚点是按名字 `#ep-*` 不是按数字，所以顺延只是显示文案改动，不会破坏任何现有链接）；顶部导航栏加一个对应的 `<a href="#ep-batch-create">批量新增记录</a>` 链接。

## 非目标

- 批量更新 / 批量删除的 Open API 端点（本次只做批量创建）。
- upsert 语义（已在"关键语义"说明，是有意排除）。
- relation 字段批量写入（现状单条创建都不支持，不在本次顺带解决）。
