# Batch & Scan Tasks Agent Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to select an OpenCode agent when creating a batch task or configuring a scan task, so the agent is used for all child sessions instead of defaulting to the OpenCode default.

**Architecture:** Add a nullable `agent` column to `ai_chat_batches` and `ai_scan_tasks` tables. Thread the value from the DB through the batch engine's `_fetch_batch_context` call to `opencode_client.send_message`, which already forwards it to `send_prompt_async(agent=)`. Add a dropdown to both frontend forms using the existing `listAgents()` API. No `agent_parts`/position data needed — these are static configs, not live chat composer mentions.

**Tech Stack:** Python/Flask, psycopg2, Vue 3, TypeScript, Element Plus. Tests: pytest (backend), vitest (frontend).

---

## File Map

| File | Change |
|---|---|
| `server/init_db.py` | Add `agent TEXT` columns to both table DDL |
| `server/utils/batch_repo.py` | `create_batch(agent=None)` → store agent in INSERT |
| `server/utils/batch_engine.py` | `send_message(agent='')`, `_fetch_batch_context` returning `(prompt, agent)`, `_run_one` passes agent |
| `server/routes/ai_chat_batches.py` | Accept `agent` from POST body |
| `server/utils/ai_scan_repo.py` | Add `agent` to `_FIELDS`, `_CAMEL`, `create_task`, `_UPDATABLE` |
| `server/utils/ai_scan_engine.py` | `_load_task` fetches `agent`; `run_task` passes `agent` to `create_batch` |
| `src/types/aiChatBatch.ts` | Add `agent?: string` to `AiChatBatch` |
| `src/types/aiScanTask.ts` | Add `agent?: string` to `AiScanTask` |
| `src/api/aiChatBatches.ts` | Add `agent?` to `createBatch` body type |
| `src/components/ai-chat/CreateBatchDialog.vue` | Agent dropdown + pass to `createBatch` |
| `src/views/admin/AiScanTaskManager.vue` | Agent dropdown + include in save payload |
| `server/tests/test_batch_routes.py` | Test POST with `agent` stored and round-tripped |
| `server/tests/test_batch_engine.py` | Test `send_message` receives agent from batch row |
| `server/tests/test_routes_ai_scan_tasks.py` | Test create/update with `agent` |

---

## Task 1: DB Schema — Add `agent` Columns

**Files:**
- Modify: `server/init_db.py`
- Run migration SQL against dev DB

- [ ] **Step 1: Run migration on existing DB**

```sql
ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS agent TEXT;
ALTER TABLE ai_scan_tasks   ADD COLUMN IF NOT EXISTS agent TEXT;
```

Run via psql or a Python one-liner:

```bash
cd server && python -c "
from config import DB_CONFIG
import psycopg2
conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()
cur.execute('ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS agent TEXT')
cur.execute('ALTER TABLE ai_scan_tasks   ADD COLUMN IF NOT EXISTS agent TEXT')
conn.commit()
print('done')
conn.close()
"
```

- [ ] **Step 2: Update init_db.py DDL for new installs**

Find the `ai_chat_batches` CREATE TABLE in `server/init_db.py`. Add `agent TEXT,` after `template_id TEXT,`:

```sql
-- in ai_chat_batches CREATE TABLE
    agent       TEXT,
```

Find the `ai_scan_tasks` CREATE TABLE. Add `agent TEXT,` after `max_records_per_scan INT NOT NULL DEFAULT 20,`:

```sql
-- in ai_scan_tasks CREATE TABLE
    agent       TEXT,
```

- [ ] **Step 3: Verify migration**

```bash
cd server && python -c "
from config import DB_CONFIG
import psycopg2
conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='ai_chat_batches' AND column_name='agent'\")
print('batches agent col:', cur.fetchone())
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='ai_scan_tasks' AND column_name='agent'\")
print('scan agent col:', cur.fetchone())
conn.close()
"
```

Expected output:
```
batches agent col: ('agent',)
scan agent col: ('agent',)
```

- [ ] **Step 4: Commit**

```bash
git add server/init_db.py
git commit -m "feat(db): add agent column to ai_chat_batches and ai_scan_tasks"
```

---

## Task 2: Batch Backend — repo + engine + route

**Files:**
- Modify: `server/utils/batch_repo.py`
- Modify: `server/utils/batch_engine.py`
- Modify: `server/routes/ai_chat_batches.py`
- Test: `server/tests/test_batch_routes.py`
- Test: `server/tests/test_batch_engine.py`

### 2a: batch_repo.py — accept and store agent

- [ ] **Step 1: Write failing test in test_batch_routes.py**

Add to `server/tests/test_batch_routes.py` (after existing tests):

```python
def test_create_batch_stores_agent(setup_app, tmp_path, monkeypatch, db_conn):
    """agent field is persisted and returned in the batch response."""
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='x.txt', upload_session_id='u-agent-1')
    body = {
        'name': 'agent-test',
        'prompt': 'do something',
        'agent': 'my-agent',
        'files': [f],
    }
    resp = client.post('/ai/chat/batches', json=body, headers=admin_headers)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['batch']['agent'] == 'my-agent'

    # Verify it's in the DB
    with db_conn.cursor() as cur:
        cur.execute("SELECT agent FROM ai_chat_batches WHERE id = %s",
                    (data['batch']['id'],))
        assert cur.fetchone()[0] == 'my-agent'
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_routes.py::test_create_batch_stores_agent -v 2>&1 | tail -20
```

Expected: FAIL — `data['batch']['agent']` KeyError or assertion error.

- [ ] **Step 3: Update batch_repo.py create_batch signature and INSERT**

In `server/utils/batch_repo.py`, change `create_batch` to accept and store `agent`:

```python
def create_batch(user_id: str, *, name: str, prompt: str,
                 template_id: str | None, files: list[dict],
                 scan_task_id: str | None = None,
                 agent: str | None = None) -> dict:
```

Change the INSERT (line 34–38):

```python
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "  (id, user_id, name, prompt, template_id, total, status, agent) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s) RETURNING *",
                (batch_id, user_id, name, prompt, template_id, len(files), agent),
            )
```

- [ ] **Step 4: Update route to pass agent**

In `server/routes/ai_chat_batches.py`, in the `create()` function, after `template_id = body.get('template_id')`:

```python
    agent = (body.get('agent') or '').strip() or None
```

And pass it to `create_batch`:

```python
    result = create_batch(g.current_user['userId'],
                          name=name, prompt=prompt,
                          template_id=template_id, files=files,
                          agent=agent)
```

- [ ] **Step 5: Run test to confirm PASS**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_routes.py::test_create_batch_stores_agent -v 2>&1 | tail -10
```

Expected: PASS.

### 2b: batch_engine.py — read agent and pass to OpenCode

- [ ] **Step 6: Write failing unit test in test_batch_engine.py**

Add to `server/tests/test_batch_engine.py` (after existing tests):

```python
def test_run_one_passes_agent_to_opencode(user_id, db_conn, monkeypatch, tmp_path):
    """When batch has agent set, send_message receives that agent."""
    import utils.batch_engine as eng
    from utils.batch_repo import create_batch

    # Stage a dummy file
    staging = tmp_path / 'batch-staging' / 'x'
    staging.mkdir(parents=True)
    fpath = staging / 'r.txt'
    fpath.write_text('hello')

    batch_data = create_batch(
        user_id,
        name='agent-engine-test',
        prompt='do stuff',
        template_id=None,
        files=[{'name': 'r.txt', 'path': f'batch-staging/x/r.txt'}],
        agent='my-agent',
    )
    session_row = batch_data['sessions'][0]

    sent_agents = []
    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-sess-1'
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'ok'}]}
    ]
    original_send = fake_oc.send_message

    def capture_send(oc_sid, prompt, directory='', agent=''):
        sent_agents.append(agent)
    fake_oc.send_message.side_effect = capture_send

    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)

    worker = eng.BatchWorker()
    # Fetch and run the session directly
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_chat_sessions SET status='pending' WHERE id=%s",
            (session_row['id'],)
        )
    db_conn.commit()

    claimed = worker._claim_pending_sessions(limit=1)
    assert claimed
    worker._run_one(claimed[0])

    assert sent_agents == ['my-agent']
```

- [ ] **Step 7: Run to confirm FAIL**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest "tests/test_batch_engine.py::test_run_one_passes_agent_to_opencode" -v 2>&1 | tail -20
```

Expected: FAIL — `sent_agents` will be `['']` not `['my-agent']`.

- [ ] **Step 8: Update _OpenCodeFacade.send_message to accept and forward agent**

In `server/utils/batch_engine.py`, change `send_message`:

```python
    def send_message(self, oc_session_id: str, prompt: str,
                     directory: str = '', agent: str = '') -> dict:
        """Fire the prompt asynchronously."""
        from config import OPENCODE_MODEL
        self._client().send_prompt_async(
            oc_session_id, prompt,
            model=OPENCODE_MODEL,
            directory=directory,
            agent=agent,
        )
        return {'id': oc_session_id}
```

- [ ] **Step 9: Update _fetch_batch_prompt → _fetch_batch_context**

Rename `_fetch_batch_prompt` to `_fetch_batch_context` and return both prompt and agent:

```python
    def _fetch_batch_context(self, batch_id: str) -> tuple[str, str] | None:
        """Returns (prompt, agent) or None if the batch was deleted."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prompt, agent FROM ai_chat_batches WHERE id = %s",
                    (batch_id,),
                )
                row = cur.fetchone()
                return (row[0], row[1] or '') if row else None
```

- [ ] **Step 10: Update _run_one to use _fetch_batch_context and pass agent**

In `_run_one`, replace:

```python
        prompt = self._fetch_batch_prompt(batch_id)
        if prompt is None:
            return
        try:
            ws = _prepare_workspace(user_id, sid, session_row['batch_input_file'] or '')
            oc_session_id = opencode_client.create_session(directory=ws)
            self._set_opencode_id(sid, oc_session_id)
            opencode_client.send_message(oc_session_id, prompt, directory=ws)
```

with:

```python
        ctx = self._fetch_batch_context(batch_id)
        if ctx is None:
            return
        prompt, agent = ctx
        try:
            ws = _prepare_workspace(user_id, sid, session_row['batch_input_file'] or '')
            oc_session_id = opencode_client.create_session(directory=ws)
            self._set_opencode_id(sid, oc_session_id)
            opencode_client.send_message(oc_session_id, prompt, directory=ws,
                                         agent=agent)
```

- [ ] **Step 11: Run new test to confirm PASS, ensure existing engine tests still pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_engine.py -v 2>&1 | tail -15
```

Expected: all pass.

- [ ] **Step 12: Commit**

```bash
git add server/utils/batch_repo.py server/utils/batch_engine.py server/routes/ai_chat_batches.py server/tests/test_batch_routes.py server/tests/test_batch_engine.py
git commit -m "feat(batch): propagate agent field from create through to OpenCode send_message"
```

---

## Task 3: Scan Backend — repo + engine

**Files:**
- Modify: `server/utils/ai_scan_repo.py`
- Modify: `server/utils/ai_scan_engine.py`
- Test: `server/tests/test_routes_ai_scan_tasks.py`

### 3a: ai_scan_repo.py — add agent to CRUD

- [ ] **Step 1: Write failing test**

Add to `server/tests/test_routes_ai_scan_tasks.py`:

```python
def test_create_task_with_agent(setup):
    client, cur, headers = setup
    # Permission check row + get_task return row (now 23 fields: +agent at end)
    task_row = tuple(['scan-y', 'n', True, 'user-admin', 'orders', 'main',
                      '审核状态', '', '处理中', '已处理', '处理失败', {}, {}, 'p',
                      [], 15, 20, None, 0, None, None, None, 'my-agent'])
    cur.fetchone.side_effect = [('admin', True, 'write'), task_row]
    body = {'name': 'n', 'collection': 'orders', 'statusField': '审核状态',
            'promptTemplate': 'p', 'fieldMapping': [], 'agent': 'my-agent'}
    resp = client.post('/ai-scan-tasks', data=json.dumps(body), content_type='application/json',
                       headers=headers)
    assert resp.status_code == 201
    assert resp.get_json()['agent'] == 'my-agent'


def test_update_task_with_agent(setup):
    client, cur, headers = setup
    task_row = tuple(['scan-y', 'n', True, 'user-admin', 'orders', 'main',
                      '审核状态', '', '处理中', '已处理', '处理失败', {}, {}, 'p',
                      [], 15, 20, None, 0, None, None, None, 'updated-agent'])
    cur.fetchone.side_effect = [('admin', True, 'write'), task_row]
    resp = client.put('/ai-scan-tasks/scan-y',
                      data=json.dumps({'agent': 'updated-agent'}),
                      content_type='application/json', headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['agent'] == 'updated-agent'
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_scan_tasks.py::test_create_task_with_agent tests/test_routes_ai_scan_tasks.py::test_update_task_with_agent -v 2>&1 | tail -20
```

Expected: FAIL.

- [ ] **Step 3: Update _FIELDS, _CAMEL, create_task, _UPDATABLE in ai_scan_repo.py**

`_FIELDS` — add `'agent'` at the end:

```python
_FIELDS = ('id', 'name', 'enabled', 'owner_user_id', 'collection', 'branch_id',
           'status_field', 'pending_value', 'running_value', 'done_value',
           'failed_value', 'extra_filter', 'context_fields', 'prompt_template',
           'field_mapping', 'schedule_interval_minutes', 'max_records_per_scan',
           'last_run_at', 'last_scan_count', 'last_error', 'created_at', 'updated_at',
           'agent')
```

`_CAMEL` — add `'agent'` at the end:

```python
_CAMEL = ('id', 'name', 'enabled', 'ownerUserId', 'collection', 'branchId',
          'statusField', 'pendingValue', 'runningValue', 'doneValue',
          'failedValue', 'extraFilter', 'contextFields', 'promptTemplate',
          'fieldMapping', 'scheduleIntervalMinutes', 'maxRecordsPerScan',
          'lastRunAt', 'lastScanCount', 'lastError', 'createdAt', 'updatedAt',
          'agent')
```

`create_task` — add `agent` to INSERT:

```python
def create_task(body, owner_user_id):
    tid = f"scan-{uuid.uuid4().hex[:8]}"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_scan_tasks (id, name, enabled, owner_user_id, collection, "
            "branch_id, status_field, pending_value, running_value, done_value, failed_value, "
            "extra_filter, context_fields, prompt_template, field_mapping, "
            "schedule_interval_minutes, max_records_per_scan, agent) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tid, body['name'], body.get('enabled', True), owner_user_id, body['collection'],
             body.get('branchId', 'main'), body['statusField'], body.get('pendingValue', ''),
             body.get('runningValue', '处理中'), body.get('doneValue', '已处理'),
             body.get('failedValue', '处理失败'),
             psycopg2.extras.Json(body.get('extraFilter') or {}),
             psycopg2.extras.Json(body.get('contextFields') or {}),
             body['promptTemplate'], psycopg2.extras.Json(body.get('fieldMapping') or []),
             int(body.get('scheduleIntervalMinutes', 15)), int(body.get('maxRecordsPerScan', 20)),
             body.get('agent') or None),
        )
    return get_task(tid)
```

`_UPDATABLE` — add `'agent': 'agent'`:

```python
_UPDATABLE = {
    'name': 'name', 'enabled': 'enabled', 'collection': 'collection', 'branchId': 'branch_id',
    'statusField': 'status_field', 'pendingValue': 'pending_value', 'runningValue': 'running_value',
    'doneValue': 'done_value', 'failedValue': 'failed_value', 'promptTemplate': 'prompt_template',
    'scheduleIntervalMinutes': 'schedule_interval_minutes', 'maxRecordsPerScan': 'max_records_per_scan',
    'agent': 'agent',
}
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_scan_tasks.py -v 2>&1 | tail -15
```

Expected: all pass.

### 3b: ai_scan_engine.py — fetch and forward agent

- [ ] **Step 5: Update _load_task to fetch agent**

In `server/utils/ai_scan_engine.py`, change `_load_task`:

```python
def _load_task(task_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, collection, branch_id, status_field, done_value, "
                "failed_value, field_mapping, agent FROM ai_scan_tasks WHERE id = %s",
                (task_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {'id': r[0], 'collection': r[1], 'branch_id': r[2],
                    'status_field': r[3], 'done_value': r[4], 'failed_value': r[5],
                    'field_mapping': r[6] or [], 'agent': r[7] or ''}
```

- [ ] **Step 6: Update run_task to pass agent to create_batch**

In `server/utils/ai_scan_engine.py`, change the `create_batch` call in `run_task`:

```python
        create_batch(task['ownerUserId'], name=f"AI定时·{task['name']}·{stamp}",
                     prompt=prompt, template_id=None, files=files,
                     scan_task_id=task['id'],
                     agent=task.get('agent') or None)
```

- [ ] **Step 7: Run scan engine tests**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_engine.py -v 2>&1 | tail -10
```

Expected: all pass (agent=None is the default, existing tests unaffected).

- [ ] **Step 8: Commit**

```bash
git add server/utils/ai_scan_repo.py server/utils/ai_scan_engine.py server/tests/test_routes_ai_scan_tasks.py
git commit -m "feat(scan): add agent field to ai_scan_tasks, thread through to batch creation"
```

---

## Task 4: Frontend — Types + Batch Dialog

**Files:**
- Modify: `src/types/aiChatBatch.ts`
- Modify: `src/api/aiChatBatches.ts`
- Modify: `src/components/ai-chat/CreateBatchDialog.vue`

- [ ] **Step 1: Update AiChatBatch type**

In `src/types/aiChatBatch.ts`, add `agent` to `AiChatBatch`:

```typescript
export interface AiChatBatch {
  id: string
  user_id: string
  name: string
  prompt: string
  template_id: string | null
  agent: string | null
  status: BatchStatus
  total: number
  done: number
  failed: number
  created_at: string
  completed_at: string | null
}
```

- [ ] **Step 2: Update createBatch API type**

In `src/api/aiChatBatches.ts`, add `agent?` to the body type:

```typescript
export function createBatch(body: {
  name: string
  prompt: string
  template_id?: string | null
  agent?: string | null
  files: StagedFile[]
}) {
  return post<AiChatBatchDetail>('/ai/chat/batches', body)
}
```

- [ ] **Step 3: Add agent dropdown to CreateBatchDialog.vue**

Add the import for `listAgents` and `AgentInfo`:

```typescript
import { listAgents } from '@/api/aiChat'
import type { AgentInfo } from '@/api/aiChat'
```

Add the ref after `const submitting = ref(false)`:

```typescript
const selectedAgent = ref<string>('')
const agents = ref<AgentInfo[]>([])
```

Load agents in `onMounted`:

```typescript
onMounted(async () => {
  try { templates.value = await listTemplates() } catch { /* non-fatal */ }
  try {
    const r = await listAgents()
    agents.value = [...r.agents, ...r.subagents]
  } catch { /* non-fatal */ }
})
```

Add `selectedAgent` to `reset()`:

```typescript
function reset() {
  name.value = ''
  prompt.value = ''
  selectedTemplateId.value = null
  selectedAgent.value = ''
  stagedFiles.value = []
  saveAsTemplate.value = false
  templateName.value = ''
  uploadSessionId.value = crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  uploading.value = []
  failed.value = []
}
```

Pass agent in `submit()`:

```typescript
    const detail = await createBatch({
      name: name.value.trim(),
      prompt: prompt.value.trim(),
      template_id: selectedTemplateId.value,
      agent: selectedAgent.value || null,
      files: stagedFiles.value,
    })
```

Add the agent row to the template, after the Prompt row and before the saveAsTemplate row:

```html
      <div class="row">
        <label>Agent <span style="color:var(--el-text-color-placeholder);font-size:11px">（可选）</span></label>
        <ElSelect v-model="selectedAgent" placeholder="使用 OpenCode 默认 Agent" clearable>
          <ElOption v-for="a in agents" :key="a.name" :label="a.name" :value="a.name">
            <span>{{ a.name }}</span>
            <span v-if="a.description" style="color:#909399;font-size:11px;margin-left:6px">{{ a.description }}</span>
          </ElOption>
        </ElSelect>
      </div>
```

- [ ] **Step 4: Run frontend tests**

```bash
npx vitest run src/ 2>&1 | tail -8
```

Expected: all pass (no component tests for this dialog exist yet; type checks should pass).

- [ ] **Step 5: Commit**

```bash
git add src/types/aiChatBatch.ts src/api/aiChatBatches.ts src/components/ai-chat/CreateBatchDialog.vue
git commit -m "feat(batch-ui): add agent dropdown to CreateBatchDialog"
```

---

## Task 5: Frontend — Scan Task Manager

**Files:**
- Modify: `src/types/aiScanTask.ts`
- Modify: `src/views/admin/AiScanTaskManager.vue`

- [ ] **Step 1: Update AiScanTask type**

In `src/types/aiScanTask.ts`, add `agent`:

```typescript
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
  agent?: string | null
  lastRunAt?: string | null
  lastScanCount?: number
  lastError?: string | null
}
```

- [ ] **Step 2: Add agent to blank() in AiScanTaskManager.vue**

```typescript
function blank(): AiScanTask {
  return { id: '', name: '', enabled: true, collection: '', branchId: 'main', statusField: '',
    pendingValue: '', runningValue: '处理中', doneValue: '已处理', failedValue: '处理失败',
    extraFilter: {}, contextFields: {}, promptTemplate: '', fieldMapping: [],
    scheduleIntervalMinutes: 15, maxRecordsPerScan: 20, agent: null }
}
```

- [ ] **Step 3: Add listAgents import and agents ref**

Add imports at the top of the `<script setup>`:

```typescript
import { listAgents } from '@/api/aiChat'
import type { AgentInfo } from '@/api/aiChat'
```

Add ref:

```typescript
const agents = ref<AgentInfo[]>([])
```

Load agents in `onMounted`:

```typescript
onMounted(async () => {
  await store.load()
  if (store.tasks.length) await select(store.tasks[0].id)
  try {
    const r = await listAgents()
    agents.value = [...r.agents, ...r.subagents]
  } catch { /* non-fatal, agent dropdown stays empty */ }
})
```

- [ ] **Step 4: Add agent form item to the editor form**

Add after the `提示词` form item (before `字段映射`):

```html
        <el-form-item label="Agent">
          <el-select v-model="form.agent" placeholder="使用 OpenCode 默认 Agent" clearable style="width:300px">
            <el-option v-for="a in agents" :key="a.name" :label="a.name" :value="a.name">
              <span>{{ a.name }}</span>
              <span v-if="a.description" style="color:#909399;font-size:11px;margin-left:6px">{{ a.description }}</span>
            </el-option>
          </el-select>
          <div class="hint">选择后，该任务的所有 AI 会话将使用指定 Agent 执行</div>
        </el-form-item>
```

- [ ] **Step 5: Run type check**

```bash
npx vue-tsc --noEmit 2>&1 | grep -E "error|warning" | head -20
```

Expected: no errors related to `agent` field.

- [ ] **Step 6: Run frontend tests**

```bash
npx vitest run src/ 2>&1 | tail -8
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/types/aiScanTask.ts src/views/admin/AiScanTaskManager.vue
git commit -m "feat(scan-ui): add agent dropdown to AiScanTaskManager"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Batch tasks: DB column → repo → engine `send_message(agent=)` → route POST body → frontend dropdown
- ✅ Scan tasks: DB column → repo `_FIELDS`/`_CAMEL`/`create_task`/`_UPDATABLE` → `_load_task` → `run_task` → `create_batch(agent=)` → frontend dropdown
- ✅ Existing `batch_engine._notify_scan` path unaffected (scan uses `run_task` → `create_batch`)
- ✅ Backward-compatible: `agent` is nullable in DB and optional in all API bodies; existing tests and data unaffected

**Placeholder scan:** No TBDs, no "fill in later" — all code is explicit.

**Type consistency:**
- `create_batch(..., agent=)` introduced in Task 2 and used in Task 3 with same signature ✅
- `_fetch_batch_context` returns `tuple[str, str]` and is destructured to `(prompt, agent)` in `_run_one` ✅
- `_FIELDS`/`_CAMEL` positionally aligned — `agent` appended at position 22 in both ✅
- `AgentInfo` imported from `@/api/aiChat` in both frontend components ✅
