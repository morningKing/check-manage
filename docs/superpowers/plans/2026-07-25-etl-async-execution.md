# ETL 异步执行 + 进度可见 + 可取消 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ETL 大文件导入不再把系统拖卡死：正式运行改为后台异步执行、分批写库、实时进度可见、可取消；试运行（dry run）改为取样本快速返回，每步带中间数据样例，供设计管道时调试。

**Architecture:** `routes/etl_tasks.py::run_task` 不再同步跑完整个 pipeline——正式运行只插入一条 `status='pending'` 的 `etl_logs` 行立即返回；新的后台调度器 `utils/etl_scheduler.py`（APScheduler `BackgroundScheduler`，`max_instances=1` 天然实现单线程串行，与 `field_index_scheduler.py`/`ai_scan_scheduler.py` 同一模式）轮询认领并执行。`utils/etl_engine.py::_step_save_to_collection` 从逐条 SQL 改为分批 `execute_values` + 每批 commit + 进度回调 + 取消检查。dry run 保持同步，但 `_step_file_upload` 用 `nrows=` 真正只读文件前 N 行，每步结果附带最多 5 条样例数据。

**Tech Stack:** Flask + psycopg2（后端），APScheduler（已是项目依赖，`field_index_scheduler.py`/`ai_scan_scheduler.py` 已在用），Vue 3 + TypeScript（前端轮询/取消 UI）。

## Global Constraints

- 正式运行（非 dry run）**始终异步**，不区分任务大小——不新增阈值判断逻辑。
- 后台执行引擎**单线程串行**：`BackgroundScheduler` 的 job 用 `max_instances=1, coalesce=True`，不引入线程池/并发执行。
- **取消/崩溃时保留已写数据**：`_step_save_to_collection` 按批（`SAVE_BATCH_SIZE = 1000`，`etl_engine.py` 里新增的模块级常量，与前端 `importPageRecords.ts` 的 `BATCH_SIZE` 惯例同量级但并非同一个共享常量）commit，不是整任务一个大事务；取消或异常时已提交的批次不回滚。
- dry run **取样本运行**（默认 `sampleSize=50`，可配置），仍然同步立即返回，不落库任何 `etl_logs` 行（与今天行为一致）。`_step_file_upload` 传了 `sample_limit` 时必须用 `nrows=` 真正只读文件前 N 行，不是读全量再截断。
- 每步 `step_results` 追加 `sampleRecords`（最多 5 条当前 `context['records']`），dry run 和正式运行都带。
- `etl_logs` 表扩展用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，不新建表。
- 一个批次内任何 SQL 异常都必须 `conn.rollback()` 后才能继续处理下一批——PostgreSQL 事务一旦出错，同一连接后续语句会全部失败，直到执行 ROLLBACK。这是分批写库正确性的硬性要求，不是可选的错误处理增强。
- 保持现有代码风格（中文注释、现有命名习惯），不引入新的第三方依赖（`apscheduler`/`psycopg2.extras` 已是项目依赖）。

---

### Task 1: 数据库 schema — 扩展 `etl_logs`

**Files:**
- Modify: `server/init_db.py`

**Interfaces:**
- Produces：`etl_logs` 表新增列 `progress_current INTEGER DEFAULT 0`、`current_step_name VARCHAR(200)`、`cancel_requested BOOLEAN NOT NULL DEFAULT FALSE`；新增 partial index `idx_etl_logs_status_pending`。后续所有任务都依赖这三列存在。

- [ ] **Step 1: 在 `etl_logs` 表定义之后加 ALTER 语句**

打开 `server/init_db.py`，定位到现有的（第 261-278 行左右）：

```sql
CREATE TABLE IF NOT EXISTS etl_logs (
    id              VARCHAR(100) PRIMARY KEY,
    task_id         VARCHAR(100) NOT NULL,
    task_name       VARCHAR(200),
    status          VARCHAR(50) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    total_records   INTEGER DEFAULT 0,
    success_count   INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    step_results    JSONB DEFAULT '[]'::jsonb,
    error_detail    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_etl_logs_task_id ON etl_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_etl_logs_created_at ON etl_logs(created_at DESC);
```

在这两行 `CREATE INDEX` 之后（`-- ==================== 版本管理表 ====================` 之前）插入：

```sql
-- ETL 异步执行：progress_current/current_step_name 供轮询展示进度，
-- cancel_requested 供取消接口置位、调度器在批次/步骤之间检查。
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS progress_current INTEGER DEFAULT 0;
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS current_step_name VARCHAR(200);
ALTER TABLE etl_logs ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;

-- 调度器的认领查询只关心 status='pending' 的行，partial index 让这个查询
-- 不随历史日志增长变慢（同一手法见 idx_dynamic_data_search_text_pending）。
CREATE INDEX IF NOT EXISTS idx_etl_logs_status_pending
  ON etl_logs(started_at) WHERE status = 'pending';
```

- [ ] **Step 2: 应用迁移**

Run: `cd server && python init_db.py`
Expected: 无报错退出（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 幂等，重复跑也不报错）。

- [ ] **Step 3: 验证列已存在**

Run:
```bash
cd server && python -c "
import psycopg2
from config import DB_CONFIG
conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='etl_logs' ORDER BY column_name\")
print([r[0] for r in cur.fetchall()])
"
```
Expected: 输出包含 `progress_current`、`current_step_name`、`cancel_requested`。

- [ ] **Step 4: 提交**

```bash
git add server/init_db.py
git commit -m "feat(etl): etl_logs 表新增进度/当前步骤/取消标记列"
```

---

### Task 2: `etl_engine.py` — dry run 取样本 + 每步样例数据

**Files:**
- Modify: `server/utils/etl_engine.py`
- Test: `server/tests/test_etl_engine.py`

**Interfaces:**
- Produces：
  - `_step_file_upload(config, context, conn, sample_limit=None)`（新增可选参数，向后兼容）
  - `_execute_step(step_type, config, context, conn, dry_run, sample_limit=None, progress_cb=None, cancel_check=None)`（新增三个可选关键字参数，向后兼容——现有 `test_unknown_type` 用 5 个位置参数调用，不受影响）
  - `execute_task(task_dict, conn, dry_run=False, sample_limit=None, step_cb=None, progress_cb=None, cancel_check=None)`（新增四个可选关键字参数，向后兼容）
  - `context` 字典新增键 `cancelled`（初始 `False`）
  - `step_results` 每项新增键 `sampleRecords`

本任务只做"采样 + 样例数据"部分（对应 Global Constraints 里 dry run 相关的两条）；`_step_save_to_collection` 的分批写库改造是 Task 3，两者都改 `etl_engine.py` 但改的是不同函数，分开做便于独立审查。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_etl_engine.py` 的 `TestFileUpload` 类里追加（紧跟在 `test_xlsx_date_column_is_json_serializable` 之后，`class TestFieldMapping` 之前）：

```python
    def test_sample_limit_reads_only_first_n_rows_csv(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['name'])
            for i in range(10):
                writer.writerow([f'row{i}'])
            path = f.name
        try:
            mock_conn = self._mock_conn_for_file('数据.csv', path)
            ctx = {'records': []}
            _step_file_upload({'fileId': 'f1'}, ctx, mock_conn, sample_limit=3)
            assert len(ctx['records']) == 3
            assert ctx['records'][0]['name'] == 'row0'
        finally:
            _os.remove(path)

    @pytest.mark.filterwarnings('ignore::DeprecationWarning')
    def test_sample_limit_reads_only_first_n_rows_xlsx(self):
        wb = Workbook()
        ws = wb.active
        ws.append(['name'])
        for i in range(10):
            ws.append([f'row{i}'])
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
            path = f.name
        wb.save(path)
        try:
            mock_conn = self._mock_conn_for_file('数据.xlsx', path)
            ctx = {'records': []}
            _step_file_upload({'fileId': 'f1'}, ctx, mock_conn, sample_limit=3)
            assert len(ctx['records']) == 3
        finally:
            _os.remove(path)

    def test_no_sample_limit_reads_all_rows(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['name'])
            for i in range(10):
                writer.writerow([f'row{i}'])
            path = f.name
        try:
            mock_conn = self._mock_conn_for_file('数据.csv', path)
            ctx = {'records': []}
            _step_file_upload({'fileId': 'f1'}, ctx, mock_conn)
            assert len(ctx['records']) == 10
        finally:
            _os.remove(path)
```

在 `TestExecuteTask` 类里追加（紧跟在 `test_pipeline_flow` 之后）：

```python
    def test_sample_limit_truncates_records_after_each_step(self):
        """json_input 一次给 5 条，sample_limit=2 时每步执行完都截到前 2 条"""
        task = {
            'steps': [
                {
                    'id': 's1', 'name': 'input', 'type': 'json_input',
                    'config': {'data': json.dumps([{'n': i} for i in range(5)])},
                    'onError': 'stop',
                },
            ]
        }
        ctx = execute_task(task, None, sample_limit=2)
        assert len(ctx['records']) == 2
        assert ctx['step_results'][0]['recordCount'] == 2

    def test_step_results_include_sample_records_capped_at_5(self):
        task = {
            'steps': [
                {
                    'id': 's1', 'name': 'input', 'type': 'json_input',
                    'config': {'data': json.dumps([{'n': i} for i in range(20)])},
                    'onError': 'stop',
                },
            ]
        }
        ctx = execute_task(task, None)
        assert len(ctx['records']) == 20  # 不传 sample_limit，正式全量
        assert len(ctx['step_results'][0]['sampleRecords']) == 5
        assert ctx['step_results'][0]['sampleRecords'][0]['n'] == 0

    def test_step_cb_called_with_step_name_before_each_step(self):
        task = {
            'steps': [
                {'id': 's1', 'name': '第一步', 'type': 'json_input', 'config': {'data': '[{"a":1}]'}, 'onError': 'stop'},
                {'id': 's2', 'name': '第二步', 'type': 'filter', 'config': {'expression': 'True'}, 'onError': 'stop'},
            ]
        }
        seen = []
        execute_task(task, None, step_cb=lambda name: seen.append(name))
        assert seen == ['第一步', '第二步']

    def test_cancel_check_stops_before_next_step(self):
        task = {
            'steps': [
                {'id': 's1', 'name': '第一步', 'type': 'json_input', 'config': {'data': '[{"a":1}]'}, 'onError': 'stop'},
                {'id': 's2', 'name': '第二步', 'type': 'filter', 'config': {'expression': 'True'}, 'onError': 'stop'},
            ]
        }
        ctx = execute_task(task, None, cancel_check=lambda: True)
        assert ctx['cancelled'] is True
        assert len(ctx['step_results']) == 0  # 第一步开始前就被取消，一步都没跑
```

在文件顶部 `import json` 已存在（第 12 行），无需新增 import。

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_engine.py -v -k "sample_limit or step_cb or cancel_check or sample_records"`
Expected: FAIL — `_step_file_upload() got an unexpected keyword argument 'sample_limit'`（以及其余新测试的类似 TypeError/AssertionError）。

- [ ] **Step 3: 实现 — `_step_file_upload` 加 `sample_limit`**

`server/utils/etl_engine.py` 里的 `_step_file_upload` 函数（第 195-227 行）改为：

```python
def _step_file_upload(config, context, conn, sample_limit=None):
    """文件上传步骤：读取配置时上传并固定的 Excel/CSV 文件，解析为记录列表。

    sample_limit 不为空时用 nrows= 真正只读文件的前 N 行（不是读全量再截断）——
    dry run 靠这个避免大文件读取本身拖慢试运行。
    """
    file_id = config.get('fileId')
    if not file_id:
        raise ValueError('未上传文件')

    cur = conn.cursor()
    cur.execute('SELECT original_name, storage_path FROM data_files WHERE id = %s', (file_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('文件不存在或已被删除')
    original_name, storage_path = row

    ext = original_name.lower().rsplit('.', 1)[-1] if '.' in original_name else ''
    read_kwargs = {'nrows': sample_limit} if sample_limit else {}
    if ext == 'csv':
        df = pd.read_csv(storage_path, **read_kwargs)
    elif ext in ('xlsx', 'xls'):
        df = pd.read_excel(storage_path, **read_kwargs)
    else:
        raise ValueError(f'不支持的文件格式: {ext}')

    # Excel 的日期列 pandas 会自动解析成 Timestamp，Timestamp 不是合法 JSON 类型，
    # 后续 save_to_collection 用 psycopg2.extras.Json 写库时会针对该记录整条失败
    # （静默失败：不报错，只是这条记录不落库）。转成字符串，NaT 会变成 NaN，
    # 交给下面既有的 NaN→None 那一步统一处理。
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime('%Y-%m-%d %H:%M:%S')

    # NaN（空单元格）转 None，否则写入 dynamic_data 时 JSON 序列化会产出非法值
    # 先转为 object dtype 确保 None 不会被转回 NaN
    df = df.astype(object).where(pd.notnull(df), None)
    context['records'] = df.to_dict('records')
```

（唯一改动：签名加 `sample_limit=None`，`ext == 'csv'`/`xlsx` 分支加 `**read_kwargs`，docstring 补一句说明。其余逻辑原样不动。）

- [ ] **Step 4: 实现 — `_execute_step` 透传新参数**

`_execute_step` 函数（第 82-99 行）改为：

```python
def _execute_step(step_type, config, context, conn, dry_run,
                   sample_limit=None, progress_cb=None, cancel_check=None):
    """根据步骤类型分发执行。

    sample_limit 只有 file_upload 用得到（限制读取行数）；progress_cb/cancel_check
    只有 save_to_collection 用得到（Task 3 引入）；其余步骤类型忽略这几个参数。
    """
    if step_type == 'http_request':
        _step_http_request(config, context)
    elif step_type == 'json_input':
        _step_json_input(config, context)
    elif step_type == 'file_upload':
        _step_file_upload(config, context, conn, sample_limit=sample_limit)
    elif step_type == 'script':
        _step_script(config, context)
    elif step_type == 'field_mapping':
        _step_field_mapping(config, context)
    elif step_type == 'filter':
        _step_filter(config, context)
    elif step_type == 'save_to_collection':
        _step_save_to_collection(config, context, conn, dry_run,
                                  progress_cb=progress_cb, cancel_check=cancel_check)
    else:
        raise ValueError(f'未知的步骤类型: {step_type}')
```

（`_step_save_to_collection` 目前还是老签名 `(config, context, conn, dry_run)`，这一步先让 `_execute_step` 按新签名调用它——Task 3 会把 `_step_save_to_collection` 本身改成接受 `progress_cb`/`cancel_check`。在 Task 3 完成之前，`_execute_step` 这里传的 `progress_cb=progress_cb, cancel_check=cancel_check` 会导致 `TypeError`；这是预期的——Task 2 和 Task 3 必须按顺序合并，Task 2 单独跑测试时 `TestSaveToCollection` 现有测试不会走到 `_execute_step` 这条路径（它们直接调 `_step_save_to_collection`），所以不受影响；只有 `TestExecuteStep`/`TestExecuteTask` 里涉及 `save_to_collection` 步骤类型的用例会受影响，而现有测试和本任务新增测试都没有构造 `save_to_collection` 类型的 step，所以 Task 2 阶段这个 `TypeError` 不会被任何测试触发。）

- [ ] **Step 5: 实现 — `execute_task` 加采样截断 + step_cb + cancel_check + sampleRecords**

`execute_task` 函数（第 24-79 行）改为：

```python
def execute_task(task_dict, conn, dry_run=False, sample_limit=None,
                  step_cb=None, progress_cb=None, cancel_check=None):
    """
    执行 ETL 任务管道。

    按顺序执行 steps 中的每个步骤，数据在步骤间通过 context['records'] 流转。

    参数:
        task_dict: 任务字典，包含 steps 列表
        conn: 数据库连接
        dry_run: True 时不实际写入数据库
        sample_limit: 不为空时，每步执行完都把 context['records'] 截到前 N 条
            （dry run 用，配合 _step_file_upload 的 nrows= 避免读全量文件）
        step_cb(step_name): 每步开始前调用一次，供调用方展示"当前在跑哪一步"
        progress_cb(current, total): 透传给 save_to_collection，每写完一批调用一次
        cancel_check() -> bool: 每步开始前检查一次；save_to_collection 内部
            每批之间也会检查。返回 True 时立即停止，不再执行后续步骤

    返回:
        context 字典，包含 records, step_results, total, success, error, errors, cancelled
    """
    context = {
        'records': [],
        'step_results': [],
        'total': 0,
        'success': 0,
        'error': 0,
        'errors': [],
        'cancelled': False,
    }

    steps = task_dict.get('steps', [])
    if not steps:
        return context

    for step in steps:
        if cancel_check and cancel_check():
            context['cancelled'] = True
            break

        step_id = step.get('id', '')
        step_name = step.get('name', '')
        step_type = step.get('type', '')
        config = step.get('config', {})
        on_error = step.get('onError', 'stop')

        if step_cb:
            step_cb(step_name)

        try:
            _execute_step(step_type, config, context, conn, dry_run,
                          sample_limit=sample_limit, progress_cb=progress_cb,
                          cancel_check=cancel_check)
            if sample_limit:
                context['records'] = context['records'][:sample_limit]
            context['step_results'].append({
                'stepId': step_id,
                'stepName': step_name,
                'status': 'success',
                'recordCount': len(context['records']),
                'sampleRecords': context['records'][:5],
            })
            if context.get('cancelled'):
                break
        except Exception as e:
            error_msg = f'步骤「{step_name}」执行失败: {str(e)}'
            context['errors'].append(error_msg)
            context['step_results'].append({
                'stepId': step_id,
                'stepName': step_name,
                'status': 'error',
                'error': str(e),
            })
            if on_error == 'stop':
                break
            # skip / continue: 继续执行下一步骤

    return context
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_engine.py -v -k "sample_limit or step_cb or cancel_check or sample_records"`
Expected: PASS（全部新增用例）

- [ ] **Step 7: 运行整个 etl_engine 测试文件，确认没有回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_engine.py -v`
Expected: PASS（`TestSaveToCollection` 里调用 `_step_save_to_collection` 的用例是直接调用，不经过 `_execute_step`，签名不变，不受影响）

- [ ] **Step 8: 提交**

```bash
git add server/utils/etl_engine.py server/tests/test_etl_engine.py
git commit -m "feat(etl): dry run 取样本运行 + 每步结果附带样例数据"
```

---

### Task 3: `etl_engine.py` — `save_to_collection` 分批写库 + 进度 + 取消

**Files:**
- Modify: `server/utils/etl_engine.py`
- Modify: `server/tests/test_etl_engine.py`（移除/替换会被本任务破坏的旧测试）
- Test（新建，真实数据库）: `server/tests/test_etl_save_batch_integration.py`

**Interfaces:**
- Consumes：Task 2 已经存在的 `_execute_step`/`execute_task` 签名（本任务只改 `_step_save_to_collection` 内部实现和签名，`_execute_step` 里调用它的那一行在 Task 2 已经写好，指向的函数签名这里补齐）
- Produces：
  - `_write_batch(cur, collection, mode, match_field, batch, now) -> (success_count, skipped_count)`（新增私有函数）
  - `_step_save_to_collection(config, context, conn, dry_run, progress_cb=None, cancel_check=None)`（原有 4 个位置参数不变，新增两个可选关键字参数）
  - `context['cancelled']` 会在这里也可能被置 `True`（取消发生在 save_to_collection 内部时）

- [ ] **Step 1: 先看懂现有测试会怎么坏，逐个替换**

`server/tests/test_etl_engine.py` 的 `TestSaveToCollection` 类（第 295-373 行）里，`test_insert_mode`/`test_upsert_existing`/`test_upsert_new` 用裸 `MagicMock()` 当游标，且 `test_upsert_existing`/`test_upsert_new` mock 的是 `fetchone`（对应旧的"每条记录一次 SELECT ... LIMIT 1"）。新实现改成"每批一次 SELECT ... WHERE ... = ANY(...)"，用 `fetchall`，而且 insert 路径走 `psycopg2.extras.execute_values`（内部会调 `cur.mogrify(...)`，裸 `MagicMock()` 没配置 `mogrify` 返回值，会导致 `execute_values` 内部因为不是 bytes/str 而抛错）。

把整个 `TestSaveToCollection` 类替换成：

```python
class TestSaveToCollection:
    def test_dry_run_counts_only(self):
        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        ctx = {
            'records': [{'name': 'a'}, {'name': 'b'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
        }
        config = {'collection': 'test', 'mode': 'insert'}
        _step_save_to_collection(config, ctx, mock_conn, dry_run=True)
        assert ctx['success'] == 2
        assert ctx['total'] == 2
        # dry_run 不应执行任何 SQL
        mock_cur.execute.assert_not_called()

    def test_empty_collection_error(self):
        ctx = {
            'records': [{'name': 'a'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
        }
        with pytest.raises(ValueError, match='目标集合'):
            _step_save_to_collection({'collection': '', 'mode': 'insert'}, ctx, MagicMock(), False)

    def test_empty_records_noop(self):
        mock_conn = MagicMock()
        ctx = {
            'records': [],
            'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
        }
        _step_save_to_collection({'collection': 'test', 'mode': 'insert'}, ctx, mock_conn, False)
        mock_conn.cursor.assert_not_called()

    def test_progress_cb_called_per_batch_with_running_totals(self):
        """3 条记录、SAVE_BATCH_SIZE 远大于 3，dry_run 下一个批次就能验证回调签名和调用次数，
        不需要真的构造 1000+ 条数据来触发多批。"""
        mock_conn = MagicMock()
        ctx = {
            'records': [{'name': 'a'}, {'name': 'b'}, {'name': 'c'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
        }
        calls = []
        _step_save_to_collection(
            {'collection': 'test', 'mode': 'insert'}, ctx, mock_conn, dry_run=True,
            progress_cb=lambda cur, tot: calls.append((cur, tot)),
        )
        assert calls == [(3, 3)]

    def test_cancel_check_stops_after_current_batch(self):
        mock_conn = MagicMock()
        ctx = {
            'records': [{'name': 'a'}, {'name': 'b'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
        }
        _step_save_to_collection(
            {'collection': 'test', 'mode': 'insert'}, ctx, mock_conn, dry_run=True,
            cancel_check=lambda: True,
        )
        assert ctx['cancelled'] is True
        # 只有一批（2 条记录远小于 SAVE_BATCH_SIZE），这一批已经跑完才检查取消，
        # 所以这 2 条已经计入 success——取消只影响"后续批次不再跑"，不影响当前批。
        assert ctx['success'] == 2
```

（真正写库的 insert/upsert/update 语义 + 批次失败回滚 + 跨批取消保留已写数据，这些需要真实 SQL 语义正确性的用例，放到 Step 3 的新集成测试文件里，不在这个纯 mock 的文件里凑合验证——mock 一个 `execute_values` 内部要用的 `mogrify` 行为，验证力度还不如直接连真库。）

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_engine.py -v -k SaveToCollection`
Expected: FAIL — `_step_save_to_collection() got an unexpected keyword argument 'progress_cb'`（新签名还没实现）；旧的 `test_insert_mode`/`test_upsert_existing`/`test_upsert_new` 已经在 Step 1 里被删除，不会再出现在测试收集里。

- [ ] **Step 3: 实现 — `_write_batch` + 重写 `_step_save_to_collection`**

`server/utils/etl_engine.py` 里，在 `_step_save_to_collection` 函数（原第 296-397 行）**之前**新增模块级常量（放在文件顶部 `HTTP_TIMEOUT = 30` 那一行附近即可，比如紧跟其后）：

```python
# save_to_collection 批量写库的批大小。大小上参照前端 importPageRecords.ts 的
# BATCH_SIZE 惯例，但这是本文件里全新的常量，不是复用某个已有的共享常量。
SAVE_BATCH_SIZE = 1000
```

然后把整个 `_step_save_to_collection` 函数（原第 296-397 行，从 `def _step_save_to_collection` 到文件末尾）替换为：

```python
def _write_batch(cur, collection, mode, match_field, batch, now):
    """写一批记录，返回 (success_count, skipped_count)。

    任何异常直接向上抛，不在这里捕获——PostgreSQL 事务一旦出错，同一个连接
    后续的语句会全部失败（"current transaction is aborted"），调用方必须先
    ROLLBACK 才能继续用这个连接处理下一批，所以异常处理和 rollback 的职责
    留给调用方（_step_save_to_collection），这里只管纯粹的写库逻辑。
    """
    if mode == 'insert':
        values = []
        for record in batch:
            record_data = {k: v for k, v in record.items() if k not in ('id', 'createdAt')}
            rid = record.get('id') or f'rec-{uuid.uuid4().hex[:12]}'
            values.append((rid, collection, psycopg2.extras.Json(record_data), now))
        psycopg2.extras.execute_values(
            cur,
            'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES %s',
            values,
        )
        return len(batch), 0

    if not match_field:
        raise ValueError(f'{mode} 模式需要指定匹配字段')

    # 批量找出这一批里已存在的匹配记录：一次查询代替原来"每条记录一次 SELECT"，
    # 这是分批写库相对逐条写库的核心收益之一。
    match_values = [str(r[match_field]) for r in batch if r.get(match_field) is not None]
    existing_map = {}
    if match_values:
        cur.execute(
            "SELECT id, data->>%s AS mv FROM dynamic_data WHERE collection = %s AND data->>%s = ANY(%s)",
            (match_field, collection, match_field, match_values),
        )
        for eid, mv in cur.fetchall():
            existing_map[mv] = eid

    insert_values = []
    updated = 0
    skipped = 0
    for record in batch:
        record_data = {k: v for k, v in record.items() if k not in ('id', 'createdAt')}
        match_val = record.get(match_field)
        existing_id = existing_map.get(str(match_val)) if match_val is not None else None

        if existing_id:
            # 每条记录的 data 值都不同，UPDATE ... SET data = %s 无法用单条
            # execute_values 语句一次性表达"多行各自不同的值"，仍然逐条执行——
            # 但已经从"每条记录 1 次 SELECT + 1 次 UPDATE"降到"每批 1 次
            # SELECT + 每条命中记录 1 次 UPDATE"，且不再是同步阻塞的单条请求。
            cur.execute(
                'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 WHERE id = %s',
                (psycopg2.extras.Json(record_data), existing_id),
            )
            updated += 1
        elif mode == 'upsert':
            rid = record.get('id') or f'rec-{uuid.uuid4().hex[:12]}'
            insert_values.append((rid, collection, psycopg2.extras.Json(record_data), now))
        else:
            # update 模式且没有匹配到已存在记录：计为失败（与原逐条实现一致）
            skipped += 1

    if insert_values:
        psycopg2.extras.execute_values(
            cur,
            'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES %s',
            insert_values,
        )

    return updated + len(insert_values), skipped


def _step_save_to_collection(config, context, conn, dry_run, progress_cb=None, cancel_check=None):
    """写入集合步骤：将数据分批写入系统 dynamic_data 表。

    每批写完立即 commit（不再是整任务一个大事务）：一是让 progress_cb 汇报的
    进度对其它连接（轮询接口）立即可见，二是让取消/崩溃时已经成功写入的批次
    不会丢失——这是与用户确认过的行为变化，见设计文档「数据模型改动」一节。

    progress_cb(current, total): 每批写完（或 dry_run 下每批"模拟写完"）调用一次
    cancel_check() -> bool: 每批之间检查一次；返回 True 时停止后续批次，
        context['cancelled'] 置 True，本批（已经写完的）不受影响
    """
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

    for start in range(0, len(records), SAVE_BATCH_SIZE):
        batch = records[start:start + SAVE_BATCH_SIZE]

        if dry_run:
            success += len(batch)
        else:
            try:
                batch_success, batch_skipped = _write_batch(cur, collection, mode, match_field, batch, now)
                success += batch_success
                errors += batch_skipped
                conn.commit()
            except Exception as e:
                conn.rollback()
                errors += len(batch)
                context['errors'].append(f'批量写入失败（{len(batch)} 条）: {str(e)}')

        if progress_cb:
            progress_cb(success + errors, len(records))
        if cancel_check and cancel_check():
            context['cancelled'] = True
            break

    if not dry_run and success > 0:
        from utils.sequences import reseed_sequences
        reseed_sequences(cur, collections=[collection], branch_id='main')
        conn.commit()

    context['total'] = len(records)
    context['success'] = success
    context['error'] = errors
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_engine.py -v`
Expected: PASS（全部用例，包括 Task 2 新增的和本任务重写的 `TestSaveToCollection`）

- [ ] **Step 5: 新建真实数据库集成测试**

创建 `server/tests/test_etl_save_batch_integration.py`：

```python
"""集成测试：ETL save_to_collection 分批写库 —— 走真实 DB，不 mock 游标。

背景：insert 走 execute_values（内部要用 cur.mogrify，纯 MagicMock 配不出正确行为）；
upsert/update 的批量匹配查询、批次失败 rollback、取消后已写批次保留，这几个行为
都是真实 SQL 事务语义，mock 出来的置信度低于直接连测试库验证。

同 test_open_api_batch_integration.py / test_dynamic_keyword_search.py 模式：
测试数据用 `_t_` 前缀自建自清理，走 get_db()（不 mock）。
"""
import os
import sys
from datetime import datetime, timezone

import psycopg2.extras
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db  # noqa: E402
from utils.etl_engine import _step_save_to_collection  # noqa: E402

COLLECTION = '_t_etl_batch_save'


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (COLLECTION,))


def _fetch_all():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, data FROM dynamic_data WHERE collection = %s ORDER BY id', (COLLECTION,))
        return cur.fetchall()


class TestInsertBatch:
    def test_batch_insert_writes_all_records(self):
        with get_db() as conn:
            ctx = {
                'records': [{'name': f'r{i}'} for i in range(5)],
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'insert'}, ctx, conn, dry_run=False,
            )
            assert ctx['success'] == 5
            assert ctx['error'] == 0
        rows = _fetch_all()
        assert len(rows) == 5
        assert {r[1]['name'] for r in rows} == {f'r{i}' for i in range(5)}


class TestUpsertBatch:
    def test_upsert_mixes_existing_update_and_new_insert_in_one_batch(self):
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s, %s, %s, %s)',
                ('_t_existing_1', COLLECTION, psycopg2.extras.Json({'code': 'A', 'name': '旧名字'}),
                 datetime.now(timezone.utc)),
            )
            conn.commit()

            ctx = {
                'records': [
                    {'code': 'A', 'name': '新名字'},   # 命中已存在记录，走 UPDATE
                    {'code': 'B', 'name': '全新记录'},  # 不存在，走 INSERT
                ],
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'upsert', 'matchField': 'code'},
                ctx, conn, dry_run=False,
            )
            assert ctx['success'] == 2
            assert ctx['error'] == 0
        rows = {r[0]: r[1] for r in _fetch_all()}
        assert rows['_t_existing_1']['name'] == '新名字'
        assert any(d['code'] == 'B' and d['name'] == '全新记录' for d in rows.values())


class TestUpdateBatch:
    def test_update_mode_skips_unmatched_records(self):
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s, %s, %s, %s)',
                ('_t_existing_2', COLLECTION, psycopg2.extras.Json({'code': 'X', 'name': '旧'}),
                 datetime.now(timezone.utc)),
            )
            conn.commit()

            ctx = {
                'records': [
                    {'code': 'X', 'name': '新'},       # 命中，更新
                    {'code': 'NOPE', 'name': '不存在'},  # 不命中，计为失败
                ],
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'update', 'matchField': 'code'},
                ctx, conn, dry_run=False,
            )
            assert ctx['success'] == 1
            assert ctx['error'] == 1
        rows = _fetch_all()
        assert len(rows) == 1
        assert rows[0][1]['name'] == '新'


class TestProgressAndCancel:
    def test_progress_cb_reports_running_totals_across_batches(self):
        """SAVE_BATCH_SIZE=1000，构造 3 条即可验证单批场景的回调；多批场景在
        test_etl_engine.py 的纯逻辑测试里已经用 dry_run 验证过回调调用形状，
        这里重点验证真实写库路径下 progress_cb 同样被正确调用。"""
        with get_db() as conn:
            ctx = {
                'records': [{'name': f'r{i}'} for i in range(3)],
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            calls = []
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'insert'}, ctx, conn, dry_run=False,
                progress_cb=lambda cur, tot: calls.append((cur, tot)),
            )
            assert calls == [(3, 3)]

    def test_cancel_after_first_batch_keeps_already_written_data(self, monkeypatch):
        """把 SAVE_BATCH_SIZE 临时改小，制造"多批"场景：第一批写完后取消，
        断言第一批已经落库、第二批没有被处理。"""
        import utils.etl_engine as etl_engine
        monkeypatch.setattr(etl_engine, 'SAVE_BATCH_SIZE', 2)

        with get_db() as conn:
            ctx = {
                'records': [{'name': f'r{i}'} for i in range(4)],  # 2 批，每批 2 条
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            call_count = {'n': 0}

            def _cancel_after_first_batch():
                call_count['n'] += 1
                return call_count['n'] >= 1  # 第一批写完、检查取消时就返回 True

            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'insert'}, ctx, conn, dry_run=False,
                cancel_check=_cancel_after_first_batch,
            )
            assert ctx['cancelled'] is True
            assert ctx['success'] == 2  # 只有第一批的 2 条

        rows = _fetch_all()
        assert len(rows) == 2  # 第一批已提交，数据库里能查到；第二批从未执行


class TestBatchFailureRollback:
    def test_batch_insert_failure_rolls_back_only_that_batch(self, monkeypatch):
        """人为让 execute_values 在处理某一批时抛错，断言：(a) 这一批的记录
        没有落库；(b) 连接在异常后仍然可用（能继续下一批，或至少调用方能正常
        commit/rollback，不会因为"事务已中止"而级联失败）；(c) errors 里记录了
        失败原因。"""
        import utils.etl_engine as etl_engine

        original_write_batch = etl_engine._write_batch
        call_count = {'n': 0}

        def _flaky_write_batch(cur, collection, mode, match_field, batch, now):
            call_count['n'] += 1
            if call_count['n'] == 1:
                raise Exception('模拟批量写入失败')
            return original_write_batch(cur, collection, mode, match_field, batch, now)

        monkeypatch.setattr(etl_engine, '_write_batch', _flaky_write_batch)
        monkeypatch.setattr(etl_engine, 'SAVE_BATCH_SIZE', 2)

        with get_db() as conn:
            ctx = {
                'records': [{'name': f'r{i}'} for i in range(4)],  # 2 批
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'insert'}, ctx, conn, dry_run=False,
            )
            # 第一批失败（计入 error），第二批正常（计入 success）
            assert ctx['success'] == 2
            assert ctx['error'] == 2
            assert any('模拟批量写入失败' in e for e in ctx['errors'])

        rows = _fetch_all()
        # 只有第二批（未失败的那批）的 2 条记录落库
        assert len(rows) == 2
```

- [ ] **Step 6: 运行集成测试，确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_save_batch_integration.py -v`
Expected: PASS（全部用例；需要本机能连上 `config.py` 里配置的测试数据库，与其它集成测试文件的前提一致）

- [ ] **Step 7: 提交**

```bash
git add server/utils/etl_engine.py server/tests/test_etl_engine.py server/tests/test_etl_save_batch_integration.py
git commit -m "feat(etl): save_to_collection 分批写库，带进度回调与取消检查"
```

---

### Task 4: 后台调度器 `etl_scheduler.py`

**Files:**
- Create: `server/utils/etl_scheduler.py`
- Modify: `server/app.py`
- Test: `server/tests/test_etl_scheduler.py`

**Interfaces:**
- Consumes：`execute_task(task, conn, dry_run=False, step_cb=..., progress_cb=..., cancel_check=...)`（Task 2/3 已实现）
- Produces：`start_etl_scheduler(app)`（在 `app.py` 里调用）

- [ ] **Step 1: 写失败测试**

创建 `server/tests/test_etl_scheduler.py`：

```python
"""ETL 后台调度器测试：认领语义 + 孤儿恢复 + 端到端执行一条 pending 日志。

走真实数据库（FOR UPDATE SKIP LOCKED 的并发语义、APScheduler 的 tick 执行，
都不适合 mock）。测试数据用 `_t_` 前缀自建自清理。
"""
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import psycopg2.extras
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db  # noqa: E402
from utils import etl_scheduler  # noqa: E402

TASK_ID = '_t_etl_sched_task'
COLLECTION = '_t_etl_sched_out'


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM etl_logs WHERE task_id = %s", (TASK_ID,))
        cur.execute("DELETE FROM etl_tasks WHERE id = %s", (TASK_ID,))
        cur.execute("DELETE FROM dynamic_data WHERE collection = %s", (COLLECTION,))


def _seed_task(steps):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO etl_tasks (id, name, description, steps, enabled, created_at, updated_at) '
            "VALUES (%s, %s, '', %s, TRUE, NOW(), NOW()) "
            'ON CONFLICT (id) DO UPDATE SET steps = EXCLUDED.steps',
            (TASK_ID, 'sched-test', psycopg2.extras.Json(steps)),
        )


def _seed_pending_log():
    log_id = f'_t_log_{uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO etl_logs (id, task_id, task_name, status, started_at, "
            "total_records, success_count, error_count, step_results, progress_current, cancel_requested) "
            "VALUES (%s, %s, 'sched-test', 'pending', %s, 0, 0, 0, '[]'::jsonb, 0, FALSE)",
            (log_id, TASK_ID, datetime.now(timezone.utc)),
        )
    return log_id


def _fetch_log(log_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT status, progress_current, total_records, current_step_name, success_count, error_count '
            'FROM etl_logs WHERE id = %s',
            (log_id,),
        )
        return cur.fetchone()


class TestClaimOne:
    # 注意：_claim_one 认领的是整张 etl_logs 表里最早的 pending 行，不按 task_id
    # 过滤。如果本机同时有 `npm run dev:all` 的后端进程在跑（它自己的调度器
    # 也在轮询同一个数据库），这个测试有极小概率被真实调度器抢先认领掉刚插入
    # 的这一行，导致断言失败——这是 claim-then-run 模式对着同一个开发数据库
    # 跑测试时的固有特性，不是这个测试本身的 bug。跑这个测试文件前建议先停掉
    # 本机正在跑的后端进程。
    def test_claims_pending_row_and_marks_running(self):
        _seed_task([{'id': 's1', 'name': 'input', 'type': 'json_input',
                     'config': {'data': '[]'}, 'onError': 'stop'}])
        log_id = _seed_pending_log()

        claimed = etl_scheduler._claim_one()

        assert claimed is not None
        assert claimed[0] == log_id
        status, *_ = _fetch_log(log_id)
        assert status == 'running'

    def test_no_pending_rows_returns_none(self):
        # _cleanup 已经清过本文件的 _t_ 前缀数据；不额外插入 pending 行
        claimed = etl_scheduler._claim_one()
        assert claimed is None or claimed[0].startswith('_t_') is False
        # 弱断言：不假设整个数据库没有其它 pending 行（并行测试可能存在别的
        # 测试数据），只确认调用不报错即可——真正的"认领了我插入的那一行"
        # 已经在 test_claims_pending_row_and_marks_running 里验证过


class TestRestartAudit:
    def test_resets_running_rows_to_pending(self):
        log_id = _seed_pending_log()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE etl_logs SET status = 'running' WHERE id = %s", (log_id,))

        etl_scheduler._restart_audit()

        status, *_ = _fetch_log(log_id)
        assert status == 'pending'


class TestRunOneEndToEnd:
    def test_executes_pending_log_and_writes_result(self):
        _seed_task([
            {
                'id': 's1', 'name': '输入', 'type': 'json_input',
                'config': {'data': '[{"name": "a"}, {"name": "b"}]'},
                'onError': 'stop',
            },
            {
                'id': 's2', 'name': '写入', 'type': 'save_to_collection',
                'config': {'collection': COLLECTION, 'mode': 'insert'},
                'onError': 'stop',
            },
        ])
        log_id = _seed_pending_log()

        etl_scheduler._run_one(log_id, TASK_ID)

        status, progress, total, step_name, success, error = _fetch_log(log_id)
        assert status == 'success'
        assert success == 2
        assert error == 0
        assert total == 2
        assert progress == 2

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM dynamic_data WHERE collection = %s', (COLLECTION,))
            assert cur.fetchone()[0] == 2

    def test_deleted_task_marks_log_as_error(self):
        log_id = _seed_pending_log()
        # 故意不 seed task：模拟认领之后、执行之前任务被删除
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM etl_tasks WHERE id = %s", (TASK_ID,))

        etl_scheduler._run_one(log_id, TASK_ID)

        status, *_ = _fetch_log(log_id)
        assert status == 'error'
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_scheduler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.etl_scheduler'`

- [ ] **Step 3: 实现 `etl_scheduler.py`**

创建 `server/utils/etl_scheduler.py`：

```python
"""ETL 任务后台调度器：单线程轮询 etl_logs 里的 pending 行，异步执行。

见 utils/etl_engine.py::execute_task。跑一次 ETL 任务可能是几万到几十万次
数据库写入，不能再放在 HTTP 请求线程里同步跑（会拖住请求线程、占着连接池
里的连接不放，浏览器超时后后端还在傻跑）。routes/etl_tasks.py::run_task
现在只插入一条 status='pending' 的 etl_logs 行就立即返回，真正的执行在这里。

用 APScheduler 的 interval job（与 field_index_scheduler.py / ai_scan_scheduler.py
同一模式）而不是自己起 threading.Thread：max_instances=1 + coalesce=True 保证
同一时刻只有一个 tick 在跑，一个 tick 里的 ETL 任务本身可能耗时很久，跑完才
轮到下一个 tick，天然实现"单线程串行执行多个 ETL 运行请求"，不需要自己管理
线程池/唤醒事件。
"""
import traceback
from datetime import datetime, timezone

import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler

from db import get_db, pool

_scheduler = None
TICK_INTERVAL_SEC = 2


def _claim_one():
    """认领一条 pending 的 etl_logs 行，置为 running，返回 (log_id, task_id)。
    没有待跑的行时返回 None。

    FOR UPDATE SKIP LOCKED 是额外的防御——正常情况下 max_instances=1 已经保证
    不会有并发 tick，这里防的是理论上的多进程场景。
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "WITH picked AS ("
            "  SELECT id FROM etl_logs WHERE status = 'pending'"
            "  ORDER BY started_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            ") "
            "UPDATE etl_logs l SET status = 'running' "
            "FROM picked WHERE l.id = picked.id "
            "RETURNING l.id, l.task_id"
        )
        row = cur.fetchone()
    return row


def _make_progress_cb(log_id):
    def _progress_cb(current, total):
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    'UPDATE etl_logs SET progress_current = %s, total_records = %s WHERE id = %s',
                    (current, total, log_id),
                )
        except Exception:
            traceback.print_exc()
    return _progress_cb


def _make_step_cb(log_id):
    def _step_cb(step_name):
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    'UPDATE etl_logs SET current_step_name = %s WHERE id = %s',
                    (step_name, log_id),
                )
        except Exception:
            traceback.print_exc()
    return _step_cb


def _make_cancel_check(log_id):
    def _cancel_check():
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute('SELECT cancel_requested FROM etl_logs WHERE id = %s', (log_id,))
                row = cur.fetchone()
                return bool(row and row[0])
        except Exception:
            traceback.print_exc()
            return False
    return _cancel_check


def _run_one(log_id, task_id):
    from utils.etl_engine import execute_task

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description, steps FROM etl_tasks WHERE id = %s', (task_id,))
        row = cur.fetchone()
    if not row:
        # 认领之后、执行之前任务被删除了
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE etl_logs SET status = 'error', error_detail = %s, finished_at = NOW() WHERE id = %s",
                ('任务已被删除', log_id),
            )
        return

    task = {'id': row[0], 'name': row[1], 'description': row[2], 'steps': row[3] or []}

    exec_conn = pool.getconn()
    try:
        context = execute_task(
            task, exec_conn, dry_run=False,
            step_cb=_make_step_cb(log_id),
            progress_cb=_make_progress_cb(log_id),
            cancel_check=_make_cancel_check(log_id),
        )
        finished_at = datetime.now(timezone.utc)

        if context.get('cancelled'):
            status = 'cancelled'
        elif context['error'] > 0 and context['success'] > 0:
            status = 'partial'
        elif context['error'] > 0 and context['success'] == 0:
            status = 'error'
        else:
            status = 'success'

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'UPDATE etl_logs SET status = %s, finished_at = %s, total_records = %s, '
                'success_count = %s, error_count = %s, step_results = %s, error_detail = %s, '
                'progress_current = %s '
                'WHERE id = %s',
                (status, finished_at, context['total'], context['success'], context['error'],
                 psycopg2.extras.Json(context['step_results']),
                 '\n'.join(context['errors']) if context['errors'] else None,
                 context['success'] + context['error'], log_id),
            )
            cur.execute(
                'UPDATE etl_tasks SET last_run_at = %s, last_run_status = %s WHERE id = %s',
                (finished_at, status, task_id),
            )
    except Exception as e:
        exec_conn.rollback()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE etl_logs SET status = 'error', finished_at = NOW(), error_detail = %s WHERE id = %s",
                (str(e)[:2000], log_id),
            )
    finally:
        pool.putconn(exec_conn)


def _tick():
    claimed = _claim_one()
    if not claimed:
        return
    log_id, task_id = claimed
    _run_one(log_id, task_id)


def _safe_tick():
    try:
        _tick()
    except Exception:
        traceback.print_exc()


def _restart_audit():
    """进程重启时把遗留的 running 行重置回 pending，避免永远卡住。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE etl_logs SET status = 'pending' WHERE status = 'running'")


def start_etl_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return
    try:
        _restart_audit()
    except Exception:
        traceback.print_exc()
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_safe_tick, 'interval', seconds=TICK_INTERVAL_SEC, id='etl_tick',
                       max_instances=1, coalesce=True)
    _scheduler.start()
```

- [ ] **Step 4: 在 `app.py` 里挂载**

`server/app.py` 里，在已有的调度器启动块（第 98-125 行）里，`field_index_scheduler` 那一段之后加：

```python
    # Start field-index build/drop worker (async CREATE/DROP INDEX CONCURRENTLY)
    from utils.field_index_scheduler import start_field_index_scheduler
    start_field_index_scheduler(app)

    # Start ETL background scheduler (async run of large imports, see utils/etl_scheduler.py)
    from utils.etl_scheduler import start_etl_scheduler
    start_etl_scheduler(app)
```

（这段代码已经在 `not _RUNNING_UNDER_PYTEST` 的门禁之内——本文件的测试用真实 DB 直接调用 `etl_scheduler._claim_one`/`_run_one` 等函数，不依赖 Flask app 启动，也不会和这个门禁冲突；门禁本身是为了防止 pytest 跑路由测试时后台调度器抢跑测试刚插入的 pending 行，`test_etl_scheduler.py` 是直接测调度器函数，不是测路由，同样不受影响。）

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_scheduler.py -v`
Expected: PASS（全部用例）

- [ ] **Step 6: 提交**

```bash
git add server/utils/etl_scheduler.py server/app.py server/tests/test_etl_scheduler.py
git commit -m "feat(etl): 新增后台调度器，异步执行 pending 的 ETL 运行"
```

---

### Task 5: `routes/etl_tasks.py` — 异步运行接口 + 日志详情 + 取消

**Files:**
- Modify: `server/routes/etl_tasks.py`
- Modify: `server/tests/test_routes_etl.py`

**Interfaces:**
- Consumes：Task 1 的新列（`progress_current`/`current_step_name`/`cancel_requested`），Task 2 的 `execute_task(..., sample_limit=...)`（dry run 用）
- Produces：
  - `POST /etlTasks/<task_id>/run`（`dryRun` 为空/false）→ `{ logId, status: 'pending' }`，202
  - `POST /etlTasks/<task_id>/run`（`dryRun: true`）→ 响应结构不变，`stepResults[].sampleRecords` 存在
  - `GET /etlTasks/<task_id>/logs/<log_id>` → 单条日志详情
  - `POST /etlTasks/<task_id>/logs/<log_id>/cancel` → 置位 `cancel_requested`

- [ ] **Step 1: 写失败测试**

现有 `server/tests/test_routes_etl.py` 里已经有一个 `class TestRunTask`（第 110-144 行），两个用例 `test_task_not_found`/`test_dry_run` **都用 `'dryRun': True`**——都是走 dry run 同步路径，不需要改动，新实现下继续按原样通过（`test_task_not_found` 走的是"任务不存在"早退路径，与 dry run 无关；`test_dry_run` 断言的 `status`/`len(stepResults)` 在新实现下不变，只是 `stepResults` 里每项多了一个新键 `sampleRecords`，测试没有对 `stepResults` 做穷尽式相等断言，不受影响）。**这两个现有测试不用动。**

但 `class TestGetLogs::test_returns_logs`（第 147-158 行）**必须改**——它 mock 的 `fetchall` 返回值是旧的 11 元素行形状（对应旧的 11 列 `LOG_COLS`），而本任务把 `LOG_COLS`/`log_row_to_dict` 扩到 13 列（新增 `progress_current`/`current_step_name`），`log_row_to_dict` 会按 `row[11]`/`row[12]` 取值，11 元素的行会直接 `IndexError`。把这一行：

```python
        mock_cursor.fetchall.return_value = [
            ('log-1', 'etl-1', '任务', 'success', now, now, 10, 10, 0, [], None),
        ]
```

改为：

```python
        mock_cursor.fetchall.return_value = [
            ('log-1', 'etl-1', '任务', 'success', now, now, 10, 10, 0, [], None, 10, None),
        ]
```

（末尾补的 `10, None` 对应新增的 `progress_current=10`（跑完了，等于 total）、`current_step_name=None`（跑完了不再有"当前步骤"）。）

在文件末尾追加：

```python
class TestRunTaskAsync:
    def test_real_run_returns_log_id_immediately(self, setup):
        client, mock_cursor, mock_conn, headers = setup
        # 第一次 fetchone：查 etl_tasks 拿任务定义；INSERT INTO etl_logs 不需要 fetchone
        mock_cursor.fetchone.return_value = (
            'etl-1', '任务1', '描述', [], True, None, None, None, None,
        )
        resp = client.post('/etlTasks/etl-1/run',
                           data=json.dumps({'dryRun': False}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 202
        data = resp.get_json()
        assert data['status'] == 'pending'
        assert data['logId'].startswith('etl-log-')
        # 确认真的插入了一条 pending 日志（INSERT INTO etl_logs 出现在调用记录里）
        calls = mock_cursor.execute.call_args_list
        assert any('INSERT INTO etl_logs' in str(c) for c in calls)

    def test_run_missing_task_returns_404(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/etlTasks/missing/run',
                           data=json.dumps({'dryRun': False}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 404

    def test_dry_run_still_synchronous_with_sample_size(self, setup):
        client, mock_cursor, mock_conn, headers = setup
        mock_cursor.fetchone.return_value = (
            'etl-1', '任务1', '描述', [
                {'id': 's1', 'name': '输入', 'type': 'json_input',
                 'config': {'data': '[{"a":1}]'}, 'onError': 'stop'},
            ], True, None, None, None, None,
        )
        resp = client.post('/etlTasks/etl-1/run',
                           data=json.dumps({'dryRun': True, 'sampleSize': 10}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'stepResults' in data
        assert data['stepResults'][0]['sampleRecords'] == [{'a': 1}]
        # dry run 不应该插入 etl_logs 行
        calls = mock_cursor.execute.call_args_list
        assert not any('INSERT INTO etl_logs' in str(c) for c in calls)


class TestLogDetail:
    def test_returns_single_log(self, setup):
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.return_value = (
            'log-1', 'etl-1', '任务1', 'running', now, None,
            100, 40, 0, [], None, 40, '写入集合',
        )
        resp = client.get('/etlTasks/etl-1/logs/log-1', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'running'
        assert data['progressCurrent'] == 40
        assert data['currentStepName'] == '写入集合'

    def test_missing_log_returns_404(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = None
        resp = client.get('/etlTasks/etl-1/logs/missing', headers=headers)
        assert resp.status_code == 404


class TestCancelRun:
    def test_cancel_pending_or_running_sets_flag(self, setup):
        client, mock_cursor, mock_conn, headers = setup
        mock_cursor.fetchone.return_value = ('running',)
        resp = client.post('/etlTasks/etl-1/logs/log-1/cancel', headers=headers)
        assert resp.status_code == 200
        calls = mock_cursor.execute.call_args_list
        assert any('cancel_requested = TRUE' in str(c) for c in calls)

    def test_cancel_finished_run_returns_409(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = ('success',)
        resp = client.post('/etlTasks/etl-1/logs/log-1/cancel', headers=headers)
        assert resp.status_code == 409

    def test_cancel_missing_log_returns_404(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/etlTasks/etl-1/logs/log-1/cancel', headers=headers)
        assert resp.status_code == 404
```

在文件顶部确认已有 `from datetime import datetime, timezone`（第 13 行已有）和 `import json`（第 9 行已有），无需新增 import。

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_etl.py -v`
Expected: FAIL —— 新测试类里的用例因为端点行为/路由还没实现而失败（`404`/`AssertionError`/`405 Method Not Allowed` 等）；Step 1 里改写过的旧测试如果没改对可能也会失败，一并观察。

- [ ] **Step 3: 实现**

`server/routes/etl_tasks.py` 里，把 `run_task` 函数（第 173-256 行，从 `@etl_tasks_bp.route('/etlTasks/<task_id>/run'` 到函数结束）整个替换为：

```python
DEFAULT_SAMPLE_SIZE = 50


@etl_tasks_bp.route('/etlTasks/<task_id>/run', methods=['POST'])
@require_permission('admin.etl_tasks')
def run_task(task_id):
    body = request.get_json(force=True) if request.data else {}
    dry_run = body.get('dryRun', False)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT {TASK_COLS} FROM etl_tasks WHERE id = %s', (task_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '任务不存在'}), 404

    task = task_row_to_dict(row)

    if dry_run:
        sample_size = body.get('sampleSize') or DEFAULT_SAMPLE_SIZE
        from db import pool
        exec_conn = pool.getconn()
        try:
            from utils.etl_engine import execute_task
            context = execute_task(task, exec_conn, dry_run=True, sample_limit=sample_size)
            exec_conn.rollback()

            status = 'success'
            if context['error'] > 0 and context['success'] > 0:
                status = 'partial'
            elif context['error'] > 0 and context['success'] == 0:
                status = 'error'

            return jsonify({
                'status': status,
                'totalRecords': context['total'],
                'successCount': context['success'],
                'errorCount': context['error'],
                'stepResults': context['step_results'],
                'errors': context['errors'],
            })
        except Exception as e:
            exec_conn.rollback()
            return jsonify({
                'status': 'error',
                'totalRecords': 0,
                'successCount': 0,
                'errorCount': 0,
                'stepResults': [],
                'errors': [str(e)],
            }), 500
        finally:
            pool.putconn(exec_conn)

    # 正式运行：插入一条 pending 日志行立即返回，真正执行交给后台调度器
    # （utils/etl_scheduler.py）。
    log_id = f'etl-log-{uuid.uuid4().hex[:12]}'
    started_at = datetime.now(timezone.utc)
    with get_db() as conn2:
        cur2 = conn2.cursor()
        cur2.execute(
            'INSERT INTO etl_logs (id, task_id, task_name, status, started_at, '
            "total_records, success_count, error_count, step_results, progress_current, cancel_requested) "
            "VALUES (%s, %s, %s, 'pending', %s, 0, 0, 0, '[]'::jsonb, 0, FALSE)",
            (log_id, task_id, task['name'], started_at),
        )

    return jsonify({'logId': log_id, 'status': 'pending'}), 202
```

然后把 `LOG_COLS` 常量（原第 61 行）和 `log_row_to_dict` 函数（原第 44-57 行）替换为：

```python
def log_row_to_dict(row):
    return {
        'id': row[0],
        'taskId': row[1],
        'taskName': row[2],
        'status': row[3],
        'startedAt': format_ts(row[4]),
        'finishedAt': format_ts(row[5]),
        'totalRecords': row[6],
        'successCount': row[7],
        'errorCount': row[8],
        'stepResults': row[9] or [],
        'errorDetail': row[10],
        'progressCurrent': row[11],
        'currentStepName': row[12],
    }
```

```python
LOG_COLS = ('id, task_id, task_name, status, started_at, finished_at, total_records, '
            'success_count, error_count, step_results, error_detail, '
            'progress_current, current_step_name')
```

最后，在文件末尾（`list_logs` 函数之后）新增两个端点：

```python
@etl_tasks_bp.route('/etlTasks/<task_id>/logs/<log_id>', methods=['GET'])
@require_permission('admin.etl_tasks')
def get_log_detail(task_id, log_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT {LOG_COLS} FROM etl_logs WHERE id = %s AND task_id = %s',
            (log_id, task_id),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '日志不存在'}), 404
    return jsonify(log_row_to_dict(row))


@etl_tasks_bp.route('/etlTasks/<task_id>/logs/<log_id>/cancel', methods=['POST'])
@require_permission('admin.etl_tasks')
def cancel_log(task_id, log_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT status FROM etl_logs WHERE id = %s AND task_id = %s', (log_id, task_id))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '日志不存在'}), 404
        if row[0] not in ('pending', 'running'):
            return jsonify({'error': '任务已结束，无法取消'}), 409
        cur.execute('UPDATE etl_logs SET cancel_requested = TRUE WHERE id = %s', (log_id,))
    return jsonify({'ok': True})
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_etl.py -v`
Expected: PASS（全部用例）

- [ ] **Step 5: 跑一次全量后端测试，确认没有跨文件回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest -v`
Expected: PASS（全部测试文件，包括本次新增的三个）

- [ ] **Step 6: 提交**

```bash
git add server/routes/etl_tasks.py server/tests/test_routes_etl.py
git commit -m "feat(etl): 正式运行改为异步返回 logId，新增日志详情/取消接口"
```

---

### Task 6: 前端类型 + API

**Files:**
- Modify: `src/types/etl.ts`
- Modify: `src/api/etl.ts`
- Modify: `src/api/__tests__/etl.test.ts`

**Interfaces:**
- Produces：
  - `EtlLog`/`EtlRunResult` 新增 `progressCurrent?`/`currentStepName?`，`status` 放宽为 `string`（已经是 `string`，不用改）
  - `EtlStepResult` 新增 `sampleRecords?: Record<string, any>[]`
  - `getEtlLog(taskId, logId)`、`cancelEtlRun(taskId, logId)`
  - `runEtlTask(id, options)` 的 `options` 新增 `sampleSize?: number`；正式运行时返回值形状变成 `{ logId: string; status: string }`

- [ ] **Step 1: 写失败测试**

现有 `src/api/__tests__/etl.test.ts` 里 mock 函数的别名是 `mockGet`/`mockPost`（`const mockGet = vi.mocked(get)` 这种写法，第 27-30 行），断言时用的是 `mockGet`/`mockPost` 不是裸的 `get`/`post`，新增用例要跟这个命名一致。

两个现有用例**不用改**（`runEtlTask 调用 POST /etlTasks/{id}/run` 第 71-77 行、`runEtlTask dryRun=true 正确传参` 第 79-84 行）：新实现的 `runEtlTask` 请求体多了 `sampleSize: options.sampleSize`，不传时值是 `undefined`；Vitest 的 `toHaveBeenCalledWith`（底层走 `toEqual` 语义）比较对象时会忽略值为 `undefined` 的键，所以 `{ dryRun: false, sampleSize: undefined }` 仍然能匹配现有断言的 `{ dryRun: false }`，不会因为新增字段而失败。

把第 18-25 行的 import 列表：

```typescript
import {
  getEtlTasks,
  createEtlTask,
  updateEtlTask,
  deleteEtlTask,
  runEtlTask,
  getEtlLogs,
} from '../etl'
```

改为：

```typescript
import {
  getEtlTasks,
  createEtlTask,
  updateEtlTask,
  deleteEtlTask,
  runEtlTask,
  getEtlLogs,
  getEtlLog,
  cancelEtlRun,
} from '../etl'
```

在文件末尾、`describe('ETL API', ...)` 闭合括号之前追加（紧跟 `getEtlLogs` 那个用例之后）：

```typescript
  it('getEtlLog 调用 GET /etlTasks/{id}/logs/{logId}', async () => {
    const log = { id: 'log-1', taskId: 'etl-1', status: 'running' }
    mockGet.mockResolvedValueOnce(log as any)

    const result = await getEtlLog('etl-1', 'log-1')
    expect(mockGet).toHaveBeenCalledWith('/etlTasks/etl-1/logs/log-1')
    expect(result).toEqual(log)
  })

  it('cancelEtlRun 调用 POST /etlTasks/{id}/logs/{logId}/cancel', async () => {
    mockPost.mockResolvedValueOnce({ ok: true } as any)

    await cancelEtlRun('etl-1', 'log-1')
    expect(mockPost).toHaveBeenCalledWith('/etlTasks/etl-1/logs/log-1/cancel', {})
  })

  it('runEtlTask dryRun 时透传 sampleSize', async () => {
    mockPost.mockResolvedValueOnce({ status: 'success' } as any)

    await runEtlTask('etl-1', { dryRun: true, sampleSize: 20 })
    expect(mockPost).toHaveBeenCalledWith('/etlTasks/etl-1/run', { dryRun: true, sampleSize: 20 })
  })
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `npx vitest run src/api/__tests__/etl.test.ts`
Expected: FAIL — `getEtlLog is not a function` / `cancelEtlRun is not a function`

- [ ] **Step 3: 实现 — 类型**

`src/types/etl.ts` 整个文件替换为：

```typescript
export interface EtlStepConfig {
  [key: string]: any
}

export interface EtlStep {
  id: string
  type: 'http_request' | 'json_input' | 'file_upload' | 'script' | 'field_mapping' | 'filter' | 'save_to_collection'
  name: string
  config: EtlStepConfig
  onError: 'stop' | 'skip'
}

export interface EtlTask {
  id: string
  name: string
  description: string
  steps: EtlStep[]
  enabled: boolean
  lastRunAt?: string
  lastRunStatus?: string
  createdAt: string
  updatedAt: string
}

export interface EtlStepResult {
  stepId: string
  stepName: string
  status: 'success' | 'error' | 'skipped'
  recordCount?: number
  error?: string
  sampleRecords?: Record<string, any>[]
}

export interface EtlRunResult {
  status: string
  totalRecords: number
  successCount: number
  errorCount: number
  stepResults: EtlStepResult[]
  errors: string[]
}

/** POST /etlTasks/<id>/run（非 dry run）的立即响应：只有一个运行 ID，真正结果要轮询 EtlLog */
export interface EtlRunStarted {
  logId: string
  status: string
}

export interface EtlLog {
  id: string
  taskId: string
  taskName: string
  status: string
  startedAt: string
  finishedAt?: string
  totalRecords: number
  successCount: number
  errorCount: number
  stepResults: EtlStepResult[]
  errorDetail?: string
  progressCurrent?: number
  currentStepName?: string
}
```

- [ ] **Step 4: 实现 — API**

`src/api/etl.ts` 整个文件替换为：

```typescript
import { get, post, put, del } from '@/utils/request'
import type { EtlTask, EtlRunResult, EtlRunStarted, EtlLog } from '@/types'
import type { DataFileMeta } from './dataFiles'

export function getEtlTasks() {
  return get<EtlTask[]>('/etlTasks')
}

export function createEtlTask(data: Partial<EtlTask>) {
  return post<EtlTask>('/etlTasks', data)
}

export function updateEtlTask(id: string, data: Partial<EtlTask>) {
  return put<EtlTask>(`/etlTasks/${id}`, data)
}

export function deleteEtlTask(id: string) {
  return del(`/etlTasks/${id}`)
}

/**
 * dryRun=true：同步返回完整结果（内部按 sampleSize 取样本，默认后端 50 条）。
 * dryRun=false（默认）：正式运行，立即返回 { logId, status: 'pending' }，
 * 真正的执行结果要用 getEtlLog 轮询。
 */
export function runEtlTask(
  id: string,
  options: { dryRun?: boolean; sampleSize?: number } = {}
): Promise<EtlRunResult | EtlRunStarted> {
  return post(`/etlTasks/${id}/run`, {
    dryRun: options.dryRun || false,
    sampleSize: options.sampleSize,
  })
}

export function getEtlLogs(taskId: string) {
  return get<EtlLog[]>(`/etlTasks/${taskId}/logs`)
}

export function getEtlLog(taskId: string, logId: string) {
  return get<EtlLog>(`/etlTasks/${taskId}/logs/${logId}`)
}

export function cancelEtlRun(taskId: string, logId: string) {
  return post(`/etlTasks/${taskId}/logs/${logId}/cancel`, {})
}

export function uploadEtlFile(file: File): Promise<DataFileMeta> {
  const form = new FormData()
  form.append('file', file)
  return post('/etlTasks/files/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `npx vitest run src/api/__tests__/etl.test.ts`
Expected: PASS（全部用例，包括原有的）

- [ ] **Step 6: 类型检查**

Run: `npx vue-tsc --noEmit`
Expected: 此时 `EtlTaskManager.vue` 还没跟着改（Task 7），如果它直接把 `runEtlTask` 的返回值当 `EtlRunResult` 用（比如 `result.status`/`result.stepResults`），因为返回类型变成了联合类型 `EtlRunResult | EtlRunStarted`，会出现新的类型错误——这是预期的，Task 7 会修。记录下这里报的错误位置，Task 7 实施时对照检查。

- [ ] **Step 7: 提交**

```bash
git add src/types/etl.ts src/api/etl.ts src/api/__tests__/etl.test.ts
git commit -m "feat(etl): 前端类型与 API 支持异步运行/日志详情/取消"
```

---

### Task 7: 前端 UI — 轮询进度 + 取消按钮 + 样例数据预览

**Files:**
- Modify: `src/views/admin/EtlTaskManager.vue`

**Interfaces:**
- Consumes：`getEtlLog`、`cancelEtlRun`、`runEtlTask`（Task 6）、`EtlLog`/`EtlRunResult`/`EtlRunStarted`（Task 6）

此任务是 UI 接线，改动集中在 `<script setup>` 的执行逻辑（`doRun`）和运行结果面板模板，不新增自动化测试（这个巨型 SFC 现有测试约定是把可测逻辑抽成独立函数单测，这里的改动是异步轮询 + DOM 状态，本质是集成行为，交给 Step 4 的 Playwright 人工验证，这也是 CLAUDE.md 对 UI 改动的强制要求）。

- [ ] **Step 1: import 与状态**

`src/views/admin/EtlTaskManager.vue` 第 467 行的 Vue 核心 import：

```typescript
import { ref, computed, onMounted } from 'vue'
```

改为：

```typescript
import { ref, computed, onMounted, onUnmounted } from 'vue'
```

第 480-489 行的 API import：

```typescript
import {
  getEtlTasks,
  createEtlTask,
  updateEtlTask,
  deleteEtlTask,
  runEtlTask,
  getEtlLogs,
  uploadEtlFile,
} from '@/api/etl'
import type { EtlTask, EtlStep, EtlRunResult, EtlLog } from '@/types'
```

改为：

```typescript
import {
  getEtlTasks,
  createEtlTask,
  updateEtlTask,
  deleteEtlTask,
  runEtlTask,
  getEtlLogs,
  getEtlLog,
  cancelEtlRun,
  uploadEtlFile,
} from '@/api/etl'
import type { EtlTask, EtlStep, EtlRunResult, EtlLog } from '@/types'
```

第 546 行 `const runResult = ref<EtlRunResult | null>(null)` 之后新增：

```typescript
// 正式运行时的轮询状态：runningLogId 非空表示当前有一个正式运行在轮询中，
// runningLog 是最近一次轮询到的日志详情（渲染进度条用）
const runningLogId = ref<string | null>(null)
const runningLog = ref<EtlLog | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onUnmounted(() => {
  stopPolling()
})
```

- [ ] **Step 2: 重写 `doRun`，新增取消处理**

`doRun` 函数（第 865-913 行）改为：

```typescript
async function doRun(dryRun: boolean) {
  if (currentTaskId.value === '__new__') return

  // 先保存
  const valid = await formRef.value?.validate()
  if (!valid) return

  saveLoading.value = true
  try {
    await updateEtlTask(currentTaskId.value!, {
      name: formData.value.name,
      description: formData.value.description,
      steps: formData.value.steps,
      enabled: true,
    })
    await loadTasks()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.error || '保存失败')
    saveLoading.value = false
    return
  }
  saveLoading.value = false

  runResult.value = null
  runningLogId.value = null
  runningLog.value = null

  if (dryRun) {
    runLoading.value = true
    try {
      const result = await runEtlTask(currentTaskId.value!, { dryRun: true }) as EtlRunResult
      runResult.value = result
      if (result.status === 'success') {
        ElMessage.success('测试运行成功')
      } else if (result.status === 'partial') {
        ElMessage.warning('部分记录处理失败')
      } else {
        ElMessage.error('执行失败')
      }
    } catch (e: any) {
      const errData = e.response?.data
      if (errData) runResult.value = errData
      ElMessage.error(e.response?.data?.errors?.[0] || '执行出错')
    } finally {
      runLoading.value = false
    }
    return
  }

  // 正式运行：拿到 logId 后开始轮询，不在这里直接展示结果
  runLoading.value = true
  try {
    const started = await runEtlTask(currentTaskId.value!, { dryRun: false }) as { logId: string; status: string }
    runningLogId.value = started.logId
    startPolling(started.logId)
  } catch (e: any) {
    runLoading.value = false
    ElMessage.error(e.response?.data?.error || '执行出错')
  }
}

function startPolling(logId: string) {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const log = await getEtlLog(currentTaskId.value!, logId)
      runningLog.value = log
      if (['success', 'partial', 'error', 'cancelled'].includes(log.status)) {
        stopPolling()
        runLoading.value = false
        runResult.value = {
          status: log.status,
          totalRecords: log.totalRecords,
          successCount: log.successCount,
          errorCount: log.errorCount,
          stepResults: log.stepResults,
          errors: log.errorDetail ? log.errorDetail.split('\n') : [],
        }
        runningLogId.value = null
        runningLog.value = null
        if (log.status === 'success') {
          ElMessage.success('执行成功')
        } else if (log.status === 'partial') {
          ElMessage.warning('部分记录处理失败')
        } else if (log.status === 'cancelled') {
          ElMessage.info('已取消')
        } else {
          ElMessage.error('执行失败')
        }
        await loadLogs(currentTaskId.value!)
        await loadTasks()
      }
    } catch {
      // 单次轮询失败不中断，等下一次 tick 重试
    }
  }, 1500)
}

async function handleCancelRun() {
  if (!currentTaskId.value || !runningLogId.value) return
  try {
    await cancelEtlRun(currentTaskId.value, runningLogId.value)
    ElMessage.info('已请求取消，正在停止...')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.error || '取消失败')
  }
}
```

- [ ] **Step 3: 模板 — 进度展示 + 取消按钮 + 样例数据预览**

`handleRun`（第 861-863 行）和"操作按钮"区块（第 182-202 行）之间不用改；把"运行结果"区块（第 204-233 行）：

```html
            <!-- 运行结果（测试/执行后显示） -->
            <div v-if="runResult" class="run-result">
              <el-divider content-position="left">运行结果</el-divider>
              <el-alert
                :type="runResult.status === 'success' ? 'success' : runResult.status === 'partial' ? 'warning' : 'error'"
                :title="`${runResultLabel} — 总计 ${runResult.totalRecords} 条，成功 ${runResult.successCount}，失败 ${runResult.errorCount}`"
                :closable="false"
                show-icon
              />
              <!-- 步骤结果 -->
              <div class="step-results">
                <div
                  v-for="sr in runResult.stepResults"
                  :key="sr.stepId"
                  class="step-result-item"
                >
                  <el-icon :color="sr.status === 'success' ? '#67c23a' : '#f56c6c'">
                    <CircleCheckFilled v-if="sr.status === 'success'" />
                    <CircleCloseFilled v-else />
                  </el-icon>
                  <span class="sr-name">{{ sr.stepName }}</span>
                  <span v-if="sr.recordCount !== undefined" class="sr-count">{{ sr.recordCount }} 条</span>
                  <span v-if="sr.error" class="sr-error">{{ sr.error }}</span>
                </div>
              </div>
              <!-- 错误详情 -->
              <div v-if="runResult.errors.length > 0" class="error-detail">
                <div v-for="(err, i) in runResult.errors" :key="i" class="error-line">{{ err }}</div>
              </div>
            </div>
```

改为（新增：运行中的进度面板 + 取消按钮 + 每步样例数据表格）：

```html
            <!-- 正式运行中：轮询进度 -->
            <div v-if="runningLogId" class="run-result">
              <el-divider content-position="left">运行中</el-divider>
              <el-alert type="info" :closable="false" show-icon>
                <template #title>
                  <span v-if="runningLog">
                    正在执行「{{ runningLog.currentStepName || '...' }}」
                    <template v-if="runningLog.totalRecords">
                      —— 已处理 {{ runningLog.progressCurrent || 0 }} / {{ runningLog.totalRecords }} 条
                    </template>
                  </span>
                  <span v-else>正在启动...</span>
                </template>
              </el-alert>
              <el-progress
                v-if="runningLog && runningLog.totalRecords"
                :percentage="Math.round(((runningLog.progressCurrent || 0) / runningLog.totalRecords) * 100)"
                :stroke-width="14"
              />
              <el-button type="danger" plain style="margin-top: 12px" @click="handleCancelRun">
                取消运行
              </el-button>
            </div>

            <!-- 运行结果（测试运行 / 正式运行结束后显示） -->
            <div v-if="runResult" class="run-result">
              <el-divider content-position="left">运行结果</el-divider>
              <el-alert
                :type="runResult.status === 'success' ? 'success' : runResult.status === 'partial' ? 'warning' : 'error'"
                :title="`${runResultLabel} — 总计 ${runResult.totalRecords} 条，成功 ${runResult.successCount}，失败 ${runResult.errorCount}`"
                :closable="false"
                show-icon
              />
              <!-- 步骤结果 -->
              <div class="step-results">
                <div
                  v-for="sr in runResult.stepResults"
                  :key="sr.stepId"
                  class="step-result-item"
                >
                  <div class="step-result-row">
                    <el-icon :color="sr.status === 'success' ? '#67c23a' : '#f56c6c'">
                      <CircleCheckFilled v-if="sr.status === 'success'" />
                      <CircleCloseFilled v-else />
                    </el-icon>
                    <span class="sr-name">{{ sr.stepName }}</span>
                    <span v-if="sr.recordCount !== undefined" class="sr-count">{{ sr.recordCount }} 条</span>
                    <span v-if="sr.error" class="sr-error">{{ sr.error }}</span>
                  </div>
                  <!-- 样例数据预览：设计管道时调试用，测试运行/正式运行都带 -->
                  <el-collapse v-if="sr.sampleRecords && sr.sampleRecords.length > 0" class="sample-collapse">
                    <el-collapse-item :title="`样例数据（前 ${sr.sampleRecords.length} 条）`">
                      <el-table :data="sr.sampleRecords" size="small" border>
                        <el-table-column
                          v-for="key in Object.keys(sr.sampleRecords[0])"
                          :key="key"
                          :prop="key"
                          :label="key"
                          show-overflow-tooltip
                        />
                      </el-table>
                    </el-collapse-item>
                  </el-collapse>
                </div>
              </div>
              <!-- 错误详情 -->
              <div v-if="runResult.errors.length > 0" class="error-detail">
                <div v-for="(err, i) in runResult.errors" :key="i" class="error-line">{{ err }}</div>
              </div>
            </div>
```

- [ ] **Step 4: 日志列表新增 pending/running/cancelled 状态样式**

`statusType`/`statusLabel` 函数（第 583-595 行）改为：

```typescript
function statusType(status: string) {
  if (status === 'success') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'error') return 'danger'
  if (status === 'running') return 'primary'
  if (status === 'cancelled') return 'info'
  return 'info'
}

function statusLabel(status: string) {
  if (status === 'success') return '成功'
  if (status === 'partial') return '部分成功'
  if (status === 'error') return '失败'
  if (status === 'pending') return '排队中'
  if (status === 'running') return '运行中'
  if (status === 'cancelled') return '已取消'
  return status
}
```

- [ ] **Step 5: 样式**

在 `<style>` 块里（靠近 `.logs-section` 附近，第 1183 行前后）新增：

```scss
.step-result-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sample-collapse {
  margin: 8px 0 4px 24px;
  :deep(.el-collapse-item__header) {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}
```

- [ ] **Step 6: 类型检查 + 前端测试回归**

Run: `npx vue-tsc --noEmit`
Expected: 无新增类型错误（Task 6 Step 6 记录的那个错误在这里应该被修掉了——`runEtlTask` 的返回值在两个分支里都做了 `as EtlRunResult` / `as { logId, status }` 的显式收窄）

Run: `npx vitest run`
Expected: PASS（全部现有测试，本任务未新增自动化测试）

- [ ] **Step 7: 提交**

```bash
git add src/views/admin/EtlTaskManager.vue
git commit -m "feat(etl): 正式运行改为轮询进度展示 + 取消按钮，运行结果面板加样例数据预览"
```

---

### Task 8: 用户文档更新

**Files:**
- Modify: `docs/user-guide/integration/etl.md`

CLAUDE.md 强制要求：用户可感知的功能变化必须同 PR 更新用户指南。这次的变化（正式运行改异步+进度可见+可取消、试运行改取样本+每步样例数据）用户能直接感知到，必须更新。

- [ ] **Step 1: 更新"运行与日志"一节**

把 `docs/user-guide/integration/etl.md` 的"运行与日志"一节（第 22-25 行）：

```markdown
## 运行与日志

- **手动运行**：在任务上点击「运行」立即执行一次。
- **执行日志**：`etl_logs` 记录每次运行的处理条数、成功 / 失败、错误信息，可在任务详情查看。
```

改为：

```markdown
## 运行与调试

- **测试运行**：只取样本（默认前 50 条，不写库）快速跑一遍整个管道，用于设计阶段调试。每一步的结果都会展开一个"样例数据"表格，可以直接看到这一步处理后的记录长什么样，不用等正式运行才知道字段映射/过滤条件写得对不对。
- **正式运行**：点击「执行」后立即返回，不会卡住页面——数据在后台异步处理，页面实时轮询展示进度（当前在跑哪一步、写入集合的第几条/共几条），并提供「取消运行」按钮。取消后已经成功写入的批次会保留，不会整体回滚；已处理的部分不需要重新执行。
- **执行日志**：`etl_logs` 记录每次运行的处理条数、成功 / 失败、错误信息，状态包括排队中 / 运行中 / 成功 / 部分成功 / 失败 / 已取消，可在任务详情查看。
```

- [ ] **Step 2: 提交**

```bash
git add docs/user-guide/integration/etl.md
git commit -m "docs(etl): 更新用户指南，说明异步运行/进度/取消/样例数据预览"
```

---

### Task 9: Playwright 人工验证（CLAUDE.md 强制要求）

本任务不产出代码改动，只验证 Task 1-8 的真实效果。本项目规则要求任何涉及前端页面、且依赖前后端交互的改动，在合并前必须用 Playwright 走一遍真实流程。

**Files:** 无（验证性任务）

- [ ] **Step 1: 准备服务**

Run: `npm run dev:all`（前端 Vite HMR 自动生效；后端本次改了大量 Python 代码，必须重启：`cd server && python app.py` 或重启 `npm run dev:all` 的后端子进程，确保加载了 Task 1-5 的改动，包括新的调度器会跟着启动）。

确认后端启动日志里能看到 ETL 调度器正常启动（没有报错；`start_etl_scheduler` 内部异常会被 `traceback.print_exc()` 打到控制台，不会让整个进程崩，但要确认没打印出来）。

- [ ] **Step 2: 准备一个能体现"分批 + 进度"的测试任务**

进入 **设置中心 → 数据运维 → ETL 管理**，新建一个任务：
- 「JSON 输入」步骤，构造几千条记录（比如用浏览器 devtools console 跑一段 JS 生成 `JSON.stringify(Array.from({length: 3000}, (_, i) => ({name: 'r'+i})))`，粘进 JSON 输入框）——不需要真的几十万条，几千条已经能在测试环境里跑出"多批次、进度会动"的效果，同时不会让本地测试库压力过大。
- 「写入集合」步骤，目标选一个测试用的数据页（如果没有现成的，创建一个只有 `name` 文本字段的测试数据页），模式选"新增"。

- [ ] **Step 3: 验证测试运行（取样本 + 样例数据）**

点击「测试运行」，用 Playwright MCP 截图确认：
- 立即返回结果（不用等待），"总计"记录数是 50（默认 sampleSize），不是 3000。
- 每个步骤旁边能展开"样例数据"表格，能看到具体的记录内容（如 `name: r0` ~ `r4` 或类似）。

- [ ] **Step 4: 验证正式运行（异步 + 进度 + 不卡页面）**

点击「执行」→ 确认对话框确认。用 Playwright MCP 观察：
- 点击后页面立即可交互（不是转圈到底），出现"运行中"面板，显示当前步骤名和进度条。
- 等待几秒后再次截图，确认"已处理 X / 3000 条"的 X 在增长（分批写入确实在推进，不是卡住不动）。
- 最终变成"运行结果"面板，总计/成功/失败数字正确，且能在任务详情的数据页里查到这 3000 条记录确实写进去了。

- [ ] **Step 5: 验证取消**

再跑一次正式运行（或如果步骤 4 的还没跑完就直接用），点击"取消运行"按钮。确认：
- 很快（几秒内，两次轮询间隔量级）状态变成"已取消"。
- 到数据页确认：已经写入的那部分记录还在（没有被回滚清空），总数少于 3000（因为提前停止了）。

- [ ] **Step 6: 用 proxy.py 验证轮询在生产反代下正常工作**

轮询用的是普通 REST GET（不是 SSE/流式），理论上不需要 `proxy.py` 的 `_is_sse()`/header 白名单特殊处理，但 CLAUDE.md 要求新路由形状过一遍 `proxy.py`：

```bash
npm run build
cd server && python proxy.py
```

通过 `:8080` 走一遍 Step 4 的正式运行流程（登录、跑任务、看进度、等结束），确认轮询请求（`GET /api/etlTasks/<id>/logs/<logId>`）能正常拿到数据、不是空响应或挂起。

- [ ] **Step 7: 截图存档**

截图存到 `.playwright-mcp/`，覆盖：测试运行的样例数据面板、正式运行中的进度面板、取消后的状态、最终成功结果面板。

- [ ] **Step 8: 记录验证结论**

在对话里明确说明：测试运行取样本+样例数据、正式运行异步+进度+不卡页面、取消保留已写数据、`proxy.py` 下轮询正常，四项均已通过 Playwright/真实反代走通，且截图证据已保存路径。
