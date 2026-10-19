# ETL 异步执行 + 进度可见 + 可取消 — 设计

## 背景

ETL 任务（`server/utils/etl_engine.py` + `server/routes/etl_tasks.py`）执行大文件时会把整个系统拖卡死。根因排查（已与用户确认）：

1. **文件读取一次性**：`_step_file_upload` 用 `pd.read_csv`/`pd.read_excel` 把整个文件一次性读进内存，没有分片、没有大小限制。
2. **写库逐条不分批**：`_step_save_to_collection` 对每条记录单独发一次 `INSERT`（`upsert`/`update` 模式下还要先单独 `SELECT`），是这个项目里唯一还在"一条条查库"的写入路径——对比 `routes/dynamic.py::batch_create_items` 用 `execute_values` 批量写、前端 `importPageRecords.ts` 按 1000 条分批。
3. **整个执行是一次同步阻塞的 HTTP 请求**：`routes/etl_tasks.py::run_task` 拿一个数据库连接跑完全部步骤才返回，可能是几万到几十万次数据库往返，中途没有进度、无法取消。前端请求 30 秒超时后用户会觉得"卡住了"，但后端并不知道客户端已放弃，会继续跑到底，占着连接池（`maxconn=20`）里的一个连接。如果这时用户以为卡住又重试，多个这种长跑请求叠加会把连接池耗尽，导致全系统所有需要数据库的请求都跟着排队卡死。
4. **没有调试能力是同一个架构缺口的另一面**：整个执行是不透明黑盒，`step_results` 只有跑完全部步骤才返回；设计管道时没有办法只用少量样本快速看到每一步的中间数据；卡住了也没有任何取消手段。

## 目标（已与用户确认）

1. 正式运行（非 dry run）**始终改为异步**：不管任务大小，点"执行"立即返回一个运行 ID，前端轮询进度，不再依赖一次性同步 HTTP 请求。
2. 后台执行引擎**单线程串行**：同一时刻只跑一个 ETL 任务，多个运行请求 FIFO 排队。ETL 是管理员偶尔手动触发的重活任务，不需要像 AI 批量任务那样高并发；单线程实现简单、不占用多个数据库连接、风险最小。
3. **分批写库**：`save_to_collection` 改用批量 SQL（`execute_values`，复用项目已有的 `BATCH_SIZE=1000` 惯例），每批写完立即 commit 并更新进度。
4. **实时进度可见**：运行中能看到"处理到第几条 / 当前在哪一步"。
5. **可取消**：卡住或跑得太久的任务可以主动取消；已经成功写入的批次保留（不整体回滚）——这是与用户确认过的行为：取消/崩溃时保留已写数据，而不是 all-or-nothing 回滚。
6. **Dry Run 改为取样本运行**（如前 50 条，`sampleSize` 可配置），仍然同步立即返回：文件读取本身就用 `nrows=` 限量读，不是读全量再截断。每一步的结果附带最多 5 条中间数据样例（`sampleRecords`），用于设计管道时调试。

## 数据模型改动（`server/init_db.py`）

扩展现有 `etl_logs` 表（不新建表，它已经有 `status`/`step_results`/计数字段）：

```sql
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS progress_current INTEGER DEFAULT 0;
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS current_step_name VARCHAR(200);
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_etl_logs_status_pending
  ON etl_logs(created_at) WHERE status = 'pending';
```

- `progress_current`：`save_to_collection` 每写完一批就累加更新，供轮询读取。
- `current_step_name`：每步开始前更新，供"哪一步"展示。
- `cancel_requested`：由取消接口置位；worker 在批次/步骤之间检查。
- `status` 新增真实的 `pending`/`running`/`cancelled` 语义（今天只在跑完之后一次性写入终态 `success`/`partial`/`error`；现在跑之前就先插入一行 `pending`，随执行推进真实流转状态）。
- `idx_etl_logs_status_pending`：partial index，仿照 `idx_dynamic_data_search_text_pending` 已有模式，让 worker 的认领查询不随历史数据增长变慢。

**行为变化（已与用户确认）**：今天整个任务是一个大事务，只在跑完最后统一 commit——中途崩溃会丢失全部进度。改为分批 commit 后，**取消或崩溃时已经成功写入的批次会保留**，不再是 all-or-nothing。这对"大任务被迫中断不用整个重跑"是更好的行为，但和今天不同，需要明确记录在案。

## 执行引擎改造（`server/utils/etl_engine.py`）

### `_step_file_upload`：新增 `sample_limit` 可选参数

```python
def _step_file_upload(config, context, conn, sample_limit=None):
    ...
    read_kwargs = {'nrows': sample_limit} if sample_limit else {}
    if ext == 'csv':
        df = pd.read_csv(storage_path, **read_kwargs)
    elif ext in ('xlsx', 'xls'):
        df = pd.read_excel(storage_path, **read_kwargs)
    ...
```

传了 `sample_limit` 时只读文件的前 N 行，不是读全量再截断——这是 dry run 能秒出结果的关键。

### `_step_save_to_collection`：分批 + 增量 commit + 进度回调 + 取消检查

`etl_engine.py` 里新增一个模块级常量 `SAVE_BATCH_SIZE = 1000`（大小上参照前端 `importPageRecords.ts` 的 `BATCH_SIZE` 惯例，但这是后端这个文件里全新的常量，不是复用某个已有的共享常量）：

```python
SAVE_BATCH_SIZE = 1000

def _step_save_to_collection(config, context, conn, dry_run, progress_cb=None, cancel_check=None):
    collection = config.get('collection', '')
    mode = config.get('mode', 'insert')
    match_field = config.get('matchField', '')
    if not collection:
        raise ValueError('目标集合不能为空')

    records = context['records']
    if not records:
        return

    cur = conn.cursor()
    now = datetime.now(timezone.utc)
    success = 0
    errors = 0
    cancelled = False

    for batch_start in range(0, len(records), SAVE_BATCH_SIZE):
        batch = records[batch_start:batch_start + SAVE_BATCH_SIZE]

        if dry_run:
            success += len(batch)
        else:
            batch_success, batch_errors = _write_batch(cur, collection, mode, match_field, batch, now, context)
            success += batch_success
            errors += batch_errors
            conn.commit()

        if progress_cb:
            progress_cb(success + errors, len(records))
        if cancel_check and cancel_check():
            cancelled = True
            break

    if not dry_run and success > 0:
        from utils.sequences import reseed_sequences
        reseed_sequences(cur, collections=[collection], branch_id='main')
        conn.commit()

    context['total'] = len(records)
    context['success'] = success
    context['error'] = errors
    context['cancelled'] = cancelled
```

`_write_batch` 是从原来逐条循环里抽出来的批量版本：

- `insert` 模式：一次 `execute_values` 批量 INSERT。
- `upsert` 模式：先一次 `SELECT id FROM dynamic_data WHERE collection = %s AND data->>%s = ANY(%s)` 批量查出已存在的匹配值 → 拆成"已存在的走批量 UPDATE（逐条，因为 UPDATE 的 SET 值不同，无法用单条 `execute_values` 语句一次性表达多行不同值——保留原来的逐条 UPDATE，但只对**这一批里真正命中已存在记录**的子集执行，不是对全部记录）"和"不存在的走批量 INSERT"两部分。
- `update` 模式：同样先批量 SELECT 找出匹配的 id，命中的走逐条 UPDATE（原因同上），未命中的计入 `errors`。

这里如实说明：**`upsert`/`update` 模式对已存在记录的更新仍然是逐条 SQL**，因为一条 `UPDATE ... SET data = %s WHERE id = %s` 里 `data` 的值每条都不同，标准 SQL 没有"批量 UPDATE 多行到各自不同的值"的单语句写法（不使用 `UPDATE ... FROM (VALUES ...)` 这类更复杂的写法）。但这仍然是本次改造的净收益：(a) 批量 SELECT 把"每条记录一次匹配查询"变成"每批一次匹配查询"，去掉了原来最大头的逐条 SELECT；(b) `insert` 模式（没有已存在记录需要更新的场景）完全批量化；(c) 即使 upsert/update 命中量大，也从"一条记录 2 次同步网络往返"降到了"一条记录 1 次"，且每批仍然是增量 commit + 进度可见，不再是不透明的整体阻塞。

### 其它步骤：进度检查点，不改内部逻辑

`http_request`/`json_input`/`script`/`field_mapping`/`filter` 本身是纯内存操作，不是卡死根因，内部实现不变。`execute_task` 主循环在每步开始前更新 `current_step_name`、检查一次 `cancel_requested`（如果取消，直接停止，不再执行后续步骤）。

### `step_results` 增加 `sampleRecords`

每步执行完，`context['step_results']` 里除了现有的 `stepId`/`stepName`/`status`/`recordCount`，追加：

```python
'sampleRecords': context['records'][:5]
```

dry run 和正式运行都带（正式运行时开销可忽略，只取前 5 条）。这是"设计管道时看每一步中间数据"的落地点。

### `execute_task` 签名扩展

```python
def execute_task(task_dict, conn, dry_run=False, sample_limit=None,
                  progress_cb=None, cancel_check=None):
```

- `sample_limit`：dry run 用，透传给 `_step_file_upload`，并在每步执行后把 `context['records']` 截断到前 `sample_limit` 条（对 `http_request`/`json_input` 这类不吃 `sample_limit` 参数的步骤也统一生效，保证 script/filter 等后续步骤不会在大数据量上跑）。
- `progress_cb(current, total)` / `cancel_check() -> bool`：正式异步运行时由调度器传入，透传给 `_step_save_to_collection`；dry run 不传（`None`）。

## 后台调度器（新文件 `server/utils/etl_scheduler.py`）

单线程，结构照抄项目里已有 scheduler 的惯例（对比 `ai_scan_scheduler.py`）：

```python
import threading, time, logging
from db import get_db, pool

logger = logging.getLogger(__name__)
POLL_INTERVAL_SEC = 2

class EtlScheduler:
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._restart_audit()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='etl-scheduler')
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _restart_audit(self):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE etl_logs SET status = 'pending' WHERE status = 'running'")
            conn.commit()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception('etl scheduler tick failed; will retry')
            self._stop.wait(POLL_INTERVAL_SEC)

    def _tick(self):
        log_row = self._claim_one()
        if not log_row:
            return
        self._run(log_row)

    def _claim_one(self):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "WITH picked AS ("
                    "  SELECT id FROM etl_logs WHERE status = 'pending'"
                    "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                    ") "
                    "UPDATE etl_logs l SET status = 'running' "
                    "FROM picked WHERE l.id = picked.id "
                    "RETURNING l.id, l.task_id"
                )
                row = cur.fetchone()
            conn.commit()
        return row

    def _run(self, log_row):
        log_id, task_id = log_row
        exec_conn = pool.getconn()
        try:
            # 取 task 定义、调用 execute_task(..., progress_cb=..., cancel_check=...)
            # 结束后写 status/finished_at/step_results/error_detail，
            # 并 UPDATE etl_tasks SET last_run_at/last_run_status（照抄现有 run_task 尾部逻辑）
            ...
        finally:
            pool.putconn(exec_conn)


_SCHEDULER = None

def start_etl_scheduler(app):
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = EtlScheduler()
    _SCHEDULER.start()
```

- `start_etl_scheduler(app)` 在 `app.py` 里和其它 scheduler 并列启动，`WERKZEUG_RUN_MAIN` 门禁避免 Flask reloader 下重复启动（照抄现有四个 scheduler 的启动方式）。
- `_restart_audit`：进程重启时把遗留的 `running` 行重置回 `pending`，避免 Flask 重启后一个任务永远卡在 running（照抄 `batch_engine.py::_restart_audit` 的模式）。
- `progress_cb`：更新 `etl_logs.progress_current`/`total_records`（用独立的短事务 `UPDATE ... WHERE id = %s; COMMIT` 更新，不占用 `execute_task` 的主连接事务，避免进度更新失败影响正式写入）。
- `cancel_check`：读一次 `SELECT cancel_requested FROM etl_logs WHERE id = %s`。

## API 接口变化（`server/routes/etl_tasks.py`）

### `POST /etlTasks/<task_id>/run`

- `dryRun` 为空或 `false`（正式运行）：不再同步执行。插入一条 `etl_logs` 行（`status='pending'`），立即返回：
  ```json
  { "logId": "etl-log-xxxx", "status": "pending" }
  ```
  真正执行交给后台调度器。
- `dryRun: true`：保持同步、立即返回，不落库任何 `etl_logs` 行（和今天一致）。请求体新增可选 `sampleSize`（默认 50），透传给 `execute_task` 的 `sample_limit`。响应结构不变，`stepResults` 每项多了 `sampleRecords`。

### 新增 `GET /etlTasks/<task_id>/logs/<log_id>`

单条日志详情：

```json
{
  "id": "etl-log-xxxx",
  "status": "running",
  "progressCurrent": 12000,
  "totalRecords": 50000,
  "currentStepName": "写入集合",
  "stepResults": [...],
  "startedAt": "...",
  "finishedAt": null
}
```

供前端轮询用，避免每次轮询都拉最近 20 条列表。

### 新增 `POST /etlTasks/<task_id>/logs/<log_id>/cancel`

置 `cancel_requested = true`。仅对 `status IN ('pending', 'running')` 的行生效；已终止的行（`success`/`partial`/`error`/`cancelled`）返回 `409`，提示"任务已结束，无法取消"。

### 现有 `GET /etlTasks/<task_id>/logs`（列表，最近 20 条）

不变，`pending`/`running` 中的行自然也会出现在列表里（它们现在从一开始就是真实的 `etl_logs` 行，不是跑完才追加）。

## 前端改造

### `src/api/etl.ts`

```typescript
export function getEtlLog(taskId: string, logId: string) {
  return get<EtlLog>(`/etlTasks/${taskId}/logs/${logId}`)
}

export function cancelEtlRun(taskId: string, logId: string) {
  return post(`/etlTasks/${taskId}/logs/${logId}/cancel`, {})
}

export function runEtlTask(id: string, options: { dryRun?: boolean; sampleSize?: number } = {}) {
  return post<EtlRunResult>(`/etlTasks/${id}/run`, {
    dryRun: options.dryRun || false,
    sampleSize: options.sampleSize,
  })
}
```

`EtlLog` 类型（`src/types`）新增 `progressCurrent`/`currentStepName`；`status` 联合类型新增 `'pending' | 'running' | 'cancelled'`；`EtlRunResult`/日志的 `stepResults` 每项新增 `sampleRecords?: Record<string, any>[]`。

### `src/views/admin/EtlTaskManager.vue`

- **正式运行**（`doRun(false)`）：`runEtlTask` 拿到 `{ logId }` 后不再直接展示"完成"结果，而是启动一个轮询定时器（间隔约 1.5 秒）调用 `getEtlLog`，把 `progressCurrent`/`totalRecords`/`currentStepName` 实时渲染成进度条 + 文案（"正在执行「写入集合」... 已处理 12000 / 50000 条"）。运行按钮旁新增"取消"按钮（`runLoading` 为真时可点），调用 `cancelEtlRun`；轮询到终态（`success`/`partial`/`error`/`cancelled`）后停止轮询、展示最终结果、刷新日志列表（沿用现有 `loadLogs`/`loadTasks`）。
- **试运行**（`doRun(true)`）：保持同步调用不变，只是结果面板每一步旁新增一个可展开区域，用 `el-table` 展示该步 `sampleRecords`（复用现有 JSON/表格展示组件风格）。
- 日志列表：`pending`/`running`/`cancelled` 三个新状态复用现有 status badge 风格，各配一个颜色/文案。

## 测试

**`server/tests/test_etl_engine.py`**（现有文件，扩充）：
- `_step_file_upload` 传 `sample_limit` 时只读文件前 N 行（构造一个超过 N 行的临时 CSV，断言返回记录数等于 N，且断言底层没有读取超过 N 行——可以通过 mock `pd.read_csv`/`pd.read_excel` 断言调用参数里 `nrows=N`）。
- `_step_save_to_collection`：
  - insert 模式下批量写入等价于原来逐条写入的最终数据库状态（同一组记录，断言写入结果一致）。
  - `progress_cb` 按预期的批次数被调用，每次参数单调递增到总数。
  - `cancel_check` 在某一批后返回 `True` 时，后续批次不再执行，`context['cancelled']` 为真，已完成批次的数据仍然落库（用真实的测试数据库连接断言，不能只 mock）。
  - upsert/update 模式：批量 SELECT 正确识别已存在/不存在的记录，各自走批量 INSERT / 逐条 UPDATE，最终数据库状态与原来逐条实现等价。
- `step_results` 里 `sampleRecords` 长度不超过 5，即使总记录数远大于 5。

**`server/tests/test_etl_scheduler.py`**（新文件）：
- 认领：一条 `pending` 记录被认领后立即变为 `running`；`FOR UPDATE SKIP LOCKED` 语义下并发认领不会拿到同一行（可以用两个连接模拟）。
- 孤儿恢复：启动时把已有的 `running` 行重置为 `pending`。
- 端到端：插入一条小任务的 `pending` 日志，启动 scheduler，轮询直到该行变为终态，断言 `progress_current`/`current_step_name`/最终 `status` 符合预期。

**`server/tests/test_routes_etl.py`**（现有文件，扩充）：
- 正式运行请求立即返回 `{ logId, status: 'pending' }`，不再同步返回最终结果。
- `dryRun` 请求仍同步返回，`sampleSize` 生效（读取的记录数不超过 `sampleSize`），响应里 `stepResults[].sampleRecords` 存在。
- `GET .../logs/<log_id>` 返回单条详情，字段齐全。
- `POST .../logs/<log_id>/cancel`：对 `pending`/`running` 的行返回成功并置位 `cancel_requested`；对已终止的行返回 `409`。

**前端**：
- `src/api/__tests__/etl.test.ts`（现有文件，扩充）：新增函数的请求 URL/参数断言。
- `EtlTaskManager.vue` 走 Playwright 验证（CLAUDE.md 强制要求）：真实跑一个足够大的样例文件（几千到几万行，能在测试环境里体现"分批+可见进度"但不用真的几十万行），确认进度条随执行更新、取消按钮生效、试运行的样例数据面板正确展示。

## 非目标

- `script`/`filter` 步骤本身的分批/流式处理（它们是纯内存操作，不是卡死根因，本次不改）。
- `http_request`/`json_input` 步骤的分批（数据量通常不大，不是卡死根因）。
- 多任务并发执行（已确认单线程串行，不做线程池）。
- 取消后的自动回滚（已确认取消保留已写数据，不做回滚机制）。
- Excel 文件的真正流式读取（`pd.read_excel` 不支持逐行流式；`sample_limit`/`nrows` 只解决 dry run 场景，正式运行大 Excel 文件仍然是整体读入内存后再分批写库——如果这一步后续仍是瓶颈，需要另外的设计，本次不处理）。
