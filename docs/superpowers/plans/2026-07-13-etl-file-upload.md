# ETL 文件上传解析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ETL 任务新增 `file_upload` 步骤类型，支持在配置步骤时上传一个 Excel/CSV 文件并固定绑定，之后每次运行都读取解析同一份文件产出记录，供后续管道步骤（字段映射/过滤/写入集合等）消费。

**Architecture:** 复用现成的 `data_files` 表 + `save_data_file()` 存储 helper（新增一个专属上传路由，权限门禁与 ETL 其它路由一致，而非复用数据页语义的 `/data-files/upload`）；`etl_engine.py` 新增一个数据源步骤类型 `_step_file_upload`，用 `pandas` 读取 csv/xlsx 落成 `context['records']`，与既有 `_step_json_input` 完全同构（都是"config 里存好的静态输入，每次运行重新解析"）。前端在 `EtlTaskManager.vue` 里新增该步骤类型的 UI（步骤类型列表项 + 步骤配置表单里的上传控件）。

**Tech Stack:** Flask + psycopg2（后端 ETL 路由/引擎）、pandas + openpyxl（Excel/CSV 解析，均已是现有依赖）、Vue 3 + TypeScript + Element Plus（`EtlTaskManager.vue`）、pytest（后端单测）。

参考设计文档：`docs/superpowers/specs/2026-07-13-etl-file-upload-design.md`（已经用户确认）。

## Global Constraints

- 绑定方式固定为"配置时上传并固定"——不支持每次运行重新选文件（用户已确认）。
- 仅支持 `.csv` / `.xlsx` / `.xls` 三种扩展名；不支持的扩展名在**上传时**（`400`）和**执行时**（`ValueError`）都要能给出清晰错误，两处独立校验，互不依赖。
- 复用 `data_files` 表/`save_data_file()`，不新建表，不重复实现存储/下载逻辑。
- 新路由的权限门禁必须是 `@require_permission('admin.etl_tasks')`（与 `etl_tasks_bp` 下所有既有路由一致），不是 `/data-files/upload` 那种页面级 CRUD 权限模型。
- `_step_file_upload(config, context, conn)` 的参数顺序、`_execute_step` 的分发签名必须与现有 5 个步骤类型保持一致（`_execute_step(step_type, config, context, conn, dry_run)` 已经把 `conn` 传给所有步骤，不需要改这个函数签名本身）。
- 前端新增的类型/函数/模板块必须严格复用既有代码风格：`STEP_TYPES`/`STEP_ICONS`/`STEP_COLORS`/`createDefaultConfig`/`getStepSummary` 都是并列的 5 项 switch/数组，新增第 6 项，不重构既有结构。
- pandas 读出的 `NaN`（空单元格）必须转成 `None` 再进 `context['records']`——`psycopg2.extras.Json` 序列化 `NaN` 会产出非法 JSON（`NaN` 不是合法 JSON token），后续 `save_to_collection` 步骤写库时会静默出错或写入损坏数据。
- 不做的事（本次非目标，不要顺手实现）：多 sheet 选择、运行时重新上传、解析预览。

---

### Task 1: 后端 ETL 引擎 — `file_upload` 步骤类型

**Files:**
- Modify: `server/utils/etl_engine.py`
- Test: `server/tests/test_etl_engine.py`

**Interfaces:**
- Produces: `_step_file_upload(config: dict, context: dict, conn) -> None`（写入 `context['records']`，无返回值，与其余 `_step_*` 函数同构）；`_execute_step` 新增对 `'file_upload'` 类型的分发。

- [ ] **Step 1: 在 `server/utils/etl_engine.py` 顶部加 `import pandas as pd`**

修改文件头部的 import 区块（第 10-17 行），在 `import psycopg2.extras` 之后加一行：

```python
import json
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
import psycopg2.extras
import pandas as pd

from utils.script_runner import run_etl_script
```

- [ ] **Step 2: 在 `_execute_step` 分发表中新增 `file_upload` 分支**

当前函数（第 81-96 行）：

```python
def _execute_step(step_type, config, context, conn, dry_run):
    """根据步骤类型分发执行。"""
    if step_type == 'http_request':
        _step_http_request(config, context)
    elif step_type == 'json_input':
        _step_json_input(config, context)
    elif step_type == 'script':
        _step_script(config, context)
    elif step_type == 'field_mapping':
        _step_field_mapping(config, context)
    elif step_type == 'filter':
        _step_filter(config, context)
    elif step_type == 'save_to_collection':
        _step_save_to_collection(config, context, conn, dry_run)
    else:
        raise ValueError(f'未知的步骤类型: {step_type}')
```

改为（在 `json_input` 分支后插入 `file_upload` 分支，保持其余不变）：

```python
def _execute_step(step_type, config, context, conn, dry_run):
    """根据步骤类型分发执行。"""
    if step_type == 'http_request':
        _step_http_request(config, context)
    elif step_type == 'json_input':
        _step_json_input(config, context)
    elif step_type == 'file_upload':
        _step_file_upload(config, context, conn)
    elif step_type == 'script':
        _step_script(config, context)
    elif step_type == 'field_mapping':
        _step_field_mapping(config, context)
    elif step_type == 'filter':
        _step_filter(config, context)
    elif step_type == 'save_to_collection':
        _step_save_to_collection(config, context, conn, dry_run)
    else:
        raise ValueError(f'未知的步骤类型: {step_type}')
```

- [ ] **Step 3: 新增 `_step_file_upload` 函数**

紧跟在 `_step_json_input` 函数之后（该函数目前在第 176-189 行结尾）插入：

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

    # NaN（空单元格）转 None，否则写入 dynamic_data 时 JSON 序列化会产出非法值
    df = df.where(pd.notnull(df), None)
    context['records'] = df.to_dict('records')
```

- [ ] **Step 4: 编写单测**

在 `server/tests/test_etl_engine.py` 顶部的 import 列表（第 14-23 行）里加入 `_step_file_upload`：

```python
from utils.etl_engine import (
    _resolve_path,
    _step_json_input,
    _step_file_upload,
    _step_field_mapping,
    _step_filter,
    _step_script,
    _step_save_to_collection,
    _execute_step,
    execute_task,
)
```

在文件顶部再加两个测试专用 import（放在现有 `import pytest`/`from unittest.mock import MagicMock` 之后）：

```python
import csv
import tempfile
import os as _os
from openpyxl import Workbook
```

在 `class TestJsonInput:` 之后（该类结束于第 85 行左右，下一个是 `# ==================== field_mapping ====================` 分隔注释）插入新的测试类：

```python
# ==================== file_upload ====================

class TestFileUpload:
    def _mock_conn_for_file(self, original_name, storage_path):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (original_name, storage_path)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    def test_missing_file_id(self):
        ctx = {'records': []}
        with pytest.raises(ValueError, match='未上传文件'):
            _step_file_upload({}, ctx, MagicMock())

    def test_file_id_not_found(self):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        ctx = {'records': []}
        with pytest.raises(ValueError, match='文件不存在或已被删除'):
            _step_file_upload({'fileId': 'missing'}, ctx, mock_conn)

    def test_unsupported_extension(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b'hello')
            path = f.name
        try:
            mock_conn = self._mock_conn_for_file('data.txt', path)
            ctx = {'records': []}
            with pytest.raises(ValueError, match='不支持的文件格式'):
                _step_file_upload({'fileId': 'f1'}, ctx, mock_conn)
        finally:
            _os.remove(path)

    def test_parses_csv_with_empty_cell_as_none(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['name', 'age'])
            writer.writerow(['张三', '25'])
            writer.writerow(['李四', ''])
            path = f.name
        try:
            mock_conn = self._mock_conn_for_file('数据.csv', path)
            ctx = {'records': []}
            _step_file_upload({'fileId': 'f1'}, ctx, mock_conn)
            assert len(ctx['records']) == 2
            assert ctx['records'][0]['name'] == '张三'
            assert ctx['records'][0]['age'] == 25
            assert ctx['records'][1]['name'] == '李四'
            assert ctx['records'][1]['age'] is None
        finally:
            _os.remove(path)

    def test_parses_xlsx(self):
        wb = Workbook()
        ws = wb.active
        ws.append(['name', 'age'])
        ws.append(['张三', 25])
        ws.append(['李四', 30])
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
            path = f.name
        wb.save(path)
        try:
            mock_conn = self._mock_conn_for_file('数据.xlsx', path)
            ctx = {'records': []}
            _step_file_upload({'fileId': 'f1'}, ctx, mock_conn)
            assert len(ctx['records']) == 2
            assert ctx['records'][0]['name'] == '张三'
            assert ctx['records'][0]['age'] == 25
            assert ctx['records'][1]['name'] == '李四'
        finally:
            _os.remove(path)
```

- [ ] **Step 5: 运行测试**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_etl_engine.py -v
```

Expected: 全部通过，新增的 `TestFileUpload` 5 个用例 + 既有用例都是 PASS。

- [ ] **Step 6: Commit**

```bash
git add server/utils/etl_engine.py server/tests/test_etl_engine.py
git commit -m "feat(etl): add file_upload step type (Excel/CSV parsing via pandas)"
```

---

### Task 2: 后端上传路由 — `POST /etlTasks/files/upload`

**Files:**
- Modify: `server/routes/etl_tasks.py`
- Test: `server/tests/test_routes_etl.py`

**Interfaces:**
- Consumes: `routes.data_files.save_data_file(f, uploaded_by=None) -> (meta: dict | None, err: (Response, int) | None)`（已存在，见 `server/routes/data_files.py:43`）。
- Produces: `POST /etlTasks/files/upload` → `201 {id, name, size, mimeType, url}`（与 `data_files` 上传路由返回结构一致，供前端 `DataFileMeta` 类型直接复用）。

- [ ] **Step 1: 在 `server/routes/etl_tasks.py` 里加 `g` 导入**

第 10 行当前是：

```python
from flask import Blueprint, request, jsonify
```

改为：

```python
from flask import Blueprint, request, jsonify, g
```

- [ ] **Step 2: 新增上传路由**

在 `TASK_COLS`/`LOG_COLS` 常量定义之后、`list_tasks` 路由之前（现有第 60-64 行之间）插入：

```python
ETL_FILE_UPLOAD_EXTENSIONS = {'csv', 'xlsx', 'xls'}


@etl_tasks_bp.route('/etlTasks/files/upload', methods=['POST'])
@require_permission('admin.etl_tasks')
def upload_etl_file():
    """file_upload 步骤专用的文件上传：落到 data_files 表 + 磁盘（复用既有
    存储 helper），权限门禁与本蓝图其余路由一致（admin.etl_tasks），而非
    /data-files/upload 那套按数据页 CRUD 权限判断的模型。"""
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

- [ ] **Step 3: 编写路由测试**

在 `server/tests/test_routes_etl.py` 顶部 import 区块加入（第 11 行 `from unittest.mock import MagicMock, patch` 已经导入了 `patch`，无需再加；只需新增 `io.BytesIO`）：

```python
from io import BytesIO
```

在文件末尾（`class TestDevRoleAccess:` 之后）新增测试类：

```python
class TestUploadEtlFile:
    def test_missing_file(self, setup):
        client, _, _, headers = setup
        resp = client.post('/etlTasks/files/upload', data={}, content_type='multipart/form-data', headers=headers)
        assert resp.status_code == 400

    def test_unsupported_extension(self, setup):
        client, _, _, headers = setup
        data = {'file': (BytesIO(b'hello'), 'notes.txt')}
        resp = client.post('/etlTasks/files/upload', data=data, content_type='multipart/form-data', headers=headers)
        assert resp.status_code == 400
        assert '不支持的文件格式' in resp.get_json()['error']

    def test_uploads_csv(self, setup):
        client, _, _, headers = setup
        with patch('routes.data_files.save_data_file') as mock_save:
            mock_save.return_value = (
                {'id': 'df-1', 'name': 'data.csv', 'size': 10, 'mimeType': 'text/csv',
                 'url': '/api/data-files/df-1/download'},
                None,
            )
            data = {'file': (BytesIO(b'name,age\n\xe5\xbc\xa0\xe4\xb8\x89,25'), 'data.csv')}
            resp = client.post('/etlTasks/files/upload', data=data, content_type='multipart/form-data', headers=headers)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['id'] == 'df-1'
        assert body['name'] == 'data.csv'

    def test_unauthorized_role(self, setup):
        client, _, _, _ = setup
        dev_token = create_token({'id': 'u2', 'username': 'dev', 'role': 'developer'})
        headers = {'Authorization': f'Bearer {dev_token}'}
        data = {'file': (BytesIO(b'a,b\n1,2'), 'data.csv')}
        resp = client.post('/etlTasks/files/upload', data=data, content_type='multipart/form-data', headers=headers)
        assert resp.status_code == 403
```

- [ ] **Step 4: 运行测试**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_etl.py -v
```

Expected: 全部通过，新增的 `TestUploadEtlFile` 4 个用例 + 既有用例都是 PASS。

- [ ] **Step 5: Commit**

```bash
git add server/routes/etl_tasks.py server/tests/test_routes_etl.py
git commit -m "feat(etl): add POST /etlTasks/files/upload route"
```

---

### Task 3: 前端类型 + API 客户端

**Files:**
- Modify: `src/types/etl.ts`
- Modify: `src/api/etl.ts`

**Interfaces:**
- Consumes: `DataFileMeta` from `src/api/dataFiles.ts`（已存在：`{id, name, size, mimeType, url}`）。
- Produces: `uploadEtlFile(file: File): Promise<DataFileMeta>`，供 Task 4 的 `EtlTaskManager.vue` 使用。

- [ ] **Step 1: 扩展 `EtlStep['type']` 联合类型**

`src/types/etl.ts` 第 6-11 行当前：

```typescript
export interface EtlStep {
  id: string
  type: 'http_request' | 'json_input' | 'script' | 'field_mapping' | 'filter' | 'save_to_collection'
  name: string
  config: EtlStepConfig
  onError: 'stop' | 'skip'
}
```

改为：

```typescript
export interface EtlStep {
  id: string
  type: 'http_request' | 'json_input' | 'file_upload' | 'script' | 'field_mapping' | 'filter' | 'save_to_collection'
  name: string
  config: EtlStepConfig
  onError: 'stop' | 'skip'
}
```

- [ ] **Step 2: 新增 `uploadEtlFile` API 函数**

`src/api/etl.ts` 当前顶部：

```typescript
import { get, post, put, del } from '@/utils/request'
import type { EtlTask, EtlRunResult, EtlLog } from '@/types'
```

改为（新增 `DataFileMeta` 类型引入）：

```typescript
import { get, post, put, del } from '@/utils/request'
import type { EtlTask, EtlRunResult, EtlLog } from '@/types'
import type { DataFileMeta } from './dataFiles'
```

在文件末尾（`getEtlLogs` 函数之后）新增：

```typescript
export function uploadEtlFile(file: File): Promise<DataFileMeta> {
  const form = new FormData()
  form.append('file', file)
  return post('/etlTasks/files/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
```

- [ ] **Step 3: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无报错（`EtlStep['type']` 新增的字面量类型不会破坏任何既有 switch/if 分支，因为 TS 的字符串字面量联合类型是加法式扩展，既有代码里对 `step.type` 的比较不受影响）。

- [ ] **Step 4: Commit**

```bash
git add src/types/etl.ts src/api/etl.ts
git commit -m "feat(etl): add file_upload type + uploadEtlFile API client"
```

---

### Task 4: 前端 UI — `EtlTaskManager.vue` 步骤类型接入

**Files:**
- Modify: `src/views/admin/EtlTaskManager.vue`

**Interfaces:**
- Consumes: `uploadEtlFile` (Task 3)、`EtlStep['type']` 新增的 `'file_upload'` (Task 3)。

- [ ] **Step 1: 新增图标/API 导入**

第 445-448 行当前：

```typescript
import {
  Plus, Edit, Delete, Top, Bottom, Right,
  Download, Document, Promotion, Switch, Filter, Upload,
  CircleCheckFilled, CircleCloseFilled,
} from '@element-plus/icons-vue'
```

改为（新增 `UploadFilled`）：

```typescript
import {
  Plus, Edit, Delete, Top, Bottom, Right,
  Download, Document, Promotion, Switch, Filter, Upload, UploadFilled,
  CircleCheckFilled, CircleCloseFilled,
} from '@element-plus/icons-vue'
```

第 456-462 行当前：

```typescript
import {
  getEtlTasks,
  createEtlTask,
  updateEtlTask,
  deleteEtlTask,
  runEtlTask,
  getEtlLogs,
} from '@/api/etl'
```

改为（新增 `uploadEtlFile`）：

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
```

再加一个类型导入（放在同一批 import 语句附近，比如紧跟在 `import type { EtlTask, EtlStep, EtlRunResult, EtlLog } from '@/types'` 之后新起一行）：

```typescript
import type { UploadRequestOptions } from 'element-plus'
```

- [ ] **Step 2: `STEP_TYPES`/`STEP_ICONS`/`STEP_COLORS` 新增一项**

第 471-496 行当前：

```typescript
const STEP_TYPES = [
  { value: 'http_request', label: 'HTTP 请求', icon: 'Download' },
  { value: 'json_input', label: 'JSON 输入', icon: 'Document' },
  { value: 'script', label: 'Python 脚本', icon: 'Promotion' },
  { value: 'field_mapping', label: '字段映射', icon: 'Switch' },
  { value: 'filter', label: '条件过滤', icon: 'Filter' },
  { value: 'save_to_collection', label: '写入集合', icon: 'Upload' },
] as const

const STEP_ICONS: Record<string, any> = {
  http_request: Download,
  json_input: Document,
  script: Promotion,
  field_mapping: Switch,
  filter: Filter,
  save_to_collection: Upload,
}

const STEP_COLORS: Record<string, string> = {
  http_request: '#409eff',
  json_input: '#67c23a',
  script: '#e6a23c',
  field_mapping: '#909399',
  filter: '#f56c6c',
  save_to_collection: '#9b59b6',
}
```

改为（在 `json_input` 之后插入 `file_upload`，其余不变）：

```typescript
const STEP_TYPES = [
  { value: 'http_request', label: 'HTTP 请求', icon: 'Download' },
  { value: 'json_input', label: 'JSON 输入', icon: 'Document' },
  { value: 'file_upload', label: '文件上传', icon: 'UploadFilled' },
  { value: 'script', label: 'Python 脚本', icon: 'Promotion' },
  { value: 'field_mapping', label: '字段映射', icon: 'Switch' },
  { value: 'filter', label: '条件过滤', icon: 'Filter' },
  { value: 'save_to_collection', label: '写入集合', icon: 'Upload' },
] as const

const STEP_ICONS: Record<string, any> = {
  http_request: Download,
  json_input: Document,
  file_upload: UploadFilled,
  script: Promotion,
  field_mapping: Switch,
  filter: Filter,
  save_to_collection: Upload,
}

const STEP_COLORS: Record<string, string> = {
  http_request: '#409eff',
  json_input: '#67c23a',
  file_upload: '#20a0ff',
  script: '#e6a23c',
  field_mapping: '#909399',
  filter: '#f56c6c',
  save_to_collection: '#9b59b6',
}
```

- [ ] **Step 3: `getStepSummary` 新增分支 + `formatFileSize` helper**

第 580-600 行当前：

```typescript
function getStepSummary(step: EtlStep): string {
  const c = step.config
  switch (step.type) {
    case 'http_request':
      return `${c.method || 'GET'} ${c.url || '(未配置)'}`
    case 'json_input':
      return c.data ? `${c.data.length} 字符 JSON` : '(未配置)'
    case 'script':
      return c.script ? `Python 脚本 (${c.script.split('\n').length} 行)` : '(未配置)'
    case 'field_mapping': {
      const count = (c.mappings || []).length
      return count > 0 ? `${count} 个字段映射` : '(未配置)'
    }
    case 'filter':
      return c.expression || '(未配置)'
    case 'save_to_collection':
      return c.collection ? `→ ${c.collection} (${c.mode || 'insert'})` : '(未配置)'
    default:
      return ''
  }
}
```

改为（新增 `file_upload` 分支）：

```typescript
function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function getStepSummary(step: EtlStep): string {
  const c = step.config
  switch (step.type) {
    case 'http_request':
      return `${c.method || 'GET'} ${c.url || '(未配置)'}`
    case 'json_input':
      return c.data ? `${c.data.length} 字符 JSON` : '(未配置)'
    case 'file_upload':
      return c.fileName ? `${c.fileName}（${formatFileSize(c.fileSize)}）` : '(未上传文件)'
    case 'script':
      return c.script ? `Python 脚本 (${c.script.split('\n').length} 行)` : '(未配置)'
    case 'field_mapping': {
      const count = (c.mappings || []).length
      return count > 0 ? `${count} 个字段映射` : '(未配置)'
    }
    case 'filter':
      return c.expression || '(未配置)'
    case 'save_to_collection':
      return c.collection ? `→ ${c.collection} (${c.mode || 'insert'})` : '(未配置)'
    default:
      return ''
  }
}
```

- [ ] **Step 4: `createDefaultConfig` 新增分支**

第 609-626 行当前：

```typescript
function createDefaultConfig(type: string): Record<string, any> {
  switch (type) {
    case 'http_request':
      return { url: '', method: 'GET', headers: [], body: '', responsePath: '' }
    case 'json_input':
      return { data: '[\n  \n]' }
    case 'script':
      return { script: '# records: list[dict] — 当前记录列表\n# 须设置 result 变量为转换后的列表\n\nresult = records\n' }
    case 'field_mapping':
      return { mappings: [{ source: '', target: '' }], keepUnmapped: false }
    case 'filter':
      return { expression: '' }
    case 'save_to_collection':
      return { collection: '', mode: 'insert', matchField: '' }
    default:
      return {}
  }
}
```

改为（新增 `file_upload` 分支）：

```typescript
function createDefaultConfig(type: string): Record<string, any> {
  switch (type) {
    case 'http_request':
      return { url: '', method: 'GET', headers: [], body: '', responsePath: '' }
    case 'json_input':
      return { data: '[\n  \n]' }
    case 'file_upload':
      return { fileId: null, fileName: null, fileSize: null }
    case 'script':
      return { script: '# records: list[dict] — 当前记录列表\n# 须设置 result 变量为转换后的列表\n\nresult = records\n' }
    case 'field_mapping':
      return { mappings: [{ source: '', target: '' }], keepUnmapped: false }
    case 'filter':
      return { expression: '' }
    case 'save_to_collection':
      return { collection: '', mode: 'insert', matchField: '' }
    default:
      return {}
  }
}
```

- [ ] **Step 5: 新增步骤配置表单的上传控件**

在模板里的 `<!-- JSON 输入 -->` 块（第 320-331 行）之后、`<!-- Python 脚本 -->` 块（第 333 行）之前插入：

```html
          <!-- 文件上传 -->
          <template v-if="editingStep.type === 'file_upload'">
            <el-form-item label="数据文件">
              <el-upload
                :limit="1"
                :show-file-list="false"
                :before-upload="beforeEtlFileUpload"
                :http-request="handleEtlFileUpload"
                accept=".xlsx,.xls,.csv"
              >
                <el-button type="primary">
                  <el-icon><UploadFilled /></el-icon>
                  {{ editingStep.config.fileName ? '重新上传' : '选择文件' }}
                </el-button>
              </el-upload>
              <div v-if="editingStep.config.fileName" class="form-tip">
                已上传：{{ editingStep.config.fileName }}（{{ formatFileSize(editingStep.config.fileSize) }}）
              </div>
              <div class="form-tip">支持 .xlsx / .xls / .csv，首行作为字段名。上传后固定绑定，之后每次运行都解析同一份文件</div>
            </el-form-item>
          </template>
```

- [ ] **Step 6: 新增上传处理函数**

紧跟在 `generateStepId` 函数（第 628-630 行）之后插入：

```typescript
function beforeEtlFileUpload(file: File): boolean {
  const ext = file.name.toLowerCase().split('.').pop() || ''
  if (!['xlsx', 'xls', 'csv'].includes(ext)) {
    ElMessage.error('仅支持 .xlsx / .xls / .csv 文件')
    return false
  }
  return true
}

async function handleEtlFileUpload(options: UploadRequestOptions): Promise<void> {
  if (!editingStep.value) return
  try {
    const res = await uploadEtlFile(options.file as File)
    editingStep.value.config.fileId = res.id
    editingStep.value.config.fileName = res.name
    editingStep.value.config.fileSize = res.size
    if (options.onSuccess) options.onSuccess(res)
    ElMessage.success('上传成功')
  } catch (err: any) {
    if (options.onError) options.onError(err)
    ElMessage.error(err?.message || '上传失败')
  }
}
```

- [ ] **Step 7: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无报错。

- [ ] **Step 8: Playwright 现场验证（仓库 CLAUDE.md 强制要求：涉及 UI 的改动必须过一遍真实浏览器）**

1. `npm run dev:all` 启动前后端（若已在跑，用改动后的代码重启 Flask —— Vite 侧走 HMR 即可）。
2. 浏览器登录 admin/admin123，进入 **设置中心 → 数据运维 → ETL 管理**。
3. 新建或编辑一个 ETL 任务，在步骤列表里通过下拉新增一个"文件上传"步骤，确认弹窗标题/图标正确显示。
4. 在步骤配置里点击"选择文件"，上传一个真实的 `.csv`（可现造一个 2-3 行、含一个空单元格的文件）；确认上传成功后显示文件名+大小，且步骤摘要（`getStepSummary`）在关闭弹窗后正确显示。
5. 尝试上传一个 `.txt` 文件，确认前端 `beforeEtlFileUpload` 拦截（不发请求）且弹出错误提示。
6. 保存任务，点击"测试运行"（dry run），确认运行结果面板显示该步骤 `recordCount` 与文件行数一致（空单元格那一行的对应字段应为空但记录本身仍计入总数）。
7. 上传一个 `.xlsx`（可用同样内容另存一份），重复步骤 4/6 确认 xlsx 路径同样工作。
8. 截图关键结果（步骤配置弹窗 + 运行结果面板）存入 `.playwright-mcp/`。

- [ ] **Step 9: Commit**

```bash
git add src/views/admin/EtlTaskManager.vue
git commit -m "feat(etl): wire file_upload step type into EtlTaskManager.vue UI"
```

---

### Task 5: 文档同步

**Files:**
- Modify: `docs/user-guide/integration/etl.md`

**Interfaces:**
- Consumes: 无（纯文档，依赖 Task 1-4 已完成且验证通过）。

- [ ] **Step 1: 补充"文件上传"作为第三种数据来源步骤**

当前第 5-11 行：

```markdown
ETL（抽取 → 转换 → 加载）任务把数据从来源（其它集合或第三方 HTTP 源）抽取出来，经字段映射 / 条件过滤 / 转换后写入目标集合。入口为 **设置中心 → 数据运维（`/admin/data-ops`）→ ETL 管理**，需要管理员权限。

## 任务组成

| 步骤 | 说明 |
|------|------|
| **抽取（Extract）** | 从来源集合或第三方 HTTP 接口读取数据 |
| **转换（Transform）** | 字段映射（源字段 → 目标字段）、条件过滤、值转换 |
| **加载（Load）** | 写入目标集合（`dynamic_data`） |
```

改为（在 Extract 行补充文件上传来源；正文再加一句）：

```markdown
ETL（抽取 → 转换 → 加载）任务把数据从来源（其它集合、第三方 HTTP 源，或直接上传的 Excel/CSV 文件）抽取出来，经字段映射 / 条件过滤 / 转换后写入目标集合。入口为 **设置中心 → 数据运维（`/admin/data-ops`）→ ETL 管理**，需要管理员权限。

## 任务组成

| 步骤 | 说明 |
|------|------|
| **抽取（Extract）** | 从来源集合、第三方 HTTP 接口读取数据，或上传 Excel（.xlsx/.xls）/ CSV 文件 |
| **转换（Transform）** | 字段映射（源字段 → 目标字段）、条件过滤、值转换 |
| **加载（Load）** | 写入目标集合（`dynamic_data`） |
```

在"创建任务"小节的步骤 2（第 16 行 `2. 配置来源：选择源集合，或填写第三方 HTTP 源地址。`）之后补充一条：

```markdown
   - 若来源是文件，新增"文件上传"步骤并上传 `.xlsx`/`.xls`/`.csv` 文件（首行作为字段名）；文件在配置时上传并固定绑定，之后每次运行该任务都会重新解析同一份文件。
```

- [ ] **Step 2: Commit**

```bash
git add docs/user-guide/integration/etl.md
git commit -m "docs(etl): document the file_upload step type"
```

## Self-Review Notes

- **Spec coverage**: 后端引擎步骤（Task 1）、后端上传路由（Task 2）、前端类型/API（Task 3）、前端 UI 接入（Task 4，含强制 Playwright 验证）、文档同步（Task 5）——设计文档里的每一节都对应到了具体任务。
- **Placeholder scan**：无 TBD/TODO，所有代码块均为完整可运行代码，测试均含真实断言。
- **Type consistency**：`_step_file_upload(config, context, conn)` 参数顺序在 Task 1 的实现、`_execute_step` 调用处、Task 1 测试的直接调用处三处一致；`EtlStep['type']` 新增的 `'file_upload'` 字面量在 Task 3（类型定义）、Task 4（`STEP_TYPES`/`STEP_ICONS`/`STEP_COLORS`/`createDefaultConfig`/`getStepSummary`/模板 v-if/`handleAddStep` 传入的 `type as EtlStep['type']`）各处保持一致拼写。`uploadEtlFile`/`DataFileMeta` 在 Task 3 定义、Task 4 消费处签名一致。
