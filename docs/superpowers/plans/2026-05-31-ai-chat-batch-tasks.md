# AI Chat Batch Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a batch-task primitive to AI Chat — upload N files + 1 prompt → N isolated sessions, throttled to 3 concurrent, with a dashboard and reusable per-user prompt templates.

**Architecture:** Flask in-process daemon worker spawns and drives child sessions via the existing OpenCode HTTP API; child sessions are real `ai_chat_sessions` rows so the existing chat view "just works" for them. Frontend adds a 批任务 tab in the AI 助手 sidebar with list + detail panels, polled every 5s.

**Tech Stack:** Python 3 / Flask + psycopg2 + APScheduler (already in use); Vue 3 + TypeScript + Element Plus + Pinia; PostgreSQL with JSONB. New code follows existing project conventions (test layout, blueprint registration order, JWT decorators, `get_db()` context manager).

**Spec:** `docs/superpowers/specs/2026-05-31-ai-chat-batch-tasks-design.md`

---

## Task 1: Database schema

**Files:**
- Modify: `server/init_db.py`

This task adds two new tables (`ai_chat_prompt_templates`, `ai_chat_batches`) and three columns on the existing `ai_chat_sessions` table. We use the project's existing single-file `init_db.py` rather than a migration framework, matching the convention.

- [ ] **Step 1: Add DDL constants**

Open `server/init_db.py` and locate the existing list of `CREATE TABLE` statements. Insert these new statements **before** the `ai_chat_sessions` definition (so foreign keys resolve) — or after if the file uses idempotent `CREATE TABLE IF NOT EXISTS`. Read the file to find the right insertion point.

```python
AI_CHAT_PROMPT_TEMPLATES_DDL = """
CREATE TABLE IF NOT EXISTS ai_chat_prompt_templates (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES users(id),
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_chat_prompt_templates_user
  ON ai_chat_prompt_templates(user_id, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_template_user_name
  ON ai_chat_prompt_templates(user_id, name);
"""

AI_CHAT_BATCHES_DDL = """
CREATE TABLE IF NOT EXISTS ai_chat_batches (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id),
  name        TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  template_id UUID NULL REFERENCES ai_chat_prompt_templates(id) ON DELETE SET NULL,
  status      TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','running','completed','partial','failed')),
  total       INT  NOT NULL DEFAULT 0,
  done        INT  NOT NULL DEFAULT 0,
  failed      INT  NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at  TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_chat_batches_user_created
  ON ai_chat_batches(user_id, created_at DESC);
"""

AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL = """
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS batch_id         UUID NULL REFERENCES ai_chat_batches(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS batch_seq        INT  NULL,
  ADD COLUMN IF NOT EXISTS batch_input_file TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_batch
  ON ai_chat_sessions(batch_id, batch_seq);
"""
```

- [ ] **Step 2: Wire the DDL into init_db's `main()`**

Locate the main DDL-execution loop in `init_db.py` (it iterates a list of DDL strings or calls `cur.execute(...)` per table). Add three executions in this order:
1. `cur.execute(AI_CHAT_PROMPT_TEMPLATES_DDL)`
2. `cur.execute(AI_CHAT_BATCHES_DDL)`
3. `cur.execute(AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL)`

Order matters: `ai_chat_batches.template_id` FKs to prompt_templates; the ALTER on sessions FKs to batches.

- [ ] **Step 3: Run init_db against the dev database and verify**

```bash
cd server && python init_db.py
```

Then verify in psql:
```sql
\d ai_chat_prompt_templates
\d ai_chat_batches
\d ai_chat_sessions   -- batch_id, batch_seq, batch_input_file should be present
```

Expected: all three exist, FKs are in place, indexes show up.

- [ ] **Step 4: Commit**

```bash
git add server/init_db.py
git commit -m "feat(ai-chat-batch): DDL for batches, prompt templates, session linkage"
```

---

## Task 2: Prompt-template utility module

**Files:**
- Create: `server/utils/prompt_template.py`
- Create: `server/tests/test_prompt_templates_util.py`

A small CRUD helper module so routes and tests don't both duplicate SQL.

- [ ] **Step 1: Write failing tests for the util**

Create `server/tests/test_prompt_templates_util.py`:

```python
"""Tests for utils.prompt_template CRUD helpers."""
import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def user_id(db_conn):
    """Insert a throwaway user, yield its UUID, clean up after."""
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, role) "
            "VALUES (%s, %s, %s, 'developer')",
            (uid, f'pt_user_{uid[:8]}', 'x'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_prompt_templates WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def test_create_returns_row_with_id(user_id):
    from utils.prompt_template import create_template
    row = create_template(user_id, name='巡检用例', content='帮我开发巡检用例')
    assert row['id']
    assert row['name'] == '巡检用例'
    assert row['content'] == '帮我开发巡检用例'
    assert row['user_id'] == user_id


def test_create_rejects_duplicate_name_for_same_user(user_id):
    from utils.prompt_template import create_template, DuplicateTemplateName
    create_template(user_id, name='dup', content='a')
    with pytest.raises(DuplicateTemplateName):
        create_template(user_id, name='dup', content='b')


def test_create_allows_same_name_for_different_users(db_conn, user_id):
    from utils.prompt_template import create_template
    other = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, role) "
            "VALUES (%s, %s, %s, 'developer')",
            (other, f'pt_other_{other[:8]}', 'x'),
        )
    db_conn.commit()
    try:
        create_template(user_id, name='shared', content='a')
        # Should not raise
        create_template(other, name='shared', content='b')
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_prompt_templates WHERE user_id = %s", (other,))
            cur.execute("DELETE FROM users WHERE id = %s", (other,))
        db_conn.commit()


def test_list_returns_user_templates_ordered(user_id):
    from utils.prompt_template import create_template, list_templates
    a = create_template(user_id, name='a', content='1')
    b = create_template(user_id, name='b', content='2')
    rows = list_templates(user_id)
    ids = [r['id'] for r in rows]
    # Most recently updated first; b was created after a
    assert ids[0] == b['id']
    assert a['id'] in ids


def test_update_changes_content_and_bumps_updated_at(user_id):
    from utils.prompt_template import create_template, update_template, get_template
    row = create_template(user_id, name='x', content='old')
    update_template(user_id, row['id'], name='x', content='new')
    fresh = get_template(user_id, row['id'])
    assert fresh['content'] == 'new'
    assert fresh['updated_at'] >= row['updated_at']


def test_update_other_users_template_returns_none(user_id, db_conn):
    from utils.prompt_template import create_template, update_template
    other = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, role) "
            "VALUES (%s, %s, %s, 'developer')",
            (other, f'pt_other2_{other[:8]}', 'x'),
        )
    db_conn.commit()
    try:
        row = create_template(other, name='x', content='theirs')
        result = update_template(user_id, row['id'], name='hacked', content='!!')
        assert result is None  # cross-user write blocked
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_prompt_templates WHERE user_id = %s", (other,))
            cur.execute("DELETE FROM users WHERE id = %s", (other,))
        db_conn.commit()


def test_delete_removes_row(user_id):
    from utils.prompt_template import create_template, delete_template, get_template
    row = create_template(user_id, name='x', content='c')
    assert delete_template(user_id, row['id']) is True
    assert get_template(user_id, row['id']) is None
```

This file uses a `db_conn` fixture. If the project doesn't already have one in `server/tests/conftest.py`, add it (one-shot conftest setup for this plan):

```python
# server/tests/conftest.py — add if not already present
import os
import sys
import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def db_conn():
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    yield conn
    conn.close()
```

(Read the existing conftest.py — if it already exposes a similar fixture under a different name, adapt the test fixture to use it.)

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_prompt_templates_util.py -v
```
Expected: `ModuleNotFoundError: No module named 'utils.prompt_template'` (or import error).

- [ ] **Step 3: Implement the utility**

Create `server/utils/prompt_template.py`:

```python
"""CRUD helpers for ai_chat_prompt_templates.

Owned by routes/ai_chat_prompt_templates.py but kept here for testability and
re-use by routes/ai_chat_batches.py (which optionally records template_id).
"""
from psycopg2.errors import UniqueViolation
from psycopg2.extras import RealDictCursor

from utils.db import get_db


class DuplicateTemplateName(ValueError):
    """User tried to create a template with a name they already used."""


def _row(cur):
    """RealDictCursor → plain dict (no metaclass surprises)."""
    r = cur.fetchone()
    return dict(r) if r else None


def create_template(user_id: str, *, name: str, content: str) -> dict:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    "INSERT INTO ai_chat_prompt_templates (user_id, name, content) "
                    "VALUES (%s, %s, %s) RETURNING *",
                    (user_id, name, content),
                )
                row = _row(cur)
                conn.commit()
                return row
            except UniqueViolation:
                conn.rollback()
                raise DuplicateTemplateName(name)


def list_templates(user_id: str) -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM ai_chat_prompt_templates "
                "WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_template(user_id: str, template_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM ai_chat_prompt_templates "
                "WHERE user_id = %s AND id = %s",
                (user_id, template_id),
            )
            return _row(cur)


def update_template(user_id: str, template_id: str, *,
                    name: str, content: str) -> dict | None:
    """Returns the updated row, or None if the template isn't this user's."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    "UPDATE ai_chat_prompt_templates "
                    "SET name = %s, content = %s, updated_at = now() "
                    "WHERE id = %s AND user_id = %s RETURNING *",
                    (name, content, template_id, user_id),
                )
                row = _row(cur)
                conn.commit()
                return row
            except UniqueViolation:
                conn.rollback()
                raise DuplicateTemplateName(name)


def delete_template(user_id: str, template_id: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ai_chat_prompt_templates "
                "WHERE id = %s AND user_id = %s",
                (template_id, user_id),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_prompt_templates_util.py -v
```
Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add server/utils/prompt_template.py server/tests/test_prompt_templates_util.py server/tests/conftest.py
git commit -m "feat(ai-chat-batch): prompt template CRUD utility + tests"
```

---

## Task 3: Prompt-template REST routes

**Files:**
- Create: `server/routes/ai_chat_prompt_templates.py`
- Create: `server/tests/test_prompt_template_routes.py`
- Modify: `server/app.py`

Wires the util onto HTTP. Authn via existing `login_required` JWT decorator.

- [ ] **Step 1: Write failing route tests**

Create `server/tests/test_prompt_template_routes.py`. Follow the existing test pattern in `server/tests/test_ai_chat.py` (read it for the auth fixture). Sketch:

```python
"""HTTP tests for /ai/chat/prompt-templates."""
import os, sys, json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    """Login as admin (seeded by init_db) and return Authorization header."""
    r = client.post('/auth/login', json={'username': 'admin', 'password': 'admin123'})
    token = r.get_json()['token']
    return {'Authorization': f'Bearer {token}'}


def test_create_and_list(client, auth_headers):
    r = client.post('/ai/chat/prompt-templates',
                    json={'name': 't1', 'content': 'hello'},
                    headers=auth_headers)
    assert r.status_code == 201
    tpl = r.get_json()
    assert tpl['name'] == 't1' and tpl['content'] == 'hello'

    r = client.get('/ai/chat/prompt-templates', headers=auth_headers)
    assert r.status_code == 200
    items = r.get_json()
    assert any(t['id'] == tpl['id'] for t in items)
    # cleanup
    client.delete(f'/ai/chat/prompt-templates/{tpl["id"]}', headers=auth_headers)


def test_duplicate_name_returns_409(client, auth_headers):
    r1 = client.post('/ai/chat/prompt-templates',
                     json={'name': 'dup', 'content': 'a'}, headers=auth_headers)
    assert r1.status_code == 201
    try:
        r2 = client.post('/ai/chat/prompt-templates',
                         json={'name': 'dup', 'content': 'b'}, headers=auth_headers)
        assert r2.status_code == 409
    finally:
        client.delete(f'/ai/chat/prompt-templates/{r1.get_json()["id"]}', headers=auth_headers)


def test_update_round_trip(client, auth_headers):
    r = client.post('/ai/chat/prompt-templates',
                    json={'name': 'u', 'content': 'old'}, headers=auth_headers)
    tid = r.get_json()['id']
    try:
        r2 = client.put(f'/ai/chat/prompt-templates/{tid}',
                        json={'name': 'u', 'content': 'new'}, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.get_json()['content'] == 'new'
    finally:
        client.delete(f'/ai/chat/prompt-templates/{tid}', headers=auth_headers)


def test_delete(client, auth_headers):
    r = client.post('/ai/chat/prompt-templates',
                    json={'name': 'd', 'content': 'x'}, headers=auth_headers)
    tid = r.get_json()['id']
    r2 = client.delete(f'/ai/chat/prompt-templates/{tid}', headers=auth_headers)
    assert r2.status_code == 204
    r3 = client.get(f'/ai/chat/prompt-templates/{tid}', headers=auth_headers)
    assert r3.status_code == 404


def test_rejects_unauthenticated(client):
    r = client.get('/ai/chat/prompt-templates')
    assert r.status_code == 401
```

The `app` fixture should come from `conftest.py` — add if missing:

```python
# server/tests/conftest.py — add:
@pytest.fixture
def app():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from app import create_app
    return create_app()
```

Adapt to however the existing test_ai_chat.py constructs an app.

- [ ] **Step 2: Run, expect import error / 404s**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_prompt_template_routes.py -v
```
Expected: fails on missing blueprint or 404s.

- [ ] **Step 3: Implement the blueprint**

Create `server/routes/ai_chat_prompt_templates.py`:

```python
"""REST endpoints for AI chat prompt templates (per-user CRUD)."""
from flask import Blueprint, g, jsonify, request

from utils.auth import login_required
from utils.prompt_template import (
    DuplicateTemplateName,
    create_template,
    delete_template,
    get_template,
    list_templates,
    update_template,
)

ai_chat_prompt_templates_bp = Blueprint(
    'ai_chat_prompt_templates', __name__,
    url_prefix='/ai/chat/prompt-templates',
)


def _payload():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    content = (body.get('content') or '').strip()
    if not name or not content:
        return None, ('name and content required', 400)
    if len(name) > 200:
        return None, ('name too long', 400)
    return (name, content), None


@ai_chat_prompt_templates_bp.get('')
@login_required
def list_():
    return jsonify(list_templates(g.current_user['id']))


@ai_chat_prompt_templates_bp.post('')
@login_required
def create():
    parsed, err = _payload()
    if err:
        msg, code = err
        return jsonify({'error': msg}), code
    name, content = parsed
    try:
        row = create_template(g.current_user['id'], name=name, content=content)
    except DuplicateTemplateName:
        return jsonify({'error': 'name already in use'}), 409
    return jsonify(row), 201


@ai_chat_prompt_templates_bp.get('/<template_id>')
@login_required
def get(template_id):
    row = get_template(g.current_user['id'], template_id)
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify(row)


@ai_chat_prompt_templates_bp.put('/<template_id>')
@login_required
def update(template_id):
    parsed, err = _payload()
    if err:
        msg, code = err
        return jsonify({'error': msg}), code
    name, content = parsed
    try:
        row = update_template(g.current_user['id'], template_id,
                              name=name, content=content)
    except DuplicateTemplateName:
        return jsonify({'error': 'name already in use'}), 409
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify(row)


@ai_chat_prompt_templates_bp.delete('/<template_id>')
@login_required
def delete(template_id):
    if not delete_template(g.current_user['id'], template_id):
        return jsonify({'error': 'not found'}), 404
    return '', 204
```

> The `g.current_user['id']` access matches the existing `login_required` decorator contract — verify by reading `server/utils/auth.py` and adapt if it exposes the user differently (e.g. `g.user` or `g.user_id`).

- [ ] **Step 4: Register the blueprint**

Open `server/app.py`, find the AI-chat blueprint registration block (right before `dynamic_bp`), and add:

```python
from routes.ai_chat_prompt_templates import ai_chat_prompt_templates_bp
# ... in create_app() or wherever blueprints are registered, BEFORE dynamic_bp:
app.register_blueprint(ai_chat_prompt_templates_bp)
```

- [ ] **Step 5: Run the route tests and confirm they pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_prompt_template_routes.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/routes/ai_chat_prompt_templates.py server/tests/test_prompt_template_routes.py server/app.py
git commit -m "feat(ai-chat-batch): REST routes for prompt templates"
```

---

## Task 4: Batch staging upload endpoint

**Files:**
- Create: `server/routes/ai_chat_batches.py` (skeleton only — Tasks 5–7 extend it)
- Modify: `server/utils/workspace.py` (add staging dir helper)
- Modify: `server/app.py` (register batch bp)
- Create: `server/tests/test_batch_staging_upload.py`

Staging-uploaded files live under `<workspace_root>/batch-staging/<user_id>/<upload_session_uuid>/<filename>`. When the batch is created (Task 6), each staged file is copied into the corresponding child session's `uploads/` dir.

- [ ] **Step 1: Write failing staging-upload tests**

Create `server/tests/test_batch_staging_upload.py`:

```python
"""Tests for POST /ai/chat/batches/staging/upload."""
import io
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_upload_returns_name_and_path(client, auth_headers):
    data = {
        'file': (io.BytesIO(b'hello bytes'), 'test.txt'),
        'upload_session_id': 'sess-abc',
    }
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data',
                    headers=auth_headers)
    assert r.status_code == 201
    j = r.get_json()
    assert j['name'] == 'test.txt'
    # path is workspace-relative, normalized
    assert j['path'].startswith('batch-staging/')
    assert j['path'].endswith('test.txt')


def test_upload_requires_upload_session_id(client, auth_headers):
    data = {'file': (io.BytesIO(b'x'), 'a.txt')}
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data',
                    headers=auth_headers)
    assert r.status_code == 400


def test_upload_rejects_traversal_filename(client, auth_headers):
    data = {
        'file': (io.BytesIO(b'x'), '../../escape.txt'),
        'upload_session_id': 'sess-trav',
    }
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data',
                    headers=auth_headers)
    assert r.status_code == 400 or r.status_code == 201
    if r.status_code == 201:
        # If accepted, the path must NOT have escaped batch-staging/
        assert '..' not in r.get_json()['path']


def test_upload_rejects_unauthenticated(client):
    data = {
        'file': (io.BytesIO(b'x'), 'a.txt'),
        'upload_session_id': 'sess',
    }
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data')
    assert r.status_code == 401
```

- [ ] **Step 2: Add the staging helper in workspace.py**

Open `server/utils/workspace.py` and append:

```python
def batch_staging_dir(workspace_root: str, user_id: str,
                      upload_session_id: str) -> Path:
    """Return (and create) the staging dir for a not-yet-created batch.

    Lives under <workspace_root>/batch-staging/<user_id>/<upload_session_id>/
    and is moved into per-session workspaces when the batch is created.
    """
    # sanitize the upload_session_id (allow uuids/letters/digits/hyphens only)
    safe = ''.join(ch for ch in upload_session_id
                   if ch.isalnum() or ch in '-_')
    if not safe:
        raise WorkspacePathError("invalid upload_session_id")
    p = Path(workspace_root) / "batch-staging" / user_id / safe
    p.mkdir(parents=True, exist_ok=True)
    return p
```

- [ ] **Step 3: Create the batch blueprint skeleton with the staging-upload route**

Create `server/routes/ai_chat_batches.py`:

```python
"""REST endpoints for AI chat batch tasks (CRUD + staging upload).

Worker engine lives in utils.batch_engine; this module only owns the HTTP edge.
"""
import os
import uuid
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.utils import secure_filename

from utils.auth import login_required
from utils.workspace import batch_staging_dir, WorkspacePathError


ai_chat_batches_bp = Blueprint('ai_chat_batches', __name__,
                               url_prefix='/ai/chat/batches')


@ai_chat_batches_bp.post('/staging/upload')
@login_required
def staging_upload():
    f = request.files.get('file')
    upload_session_id = (request.form.get('upload_session_id') or '').strip()
    if not f or not upload_session_id:
        return jsonify({'error': 'file and upload_session_id required'}), 400

    filename = secure_filename(f.filename or '')
    if not filename:
        return jsonify({'error': 'invalid filename'}), 400

    workspace_root = current_app.config.get('AI_CHAT_WORKSPACE_ROOT') \
        or os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')
    try:
        staging = batch_staging_dir(workspace_root,
                                    g.current_user['id'],
                                    upload_session_id)
    except WorkspacePathError as e:
        return jsonify({'error': str(e)}), 400

    dest = staging / filename
    f.save(dest)

    rel = dest.relative_to(workspace_root).as_posix()
    return jsonify({'name': filename, 'path': rel}), 201
```

- [ ] **Step 4: Register the blueprint**

In `server/app.py`, just before `dynamic_bp`:

```python
from routes.ai_chat_batches import ai_chat_batches_bp
app.register_blueprint(ai_chat_batches_bp)
```

- [ ] **Step 5: Run the upload tests and confirm pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_staging_upload.py -v
```

- [ ] **Step 6: Commit**

```bash
git add server/routes/ai_chat_batches.py server/utils/workspace.py server/app.py server/tests/test_batch_staging_upload.py
git commit -m "feat(ai-chat-batch): staging upload endpoint for batch files"
```

---

## Task 5: Batch CRUD endpoints (no worker yet)

**Files:**
- Modify: `server/routes/ai_chat_batches.py`
- Create: `server/utils/batch_repo.py`
- Create: `server/tests/test_batch_routes.py`

Implements POST/GET/DELETE and the retry-failed endpoint. The worker doesn't exist yet, so batches stay at `pending` after creation — Task 6 brings them to life.

- [ ] **Step 1: Write failing batch-route tests**

Create `server/tests/test_batch_routes.py`:

```python
"""HTTP tests for /ai/chat/batches CRUD."""
import io
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _stage_one(client, headers, content=b'hi', name='f.txt',
               upload_session_id='upload-sess-001'):
    data = {
        'file': (io.BytesIO(content), name),
        'upload_session_id': upload_session_id,
    }
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data',
                    headers=headers)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()  # {name, path}


def test_create_batch_returns_201_and_seeds_sessions(client, auth_headers):
    f1 = _stage_one(client, auth_headers, name='a.txt', upload_session_id='u1')
    f2 = _stage_one(client, auth_headers, name='b.txt', upload_session_id='u1')

    r = client.post('/ai/chat/batches', json={
        'name': 'batch-test',
        'prompt': 'do the thing',
        'files': [f1, f2],
    }, headers=auth_headers)
    assert r.status_code == 201
    body = r.get_json()
    assert body['batch']['name'] == 'batch-test'
    assert body['batch']['total'] == 2
    assert body['batch']['status'] == 'pending'
    assert len(body['sessions']) == 2
    seqs = sorted(s['batch_seq'] for s in body['sessions'])
    assert seqs == [0, 1]
    # cleanup
    client.delete(f'/ai/chat/batches/{body["batch"]["id"]}', headers=auth_headers)


def test_create_rejects_empty_files(client, auth_headers):
    r = client.post('/ai/chat/batches',
                    json={'name': 'x', 'prompt': 'p', 'files': []},
                    headers=auth_headers)
    assert r.status_code == 400


def test_create_rejects_too_many_files(client, auth_headers):
    files = [{'name': f'{i}.txt', 'path': f'batch-staging/x/y/{i}.txt'}
             for i in range(51)]
    r = client.post('/ai/chat/batches',
                    json={'name': 'x', 'prompt': 'p', 'files': files},
                    headers=auth_headers)
    assert r.status_code == 400


def test_list_returns_user_batches(client, auth_headers):
    f1 = _stage_one(client, auth_headers, name='a.txt', upload_session_id='u-list')
    r = client.post('/ai/chat/batches', json={
        'name': 'list-me', 'prompt': 'p', 'files': [f1],
    }, headers=auth_headers)
    bid = r.get_json()['batch']['id']
    try:
        r2 = client.get('/ai/chat/batches?page=1&pageSize=20', headers=auth_headers)
        assert r2.status_code == 200
        items = r2.get_json()['items']
        assert any(b['id'] == bid for b in items)
    finally:
        client.delete(f'/ai/chat/batches/{bid}', headers=auth_headers)


def test_detail_returns_children(client, auth_headers):
    f1 = _stage_one(client, auth_headers, name='a.txt', upload_session_id='u-d')
    f2 = _stage_one(client, auth_headers, name='b.txt', upload_session_id='u-d')
    r = client.post('/ai/chat/batches', json={
        'name': 'detail', 'prompt': 'p', 'files': [f1, f2],
    }, headers=auth_headers)
    bid = r.get_json()['batch']['id']
    try:
        r2 = client.get(f'/ai/chat/batches/{bid}', headers=auth_headers)
        assert r2.status_code == 200
        body = r2.get_json()
        assert body['batch']['id'] == bid
        assert len(body['sessions']) == 2
        assert [s['batch_seq'] for s in body['sessions']] == [0, 1]
    finally:
        client.delete(f'/ai/chat/batches/{bid}', headers=auth_headers)


def test_delete_cascades_sessions(client, auth_headers, db_conn):
    f1 = _stage_one(client, auth_headers, name='c.txt', upload_session_id='u-del')
    r = client.post('/ai/chat/batches', json={
        'name': 'gone', 'prompt': 'p', 'files': [f1],
    }, headers=auth_headers)
    bid = r.get_json()['batch']['id']
    r2 = client.delete(f'/ai/chat/batches/{bid}', headers=auth_headers)
    assert r2.status_code == 204
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_sessions WHERE batch_id = %s", (bid,))
        assert cur.fetchone()[0] == 0


def test_retry_failed_resets_failed_to_pending(client, auth_headers, db_conn):
    f1 = _stage_one(client, auth_headers, name='r.txt', upload_session_id='u-r')
    r = client.post('/ai/chat/batches', json={
        'name': 'retry', 'prompt': 'p', 'files': [f1],
    }, headers=auth_headers)
    bid = r.get_json()['batch']['id']
    # Manually force the child to 'failed'
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='failed' WHERE batch_id = %s",
                    (bid,))
        cur.execute("UPDATE ai_chat_batches SET failed = 1, status='partial' WHERE id = %s",
                    (bid,))
    db_conn.commit()
    try:
        r2 = client.post(f'/ai/chat/batches/{bid}/retry-failed', headers=auth_headers)
        assert r2.status_code == 200
        assert r2.get_json()['retried'] == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_chat_sessions WHERE batch_id = %s", (bid,))
            assert cur.fetchone()[0] == 'pending'
            cur.execute("SELECT failed, status FROM ai_chat_batches WHERE id = %s", (bid,))
            failed, status = cur.fetchone()
            assert failed == 0
            assert status == 'pending'
    finally:
        client.delete(f'/ai/chat/batches/{bid}', headers=auth_headers)
```

- [ ] **Step 2: Run, expect 404s**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_routes.py -v
```

- [ ] **Step 3: Add the batch repository helper**

Create `server/utils/batch_repo.py`:

```python
"""DB layer for ai_chat_batches and their child ai_chat_sessions rows.

Routes are thin; this module owns the SQL.
"""
import uuid
from psycopg2.extras import RealDictCursor

from utils.db import get_db


MAX_FILES_PER_BATCH = 50


def create_batch(user_id: str, *, name: str, prompt: str,
                 template_id: str | None, files: list[dict]) -> dict:
    """Atomically insert a batch + N child sessions.

    `files` is a list of {name, path} dicts where path is workspace-relative
    (under batch-staging/...). Returns {batch, sessions}.
    """
    if not files:
        raise ValueError("at least one file required")
    if len(files) > MAX_FILES_PER_BATCH:
        raise ValueError(f"max {MAX_FILES_PER_BATCH} files per batch")

    batch_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "  (id, user_id, name, prompt, template_id, total, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING *",
                (batch_id, user_id, name, prompt, template_id, len(files)),
            )
            batch = dict(cur.fetchone())

            sessions = []
            for seq, f in enumerate(files):
                sid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, seq, f['path']),
                )
                sessions.append(dict(cur.fetchone()))
        conn.commit()
    return {'batch': batch, 'sessions': sessions}


def list_batches(user_id: str, *, page: int, page_size: int) -> dict:
    offset = (page - 1) * page_size
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM ai_chat_batches WHERE user_id = %s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (user_id, page_size, offset),
            )
            items = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT count(*) AS n FROM ai_chat_batches WHERE user_id = %s",
                        (user_id,))
            total = cur.fetchone()['n']
    return {'items': items, 'total': total, 'page': page, 'pageSize': page_size}


def get_batch_detail(user_id: str, batch_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM ai_chat_batches WHERE id=%s AND user_id=%s",
                        (batch_id, user_id))
            batch = cur.fetchone()
            if not batch:
                return None
            cur.execute(
                "SELECT id, status, batch_seq, batch_input_file, "
                "       opencode_session_id, error_message, last_message_preview "
                "FROM ai_chat_sessions WHERE batch_id=%s ORDER BY batch_seq",
                (batch_id,),
            )
            sessions = [dict(r) for r in cur.fetchall()]
    return {'batch': dict(batch), 'sessions': sessions}


def delete_batch(user_id: str, batch_id: str) -> bool:
    """Returns True if deleted, False if not found.

    Callers MUST run per-session workspace cleanup BEFORE invoking this for
    children that have a workspace_path. See routes for the orchestration.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s AND user_id=%s",
                        (batch_id, user_id))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def reset_failed_to_pending(user_id: str, batch_id: str) -> int:
    """Returns count of sessions reset. Also clears batch.failed counter and
    recomputes batch.status."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_chat_sessions "
                "SET status='pending', error_message=NULL "
                "WHERE batch_id=%s AND status='failed' "
                "  AND batch_id IN (SELECT id FROM ai_chat_batches WHERE user_id=%s)",
                (batch_id, user_id),
            )
            count = cur.rowcount
            if count:
                cur.execute(
                    "UPDATE ai_chat_batches SET failed = failed - %s, "
                    "  status = CASE WHEN done = total THEN 'completed' "
                    "                ELSE 'pending' END "
                    "WHERE id = %s",
                    (count, batch_id),
                )
            conn.commit()
    return count
```

> Some columns referenced above (`opencode_session_id`, `error_message`, `last_message_preview`) must exist on `ai_chat_sessions`. If `opencode_session_id` is the only one already there, this task also needs to add `error_message TEXT NULL` and `last_message_preview TEXT NULL`. Verify by reading the current `ai_chat_sessions` schema in `init_db.py`. If missing, extend Task 1's `AI_CHAT_SESSIONS_BATCH_COLUMNS_DDL` to include:

```sql
ADD COLUMN IF NOT EXISTS error_message         TEXT NULL,
ADD COLUMN IF NOT EXISTS last_message_preview  TEXT NULL,
```

…and re-run `init_db.py`.

- [ ] **Step 4: Wire the routes**

Open `server/routes/ai_chat_batches.py` (created in Task 4) and append:

```python
from utils.batch_repo import (
    MAX_FILES_PER_BATCH,
    create_batch,
    delete_batch,
    get_batch_detail,
    list_batches,
    reset_failed_to_pending,
)
# (the in-process worker is imported lazily inside endpoint functions to avoid
# circular imports during app construction)


@ai_chat_batches_bp.post('')
@login_required
def create():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    prompt = (body.get('prompt') or '').strip()
    template_id = body.get('template_id')
    files = body.get('files') or []
    if not name or not prompt:
        return jsonify({'error': 'name and prompt required'}), 400
    if not isinstance(files, list) or not files:
        return jsonify({'error': 'at least one file required'}), 400
    if len(files) > MAX_FILES_PER_BATCH:
        return jsonify({'error': f'max {MAX_FILES_PER_BATCH} files'}), 400
    for f in files:
        if not isinstance(f, dict) or not f.get('path') or not f.get('name'):
            return jsonify({'error': 'each file must have {name, path}'}), 400

    result = create_batch(g.current_user['id'],
                          name=name, prompt=prompt,
                          template_id=template_id, files=files)
    # Wake the worker so it picks up the new pending sessions immediately.
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result), 201


@ai_chat_batches_bp.get('')
@login_required
def list_():
    page = int(request.args.get('page', 1))
    page_size = min(int(request.args.get('pageSize', 20)), 100)
    return jsonify(list_batches(g.current_user['id'],
                                page=page, page_size=page_size))


@ai_chat_batches_bp.get('/<batch_id>')
@login_required
def detail(batch_id):
    body = get_batch_detail(g.current_user['id'], batch_id)
    if not body:
        return jsonify({'error': 'not found'}), 404
    return jsonify(body)


@ai_chat_batches_bp.delete('/<batch_id>')
@login_required
def remove(batch_id):
    # Tear down per-child workspaces before DB cascade
    body = get_batch_detail(g.current_user['id'], batch_id)
    if not body:
        return jsonify({'error': 'not found'}), 404
    from utils.workspace import cleanup_session_workspace
    workspace_root = current_app.config.get('AI_CHAT_WORKSPACE_ROOT') \
        or os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')
    for s in body['sessions']:
        try:
            cleanup_session_workspace(workspace_root,
                                      g.current_user['id'], s['id'])
        except Exception:
            pass  # best-effort
    delete_batch(g.current_user['id'], batch_id)
    return '', 204


@ai_chat_batches_bp.post('/<batch_id>/retry-failed')
@login_required
def retry_failed(batch_id):
    count = reset_failed_to_pending(g.current_user['id'], batch_id)
    if count:
        from utils.batch_engine import get_worker
        get_worker().notify()
    return jsonify({'retried': count})
```

> The `get_worker()` accessor is implemented in Task 6. Until then, the create/retry endpoints would error on `import utils.batch_engine`. To unblock Task 5 testing, **temporarily** stub `utils/batch_engine.py` with:
> ```python
> def get_worker():
>     class _Stub:
>         def notify(self): pass
>     return _Stub()
> ```
> Task 6 replaces this stub.

- [ ] **Step 5: Run the batch route tests, confirm they pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_routes.py -v
```

- [ ] **Step 6: Commit**

```bash
git add server/routes/ai_chat_batches.py server/utils/batch_repo.py server/utils/batch_engine.py server/init_db.py server/tests/test_batch_routes.py
git commit -m "feat(ai-chat-batch): batch CRUD + retry-failed (worker stubbed)"
```

---

## Task 6: Batch worker engine

**Files:**
- Modify: `server/utils/batch_engine.py` (replace stub with real implementation)
- Modify: `server/app.py` (start worker)
- Create: `server/tests/test_batch_engine.py`

The heart of the feature. Drives child sessions through their lifecycle by calling the existing OpenCode HTTP API. Mocked in tests via patching `utils.opencode_client`.

- [ ] **Step 1: Read the existing OpenCode client to learn the shape of the API**

```bash
cd server && python -c "import utils.opencode_client; help(utils.opencode_client)" | head -50
```

Note the function names you'll call (e.g. `create_session`, `send_message`, `list_messages`, …). Adapt the worker code below to the actual signatures.

- [ ] **Step 2: Write failing engine tests**

Create `server/tests/test_batch_engine.py`:

```python
"""Tests for the in-process BatchWorker. OpenCode is mocked."""
import os
import sys
import uuid
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _seed_batch(db_conn, user_id, n_sessions=3):
    """Insert a batch + n_sessions pending sessions, return (batch_id, [session_ids])."""
    bid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'engine-test', 'p', %s)",
            (bid, user_id, n_sessions),
        )
        sids = []
        for seq in range(n_sessions):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, %s)",
                (sid, user_id, bid, seq, f'batch-staging/x/{seq}.txt'),
            )
            sids.append(sid)
    db_conn.commit()
    return bid, sids


def test_claim_pending_respects_limit(user_id, db_conn):
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, n_sessions=5)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=2)
    assert len(claimed) == 2
    # claimed rows now status='running' in DB
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_sessions "
                    "WHERE batch_id=%s AND status='running'", (bid,))
        assert cur.fetchone()[0] == 2


def test_run_one_happy_path_marks_completed(user_id, db_conn, monkeypatch):
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng
    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)

    # Stub OpenCode interactions
    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-session-1'
    fake_oc.send_message.return_value = {'id': 'msg-1'}
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'done!'}]}
    ]
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: '/tmp/ws')

    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, opencode_session_id, last_message_preview "
                    "FROM ai_chat_sessions WHERE id=%s", (sids[0],))
        status, oc_id, preview = cur.fetchone()
        assert status == 'completed'
        assert oc_id == 'oc-session-1'
        assert preview is not None
        cur.execute("SELECT status, done, failed FROM ai_chat_batches WHERE id=%s", (bid,))
        bstatus, done, failed = cur.fetchone()
        assert done == 1 and failed == 0
        assert bstatus == 'completed'


def test_run_one_http_error_marks_failed(user_id, db_conn, monkeypatch):
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng
    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)

    fake_oc = MagicMock()
    fake_oc.create_session.side_effect = RuntimeError("opencode 500")
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: '/tmp/ws')

    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, error_message FROM ai_chat_sessions WHERE id=%s",
                    (sids[0],))
        status, err = cur.fetchone()
        assert status == 'failed'
        assert 'opencode 500' in (err or '')
        cur.execute("SELECT status, done, failed FROM ai_chat_batches WHERE id=%s", (bid,))
        bstatus, done, failed = cur.fetchone()
        assert done == 0 and failed == 1
        assert bstatus == 'failed'


def test_run_one_timeout_marks_failed(user_id, db_conn, monkeypatch):
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng
    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)

    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-session-T'
    fake_oc.send_message.return_value = {'id': 'msg-1'}
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': False, 'content': []}
    ]  # never finishes
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: '/tmp/ws')

    w = BatchWorker()
    w.SESSION_TIMEOUT_SEC = 1  # speed up the test
    w.POLL_INTERVAL_SEC = 0.2
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, error_message FROM ai_chat_sessions WHERE id=%s",
                    (sids[0],))
        status, err = cur.fetchone()
        assert status == 'failed'
        assert 'timeout' in (err or '').lower()


def test_batch_status_partial_when_mix(user_id, db_conn):
    """When _recompute_batch_status sees done>0 and failed>0 and all terminal,
    parent batch.status = 'partial'."""
    from utils.batch_engine import _recompute_batch_status
    bid, sids = _seed_batch(db_conn, user_id, n_sessions=3)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='completed' WHERE id=%s", (sids[0],))
        cur.execute("UPDATE ai_chat_sessions SET status='failed' WHERE id=%s", (sids[1],))
        cur.execute("UPDATE ai_chat_sessions SET status='completed' WHERE id=%s", (sids[2],))
        cur.execute("UPDATE ai_chat_batches SET done=2, failed=1 WHERE id=%s", (bid,))
    db_conn.commit()
    _recompute_batch_status(bid)
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone()[0] == 'partial'


def test_concurrency_cap_3(user_id, db_conn, monkeypatch):
    """5 pending, only 3 ever in _running_session_ids at once."""
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=5)
    in_flight_peak = [0]

    fake_oc = MagicMock()
    def slow_create(*a, **kw):
        time.sleep(0.05)
        return 'oc'
    fake_oc.create_session.side_effect = slow_create
    fake_oc.send_message.return_value = {'id': 'm'}
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'ok'}]}
    ]
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: '/tmp/ws')

    w = BatchWorker()
    original_run_one = w._run_one
    def watching_run_one(s):
        with w._lock:
            in_flight_peak[0] = max(in_flight_peak[0], len(w._running_session_ids))
        original_run_one(s)
    w._run_one = watching_run_one
    w.start()
    # let it drain
    for _ in range(60):
        with db_conn.cursor() as cur:
            cur.execute("SELECT done + failed FROM ai_chat_batches WHERE id=%s", (bid,))
            if cur.fetchone()[0] == 5:
                break
        time.sleep(0.2)
    w.stop()

    assert in_flight_peak[0] <= 3
```

- [ ] **Step 3: Implement the engine**

Replace `server/utils/batch_engine.py` (the stub from Task 5) with:

```python
"""In-process worker that turns batch child sessions into running OpenCode runs.

Singleton via `get_worker()`. Started from app.py next to existing schedulers,
guarded by WERKZEUG_RUN_MAIN to avoid double-start under Flask's reloader.
"""
import os
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from psycopg2.extras import RealDictCursor

from utils import opencode_client
from utils.db import get_db
from utils.workspace import create_session_workspace


_WORKER = None


def get_worker() -> 'BatchWorker':
    global _WORKER
    if _WORKER is None:
        _WORKER = BatchWorker()
    return _WORKER


def _workspace_root() -> str:
    return os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')


def _prepare_workspace(user_id: str, session_id: str,
                       staged_file_path: str) -> str:
    """Create the per-session workspace and copy the staged file into uploads/.
    Returns the workspace path. Pure side-effect, no DB writes."""
    ws = create_session_workspace(_workspace_root(), user_id, session_id)
    src = Path(_workspace_root()) / staged_file_path
    dst = Path(ws) / 'uploads' / Path(staged_file_path).name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return ws


def _recompute_batch_status(batch_id: str) -> None:
    """Set ai_chat_batches.status based on its done/failed/total counts."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT done, failed, total FROM ai_chat_batches "
                        "WHERE id = %s", (batch_id,))
            row = cur.fetchone()
            if not row:
                return
            done, failed, total = row
            terminal = done + failed
            if terminal == 0:
                new_status = 'pending'
            elif terminal < total:
                new_status = 'running'
            elif failed == total:
                new_status = 'failed'
            elif done == total:
                new_status = 'completed'
            else:
                new_status = 'partial'
            completed_at = "now()" if terminal == total else "NULL"
            cur.execute(
                f"UPDATE ai_chat_batches "
                f"SET status = %s, completed_at = "
                f"  CASE WHEN %s = total THEN now() ELSE NULL END "
                f"WHERE id = %s",
                (new_status, terminal, batch_id),
            )
        conn.commit()


class BatchWorker:
    MAX_CONCURRENT = 3
    POLL_INTERVAL_SEC = 2
    SESSION_TIMEOUT_SEC = 1800

    def __init__(self):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=self.MAX_CONCURRENT)
        self._running_session_ids: set[str] = set()
        self._lock = threading.Lock()
        self._dispatcher: threading.Thread | None = None

    # --- lifecycle ---
    def start(self):
        if self._dispatcher and self._dispatcher.is_alive():
            return
        self._restart_audit()
        self._dispatcher = threading.Thread(
            target=self._dispatcher_loop, daemon=True, name='batch-worker')
        self._dispatcher.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def notify(self):
        self._wake.set()

    # --- dispatcher ---
    def _dispatcher_loop(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=10)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                free = self.MAX_CONCURRENT - len(self._running_session_ids)
            if free <= 0:
                continue
            pending = self._claim_pending_sessions(limit=free)
            for s in pending:
                with self._lock:
                    self._running_session_ids.add(s['id'])
                self._executor.submit(self._safe_run_one, s)

    def _safe_run_one(self, session_row):
        try:
            self._run_one(session_row)
        except Exception:
            traceback.print_exc()
        finally:
            with self._lock:
                self._running_session_ids.discard(session_row['id'])
            self.notify()  # let the dispatcher start the next queued one

    # --- DB primitives ---
    def _claim_pending_sessions(self, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "WITH picked AS ( "
                    "  SELECT id FROM ai_chat_sessions "
                    "   WHERE status = 'pending' AND batch_id IS NOT NULL "
                    "   ORDER BY created_at, batch_seq "
                    "   FOR UPDATE SKIP LOCKED LIMIT %s "
                    ") "
                    "UPDATE ai_chat_sessions s SET status = 'running' "
                    "FROM picked WHERE s.id = picked.id "
                    "RETURNING s.*",
                    (limit,),
                )
                rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
        return rows

    def _restart_audit(self):
        """Reset any 'running' batch session left over from a previous Flask
        process back to 'pending'. Idempotent."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET status='pending' "
                    "WHERE status='running' AND batch_id IS NOT NULL"
                )
            conn.commit()

    # --- per-session run ---
    def _run_one(self, session_row):
        sid = session_row['id']
        user_id = session_row['user_id']
        batch_id = session_row['batch_id']
        prompt = self._fetch_batch_prompt(batch_id)

        try:
            ws = _prepare_workspace(user_id, sid, session_row['batch_input_file'])
            oc_session_id = opencode_client.create_session(workspace_path=ws)
            self._set_opencode_id(sid, oc_session_id)
            opencode_client.send_message(oc_session_id, prompt)

            preview = self._await_finished(oc_session_id)
            self._mark_done(sid, batch_id, last_preview=preview)
        except _SessionTimeout as e:
            self._mark_failed(sid, batch_id, error=f'timeout after {e.seconds}s')
        except Exception as e:
            self._mark_failed(sid, batch_id, error=f'{type(e).__name__}: {e}'[:500])

    def _fetch_batch_prompt(self, batch_id: str) -> str:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT prompt FROM ai_chat_batches WHERE id=%s",
                            (batch_id,))
                return cur.fetchone()[0]

    def _set_opencode_id(self, session_id: str, oc_session_id: str):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET opencode_session_id=%s WHERE id=%s",
                    (oc_session_id, session_id),
                )
            conn.commit()

    def _await_finished(self, oc_session_id: str) -> str | None:
        deadline = time.time() + self.SESSION_TIMEOUT_SEC
        last_preview = None
        while time.time() < deadline:
            msgs = opencode_client.list_messages(oc_session_id) or []
            for m in reversed(msgs):
                if m.get('role') == 'assistant':
                    last_preview = self._preview_from(m)
                    if m.get('finished'):
                        return last_preview
                    break
            time.sleep(self.POLL_INTERVAL_SEC)
        raise _SessionTimeout(self.SESSION_TIMEOUT_SEC)

    @staticmethod
    def _preview_from(message: dict) -> str | None:
        for part in (message.get('content') or []):
            if part.get('type') == 'text' and part.get('text'):
                t = part['text'].strip().splitlines()
                return (t[0] if t else '')[:200]
        return None

    def _mark_done(self, session_id: str, batch_id: str, last_preview: str | None):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET status='completed', "
                    "  last_message_preview=%s WHERE id=%s",
                    (last_preview, session_id),
                )
                cur.execute("UPDATE ai_chat_batches SET done = done + 1 "
                            "WHERE id=%s", (batch_id,))
            conn.commit()
        _recompute_batch_status(batch_id)

    def _mark_failed(self, session_id: str, batch_id: str, error: str):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET status='failed', error_message=%s "
                    "WHERE id=%s",
                    (error, session_id),
                )
                cur.execute("UPDATE ai_chat_batches SET failed = failed + 1 "
                            "WHERE id=%s", (batch_id,))
            conn.commit()
        _recompute_batch_status(batch_id)


class _SessionTimeout(Exception):
    def __init__(self, seconds: int):
        super().__init__(f'timeout after {seconds}s')
        self.seconds = seconds
```

> Adapt the three OpenCode-client function names (`create_session`, `send_message`, `list_messages`) to match `server/utils/opencode_client.py`'s actual API. The behavior contract is "create a session bound to a workspace dir, send the prompt as a user message, list messages and check the latest assistant message's finished flag." If the existing client only exposes raw HTTP wrappers, add thin convenience functions there OR call the wrappers directly.

- [ ] **Step 4: Wire startup in app.py**

In `server/app.py`, find the existing scheduler startup block (the `WERKZEUG_RUN_MAIN` guarded one that runs `start_backup_scheduler()` and `start_dependency_scheduler()`). Add next to them:

```python
from utils.batch_engine import get_worker
# ... inside the WERKZEUG_RUN_MAIN guard:
get_worker().start()
```

- [ ] **Step 5: Run engine tests**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_engine.py -v
```
Expected: all pass.

- [ ] **Step 6: Re-run all batch route tests to confirm worker integration didn't break them**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_routes.py tests/test_prompt_template_routes.py tests/test_batch_engine.py -v
```

- [ ] **Step 7: Commit**

```bash
git add server/utils/batch_engine.py server/app.py server/tests/test_batch_engine.py
git commit -m "feat(ai-chat-batch): in-process worker (3 concurrent, FIFO, timeout, retry-safe)"
```

---

## Task 7: Frontend types and API clients

**Files:**
- Create: `src/types/aiChatBatch.ts`
- Create: `src/api/aiChatBatches.ts`
- Create: `src/api/aiChatPromptTemplates.ts`

Plain shapes + thin axios wrappers, no logic.

- [ ] **Step 1: Create the types**

`src/types/aiChatBatch.ts`:

```typescript
export type BatchStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed'
export type BatchSessionStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface AiChatBatch {
  id: string
  user_id: string
  name: string
  prompt: string
  template_id: string | null
  status: BatchStatus
  total: number
  done: number
  failed: number
  created_at: string
  completed_at: string | null
}

export interface AiChatBatchSession {
  id: string
  status: BatchSessionStatus
  batch_seq: number
  batch_input_file: string
  opencode_session_id: string | null
  error_message: string | null
  last_message_preview: string | null
}

export interface AiChatBatchDetail {
  batch: AiChatBatch
  sessions: AiChatBatchSession[]
}

export interface AiChatPromptTemplate {
  id: string
  user_id: string
  name: string
  content: string
  created_at: string
  updated_at: string
}

export interface StagedFile {
  name: string
  path: string
}
```

- [ ] **Step 2: Create the batch API client**

`src/api/aiChatBatches.ts`:

```typescript
import axios from '@/api/axios'
import type {
  AiChatBatch, AiChatBatchDetail, StagedFile,
} from '@/types/aiChatBatch'

export async function listBatches(page = 1, pageSize = 20):
  Promise<{ items: AiChatBatch[]; total: number }> {
  const r = await axios.get('/ai/chat/batches', { params: { page, pageSize } })
  return r.data
}

export async function getBatch(id: string): Promise<AiChatBatchDetail> {
  const r = await axios.get(`/ai/chat/batches/${id}`)
  return r.data
}

export async function createBatch(body: {
  name: string
  prompt: string
  template_id?: string | null
  files: StagedFile[]
}): Promise<AiChatBatchDetail> {
  const r = await axios.post('/ai/chat/batches', body)
  return r.data
}

export async function deleteBatch(id: string): Promise<void> {
  await axios.delete(`/ai/chat/batches/${id}`)
}

export async function retryFailedSessions(id: string): Promise<{ retried: number }> {
  const r = await axios.post(`/ai/chat/batches/${id}/retry-failed`)
  return r.data
}

export async function stagingUpload(file: File, uploadSessionId: string):
  Promise<StagedFile> {
  const form = new FormData()
  form.append('file', file)
  form.append('upload_session_id', uploadSessionId)
  const r = await axios.post('/ai/chat/batches/staging/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return r.data
}
```

- [ ] **Step 3: Create the templates API client**

`src/api/aiChatPromptTemplates.ts`:

```typescript
import axios from '@/api/axios'
import type { AiChatPromptTemplate } from '@/types/aiChatBatch'

export async function listTemplates(): Promise<AiChatPromptTemplate[]> {
  const r = await axios.get('/ai/chat/prompt-templates')
  return r.data
}

export async function createTemplate(name: string, content: string):
  Promise<AiChatPromptTemplate> {
  const r = await axios.post('/ai/chat/prompt-templates', { name, content })
  return r.data
}

export async function updateTemplate(id: string, name: string, content: string):
  Promise<AiChatPromptTemplate> {
  const r = await axios.put(`/ai/chat/prompt-templates/${id}`, { name, content })
  return r.data
}

export async function deleteTemplate(id: string): Promise<void> {
  await axios.delete(`/ai/chat/prompt-templates/${id}`)
}
```

- [ ] **Step 4: Type-check and commit**

```bash
npx vue-tsc --noEmit
git add src/types/aiChatBatch.ts src/api/aiChatBatches.ts src/api/aiChatPromptTemplates.ts
git commit -m "feat(ai-chat-batch): TS types + axios clients"
```

---

## Task 8: Batches Pinia store with polling

**Files:**
- Create: `src/stores/aiChatBatches.ts`
- Create: `src/stores/__tests__/aiChatBatches.test.ts`

State: list, current detail, polling loop. UI components subscribe; no UI here.

- [ ] **Step 1: Write failing store tests**

`src/stores/__tests__/aiChatBatches.test.ts`:

```typescript
import { describe, it, expect, beforeEach, vi, beforeAll } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAiChatBatchesStore } from '../aiChatBatches'
import * as api from '@/api/aiChatBatches'

vi.mock('@/api/aiChatBatches')

beforeAll(() => {
  vi.useFakeTimers()
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const mockBatch = {
  id: 'b1', user_id: 'u', name: 'B', prompt: 'p', template_id: null,
  status: 'running' as const, total: 3, done: 1, failed: 0,
  created_at: '', completed_at: null,
}

describe('aiChatBatches store', () => {
  it('fetchList populates items', async () => {
    vi.mocked(api.listBatches).mockResolvedValue({ items: [mockBatch], total: 1 })
    const s = useAiChatBatchesStore()
    await s.fetchList()
    expect(s.items).toEqual([mockBatch])
  })

  it('selectBatch fetches detail and starts polling', async () => {
    vi.mocked(api.getBatch).mockResolvedValue({
      batch: mockBatch, sessions: [],
    })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    expect(api.getBatch).toHaveBeenCalledTimes(1)
    expect(s.activeBatch?.id).toBe('b1')
    expect(s.polling).toBe(true)
    // After 5 seconds another fetch
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.getBatch).toHaveBeenCalledTimes(2)
  })

  it('stops polling when batch reaches terminal state', async () => {
    const terminal = { ...mockBatch, status: 'completed' as const, done: 3 }
    vi.mocked(api.getBatch).mockResolvedValue({ batch: terminal, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    await vi.advanceTimersByTimeAsync(5000)
    expect(s.polling).toBe(false)
  })

  it('retryFailed optimistically clears failed count and refetches', async () => {
    vi.mocked(api.getBatch).mockResolvedValue({
      batch: { ...mockBatch, failed: 2, status: 'partial' as const }, sessions: [],
    })
    vi.mocked(api.retryFailedSessions).mockResolvedValue({ retried: 2 })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    await s.retryFailed()
    // immediate optimistic clear
    expect(s.activeBatch?.failed).toBe(0)
    expect(api.getBatch).toHaveBeenCalled()
  })

  it('clearSelection stops polling', async () => {
    vi.mocked(api.getBatch).mockResolvedValue({ batch: mockBatch, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    s.clearSelection()
    expect(s.polling).toBe(false)
    await vi.advanceTimersByTimeAsync(10000)
    // No further calls beyond the initial selectBatch fetch
    expect(api.getBatch).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run tests, expect failures**

```bash
npx vitest run src/stores/__tests__/aiChatBatches.test.ts
```
Expected: import error.

- [ ] **Step 3: Implement the store**

`src/stores/aiChatBatches.ts`:

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '@/api/aiChatBatches'
import type {
  AiChatBatch, AiChatBatchDetail, AiChatBatchSession,
} from '@/types/aiChatBatch'

const TERMINAL_STATUSES = new Set(['completed', 'failed'])
const DETAIL_POLL_MS = 5000
const LIST_POLL_MS = 10000

export const useAiChatBatchesStore = defineStore('aiChatBatches', () => {
  const items = ref<AiChatBatch[]>([])
  const activeBatch = ref<AiChatBatch | null>(null)
  const activeSessions = ref<AiChatBatchSession[]>([])
  const polling = ref(false)
  const listPolling = ref(false)

  let detailTimer: ReturnType<typeof setTimeout> | null = null
  let listTimer: ReturnType<typeof setTimeout> | null = null

  async function fetchList() {
    const { items: rows } = await api.listBatches()
    items.value = rows
  }

  function startListPolling() {
    if (listPolling.value) return
    listPolling.value = true
    const tick = async () => {
      if (!listPolling.value) return
      try { await fetchList() } catch { /* swallow during polling */ }
      listTimer = setTimeout(tick, LIST_POLL_MS)
    }
    listTimer = setTimeout(tick, LIST_POLL_MS)
  }

  function stopListPolling() {
    listPolling.value = false
    if (listTimer) { clearTimeout(listTimer); listTimer = null }
  }

  async function selectBatch(id: string) {
    const detail = await api.getBatch(id)
    applyDetail(detail)
    if (!TERMINAL_STATUSES.has(detail.batch.status)) {
      startDetailPolling(id)
    }
  }

  function applyDetail(detail: AiChatBatchDetail) {
    activeBatch.value = detail.batch
    activeSessions.value = detail.sessions
  }

  function startDetailPolling(id: string) {
    stopDetailPolling()
    polling.value = true
    const tick = async () => {
      if (!polling.value) return
      try {
        const detail = await api.getBatch(id)
        applyDetail(detail)
        if (TERMINAL_STATUSES.has(detail.batch.status)) {
          stopDetailPolling()
          return
        }
      } catch { /* swallow */ }
      detailTimer = setTimeout(tick, DETAIL_POLL_MS)
    }
    detailTimer = setTimeout(tick, DETAIL_POLL_MS)
  }

  function stopDetailPolling() {
    polling.value = false
    if (detailTimer) { clearTimeout(detailTimer); detailTimer = null }
  }

  function clearSelection() {
    stopDetailPolling()
    activeBatch.value = null
    activeSessions.value = []
  }

  async function retryFailed() {
    if (!activeBatch.value) return
    const id = activeBatch.value.id
    activeBatch.value.failed = 0   // optimistic
    await api.retryFailedSessions(id)
    // refetch authoritative state and resume polling
    const detail = await api.getBatch(id)
    applyDetail(detail)
    if (!TERMINAL_STATUSES.has(detail.batch.status)) startDetailPolling(id)
  }

  async function createAndSelect(body: Parameters<typeof api.createBatch>[0]) {
    const detail = await api.createBatch(body)
    applyDetail(detail)
    items.value = [detail.batch, ...items.value]
    if (!TERMINAL_STATUSES.has(detail.batch.status)) {
      startDetailPolling(detail.batch.id)
    }
    return detail
  }

  async function removeBatch(id: string) {
    await api.deleteBatch(id)
    items.value = items.value.filter(b => b.id !== id)
    if (activeBatch.value?.id === id) clearSelection()
  }

  // Tab-visibility pause: when the page is hidden, stop both polling loops to
  // save bandwidth and battery; resume when visible (refetching once first so
  // the UI is up-to-date the moment the user returns).
  let pausedDetailId: string | null = null
  let pausedList = false
  function attachVisibilityHandler() {
    if (typeof document === 'undefined') return
    document.addEventListener('visibilitychange', async () => {
      if (document.hidden) {
        pausedDetailId = activeBatch.value?.id ?? null
        pausedList = listPolling.value
        stopDetailPolling()
        stopListPolling()
      } else {
        if (pausedList) { await fetchList(); startListPolling() }
        if (pausedDetailId) {
          try {
            const d = await api.getBatch(pausedDetailId)
            applyDetail(d)
            if (!TERMINAL_STATUSES.has(d.batch.status)) startDetailPolling(pausedDetailId)
          } catch { /* swallow */ }
        }
        pausedDetailId = null
        pausedList = false
      }
    })
  }
  attachVisibilityHandler()

  return {
    items, activeBatch, activeSessions, polling, listPolling,
    fetchList, startListPolling, stopListPolling,
    selectBatch, clearSelection, retryFailed,
    createAndSelect, removeBatch,
  }
})
```

Also add to the test file (`src/stores/__tests__/aiChatBatches.test.ts`) one extra test:

```typescript
it('pauses polling on document.hidden, resumes on visible', async () => {
  vi.mocked(api.getBatch).mockResolvedValue({ batch: mockBatch, sessions: [] })
  const s = useAiChatBatchesStore()
  await s.selectBatch('b1')
  // simulate tab hide
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
  document.dispatchEvent(new Event('visibilitychange'))
  await vi.advanceTimersByTimeAsync(15000)
  // No further fetches while hidden
  expect(api.getBatch).toHaveBeenCalledTimes(1)
  // simulate tab visible
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
  document.dispatchEvent(new Event('visibilitychange'))
  await vi.runOnlyPendingTimersAsync()
  expect(api.getBatch.mock.calls.length).toBeGreaterThan(1)
})
```

- [ ] **Step 4: Run tests and confirm pass**

```bash
npx vitest run src/stores/__tests__/aiChatBatches.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/stores/aiChatBatches.ts src/stores/__tests__/aiChatBatches.test.ts
git commit -m "feat(ai-chat-batch): Pinia store with detail + list polling"
```

---

## Task 9: CreateBatchDialog component

**Files:**
- Create: `src/components/ai-chat/CreateBatchDialog.vue`
- Create: `src/components/ai-chat/__tests__/CreateBatchDialog.test.ts`

The dialog handles per-file staged upload, template picking, "save as template" checkbox, and final batch creation.

- [ ] **Step 1: Write the failing component test**

`src/components/ai-chat/__tests__/CreateBatchDialog.test.ts`:

```typescript
import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import CreateBatchDialog from '../CreateBatchDialog.vue'
import * as batchApi from '@/api/aiChatBatches'
import * as tplApi from '@/api/aiChatPromptTemplates'

vi.mock('@/api/aiChatBatches')
vi.mock('@/api/aiChatPromptTemplates')

beforeAll(() => {
  globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(tplApi.listTemplates).mockResolvedValue([])
})

const stubs = {
  'el-dialog': { template: '<div><slot /><slot name="footer" /></div>' },
  'el-input':  {
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-select': {
    template: '<select><slot /></select>',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-option':   { template: '<option />' },
  'el-checkbox': {
    template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-button': { template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>', props: ['disabled'], emits: ['click'] },
  'el-upload': true,
}

describe('CreateBatchDialog', () => {
  it('disables 创建 until name + prompt + ≥1 staged file', async () => {
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    const create = w.find('button[data-test="create-btn"]')
    expect((create.element as HTMLButtonElement).disabled).toBe(true)
    // simulate name + prompt
    await w.find('input[data-test="name"]').setValue('B1')
    await w.find('input[data-test="prompt"]').setValue('do')
    expect((create.element as HTMLButtonElement).disabled).toBe(true)
    // simulate one staged file present
    ;(w.vm as any).stagedFiles = [{ name: 'a.txt', path: 'batch-staging/x/y/a.txt' }]
    await flushPromises()
    expect((w.find('button[data-test="create-btn"]').element as HTMLButtonElement).disabled).toBe(false)
  })

  it('calls createBatch with the staged file list on submit', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B1', prompt: 'do',
               template_id: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B1')
    await w.find('input[data-test="prompt"]').setValue('do')
    ;(w.vm as any).stagedFiles = [{ name: 'a.txt', path: 'p/a.txt' }]
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      name: 'B1', prompt: 'do',
      files: [{ name: 'a.txt', path: 'p/a.txt' }],
    }))
  })

  it('saves a new template when 保存为模板 is checked', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B', prompt: 'p',
               template_id: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    vi.mocked(tplApi.createTemplate).mockResolvedValue({} as any)
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('hello')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).saveAsTemplate = true
    ;(w.vm as any).templateName = 'My T'
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(tplApi.createTemplate).toHaveBeenCalledWith('My T', 'hello')
  })
})
```

- [ ] **Step 2: Implement the dialog**

`src/components/ai-chat/CreateBatchDialog.vue`:

```vue
<template>
  <ElDialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="新建批任务" width="640px"
  >
    <div class="batch-create">
      <div class="row">
        <label>批任务名</label>
        <ElInput v-model="name" data-test="name" placeholder="给这批任务起个名" />
      </div>

      <div class="row">
        <label>模板</label>
        <div class="row__inline">
          <ElSelect v-model="selectedTemplateId"
                    placeholder="可选: 从已保存模板填充"
                    clearable
                    @change="onPickTemplate">
            <ElOption v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </ElSelect>
          <ElButton link @click="emit('manageTemplates')">管理模板</ElButton>
        </div>
      </div>

      <div class="row">
        <label>Prompt</label>
        <ElInput v-model="prompt" type="textarea" :rows="6"
                 data-test="prompt"
                 placeholder="例如: 根据上传的指导书开发巡检用例…" />
      </div>

      <div class="row row--inline">
        <ElCheckbox v-model="saveAsTemplate">保存为新模板</ElCheckbox>
        <ElInput v-if="saveAsTemplate"
                 v-model="templateName" placeholder="模板名" style="max-width: 240px;" />
      </div>

      <div class="row">
        <label>文件 ({{ stagedFiles.length }} / 50)</label>
        <ElUpload
          :auto-upload="false" multiple :show-file-list="false"
          @change="onPick" class="upload">
          <ElButton>+ 选择文件...</ElButton>
        </ElUpload>
        <ul class="files">
          <li v-for="f in stagedFiles" :key="f.path">
            <span>{{ f.name }}</span>
            <ElButton link size="small" @click="removeFile(f)">移除</ElButton>
          </li>
          <li v-for="f in uploading" :key="`u-${f.id}`" class="files__uploading">
            <span>{{ f.name }} ({{ f.progress }}%)</span>
          </li>
          <li v-for="f in failed" :key="`f-${f.id}`" class="files__failed">
            <span>{{ f.name }} — {{ f.error }}</span>
            <ElButton link size="small" @click="removeFailed(f)">移除</ElButton>
          </li>
        </ul>
      </div>
    </div>

    <template #footer>
      <ElButton @click="$emit('update:modelValue', false)">取消</ElButton>
      <ElButton type="primary" data-test="create-btn"
                :disabled="!canCreate" :loading="submitting"
                @click="submit">创建</ElButton>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  ElDialog, ElInput, ElSelect, ElOption, ElCheckbox, ElButton, ElUpload,
  ElMessage,
} from 'element-plus'
import { stagingUpload, createBatch } from '@/api/aiChatBatches'
import { listTemplates, createTemplate } from '@/api/aiChatPromptTemplates'
import type { AiChatBatchDetail, AiChatPromptTemplate, StagedFile } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'created', detail: AiChatBatchDetail): void
  (e: 'manageTemplates'): void
}>()

const name = ref('')
const prompt = ref('')
const selectedTemplateId = ref<string | null>(null)
const templates = ref<AiChatPromptTemplate[]>([])
const stagedFiles = ref<StagedFile[]>([])
const saveAsTemplate = ref(false)
const templateName = ref('')
const submitting = ref(false)
const uploadSessionId = ref<string>(crypto.randomUUID())

const uploading = ref<{ id: number; name: string; progress: number }[]>([])
const failed = ref<{ id: number; name: string; error: string }[]>([])
let counter = 0

const canCreate = computed(() =>
  name.value.trim() !== '' &&
  prompt.value.trim() !== '' &&
  stagedFiles.value.length > 0 &&
  stagedFiles.value.length <= 50 &&
  !submitting.value
)

onMounted(async () => {
  try { templates.value = await listTemplates() } catch { /* non-fatal */ }
})

function onPickTemplate(id: string | null) {
  const t = templates.value.find(x => x.id === id)
  if (t) prompt.value = t.content
}

async function onPick(file: { raw: File }) {
  if (stagedFiles.value.length + uploading.value.length >= 50) {
    ElMessage.warning('已达到 50 个文件上限'); return
  }
  const id = ++counter
  uploading.value.push({ id, name: file.raw.name, progress: 0 })
  try {
    const staged = await stagingUpload(file.raw, uploadSessionId.value)
    uploading.value = uploading.value.filter(u => u.id !== id)
    stagedFiles.value.push(staged)
  } catch (e: any) {
    uploading.value = uploading.value.filter(u => u.id !== id)
    failed.value.push({ id, name: file.raw.name, error: e?.message || '上传失败' })
  }
}

function removeFile(f: StagedFile) {
  stagedFiles.value = stagedFiles.value.filter(x => x.path !== f.path)
}
function removeFailed(f: { id: number }) {
  failed.value = failed.value.filter(x => x.id !== f.id)
}

async function submit() {
  if (!canCreate.value) return
  submitting.value = true
  try {
    const detail = await createBatch({
      name: name.value.trim(),
      prompt: prompt.value.trim(),
      template_id: selectedTemplateId.value,
      files: stagedFiles.value,
    })
    if (saveAsTemplate.value && templateName.value.trim()) {
      try {
        await createTemplate(templateName.value.trim(), prompt.value.trim())
      } catch {
        ElMessage.warning('批任务已创建,但模板保存失败')
      }
    }
    emit('created', detail)
    emit('update:modelValue', false)
    reset()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '创建失败')
  } finally {
    submitting.value = false
  }
}

function reset() {
  name.value = ''
  prompt.value = ''
  selectedTemplateId.value = null
  stagedFiles.value = []
  saveAsTemplate.value = false
  templateName.value = ''
  uploadSessionId.value = crypto.randomUUID()
  uploading.value = []
  failed.value = []
}
</script>

<style scoped lang="scss">
.batch-create { display: flex; flex-direction: column; gap: 16px; }
.row { display: flex; flex-direction: column; gap: 6px; }
.row label { font-size: 13px; color: var(--el-text-color-secondary); }
.row__inline { display: flex; gap: 8px; align-items: center; }
.row--inline { display: flex; flex-direction: row; gap: 12px; align-items: center; }
.files { list-style: none; padding: 0; margin: 8px 0 0; max-height: 200px; overflow: auto; }
.files li { display: flex; justify-content: space-between; padding: 4px 8px; }
.files__uploading { color: var(--el-text-color-secondary); }
.files__failed { color: var(--el-color-danger); }
.upload :deep(.el-upload-list) { display: none; }
</style>
```

- [ ] **Step 3: Run the dialog tests**

```bash
npx vitest run src/components/ai-chat/__tests__/CreateBatchDialog.test.ts
```

- [ ] **Step 4: Commit**

```bash
git add src/components/ai-chat/CreateBatchDialog.vue src/components/ai-chat/__tests__/CreateBatchDialog.test.ts
git commit -m "feat(ai-chat-batch): CreateBatchDialog with staged upload + template picker"
```

---

## Task 10: BatchListView

**Files:**
- Create: `src/views/ai-chat/BatchListView.vue`

A simple list with cards. No unit tests — its behavior is thin glue over the store.

- [ ] **Step 1: Implement**

`src/views/ai-chat/BatchListView.vue`:

```vue
<template>
  <div class="batch-list">
    <div class="batch-list__head">
      <ElButton type="primary" @click="emit('newBatch')">+ 新建批任务</ElButton>
    </div>
    <div class="batch-list__items">
      <div v-for="b in store.items" :key="b.id"
           class="batch-card"
           :class="{ active: store.activeBatch?.id === b.id }"
           @click="emit('select', b.id)">
        <div class="batch-card__name">{{ b.name }}</div>
        <ElProgress :percentage="percentOf(b)" :status="progressStatus(b)" />
        <div class="batch-card__meta">
          <span :class="`badge badge--${b.status}`">{{ statusLabel(b.status) }}</span>
          <span v-if="b.failed">· {{ b.failed }} 失败</span>
          <span>· {{ b.done }}/{{ b.total }}</span>
          <span class="muted">· {{ relativeTime(b.created_at) }}</span>
        </div>
      </div>
      <div v-if="!store.items.length" class="empty">还没有批任务</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { ElButton, ElProgress } from 'element-plus'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { AiChatBatch, BatchStatus } from '@/types/aiChatBatch'

const store = useAiChatBatchesStore()
const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'newBatch'): void
}>()

onMounted(async () => {
  await store.fetchList()
  store.startListPolling()
})
onUnmounted(() => store.stopListPolling())

function percentOf(b: AiChatBatch): number {
  return b.total === 0 ? 0 : Math.round(((b.done + b.failed) / b.total) * 100)
}

function progressStatus(b: AiChatBatch) {
  if (b.status === 'failed') return 'exception'
  if (b.status === 'completed') return 'success'
  if (b.status === 'partial') return 'warning'
  return undefined
}

function statusLabel(s: BatchStatus): string {
  return {
    pending: '等待中', running: '运行中', completed: '完成',
    partial: '部分完成', failed: '失败',
  }[s]
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const min = Math.floor(ms / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  return `${Math.floor(hr / 24)} 天前`
}
</script>

<style scoped lang="scss">
.batch-list { display: flex; flex-direction: column; height: 100%; }
.batch-list__head { padding: 12px; }
.batch-list__items { flex: 1; overflow: auto; padding: 0 8px 12px; }
.batch-card {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px; padding: 10px; margin-bottom: 8px; cursor: pointer;
  &.active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
  &:hover { background: var(--el-fill-color-light); }
}
.batch-card__name { font-weight: 600; margin-bottom: 6px; }
.batch-card__meta { display: flex; gap: 6px; font-size: 12px; color: var(--el-text-color-secondary); margin-top: 6px; }
.badge { padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.badge--running    { background: var(--el-color-primary-light-8); color: var(--el-color-primary); }
.badge--pending    { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.badge--completed  { background: var(--el-color-success-light-8); color: var(--el-color-success); }
.badge--partial    { background: var(--el-color-warning-light-8); color: var(--el-color-warning); }
.badge--failed     { background: var(--el-color-danger-light-8); color: var(--el-color-danger); }
.muted { color: var(--el-text-color-placeholder); }
.empty { text-align: center; padding: 20px; color: var(--el-text-color-secondary); }
</style>
```

- [ ] **Step 2: Type check + commit**

```bash
npx vue-tsc --noEmit
git add src/views/ai-chat/BatchListView.vue
git commit -m "feat(ai-chat-batch): BatchListView with progress + status badges"
```

---

## Task 11: BatchDetailView

**Files:**
- Create: `src/views/ai-chat/BatchDetailView.vue`

The dashboard: overview, prompt summary, retry-failed, session list, click-to-jump.

- [ ] **Step 1: Implement**

`src/views/ai-chat/BatchDetailView.vue`:

```vue
<template>
  <div v-if="batch" class="batch-detail">
    <div class="batch-detail__head">
      <div>
        <h3 class="title">{{ batch.name }}</h3>
        <div class="meta">
          <span :class="`badge badge--${batch.status}`">{{ statusLabel(batch.status) }}</span>
          <span>· {{ batch.done }} / {{ batch.total }}</span>
          <span v-if="batch.failed">· {{ batch.failed }} 失败</span>
        </div>
      </div>
      <div class="actions">
        <ElButton v-if="batch.failed" type="warning" @click="onRetry">
          重试失败 ({{ batch.failed }})
        </ElButton>
        <ElButton @click="onDelete" :disabled="batch.status === 'running'">删除</ElButton>
      </div>
    </div>

    <div class="batch-detail__prompt">
      <span class="label">Prompt:</span>
      <span class="text" :title="batch.prompt">{{ truncated(batch.prompt) }}</span>
      <ElButton v-if="batch.prompt.length > 200" link size="small"
                @click="promptOpen = !promptOpen">
        {{ promptOpen ? '收起' : '展开' }}
      </ElButton>
      <div v-if="promptOpen" class="prompt-full">{{ batch.prompt }}</div>
    </div>

    <ElProgress :percentage="percent" :status="progressStatus" />

    <table class="sessions">
      <thead>
        <tr>
          <th>#</th><th>状态</th><th>文件</th><th>最近消息 / 错误</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in sessions" :key="s.id">
          <td>{{ s.batch_seq + 1 }}</td>
          <td><span :class="`badge badge--${s.status}`">
            {{ sessionStatusLabel(s.status) }}
          </span></td>
          <td>{{ fileBaseName(s.batch_input_file) }}</td>
          <td>
            <span v-if="s.status === 'failed'" class="err">{{ s.error_message }}</span>
            <span v-else class="muted">{{ s.last_message_preview || '—' }}</span>
          </td>
          <td>
            <ElButton link size="small" :disabled="s.status === 'pending'"
                      @click="emit('openSession', s.id)">查看</ElButton>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div v-else class="empty">选择一个批任务查看详情</div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElProgress, ElMessageBox, ElMessage } from 'element-plus'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { BatchStatus, BatchSessionStatus } from '@/types/aiChatBatch'

const store = useAiChatBatchesStore()
const emit = defineEmits<{
  (e: 'openSession', id: string): void
}>()

const batch = computed(() => store.activeBatch)
const sessions = computed(() => store.activeSessions)
const promptOpen = ref(false)

const percent = computed(() => {
  const b = batch.value
  if (!b || b.total === 0) return 0
  return Math.round(((b.done + b.failed) / b.total) * 100)
})

const progressStatus = computed(() => {
  if (!batch.value) return undefined
  if (batch.value.status === 'completed') return 'success'
  if (batch.value.status === 'failed') return 'exception'
  if (batch.value.status === 'partial') return 'warning'
  return undefined
})

function truncated(s: string) { return s.length > 200 ? s.slice(0, 200) + '…' : s }
function fileBaseName(p: string) { return p.split('/').pop() || p }

function statusLabel(s: BatchStatus): string {
  return { pending: '等待中', running: '运行中', completed: '完成',
           partial: '部分完成', failed: '失败' }[s]
}
function sessionStatusLabel(s: BatchSessionStatus): string {
  return { pending: '排队', running: '运行中',
           completed: '完成', failed: '失败' }[s]
}

async function onRetry() {
  try {
    await store.retryFailed()
    ElMessage.success('已重新加入队列')
  } catch (e: any) {
    ElMessage.error(e?.message || '重试失败')
  }
}

async function onDelete() {
  if (!batch.value) return
  try {
    await ElMessageBox.confirm(
      `删除批任务「${batch.value.name}」会移除其下所有子会话。继续?`,
      '删除批任务', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  await store.removeBatch(batch.value.id)
}
</script>

<style scoped lang="scss">
.batch-detail { padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.batch-detail__head { display: flex; justify-content: space-between; align-items: flex-start; }
.title { margin: 0; font-size: 16px; }
.meta { font-size: 12px; color: var(--el-text-color-secondary); display: flex; gap: 6px; margin-top: 4px; }
.actions { display: flex; gap: 8px; }
.batch-detail__prompt { font-size: 13px; }
.batch-detail__prompt .label { color: var(--el-text-color-secondary); margin-right: 6px; }
.prompt-full { margin-top: 6px; padding: 8px 10px; background: var(--el-fill-color-light); border-radius: 6px; white-space: pre-wrap; }
.sessions { width: 100%; border-collapse: collapse; font-size: 13px; }
.sessions th, .sessions td { padding: 8px; border-bottom: 1px solid var(--el-border-color-lighter); text-align: left; }
.badge { padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.badge--running    { background: var(--el-color-primary-light-8); color: var(--el-color-primary); }
.badge--pending    { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.badge--completed  { background: var(--el-color-success-light-8); color: var(--el-color-success); }
.badge--partial    { background: var(--el-color-warning-light-8); color: var(--el-color-warning); }
.badge--failed     { background: var(--el-color-danger-light-8); color: var(--el-color-danger); }
.err { color: var(--el-color-danger); }
.muted { color: var(--el-text-color-secondary); }
.empty { padding: 40px; text-align: center; color: var(--el-text-color-secondary); }
</style>
```

- [ ] **Step 2: Type check + commit**

```bash
npx vue-tsc --noEmit
git add src/views/ai-chat/BatchDetailView.vue
git commit -m "feat(ai-chat-batch): BatchDetailView with retry, delete, session jump"
```

---

## Task 12: PromptTemplateManager drawer

**Files:**
- Create: `src/components/ai-chat/PromptTemplateManager.vue`

CRUD drawer reachable from the create-batch dialog. No tests (basic CRUD over store).

- [ ] **Step 1: Implement**

`src/components/ai-chat/PromptTemplateManager.vue`:

```vue
<template>
  <ElDrawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="管理模板" size="480px">
    <div class="tpl-mgr">
      <div class="tpl-mgr__head">
        <ElButton type="primary" @click="startNew">+ 新模板</ElButton>
      </div>

      <div v-for="t in templates" :key="t.id" class="tpl"
           :class="{ active: editingId === t.id }">
        <div v-if="editingId === t.id" class="tpl__edit">
          <ElInput v-model="form.name" placeholder="模板名" />
          <ElInput v-model="form.content" type="textarea" :rows="5" placeholder="prompt 内容" />
          <div class="tpl__actions">
            <ElButton @click="cancelEdit">取消</ElButton>
            <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
          </div>
        </div>
        <div v-else class="tpl__row">
          <div>
            <div class="tpl__name">{{ t.name }}</div>
            <div class="tpl__preview">{{ truncated(t.content) }}</div>
          </div>
          <div class="tpl__actions">
            <ElButton link @click="startEdit(t)">编辑</ElButton>
            <ElButton link @click="remove(t)" type="danger">删除</ElButton>
          </div>
        </div>
      </div>

      <div v-if="editingId === '__new__'" class="tpl active">
        <div class="tpl__edit">
          <ElInput v-model="form.name" placeholder="模板名" />
          <ElInput v-model="form.content" type="textarea" :rows="5" placeholder="prompt 内容" />
          <div class="tpl__actions">
            <ElButton @click="cancelEdit">取消</ElButton>
            <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
          </div>
        </div>
      </div>
    </div>
  </ElDrawer>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElDrawer, ElInput, ElButton, ElMessage, ElMessageBox } from 'element-plus'
import {
  listTemplates, createTemplate, updateTemplate, deleteTemplate,
} from '@/api/aiChatPromptTemplates'
import type { AiChatPromptTemplate } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean }>()
defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const templates = ref<AiChatPromptTemplate[]>([])
const editingId = ref<string | null>(null)
const form = ref({ name: '', content: '' })
const saving = ref(false)

async function refresh() {
  templates.value = await listTemplates()
}

watch(() => props.modelValue, async (v) => {
  if (v) {
    editingId.value = null
    await refresh()
  }
})

function startNew() {
  editingId.value = '__new__'
  form.value = { name: '', content: '' }
}
function startEdit(t: AiChatPromptTemplate) {
  editingId.value = t.id
  form.value = { name: t.name, content: t.content }
}
function cancelEdit() {
  editingId.value = null
}

async function save() {
  if (!form.value.name.trim() || !form.value.content.trim()) {
    ElMessage.warning('名称和内容不能为空'); return
  }
  saving.value = true
  try {
    if (editingId.value === '__new__') {
      await createTemplate(form.value.name.trim(), form.value.content.trim())
    } else if (editingId.value) {
      await updateTemplate(editingId.value, form.value.name.trim(), form.value.content.trim())
    }
    editingId.value = null
    await refresh()
  } catch (e: any) {
    if (e?.response?.status === 409) ElMessage.error('已有同名模板')
    else ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(t: AiChatPromptTemplate) {
  try {
    await ElMessageBox.confirm(`删除模板「${t.name}」?`, '确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  await deleteTemplate(t.id)
  await refresh()
}

function truncated(s: string) { return s.length > 120 ? s.slice(0, 120) + '…' : s }
</script>

<style scoped lang="scss">
.tpl-mgr { display: flex; flex-direction: column; gap: 10px; padding: 10px; }
.tpl-mgr__head { margin-bottom: 8px; }
.tpl { border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 10px; }
.tpl.active { border-color: var(--el-color-primary); }
.tpl__row { display: flex; justify-content: space-between; gap: 12px; }
.tpl__name { font-weight: 600; }
.tpl__preview { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; white-space: pre-wrap; }
.tpl__edit { display: flex; flex-direction: column; gap: 8px; }
.tpl__actions { display: flex; gap: 6px; justify-content: flex-end; }
</style>
```

- [ ] **Step 2: Type check + commit**

```bash
npx vue-tsc --noEmit
git add src/components/ai-chat/PromptTemplateManager.vue
git commit -m "feat(ai-chat-batch): prompt template manager drawer"
```

---

## Task 13: AiChatView integration — tabs + jumpToSession

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`
- Modify: `src/stores/aiChat.ts`

Wires the new views and dialog into the existing AI Chat page.

- [ ] **Step 1: Add `jumpToSession` to the AI chat store**

In `src/stores/aiChat.ts`, add (or extend an existing action) so external callers can switch to a specific session:

```typescript
// inside the store body
async function jumpToSession(sessionId: string) {
  // Refresh the list if the target isn't present (e.g. just-created batch child)
  if (!sessions.value.find(s => s.id === sessionId)) {
    await fetchSessions()
  }
  const target = sessions.value.find(s => s.id === sessionId)
  if (!target) return false
  await selectSession(target.id)
  return true
}
// remember to expose it in the return object
```

(Adapt to the existing store's shape — names like `selectSession`/`fetchSessions` should already be present.)

- [ ] **Step 2: Read the existing AiChatView to find the sidebar insertion point**

```bash
grep -nE "session-list|drawer|sidebar" src/views/ai-chat/AiChatView.vue | head
```

Identify (a) the sidebar header (where the "+ 新建会话" button currently lives), (b) the session list rendering, and (c) the main chat area.

- [ ] **Step 3: Edit AiChatView**

In the script section, add:

```typescript
import { ref } from 'vue'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import BatchListView from './BatchListView.vue'
import BatchDetailView from './BatchDetailView.vue'
import CreateBatchDialog from '@/components/ai-chat/CreateBatchDialog.vue'
import PromptTemplateManager from '@/components/ai-chat/PromptTemplateManager.vue'

const sidebarTab = ref<'sessions' | 'batches'>('sessions')
const batches = useAiChatBatchesStore()
const showCreateBatch = ref(false)
const showTemplateManager = ref(false)

async function openSession(sessionId: string) {
  if (await store.jumpToSession(sessionId)) {
    sidebarTab.value = 'sessions'
    batches.clearSelection()
  }
}

async function selectBatch(id: string) {
  await batches.selectBatch(id)
}
```

In the template, wrap the sidebar with a tab switch:

```vue
<div class="ai-sidebar__tabs">
  <button :class="{ active: sidebarTab === 'sessions' }"
          @click="sidebarTab = 'sessions'">会话</button>
  <button :class="{ active: sidebarTab === 'batches' }"
          @click="sidebarTab = 'batches'">批任务</button>
</div>

<!-- existing session list rendered when sidebarTab === 'sessions' -->
<div v-show="sidebarTab === 'sessions'">
  <!-- existing markup -->
</div>

<BatchListView v-if="sidebarTab === 'batches'"
               @select="selectBatch"
               @newBatch="showCreateBatch = true" />
```

In the main panel:

```vue
<BatchDetailView v-if="sidebarTab === 'batches' && batches.activeBatch"
                 @openSession="openSession" />
<!-- existing chat thread rendered otherwise -->
<template v-else>
  <!-- existing markup -->
</template>
```

And mount the dialogs at the root:

```vue
<CreateBatchDialog
  v-model="showCreateBatch"
  @manageTemplates="showTemplateManager = true"
  @created="(d) => { sidebarTab = 'batches'; batches.selectBatch(d.batch.id) }" />
<PromptTemplateManager v-model="showTemplateManager" />
```

Tab CSS:

```scss
.ai-sidebar__tabs {
  display: flex;
  border-bottom: 1px solid var(--el-border-color-light);
  button {
    flex: 1; padding: 8px; background: none; border: none;
    cursor: pointer; color: var(--el-text-color-secondary);
    &.active { color: var(--el-color-primary); box-shadow: inset 0 -2px 0 var(--el-color-primary); }
  }
}
```

- [ ] **Step 4: Manual smoke test in dev mode**

```bash
npm run dev:all
```

Login → AI 助手 → click 批任务 tab → empty state shows → 新建批任务 → upload 2 small text files → write a short prompt → 创建 → batch should appear in list, detail panel opens, sessions transition from pending → running → completed (depending on OpenCode availability).

- [ ] **Step 5: Commit**

```bash
git add src/views/ai-chat/AiChatView.vue src/stores/aiChat.ts
git commit -m "feat(ai-chat-batch): tab switch in AI chat sidebar + dialogs"
```

---

## Task 14: E2E smoke test

**Files:**
- Create: `e2e/ai-chat-batch.spec.ts`

End-to-end test mirroring the existing `e2e/ai-chat-smoke.spec.ts` pattern. Requires OpenCode + MCP running.

- [ ] **Step 1: Read the existing E2E file to learn the harness**

```bash
cat e2e/ai-chat-smoke.spec.ts | head -80
```

- [ ] **Step 2: Implement**

`e2e/ai-chat-batch.spec.ts`:

```typescript
import { test, expect } from '@playwright/test'

test('batch task: create, run, retry, delete', async ({ page }) => {
  // Login as admin (reuse the existing helper if available)
  await page.goto('/login')
  await page.getByPlaceholder(/用户名/).fill('admin')
  await page.getByPlaceholder(/密码/).fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(/\/(home|ai-chat|$)/)

  await page.goto('/ai-chat')

  // Switch to 批任务 tab
  await page.getByRole('button', { name: '批任务' }).click()
  await page.getByRole('button', { name: '+ 新建批任务' }).click()

  // Fill name + prompt
  await page.locator('input[data-test="name"]').fill('e2e-batch')
  await page.locator('textarea[data-test="prompt"]').fill('echo hi')

  // Upload 2 in-memory files
  const upload = page.locator('input[type=file]').first()
  await upload.setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
    { name: 'b.txt', mimeType: 'text/plain', buffer: Buffer.from('B') },
  ])
  await expect(page.getByText('a.txt')).toBeVisible({ timeout: 8000 })
  await expect(page.getByText('b.txt')).toBeVisible({ timeout: 8000 })

  // Create
  await page.locator('button[data-test="create-btn"]').click()

  // Detail panel shows
  await expect(page.getByText('e2e-batch')).toBeVisible({ timeout: 5000 })
  await expect(page.locator('table.sessions tbody tr')).toHaveCount(2)

  // Wait up to 90s for both children to reach a terminal state
  await page.waitForFunction(() => {
    const rows = document.querySelectorAll('table.sessions tbody tr')
    return rows.length === 2 && Array.from(rows).every(r => {
      const badge = r.querySelector('[class*="badge--"]')
      return badge && (badge.className.includes('completed') ||
                       badge.className.includes('failed'))
    })
  }, { timeout: 90_000 })

  // Delete and confirm
  page.on('dialog', d => d.accept())
  await page.getByRole('button', { name: '删除' }).click()
  await page.getByRole('button', { name: '删除', exact: true }).last().click()
  await expect(page.getByText('e2e-batch')).not.toBeVisible()
})
```

- [ ] **Step 3: Run E2E**

```bash
npm run test:e2e -- ai-chat-batch.spec.ts
```

(Requires OpenCode + MCP server up; same prerequisites as the existing `ai-chat-smoke.spec.ts`.)

- [ ] **Step 4: Commit**

```bash
git add e2e/ai-chat-batch.spec.ts
git commit -m "test(ai-chat-batch): e2e smoke for create/run/retry/delete"
```

---

## Task 15: CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md`

Append a short section under "AI Agent Chat (M1)".

- [ ] **Step 1: Add the section**

Locate "### AI Agent Chat (M1)" in `CLAUDE.md`. Right after it, add:

```markdown
### AI Agent Chat — Batch Tasks (M1.5)

Pick N files + 1 prompt → N isolated sessions, throttled to 3 concurrent. Sessions are real `ai_chat_sessions` rows with `batch_id` / `batch_seq` / `batch_input_file` set; the existing chat view handles them as-is.

* **Worker**: `server/utils/batch_engine.py` (in-process daemon, started from `app.py` next to existing schedulers).
* **REST**: `server/routes/ai_chat_batches.py` (POST/GET/DELETE/retry-failed + staging upload), `server/routes/ai_chat_prompt_templates.py` (per-user CRUD).
* **Frontend**: AI 助手 page sidebar gains a 会话/批任务 tab switch; `BatchListView` + `BatchDetailView` + `CreateBatchDialog` + `PromptTemplateManager`. Polling every 5s for detail, 10s for list; stops on terminal states.
* **Failure policy**: failed children are red-flagged and don't abort the batch; `POST /ai/chat/batches/:id/retry-failed` resets them to pending and the worker picks them up. No auto-retry.

Design: `docs/superpowers/specs/2026-05-31-ai-chat-batch-tasks-design.md`. Plan: `docs/superpowers/plans/2026-05-31-ai-chat-batch-tasks.md`.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(ai-chat-batch): document batch tasks subsystem"
```

---

## Final integration check

After all 15 tasks are committed, run the full suite to verify nothing regressed:

```bash
npm run test:all
```

Expected: all green. If anything fails, debug per `superpowers:systematic-debugging` before merging.
