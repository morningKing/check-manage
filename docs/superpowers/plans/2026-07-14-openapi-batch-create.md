# Open API 批量创建记录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open API 新增 `POST /api/v1/collections/<collection>/batch`，一次请求创建多条记录（最多 1000 条），语义上是既有单条创建 `create_collection_item` 的批量版本：create-only（不做 upsert）、支持 `continueOnError` 部分失败容忍。同步更新 Python 客户端包和两份用户文档。

**Architecture:** 复用 `create_collection_item` 已有的全部校验 helper（`check_collection_writable`/`get_request_branch_id`/`get_page_fields`/`get_primary_key_fields`/`validate_required_fields`/`check_primary_key_unique`），只是把单条循环的校验逻辑套进一次请求处理 N 条记录；共享配置（`fields`/`pk_fields`）和已存在 ID 检测各只查一次（而不是像"循环调用单条创建"那样每条都查一次），这是批量端点相对多次单条调用的真实性能收益。写入用 `psycopg2.extras.execute_values(..., fetch=True)` 一次性批量 INSERT 并拿回 `RETURNING` 的完整记录。

**Tech Stack:** Flask + psycopg2（后端）、Python `requests` 客户端包（`python-client/`）、Markdown + 静态 HTML（用户文档）。

参考设计文档：`docs/superpowers/specs/2026-07-14-openapi-batch-create-design.md`（已经用户确认）。

## Global Constraints

- **Create-only，不做 upsert**：批次里任何一条 `id` 已存在（批次内部重复，或数据库里已存在）都算该条失败，绝不静默更新已有记录。
- **`continueOnError`**（请求体 `options.continueOnError`，默认 `false`）：为 `false` 时，只要有一条记录校验失败，整个请求返回 `400`，不写入任何记录；为 `true` 时，跳过失败记录，成功的都写入，返回结果同时带成功和失败明细。
- **单次最多 1000 条**：超过直接 `400`，不做任何进一步处理（不查库、不写库）。
- **不处理 `relations` 字段**：与 `create_collection_item`（单条创建，本身就不支持写 relation 字段）保持一致，不额外增强。
- **不触发 webhook**：`create_collection_item` 单条创建本身没有触发 webhook，批量版本保持一致。
- 响应始终 `data` 数组只包含**实际成功写入**的记录（`continueOnError=true` 部分失败时，`data` 不包含失败的记录）。
- 批次内重复 ID 判定：**该 ID 值出现次数 > 1 的所有记录**都算失败（不是只让第 2 次出现的失败、第 1 次放行）——与内部 `routes/dynamic.py::batch_create_items` 的既有约定一致。

---

### Task 1: 后端批量创建端点

**Files:**
- Modify: `server/routes/open_api.py`
- Test: `server/tests/test_routes_open_api.py`

**Interfaces:**
- Produces: `POST /api/v1/collections/<collection>/batch` → `201 {"data": [...], "created": N, "failed": M, "errors"?: [...]}` 或 `400 {"error": ..., "failed": M, "errors": [...]}`。

- [ ] **Step 1: 在 `server/routes/open_api.py` 里新增 `create_batch_items` 路由**

紧接在 `create_collection_item` 函数结尾（第 428 行 `return jsonify({'data': result}), 201`）之后、下一个路由 `@open_api_bp.route('/collections/<collection>/<item_id>', methods=['PUT'])`（第 431 行）之前插入：

```python
@open_api_bp.route('/collections/<collection>/batch', methods=['POST'])
@api_key_required
def create_batch_items(collection):
    """Batch-create multiple records in a public writable collection.

    Create-only (no upsert): any record whose id already exists — either
    duplicated within the batch or already present in the database — counts
    as a failed record for that index, never overwrites an existing row.
    """
    MAX_BATCH_SIZE = 1000

    with get_db() as conn:
        cur = conn.cursor()

        # Check collection is public and writable
        error_resp = check_collection_writable(cur, collection)
        if error_resp:
            return error_resp

        # Get and validate branch_id
        branch_id, branch_error = get_request_branch_id(cur)
        if branch_error:
            return branch_error

        body = request.get_json(silent=True)
        if not body:
            return jsonify({'error': 'Request body is required'}), 400

        records = body.get('records')
        if not records:
            return jsonify({'error': 'records is required'}), 400
        if len(records) > MAX_BATCH_SIZE:
            return jsonify({'error': f'Batch size exceeds maximum of {MAX_BATCH_SIZE} records'}), 400

        options = body.get('options', {})
        continue_on_error = options.get('continueOnError', False)

        fields = get_page_fields(cur, collection)
        pk_fields = get_primary_key_fields(cur, collection)

        # Duplicate ID detection within the batch: any id value that appears
        # more than once fails ALL of its occurrences (not just the 2nd+),
        # matching routes/dynamic.py::batch_create_items's convention.
        id_counts = {}
        for record in records:
            rid = record.get('id')
            if rid:
                id_counts[rid] = id_counts.get(rid, 0) + 1
        duplicate_id_values = {rid for rid, count in id_counts.items() if count > 1}

        # Batch-check which of the explicitly-provided ids already exist
        # (one query for the whole batch, not one per record).
        all_ids = list(id_counts.keys())
        existing_ids = set()
        if all_ids:
            cur.execute(
                'SELECT id FROM dynamic_data WHERE collection = %s AND id = ANY(%s) AND branch_id = %s',
                (collection, all_ids, branch_id),
            )
            existing_ids = set(row[0] for row in cur.fetchall())

        from datetime import datetime
        errors = []
        prepared = []

        for idx, record in enumerate(records):
            rid = record.get('id')

            if rid and rid in duplicate_id_values:
                errors.append({'index': idx, 'error': 'Duplicate ID in batch', 'record': record})
                continue

            req_errors = validate_required_fields(record, fields)
            if req_errors:
                errors.append({
                    'index': idx,
                    'error': 'Validation failed: ' + '; '.join(req_errors),
                    'record': record,
                })
                continue

            body_copy = dict(record)
            record_id = body_copy.pop('id', None) or f'api-{uuid.uuid4().hex[:12]}'

            if rid and rid in existing_ids:
                errors.append({'index': idx, 'error': 'Record ID already exists', 'record': record})
                continue

            if pk_fields:
                pk_error = check_primary_key_unique(cur, collection, body_copy, pk_fields, branch_id=branch_id)
                if pk_error:
                    errors.append({'index': idx, 'error': pk_error, 'record': record})
                    continue

            data = {k: v for k, v in body_copy.items() if k not in ('createdAt', 'updatedAt', '_version')}

            # statusBadge 字段：创建时若已带初始值，盖变化时间戳作为超时判定基准
            # （与 create_collection_item 单条创建的逻辑一致）
            for f in (fields or []):
                if f.get('controlType') == 'statusBadge' and data.get(f.get('fieldName')):
                    data[f'_statusBadge_{f["fieldName"]}_changedAt'] = datetime.now(timezone.utc).isoformat()

            prepared.append({'id': record_id, 'data': data})

        # continueOnError=false 且有任何一条失败：整批 400，不写入任何记录
        if errors and not continue_on_error:
            return jsonify({
                'error': 'Validation failed for one or more records',
                'failed': len(errors),
                'errors': errors,
            }), 400

        created_records = []
        if prepared:
            values = [
                (p['id'], collection, psycopg2.extras.Json(p['data']), branch_id,
                 compute_search_text(p['data'], fields))
                for p in prepared
            ]
            rows = psycopg2.extras.execute_values(
                cur,
                'INSERT INTO dynamic_data (id, collection, data, branch_id, search_text) VALUES %s '
                'RETURNING id, collection, data, created_at',
                values,
                fetch=True,
            )
            for row in rows:
                rec = row_to_record(row)
                rec['branchId'] = branch_id
                created_records.append(rec)

            from utils.sequences import reseed_sequences
            reseed_sequences(cur, collections=[collection])

    result = {
        'data': created_records,
        'created': len(created_records),
        'failed': len(errors),
    }
    if errors:
        result['errors'] = errors
    return jsonify(result), 201
```

**关键实现细节（容易出错，务必照做）**：
- `psycopg2.extras.execute_values(...)` 必须传 `fetch=True` 才会真正执行 `RETURNING` 并把结果行收集返回；不传 `fetch=True`（默认 `False`）时 `RETURNING` 的行会被丢弃，函数返回 `None`。内部 `routes/dynamic.py::batch_create_items` 的 `execute_values` 调用没有用 `RETURNING`（不需要拿回整行），所以那处代码里看不到 `fetch=True` 的用法——这是本任务新引入的用法，不要照抄那边的调用方式。
- `existing_ids`/`duplicate_id_values` 的判定要在**生成随机 id 之前**用 `rid = record.get('id')`（原始请求里的值）去查，不要用 `record_id`（可能是刚生成的随机值）去查——否则永远查不到冲突。

- [ ] **Step 2: 编写测试**

在 `server/tests/test_routes_open_api.py` 的 `class TestCreateRecord:`（结束于第 250 行）之后、`class TestUpdateRecord:`（第 253 行）之前插入：

```python
class TestBatchCreateRecords:
    def test_collection_not_writable(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, False),  # public but not writable
        ])
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': [{'name': 'A'}]}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 403

    def test_missing_records_field(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
        ])
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        assert 'records is required' in resp.get_json()['error']

    def test_batch_size_exceeds_limit(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),  # writable check
        ])
        records = [{'name': f'item-{i}'} for i in range(1001)]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        assert 'exceeds maximum' in resp.get_json()['error']

    def test_batch_create_success(self, setup):
        client, mock_cursor, api_h = setup
        fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),   # writable check
            (fields,),      # get_page_fields
            ([],),          # get_primary_key_fields -> pk_fields = []
        ])
        mock_cursor.fetchall.side_effect = [
            [],  # existing-ids check: 'rec-1' not found in DB
            [('rec-1', 'devices', {'name': 'A'}, now),
             ('api-generated123', 'devices', {'name': 'B'}, now)],  # RETURNING rows
        ]
        records = [
            {'id': 'rec-1', 'name': 'A'},
            {'name': 'B'},
        ]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['created'] == 2
        assert body['failed'] == 0
        assert len(body['data']) == 2
        assert body['data'][0]['id'] == 'rec-1'
        assert body['data'][0]['name'] == 'A'
        assert 'errors' not in body

    def test_continue_on_error_false_aborts_whole_batch(self, setup):
        client, mock_cursor, api_h = setup
        fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
            (fields,),
            ([],),
        ])
        mock_cursor.fetchall.side_effect = [
            [('rec-1',)],  # existing-ids check: rec-1 already exists
        ]
        records = [
            {'id': 'rec-1', 'name': 'A'},
            {'name': 'B'},
        ]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['failed'] == 1
        assert body['errors'][0]['index'] == 0
        assert body['errors'][0]['error'] == 'Record ID already exists'
        # Nothing should have been inserted
        insert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if c.args and 'INSERT INTO dynamic_data' in str(c.args[0])
        ]
        assert len(insert_calls) == 0

    def test_continue_on_error_true_partial_success(self, setup):
        client, mock_cursor, api_h = setup
        fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
            (fields,),
            ([],),
        ])
        mock_cursor.fetchall.side_effect = [
            [('rec-1',)],  # existing-ids check: rec-1 already exists
            [('api-generated456', 'devices', {'name': 'B'}, now)],  # RETURNING (only the successful one)
        ]
        records = [
            {'id': 'rec-1', 'name': 'A'},  # will fail: already exists
            {'name': 'B'},                 # will succeed
        ]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records, 'options': {'continueOnError': True}}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['created'] == 1
        assert body['failed'] == 1
        assert body['errors'][0]['index'] == 0
        assert len(body['data']) == 1
        assert body['data'][0]['name'] == 'B'

    def test_duplicate_id_within_batch(self, setup):
        client, mock_cursor, api_h = setup
        fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
            (fields,),
            ([],),
        ])
        mock_cursor.fetchall.side_effect = [
            [],  # existing-ids check: 'dup-1' not found in DB
        ]
        records = [
            {'id': 'dup-1', 'name': 'A'},
            {'id': 'dup-1', 'name': 'B'},
        ]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['failed'] == 2
        assert all(e['error'] == 'Duplicate ID in batch' for e in body['errors'])

    def test_primary_key_conflict(self, setup):
        client, mock_cursor, api_h = setup
        fields = [
            {'fieldName': 'sku', 'label': 'SKU', 'controlType': 'text', 'required': True, 'isPrimaryKey': True},
        ]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),           # writable
            (fields,),              # get_page_fields
            (fields,),              # get_primary_key_fields -> pk_fields = ['sku']
            ('existing-rec-id',),   # check_primary_key_unique -> conflict found
        ])
        records = [{'sku': 'SKU-001'}]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['failed'] == 1
        assert 'Primary key conflict' in body['errors'][0]['error']
```

Note: `test_primary_key_conflict` 不需要设置 `mock_cursor.fetchall.side_effect`——该测试的唯一记录没有显式 `id`，所以 `all_ids` 为空，第 5 步的批量已存在 ID 查询（`if all_ids:`）不会执行，不会调用 `fetchall`。

- [ ] **Step 3: 运行测试**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_open_api.py -v
```

Expected: 全部通过，新增的 7 个 `TestBatchCreateRecords` 用例 + 既有用例都是 PASS，输出干净无警告。

- [ ] **Step 4: Commit**

```bash
git add server/routes/open_api.py server/tests/test_routes_open_api.py
git commit -m "feat(open-api): add POST /collections/<collection>/batch for batch record creation"
```

---

### Task 2: Python 客户端

**Files:**
- Modify: `python-client/checkmanage_openapi/client.py`
- Test: `python-client/tests/test_client.py`

**Interfaces:**
- Consumes: Task 1 的 `POST /api/v1/collections/<collection>/batch`。
- Produces: `OpenApiClient.batch_create_records(collection, records, *, branch_id="main", continue_on_error=False) -> dict`。

- [ ] **Step 1: 新增 `batch_create_records` 方法**

在 `python-client/checkmanage_openapi/client.py` 里，紧跟在 `create_record` 方法（第 196-200 行）之后插入：

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
        任何记录，整个调用抛 ApiError 子类异常；True 时跳过失败记录，成功的
        会写入，返回值里的 failed/errors 描述哪些没写进去。
        """
        resp = self._request(
            "POST",
            f"/collections/{collection}/batch",
            json_body={"records": records, "options": {"continueOnError": continue_on_error}},
            params={"branchId": branch_id},
        )
        return resp.json()
```

- [ ] **Step 2: 编写测试**

在 `python-client/tests/test_client.py` 的 `test_create_record_sends_json_body`（第 119-126 行）之后插入：

```python
def test_batch_create_records_sends_records_and_options():
    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert url == "http://x/api/v1/collections/devices/batch"
        assert kwargs["json"] == {
            "records": [{"name": "a"}, {"name": "b"}],
            "options": {"continueOnError": True},
        }
        return FakeResponse(201, {"data": [{"id": "api-1", "name": "a"}], "created": 1, "failed": 1,
                                   "errors": [{"index": 1, "error": "..."}]})

    client, _ = make_client(fake_request)
    result = client.batch_create_records(
        "devices", [{"name": "a"}, {"name": "b"}], continue_on_error=True
    )
    assert result["created"] == 1
    assert result["failed"] == 1


def test_batch_create_records_default_continue_on_error_is_false():
    def fake_request(method, url, **kwargs):
        assert kwargs["json"]["options"] == {"continueOnError": False}
        return FakeResponse(201, {"data": [{"id": "api-1"}], "created": 1, "failed": 0})

    client, _ = make_client(fake_request)
    client.batch_create_records("devices", [{"name": "a"}])


def test_batch_create_records_raises_on_400():
    def fake_request(method, url, **kwargs):
        return FakeResponse(400, {"error": "Validation failed for one or more records",
                                   "failed": 1, "errors": [{"index": 0, "error": "..."}]})

    client, _ = make_client(fake_request)
    with pytest.raises(ValidationError):
        client.batch_create_records("devices", [{"name": "a"}])
```

Note: `test_batch_create_records_raises_on_400` 用的 `ValidationError` 已经在文件顶部的 import 列表里（第 6-13 行），不需要新增 import。（已确认 `client.py` 的 `_raise_for_error` 里 400 → `ValidationError`，见该函数第 123-124 行。）

- [ ] **Step 3: 运行测试**

```bash
cd python-client && python -m pytest tests/test_client.py -v
```

Expected: 全部通过，新增的 3 个用例 + 既有用例都是 PASS。

- [ ] **Step 4: Commit**

```bash
git add python-client/checkmanage_openapi/client.py python-client/tests/test_client.py
git commit -m "feat(python-client): add batch_create_records method"
```

---

### Task 3: 文档同步

**Files:**
- Modify: `docs/user-guide/integration/open-api.md`
- Modify: `docs/user-guide/integration/open-api-reference.html`

**Interfaces:**
- Consumes: 无（纯文档，依赖 Task 1-2 已完成）。

- [ ] **Step 1: `open-api.md` 插入新章节 + 重排后续编号**

在第 524 行（`---`，"5.6 新增记录"小节的结束分隔线）之后、第 526 行 `### 5.7 修改记录` 之前插入新的 "5.7 批量新增记录" 小节：

```markdown
### 5.7 批量新增记录

一次请求创建多条记录（单次最多 1000 条）。语义上是 5.6 新增记录的批量版本：**create-only，不做 upsert**——批次里任何一条 `id` 已存在（批次内部重复，或数据库里已存在）都算该条失败，不会更新已有记录。

**请求**

```
POST /api/v1/collections/{collection}/batch?branchId=main
Content-Type: application/json
```

**请求体**

```json
{
  "records": [
    { "id": "prod-001", "name": "产品A", "price": 99 },
    { "name": "产品B", "price": 199 }
  ],
  "options": { "continueOnError": true }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `records` | array | 是 | 待创建的记录数组，单个元素的形状与 5.6 新增记录的请求体完全一致；单次最多 1000 条 |
| `options.continueOnError` | boolean | 否，默认 `false` | `false`：只要有一条记录校验失败，整批返回 400，不写入任何记录；`true`：跳过失败记录，成功的都写入 |

**响应 — 201（全部成功）**

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

**响应 — 201（`continueOnError=true` 且部分失败：`data` 只包含成功创建的记录）**

```json
{
  "data": [ { "id": "api-a1b2c3d4e5f6", "name": "产品B", "price": 199, "createdAt": "...", "branchId": "main" } ],
  "created": 1,
  "failed": 1,
  "errors": [ { "index": 0, "error": "Record ID already exists", "record": { "id": "prod-001", "name": "产品A", "price": 99 } } ]
}
```

**响应 — 400（`continueOnError=false` 且存在失败记录：不写入任何记录）**

```json
{
  "error": "Validation failed for one or more records",
  "failed": 1,
  "errors": [ { "index": 0, "error": "Record ID already exists", "record": { "id": "prod-001", "name": "产品A", "price": 99 } } ]
}
```

**错误响应**

| HTTP 状态码 | error | 触发条件 |
|-------------|-------|---------|
| 400 | `Request body is required` | 请求体为空 |
| 400 | `records is required` | `records` 为空数组或缺失 |
| 400 | `Batch size exceeds maximum of 1000 records` | 单次请求记录数超过 1000 |
| 400 | `Validation failed for one or more records` | `continueOnError=false` 且至少一条记录校验失败（`errors` 数组列出每条失败记录的 `index`/`error`/`record`） |
| 403 | `Collection is read-only` | 集合未开启「允许写入」 |
| 404 | `Collection not found or not public` | 集合不存在或未开放 API 访问 |
| 404 | `Branch not found or not active` | 指定的分支不存在或非活跃状态 |

---

```

**然后**把原第 526 行开始的 `### 5.7 修改记录` 改为 `### 5.8 修改记录`，其后续的 "5.8 上传文件" 改为 "5.9 上传文件"，"5.9 下载文件" 改为 "5.10 下载文件"（三处标题文字里的编号各加一）。

**再**把第 645 行 `> - 文件被某条记录引用后，即可经 \`GET /api/v1/files/{uid}/download\` 下载（见 5.9）。` 里的 "见 5.9" 改为 "见 5.10"（这是全文唯一一处引用了被顺延编号的地方；第 490 行 "详见 5.6 新增记录" 不用改，因为 5.6 本身没有移动）。

- [ ] **Step 2: `open-api-reference.html` 插入新卡片 + 重排后续 eyebrow 编号 + 加导航链接**

在第 1075 行 `</section>`（`id="ep-create"` 卡片的结束标签）之后、第 1077 行 `<section id="ep-update">` 之前插入新卡片：

```html
    <section id="ep-batch-create">
      <p class="eyebrow">5.7</p>
      <h2>批量新增记录</h2>
      <div class="endpoint">
        <div class="endpoint-head">
          <span class="m m-post">POST</span>
          <span class="path">/api/v1/collections/{collection}/batch</span>
          <span class="flags"><span class="flag write">需允许写入</span></span>
        </div>
        <div class="endpoint-body">
          <p>一次请求创建多条记录（单次最多 1000 条）。<strong>create-only，不做 upsert</strong>——批次里任何一条 <code>id</code> 已存在都算该条失败，不会更新已有记录。</p>
          <div class="code">
            <div class="code-bar"><span class="dots"><span></span><span></span><span></span></span>request body</div>
            <pre><code>{
  <span class="k">"records"</span>: [
    { <span class="k">"id"</span>: <span class="s">"prod-001"</span>, <span class="k">"name"</span>: <span class="s">"产品A"</span> },
    { <span class="k">"name"</span>: <span class="s">"产品B"</span> }
  ],
  <span class="k">"options"</span>: { <span class="k">"continueOnError"</span>: <span class="s">true</span> }
}</code></pre>
          </div>
          <div class="code">
            <div class="code-bar"><span class="dots"><span></span><span></span><span></span></span>response · 201</div>
            <pre><code>{
  <span class="k">"data"</span>: [ <span class="k">"id"</span>: <span class="s">"prod-001"</span>, ... ],
  <span class="k">"created"</span>: <span class="n">2</span>,
  <span class="k">"failed"</span>: <span class="n">0</span>
}</code></pre>
          </div>
          <p style="font-size: 13px; margin-top: -8px;"><code>options.continueOnError</code> 默认 <code>false</code>：任何一条记录失败就整批 400、不写入；传 <code>true</code> 则跳过失败记录，成功的照常写入，响应体 <code>errors</code> 数组列出失败详情。</p>
          <table class="params">
            <tr><th>状态码</th><th>error</th><th>触发条件</th></tr>
            <tr><td>400</td><td><code>records is required</code></td><td><code>records</code> 为空数组或缺失</td></tr>
            <tr><td>400</td><td><code>Batch size exceeds maximum of 1000 records</code></td><td>单次请求记录数超过 1000</td></tr>
            <tr><td>400</td><td><code>Validation failed for one or more records</code></td><td><code>continueOnError=false</code> 且至少一条记录校验失败</td></tr>
            <tr><td>403</td><td><code>Collection is read-only</code></td><td>集合未开启「允许写入」</td></tr>
          </table>
        </div>
      </div>
    </section>
```

**然后**把三处已有卡片的 eyebrow 编号各顺延一位：
- `id="ep-update"` 卡片（第 1078 行）：`<p class="eyebrow">5.7</p>` → `<p class="eyebrow">5.8</p>`
- `id="files"` 卡片（第 1103 行）：`<p class="eyebrow">5.8 · 深入讲解</p>` → `<p class="eyebrow">5.9 · 深入讲解</p>`
- `id="ep-download"` 卡片（第 1297 行）：`<p class="eyebrow">5.9</p>` → `<p class="eyebrow">5.10</p>`

**最后**在导航栏里加对应链接：第 757 行 `<a href="#ep-create">新增记录</a>` 之后、第 758 行 `<a href="#ep-update">修改记录</a>` 之前插入：

```html
      <a href="#ep-batch-create">批量新增记录</a>
```

- [ ] **Step 3: Commit**

```bash
git add docs/user-guide/integration/open-api.md docs/user-guide/integration/open-api-reference.html
git commit -m "docs(open-api): document the batch-create endpoint"
```

## Self-Review Notes

- **Spec coverage**：后端端点+测试（Task 1）、Python 客户端+测试（Task 2）、Markdown 文档+HTML 参考文档（Task 3）——设计文档的每一节都对应到了具体任务。
- **Placeholder scan**：无 TBD/TODO，所有代码块均为完整可运行代码，测试均含真实断言，mock 的 `fetchone`/`fetchall` 调用序列已经按实现逻辑精确推演过。
- **Type consistency**：`create_batch_items` 的响应字段名（`data`/`created`/`failed`/`errors`）在 Task 1 实现、Task 1 测试断言、Task 2 客户端方法文档字符串、Task 3 文档示例四处保持一致；`options.continueOnError` 的默认值（`false`）在四处也保持一致。
