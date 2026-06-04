# Scheduled AI Row-Processor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A generic, config-driven scheduled pipeline that scans a data page, hands each pending record (fields + attached documents) to an AI session with a prompt/skill, parses the AI's structured JSON, and writes results back into the record while flowing a status field 待处理 → 处理中 → 已处理/处理失败.

**Architecture:** Reuse the existing AI batch engine (`utils/batch_engine.py` `BatchWorker`) for execution — each scan = one batch, each record = one child session. A new scheduler ticks every minute and, for each due task, atomically claims pending records (`FOR UPDATE SKIP LOCKED`, flip to 处理中), stages a per-record context directory, and creates a batch. A one-line completion hook in the batch worker calls back into the scan engine to parse the AI output and write it back to the record.

**Tech Stack:** Python Flask + psycopg2 + APScheduler (backend), pytest (backend tests), Vue 3 + TypeScript + Pinia + Element Plus (frontend), Vitest.

**Spec:** `docs/superpowers/specs/2026-06-03-scheduled-ai-row-processor-design.md`

---

## File Structure

**Backend — created:**
- `server/utils/ai_scan_repo.py` — CRUD for `ai_scan_tasks` (+ in-flight reset on delete)
- `server/utils/ai_scan_engine.py` — claim, context build, prompt assembly, JSON extraction, write-back, orphan sweep, `run_task`
- `server/utils/ai_scan_scheduler.py` — APScheduler tick + due logic
- `server/routes/ai_scan_tasks.py` — `ai_scan_tasks_bp` REST API
- `server/tests/test_ai_scan_engine.py`, `server/tests/test_ai_scan_writeback.py`, `server/tests/test_routes_ai_scan_tasks.py`

**Backend — modified:**
- `server/init_db.py` — DDL `ai_scan_tasks`, ALTER `ai_chat_sessions`, seed menu
- `server/seed_data.py` — seed menu entry
- `server/utils/permissions.py` — add `admin.ai_scan` to `PERMISSION_CATALOG`
- `server/utils/batch_repo.py` — `create_batch` gains `scan_task_id` + per-file `recordId`
- `server/utils/batch_engine.py` — `_prepare_workspace` dir-copy; completion hook in `_run_one`
- `server/routes/dynamic.py` — add `ai-scan-tasks` to `RESERVED`
- `server/app.py` — register `ai_scan_tasks_bp`, start scheduler

**Frontend — created:**
- `src/types/aiScanTask.ts`, `src/api/aiScanTask.ts`, `src/stores/aiScanTask.ts`, `src/views/admin/AiScanTaskManager.vue`, `src/stores/__tests__/aiScanTask.test.ts`

**Frontend — modified:**
- `src/router/index.ts` (route), `src/stores/auth.ts` (`ADMIN_PATH_PERMISSION`)

---

## Phase 0 — Database schema, RBAC key, menu

### Task 0.1: Create `ai_scan_tasks` table + `ai_chat_sessions` columns

**Files:** Modify `server/init_db.py`

- [ ] **Step 1: Add DDL constant** after `AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL` (~line 467):

```python
AI_SCAN_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS ai_scan_tasks (
  id              VARCHAR(100) PRIMARY KEY,
  name            TEXT NOT NULL,
  enabled         BOOLEAN NOT NULL DEFAULT TRUE,
  owner_user_id   VARCHAR(100) NOT NULL REFERENCES users(id),
  collection      VARCHAR(200) NOT NULL,
  branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
  status_field    TEXT NOT NULL,
  pending_value   TEXT NOT NULL DEFAULT '',
  running_value   TEXT NOT NULL DEFAULT '处理中',
  done_value      TEXT NOT NULL DEFAULT '已处理',
  failed_value    TEXT NOT NULL DEFAULT '处理失败',
  extra_filter    JSONB NOT NULL DEFAULT '{}'::jsonb,
  context_fields  JSONB NOT NULL DEFAULT '{}'::jsonb,
  prompt_template TEXT NOT NULL,
  field_mapping   JSONB NOT NULL DEFAULT '[]'::jsonb,
  schedule_interval_minutes INT NOT NULL DEFAULT 15,
  max_records_per_scan      INT NOT NULL DEFAULT 20,
  last_run_at     TIMESTAMPTZ,
  last_scan_count INT DEFAULT 0,
  last_error      TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS scan_task_id     VARCHAR(100) NULL REFERENCES ai_scan_tasks(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_record_id VARCHAR(100) NULL;
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_scan
  ON ai_chat_sessions(scan_task_id, source_record_id);
"""
```

- [ ] **Step 2: Execute it** in `init_db()` after the AI-chat batch columns block (~line 496):

```python
        cur.execute(AI_SCAN_TASKS_DDL)
        conn.commit()
        print("ai_scan_tasks table + ai_chat_sessions scan columns created.")
```

- [ ] **Step 3: Run + verify**

Run: `cd server && python init_db.py`
Then: `cd server && python -c "import psycopg2; from config import DB_CONFIG; c=psycopg2.connect(**DB_CONFIG); cur=c.cursor(); cur.execute(\"SELECT to_regclass('ai_scan_tasks')\"); print(cur.fetchone()[0]); cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='ai_chat_sessions' AND column_name IN ('scan_task_id','source_record_id') ORDER BY 1\"); print([r[0] for r in cur.fetchall()])"`
Expected: `ai_scan_tasks` then `['scan_task_id', 'source_record_id']`

- [ ] **Step 4: Commit**

```bash
git add server/init_db.py
git commit -m "feat(ai-scan): ai_scan_tasks table + ai_chat_sessions scan columns"
```

### Task 0.2: Add `admin.ai_scan` capability + menu

**Files:** Modify `server/utils/permissions.py`, `server/init_db.py`, `server/seed_data.py`

- [ ] **Step 1: Add catalog entry** — in `server/utils/permissions.py`, inside `PERMISSION_CATALOG`, in the `'数据工具'` group (after `admin.etl_tasks`):

```python
    {'key': 'admin.ai_scan',            'label': 'AI 定时任务', 'group': '数据工具'},
```

- [ ] **Step 2: Add seed menu** — in `server/seed_data.py` MENUS list, under 数据工具 (`menu-3-b`):

```python
    {"id": "menu-3-16", "name": "AI 定时任务", "icon": "AlarmClock", "pageId": None, "parentId": "menu-3-b", "order": 6, "path": "/admin/ai-scan-tasks", "roles": ["admin"]},
```

- [ ] **Step 3: Add idempotent menu migration** — in `server/init_db.py`, near the `menu-3-15` migration:

```python
        cur.execute("SELECT id FROM menus WHERE id = 'menu-3-16'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                "VALUES ('menu-3-16', %s, 'AlarmClock', NULL, 'menu-3-b', 6, '/admin/ai-scan-tasks', %s, 'system')",
                ('AI 定时任务', psycopg2.extras.Json(['admin'])),
            )
            conn.commit()
            print("Added AI 定时任务 menu.")
```

- [ ] **Step 4: Run + verify**

Run: `cd server && python init_db.py`
Then: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_permissions.py -q`
Expected: PASS (catalog still valid). Verify menu: `cd server && python -c "import psycopg2; from config import DB_CONFIG; c=psycopg2.connect(**DB_CONFIG); cur=c.cursor(); cur.execute(\"SELECT path FROM menus WHERE id='menu-3-16'\"); print(cur.fetchone())"`
Expected: `('/admin/ai-scan-tasks',)`

- [ ] **Step 5: Commit**

```bash
git add server/utils/permissions.py server/init_db.py server/seed_data.py
git commit -m "feat(ai-scan): admin.ai_scan capability + menu"
```

---

## Phase 1 — Batch engine extensions

### Task 1.1: `_prepare_workspace` copies a directory

**Files:** Modify `server/utils/batch_engine.py`; Test `server/tests/test_ai_scan_engine.py`

- [ ] **Step 1: Write failing test** — create `server/tests/test_ai_scan_engine.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pathlib import Path


def test_prepare_workspace_copies_directory(tmp_path, monkeypatch):
    import utils.batch_engine as eng
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    # stage a directory with two files
    staged = tmp_path / 'scan-staging' / 't1' / 'r1'
    (staged / 'attachments').mkdir(parents=True)
    (staged / 'record.md').write_text('hi', encoding='utf-8')
    (staged / 'attachments' / 'doc.txt').write_text('doc', encoding='utf-8')
    ws = eng._prepare_workspace('user-1', 'sess-1', 'scan-staging/t1/r1')
    up = Path(ws) / 'uploads'
    assert (up / 'record.md').read_text(encoding='utf-8') == 'hi'
    assert (up / 'attachments' / 'doc.txt').read_text(encoding='utf-8') == 'doc'


def test_prepare_workspace_single_file_still_works(tmp_path, monkeypatch):
    import utils.batch_engine as eng
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    (tmp_path / 'batch-staging').mkdir(parents=True)
    (tmp_path / 'batch-staging' / 'f.txt').write_text('x', encoding='utf-8')
    ws = eng._prepare_workspace('u', 's', 'batch-staging/f.txt')
    assert (Path(ws) / 'uploads' / 'f.txt').read_text(encoding='utf-8') == 'x'
```

- [ ] **Step 2: Run — fail**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -k prepare_workspace -v`
Expected: `test_prepare_workspace_copies_directory` FAILS (dir not copied; only `Path(...).name` file copied).

- [ ] **Step 3: Implement** — in `server/utils/batch_engine.py`, replace the body of `_prepare_workspace` (keep signature/docstring):

```python
    ws = create_session_workspace(_workspace_root(), user_id, session_id)
    src = Path(_workspace_root()) / staged_file_path
    up = Path(ws) / 'uploads'
    up.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        # scan-task context directory: copy its whole contents into uploads/
        shutil.copytree(str(src), str(up), dirs_exist_ok=True)
    elif src.exists():
        dst = up / Path(staged_file_path).name
        shutil.copy2(str(src), str(dst))
    return ws
```

- [ ] **Step 4: Run — pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -k prepare_workspace -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_engine.py server/tests/test_ai_scan_engine.py
git commit -m "feat(ai-scan): _prepare_workspace copies a staged context directory"
```

### Task 1.2: `create_batch` stamps scan_task_id + source_record_id

**Files:** Modify `server/utils/batch_repo.py`; Test `server/tests/test_ai_scan_engine.py`

- [ ] **Step 1: Write failing test** — append to `server/tests/test_ai_scan_engine.py`:

```python
from unittest.mock import MagicMock, patch
from contextlib import contextmanager


def _mock_db():
    cur = MagicMock()
    cur.fetchone.side_effect = lambda: {'id': 'x'}
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    @contextmanager
    def fake():
        yield conn
    return fake, cur


def test_create_batch_stamps_scan_columns():
    import utils.batch_repo as repo
    fake, cur = _mock_db()
    with patch('utils.batch_repo.get_db', fake):
        repo.create_batch('user-1', name='n', prompt='p', template_id=None,
                          files=[{'name': 'r1', 'path': 'scan-staging/t/r1', 'recordId': 'rec-1'}],
                          scan_task_id='task-1')
    # the child INSERT must include scan_task_id + source_record_id values
    inserts = [c for c in cur.execute.call_args_list if 'INSERT INTO ai_chat_sessions' in str(c.args[0])]
    assert inserts
    assert any('scan_task_id' in str(c.args[0]) for c in inserts)
    flat = [v for c in inserts for v in (c.args[1] if len(c.args) > 1 else ())]
    assert 'task-1' in flat and 'rec-1' in flat
```

- [ ] **Step 2: Run — fail**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -k create_batch -v`
Expected: FAIL (`create_batch` has no `scan_task_id` kwarg).

- [ ] **Step 3: Implement** — in `server/utils/batch_repo.py`, change `create_batch` signature and the child INSERT. Replace the signature line:

```python
def create_batch(user_id: str, *, name: str, prompt: str,
                 template_id: str | None, files: list[dict],
                 scan_task_id: str | None = None) -> dict:
```

And replace the child-insert loop body with:

```python
            for seq, f in enumerate(files):
                sid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
                    "   scan_task_id, source_record_id) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, seq, f['path'],
                     scan_task_id, f.get('recordId')),
                )
                sessions.append(dict(cur.fetchone()))
```

- [ ] **Step 4: Run — pass + regression**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -k create_batch tests/test_batch_routes.py tests/test_batch_engine.py -q`
Expected: PASS (existing batch tests unaffected — the new columns are nullable; calls without `scan_task_id` pass `None`).

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_repo.py server/tests/test_ai_scan_engine.py
git commit -m "feat(ai-scan): create_batch stamps scan_task_id + source_record_id"
```

### Task 1.3: Completion hook in `_run_one`

**Files:** Modify `server/utils/batch_engine.py`; Test `server/tests/test_ai_scan_engine.py`

- [ ] **Step 1: Write failing test** — append:

```python
def test_run_one_invokes_scan_hook_on_success(monkeypatch):
    import utils.batch_engine as eng
    calls = {}
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **k: '/tmp/ws')
    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-1'
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    w = eng.BatchWorker()
    monkeypatch.setattr(w, '_fetch_batch_prompt', lambda b: 'prompt')
    monkeypatch.setattr(w, '_set_opencode_id', lambda *a: None)
    monkeypatch.setattr(w, '_await_finished', lambda *a, **k: ('preview', {'role': 'assistant', 'content': [{'type': 'text', 'text': 'ok'}]}))
    monkeypatch.setattr(w, '_persist_conversation', lambda *a: None)
    monkeypatch.setattr(w, '_mark_done', lambda *a, **k: None)
    import utils.ai_scan_engine as se
    monkeypatch.setattr(se, 'on_child_finished', lambda row, msg, ok: calls.update(row=row, ok=ok))
    w._run_one({'id': 's1', 'user_id': 'u', 'batch_id': 'b', 'batch_input_file': 'd',
                'scan_task_id': 'task-1', 'source_record_id': 'rec-1'})
    assert calls['ok'] is True and calls['row']['source_record_id'] == 'rec-1'
```

- [ ] **Step 2: Run — fail**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -k run_one_invokes -v`
Expected: FAIL (no hook called; also `utils.ai_scan_engine` may not exist yet — create a stub in Step 3).

- [ ] **Step 3: Implement** — first create a minimal `server/utils/ai_scan_engine.py` stub so the import resolves (full impl in Phase 2):

```python
def on_child_finished(session_row, final_msg, ok):
    """Write-back hook fired by the batch worker for scan-task children.
    Full implementation in Phase 2."""
    return None
```

Then in `server/utils/batch_engine.py`, in `_run_one`, add the hook after `_mark_done` and in BOTH except branches after `_mark_failed`:

```python
            preview, final_msg = self._await_finished(oc_session_id, directory=ws)
            self._persist_conversation(sid, prompt, final_msg)
            self._mark_done(sid, batch_id, last_preview=preview)
            self._notify_scan(session_row, final_msg, ok=True)
        except _SessionTimeout as e:
            self._mark_failed(sid, batch_id, error=f'timeout after {e.seconds}s')
            self._notify_scan(session_row, None, ok=False)
        except Exception as e:
            self._mark_failed(sid, batch_id,
                              error=f'{type(e).__name__}: {e}'[:500])
            self._notify_scan(session_row, None, ok=False)
```

Add the helper method on `BatchWorker` (keeps the hook isolated and exception-safe):

```python
    def _notify_scan(self, session_row, final_msg, ok: bool):
        if not session_row.get('scan_task_id'):
            return
        try:
            from utils.ai_scan_engine import on_child_finished
            on_child_finished(session_row, final_msg, ok=ok)
        except Exception:
            traceback.print_exc()
```

- [ ] **Step 4: Run — pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -k run_one_invokes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_engine.py server/utils/ai_scan_engine.py server/tests/test_ai_scan_engine.py
git commit -m "feat(ai-scan): batch worker completion hook for scan-task children"
```

---

## Phase 2 — Scan engine: context, prompt, JSON, write-back

### Task 2.1: JSON extraction

**Files:** Modify `server/utils/ai_scan_engine.py`; Test `server/tests/test_ai_scan_writeback.py`

- [ ] **Step 1: Write failing test** — create `server/tests/test_ai_scan_writeback.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.ai_scan_engine import extract_json


def test_extract_fenced_json():
    text = "分析如下\n```json\n{\"结论\": \"通过\", \"意见\": \"ok\"}\n```\n谢谢"
    assert extract_json(text) == {'结论': '通过', '意见': 'ok'}


def test_extract_last_balanced_object():
    text = '随便 {\"a\":1} 中间 {\"结论\": \"驳回\"}'
    assert extract_json(text) == {'结论': '驳回'}


def test_extract_none_returns_none():
    assert extract_json('没有 JSON 的纯文本') is None
```

- [ ] **Step 2: Run — fail**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_writeback.py -k extract -v`
Expected: FAIL (`extract_json` not defined)

- [ ] **Step 3: Implement** — replace `server/utils/ai_scan_engine.py` stub top with imports + `extract_json` (keep `on_child_finished` stub for now):

```python
import json
import re


def extract_json(text):
    """Return the parsed JSON object from the AI reply, or None.
    Prefers the last ```json fenced block; falls back to the last balanced {...}."""
    if not text:
        return None
    blocks = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    for raw in reversed(blocks):
        try:
            return json.loads(raw)
        except ValueError:
            continue
    # fallback: scan for balanced top-level objects, try the last parseable one
    candidates = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:i + 1])
    for raw in reversed(candidates):
        try:
            return json.loads(raw)
        except ValueError:
            continue
    return None
```

- [ ] **Step 4: Run — pass; Step 5: Commit**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_writeback.py -k extract -v` → PASS

```bash
git add server/utils/ai_scan_engine.py server/tests/test_ai_scan_writeback.py
git commit -m "feat(ai-scan): tolerant JSON extraction from AI replies"
```

### Task 2.2: `final_msg` → text helper

**Files:** Modify `server/utils/ai_scan_engine.py`; Test `server/tests/test_ai_scan_writeback.py`

- [ ] **Step 1: Write failing test** — append:

```python
from utils.ai_scan_engine import message_text


def test_message_text_joins_parts():
    msg = {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'a'}, {'type': 'text', 'text': 'b'}]}
    assert message_text(msg) == 'a\nb'


def test_message_text_none():
    assert message_text(None) == ''
```

- [ ] **Step 2: Run — fail** (`message_text` undefined)

- [ ] **Step 3: Implement** — append to `ai_scan_engine.py`:

```python
def message_text(final_msg):
    """Concatenate the text parts of an assistant message dict."""
    if not final_msg:
        return ''
    parts = [p.get('text', '') for p in (final_msg.get('content') or [])
             if p.get('type') == 'text']
    return '\n'.join(t for t in parts if t)
```

- [ ] **Step 4: Run — pass; Step 5: Commit**

```bash
git add server/utils/ai_scan_engine.py server/tests/test_ai_scan_writeback.py
git commit -m "feat(ai-scan): message_text helper"
```

### Task 2.3: Prompt assembly

**Files:** Modify `server/utils/ai_scan_engine.py`; Test `server/tests/test_ai_scan_engine.py`

- [ ] **Step 1: Write failing test** — append to `test_ai_scan_engine.py`:

```python
def test_assemble_prompt_appends_contract():
    from utils.ai_scan_engine import assemble_prompt
    task = {'prompt_template': '用方案审核skill审核。',
            'field_mapping': [{'jsonKey': '结论', 'column': '审核结论', 'required': True},
                              {'jsonKey': '意见', 'column': '审核意见', 'required': False}]}
    p = assemble_prompt(task)
    assert 'uploads/record.md' in p
    assert '用方案审核skill审核。' in p
    assert '结论' in p and '意见' in p
    assert 'JSON' in p
```

- [ ] **Step 2: Run — fail; Step 3: Implement** — append to `ai_scan_engine.py`:

```python
def assemble_prompt(task):
    """[system preamble] + [user prompt_template] + [system JSON output contract]."""
    keys = [m['jsonKey'] for m in (task.get('field_mapping') or [])]
    contract_obj = ', '.join(f'"{k}": ...' for k in keys)
    preamble = ('本任务的数据见工作区 uploads/record.md，附件见 uploads/attachments/ 目录。\n'
                '请阅读这些内容后完成下面的任务。\n\n')
    contract = ('\n\n---\n完成后，请在回复的最后输出一个 JSON 代码块，'
                f'且仅包含以下字段：\n```json\n{{ {contract_obj} }}\n```')
    return preamble + (task.get('prompt_template') or '') + contract
```

- [ ] **Step 4: Run — pass; Step 5: Commit**

```bash
git add server/utils/ai_scan_engine.py server/tests/test_ai_scan_engine.py
git commit -m "feat(ai-scan): prompt assembly with auto-appended JSON contract"
```

### Task 2.4: `on_child_finished` write-back

**Files:** Modify `server/utils/ai_scan_engine.py`; Test `server/tests/test_ai_scan_writeback.py`

- [ ] **Step 1: Write failing tests** — append to `test_ai_scan_writeback.py`:

```python
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
import utils.ai_scan_engine as se


def _patch_db():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    @contextmanager
    def fake():
        yield conn
    return fake, cur


TASK = {'id': 't1', 'collection': 'orders', 'branch_id': 'main',
        'status_field': '审核状态', 'done_value': '已审核', 'failed_value': '审核失败',
        'field_mapping': [{'jsonKey': '结论', 'column': '审核结论', 'required': True}]}


def test_writeback_success_sets_mapped_columns_and_done():
    fake, cur = _patch_db()
    row = {'scan_task_id': 't1', 'source_record_id': 'rec-1'}
    msg = {'content': [{'type': 'text', 'text': '```json\n{"结论":"通过"}\n```'}]}
    with patch('utils.ai_scan_engine.get_db', fake), \
         patch('utils.ai_scan_engine._load_task', lambda tid: TASK):
        se.on_child_finished(row, msg, ok=True)
    upd = [c for c in cur.execute.call_args_list if 'UPDATE dynamic_data' in str(c.args[0])]
    assert upd, 'expected a dynamic_data UPDATE'
    flat = [v for c in upd for v in (c.args[1] if len(c.args) > 1 else ())]
    assert '通过' in flat and '已审核' in flat and 'rec-1' in flat


def test_writeback_missing_required_marks_failed():
    fake, cur = _patch_db()
    row = {'scan_task_id': 't1', 'source_record_id': 'rec-1'}
    msg = {'content': [{'type': 'text', 'text': '没有 JSON'}]}
    with patch('utils.ai_scan_engine.get_db', fake), \
         patch('utils.ai_scan_engine._load_task', lambda tid: TASK):
        se.on_child_finished(row, msg, ok=True)
    flat = [v for c in cur.execute.call_args_list for v in (c.args[1] if len(c.args) > 1 else ())]
    assert '审核失败' in flat


def test_writeback_child_failed_marks_failed():
    fake, cur = _patch_db()
    row = {'scan_task_id': 't1', 'source_record_id': 'rec-1'}
    with patch('utils.ai_scan_engine.get_db', fake), \
         patch('utils.ai_scan_engine._load_task', lambda tid: TASK):
        se.on_child_finished(row, None, ok=False)
    flat = [v for c in cur.execute.call_args_list for v in (c.args[1] if len(c.args) > 1 else ())]
    assert '审核失败' in flat
```

- [ ] **Step 2: Run — fail** (`_load_task`/real `on_child_finished` not implemented)

- [ ] **Step 3: Implement** — in `ai_scan_engine.py`, add `from db import get_db` at top, and replace the `on_child_finished` stub:

```python
from db import get_db


def _load_task(task_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, collection, branch_id, status_field, done_value, "
                "failed_value, field_mapping FROM ai_scan_tasks WHERE id = %s",
                (task_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {'id': r[0], 'collection': r[1], 'branch_id': r[2],
                    'status_field': r[3], 'done_value': r[4], 'failed_value': r[5],
                    'field_mapping': r[6] or []}


def _set_record_status(task, record_id, value):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE dynamic_data SET data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text)), "
                "updated_at = now(), version = version + 1 "
                "WHERE id = %s AND collection = %s AND branch_id = %s",
                (task['status_field'], value, record_id, task['collection'], task['branch_id']),
            )
        conn.commit()


def _write_back(task, record_id, parsed):
    """Apply mapped columns + done_value in one UPDATE. Returns rowcount."""
    # build nested jsonb_set: start from `data`, wrap once per mapped column + status
    expr = 'data'
    params = []
    for m in task['field_mapping']:
        val = parsed.get(m['jsonKey'])
        expr = f"jsonb_set({expr}, ARRAY[%s], to_jsonb(%s::text))"
        params.extend([m['column'], '' if val is None else str(val)])
    expr = f"jsonb_set({expr}, ARRAY[%s], to_jsonb(%s::text))"
    params.extend([task['status_field'], task['done_value']])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE dynamic_data SET data = {expr}, updated_at = now(), "
                "version = version + 1 WHERE id = %s AND collection = %s AND branch_id = %s",
                params + [record_id, task['collection'], task['branch_id']],
            )
            n = cur.rowcount
        conn.commit()
    return n


def on_child_finished(session_row, final_msg, ok):
    task = _load_task(session_row.get('scan_task_id'))
    if not task:
        return
    rid = session_row.get('source_record_id')
    if not rid:
        return
    if not ok:
        _set_record_status(task, rid, task['failed_value'])
        return
    parsed = extract_json(message_text(final_msg))
    required = [m['jsonKey'] for m in task['field_mapping'] if m.get('required')]
    if parsed is None or any(parsed.get(k) in (None, '') for k in required):
        _set_record_status(task, rid, task['failed_value'])
        return
    _write_back(task, rid, parsed)
```

- [ ] **Step 4: Run — pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_writeback.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add server/utils/ai_scan_engine.py server/tests/test_ai_scan_writeback.py
git commit -m "feat(ai-scan): on_child_finished write-back (mapped columns + status)"
```

### Task 2.5: Context directory builder

**Files:** Modify `server/utils/ai_scan_engine.py`; Test `server/tests/test_ai_scan_engine.py`

- [ ] **Step 1: Write failing test** — append to `test_ai_scan_engine.py`:

```python
def test_build_context_dir_writes_record_md(tmp_path, monkeypatch):
    import utils.ai_scan_engine as se
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    # no file fields → no attachments; record.md rendered from data
    monkeypatch.setattr(se, '_field_labels', lambda coll: {'name': '名称', 'amount': '金额'})
    monkeypatch.setattr(se, '_file_field_names', lambda coll: [])
    task = {'id': 't1', 'collection': 'orders', 'branch_id': 'main', 'context_fields': {}}
    rec = {'id': 'rec-1', 'data': {'name': 'A', 'amount': 99, '审核状态': '处理中'}}
    rel = se.build_context_dir(task, rec)
    from pathlib import Path
    md = (Path(str(tmp_path)) / rel / 'record.md').read_text(encoding='utf-8')
    assert '名称' in md and 'A' in md and '金额' in md and '99' in md
```

- [ ] **Step 2: Run — fail; Step 3: Implement** — append to `ai_scan_engine.py`:

```python
import os
from pathlib import Path


def _workspace_root():
    return os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')


def _field_labels(collection):
    """fieldName -> label from the page config. Reuses operation_log helper."""
    from utils.operation_log import get_field_label_map
    return get_field_label_map(collection)


def _file_field_names(collection):
    """Names of file/image controlType fields in the page config."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT fields FROM page_configs WHERE id = %s",
                        (f'page-{collection}',))
            r = cur.fetchone()
    fields = (r[0] if r else []) or []
    return [f['fieldName'] for f in fields
            if f.get('controlType') in ('file', 'image')]


def _render_record_md(data, labels):
    lines = ['# 记录数据', '']
    for k, v in data.items():
        if k in ('createdAt', 'updatedAt', '_version', '_branchId'):
            continue
        label = labels.get(k, k)
        lines.append(f'- **{label}**: {v}')
    return '\n'.join(lines) + '\n'


def _copy_attachments(data, file_fields, dest_dir):
    ids = []
    for fn in file_fields:
        val = data.get(fn)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and item.get('uid'):
                    ids.append(item['uid'])
    if not ids:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, original_name, storage_path FROM data_files "
                        "WHERE id = ANY(%s)", (ids,))
            rows = cur.fetchall()
    att = Path(dest_dir) / 'attachments'
    for _id, name, path in rows:
        src = Path(path)
        if src.exists():
            att.mkdir(parents=True, exist_ok=True)
            import shutil as _sh
            _sh.copy2(str(src), str(att / name))


def build_context_dir(task, record):
    """Stage <ws_root>/scan-staging/<task>/<record>/ with record.md + attachments/.
    Returns the path RELATIVE to the workspace root (for batch_input_file)."""
    rel = os.path.join('scan-staging', task['id'], record['id'])
    dest = Path(_workspace_root()) / rel
    if dest.exists():
        import shutil as _sh
        _sh.rmtree(str(dest))
    dest.mkdir(parents=True, exist_ok=True)
    labels = _field_labels(task['collection'])
    (dest / 'record.md').write_text(
        _render_record_md(record.get('data') or {}, labels), encoding='utf-8')
    _copy_attachments(record.get('data') or {}, _file_field_names(task['collection']), str(dest))
    return rel
```

- [ ] **Step 4: Run — pass; Step 5: Commit**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -k build_context -v` → PASS

```bash
git add server/utils/ai_scan_engine.py server/tests/test_ai_scan_engine.py
git commit -m "feat(ai-scan): per-record context directory builder (record.md + attachments)"
```

---

## Phase 3 — Repo CRUD + claim + run_task

### Task 3.1: `ai_scan_repo` CRUD

**Files:** Create `server/utils/ai_scan_repo.py`; Test `server/tests/test_routes_ai_scan_tasks.py` (later) — for now a focused unit via mock.

- [ ] **Step 1: Implement** (this task is mechanical CRUD; the route tests in Phase 5 exercise it, so implement directly then smoke-import):

Create `server/utils/ai_scan_repo.py`:

```python
import uuid
import psycopg2.extras
from db import get_db

_FIELDS = ('id', 'name', 'enabled', 'owner_user_id', 'collection', 'branch_id',
           'status_field', 'pending_value', 'running_value', 'done_value',
           'failed_value', 'extra_filter', 'context_fields', 'prompt_template',
           'field_mapping', 'schedule_interval_minutes', 'max_records_per_scan',
           'last_run_at', 'last_scan_count', 'last_error', 'created_at', 'updated_at')


def _row_to_dict(r):
    d = dict(zip(_FIELDS, r))
    for ts in ('last_run_at', 'created_at', 'updated_at'):
        if d.get(ts) is not None:
            d[ts] = d[ts].isoformat()
    return d


def list_tasks():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(_FIELDS)} FROM ai_scan_tasks ORDER BY created_at DESC")
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_task(task_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(_FIELDS)} FROM ai_scan_tasks WHERE id = %s", (task_id,))
        r = cur.fetchone()
        return _row_to_dict(r) if r else None


def create_task(body, owner_user_id):
    tid = f"scan-{uuid.uuid4().hex[:8]}"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_scan_tasks (id, name, enabled, owner_user_id, collection, "
            "branch_id, status_field, pending_value, running_value, done_value, failed_value, "
            "extra_filter, context_fields, prompt_template, field_mapping, "
            "schedule_interval_minutes, max_records_per_scan) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tid, body['name'], body.get('enabled', True), owner_user_id, body['collection'],
             body.get('branchId', 'main'), body['statusField'], body.get('pendingValue', ''),
             body.get('runningValue', '处理中'), body.get('doneValue', '已处理'),
             body.get('failedValue', '处理失败'),
             psycopg2.extras.Json(body.get('extraFilter') or {}),
             psycopg2.extras.Json(body.get('contextFields') or {}),
             body['promptTemplate'], psycopg2.extras.Json(body.get('fieldMapping') or []),
             int(body.get('scheduleIntervalMinutes', 15)), int(body.get('maxRecordsPerScan', 20))),
        )
    return get_task(tid)


_UPDATABLE = {
    'name': 'name', 'enabled': 'enabled', 'collection': 'collection', 'branchId': 'branch_id',
    'statusField': 'status_field', 'pendingValue': 'pending_value', 'runningValue': 'running_value',
    'doneValue': 'done_value', 'failedValue': 'failed_value', 'promptTemplate': 'prompt_template',
    'scheduleIntervalMinutes': 'schedule_interval_minutes', 'maxRecordsPerScan': 'max_records_per_scan',
}
_UPDATABLE_JSON = {'extraFilter': 'extra_filter', 'contextFields': 'context_fields', 'fieldMapping': 'field_mapping'}


def update_task(task_id, body):
    sets, params = [], []
    for k, col in _UPDATABLE.items():
        if k in body:
            sets.append(f"{col} = %s"); params.append(body[k])
    for k, col in _UPDATABLE_JSON.items():
        if k in body:
            sets.append(f"{col} = %s"); params.append(psycopg2.extras.Json(body[k]))
    if not sets:
        return get_task(task_id)
    sets.append("updated_at = now()")
    params.append(task_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE ai_scan_tasks SET {', '.join(sets)} WHERE id = %s", params)
    return get_task(task_id)


def delete_task(task_id):
    """Reset this task's in-flight (running_value) records to pending_value, then delete."""
    t = get_task(task_id)
    if not t:
        return False
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dynamic_data SET data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text)) "
            "WHERE collection = %s AND branch_id = %s AND data->>%s = %s",
            (t['status_field'], t['pending_value'], t['collection'], t['branch_id'],
             t['status_field'], t['running_value']),
        )
        cur.execute("DELETE FROM ai_scan_tasks WHERE id = %s", (task_id,))
    return True


def mark_run(task_id, scan_count, error=None):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_scan_tasks SET last_run_at = now(), last_scan_count = %s, "
                    "last_error = %s WHERE id = %s", (scan_count, error, task_id))
```

- [ ] **Step 2: Smoke-import**

Run: `cd server && python -c "import utils.ai_scan_repo; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add server/utils/ai_scan_repo.py
git commit -m "feat(ai-scan): ai_scan_repo CRUD (+ delete resets in-flight records)"
```

### Task 3.2: Atomic claim

**Files:** Modify `server/utils/ai_scan_engine.py`; Test `server/tests/test_ai_scan_engine.py`

- [ ] **Step 1: Write failing test** — append to `test_ai_scan_engine.py`:

```python
def test_claim_builds_pending_predicate_and_running_update():
    import utils.ai_scan_engine as se
    fake, cur = _mock_db()
    cur.fetchall = MagicMock(return_value=[('rec-1', {'name': 'A'})])
    task = {'id': 't1', 'collection': 'orders', 'branch_id': 'main',
            'status_field': '审核状态', 'pending_value': '未审核', 'running_value': '处理中',
            'extra_filter': {}, 'max_records_per_scan': 5}
    with patch('utils.ai_scan_engine.get_db', fake):
        claimed = se.claim_records(task)
    sql = str(cur.execute.call_args_list[-1].args[0])
    assert 'FOR UPDATE SKIP LOCKED' in sql and 'UPDATE dynamic_data' in sql
    assert claimed == [{'id': 'rec-1', 'data': {'name': 'A'}}]
```

- [ ] **Step 2: Run — fail; Step 3: Implement** — append to `ai_scan_engine.py`:

```python
def _pending_predicate(task, params):
    sf = task['status_field']
    pv = task.get('pending_value') or ''
    if pv == '':
        return "(d.data->>%s IS NULL OR d.data->>%s = '')", params + [sf, sf]
    return "d.data->>%s = %s", params + [sf, pv]


def claim_records(task):
    """Atomically pick up to max_records_per_scan pending records and flip them to
    running_value. Returns [{id, data}]."""
    base_params = [task['collection'], task['branch_id']]
    pred, params = _pending_predicate(task, base_params)
    filter_sql = ''
    extra = task.get('extra_filter') or {}
    if extra:
        from utils.mongo_query import translate, MongoQueryError
        try:
            clause, fparams = translate(extra, column='d.data')
            if clause:
                filter_sql = ' AND ' + clause
                params = params + list(fparams)
        except MongoQueryError:
            filter_sql = ''
    sql = (
        "WITH picked AS ("
        "  SELECT d.id FROM dynamic_data d "
        "   WHERE d.collection = %s AND d.branch_id = %s AND " + pred + filter_sql +
        "   ORDER BY d.created_at FOR UPDATE SKIP LOCKED LIMIT %s) "
        "UPDATE dynamic_data d SET data = jsonb_set(d.data, ARRAY[%s], to_jsonb(%s::text)), "
        "  updated_at = now(), version = d.version + 1 "
        "FROM picked WHERE d.id = picked.id AND d.branch_id = %s "
        "RETURNING d.id, d.data"
    )
    params = params + [task['max_records_per_scan'], task['status_field'],
                       task['running_value'], task['branch_id']]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
    return [{'id': r[0], 'data': r[1]} for r in rows]
```

Note: confirm `utils.mongo_query.translate` signature during implementation; if it differs, adapt the `column=` kwarg / return shape (it returns a `(clause, params)` pair used elsewhere in `routes/dynamic.py` — mirror that usage). If `extra_filter` integration proves non-trivial, ship V1 with `extra_filter` ignored (empty) and a `# TODO follow-up` is NOT acceptable — instead wire it exactly as `routes/dynamic.py` does its `q` translation.

- [ ] **Step 4: Run — pass; Step 5: Commit**

```bash
git add server/utils/ai_scan_engine.py server/tests/test_ai_scan_engine.py
git commit -m "feat(ai-scan): atomic tri-state claim of pending records"
```

### Task 3.3: `run_task` + orphan sweep

**Files:** Modify `server/utils/ai_scan_engine.py`; Test `server/tests/test_ai_scan_engine.py`

- [ ] **Step 1: Write failing test** — append:

```python
def test_run_task_creates_batch_for_claimed(monkeypatch):
    import utils.ai_scan_engine as se
    monkeypatch.setattr(se, 'claim_records', lambda task: [{'id': 'rec-1', 'data': {}}])
    monkeypatch.setattr(se, 'build_context_dir', lambda task, rec: f"scan-staging/{task['id']}/{rec['id']}")
    captured = {}
    monkeypatch.setattr(se, 'create_batch', lambda *a, **k: captured.update(kwargs=k, args=a) or {'batch': {}})
    monkeypatch.setattr(se, 'mark_run', lambda *a, **k: None)
    task = {'id': 't1', 'name': '审核', 'owner_user_id': 'u', 'collection': 'orders',
            'prompt_template': 'p', 'field_mapping': []}
    se.run_task(task)
    assert captured['kwargs']['scan_task_id'] == 't1'
    assert captured['kwargs']['files'][0]['recordId'] == 'rec-1'


def test_run_task_zero_claimed_no_batch(monkeypatch):
    import utils.ai_scan_engine as se
    monkeypatch.setattr(se, 'claim_records', lambda task: [])
    called = {'batch': False}
    monkeypatch.setattr(se, 'create_batch', lambda *a, **k: called.update(batch=True))
    monkeypatch.setattr(se, 'mark_run', lambda *a, **k: None)
    se.run_task({'id': 't1', 'name': 'n', 'owner_user_id': 'u', 'collection': 'c',
                 'prompt_template': 'p', 'field_mapping': []})
    assert called['batch'] is False
```

- [ ] **Step 2: Run — fail; Step 3: Implement** — append to `ai_scan_engine.py`:

```python
from datetime import datetime, timezone
from utils.batch_repo import create_batch
from utils.ai_scan_repo import mark_run


def run_task(task):
    """One scan: claim → stage context → create batch → record run."""
    claimed = claim_records(task)
    if not claimed:
        mark_run(task['id'], 0)
        return
    files = []
    try:
        for rec in claimed:
            rel = build_context_dir(task, rec)
            files.append({'name': rec['id'], 'path': rel, 'recordId': rec['id']})
        prompt = assemble_prompt(task)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        create_batch(task['owner_user_id'], name=f"AI定时·{task['name']}·{stamp}",
                     prompt=prompt, template_id=None, files=files,
                     scan_task_id=task['id'])
        mark_run(task['id'], len(claimed))
    except Exception as e:
        # revert claimed records to pending so they retry next scan
        _revert_claimed(task, [r['id'] for r in claimed])
        mark_run(task['id'], 0, error=f'{type(e).__name__}: {e}'[:500])
        raise


def _revert_claimed(task, record_ids):
    if not record_ids:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE dynamic_data SET data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text)) "
                "WHERE id = ANY(%s) AND collection = %s AND branch_id = %s",
                (task['status_field'], task.get('pending_value') or '', record_ids,
                 task['collection'], task['branch_id']),
            )
        conn.commit()


def sweep_orphans():
    """Reset running_value records that have no live (pending/running) child session."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, collection, branch_id, status_field, running_value, "
                    "pending_value FROM ai_scan_tasks")
        tasks = cur.fetchall()
    for tid, coll, branch, sf, running, pending in tasks:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE dynamic_data d SET data = jsonb_set(d.data, ARRAY[%s], to_jsonb(%s::text)) "
                    "WHERE d.collection = %s AND d.branch_id = %s AND d.data->>%s = %s "
                    "AND NOT EXISTS (SELECT 1 FROM ai_chat_sessions s "
                    "  WHERE s.scan_task_id = %s AND s.source_record_id = d.id "
                    "    AND s.status IN ('pending','running'))",
                    (sf, pending or '', coll, branch, sf, running, tid),
                )
            conn.commit()
```

- [ ] **Step 4: Run — pass; Step 5: Commit**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -q` → PASS

```bash
git add server/utils/ai_scan_engine.py server/tests/test_ai_scan_engine.py
git commit -m "feat(ai-scan): run_task (claim→stage→batch) + orphan sweep"
```

---

## Phase 4 — Scheduler + app wiring

### Task 4.1: Scheduler

**Files:** Create `server/utils/ai_scan_scheduler.py`; Test `server/tests/test_ai_scan_engine.py`

- [ ] **Step 1: Write failing test** — append:

```python
def test_is_due_logic():
    from utils.ai_scan_scheduler import _is_due
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    assert _is_due({'last_run_at': None, 'schedule_interval_minutes': 15}, now) is True
    assert _is_due({'last_run_at': (now - timedelta(minutes=20)).isoformat(),
                    'schedule_interval_minutes': 15}, now) is True
    assert _is_due({'last_run_at': (now - timedelta(minutes=5)).isoformat(),
                    'schedule_interval_minutes': 15}, now) is False
```

- [ ] **Step 2: Run — fail; Step 3: Implement** — create `server/utils/ai_scan_scheduler.py`:

```python
import threading
import traceback
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler

_scheduler = None
_locks = {}
_locks_guard = threading.Lock()


def _is_due(task, now):
    if not task.get('enabled', True):
        return False
    lr = task.get('last_run_at')
    if not lr:
        return True
    if isinstance(lr, str):
        lr = datetime.fromisoformat(lr)
    interval = task.get('schedule_interval_minutes', 15)
    return (now - lr).total_seconds() >= interval * 60


def _task_lock(task_id):
    with _locks_guard:
        return _locks.setdefault(task_id, threading.Lock())


def _tick():
    from utils.ai_scan_repo import list_tasks
    from utils.ai_scan_engine import run_task
    now = datetime.now(timezone.utc)
    for task in list_tasks():
        if not task.get('enabled', True) or not _is_due(task, now):
            continue
        lock = _task_lock(task['id'])
        if not lock.acquire(blocking=False):
            continue
        try:
            run_task(task)
        except Exception:
            traceback.print_exc()
        finally:
            lock.release()


def start_scan_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from utils.ai_scan_engine import sweep_orphans
        sweep_orphans()
    except Exception:
        traceback.print_exc()
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_tick, 'interval', minutes=1, id='ai_scan_tick',
                       max_instances=1, coalesce=True)
    _scheduler.start()
```

- [ ] **Step 4: Run — pass; Step 5: Commit**

```bash
git add server/utils/ai_scan_scheduler.py server/tests/test_ai_scan_engine.py
git commit -m "feat(ai-scan): scheduler tick + due logic + startup orphan sweep"
```

### Task 4.2: Wire scheduler into app.py

**Files:** Modify `server/app.py`

- [ ] **Step 1: Add startup** — in `server/app.py`, in the `WERKZEUG_RUN_MAIN` block next to the other schedulers (~line 90-94):

```python
    from utils.ai_scan_scheduler import start_scan_scheduler
    start_scan_scheduler(app)
```

- [ ] **Step 2: Verify import**

Run: `cd server && python -c "import app; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add server/app.py
git commit -m "feat(ai-scan): start scan scheduler under WERKZEUG_RUN_MAIN"
```

---

## Phase 5 — Routes + RBAC reserved path

### Task 5.1: roles routes + blueprint registration + RESERVED

**Files:** Create `server/routes/ai_scan_tasks.py`; Modify `server/app.py`, `server/routes/dynamic.py`; Test `server/tests/test_routes_ai_scan_tasks.py`

- [ ] **Step 1: Write failing tests** — create `server/tests/test_routes_ai_scan_tasks.py`:

```python
import sys, os, json
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token


def _make_db(mock_conn):
    @contextmanager
    def fake():
        yield mock_conn
    return fake


@pytest.fixture
def setup(mock_conn, mock_cursor):
    fake = _make_db(mock_conn)
    patches = [patch('db.get_db', fake), patch('routes.ai_scan_tasks.get_db', fake),
               patch('utils.permissions.get_db', fake), patch('utils.ai_scan_repo.get_db', fake),
               patch('db.pool', MagicMock()), patch('utils.operation_log.log_operation')]
    for p in patches:
        p.start()
    import utils.permissions as perms
    perms.invalidate_cache()
    mock_cursor.fetchone.return_value = ('admin', True, 'write')
    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    yield app.test_client(), mock_cursor, {'Authorization': f'Bearer {admin}'}
    for p in patches:
        p.stop()


def test_list_requires_admin_ai_scan(setup):
    client, cur, headers = setup
    cur.fetchall.return_value = []
    resp = client.get('/ai-scan-tasks', headers=headers)
    assert resp.status_code == 200


def test_create_task(setup):
    client, cur, headers = setup
    cur.fetchone.side_effect = [('admin', True, 'write'),  # permission
                                # get_task after insert returns a row tuple of 22 fields
                                tuple(['scan-x', 'n', True, 'user-admin', 'orders', 'main',
                                       '审核状态', '', '处理中', '已处理', '处理失败', {}, {}, 'p',
                                       [], 15, 20, None, 0, None, None, None])]
    body = {'name': 'n', 'collection': 'orders', 'statusField': '审核状态',
            'promptTemplate': 'p', 'fieldMapping': []}
    resp = client.post('/ai-scan-tasks', data=json.dumps(body), content_type='application/json',
                       headers=headers)
    assert resp.status_code == 201
```

- [ ] **Step 2: Run — fail** (404; blueprint missing)

- [ ] **Step 3: Implement** — create `server/routes/ai_scan_tasks.py`:

```python
from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import require_permission
from utils import ai_scan_repo
from utils.operation_log import log_operation

ai_scan_tasks_bp = Blueprint('ai_scan_tasks', __name__)


@ai_scan_tasks_bp.route('/ai-scan-tasks', methods=['GET'])
@require_permission('admin.ai_scan')
def list_tasks():
    return jsonify(ai_scan_repo.list_tasks())


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>', methods=['GET'])
@require_permission('admin.ai_scan')
def get_task(task_id):
    t = ai_scan_repo.get_task(task_id)
    return (jsonify(t), 200) if t else (jsonify({'error': '任务不存在'}), 404)


@ai_scan_tasks_bp.route('/ai-scan-tasks', methods=['POST'])
@require_permission('admin.ai_scan')
def create_task():
    body = request.get_json(force=True)
    for k in ('name', 'collection', 'statusField', 'promptTemplate'):
        if not body.get(k):
            return jsonify({'error': f'缺少必填项：{k}'}), 400
    t = ai_scan_repo.create_task(body, g.current_user['userId'])
    log_operation('create', 'ai_scan_task', t['id'], t['name'], f'新增 AI 定时任务「{t["name"]}」')
    return jsonify(t), 201


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>', methods=['PUT'])
@require_permission('admin.ai_scan')
def update_task(task_id):
    body = request.get_json(force=True)
    t = ai_scan_repo.update_task(task_id, body)
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    log_operation('update', 'ai_scan_task', task_id, t['name'], f'更新 AI 定时任务「{t["name"]}」')
    return jsonify(t)


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>', methods=['DELETE'])
@require_permission('admin.ai_scan')
def delete_task(task_id):
    ok = ai_scan_repo.delete_task(task_id)
    if not ok:
        return jsonify({'error': '任务不存在'}), 404
    log_operation('delete', 'ai_scan_task', task_id, task_id, f'删除 AI 定时任务「{task_id}」')
    return jsonify({})


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>/run-now', methods=['POST'])
@require_permission('admin.ai_scan')
def run_now(task_id):
    t = ai_scan_repo.get_task(task_id)
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    from utils.ai_scan_engine import run_task
    try:
        run_task(t)
    except Exception as e:
        return jsonify({'error': f'运行失败：{e}'}), 500
    return jsonify({'message': '已触发一次扫描'})
```

- [ ] **Step 4: Register** — in `server/app.py`, import `from routes.ai_scan_tasks import ai_scan_tasks_bp` and `app.register_blueprint(ai_scan_tasks_bp)` BEFORE `dynamic_bp`. In `server/routes/dynamic.py`, add `'ai-scan-tasks'` to the `RESERVED` set.

- [ ] **Step 5: Run — pass; Commit**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_scan_tasks.py tests/test_route_permission_keys.py -q` → PASS

```bash
git add server/routes/ai_scan_tasks.py server/app.py server/routes/dynamic.py server/tests/test_routes_ai_scan_tasks.py
git commit -m "feat(ai-scan): REST API (CRUD + run-now), reserved path, blueprint"
```

### Task 5.2: Full backend regression

- [ ] **Step 1:** Run `npm run test:server` → all pass (fix any permission-resolution mock gaps with the conftest superuser prime pattern). **Step 2:** Commit any test fixups: `git commit -am "test(ai-scan): backend regression fixups"`.

---

## Phase 6 — Frontend

### Task 6.1: Types + API + store

**Files:** Create `src/types/aiScanTask.ts`, `src/api/aiScanTask.ts`, `src/stores/aiScanTask.ts`; modify `src/types/index.ts`

- [ ] **Step 1: Types** — create `src/types/aiScanTask.ts`:

```typescript
export interface FieldMappingRow { jsonKey: string; column: string; required: boolean }
export interface AiScanTask {
  id: string
  name: string
  enabled: boolean
  ownerUserId?: string
  collection: string
  branchId: string
  statusField: string
  pendingValue: string
  runningValue: string
  doneValue: string
  failedValue: string
  extraFilter: Record<string, unknown>
  contextFields: Record<string, unknown>
  promptTemplate: string
  fieldMapping: FieldMappingRow[]
  scheduleIntervalMinutes: number
  maxRecordsPerScan: number
  lastRunAt?: string | null
  lastScanCount?: number
  lastError?: string | null
}
```

Add `export * from './aiScanTask'` to `src/types/index.ts`.

- [ ] **Step 2: API client** — create `src/api/aiScanTask.ts`:

```typescript
import { get, post, put, del } from '@/utils/request'
import type { AiScanTask } from '@/types'

export function getScanTasks() { return get<AiScanTask[]>('/ai-scan-tasks') }
export function getScanTask(id: string) { return get<AiScanTask>(`/ai-scan-tasks/${id}`) }
export function createScanTask(data: Partial<AiScanTask>) { return post<AiScanTask>('/ai-scan-tasks', data) }
export function updateScanTask(id: string, data: Partial<AiScanTask>) { return put<AiScanTask>(`/ai-scan-tasks/${id}`, data) }
export function deleteScanTask(id: string) { return del(`/ai-scan-tasks/${id}`) }
export function runScanTaskNow(id: string) { return post<{ message: string }>(`/ai-scan-tasks/${id}/run-now`, {}) }
```

- [ ] **Step 3: Store + test** — create `src/stores/aiScanTask.ts`:

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getScanTasks, getScanTask, createScanTask, updateScanTask, deleteScanTask, runScanTaskNow } from '@/api/aiScanTask'
import type { AiScanTask } from '@/types'

export const useAiScanTaskStore = defineStore('aiScanTask', () => {
  const tasks = ref<AiScanTask[]>([])
  const loading = ref(false)
  async function load() {
    loading.value = true
    try { tasks.value = await getScanTasks() } finally { loading.value = false }
  }
  function fetchOne(id: string) { return getScanTask(id) }
  async function save(id: string, data: Partial<AiScanTask>) { await updateScanTask(id, data); await load() }
  async function add(data: Partial<AiScanTask>) { const t = await createScanTask(data); await load(); return t }
  async function remove(id: string) { await deleteScanTask(id); await load() }
  function runNow(id: string) { return runScanTaskNow(id) }
  return { tasks, loading, load, fetchOne, save, add, remove, runNow }
})
```

Create `src/stores/__tests__/aiScanTask.test.ts`:

```typescript
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, it, expect, vi } from 'vitest'

vi.mock('@/api/aiScanTask', () => ({
  getScanTasks: vi.fn(() => Promise.resolve([{ id: 't1', name: 'n' }])),
  getScanTask: vi.fn(), createScanTask: vi.fn(() => Promise.resolve({ id: 't2' })),
  updateScanTask: vi.fn(() => Promise.resolve({})), deleteScanTask: vi.fn(() => Promise.resolve()),
  runScanTaskNow: vi.fn(() => Promise.resolve({ message: 'ok' })),
}))
import { useAiScanTaskStore } from '@/stores/aiScanTask'

describe('aiScanTask store', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('loads tasks', async () => {
    const s = useAiScanTaskStore()
    await s.load()
    expect(s.tasks).toHaveLength(1)
    expect(s.tasks[0].id).toBe('t1')
  })
})
```

- [ ] **Step 4: Run + build**

Run: `npx vitest run src/stores/__tests__/aiScanTask.test.ts` → PASS. Run: `npm run build` → passes.

- [ ] **Step 5: Commit**

```bash
git add src/types/aiScanTask.ts src/types/index.ts src/api/aiScanTask.ts src/stores/aiScanTask.ts src/stores/__tests__/aiScanTask.test.ts
git commit -m "feat(ai-scan): frontend types, api client, store"
```

### Task 6.2: AiScanTaskManager.vue + route + RBAC path

**Files:** Create `src/views/admin/AiScanTaskManager.vue`; Modify `src/router/index.ts`, `src/stores/auth.ts`

- [ ] **Step 1: Add route + RBAC path** — in `src/router/index.ts`, add a child route:

```typescript
      {
        path: 'admin/ai-scan-tasks',
        name: 'AiScanTaskManager',
        component: () => import('@/views/admin/AiScanTaskManager.vue'),
        meta: { title: 'AI 定时任务', icon: 'AlarmClock' },
      },
```

In `src/stores/auth.ts` `ADMIN_PATH_PERMISSION`, add: `'/admin/ai-scan-tasks': 'admin.ai_scan',`

- [ ] **Step 2: Implement the page** — create `src/views/admin/AiScanTaskManager.vue`:

```vue
<template>
  <div class="scan-mgr">
    <el-card class="list-card" v-loading="store.loading">
      <template #header>
        <div class="hd"><span>AI 定时任务</span>
          <el-button type="primary" size="small" @click="openCreate">新建任务</el-button></div>
      </template>
      <el-empty v-if="store.tasks.length === 0" description="暂无任务" :image-size="80" />
      <el-menu v-else :default-active="selectedId" @select="select">
        <el-menu-item v-for="t in store.tasks" :key="t.id" :index="t.id">
          <span>{{ t.name }}</span>
          <el-tag :type="t.enabled ? 'success' : 'info'" size="small" style="margin-left:8px">
            {{ t.enabled ? '启用' : '停用' }}</el-tag>
        </el-menu-item>
      </el-menu>
    </el-card>

    <el-card v-if="form" class="editor-card">
      <template #header>
        <div class="hd"><span>{{ form.name || '任务配置' }}</span>
          <div>
            <el-button size="small" @click="runNow" :loading="running">立即运行</el-button>
            <el-button v-if="form.id" type="danger" size="small" @click="remove">删除</el-button>
            <el-button type="primary" size="small" @click="save">保存</el-button>
          </div>
        </div>
      </template>
      <el-form label-width="120px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
        <el-form-item label="数据页(collection)"><el-input v-model="form.collection" placeholder="如 inspection-case" /></el-form-item>
        <el-form-item label="分支"><el-input v-model="form.branchId" /></el-form-item>
        <el-form-item label="状态字段"><el-input v-model="form.statusField" placeholder="记录里的字段名，如 审核状态" /></el-form-item>
        <el-form-item label="待处理值"><el-input v-model="form.pendingValue" placeholder="留空=匹配空/未设置" /></el-form-item>
        <el-form-item label="处理中值"><el-input v-model="form.runningValue" /></el-form-item>
        <el-form-item label="已处理值"><el-input v-model="form.doneValue" /></el-form-item>
        <el-form-item label="失败值"><el-input v-model="form.failedValue" /></el-form-item>
        <el-form-item label="调度间隔(分钟)"><el-input-number v-model="form.scheduleIntervalMinutes" :min="1" /></el-form-item>
        <el-form-item label="每次最多条数"><el-input-number v-model="form.maxRecordsPerScan" :min="1" /></el-form-item>
        <el-form-item label="候选过滤(JSON)">
          <el-input v-model="extraFilterText" type="textarea" :rows="2" placeholder='如 {"优先级":"高"}' />
        </el-form-item>
        <el-form-item label="提示词">
          <el-input v-model="form.promptTemplate" type="textarea" :rows="5"
            placeholder="操作指令，引用要用的 skill" />
        </el-form-item>
        <el-form-item label="字段映射">
          <div v-for="(m, i) in form.fieldMapping" :key="i" class="map-row">
            <el-input v-model="m.jsonKey" placeholder="AI JSON 键" style="width:160px" />
            <span>→</span>
            <el-input v-model="m.column" placeholder="回写到的列" style="width:160px" />
            <el-checkbox v-model="m.required">必填</el-checkbox>
            <el-button link type="danger" @click="form.fieldMapping.splice(i,1)">删除</el-button>
          </div>
          <el-button size="small" @click="form.fieldMapping.push({ jsonKey:'', column:'', required:false })">+ 添加映射</el-button>
        </el-form-item>
        <el-form-item label="输出契约预览">
          <pre class="contract">{{ contractPreview }}</pre>
        </el-form-item>
        <el-form-item label="运行信息" v-if="form.id">
          <div class="hint">上次运行：{{ form.lastRunAt || '从未' }}；本次处理：{{ form.lastScanCount ?? 0 }} 条
            <span v-if="form.lastError" style="color:#f56c6c">；错误：{{ form.lastError }}</span></div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAiScanTaskStore } from '@/stores/aiScanTask'
import type { AiScanTask } from '@/types'

const store = useAiScanTaskStore()
const selectedId = ref('')
const form = ref<AiScanTask | null>(null)
const extraFilterText = ref('{}')
const running = ref(false)

function blank(): AiScanTask {
  return { id: '', name: '', enabled: true, collection: '', branchId: 'main', statusField: '',
    pendingValue: '', runningValue: '处理中', doneValue: '已处理', failedValue: '处理失败',
    extraFilter: {}, contextFields: {}, promptTemplate: '', fieldMapping: [],
    scheduleIntervalMinutes: 15, maxRecordsPerScan: 20 }
}

const contractPreview = computed(() => {
  const keys = (form.value?.fieldMapping || []).map(m => `"${m.jsonKey}": ...`).join(', ')
  return `完成后请在末尾输出 JSON：\n{ ${keys} }`
})

async function select(id: string) {
  selectedId.value = id
  const t = await store.fetchOne(id)
  form.value = t
  extraFilterText.value = JSON.stringify(t.extraFilter || {}, null, 0)
}

function openCreate() { form.value = blank(); selectedId.value = ''; extraFilterText.value = '{}' }

function parsedFilter(): Record<string, unknown> {
  try { return JSON.parse(extraFilterText.value || '{}') } catch { ElMessage.error('候选过滤 JSON 不合法'); throw new Error('bad json') }
}

async function save() {
  if (!form.value) return
  if (!form.value.name || !form.value.collection || !form.value.statusField || !form.value.promptTemplate) {
    ElMessage.warning('名称/数据页/状态字段/提示词为必填'); return
  }
  const payload = { ...form.value, extraFilter: parsedFilter() }
  if (form.value.id) await store.save(form.value.id, payload)
  else { const t = await store.add(payload); await select(t.id) }
  ElMessage.success('已保存')
}

async function remove() {
  if (!form.value?.id) return
  try { await ElMessageBox.confirm(`删除任务「${form.value.name}」？`, '确认', { type: 'warning' }) } catch { return }
  await store.remove(form.value.id)
  form.value = null; selectedId.value = ''
  ElMessage.success('已删除')
}

async function runNow() {
  if (!form.value?.id) { ElMessage.warning('请先保存任务'); return }
  running.value = true
  try { const r = await store.runNow(form.value.id); ElMessage.success(r.message); await select(form.value.id) }
  finally { running.value = false }
}

onMounted(async () => {
  await store.load()
  if (store.tasks.length) await select(store.tasks[0].id)
})
</script>

<style scoped lang="scss">
.scan-mgr { display: flex; gap: 16px; height: 100%; }
.list-card { width: 240px; flex-shrink: 0; }
.editor-card { flex: 1; overflow: auto; }
.hd { display: flex; justify-content: space-between; align-items: center; }
.map-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.contract { background: #f5f7fa; padding: 8px; border-radius: 4px; font-size: 12px; white-space: pre-wrap; }
.hint { color: #909399; font-size: 12px; }
</style>
```

- [ ] **Step 3: Build + test**

Run: `npm run build` → passes. Run: `npx vitest run src/stores/__tests__/aiScanTask.test.ts` → PASS.

- [ ] **Step 4: Commit**

```bash
git add src/views/admin/AiScanTaskManager.vue src/router/index.ts src/stores/auth.ts
git commit -m "feat(ai-scan): AI 定时任务 admin page + route + RBAC path"
```

---

## Phase 7 — Integration verification

### Task 7.1: Full suites + manual/E2E smoke

- [ ] **Step 1:** `npm run test:server` (all pass) and `npm run test` (all pass) and `npm run build` (clean).
- [ ] **Step 2: Manual smoke** (requires OpenCode + MCP running, `npm run dev:all`):
  1. Create a data page with a `审核状态` field + a few records left blank.
  2. In 「AI 定时任务」, create a task: collection = that page, statusField=`审核状态`, pendingValue blank, prompt = a trivial instruction + a skill, fieldMapping=`[{jsonKey:结论, column:审核结论, required:true}]`.
  3. Click 「立即运行」 → a batch appears in 「批任务」; click in to watch a child run.
  4. After completion, the record's `审核状态` flips to `已处理` and `审核结论` is written; failures show `处理失败` with the transcript inspectable.
- [ ] **Step 3: Commit any fixups**, then `git push`.

---

## Self-Review Notes

- **Spec coverage:** data model (0.1), RBAC+menu (0.2), batch reuse (1.1–1.3), JSON/prompt/write-back/context (2.x), claim+run_task+orphan (3.x), scheduler (4.x), routes+reserved (5.x), frontend (6.x), tests throughout, E2E (7.1). The `extra_filter` via `mongo_query` is wired in 3.2 with an explicit instruction to mirror `routes/dynamic.py`'s translation usage.
- **Deferred confirmations (resolve in-task, not placeholders):** exact `mongo_query.translate` signature (3.2 — mirror dynamic.py), file-field value shape (2.5 — uses `{uid}` per `routes/data_files.py:12`), `get_field_label_map` import path (2.5 — from `utils.operation_log`, used by dynamic.py). Each task says exactly what to verify.
- **Out of scope (per spec §1):** cross-collection write-back/record creation (skill via MCP), cron, multi-branch.
