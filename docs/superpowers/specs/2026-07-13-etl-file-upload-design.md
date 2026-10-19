# ETL 文件上传解析 — 设计

## 背景

ETL 任务（`etl_tasks` 表 + `server/utils/etl_engine.py` + `src/views/admin/EtlTaskManager.vue`）目前的数据来源步骤只有 `http_request`（调用第三方 API）和 `json_input`（手工粘贴 JSON 文本）。用户需要能直接上传 Excel/CSV 文件作为 ETL 管道的数据源。

## 目标

新增一个 `file_upload` 步骤类型，与 `json_input` 并列，作为管道的数据源步骤（产出 `context['records']`）。

## 绑定方式（已与用户确认）

**配置时上传并固定**：编辑步骤时上传一次文件，文件引用（`fileId`）存进 `step.config`；之后每次点击"运行"/"Dry Run"都重新读取、重新解析同一份文件——与 `json_input` 每次运行都重新解析同一段粘贴文本的模型完全一致，改动面最小。不支持"每次运行重新选文件"。

## 支持格式（已与用户确认）

Excel（`.xlsx`/`.xls`，`pandas.read_excel`）+ CSV（`pandas.read_csv`）。首行作为字段名。`pandas`/`openpyxl` 已是后端依赖（`requirements.txt`），无需新增包。不支持上传 JSON 文件（该场景已由 `json_input` 的粘贴文本覆盖）。

## 存储

复用现有 `data_files` 表 + `routes/data_files.py::save_data_file()` helper（该表本是"数据页文件/图片字段上传"的通用存储，非数据页专属；已有磁盘布局、大小限制 `DATA_FILE_MAX_MB`、下载端点，直接复用避免重复实现）。

**不复用 `POST /data-files/upload` 路由本身**——该路由的鉴权模型是"按数据页的 create/update 权限，或非访客兜底"，与 ETL 步骤所处的 `admin.etl_tasks` 管理员能力域不对齐。改为新增一个专属路由，权限门禁与 `etl_tasks_bp` 下其它所有路由一致：

```python
ETL_FILE_UPLOAD_EXTENSIONS = {'csv', 'xlsx', 'xls'}

@etl_tasks_bp.route('/etlTasks/files/upload', methods=['POST'])
@require_permission('admin.etl_tasks')
def upload_etl_file():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file required'}), 400
    ext = f.filename.lower().rsplit('.', 1)[-1] if '.' in f.filename else ''
    if ext not in ETL_FILE_UPLOAD_EXTENSIONS:
        return jsonify({'error': f'不支持的文件格式: .{ext}（仅支持 csv/xlsx/xls）'}), 400
    from routes.data_files import save_data_file
    meta, err = save_data_file(f, uploaded_by=g.current_user['userId'])
    if err:
        return err
    return jsonify(meta), 201
```

（`require_permission` 装饰器同样会设置 `g.current_user`，与 `login_required` 一致，见 `server/auth.py`。扩展名在**上传时**就校验一次——虽然 `_step_file_upload` 执行时也会再查一次扩展名，但那是"配置好之后过了很久才点运行"才会触发，早失败给用户的反馈更及时。）

## 执行引擎

`server/utils/etl_engine.py` 新增 `_step_file_upload(config, context, conn)`，在 `_execute_step` 分发表中接入（该函数已经接收 `conn` 参数，因为 `_step_save_to_collection` 需要）：

```python
def _step_file_upload(config, context, conn):
    """文件上传步骤：读取配置时上传并固定的 Excel/CSV 文件，解析为记录列表。"""
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
    if ext == 'csv':
        df = pd.read_csv(storage_path)
    elif ext in ('xlsx', 'xls'):
        df = pd.read_excel(storage_path)
    else:
        raise ValueError(f'不支持的文件格式: {ext}')

    # NaN（空单元格）转 None，否则 JSON 序列化会失败/产生非法 JSON
    df = df.where(pd.notnull(df), None)
    context['records'] = df.to_dict('records')
```

`_execute_step` 分发新增一支：

```python
elif step_type == 'file_upload':
    _step_file_upload(config, context, conn)
```

文件顶部新增 `import pandas as pd`。

## 前端

*   `src/types/etl.ts`：`EtlStep['type']` 联合类型新增 `'file_upload'`。
*   `src/api/etl.ts`：新增

    ```ts
    export function uploadEtlFile(file: File): Promise<DataFileMeta> {
      const form = new FormData()
      form.append('file', file)
      return post('/etlTasks/files/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    }
    ```

    （复用 `@/api/dataFiles` 已导出的 `DataFileMeta` 类型，不重复定义。）
*   `src/views/admin/EtlTaskManager.vue`：
    *   `STEP_TYPES` 新增 `{ value: 'file_upload', label: '文件上传', icon: 'UploadFilled' }`；`STEP_ICONS`/`STEP_COLORS` 对应补充一条（图标用 `UploadFilled`，与已被 `save_to_collection` 占用的 `Upload` 区分）。
    *   `createDefaultConfig`：`case 'file_upload': return { fileId: null, fileName: null, fileSize: null }`。
    *   `getStepSummary`：`case 'file_upload': return c.fileName ? \`${c.fileName}（${formatFileSize(c.fileSize)}）\` : '(未上传文件)'`（新增一个极小的 `formatFileSize(bytes)` 本地 helper，字节转 KB/MB 两位小数）。
    *   步骤配置区新增一个 `v-if="editingStep.type === 'file_upload'"` 模板块：`el-upload` + `:limit="1"` + 自定义 `:http-request` 调 `uploadEtlFile`（模式参照 `src/components/dynamic-form/controls/FileUpload.vue` 的 `uploadToBackend`），成功后把返回的 `id`/`name`/`size` 写入 `editingStep.config.fileId`/`fileName`/`fileSize`；显示已上传文件名 + 大小 + 一个"重新上传"入口（重新上传即替换 `fileId` 等三个字段）。

## 测试

*   `server/tests/test_etl_engine.py`：新增 `TestFileUpload` 类，用真实的临时 CSV/XLSX 文件（`pandas.to_csv`/`openpyxl` 直接写，而非 mock `pandas.read_*`，更贴近真实解析行为，也是这个测试文件里 `TestSaveToCollection` 之外少数需要真实 I/O 的用例）覆盖：
    *   CSV 正常解析（含一个空单元格 → 记录里对应字段为 `None`）。
    *   XLSX 正常解析。
    *   `fileId` 缺失 → 抛 `ValueError`。
    *   `data_files` 里找不到该 `fileId` → 抛 `ValueError`。
    *   不支持的扩展名（如 `.txt`）→ 抛 `ValueError`。
*   `server/tests/test_routes_etl.py`：新增对 `POST /etlTasks/files/upload` 的路由测试——未授权角色 403、不支持的扩展名（如 `.txt`）400、成功上传返回 201 且透传 `save_data_file` 的返回结构（mock `save_data_file`，不用真的落盘，遵循这个测试文件里对其它路由 mock `get_db`/DB 层的既有风格）。
*   前端：`EtlTaskManager.vue` 目前没有任何既有单测覆盖（其它 5 种步骤类型也都没有），本次新增步骤类型不额外破例引入 Vue Test Utils 覆盖，遵循 YAGNI；UI 走通由 Playwright 现场验证（新增/含 UI 交互的改动是仓库 CLAUDE.md 的强制要求）。

## 文档同步

`docs/user-guide/integration/etl.md` 补充"文件上传"作为第三种数据来源步骤的简短说明（现有文档本就是步骤级概览，非逐步骤详解，不需要大改）。

## 非目标（本次不做）

*   多 sheet Excel 的 sheet 选择（默认读第一个 sheet，与仓库里前端 Excel 导入的既有行为一致）。
*   "每次运行重新上传文件"的运行时选择流程。
*   解析预览（上传后不在步骤编辑器里预览已解析出的记录条数/内容）。
