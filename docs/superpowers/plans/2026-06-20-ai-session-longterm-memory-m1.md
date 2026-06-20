# AI 会话长期记忆 — M1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 助手的真人交互会话自动形成并调用按用户全局的长期记忆（mem0 + Chroma + DashScope 兼容端点），且记忆层异常绝不影响聊天。

**Architecture:** 新增 `server/utils/memory.py` 作为 mem0 的唯一封装层（单例 + 全程降级）。Flask 被动接入：`send_message` 转发前 `search` 注入记忆段；后台 SSE listener 在每轮 `idle` 落库后 `extract_from_turn` 抽取记忆。配置项挂在 `ai_settings`。

**Tech Stack:** Python/Flask、psycopg2、mem0ai、chromadb、DashScope OpenAI 兼容端点、Vue3/Element Plus（设置页）、pytest/vitest。

**Spec:** `docs/superpowers/specs/2026-06-20-ai-session-longterm-memory-design.md`（§5/§6 为 M1 核心）。

---

## File Structure（M1 触及的文件）

- `server/requirements.txt` — 加 `mem0ai`、`chromadb`
- `server/scripts/mem0_smoke.py`（新）— 连通性验证脚本
- `server/add_mem0_settings_columns.py`（新）— 幂等加列迁移
- `server/init_db.py` — DDL 新部署带上 2 列（如已用 CREATE TABLE 定义 ai_settings）
- `server/utils/ai_query.py` — `get_ai_settings`/`update_ai_settings` 带 `mem0Enabled`/`embeddingModel`
- `server/routes/ai.py` — `get_settings`/`put_settings` 带新字段 + 调 `reset_memory_singleton`
- `server/utils/memory.py`（新）— 记忆集成层 + 抽取钩子
- `server/routes/ai_chat.py` — `send_message` 注入记忆段
- `server/utils/chat_persist.py` — `idle` 分支调 `extract_from_turn`
- `server/tests/test_memory.py`（新）— 单元测试
- `src/views/admin/AiSettings.vue` — 2 个配置控件
- `docs/user-guide/ai/long-term-memory.md`（新）+ `CLAUDE.md` — 文档同步

---

## Task 1：连通性 spike + 依赖（先验证三者打通）

**Files:**
- Modify: `server/requirements.txt`
- Create: `server/scripts/mem0_smoke.py`

> 这是 spike：先证明 mem0 + Chroma + DashScope 能 add/search 一条记忆，并记录 mem0 实际返回结构，给后续 task 校准。**若 DashScope 作 embedder 不通，退路在本任务末尾。**

- [ ] **Step 1: 加依赖并安装**

`server/requirements.txt` 追加两行：
```
mem0ai>=0.1.0
chromadb>=0.5.0
```
安装：
```bash
cd server && pip install mem0ai chromadb
```
Expected: 安装成功；`python -c "import mem0, chromadb; print('ok')"` 打印 `ok`。

- [ ] **Step 2: 临时给 ai_settings 配置（手动，仅本机验证）**

确保 `ai_settings` 已配 `api_key`/`endpoint`/`model`（AI 查询能用即说明已配）。spike 脚本会直接读它。

- [ ] **Step 3: 写连通性脚本**

`server/scripts/mem0_smoke.py`：
```python
"""M1 连通性 spike：证明 mem0 + Chroma + DashScope 能 add/search 一条记忆。
Run: cd server && python scripts/mem0_smoke.py
依赖 ai_settings 已配 api_key/endpoint/model；用 --embed 覆盖 embedding 模型。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.ai_query import get_ai_settings

CHROMA_PATH = os.path.join(os.path.expanduser('~'), '.check-manage', 'mem0_smoke')
EMBED = sys.argv[sys.argv.index('--embed') + 1] if '--embed' in sys.argv else 'text-embedding-v3'


def base_url(endpoint):
    return endpoint.rsplit('/chat/completions', 1)[0] if endpoint else ''


def main():
    cfg = get_ai_settings()
    if not cfg.get('apiKey'):
        print('FAIL: ai_settings 未配 api_key'); return 1
    os.makedirs(CHROMA_PATH, exist_ok=True)
    from mem0 import Memory
    m = Memory.from_config({
        'vector_store': {'provider': 'chroma',
                         'config': {'collection_name': 'smoke', 'path': CHROMA_PATH}},
        'llm': {'provider': 'openai',
                'config': {'model': cfg['model'], 'openai_base_url': base_url(cfg['endpoint']),
                           'api_key': cfg['apiKey'], 'temperature': 0.1}},
        'embedder': {'provider': 'openai',
                     'config': {'model': EMBED, 'openai_base_url': base_url(cfg['endpoint']),
                                'api_key': cfg['apiKey'], 'embedding_dims': 1024}},
    })
    add_res = m.add([{'role': 'user', 'content': '我喜欢用 Python 和 PostgreSQL'},
                     {'role': 'assistant', 'content': '好的，记住你的技术偏好。'}],
                    user_id='smoke-user')
    print('ADD result:', add_res)
    res = m.search(query='我熟悉哪些技术', user_id='smoke-user', limit=5)
    print('SEARCH result:', res)
    ok = bool((res or {}).get('results') if isinstance(res, dict) else res)
    print('PASS' if ok else 'WARN: 无检索结果（抽取/维度可能需调整）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: 跑脚本，记录实际 API 形态**

Run: `cd server && python scripts/mem0_smoke.py`
Expected: 打印 `ADD result` 与 `SEARCH result`，末尾 `PASS`。
**把 `SEARCH result` 的真实结构记下来**（确认是 `{'results': [{'id','memory',...}]}` 还是 list），Task 4 据此校准 `search_memory`/`list_memories` 的解包。

- [ ] **Step 5（仅当 embedder 失败时的退路）**

若 DashScope embedder 报维度/兼容错误：把 `embedder` 换成本地：
```python
'embedder': {'provider': 'huggingface',
             'config': {'model': 'sentence-transformers/all-MiniLM-L6-v2'}},
```
并 `pip install sentence-transformers`，重跑 Step 4。记录最终可用的 embedder 形态供 Task 4。

- [ ] **Step 6: 清理 + 提交**

```bash
rm -rf ~/.check-manage/mem0_smoke
cd E:/wsl/check/check-manage
git add server/requirements.txt server/scripts/mem0_smoke.py
git commit -m "chore(memory): add mem0/chromadb deps + connectivity spike script"
```

---

## Task 2：ai_settings 加 2 列 + 读写带上新字段

**Files:**
- Create: `server/add_mem0_settings_columns.py`
- Modify: `server/utils/ai_query.py`（`get_ai_settings` ~24-54、`update_ai_settings` ~56-66）
- Test: `server/tests/test_ai_settings_mem0.py`

- [ ] **Step 1: 写幂等加列脚本**

`server/add_mem0_settings_columns.py`：
```python
"""Idempotent: add mem0 config columns to ai_settings. Run once per DB."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import psycopg2
from config import DB_CONFIG

DDL = [
    "ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS mem0_enabled boolean DEFAULT false",
    "ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS embedding_model varchar DEFAULT 'text-embedding-v3'",
]

def main():
    conn = psycopg2.connect(**DB_CONFIG); conn.autocommit = True
    cur = conn.cursor()
    for sql in DDL:
        cur.execute(sql)
    print('ai_settings mem0 columns ensured')
    cur.close(); conn.close()

if __name__ == '__main__':
    main()
```
Run: `cd server && python add_mem0_settings_columns.py`
Expected: 打印 `ai_settings mem0 columns ensured`。

- [ ] **Step 2: 写失败测试（get_ai_settings 暴露新字段）**

`server/tests/test_ai_settings_mem0.py`：
```python
import sys, os
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.ai_query as aq

def _db(row):
    conn = MagicMock(); cur = MagicMock()
    cur.fetchone.return_value = row
    conn.cursor.return_value = cur
    @contextmanager
    def fake():
        yield conn
    return fake

def test_get_ai_settings_exposes_mem0_fields():
    # row order must match the SELECT in get_ai_settings
    row = (True, 'sk', 'https://x/v1/chat/completions', 'qwen-plus', 30, 1024, None, True, 'text-embedding-v3')
    with patch.object(aq, 'get_db', _db(row)):
        cfg = aq.get_ai_settings()
    assert cfg['mem0Enabled'] is True
    assert cfg['embeddingModel'] == 'text-embedding-v3'
```

- [ ] **Step 3: 跑测试，确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_settings_mem0.py -v`
Expected: FAIL（`KeyError: 'mem0Enabled'`）。

- [ ] **Step 4: 改 get_ai_settings / update_ai_settings**

在 `get_ai_settings` 的 SELECT 末尾加两列并在返回 dict 加两键。SELECT 改为：
```python
'SELECT enabled, api_key, endpoint, model, timeout, max_tokens, updated_at, '
'mem0_enabled, embedding_model FROM ai_settings WHERE id = 1'
```
返回 dict 增加（默认值兜底，兼容未迁移库）：
```python
'mem0Enabled': bool(row[7]) if len(row) > 7 else False,
'embeddingModel': (row[8] if len(row) > 8 else None) or 'text-embedding-v3',
```
默认行（无 row 时）那段也加：
```python
'mem0Enabled': False,
'embeddingModel': 'text-embedding-v3',
```
`update_ai_settings` 增加 `mem0_enabled, embedding_model` 两个参数并写入 UPDATE：
```python
def update_ai_settings(enabled, api_key, endpoint, model, timeout, max_tokens,
                       mem0_enabled=False, embedding_model='text-embedding-v3'):
    ...
    'UPDATE ai_settings SET enabled=%s, api_key=%s, endpoint=%s, model=%s, '
    'timeout=%s, max_tokens=%s, mem0_enabled=%s, embedding_model=%s, updated_at=%s WHERE id=1'
    ...,
    (enabled, api_key, endpoint, model, timeout, max_tokens,
     mem0_enabled, embedding_model, now)
```

- [ ] **Step 5: 跑测试，确认通过**

Run: 同 Step 3。Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add server/add_mem0_settings_columns.py server/utils/ai_query.py server/tests/test_ai_settings_mem0.py
git commit -m "feat(memory): ai_settings mem0_enabled + embedding_model config"
```

---

## Task 3：settings API 带新字段 + 配置变更后重置记忆单例

**Files:**
- Modify: `server/routes/ai.py`（`get_settings` ~60-70、`put_settings` ~72-104）
- Test: `server/tests/test_routes_ai_settings.py`（若已存在则追加）

- [ ] **Step 1: 写失败测试（PUT 带 mem0Enabled 会落库 + GET 回显，掩码不变）**

`server/tests/test_routes_ai_settings.py`：
```python
import sys, os, json
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token

def test_put_settings_persists_mem0_fields():
    import routes.ai as ai
    captured = {}
    def fake_update(*args, **kwargs):
        captured['args'] = args
        return {'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                'mem0Enabled': True, 'embeddingModel': 'text-embedding-v3'}
    with patch.object(ai, 'update_ai_settings', fake_update), \
         patch.object(ai, 'get_ai_settings', return_value={'apiKey': 'sk'}):
        from app import app
        app.config['TESTING'] = True
        tok = create_token({'id': 'u', 'username': 'admin', 'role': 'admin'})
        resp = app.test_client().put('/ai/settings',
            headers={'Authorization': f'Bearer {tok}'},
            json={'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                  'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                  'mem0Enabled': True, 'embeddingModel': 'text-embedding-v3'})
    assert resp.status_code == 200
    assert resp.get_json()['mem0Enabled'] is True
```
> `reset_memory_singleton` 的接入与断言放到 Task 4（memory.py 创建后），避免本任务依赖尚不存在的 `utils.memory`。

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_settings.py -v`
Expected: FAIL（返回无 `mem0Enabled`）。

- [ ] **Step 3: 改 routes/ai.py**

`put_settings` 读取并透传新字段（暂不接 `reset_memory_singleton`——它在 Task 4 接入）：
```python
mem0_enabled = bool(body.get('mem0Enabled', False))
embedding_model = (body.get('embeddingModel') or 'text-embedding-v3').strip()
settings = update_ai_settings(enabled, api_key, endpoint, model, timeout, max_tokens,
                              mem0_enabled=mem0_enabled, embedding_model=embedding_model)
```
`get_settings` 无需改（`get_ai_settings` 已含新字段；`api_key` 掩码逻辑保持）。确保 `put_settings` 返回的 dict 含 `mem0Enabled`/`embeddingModel`（来自 `update_ai_settings` 的返回）。

- [ ] **Step 4: 跑测试，确认通过** — Run 同 Step 2。Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai.py server/tests/test_routes_ai_settings.py
git commit -m "feat(memory): settings API carries mem0 fields + resets memory singleton"
```

---

## Task 4：记忆集成层 `server/utils/memory.py`

**Files:**
- Create: `server/utils/memory.py`
- Test: `server/tests/test_memory.py`

> Task 1 spike 已确认 mem0 的 `search`/`get_all` 返回结构；若与下方 `.get('results', ...)` 解包不符，按 spike 实测调整这两处解包。

- [ ] **Step 1: 写失败测试（降级 + 作用域）**

`server/tests/test_memory.py`：
```python
import sys, os
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.memory as mem

def setup_function():
    mem.reset_memory_singleton()

def test_get_memory_none_when_disabled():
    with patch.object(mem, 'get_ai_settings', return_value={'mem0Enabled': False, 'apiKey': 'sk'}):
        assert mem.get_memory() is None

def test_search_empty_when_disabled():
    with patch.object(mem, 'get_ai_settings', return_value={'mem0Enabled': False, 'apiKey': 'sk'}):
        assert mem.search_memory('u', 'q') == []

def test_add_noop_when_disabled():
    with patch.object(mem, 'get_ai_settings', return_value={'mem0Enabled': False, 'apiKey': 'sk'}):
        mem.add_memory('u', [{'role': 'user', 'content': 'x'}])  # must not raise

def test_search_scopes_by_user_and_unwraps_results():
    fake = MagicMock()
    fake.search.return_value = {'results': [{'id': '1', 'memory': '喜欢 Python'}]}
    with patch.object(mem, 'get_memory', return_value=fake):
        out = mem.search_memory('alice', '技术', limit=3)
    fake.search.assert_called_once_with(query='技术', filters={'user_id': 'alice'}, limit=3)
    assert out == [{'id': '1', 'memory': '喜欢 Python'}]

def test_add_swallows_errors():
    fake = MagicMock(); fake.add.side_effect = RuntimeError('boom')
    with patch.object(mem, 'get_memory', return_value=fake):
        mem.add_memory('alice', [{'role': 'user', 'content': 'x'}])  # must not raise
```

- [ ] **Step 2: 跑测试，确认失败** — Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_memory.py -v` → FAIL（模块不存在）。

- [ ] **Step 3: 写 memory.py**

```python
"""User-scoped long-term memory via mem0 (Chroma + DashScope-compatible LLM/embedder).

Flask is the SOLE owner of the mem0 instance (Chroma is a single-writer store). Every
operation degrades to no-op/empty when disabled or on error, so chat never breaks
because of the memory layer.
"""
import os
import threading
import logging

from db import get_db
from utils.ai_query import get_ai_settings

logger = logging.getLogger(__name__)

MEM0_STORE_ROOT = os.environ.get(
    'MEM0_STORE_ROOT', os.path.join(os.path.expanduser('~'), '.check-manage', 'mem0'))

_memory = None
_init_attempted = False
_lock = threading.Lock()


def _base_url(endpoint):
    return endpoint.rsplit('/chat/completions', 1)[0] if endpoint else ''


def _build_config(cfg):
    base = _base_url(cfg['endpoint'])
    key = cfg['apiKey']
    return {
        'vector_store': {'provider': 'chroma',
                         'config': {'collection_name': 'memories', 'path': MEM0_STORE_ROOT}},
        'llm': {'provider': 'openai',
                'config': {'model': cfg['model'], 'openai_base_url': base,
                           'api_key': key, 'temperature': 0.1}},
        'embedder': {'provider': 'openai',
                     'config': {'model': cfg.get('embeddingModel') or 'text-embedding-v3',
                                'openai_base_url': base, 'api_key': key, 'embedding_dims': 1024}},
    }


def get_memory():
    """mem0 Memory 单例；未启用/初始化失败返回 None（只尝试一次，不每次重试）。"""
    global _memory, _init_attempted
    if _memory is not None:
        return _memory
    with _lock:
        if _memory is not None:
            return _memory
        if _init_attempted:
            return None
        _init_attempted = True
        cfg = get_ai_settings()
        if not cfg.get('mem0Enabled') or not cfg.get('apiKey'):
            return None
        try:
            os.makedirs(MEM0_STORE_ROOT, exist_ok=True)
            from mem0 import Memory
            _memory = Memory.from_config(_build_config(cfg))
            return _memory
        except Exception as e:
            logger.warning('mem0 init failed, memory disabled: %s', e)
            return None


def reset_memory_singleton():
    """配置变更后丢弃缓存实例，使下次调用按新配置重建。"""
    global _memory, _init_attempted
    with _lock:
        _memory = None
        _init_attempted = False


def _unwrap(res):
    if isinstance(res, dict):
        return res.get('results', [])
    return res or []


def add_memory(user_id, messages):
    m = get_memory()
    if m is None or not user_id or not messages:
        return
    try:
        m.add(messages, user_id=user_id)
    except Exception as e:
        logger.warning('mem0 add failed: %s', e)


def search_memory(user_id, query, limit=5):
    m = get_memory()
    if m is None or not user_id or not query:
        return []
    try:
        return _unwrap(m.search(query=query, filters={'user_id': user_id}, limit=limit))
    except Exception as e:
        logger.warning('mem0 search failed: %s', e)
        return []


def list_memories(user_id):
    m = get_memory()
    if m is None or not user_id:
        return []
    try:
        return _unwrap(m.get_all(filters={'user_id': user_id}))
    except Exception as e:
        logger.warning('mem0 get_all failed: %s', e)
        return []


def delete_memory(memory_id):
    m = get_memory()
    if m is None or not memory_id:
        return
    try:
        m.delete(memory_id=memory_id)
    except Exception as e:
        logger.warning('mem0 delete failed: %s', e)
```

- [ ] **Step 4: 跑测试，确认通过** — Run 同 Step 2。Expected: 5 passed。

- [ ] **Step 5: 接入 settings → 配置变更后重置记忆单例**

现在 `utils.memory` 已存在，把 `reset_memory_singleton` 接到 settings PUT：
`server/routes/ai.py` 顶部加导入，并在 `put_settings` 的 `update_ai_settings(...)` 之后、`return` 之前调用：
```python
from utils.memory import reset_memory_singleton   # 顶部
...
reset_memory_singleton()  # put_settings 内，update_ai_settings 之后
```
追加测试到 `server/tests/test_routes_ai_settings.py`：
```python
def test_put_settings_resets_memory_singleton():
    import routes.ai as ai
    def fake_update(*a, **k):
        return {'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                'mem0Enabled': False, 'embeddingModel': 'text-embedding-v3'}
    with patch.object(ai, 'update_ai_settings', fake_update), \
         patch.object(ai, 'get_ai_settings', return_value={'apiKey': 'sk'}), \
         patch.object(ai, 'reset_memory_singleton') as reset:
        from app import app
        app.config['TESTING'] = True
        tok = create_token({'id': 'u', 'username': 'admin', 'role': 'admin'})
        app.test_client().put('/ai/settings', headers={'Authorization': f'Bearer {tok}'},
            json={'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                  'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                  'mem0Enabled': False, 'embeddingModel': 'text-embedding-v3'})
    reset.assert_called_once()
```
Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_settings.py -v` → PASS。

- [ ] **Step 6: 提交**

```bash
git add server/utils/memory.py server/tests/test_memory.py server/routes/ai.py server/tests/test_routes_ai_settings.py
git commit -m "feat(memory): mem0 integration layer with full degradation + settings reset hook"
```

---

## Task 5：被动注入（send_message 转发前注入记忆段）

**Files:**
- Modify: `server/routes/ai_chat.py`（`send_message` ~315-316 拼 prompt 处）
- Test: `server/tests/test_memory.py`（追加渲染测试）

- [ ] **Step 1: 写失败测试（渲染函数）**

追加到 `server/tests/test_memory.py`：
```python
def test_render_memory_block_formats_lines():
    block = mem.render_memory_block([{'memory': '喜欢 Python'}, {'memory': '在用 PostgreSQL'}, {'memory': ''}])
    assert '喜欢 Python' in block and '在用 PostgreSQL' in block
    assert block.startswith('[关于当前用户的长期记忆')
    assert block.endswith('\n\n')

def test_render_memory_block_empty_returns_empty():
    assert mem.render_memory_block([]) == ''
```

- [ ] **Step 2: 跑测试，确认失败** — `pytest tests/test_memory.py::test_render_memory_block_formats_lines -v` → FAIL。

- [ ] **Step 3: 在 memory.py 加 render_memory_block**

```python
def render_memory_block(mems, limit=5):
    lines = [str(x.get('memory', '')).strip() for x in (mems or [])]
    lines = [l for l in lines if l][:limit]
    if not lines:
        return ''
    body = '\n'.join(f'- {l}' for l in lines)
    return f'[关于当前用户的长期记忆（供参考，不必逐条复述）]\n{body}\n\n'
```

- [ ] **Step 4: 跑测试，确认通过** — Expected: PASS。

- [ ] **Step 5: 在 send_message 注入**

`server/routes/ai_chat.py` 顶部导入：
```python
from utils.memory import search_memory, render_memory_block
```
把 `prompt = _AGENT_DIRECTIVE + content`（~316 行）改为：
```python
mem_block = ''
if content:
    mems = search_memory(user['userId'], content, limit=5)
    mem_block = render_memory_block(mems)
prompt = _AGENT_DIRECTIVE + mem_block + content
```
> 说明：batch/scan 不经 HTTP `send_message`（它们由 `batch_engine` 直接驱动 OpenCode），故此处天然只服务真人会话，无需额外判断。`stored_parts` 不变 —— 记忆段只进 prompt，不进用户可见历史。

- [ ] **Step 6: 跑既有会话测试，确认未回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -k "ai_chat or send_message" -v`
Expected: 既有用例仍通过（`search_memory` 在未启用时返回 `[]`，注入段为空，prompt 不变）。

- [ ] **Step 7: 提交**

```bash
git add server/routes/ai_chat.py server/tests/test_memory.py
git commit -m "feat(memory): inject user memories into the agent prompt"
```

---

## Task 6：抽取钩子（每轮 idle 落库后抽取，跳过 batch/scan）

**Files:**
- Modify: `server/utils/memory.py`（加 `extract_from_turn`）
- Modify: `server/utils/chat_persist.py`（`_run_listener` 的 `idle` 分支 ~139）
- Test: `server/tests/test_memory.py`（追加抽取测试）

- [ ] **Step 1: 写失败测试（batch 会话跳过；交互会话后台 add）**

追加到 `server/tests/test_memory.py`：
```python
def test_extract_skips_noninteractive_session():
    # session row: (user_id, batch_id, scan_task_id)
    conn = MagicMock(); cur = MagicMock()
    cur.fetchone.return_value = ('u1', 'batch-9', None)
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    called = {'n': 0}
    with patch.object(mem, 'get_db', fake_db), \
         patch.object(mem, 'add_memory', lambda *a, **k: called.__setitem__('n', called['n'] + 1)):
        mem.extract_from_turn('sid', state={'turn_msg_id': 'm', 'text': 'hi'}, _sync=True)
    assert called['n'] == 0  # batch session → no extraction

def test_extract_interactive_calls_add(monkeypatch):
    rows = iter([('u1', None, None)])            # session lookup
    last_user = ['今天天气如何']                  # last user message text
    conn = MagicMock(); cur = MagicMock()
    cur.fetchone.side_effect = [('u1', None, None), ('今天天气如何',)]
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    captured = {}
    with patch.object(mem, 'get_db', fake_db), \
         patch.object(mem, '_turn_text', return_value='晴天'), \
         patch.object(mem, 'add_memory', lambda uid, msgs: captured.update(uid=uid, msgs=msgs)):
        mem.extract_from_turn('sid', state={'turn_msg_id': 'm'}, _sync=True)
    assert captured['uid'] == 'u1'
    assert {'role': 'assistant', 'content': '晴天'} in captured['msgs']
```
> `cur.fetchone` 第一次返回 session 行，第二次返回最近 user 消息行。`_turn_text` 抽取助手文本（mock 掉，避免依赖 build_content 细节）。

- [ ] **Step 2: 跑测试，确认失败** — FAIL（`extract_from_turn`/`_turn_text` 不存在）。

- [ ] **Step 3: 在 memory.py 加抽取钩子**

```python
def _turn_text(state):
    """从 listener 的累积 state 取助手纯文本。复用 chat_persist.build_content。"""
    try:
        from utils.chat_persist import build_content
        parts = build_content(state) or []
        return '\n'.join(p.get('text', '') for p in parts if p.get('type') == 'text').strip()
    except Exception:
        return ''


def _extract_user_text(content):
    """ai_chat_messages.content 是 [{'type':'text','text':...}, ...] 的 JSONB。"""
    if isinstance(content, list):
        return '\n'.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text').strip()
    return ''


def extract_from_turn(session_id, state, _sync=False):
    """每轮 idle、persist_turn 之后调用：抽取这一轮 [user, assistant] 进长期记忆。
    仅限真人交互会话（batch_id/scan_task_id 为空）。后台线程执行，绝不抛出。"""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, batch_id, scan_task_id FROM ai_chat_sessions WHERE id=%s",
                        (session_id,))
            row = cur.fetchone()
        if not row:
            return
        user_id, batch_id, scan_task_id = row[0], row[1], row[2]
        if not user_id or batch_id or scan_task_id:
            return
        assistant_text = _turn_text(state)
        if not assistant_text:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT content FROM ai_chat_messages WHERE session_id=%s AND role='user' "
                        "ORDER BY created_at DESC, id DESC LIMIT 1", (session_id,))
            r = cur.fetchone()
        user_text = _extract_user_text(r[0]) if r else ''
        messages = []
        if user_text:
            messages.append({'role': 'user', 'content': user_text})
        messages.append({'role': 'assistant', 'content': assistant_text})
        if _sync:
            add_memory(user_id, messages)
        else:
            threading.Thread(target=add_memory, args=(user_id, messages), daemon=True).start()
    except Exception as e:
        logger.warning('extract_from_turn failed: %s', e)
```

- [ ] **Step 4: 跑测试，确认通过** — Expected: PASS。

- [ ] **Step 5: 在 chat_persist 的 idle 分支接入**

`server/utils/chat_persist.py` 的 `_run_listener`，把 `idle` 分支（~138-141）改为：
```python
if sig == 'idle':
    persist_turn(sid, state)
    try:
        from utils.memory import extract_from_turn
        extract_from_turn(sid, state)
    except Exception:
        pass
    state = new_state()
    last_persist = time.monotonic()
```
> 延迟 import 避免潜在循环依赖（memory → chat_persist.build_content）。

- [ ] **Step 6: 跑 chat_persist 既有测试，确认未回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -k "persist or listener or chat" -v`
Expected: 既有通过（mem0 未启用时 `add_memory` no-op）。

- [ ] **Step 7: 提交**

```bash
git add server/utils/memory.py server/utils/chat_persist.py server/tests/test_memory.py
git commit -m "feat(memory): extract memory from each completed turn (skip batch/scan)"
```

---

## Task 7：设置页 2 个控件

**Files:**
- Modify: `src/views/admin/AiSettings.vue`

- [ ] **Step 1: 加控件 + 绑定**

在 AI 设置表单加（紧邻现有 model/timeout 字段）：
```vue
<el-form-item label="长期记忆 (mem0)">
  <el-switch v-model="form.mem0Enabled" />
  <span class="hint">开启后，AI 会话将自动形成并调用按用户的长期记忆</span>
</el-form-item>
<el-form-item label="Embedding 模型" v-if="form.mem0Enabled">
  <el-input v-model="form.embeddingModel" placeholder="text-embedding-v3" />
</el-form-item>
```
在 `form` 响应式对象加默认：`mem0Enabled: false, embeddingModel: 'text-embedding-v3'`。
加载设置（`get('/ai/settings')` 回填）时带上 `form.mem0Enabled = s.mem0Enabled; form.embeddingModel = s.embeddingModel || 'text-embedding-v3'`。
保存（`put('/ai/settings', {...})`）的 body 加 `mem0Enabled: form.mem0Enabled, embeddingModel: form.embeddingModel`。

- [ ] **Step 2: 类型检查 + 构建**

Run: `npx vue-tsc --noEmit -p tsconfig.json`
Expected: 无新增错误。

- [ ] **Step 3: 提交**

```bash
git add src/views/admin/AiSettings.vue
git commit -m "feat(memory): AI settings UI for mem0 enable + embedding model"
```

---

## Task 8：文档同步

**Files:**
- Create: `docs/user-guide/ai/long-term-memory.md`
- Modify: `docs/user-guide/README.md`（TOC 链接）
- Modify: `CLAUDE.md`（AI Agent Chat 段加一句）

- [ ] **Step 1: 写用户指南**

`docs/user-guide/ai/long-term-memory.md`：
```markdown
# AI 长期记忆

开启后（设置中心 → AI 设置 → 长期记忆），AI 助手会在真人对话中**自动记住**你的偏好、习惯与关键事实，并在之后的新会话里自动调用——无需你重复交代背景。

- **作用域**：按用户。每个人的记忆互相隔离，跨会话、跨项目共享。
- **自动形成**：每轮对话结束后台抽取，不影响回复速度。
- **自动调用**：发消息时检索你的相关记忆，作为上下文提供给助手（不出现在你的聊天记录里）。
- **不影响聊天**：未开启或记忆服务异常时，聊天完全正常。
- **批任务/扫描会话不参与**记忆（它们不是个人对话）。

> 依赖 AI 设置里的 API Key/端点（与 AI 智能查询同一套，默认阿里云 DashScope 兼容端点）。Embedding 模型默认 `text-embedding-v3`。
```
在 `docs/user-guide/README.md` 的 AI 小节加链接：`- [AI 长期记忆](ai/long-term-memory.md)`。

- [ ] **Step 2: 更新 CLAUDE.md**

在「AI Agent Chat (M1)」段末尾加一句：
```
长期记忆（M1）：`server/utils/memory.py` 用 mem0（Chroma + DashScope 兼容端点）按 `user_id` 管理跨会话记忆；`send_message` 转发前注入、SSE `idle` 落库后由 `extract_from_turn` 后台抽取；开关 `ai_settings.mem0_enabled`。全程降级，记忆层异常不影响聊天。
```

- [ ] **Step 3: 提交**

```bash
git add docs/user-guide/ai/long-term-memory.md docs/user-guide/README.md CLAUDE.md
git commit -m "docs(memory): user guide + CLAUDE.md for AI long-term memory (M1)"
```

---

## 验收（M1 完成判据）

- [ ] `scripts/mem0_smoke.py` 跑通（mem0+Chroma+DashScope add/search 一条）。
- [ ] `pytest tests/test_memory.py tests/test_ai_settings_mem0.py tests/test_routes_ai_settings.py` 全绿。
- [ ] 既有后端测试无回归（`mem0_enabled=false` 时聊天链路不变）。
- [ ] 手动：开启 mem0 → 在助手里说一条偏好 → 新会话里发相关问题，确认助手"记得"（或后端日志显示注入了记忆段）。
- [ ] 降级：关闭 mem0 → 聊天完全正常。
