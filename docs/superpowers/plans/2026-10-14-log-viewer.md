# 运行日志查看器（ai-chat.log / oc-serve.log） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理后台「运行日志」页实时跟尾查看 `ai-chat.log` 与 `oc-serve.log`（1.5s 增量轮询 + 暂停/自动滚动/关键字筛选）。

**Architecture:** 后端新增纯函数 `log_tail.read_tail`（字节偏移增量读、只吐完整行、轮转/缺失容错）+ 只读端点 `GET /ai/logs/tail`（双文件白名单、admin 权限）；前端新页 `LogViewer.vue` 以 1.5s 轮询增量追加渲染，筛选/缓冲裁剪纯前端。

**Tech Stack:** Flask（蓝图 + require_permission）、Vue 3 + Element Plus、vitest、Playwright、pytest。

**Spec:** `docs/superpowers/specs/2026-10-14-log-viewer-design.md`

## Global Constraints

- 权限 `admin.ai_chat_admin`；端点只读，无操作日志留痕（spec §2/§4）。
- 服务端常量（spec §4）：`TAIL_WINDOW_BYTES = MAX_READ_BYTES = 262144`；`DEFAULT_LIMIT = 300`；`MAX_LIMIT = 500`；`MAX_LINE_CHARS = 4000`。
- 前端常量（spec §5）：`POLL_MS = 1500`；`BUFFER_MAX = 5000`；裁剪批 `TRIM_BATCH = 1000`。
- 文件白名单固定映射：`chat` → `server/ai-chat.log`、`serve` → `server/oc-serve.log`（相对 `server/` 目录解析，与 `logging_setup.py` 同口径）；**不接受用户传入路径**。
- 核心语义：只吐完整行（无换行尾段留待下次）；`offset=-1` 为首次哨兵（末尾窗口回读最后 N 行，返回 `offset=size`）；`offset > size` 判轮转（`rotated=true`）。
- 中文注释/文案风格与仓库一致；提交信息 `feat(logs): ...`。
- 运行命令：pytest 在 `server/` 下（`python -m pytest tests/xxx.py -q`）；前端 `npx vitest run <file>`；E2E `npx playwright test <spec>`（根 `playwright.config.ts`）。
- Windows + Git Bash 环境；后端**不自动 reload**，新路由生效需重启后端。

---

### Task 1: log_tail 纯函数（跟尾读取核心）

**Files:**
- Create: `server/utils/log_tail.py`
- Test: `server/tests/test_log_tail.py`

**Interfaces:**
- Consumes: 无（标准库）。
- Produces（Task 2 依赖，签名完整）：
  - `read_tail(path: str, offset: int = -1, limit: int = DEFAULT_LIMIT) -> dict` → `{'offset': int, 'size': int, 'rotated': bool, 'truncated': bool, 'missing': bool, 'lines': list[str]}`
  - 常量：`TAIL_WINDOW_BYTES`、`MAX_READ_BYTES`、`MAX_LINE_CHARS`、`TRUNC_SUFFIX`、`SKIP_MARKER_TPL`、`DEFAULT_LIMIT`、`MAX_LIMIT`

- [ ] **Step 1: 写失败测试**

创建 `server/tests/test_log_tail.py`：

```python
"""log_tail 跟尾纯函数测试（2026-10-14 spec §6）。

覆盖：缺失态、哨兵尾窗、增量不重不漏、半行缓冲、轮转、limit/truncated
续读、超长行截断、整块无换行防活锁、CRLF。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.log_tail import (  # noqa: E402
    MAX_READ_BYTES, TRUNC_SUFFIX, read_tail,
)


def _write(tmp_path, content: bytes) -> str:
    p = tmp_path / 'a.log'
    p.write_bytes(content)
    return str(p)


def test_missing_file(tmp_path):
    r = read_tail(str(tmp_path / 'nope.log'))
    assert r['missing'] is True
    assert r['lines'] == []
    assert r['offset'] == 0


def test_sentinel_reads_tail_window(tmp_path):
    path = _write(tmp_path, b''.join(f'l{i}\n'.encode() for i in range(10)))
    r = read_tail(path, -1, limit=3)
    assert r['lines'] == ['l7', 'l8', 'l9']
    assert r['offset'] == os.path.getsize(path)
    assert r['rotated'] is False


def test_incremental_appends_only_new_lines(tmp_path):
    path = _write(tmp_path, b'one\ntwo\n')
    first = read_tail(path, -1)
    assert first['lines'] == ['one', 'two']
    with open(path, 'ab') as f:
        f.write(b'three\n')
    second = read_tail(path, first['offset'])
    assert second['lines'] == ['three']
    third = read_tail(path, second['offset'])
    assert third['lines'] == []


def test_partial_line_buffered_until_newline(tmp_path):
    path = _write(tmp_path, b'abc')
    r = read_tail(path, 0)
    assert r['lines'] == []
    assert r['offset'] == 0
    with open(path, 'ab') as f:
        f.write(b'def\n')
    r2 = read_tail(path, r['offset'])
    assert r2['lines'] == ['abcdef']


def test_rotation_offset_beyond_size(tmp_path):
    path = _write(tmp_path, b'old1\nold2\nold3\n')
    first = read_tail(path, -1)
    _write(tmp_path, b'new1\nnew2\n')          # 同路径重写 = 轮转后新文件
    r = read_tail(path, first['offset'])
    assert r['rotated'] is True
    assert r['lines'] == ['new1', 'new2']
    assert r['offset'] == os.path.getsize(path)


def test_limit_truncated_then_continue(tmp_path):
    path = _write(tmp_path, b''.join(f'l{i}\n'.encode() for i in range(10)))
    r1 = read_tail(path, 0, limit=4)
    assert r1['lines'] == ['l0', 'l1', 'l2', 'l3']
    assert r1['truncated'] is True
    r2 = read_tail(path, r1['offset'], limit=4)
    assert r2['lines'] == ['l4', 'l5', 'l6', 'l7']
    r3 = read_tail(path, r2['offset'], limit=4)
    assert r3['lines'] == ['l8', 'l9']
    assert r3['truncated'] is False


def test_long_line_truncated_but_offset_advances(tmp_path):
    long = 'x' * 5000
    path = _write(tmp_path, (long + '\n').encode())
    r = read_tail(path, 0)
    assert r['lines'] == [long[:4000] + TRUNC_SUFFIX]
    assert r['offset'] == os.path.getsize(path)


def test_giant_line_without_newline_skips_no_livelock(tmp_path):
    path = _write(tmp_path, b'x' * (MAX_READ_BYTES + 100))
    r = read_tail(path, 0)
    assert r['offset'] == MAX_READ_BYTES          # 已推进：不会重读同块
    assert r['lines'] and r['lines'][0].startswith('…[超长行已跳过')
    r2 = read_tail(path, r['offset'])
    assert r2['offset'] == os.path.getsize(path)


def test_crlf_stripped(tmp_path):
    path = _write(tmp_path, b'a\r\nb\r\n')
    assert read_tail(path, 0)['lines'] == ['a', 'b']
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd server && python -m pytest tests/test_log_tail.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.log_tail'`

- [ ] **Step 3: 实现**

创建 `server/utils/log_tail.py`：

```python
"""运行日志跟尾读取（纯函数，无 Flask 依赖；供 routes/ai_logs.py 调用）。

字节偏移增量模型（spec 2026-10-14-log-viewer-design §4）：
- offset=-1 哨兵：从末尾 256KB 窗口回读最后 limit 行，返回 offset=size；
- 增量：从 offset 顺序读（单次 ≤256KB），只吐完整行——最后一行无换行符
  视为半行留在文件里，offset 只推进到最后一个完整行末（不重不漏）；
- offset > size 判定轮转/截断：rotated=True 并按哨兵语义重读末尾窗口；
- 单行超 4000 字符截断显示；整块无换行（超长行）跳读防活锁；
- 文件缺失返回 missing=True（UI 显示等待，不是错误）。

Windows 注意：只读打开，不与写侧（logging RotatingFileHandler / serve
append）互斥；轮转后路径仍是原名，由 size < offset 自动发现。
"""
import os

TAIL_WINDOW_BYTES = 256 * 1024
MAX_READ_BYTES = 256 * 1024
MAX_LINE_CHARS = 4000
TRUNC_SUFFIX = ' …[已截断]'
SKIP_MARKER_TPL = '…[超长行已跳过 {n} 字节]'
DEFAULT_LIMIT = 300
MAX_LIMIT = 500


def _decode_line(raw: bytes) -> str:
    text = raw.decode('utf-8', errors='replace').rstrip('\r')
    if len(text) > MAX_LINE_CHARS:
        text = text[:MAX_LINE_CHARS] + TRUNC_SUFFIX
    return text


def _missing() -> dict:
    return {'offset': 0, 'size': 0, 'rotated': False, 'truncated': False,
            'missing': True, 'lines': []}


def _tail_window(path: str, size: int, limit: int):
    """末尾窗口回读最后 limit 行（跟尾语义：窗口内更早的行舍弃不标记）。"""
    with open(path, 'rb') as f:
        f.seek(max(0, size - TAIL_WINDOW_BYTES))
        raw = f.read()
    end = raw.rfind(b'\n')
    if end < 0:
        return [], size
    lines = [_decode_line(seg) for seg in raw[:end + 1].split(b'\n')[:-1]]
    return lines[-limit:], size


def _read_forward(path: str, offset: int, limit: int):
    """从 offset 顺序读完整行。返回 (lines, consumed_bytes, truncated)。"""
    with open(path, 'rb') as f:
        f.seek(offset)
        raw = f.read(MAX_READ_BYTES)
    lines, pos = [], 0
    while len(lines) < limit:
        nl = raw.find(b'\n', pos)
        if nl < 0:
            break
        lines.append(_decode_line(raw[pos:nl]))
        pos = nl + 1
    if not lines and len(raw) == MAX_READ_BYTES:
        # 整块无换行 = 超长行：跳读防活锁（否则 offset 不推进，永久重读同块）
        return [SKIP_MARKER_TPL.format(n=len(raw))], len(raw), False
    truncated = len(lines) == limit and (
        pos < len(raw) or len(raw) == MAX_READ_BYTES)
    return lines, pos, truncated


def read_tail(path: str, offset: int = -1, limit: int = DEFAULT_LIMIT) -> dict:
    """spec §4.2 响应形状：{offset, size, rotated, truncated, missing, lines}。"""
    if not path or not os.path.isfile(path):
        return _missing()
    size = os.path.getsize(path)
    if offset < 0 or offset > size:
        lines, off = _tail_window(path, size, limit)
        return {'offset': off, 'size': size, 'rotated': offset > size,
                'truncated': False, 'missing': False, 'lines': lines}
    lines, consumed, truncated = _read_forward(path, offset, limit)
    return {'offset': offset + consumed, 'size': size, 'rotated': False,
            'truncated': truncated, 'missing': False, 'lines': lines}
```

- [ ] **Step 4: 跑测试确认绿**

Run: `cd server && python -m pytest tests/test_log_tail.py -q`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add server/utils/log_tail.py server/tests/test_log_tail.py
git commit -m "feat(logs): log_tail 跟尾纯函数——字节偏移增量/轮转/半行缓冲"
```

---

### Task 2: `/ai/logs/tail` 路由与蓝图注册

**Files:**
- Create: `server/routes/ai_logs.py`
- Modify: `server/app.py`（`from routes.ai_gate_policies import ...` 行后加导入；`app.register_blueprint(ai_gate_policies_bp)` 行后加注册）
- Test: `server/tests/test_ai_logs_routes.py`

**Interfaces:**
- Consumes: Task 1 的 `read_tail`、`DEFAULT_LIMIT`、`MAX_LIMIT`。
- Produces（Task 3 前端依赖的 HTTP 契约）：`GET /ai/logs/tail?file=chat|serve&offset=<int≥-1，默认-1>&limit=<1..500，默认300>` → 200 `{file, offset, size, rotated, truncated, missing, lines}`；非法参数 400；无权限 401/403；读失败 500。模块级 `LOG_FILES: dict[str, str]`（测试 monkeypatch 点）。

- [ ] **Step 1: 写失败测试**

创建 `server/tests/test_ai_logs_routes.py`：

```python
"""GET /ai/logs/tail 路由测试：权限门遍历 + 契约（白名单/参数/形状/缺失态）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routes import ai_logs  # noqa: E402
from routes.ai_logs import LOG_FILES  # noqa: E402

BASE = '/ai/logs/tail'


def test_blueprint_exposes_expected_route(app):
    paths = {str(r) for r in app.url_map.iter_rules()}
    assert BASE in paths


def test_anonymous_rejected(client):
    assert client.get(BASE + '?file=chat').status_code in (401, 403)


def test_every_route_permission_gated(app, client):
    """遍历式兜底：本蓝图任何端点漏挂 require_permission 在这里红。"""
    checked = 0
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith('/ai/logs'):
            continue
        for m in (rule.methods or set()) & {'GET', 'POST'}:
            resp = client.open(str(rule) + '?file=chat', method=m)
            assert resp.status_code in (401, 403), f'{m} {rule} 未挂权限门'
            checked += 1
    assert checked >= 1


def test_admin_reads_file(client, admin_headers, tmp_path, monkeypatch):
    p = tmp_path / 'ai-chat.log'
    p.write_bytes(b'hello\nworld\n')
    monkeypatch.setitem(LOG_FILES, 'chat', str(p))
    resp = client.get(BASE + '?file=chat', headers=admin_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['lines'] == ['hello', 'world']
    assert data['file'] == 'chat'
    assert data['missing'] is False


def test_invalid_file_400(client, admin_headers):
    resp = client.get(BASE + '?file=../secret', headers=admin_headers)
    assert resp.status_code == 400


def test_invalid_params_400(client, admin_headers):
    for qs in ('limit=0', 'limit=501', 'offset=-2', 'offset=abc'):
        resp = client.get(f'{BASE}?file=chat&{qs}', headers=admin_headers)
        assert resp.status_code == 400, qs


def test_missing_file_state(client, admin_headers, tmp_path, monkeypatch):
    monkeypatch.setitem(LOG_FILES, 'serve', str(tmp_path / 'nope.log'))
    resp = client.get(BASE + '?file=serve', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['missing'] is True
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd server && python -m pytest tests/test_ai_logs_routes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'routes.ai_logs'`

- [ ] **Step 3: 实现路由并注册**

创建 `server/routes/ai_logs.py`：

```python
"""运行日志查看端点（spec 2026-10-14-log-viewer-design §4）。

GET /ai/logs/tail?file=chat|serve&offset=&limit= —— 只读跟尾，双文件白名单，
路径不接受用户输入；权限 admin.ai_chat_admin（日志含聊天内容与提示词）。
前端 1.5s 增量轮询消费；跟尾语义细节见 utils/log_tail.py。
"""
import logging
import os

from flask import Blueprint, jsonify, request

from auth import require_permission
from utils.log_tail import DEFAULT_LIMIT, MAX_LIMIT, read_tail

log = logging.getLogger(__name__)

ai_logs_bp = Blueprint('ai_logs', __name__, url_prefix='/ai/logs')

# server/ 目录（routes/ 的上一级）——与 logging_setup / opencode_global 落盘同口径
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 白名单：逻辑名 → 落盘路径。公开展示给确认页的 ID 集合唯一入口。
LOG_FILES = {
    'chat': os.path.join(_SERVER_DIR, 'ai-chat.log'),
    'serve': os.path.join(_SERVER_DIR, 'oc-serve.log'),
}


@ai_logs_bp.get('/tail')
@require_permission('admin.ai_chat_admin')
def tail():
    name = (request.args.get('file') or '').strip()
    if name not in LOG_FILES:
        return jsonify({'error': 'file 只支持 chat / serve'}), 400
    try:
        offset = int(request.args.get('offset', -1))
        limit = int(request.args.get('limit', DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return jsonify({'error': 'offset/limit 必须是整数'}), 400
    if offset < -1 or limit < 1 or limit > MAX_LIMIT:
        return jsonify({'error': f'offset≥-1；limit 1..{MAX_LIMIT}'}), 400
    try:
        result = read_tail(LOG_FILES[name], offset=offset, limit=limit)
    except Exception as e:  # noqa: BLE001 —— 读失败按不可用上报，留痕
        log.warning('log tail failed file=%s: %s', name, e)
        return jsonify({'error': '读取日志失败'}), 500
    result['file'] = name
    return jsonify(result), 200
```

修改 `server/app.py` 两处（锚点已验证）：

1. 第 56 行 `from routes.ai_gate_policies import ai_gate_policies_bp` 之后加：

```python
from routes.ai_logs import ai_logs_bp
```

2. 第 576 行 `app.register_blueprint(ai_gate_policies_bp)` 之后加：

```python
app.register_blueprint(ai_logs_bp)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `cd server && python -m pytest tests/test_ai_logs_routes.py -q`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai_logs.py server/app.py server/tests/test_ai_logs_routes.py
git commit -m "feat(logs): GET /ai/logs/tail 只读跟尾端点——白名单双文件+admin 权限"
```

---

### Task 3: 前端页（API 封装 + LogViewer.vue + 目录入口 + 组件测）

**Files:**
- Create: `src/api/adminLogs.ts`
- Create: `src/views/admin/LogViewer.vue`
- Modify: `src/views/admin/hub/settingsCatalog.ts`（AI 能力组 `gate-policies` 条目后加一条）
- Test: `src/views/admin/__tests__/LogViewer.test.ts`

**Interfaces:**
- Consumes: Task 2 的 HTTP 契约；`@/utils/request` 的 `get`；目录项 id `logs` → 路由 `/admin/logs` 由 settingsCatalog 自动生成。
- Produces: 页面组件（无对外符号）；`fetchLogTail(file: 'chat'|'serve', offset: number, limit?: number): Promise<LogTailResponse>`。

- [ ] **Step 1: 写 API 封装**

创建 `src/api/adminLogs.ts`：

```ts
/**
 * 运行日志查看（2026-10-14 spec §4）：GET /ai/logs/tail 增量跟尾。
 */
import { get } from '@/utils/request'

export interface LogTailResponse {
  file: string
  offset: number
  size: number
  rotated: boolean
  truncated: boolean
  missing: boolean
  lines: string[]
}

export function fetchLogTail(
  file: 'chat' | 'serve',
  offset: number,
  limit = 300,
): Promise<LogTailResponse> {
  return get<LogTailResponse>('/ai/logs/tail', { file, offset, limit })
}
```

- [ ] **Step 2: 写失败组件测试**

创建 `src/views/admin/__tests__/LogViewer.test.ts`：

```ts
/**
 * LogViewer 组件单元测试（2026-10-14 spec §6）：轮询/暂停/筛选/轮转/裁剪/切文件。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mockGet = vi.fn()
vi.mock('@/utils/request', () => ({
  get: (...args: unknown[]) => mockGet(...args),
}))

import LogViewer from '../LogViewer.vue'

function resp(over: Record<string, unknown> = {}) {
  return {
    file: 'chat', offset: 10, size: 10, rotated: false,
    truncated: false, missing: false, lines: [], ...over,
  }
}

async function mountView() {
  const wrapper = mount(LogViewer)
  await flushPromises()
  return wrapper
}

describe('LogViewer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockGet.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('首次哨兵 offset=-1，随后按返回 offset 增量轮询', async () => {
    mockGet
      .mockResolvedValueOnce(resp({ lines: ['alpha'], offset: 10 }))
      .mockResolvedValueOnce(resp({ lines: ['beta'], offset: 20 }))
    const wrapper = await mountView()
    expect(mockGet).toHaveBeenCalledWith('/ai/logs/tail',
      { file: 'chat', offset: -1, limit: 300 })
    expect(wrapper.text()).toContain('alpha')
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()
    expect(mockGet).toHaveBeenLastCalledWith('/ai/logs/tail',
      { file: 'chat', offset: 10, limit: 300 })
    expect(wrapper.text()).toContain('beta')
    wrapper.unmount()
  })

  it('暂停停表不发请求，继续立即补拉', async () => {
    mockGet.mockResolvedValue(resp({ lines: ['x'] }))
    const wrapper = await mountView()
    const calls = mockGet.mock.calls.length
    await wrapper.find('[data-test="pause-btn"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    expect(mockGet.mock.calls.length).toBe(calls)
    await wrapper.find('[data-test="pause-btn"]').trigger('click')
    await flushPromises()
    expect(mockGet.mock.calls.length).toBe(calls + 1)
    wrapper.unmount()
  })

  it('关键字筛选只渲染命中行并计数', async () => {
    mockGet.mockResolvedValueOnce(resp({
      lines: ['gate hit', 'other line', 'gate again'], offset: 30,
    }))
    const wrapper = await mountView()
    await wrapper.find('[data-test="filter-input"] input').setValue('gate')
    await flushPromises()
    expect(wrapper.text()).toContain('gate hit')
    expect(wrapper.text()).not.toContain('other line')
    expect(wrapper.text()).toContain('命中 2 / 共 3')
    wrapper.unmount()
  })

  it('rotated 响应清缓冲并插分隔线', async () => {
    mockGet
      .mockResolvedValueOnce(resp({ lines: ['old1'], offset: 999 }))
      .mockResolvedValueOnce(resp({ rotated: true, lines: ['new1'], offset: 5 }))
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('old1')
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()
    expect(wrapper.text()).toContain('文件已轮转')
    expect(wrapper.text()).not.toContain('old1')
    expect(wrapper.text()).toContain('new1')
    wrapper.unmount()
  })

  it('超出缓冲上限裁剪最早行并显示标记', async () => {
    const many = Array.from({ length: 5200 },
      (_, i) => `#${String(i).padStart(4, '0')}#`)
    mockGet.mockResolvedValueOnce(resp({ lines: many, offset: 1 }))
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('已裁剪更早日志')
    expect(wrapper.text()).toContain('#5199#')
    expect(wrapper.text()).not.toContain('#0999#')
    expect(wrapper.text()).toContain('#1000#')
    wrapper.unmount()
  })

  it('文件切换重置 offset 并请求新文件', async () => {
    mockGet.mockResolvedValue(resp({ lines: [] }))
    const wrapper = await mountView()
    // 广播组件事件直驱 v-model + change（jsdom 里 radio label 点击不可靠，
    // 真实浏览器行为由 E2E 覆盖）
    const group = wrapper.findComponent({ name: 'ElRadioGroup' })
    group.vm.$emit('update:modelValue', 'serve')
    group.vm.$emit('change', 'serve')
    await flushPromises()
    expect(mockGet).toHaveBeenLastCalledWith('/ai/logs/tail',
      { file: 'serve', offset: -1, limit: 300 })
    wrapper.unmount()
  })

  it('missing 响应显示等待文案', async () => {
    mockGet.mockResolvedValueOnce(resp({ missing: true, lines: [] }))
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('暂无日志文件（等待产生）')
    expect(mockGet).toHaveBeenLastCalledWith('/ai/logs/tail',
      { file: 'chat', offset: -1, limit: 300 })
    wrapper.unmount()
  })
})
```

- [ ] **Step 3: 跑测试确认红**

Run: `npx vitest run src/views/admin/__tests__/LogViewer.test.ts`
Expected: FAIL — `Failed to resolve import "../LogViewer.vue"`

- [ ] **Step 4: 实现组件**

创建 `src/views/admin/LogViewer.vue`：

```vue
<template>
  <div class="log-viewer" data-test="log-viewer">
    <div class="log-viewer__bar">
      <el-radio-group v-model="activeFile" size="small" @change="switchFile">
        <el-radio-button data-test="file-chat" value="chat">ai-chat.log</el-radio-button>
        <el-radio-button data-test="file-serve" value="serve">oc-serve.log</el-radio-button>
      </el-radio-group>
      <el-input v-model="filter" size="small" clearable class="log-viewer__filter"
                data-test="filter-input" placeholder="关键字筛选（等价 grep）" />
      <el-button size="small" data-test="pause-btn" @click="togglePause">
        {{ paused ? '继续' : '暂停' }}</el-button>
      <el-switch v-model="autoScroll" size="small" active-text="自动滚动" />
      <el-button size="small" data-test="clear-btn" @click="clearView">清屏</el-button>
      <span class="log-viewer__status">
        <span :class="['log-viewer__dot', `is-${status}`]" />
        {{ statusText }} · 命中 {{ matchedCount }} / 共 {{ lineCount }} 行
      </span>
    </div>
    <div ref="paneRef" class="log-viewer__pane" data-test="log-pane" @scroll="onScroll">
      <div v-if="trimmed" class="log-viewer__marker">— 已裁剪更早日志 —</div>
      <div v-for="(e, i) in visibleEntries" :key="i"
           :class="['log-viewer__line', e.kind === 'marker' ? 'is-marker' : '']">
        {{ e.text }}</div>
      <div v-if="!visibleEntries.length" class="log-viewer__empty">
        {{ status === 'waiting' ? '暂无日志文件（等待产生）' : '（无匹配行）' }}</div>
    </div>
    <el-button v-if="!autoScroll" class="log-viewer__to-bottom" size="small"
               data-test="to-bottom" @click="backToBottom">回到底部</el-button>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElButton, ElInput, ElRadioButton, ElRadioGroup, ElSwitch } from 'element-plus'
import { fetchLogTail } from '@/api/adminLogs'

/**
 * 运行日志跟尾页（2026-10-14 spec §5）：1.5s 增量轮询 + 暂停/自动滚动/筛选。
 * 两个常量与轮询契约对齐 spec §4/§5，改动需两侧同步。
 */
const POLL_MS = 1500
const BUFFER_MAX = 5000
const TRIM_BATCH = 1000
const DEFAULT_LIMIT = 300

interface LogEntry { kind: 'line' | 'marker'; text: string }

const activeFile = ref<'chat' | 'serve'>('chat')
const entries = ref<LogEntry[]>([])
const offsetRef = ref(-1)
const paused = ref(false)
const autoScroll = ref(true)
const filter = ref('')
const status = ref<'ok' | 'error' | 'waiting'>('ok')
const trimmed = ref(false)
const paneRef = ref<HTMLElement | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const lineEntries = computed(() => entries.value.filter(e => e.kind === 'line'))
const lineCount = computed(() => lineEntries.value.length)
const matchedCount = computed(() => {
  const kw = filter.value.trim().toLowerCase()
  if (!kw) return lineCount.value
  return lineEntries.value.filter(e => e.text.toLowerCase().includes(kw)).length
})
const visibleEntries = computed(() => {
  const kw = filter.value.trim().toLowerCase()
  return entries.value.filter(e =>
    e.kind === 'marker' || !kw || e.text.toLowerCase().includes(kw))
})
const statusText = computed(() =>
  status.value === 'ok' ? '实时' : status.value === 'waiting' ? '等待文件' : '连接中断')

function scrollToBottom() {
  void nextTick(() => {
    const el = paneRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function appendLines(lines: string[]) {
  if (!lines.length) return
  for (const text of lines) entries.value.push({ kind: 'line', text })
  if (entries.value.length > BUFFER_MAX) {
    entries.value.splice(0, TRIM_BATCH)
    trimmed.value = true
  }
  if (autoScroll.value) scrollToBottom()
}

async function poll() {
  if (document.hidden || paused.value) return
  try {
    const r = await fetchLogTail(activeFile.value, offsetRef.value, DEFAULT_LIMIT)
    if (r.missing) {
      status.value = 'waiting'
      return
    }
    status.value = 'ok'
    if (r.rotated && offsetRef.value >= 0) {
      // 轮转/截断：清缓冲、留分隔线，再把新文件的尾窗行补进来
      entries.value = [{
        kind: 'marker',
        text: `— 文件已轮转，重新加载（${new Date().toLocaleTimeString()}） —`,
      }]
      trimmed.value = false
    }
    offsetRef.value = r.offset
    appendLines(r.lines)
  } catch {
    status.value = 'error'
  }
}

function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}
function startTimer() {
  if (!timer) timer = setInterval(() => { void poll() }, POLL_MS)
}

function togglePause() {
  paused.value = !paused.value
  if (paused.value) stopTimer()
  else { startTimer(); void poll() }
}

function switchFile() {
  stopTimer()
  offsetRef.value = -1
  entries.value = []
  trimmed.value = false
  status.value = 'ok'
  if (!paused.value) { startTimer(); void poll() }
}

function clearView() {
  entries.value = []
  trimmed.value = false
}

function backToBottom() {
  autoScroll.value = true
  scrollToBottom()
}

function onScroll() {
  const el = paneRef.value
  if (!el) return
  const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 24
  autoScroll.value = nearBottom
}

function onVisibility() {
  if (document.hidden) stopTimer()
  else if (!paused.value) { startTimer(); void poll() }
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
  startTimer()
  void poll()
})
onUnmounted(() => {
  document.removeEventListener('visibilitychange', onVisibility)
  stopTimer()
})
</script>

<style scoped lang="scss">
.log-viewer {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 400px;
}
.log-viewer__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.log-viewer__filter { width: 220px; }
.log-viewer__status {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.log-viewer__dot {
  width: 8px; height: 8px; border-radius: 50%; background: #67c23a;
  &.is-error { background: #f56c6c; }
  &.is-waiting { background: #e6a23c; }
}
.log-viewer__pane {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #1e1e1e;
  color: #d4d4d4;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
  padding: 8px 10px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-viewer__line.is-marker {
  color: #e6a23c;
  text-align: center;
  margin: 4px 0;
}
.log-viewer__empty {
  color: #666;
  text-align: center;
  padding-top: 40px;
}
.log-viewer__to-bottom {
  position: absolute;
  right: 24px;
  bottom: 24px;
}
</style>
```

- [ ] **Step 5: 加目录入口（自动生成路由与权限门）**

修改 `src/views/admin/hub/settingsCatalog.ts`，AI 能力组 `gate-policies` 条目之后加：

```ts
    { id: 'logs', label: '运行日志', perm: 'admin.ai_chat_admin', icon: 'Document',
      component: () => import('@/views/admin/LogViewer.vue') },
```

- [ ] **Step 6: 跑测试确认绿**

Run: `npx vitest run src/views/admin/__tests__/LogViewer.test.ts`
Expected: 7 passed

- [ ] **Step 7: 提交**

```bash
git add src/api/adminLogs.ts src/views/admin/LogViewer.vue \
  src/views/admin/hub/settingsCatalog.ts src/views/admin/__tests__/LogViewer.test.ts
git commit -m "feat(logs): 运行日志页——跟尾/暂停/自动滚动/筛选 + AI 能力组入口"
```

---

### Task 4: 真实链路 E2E

**Files:**
- Create: `e2e/ai-full/admin-log-viewer.spec.ts`

**Interfaces:**
- Consumes: Task 2/3 的端点与页面（`/admin/logs`）；`e2e/ai-full/helpers.ts` 的 `gotoWithAuth(page, path)` / `screenshot(page, name)`。
- Produces: 回归用例（不带出任何被依赖符号）。

**前置（本任务的运行条件，先做）：**
- dev 栈在跑（后端 3002 / 前端 vite / OC serve）；**后端不自动 reload**——若 Task 2 之后没重启过后端，先重启：找到 3002 监听 PID（`netstat -ano | grep :3002`）→ `taskkill //F //PID <pid>` → `cd server && python app.py`（后台起）。
- 页面 `/admin/logs` 需前端 dev server 已加载新目录项（vite 即时编译，无需重启）。

- [ ] **Step 1: 写 E2E 用例**

创建 `e2e/ai-full/admin-log-viewer.spec.ts`：

```ts
/**
 * 运行日志查看器 E2E（2026-10-14 spec §6 真实链路）：真页面 + 真文件 + 真轮询。
 * 标记行走 fs 直写 server/ai-chat.log——后端 logging 以 append 打开，
 * 外部追加对已打开句柄可见（Python 日志句柄同文件追加）。
 * 轮转场景不做 E2E（Windows 重命名被占日志文件不可靠，单测覆盖，spec §6）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { gotoWithAuth, screenshot } from './helpers'

const DIR = path.dirname(fileURLToPath(import.meta.url))
const LOG = path.join(DIR, '../../server/ai-chat.log')

function appendMarker(text: string) {
  fs.appendFileSync(LOG, `\n${text}\n`, 'utf-8')
}

test('运行日志页：跟尾出现 / 暂停停刷 / 继续补拉 / 筛选计数', async ({ page }) => {
  test.setTimeout(180_000)
  const tk = `E2E-LOGVIEW-${Date.now()}`
  appendMarker(`${tk}-A`)

  await gotoWithAuth(page, '/admin/logs')
  const pane = page.locator('[data-test="log-pane"]')
  await expect(pane).toBeVisible({ timeout: 30_000 })
  await expect(pane).toContainText(`${tk}-A`, { timeout: 15_000 })
  await screenshot(page, 'admin-log-viewer-live')

  // 暂停：新行不出现；继续：补拉出现
  await page.locator('[data-test="pause-btn"]').click()
  appendMarker(`${tk}-B`)
  await page.waitForTimeout(4_000)
  await expect(pane).not.toContainText(`${tk}-B`)
  await page.locator('[data-test="pause-btn"]').click()
  await expect(pane).toContainText(`${tk}-B`, { timeout: 15_000 })

  // 筛选：命中显示、无匹配计 0
  await page.locator('[data-test="filter-input"] input').fill(tk)
  await expect(pane).toContainText(`${tk}-A`)
  await expect(page.locator('.log-viewer__status')).toContainText('命中 2 /')
  await page.locator('[data-test="filter-input"] input').fill('zzz-not-exist-zzz')
  await expect(page.locator('.log-viewer__status')).toContainText('命中 0')
  await page.locator('[data-test="filter-input"] input').fill('')

  // 文件切换：oc-serve.log 可加载（内容不限，页签生效即可）
  await page.locator('[data-test="file-serve"]').click()
  await expect(pane).toBeVisible()
  await expect(page.locator('.log-viewer__status')).toContainText(/实时|等待文件|连接中断/)
})
```

- [ ] **Step 2: 跑 E2E 确认绿**

Run: `npx playwright test e2e/ai-full/admin-log-viewer.spec.ts`
Expected: 1 passed（如失败按输出定位；`/admin/logs` 404 说明目录项没进构建，重启 vite）

- [ ] **Step 3: 提交**

```bash
git add e2e/ai-full/admin-log-viewer.spec.ts
git commit -m "test(logs): 运行日志页 E2E——跟尾/暂停/筛选真实链路"
```

---

## Self-Review 记录（写计划后自查）

- **Spec 覆盖**：§3 架构 → Task 1/2/3 文件全部落实；§4 契约（参数/响应/语义/上限/错误码）→ Task 1 实现+测试、Task 2 路由测；§5 前端行为（轮询/暂停/自动滚动/筛选/缓冲/轮转/缺失）→ Task 3 组件+7 例测试；§6 三层测试 → Task 1/2（单测）、Task 3（组件测）、Task 4（E2E）；§2 非目标均无对应任务（符合）。轮转 E2E 不做——spec 明示由单测覆盖，Task 4 头注释注明。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码；运行命令含预期结果。
- **类型一致性**：`read_tail` 返回键在 Task 2 路由与 Task 3 `LogTailResponse` 逐一对应（`file/offset/size/rotated/truncated/missing/lines`）；`fetchLogTail` 参数 `(file, offset, limit)` 与组件调用一致；`LOG_FILES` monkeypatch 点与路由实现同名。
