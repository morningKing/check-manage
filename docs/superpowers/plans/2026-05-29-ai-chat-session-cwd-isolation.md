# AI 助手会话工作目录隔离 (SP1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让某会话内 agent/skill 的内置工具(bash/write/edit/git clone)在该会话的 workspace 下执行,通过在 prompt 请求上传 `directory=<workspace>` 实现会话级工作目录隔离(保留单一共享 opencode serve)。

**Architecture:** OpenCode 的 `/session/{id}/prompt_async` 接受 `directory` query 参数并以它作为该轮工具执行的 cwd(已实测)。`send_prompt_async` 现在不传它,导致工具落在 server 启动 cwd(仓库根)。修复:`send_prompt_async` 增加 `directory` 参数,`send_message` 传该会话的 `workspace_path`。

**Tech Stack:** Python Flask 后端;`requests`;pytest(`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`)。

设计依据:`docs/superpowers/specs/2026-05-29-ai-chat-session-cwd-isolation-design.md`

> 后端测试:在 `server/` 下 `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/<file> -v`(Windows cmd;PowerShell 用 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`)。

---

### Task 1: `send_prompt_async` 增加 `directory` 参数

**Files:**
- Modify: `server/utils/opencode_client.py`
- Test: `server/tests/test_opencode_client.py`

- [ ] **Step 1: 写失败测试** — 在 `server/tests/test_opencode_client.py` 末尾追加:

```python
def test_send_prompt_async_includes_directory_when_given():
    fake_resp = MagicMock()
    fake_resp.status_code = 204
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").send_prompt_async("ses_42", "hi", directory="/tmp/ws")
    _, kwargs = post.call_args
    assert kwargs["params"] == {"directory": "/tmp/ws"}
    assert kwargs["json"]["parts"] == [{"type": "text", "text": "hi"}]


def test_send_prompt_async_omits_directory_when_empty():
    fake_resp = MagicMock()
    fake_resp.status_code = 204
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").send_prompt_async("ses_42", "hi")
    _, kwargs = post.call_args
    assert kwargs.get("params") is None
```

- [ ] **Step 2: 运行确认失败**

Run (server 目录): `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_opencode_client.py::test_send_prompt_async_includes_directory_when_given -v`
Expected: FAIL — `send_prompt_async` 收到未知关键字 `directory`(TypeError),或 `params` 未传(KeyError)。

- [ ] **Step 3: 实现** — 编辑 `server/utils/opencode_client.py`,把 `send_prompt_async` 整体替换为:

```python
    def send_prompt_async(self, opencode_session_id: str, content: str,
                          model: str = "", directory: str = "") -> None:
        """Send a prompt. `model` ("<providerID>/<modelID>") is passed explicitly
        because OpenCode does NOT honor the per-directory opencode.json `model`
        field for prompt selection — without it the server falls back to its
        own default model.

        `directory` (absolute path) is passed as the ?directory= query param so
        this turn's tools (bash/write/edit) run with cwd=directory — i.e. the
        session's workspace. Without it OpenCode uses the server's launch cwd.
        """
        body = {"parts": [{"type": "text", "text": content}]}
        if model and "/" in model:
            provider_id, model_id = model.split("/", 1)
            body["model"] = {"providerID": provider_id, "modelID": model_id}
        params = {"directory": directory} if directory else None
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/prompt_async"),
            params=params,
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
```

- [ ] **Step 4: 运行确认通过(含既有测试不回归)**

Run (server 目录): `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_opencode_client.py -v`
Expected: 全部通过(新增 2 项 + 既有 `test_send_prompt_async_sends_parts_array` / `_includes_model_when_given` 仍过)。

- [ ] **Step 5: 提交**

```bash
git add server/utils/opencode_client.py server/tests/test_opencode_client.py
git commit -m "feat(ai-chat): send_prompt_async passes directory= for per-session cwd"
```

---

### Task 2: `send_message` 传入会话 workspace 作为 directory

**Files:**
- Modify: `server/routes/ai_chat.py`
- Test: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: 写失败断言** — 在 `server/tests/test_routes_ai_chat.py` 的 `test_send_message_persists_user_and_calls_opencode` 中,`assert kwargs.get('model')` 之后追加一行:

```python
    # directory must be the session workspace so the agent's tools run there
    assert kwargs.get('directory') == '/tmp/ws'
```
(该测试已把会话行 mock 为 `('sess_x','user-1','oc_sess_42','active','/tmp/ws')`,故 `sess[4]=='/tmp/ws'`。)

- [ ] **Step 2: 运行确认失败**

Run (server 目录): `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py::test_send_message_persists_user_and_calls_opencode -v`
Expected: FAIL —`kwargs.get('directory')` 为 None(当前未传)。

- [ ] **Step 3: 实现** — 编辑 `server/routes/ai_chat.py` 的 `send_message`,把现有调用:

```python
    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(sess[2], prompt.strip(), model=OPENCODE_MODEL)
```
替换为:
```python
    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(
        sess[2], prompt.strip(), model=OPENCODE_MODEL, directory=sess[4],
    )
```
(`sess` 来自 `_load_session_for_user`,字段顺序为 id, user_id, opencode_session_id, status, workspace_path,故 `sess[4]` 为 workspace 绝对路径。)

- [ ] **Step 4: 运行确认通过 + ai_chat 路由不回归**

Run (server 目录): `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v`
Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): run agent tools in the session workspace (pass directory)"
```

---

### Task 3: 真机验证(隔离生效)

**Files:** 无(验证)。前置:`:8080` 生产栈运行(`cd server && python proxy.py`),OpenCode 在 4096。

- [ ] **Step 1: 重启后端以加载改动**

后端是 proxy.py 的子进程。停掉 8080/3001/3003 进程树,`cd server && python proxy.py` 重启,确认 `http://127.0.0.1:3003/health` 200、OpenCode 4096 可达。

- [ ] **Step 2: 真机:agent 在会话内 clone+改文件,确认落在该会话 workspace**

Run (server 目录,`python -`):

```python
import json, time, threading, urllib.request, os
from config import DB_CONFIG
import psycopg2
BASE='http://127.0.0.1:8080'
def req(m,p,t=None,b=None):
    d=json.dumps(b).encode() if b is not None else None
    r=urllib.request.Request(BASE+p,data=d,method=m); r.add_header('Content-Type','application/json')
    if t: r.add_header('Authorization','Bearer '+t)
    return json.loads(urllib.request.urlopen(r,timeout=30).read())
tok=req('POST','/api/auth/login',b={'username':'admin','password':'admin123'})['token']
s=req('POST','/api/ai/chat/sessions',t=tok,b={}); sid=s['id']; ws=s['workspacePath']
idle={'v':False}
def sse():
    resp=urllib.request.urlopen(f'{BASE}/api/ai/chat/sessions/{sid}/events?access_token={tok}',timeout=180)
    for line in resp:
        if b'session.idle' in line: idle['v']=True; break
threading.Thread(target=sse,daemon=True).start(); time.sleep(1)
req('POST',f'/api/ai/chat/sessions/{sid}/messages',t=tok,b={'content':'用 bash 执行 git clone https://github.com/octocat/Hello-World.git，然后在 Hello-World 目录新建 note.txt 内容 hi'})
dl=time.time()+150
while time.time()<dl and not idle['v']: time.sleep(2)
time.sleep(2)
# 断言:文件落在该会话 workspace,而不是仓库根目录
in_ws=[]
for root,_,fs in os.walk(ws):
    for f in fs: in_ws.append(os.path.relpath(os.path.join(root,f),ws))
print('files in session workspace:', [f for f in in_ws if 'Hello-World' in f][:10])
print('Hello-World cloned into workspace:', any('Hello-World' in f for f in in_ws))
# cwd is server/, so ../Hello-World would be the repo root (the old buggy location)
print('repo root has stray Hello-World:', os.path.isdir(os.path.join(os.getcwd(), '..', 'Hello-World')))
```

Expected: `Hello-World cloned into workspace: True`;仓库根目录**没有**新的 `Hello-World`(`repo root has stray Hello-World: False`)。若 agent 走了 `task` 子代理或未克隆成功,重试或换更直接的提示;关键判据是"新文件出现在会话 workspace 而非仓库根"。

- [ ] **Step 3:(可选)二次会话隔离**

再建一个会话,确认其 workspace 看不到第一个会话 clone 的文件(两 workspace 路径不同,天然隔离)。

- [ ] **Step 4: 清理**

删除验证产生的会话 workspace(或留作演示);若仓库根出现任何 `Hello-World` 残留(说明未生效),`rm -rf Hello-World` 并排查。

---

## 备注 / 风险

- 依赖 OpenCode 的 `directory` query 参数生效(当前运行版本已实测 `/shell?directory=X` → `info.path.cwd==X`);`prompt_async` 同属带 `directory` 的端点,预期一致,Task 3 即端到端验证。
- 向后兼容:`directory` 为空时不带 `params`(既有调用/测试不受影响)。
- 改动面极小(2 个函数 + 测试),回归风险低。
- SP2(变更文件渲染)另立,可复用 OpenCode 原生 `GET /session/{id}/diff`(`FileDiff[]`)。
