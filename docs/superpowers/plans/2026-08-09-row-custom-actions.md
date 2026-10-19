# 数据页自定义行级操作按钮 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让数据页可以配置行级功能按钮，点击后对该行异步触发一条 Webhook 规则或一个 AI 扫描任务，执行状态与结果回写到该行字段上。

**Architecture:** 在 `page_configs` 新增 `row_actions` JSONB 列存放**薄绑定配置**（按钮文案、绑哪个执行器、可见性、参数表单、回写映射）；重执行器复用既有的 `webhook_engine`（HMAC/超时/重试/日志）与 `ai_scan_engine` + 批任务引擎（上下文目录/子会话/结构化回写）。新增 `row_action_engine` 只做编排：校验 → 写「执行中」→ 分派 → 落终态。前端在 `DataTable` 已有的 `extra-actions` 插槽内渲染按钮，提交后轮询该行状态。

**Tech Stack:** Vue 3 + TypeScript + Element Plus + Pinia；Python Flask + psycopg2；PostgreSQL JSONB；Vitest + Pytest；Playwright（MCP）。

设计文档：`docs/superpowers/specs/2026-08-09-row-custom-actions-design.md`

## Global Constraints

- 后端测试命令必须带 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`（Windows 必需，与 `npm run test:server` 一致）。
- `dynamic_bp` 必须保持在 `app.py` 中**最后注册**，本计划不调整注册顺序。
- 前端权限判断只是 UX，**后端为权威**：`roles` 白名单与 `visibleWhen` 必须在后端复核一次。
- 所有面向用户的字符串用中文，与现有 UI 一致。
- 本期**不做批量执行**、不做行内平铺按钮、不做组合条件（and/or）、不做全局动作库。
- 本期**不新增 SSE 端点**，因此 `server/proxy.py::_is_sse()` 不需要改动。
- `page_configs` 的字段编辑锁（有数据后禁改字段）只作用于 `fields`，`rowActions` 不受该限制。
- **AI 类动作一律使用所绑 `ai_scan_tasks` 的状态配置**（`statusField` / `runningValue` / `doneValue` / `failedValue`）。行动作自己配置的这四项 **只对 webhook 类动作生效**；`responseMapping` 同理。理由：AI 的终态回写由 `ai_scan_engine.on_child_finished` 完成，它只认扫描任务的配置，若行动作再叠一套会导致行上出现一个永远卡在「执行中」、无人写终态的字段。配置 UI 在选中 AI 类型时必须隐藏这五项。
- **陈旧状态放行只适用于 webhook 类动作**。AI 类动作的幂等闸门是严格的（当前值 == `runningValue` 就一律 409），因为 AI 任务经常跑超过 5 分钟，按时间放行会给同一条记录并发出第二个子会话；AI 的孤儿恢复由既有的 `ai_scan_engine.sweep_orphans` 负责。
- 提交信息使用中文 + conventional commit 前缀，与仓库历史一致（如 `feat(row-actions): ...`）。

---

### Task 1: 后端条件求值器 + 共享测试夹具

**Files:**
- Create: `server/utils/row_action_condition.py`
- Create: `server/tests/fixtures/row_action_conditions.json`
- Test: `server/tests/test_row_action_condition.py`

**Interfaces:**
- Consumes: 无
- Produces: `evaluate(condition: dict | None, data: dict) -> bool`，供 Task 6 的 `row_action_engine` 调用；`server/tests/fixtures/row_action_conditions.json` 供 Task 2 的前端测试读取。

**背景**：这个条件对象要在前后端**各求值一次**（前端决定显不显示、后端决定准不准执行）。两套实现靠同一份夹具锁死行为，防止漂移。

- [ ] **Step 1: 写夹具文件**

创建 `server/tests/fixtures/row_action_conditions.json`：

```json
[
  { "name": "空条件视为总是通过", "condition": null, "data": { "status": "x" }, "expected": true },
  { "name": "缺 field 视为总是通过", "condition": { "operator": "eq", "value": "a" }, "data": {}, "expected": true },
  { "name": "eq 命中", "condition": { "field": "status", "operator": "eq", "value": "待审核" }, "data": { "status": "待审核" }, "expected": true },
  { "name": "eq 未命中", "condition": { "field": "status", "operator": "eq", "value": "待审核" }, "data": { "status": "已通过" }, "expected": false },
  { "name": "eq 字段缺失", "condition": { "field": "status", "operator": "eq", "value": "待审核" }, "data": {}, "expected": false },
  { "name": "eq 数字按文本比较", "condition": { "field": "n", "operator": "eq", "value": "3" }, "data": { "n": 3 }, "expected": true },
  { "name": "eq 布尔按文本比较", "condition": { "field": "b", "operator": "eq", "value": "true" }, "data": { "b": true }, "expected": true },
  { "name": "ne 命中", "condition": { "field": "status", "operator": "ne", "value": "已通过" }, "data": { "status": "待审核" }, "expected": true },
  { "name": "ne 未命中", "condition": { "field": "status", "operator": "ne", "value": "已通过" }, "data": { "status": "已通过" }, "expected": false },
  { "name": "ne 字段缺失视为空串不等", "condition": { "field": "status", "operator": "ne", "value": "已通过" }, "data": {}, "expected": true },
  { "name": "in 命中", "condition": { "field": "status", "operator": "in", "value": ["待审核", "复核中"] }, "data": { "status": "复核中" }, "expected": true },
  { "name": "in 未命中", "condition": { "field": "status", "operator": "in", "value": ["待审核"] }, "data": { "status": "已通过" }, "expected": false },
  { "name": "in 值非数组视为不通过", "condition": { "field": "status", "operator": "in", "value": "待审核" }, "data": { "status": "待审核" }, "expected": false },
  { "name": "notIn 命中", "condition": { "field": "status", "operator": "notIn", "value": ["已通过", "已驳回"] }, "data": { "status": "待审核" }, "expected": true },
  { "name": "notIn 未命中", "condition": { "field": "status", "operator": "notIn", "value": ["已通过"] }, "data": { "status": "已通过" }, "expected": false },
  { "name": "empty 字段缺失", "condition": { "field": "note", "operator": "empty" }, "data": {}, "expected": true },
  { "name": "empty 空串", "condition": { "field": "note", "operator": "empty" }, "data": { "note": "" }, "expected": true },
  { "name": "empty null", "condition": { "field": "note", "operator": "empty" }, "data": { "note": null }, "expected": true },
  { "name": "empty 有值", "condition": { "field": "note", "operator": "empty" }, "data": { "note": "x" }, "expected": false },
  { "name": "notEmpty 有值", "condition": { "field": "note", "operator": "notEmpty" }, "data": { "note": "x" }, "expected": true },
  { "name": "notEmpty 空串", "condition": { "field": "note", "operator": "notEmpty" }, "data": { "note": "" }, "expected": false },
  { "name": "未知算子失败关闭", "condition": { "field": "status", "operator": "gt", "value": "1" }, "data": { "status": "5" }, "expected": false }
]
```

- [ ] **Step 2: 写失败的测试**

创建 `server/tests/test_row_action_condition.py`：

```python
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

FIXTURE = Path(__file__).parent / 'fixtures' / 'row_action_conditions.json'
CASES = json.loads(FIXTURE.read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_evaluate_matches_fixture(case):
    from utils.row_action_condition import evaluate
    assert evaluate(case['condition'], case['data']) is case['expected']


def test_empty_dict_condition_passes():
    from utils.row_action_condition import evaluate
    assert evaluate({}, {'status': 'x'}) is True
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_row_action_condition.py -v
```

预期：全部 FAIL，`ModuleNotFoundError: No module named 'utils.row_action_condition'`

- [ ] **Step 4: 实现求值器**

创建 `server/utils/row_action_condition.py`：

```python
"""行动作可见性条件求值（后端半边）。

同一个 condition 对象会在前端 (src/utils/rowActionCondition.ts) 求值一次决定
按钮显不显示，在这里求值一次决定准不准执行。两边共读
server/tests/fixtures/row_action_conditions.json 这份夹具，防止语义漂移。

比较一律走文本：dynamic_data 的值来自 JSONB，同一个字段可能是字符串也可能是
数字/布尔，按文本比较才能和前端 String(v) 的结果对齐。
"""

_OPERATORS = ('eq', 'ne', 'in', 'notIn', 'empty', 'notEmpty')


def _as_text(v):
    """与前端 asText() 保持一致：None/缺失 -> ''，布尔 -> 'true'/'false'。"""
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return str(v)


def evaluate(condition, data):
    """条件是否满足。空条件 / 无 field 视为「总是通过」；未知算子失败关闭。"""
    if not condition:
        return True
    field = condition.get('field')
    if not field:
        return True
    op = condition.get('operator')
    if op not in _OPERATORS:
        return False

    actual = _as_text((data or {}).get(field))
    expected = condition.get('value')

    if op == 'empty':
        return actual == ''
    if op == 'notEmpty':
        return actual != ''
    if op == 'eq':
        return actual == _as_text(expected)
    if op == 'ne':
        return actual != _as_text(expected)
    if op in ('in', 'notIn'):
        if not isinstance(expected, list):
            return False
        hit = actual in [_as_text(x) for x in expected]
        return hit if op == 'in' else not hit
    return False
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_row_action_condition.py -v
```

预期：23 passed

- [ ] **Step 6: 提交**

```bash
git add server/utils/row_action_condition.py server/tests/test_row_action_condition.py server/tests/fixtures/row_action_conditions.json
git commit -m "feat(row-actions): 行动作可见性条件求值器（后端）+ 前后端共享测试夹具"
```

---

### Task 2: TS 类型定义 + 前端条件求值器

**Files:**
- Create: `src/types/rowAction.ts`
- Create: `src/utils/rowActionCondition.ts`
- Modify: `src/types/index.ts`（再导出）、`src/types/page.ts`（`PageConfig` / `PageFormData` / `createEmptyPageFormData`）、`src/types/webhook.ts`（`triggerEvent` 枚举加 `'manual'`）
- Test: `src/utils/__tests__/rowActionCondition.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `server/tests/fixtures/row_action_conditions.json`
- Produces:
  - `RowActionType`、`RowActionCondition`、`RowActionConfig`（`src/types/rowAction.ts`）
  - `evaluateRowActionCondition(condition: RowActionCondition | null | undefined, data: Record<string, unknown>): boolean`
  - `PageConfig.rowActions?: RowActionConfig[]`、`PageFormData.rowActions?: RowActionConfig[]`

- [ ] **Step 1: 写类型定义**

创建 `src/types/rowAction.ts`：

```ts
/**
 * 数据页自定义行级操作按钮的类型定义
 *
 * 配置存在 page_configs.row_actions（JSONB 列），只描述「哪个按钮、绑哪个执行器、
 * 怎么显示、怎么回写」这层薄绑定；真正的执行器（Webhook 规则 / AI 扫描任务）
 * 仍是全局资源，各有自己的管理页。
 */

import type { FieldConfig } from './field'
import type { FieldMappingRow } from './aiScanTask'

/** 动作类型：绑 Webhook 规则，或绑 AI 扫描任务 */
export type RowActionType = 'webhook' | 'aiTask'

/** 单条可见性条件。前后端各求值一次，语义由共享夹具锁定 */
export interface RowActionCondition {
  field: string
  operator: 'eq' | 'ne' | 'in' | 'notIn' | 'empty' | 'notEmpty'
  /** eq/ne 用 string；in/notIn 用 string[]；empty/notEmpty 不用 */
  value?: string | string[]
}

export interface RowActionConfig {
  /** nanoid，页面内唯一 */
  id: string
  /** 菜单文案，必填 */
  label: string
  actionType: RowActionType
  enabled: boolean

  /** actionType='webhook' 时必填 */
  webhookRuleId?: string
  /** actionType='aiTask' 时必填 */
  scanTaskId?: string

  /** 空/缺省 = 不限角色 */
  roles?: string[]
  /** 缺省 = 总是显示 */
  visibleWhen?: RowActionCondition
  /** 缺省 = 不二次确认 */
  confirmText?: string
  /** 缺省 = 不弹参数表单 */
  paramFields?: FieldConfig[]

  /** 缺省 = 不写状态 */
  statusField?: string
  runningValue?: string
  doneValue?: string
  failedValue?: string
  /** 仅 webhook 生效：把响应 JSON 按映射写回字段 */
  responseMapping?: FieldMappingRow[]
}

/** 执行接口的返回体 */
export interface RunRowActionResult {
  ok: boolean
  /** 'running' = 已写入 runningValue，前端可轮询；'submitted' = 无状态字段，不轮询 */
  status: 'running' | 'submitted'
}
```

- [ ] **Step 2: 接进现有类型**

在 `src/types/index.ts` 中加入再导出（与其他 `export * from './xxx'` 并列）：

```ts
export * from './rowAction'
```

在 `src/types/page.ts` 顶部 import 处加：

```ts
import type { RowActionConfig } from './rowAction'
```

在 `PageConfig` 接口内 `deleteBinding?: DeleteBindingConfig` 之后加一行：

```ts
  rowActions?: RowActionConfig[]
```

在 `PageFormData` 接口内 `deleteBinding?: DeleteBindingConfig` 之后加同样一行：

```ts
  rowActions?: RowActionConfig[]
```

在 `createEmptyPageFormData()` 返回对象里 `viewConfig: {},` 之后加：

```ts
    rowActions: [],
```

在 `src/types/webhook.ts` 中，把 4 处 `triggerEvent` 的联合类型全部从
`'create' | 'update' | 'delete' | 'merge'` 改为
`'create' | 'update' | 'delete' | 'merge' | 'manual'`
（`WebhookRule`、`CreateWebhookRuleRequest`、`UpdateWebhookRuleRequest` 各一处；`UpdateWebhookRuleRequest` 的是可选属性，注意保留 `?`）。

- [ ] **Step 3: 写失败的测试**

创建 `src/utils/__tests__/rowActionCondition.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { evaluateRowActionCondition } from '../rowActionCondition'
import type { RowActionCondition } from '@/types/rowAction'

/**
 * 与后端 server/tests/test_row_action_condition.py 读同一份夹具。
 * 任何一边改了求值语义而没改另一边，这里就会红。
 */
interface Case {
  name: string
  condition: RowActionCondition | null
  data: Record<string, unknown>
  expected: boolean
}

const cases: Case[] = JSON.parse(
  readFileSync(
    resolve(process.cwd(), 'server/tests/fixtures/row_action_conditions.json'),
    'utf-8',
  ),
)

describe('evaluateRowActionCondition', () => {
  it('夹具非空（防止路径写错导致空跑通过）', () => {
    expect(cases.length).toBeGreaterThan(20)
  })

  for (const c of cases) {
    it(c.name, () => {
      expect(evaluateRowActionCondition(c.condition, c.data)).toBe(c.expected)
    })
  }

  it('空对象条件视为总是通过', () => {
    expect(evaluateRowActionCondition({} as RowActionCondition, { status: 'x' })).toBe(true)
  })
})
```

- [ ] **Step 4: 运行测试确认失败**

```bash
npx vitest run src/utils/__tests__/rowActionCondition.test.ts
```

预期：FAIL，无法解析 `../rowActionCondition`

- [ ] **Step 5: 实现求值器**

创建 `src/utils/rowActionCondition.ts`：

```ts
/**
 * 行动作可见性条件求值（前端半边）
 *
 * 与 server/utils/row_action_condition.py 逐条对齐，两边共读
 * server/tests/fixtures/row_action_conditions.json 夹具。
 * 前端求值只决定按钮显不显示；能不能执行由后端复核。
 */
import type { RowActionCondition } from '@/types/rowAction'

const OPERATORS = ['eq', 'ne', 'in', 'notIn', 'empty', 'notEmpty'] as const

/** 与后端 _as_text() 一致：null/undefined -> ''，布尔 -> 'true'/'false' */
function asText(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}

export function evaluateRowActionCondition(
  condition: RowActionCondition | null | undefined,
  data: Record<string, unknown>,
): boolean {
  if (!condition) return true
  const field = condition.field
  if (!field) return true
  const op = condition.operator
  if (!OPERATORS.includes(op)) return false

  const actual = asText((data ?? {})[field])
  const expected = condition.value

  switch (op) {
    case 'empty':
      return actual === ''
    case 'notEmpty':
      return actual !== ''
    case 'eq':
      return actual === asText(expected)
    case 'ne':
      return actual !== asText(expected)
    case 'in':
    case 'notIn': {
      if (!Array.isArray(expected)) return false
      const hit = expected.map(asText).includes(actual)
      return op === 'in' ? hit : !hit
    }
    default:
      return false
  }
}
```

- [ ] **Step 6: 运行测试 + 类型检查**

```bash
npx vitest run src/utils/__tests__/rowActionCondition.test.ts
npx vue-tsc --noEmit
```

预期：vitest 24 passed；vue-tsc 无报错

- [ ] **Step 7: 提交**

```bash
git add src/types/rowAction.ts src/types/index.ts src/types/page.ts src/types/webhook.ts src/utils/rowActionCondition.ts src/utils/__tests__/rowActionCondition.test.ts
git commit -m "feat(row-actions): 行动作 TS 类型定义 + 前端条件求值器（与后端共享夹具）"
```

---

### Task 3: `page_configs.row_actions` 列 + 持久化 + 保存校验

**Files:**
- Modify: `server/init_db.py`（新增列迁移，紧跟现有 `delete_binding` 迁移之后，约 1118 行）
- Modify: `server/routes/page_configs.py`（`row_to_dict` + 3 处 SELECT 列表 + INSERT + UPDATE SET 分支 + 校验）
- Create: `server/utils/row_action_validate.py`
- Test: `server/tests/test_row_action_validate.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - DB 列 `page_configs.row_actions JSONB DEFAULT '[]'::jsonb`
  - API 字段 `rowActions`（`GET/POST/PUT /pageConfigs`）
  - `validate_row_actions(row_actions: list, fields: list, cur) -> str | None`（返回错误信息，None 表示通过），供 `routes/page_configs.py` 调用

- [ ] **Step 1: 加列迁移**

在 `server/init_db.py` 中，紧接现有 `delete_binding` 迁移块（`print("Added delete_binding column to page_configs table.")` 之后）插入：

```python
        # Migration: add row_actions column to page_configs (自定义行级操作按钮)
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'page_configs' AND column_name = 'row_actions'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE page_configs ADD COLUMN row_actions JSONB DEFAULT '[]'::jsonb")
            conn.commit()
            print("Added row_actions column to page_configs table.")
```

- [ ] **Step 2: 写失败的校验测试**

创建 `server/tests/test_row_action_validate.py`：

```python
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

FIELDS = [
    {'fieldName': 'status', 'label': '状态', 'controlType': 'select'},
    {'fieldName': 'result', 'label': '结果', 'controlType': 'text'},
]


def _cur(webhook_ids=(), scan_ids=()):
    """mock cursor：按 SQL 里出现的表名决定 fetchone 返回值。"""
    cur = MagicMock()

    def execute(sql, params=None):
        cur._last = (sql, params)

    def fetchone():
        sql, params = cur._last
        target = params[0]
        if 'webhook_rules' in sql:
            return (target,) if target in webhook_ids else None
        if 'ai_scan_tasks' in sql:
            return (target,) if target in scan_ids else None
        return None

    cur.execute.side_effect = execute
    cur.fetchone.side_effect = fetchone
    return cur


def _base(**over):
    a = {
        'id': 'ra-1', 'label': '推送外部', 'actionType': 'webhook',
        'enabled': True, 'webhookRuleId': 'wh-1',
    }
    a.update(over)
    return a


def test_valid_webhook_action_passes():
    from utils.row_action_validate import validate_row_actions
    assert validate_row_actions([_base()], FIELDS, _cur(webhook_ids=('wh-1',))) is None


def test_valid_ai_action_passes():
    from utils.row_action_validate import validate_row_actions
    a = _base(actionType='aiTask', webhookRuleId=None, scanTaskId='st-1')
    assert validate_row_actions([a], FIELDS, _cur(scan_ids=('st-1',))) is None


def test_empty_list_passes():
    from utils.row_action_validate import validate_row_actions
    assert validate_row_actions([], FIELDS, _cur()) is None


def test_missing_label_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(label='')], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and '名称' in err


def test_duplicate_id_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(), _base()], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and '唯一' in err


def test_unknown_action_type_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(actionType='sms')], FIELDS, _cur())
    assert err and '类型' in err


def test_missing_webhook_rule_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base()], FIELDS, _cur(webhook_ids=()))
    assert err and 'Webhook' in err


def test_missing_scan_task_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(actionType='aiTask', webhookRuleId=None, scanTaskId='nope')
    err = validate_row_actions([a], FIELDS, _cur(scan_ids=('st-1',)))
    assert err and '扫描任务' in err


def test_unknown_status_field_rejected():
    from utils.row_action_validate import validate_row_actions
    err = validate_row_actions([_base(statusField='ghost')], FIELDS,
                               _cur(webhook_ids=('wh-1',)))
    assert err and 'ghost' in err


def test_unknown_visible_when_field_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(visibleWhen={'field': 'ghost', 'operator': 'eq', 'value': 'x'})
    err = validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and 'ghost' in err


def test_unknown_response_mapping_column_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(responseMapping=[{'jsonKey': 'k', 'column': 'ghost', 'required': False}])
    err = validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and 'ghost' in err


def test_known_fields_accepted():
    from utils.row_action_validate import validate_row_actions
    a = _base(
        statusField='status',
        visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'},
        responseMapping=[{'jsonKey': 'k', 'column': 'result', 'required': True}],
    )
    assert validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',))) is None


def test_relation_param_field_rejected():
    from utils.row_action_validate import validate_row_actions
    a = _base(paramFields=[{'fieldName': 'p', 'label': '关联', 'controlType': 'relation'}])
    err = validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',)))
    assert err and 'relation' in err


def test_text_param_field_accepted():
    from utils.row_action_validate import validate_row_actions
    a = _base(paramFields=[{'fieldName': 'p', 'label': '原因', 'controlType': 'textarea'}])
    assert validate_row_actions([a], FIELDS, _cur(webhook_ids=('wh-1',))) is None
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_row_action_validate.py -v
```

预期：全部 FAIL，`ModuleNotFoundError: No module named 'utils.row_action_validate'`

- [ ] **Step 4: 实现校验器**

创建 `server/utils/row_action_validate.py`：

```python
"""page_configs.row_actions 的保存时校验。

前端配错（绑了不存在的规则、引用了不存在的字段）在保存时就要拦下来，
否则用户点按钮才报错，排查成本高得多。
"""

# 这些控件要落库才有语义，放在「点击时弹的参数表单」里没有意义
_FORBIDDEN_PARAM_CONTROLS = ('relation', 'reference', 'quoteSelect', 'autoSequence')


def validate_row_actions(row_actions, fields, cur):
    """校验行动作配置。通过返回 None，否则返回中文错误信息。

    Args:
        row_actions: 配置数组
        fields: 该页面的字段配置数组（用于校验字段名引用）
        cur: 数据库游标（用于校验绑定的执行器是否存在）
    """
    if row_actions is None:
        return None
    if not isinstance(row_actions, list):
        return '行操作配置必须是数组'

    known_fields = {
        f.get('fieldName') for f in (fields or []) if isinstance(f, dict)
    }
    seen_ids = set()

    for idx, a in enumerate(row_actions):
        where = f'第 {idx + 1} 个行操作'
        if not isinstance(a, dict):
            return f'{where}配置格式不正确'

        aid = a.get('id')
        if not aid:
            return f'{where}缺少 id'
        if aid in seen_ids:
            return f'{where}的 id「{aid}」重复，行操作 id 必须页面内唯一'
        seen_ids.add(aid)

        if not (a.get('label') or '').strip():
            return f'{where}的名称不能为空'

        atype = a.get('actionType')
        if atype not in ('webhook', 'aiTask'):
            return f'{where}的类型「{atype}」不合法，只能是 webhook 或 aiTask'

        if atype == 'webhook':
            rule_id = a.get('webhookRuleId')
            if not rule_id:
                return f'{where}未选择 Webhook 规则'
            cur.execute('SELECT id FROM webhook_rules WHERE id = %s', (rule_id,))
            if not cur.fetchone():
                return f'{where}绑定的 Webhook 规则「{rule_id}」不存在'
        else:
            task_id = a.get('scanTaskId')
            if not task_id:
                return f'{where}未选择 AI 扫描任务'
            cur.execute('SELECT id FROM ai_scan_tasks WHERE id = %s', (task_id,))
            if not cur.fetchone():
                return f'{where}绑定的 AI 扫描任务「{task_id}」不存在'

        # 字段名引用校验
        sf = a.get('statusField')
        if sf and sf not in known_fields:
            return f'{where}的状态字段「{sf}」不是本页面的字段'

        vw = a.get('visibleWhen') or {}
        vf = vw.get('field')
        if vf and vf not in known_fields:
            return f'{where}的显示条件字段「{vf}」不是本页面的字段'

        for m in (a.get('responseMapping') or []):
            col = (m or {}).get('column')
            if col and col not in known_fields:
                return f'{where}的响应映射目标字段「{col}」不是本页面的字段'

        for p in (a.get('paramFields') or []):
            ct = (p or {}).get('controlType')
            if ct in _FORBIDDEN_PARAM_CONTROLS:
                return f'{where}的参数表单不支持 {ct} 类型控件'

    return None
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_row_action_validate.py -v
```

预期：14 passed

- [ ] **Step 6: 把 `rowActions` 接进 page_configs 路由**

在 `server/routes/page_configs.py` 做 6 处改动：

1. 顶部 import 处加：

```python
from utils.row_action_validate import validate_row_actions
```

2. `row_to_dict`（约 22 行）在 `'deleteBinding': row[13] if len(row) > 13 else None,` 之后加：

```python
        'rowActions': row[14] if len(row) > 14 else [],
```

3. **3 处** SELECT 语句（约 46、56、200 行）的列列表末尾，把 `delete_binding FROM page_configs` 改成 `delete_binding, row_actions FROM page_configs`。三处都要改。

4. `create_page_config`（约 65 行）的 INSERT 改为带 `row_actions`：

```python
        cur.execute(
            'INSERT INTO page_configs (id, name, description, api_endpoint, fields, created_at, updated_at, row_actions) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            (config_id, body.get('name'), body.get('description'), body.get('apiEndpoint'),
             psycopg2.extras.Json(body.get('fields', [])),
             body.get('createdAt'), body.get('updatedAt'),
             psycopg2.extras.Json(body.get('rowActions') or [])),
        )
```

并在这条 INSERT **之前**插入校验（`cur` 已可用）：

```python
        err = validate_row_actions(body.get('rowActions'), body.get('fields', []), cur)
        if err:
            return jsonify({'error': err}), 400
```

5. `update_page_config` 的 SET 分支（约 186 行 `delete_binding` 之后）加：

```python
        if 'rowActions' in body:
            sets.append('row_actions=%s')
            params.append(psycopg2.extras.Json(body['rowActions']))
```

6. 在 `update_page_config` 里构建 SET 之前插入校验。`fields` 可能不在本次请求体里，缺省时从库里读当前值：

```python
        if 'rowActions' in body:
            if 'fields' in body:
                eff_fields = body['fields'] or []
            else:
                cur.execute('SELECT fields FROM page_configs WHERE id = %s', (config_id,))
                _r = cur.fetchone()
                eff_fields = (_r[0] if _r else []) or []
            err = validate_row_actions(body['rowActions'], eff_fields, cur)
            if err:
                return jsonify({'error': err}), 400
```

- [ ] **Step 7: 跑迁移并手工验证列已存在**

```bash
cd server && python init_db.py
```

预期输出含 `Added row_actions column to page_configs table.`（首次运行）。再跑一次应**不再**打印该行（幂等）。

- [ ] **Step 8: 跑全量后端测试确认没打破现有用例**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -q
```

预期：全部通过（`row_to_dict` 的 `len(row) > 14` 保护让旧的 mock 行仍能工作）

- [ ] **Step 9: 提交**

```bash
git add server/init_db.py server/routes/page_configs.py server/utils/row_action_validate.py server/tests/test_row_action_validate.py
git commit -m "feat(row-actions): page_configs 新增 row_actions 列、读写打通与保存校验"
```

---

### Task 4: Webhook `manual` 事件 + 按 id 单条触发

**Files:**
- Modify: `server/utils/webhook_engine.py`（`_build_payload` 加 `manual` 分支、新增 `fire_webhook_rule`）
- Test: `server/tests/test_webhook_manual.py`

**Interfaces:**
- Consumes: 无
- Produces: `fire_webhook_rule(rule_id, collection, record_id, row_data, operator, params, branch_id) -> dict`，返回 `_fire_single_webhook` 的结果并额外带 `responseBody`；供 Task 6 调用。

**背景**：`webhook_rules.trigger_event` 是 `VARCHAR(50)` 无 CHECK 约束，加 `'manual'` 取值**不需要 DDL**。`fire_webhooks` 按 event 精确匹配，所以 `manual` 规则永远不会被 create/update/delete 自动触发。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_webhook_manual.py`：

```python
import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# webhook_rules 查询返回的列顺序，与 fire_webhook_rule 里的 SELECT 对齐
RULE_ROW = ('wh-1', '推送外部', 'https://example.com/hook', 'sec', 10, 1)


def _db(rule_row=RULE_ROW):
    cur = MagicMock()
    cur.fetchone.return_value = rule_row
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def fake_get_db():
        yield conn

    return fake_get_db, cur


def test_build_payload_manual_carries_action_and_params():
    from utils.webhook_engine import _build_payload
    payload = _build_payload(
        'after', 'manual', 'orders', 'rec-1', None, {'status': '待审核'},
        'admin', 'wh-1', '推送外部', 'main', None,
        action_id='ra-1', action_label='推送外部', params={'reason': '缺料'},
    )
    assert payload['event'] == 'manual'
    assert payload['collection'] == 'orders'
    assert payload['recordId'] == 'rec-1'
    assert payload['record'] == {'status': '待审核'}
    assert payload['actionId'] == 'ra-1'
    assert payload['actionLabel'] == '推送外部'
    assert payload['params'] == {'reason': '缺料'}


def test_fire_webhook_rule_calls_single_webhook_with_rule_config():
    import utils.webhook_engine as we
    fake_get_db, _cur = _db()
    with patch.object(we, 'get_db', fake_get_db), \
         patch.object(we, '_fire_single_webhook',
                      return_value={'success': True, 'logId': 'l1',
                                    'responseStatus': 200, 'errorMessage': None,
                                    'retryCount': 0, 'responseBody': '{"a":1}'}) as m:
        res = we.fire_webhook_rule(
            'wh-1', 'orders', 'rec-1', {'status': '待审核'}, 'admin',
            {'reason': '缺料'}, 'main', action_id='ra-1', action_label='推送外部')
    assert res['success'] is True
    kwargs = m.call_args
    # 位置参数顺序: rule_id, rule_name, url, secret, event_type, payload, timeout, retries
    assert kwargs[0][0] == 'wh-1'
    assert kwargs[0][2] == 'https://example.com/hook'
    assert kwargs[0][4] == 'manual'
    assert kwargs[0][6] == 10
    assert kwargs[0][7] == 1


def test_fire_webhook_rule_missing_rule_raises():
    import utils.webhook_engine as we
    fake_get_db, _cur = _db(rule_row=None)
    with patch.object(we, 'get_db', fake_get_db):
        try:
            we.fire_webhook_rule('gone', 'orders', 'rec-1', {}, 'admin', {}, 'main',
                                 action_id='ra-1', action_label='x')
        except ValueError as e:
            assert 'gone' in str(e)
        else:
            raise AssertionError('should raise ValueError')


def test_manual_rules_not_matched_by_data_events():
    """fire_webhooks 按 event 精确匹配，manual 规则不会被 create 触发。"""
    import utils.webhook_engine as we
    cur = MagicMock()
    cur.fetchall.return_value = []
    we.fire_webhooks('create', 'orders', 'rec-1', None, {'a': 1}, 'admin', cur=cur)
    sql = cur.execute.call_args[0][0]
    args = cur.execute.call_args[0][1]
    assert 'trigger_event = %s' in sql
    assert args[0] == 'create'
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_webhook_manual.py -v
```

预期：FAIL（`_build_payload` 不接受 `action_id`；`fire_webhook_rule` 不存在）

- [ ] **Step 3: 给 `_build_payload` 加 manual 分支**

在 `server/utils/webhook_engine.py` 中，把 `_build_payload` 的签名末尾加三个关键字参数：

```python
def _build_payload(
    timing: str,
    event: str,
    collection: Optional[str],
    record_id: Optional[str],
    old_data: Optional[dict],
    new_data: Optional[dict],
    operator: str,
    rule_id: str,
    rule_name: str,
    branch_id: Optional[str],
    cur,
    action_id: Optional[str] = None,
    action_label: Optional[str] = None,
    params: Optional[dict] = None,
) -> dict:
```

然后把原来 `if event in ('create', 'update', 'delete') and collection:` 这一行改成把 `'manual'` 也纳入（这样 `collection` / `pageName` / `recordId` 的填充逻辑复用）：

```python
    if event in ('create', 'update', 'delete', 'manual') and collection:
```

再在该分支内部、`if timing == 'before':` **之前**插入 manual 的短路分支：

```python
        if event == 'manual':
            # 手动行动作：无 before/after 之分，直接给当前行数据 + 动作元信息
            payload['record'] = new_data
            payload['oldRecord'] = None
            payload['actionId'] = action_id
            payload['actionLabel'] = action_label
            payload['params'] = params or {}
            return payload
```

- [ ] **Step 4: 实现 `fire_webhook_rule`**

在 `server/utils/webhook_engine.py` 中 `fire_webhooks` 之后追加：

```python
def fire_webhook_rule(
    rule_id: str,
    collection: str,
    record_id: str,
    row_data: dict,
    operator: str,
    params: Optional[dict],
    branch_id: Optional[str],
    action_id: str,
    action_label: str,
) -> dict:
    """按 id 触发单条 webhook 规则（自定义行操作按钮专用）。

    与 fire_webhooks 的区别：不做 event/collection 匹配，也**忽略规则自身的
    trigger_condition** —— 触发条件由行动作的 visibleWhen 单点负责，避免两处
    条件语义打架。签名、超时、重试、webhook_logs 全部复用 _fire_single_webhook。

    Raises:
        ValueError: 规则不存在或已禁用
    """
    from db import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, webhook_url, secret, timeout, retries '
            'FROM webhook_rules WHERE id = %s AND enabled = TRUE',
            (rule_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f'Webhook 规则「{rule_id}」不存在或已禁用')
        rid, rname, url, secret, timeout, retries = row

        payload = _build_payload(
            'after', 'manual', collection, record_id, None, row_data,
            operator, rid, rname, branch_id, cur,
            action_id=action_id, action_label=action_label, params=params,
        )

    return _fire_single_webhook(
        rid, rname, url, secret, 'manual', payload,
        timeout or 30, retries or 0,
    )
```

- [ ] **Step 5: 让 `_fire_single_webhook` 回传响应体**

`fire_webhook_rule` 的调用方（Task 6）需要响应 JSON 才能做 `responseMapping` 回写，而现在返回的 dict 里没有 `responseBody`。在 `_fire_single_webhook` 的 return 字典里补一个键（约 325 行）：

```python
    return {
        'success': success,
        'logId': log_id,
        'responseStatus': response_status,
        'responseBody': response_body,
        'errorMessage': error_message,
        'retryCount': retry_count,
    }
```

（新增键不会破坏现有调用方——它们只读已有的键。）

- [ ] **Step 6: 运行测试确认通过**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_webhook_manual.py tests/test_webhook_rules.py -v
```

预期：新测试全绿；现有 webhook 测试不回归（若 `tests/test_webhook_rules.py` 不存在则改跑 `python -m pytest tests/ -q -k webhook`）

- [ ] **Step 7: 提交**

```bash
git add server/utils/webhook_engine.py server/tests/test_webhook_manual.py
git commit -m "feat(row-actions): webhook 支持 manual 事件与按 id 单条触发"
```

---

### Task 5: `ai_scan_engine.claim_one` —— 单行认领

**Files:**
- Modify: `server/utils/ai_scan_engine.py`（新增 `claim_one`）
- Test: `server/tests/test_ai_scan_claim_one.py`

**Interfaces:**
- Consumes: 无
- Produces: `claim_one(task: dict, record_id: str) -> dict | None`，返回 `{'id', 'data'}` 或 `None`（行不存在/被锁）。`task` 必须是 **camelCase 形状**（`ai_scan_repo.get_task()` 的返回值：`collection` / `branchId` / `statusField` / `runningValue` / …）。

**⚠️ 实现地雷**：`ai_scan_engine` 内存在两种 task dict 形状 —— `_load_task()` 返回 snake_case（`status_field`），而 `build_context_dir` / `claim_records` 用 camelCase（`statusField`）。`claim_one` 必须与 `build_context_dir` 保持同一形状（camelCase），否则会静默 `KeyError`。

**与 `claim_records` 的唯一语义差别**：`claim_one` **不带 `pendingValue` 谓词** —— 手动重跑必须能对「已处理」的行再来一次。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_ai_scan_claim_one.py`：

```python
import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

TASK = {
    'id': 'st-1', 'collection': 'orders', 'branchId': 'main',
    'statusField': 'aiStatus', 'pendingValue': '待处理',
    'runningValue': '处理中', 'doneValue': '已处理', 'failedValue': '处理失败',
    'maxRecordsPerScan': 20, 'fieldMapping': [],
}


def _db(returning=('rec-1', {'a': 1})):
    cur = MagicMock()
    cur.fetchone.return_value = returning
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None

    @contextmanager
    def fake_get_db():
        yield conn

    return fake_get_db, cur


def test_claim_one_returns_record_and_flips_status():
    import utils.ai_scan_engine as eng
    fake_get_db, cur = _db()
    with patch.object(eng, 'get_db', fake_get_db):
        rec = eng.claim_one(TASK, 'rec-1')
    assert rec == {'id': 'rec-1', 'data': {'a': 1}}
    sql, params = cur.execute.call_args[0]
    assert 'FOR UPDATE SKIP LOCKED' in sql
    assert '处理中' in params
    assert 'rec-1' in params


def test_claim_one_ignores_pending_value_predicate():
    """手动重跑必须能对「已处理」的行再来一次，所以 SQL 里不能有状态谓词。"""
    import utils.ai_scan_engine as eng
    fake_get_db, cur = _db()
    with patch.object(eng, 'get_db', fake_get_db):
        eng.claim_one(TASK, 'rec-1')
    sql, params = cur.execute.call_args[0]
    assert '待处理' not in params


def test_claim_one_returns_none_when_no_row():
    import utils.ai_scan_engine as eng
    fake_get_db, _cur = _db(returning=None)
    with patch.object(eng, 'get_db', fake_get_db):
        assert eng.claim_one(TASK, 'gone') is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_claim_one.py -v
```

预期：FAIL，`AttributeError: module 'utils.ai_scan_engine' has no attribute 'claim_one'`

- [ ] **Step 3: 实现 `claim_one`**

在 `server/utils/ai_scan_engine.py` 的 `claim_records` 之后追加：

```python
def claim_one(task, record_id):
    """认领单条记录并翻到 running_value，供自定义行操作按钮手动触发。

    与 claim_records 的唯一语义差别：**不带 pending_value 谓词**。定时扫描只挑
    「待处理」的行，而手动重跑必须能对已经「已处理」的行再来一次。

    `task` 必须是 camelCase 形状（ai_scan_repo.get_task() 的返回值），与
    build_context_dir 保持一致。

    Returns: {'id', 'data'} 或 None（行不存在 / 已被其他事务锁住）
    """
    sql = (
        "WITH picked AS ("
        "  SELECT d.id FROM dynamic_data d "
        "   WHERE d.id = %s AND d.collection = %s AND d.branch_id = %s "
        "   FOR UPDATE SKIP LOCKED) "
        "UPDATE dynamic_data d SET data = jsonb_set(d.data, ARRAY[%s], to_jsonb(%s::text)), "
        "  updated_at = now(), version = d.version + 1 "
        "FROM picked WHERE d.id = picked.id AND d.branch_id = %s "
        "RETURNING d.id, d.data"
    )
    params = [record_id, task['collection'], task['branchId'],
              task['statusField'], task['runningValue'], task['branchId']]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
    return {'id': row[0], 'data': row[1]} if row else None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_scan_claim_one.py tests/test_ai_scan_engine.py -v
```

预期：新测试 3 passed；现有 `test_ai_scan_engine.py` 不回归

- [ ] **Step 5: 提交**

```bash
git add server/utils/ai_scan_engine.py server/tests/test_ai_scan_claim_one.py
git commit -m "feat(row-actions): ai_scan_engine 新增 claim_one 单行认领（不带待处理谓词）"
```

---

### Task 6: `row_action_engine` —— 编排、闸门与回写

**Files:**
- Create: `server/utils/row_action_engine.py`
- Test: `server/tests/test_row_action_engine.py`

**Interfaces:**
- Consumes: Task 1 的 `row_action_condition.evaluate`；Task 4 的 `webhook_engine.fire_webhook_rule`；Task 5 的 `ai_scan_engine.claim_one`；既有的 `ai_scan_repo.get_task`、`ai_scan_engine.build_context_dir` / `assemble_prompt`、`batch_repo.create_batch`
- Produces:
  - `RowActionError(message, http_status)` 异常类
  - `run_action(collection, record_id, action, row_data, role, operator, branch_id, params) -> str`，返回 `'running'` 或 `'submitted'`
  - `is_visible(action, role, is_superuser, row_data) -> bool`
  - `write_status(collection, record_id, branch_id, field, value)`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_row_action_engine.py`：

```python
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _action(**over):
    a = {
        'id': 'ra-1', 'label': '推送外部', 'actionType': 'webhook',
        'enabled': True, 'webhookRuleId': 'wh-1',
        'statusField': 'syncStatus', 'runningValue': '同步中',
        'doneValue': '已同步', 'failedValue': '同步失败',
    }
    a.update(over)
    return a


# ---------- is_visible ----------

def test_visible_when_no_roles_and_no_condition():
    from utils.row_action_engine import is_visible
    assert is_visible(_action(), 'developer', False, {}) is True


def test_hidden_when_disabled():
    from utils.row_action_engine import is_visible
    assert is_visible(_action(enabled=False), 'developer', False, {}) is False


def test_hidden_when_role_not_whitelisted():
    from utils.row_action_engine import is_visible
    a = _action(roles=['admin'])
    assert is_visible(a, 'developer', False, {}) is False


def test_visible_when_role_whitelisted():
    from utils.row_action_engine import is_visible
    a = _action(roles=['developer', 'admin'])
    assert is_visible(a, 'developer', False, {}) is True


def test_superuser_bypasses_role_whitelist():
    from utils.row_action_engine import is_visible
    a = _action(roles=['nobody'])
    assert is_visible(a, 'admin', True, {}) is True


def test_hidden_when_condition_not_met():
    from utils.row_action_engine import is_visible
    a = _action(visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'})
    assert is_visible(a, 'developer', False, {'status': '已通过'}) is False


def test_visible_when_condition_met():
    from utils.row_action_engine import is_visible
    a = _action(visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'})
    assert is_visible(a, 'developer', False, {'status': '待审核'}) is True


# ---------- 幂等闸门 ----------

def test_running_row_is_rejected():
    import utils.row_action_engine as eng
    with patch.object(eng, 'write_status') as ws, \
         patch.object(eng, '_row_updated_at', return_value=datetime.now(timezone.utc)):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _action(), {'syncStatus': '同步中'},
                           'admin', 'admin', 'main', {})
    assert ei.value.http_status == 409
    ws.assert_not_called()


def test_stale_running_row_is_allowed_through():
    """webhook 动作：卡住超过阈值的「同步中」视为陈旧，允许重新触发。"""
    import utils.row_action_engine as eng
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    with patch.object(eng, 'write_status'), \
         patch.object(eng, '_row_updated_at', return_value=stale), \
         patch.object(eng, '_rule_stale_after_seconds', return_value=300), \
         patch.object(eng, '_spawn') as spawn:
        status = eng.run_action('orders', 'rec-1', _action(),
                                {'syncStatus': '同步中'}, 'admin', 'admin', 'main', {})
    assert status == 'running'
    spawn.assert_called_once()


# ---------- AI 动作用扫描任务的状态配置 ----------

AI_TASK = {
    'id': 'st-1', 'collection': 'orders', 'branchId': 'main',
    'statusField': 'aiStatus', 'runningValue': '处理中', 'doneValue': '已处理',
    'failedValue': '处理失败', 'ownerUserId': 'u1', 'name': '审核',
    'promptTemplate': 'p', 'fieldMapping': [], 'agent': None,
}


def _ai_action(**over):
    a = {
        'id': 'ra-2', 'label': 'AI 审核', 'actionType': 'aiTask',
        'enabled': True, 'scanTaskId': 'st-1',
        # 这四项对 AI 动作应当被忽略
        'statusField': 'syncStatus', 'runningValue': '同步中',
        'doneValue': '已同步', 'failedValue': '同步失败',
    }
    a.update(over)
    return a


def test_ai_action_gate_uses_scan_task_status_not_action_status():
    """行动作自己的 statusField 对 AI 无效；闸门看扫描任务的 aiStatus。"""
    import utils.row_action_engine as eng
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
         patch.object(eng, '_run_ai'):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _ai_action(),
                           {'aiStatus': '处理中'}, 'admin', 'admin', 'main', {})
    assert ei.value.http_status == 409


def test_ai_action_ignores_action_status_field_when_gating():
    """行动作的 syncStatus=同步中 不该拦住 AI 动作。"""
    import utils.row_action_engine as eng
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
         patch.object(eng, 'write_status') as ws, \
         patch.object(eng, '_run_ai') as ra:
        status = eng.run_action('orders', 'rec-1', _ai_action(),
                                {'syncStatus': '同步中'}, 'admin', 'admin', 'main', {})
    assert status == 'running'
    ra.assert_called_once()
    # AI 分支的「执行中」由 claim_one 原子写入，run_action 不得自己写
    ws.assert_not_called()


def test_ai_action_gate_is_strict_no_stale_bypass():
    """AI 任务常跑超过 5 分钟，陈旧放行只对 webhook 生效。"""
    import utils.row_action_engine as eng
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    with patch.object(eng, 'get_task', return_value=AI_TASK), \
         patch.object(eng, '_row_updated_at', return_value=stale), \
         patch.object(eng, '_run_ai'):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _ai_action(),
                           {'aiStatus': '处理中'}, 'admin', 'admin', 'main', {})
    assert ei.value.http_status == 409


def test_ai_action_missing_task_raises_400_before_gate():
    import utils.row_action_engine as eng
    with patch.object(eng, 'get_task', return_value=None):
        with pytest.raises(eng.RowActionError) as ei:
            eng.run_action('orders', 'rec-1', _ai_action(), {}, 'admin', 'admin',
                           'main', {})
    assert ei.value.http_status == 400


def test_no_status_field_means_no_gate_and_submitted():
    import utils.row_action_engine as eng
    a = _action(statusField=None, runningValue=None, doneValue=None, failedValue=None)
    with patch.object(eng, 'write_status') as ws, patch.object(eng, '_spawn'):
        status = eng.run_action('orders', 'rec-1', a, {}, 'admin', 'admin', 'main', {})
    assert status == 'submitted'
    ws.assert_not_called()


# ---------- 后端复核 ----------

def test_run_rejects_when_condition_not_met():
    import utils.row_action_engine as eng
    a = _action(visibleWhen={'field': 'status', 'operator': 'eq', 'value': '待审核'})
    with pytest.raises(eng.RowActionError) as ei:
        eng.run_action('orders', 'rec-1', a, {'status': '已通过'},
                       'developer', 'dev', 'main', {})
    assert ei.value.http_status == 409


def test_run_rejects_when_role_not_whitelisted():
    import utils.row_action_engine as eng
    a = _action(roles=['admin'])
    with pytest.raises(eng.RowActionError) as ei:
        eng.run_action('orders', 'rec-1', a, {}, 'developer', 'dev', 'main', {})
    assert ei.value.http_status == 403


# ---------- webhook 执行 ----------

def test_webhook_success_writes_done_and_mapped_columns():
    import utils.row_action_engine as eng
    a = _action(responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': False}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '{"code": "X9"}'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main',
                               {'extCode': 'X9', 'syncStatus': '已同步'})


def test_webhook_success_without_mapping_writes_only_status():
    import utils.row_action_engine as eng
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': 'OK'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', _action(), {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '已同步'})


def test_webhook_failure_writes_failed_value():
    import utils.row_action_engine as eng
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': False, 'responseBody': None}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', _action(), {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '同步失败'})


def test_webhook_exception_writes_failed_value():
    import utils.row_action_engine as eng
    with patch.object(eng, 'fire_webhook_rule', side_effect=RuntimeError('boom')), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', _action(), {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '同步失败'})


def test_webhook_unparseable_response_still_writes_done():
    """响应不是 JSON 时不当作失败，只是没有可映射的值。"""
    import utils.row_action_engine as eng
    a = _action(responseMapping=[{'jsonKey': 'code', 'column': 'extCode', 'required': False}])
    with patch.object(eng, 'fire_webhook_rule',
                      return_value={'success': True, 'responseBody': '<html>ok</html>'}), \
         patch.object(eng, 'write_back') as wb:
        eng._run_webhook('orders', 'rec-1', a, {}, 'admin', 'main', {})
    wb.assert_called_once_with('orders', 'rec-1', 'main', {'syncStatus': '已同步'})


# ---------- AI 执行 ----------

def test_ai_claim_miss_raises_409():
    import utils.row_action_engine as eng
    a = _ai_action()
    with patch.object(eng, 'claim_one', return_value=None):
        with pytest.raises(eng.RowActionError) as ei:
            eng._run_ai('rec-1', a, AI_TASK, {})
    assert ei.value.http_status == 409


def test_ai_creates_single_child_batch():
    import utils.row_action_engine as eng
    a = _ai_action()
    task = dict(AI_TASK, agent='build')
    with patch.object(eng, 'claim_one', return_value={'id': 'rec-1', 'data': {}}), \
         patch.object(eng, 'build_context_dir', return_value='scan-staging/st-1/rec-1'), \
         patch.object(eng, 'assemble_prompt', return_value='PROMPT'), \
         patch.object(eng, 'append_params_to_context') as ap, \
         patch.object(eng, 'create_batch') as cb:
        eng._run_ai('rec-1', a, task, {'reason': '缺料'})
    ap.assert_called_once_with('scan-staging/st-1/rec-1', {'reason': '缺料'})
    kwargs = cb.call_args[1]
    assert kwargs['files'] == [{'name': 'rec-1', 'path': 'scan-staging/st-1/rec-1',
                                'recordId': 'rec-1'}]
    assert kwargs['scan_task_id'] == 'st-1'
    assert kwargs['agent'] == 'build'
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_row_action_engine.py -v
```

预期：全部 FAIL，`ModuleNotFoundError: No module named 'utils.row_action_engine'`

- [ ] **Step 3: 实现引擎**

创建 `server/utils/row_action_engine.py`：

```python
"""自定义行级操作按钮的执行编排。

本模块只做编排，不自己实现执行器：
  - webhook 分支 -> webhook_engine.fire_webhook_rule（HMAC/超时/重试/日志全复用）
  - AI 分支      -> ai_scan_engine.claim_one + build_context_dir + batch_repo.create_batch
                     （后续回写由 ai_scan_engine.on_child_finished 完成，不经过这里）

执行痕迹刻意不建表：webhook 的审计是 webhook_logs，AI 的审计是子会话完整对话。
行上的状态字段回答「现在怎么样」，那两处回答「当时发生了什么」。
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from db import get_db
from utils.row_action_condition import evaluate
from utils.webhook_engine import fire_webhook_rule
from utils.ai_scan_engine import (
    claim_one, build_context_dir, assemble_prompt, _workspace_root,
)
from utils.ai_scan_repo import get_task
from utils.batch_repo import create_batch

logger = logging.getLogger(__name__)

# 卡住的 runningValue 超过这个秒数就视为陈旧，允许重新触发（进程崩溃兜底）
MIN_STALE_SECONDS = 300


class RowActionError(Exception):
    """带 HTTP 状态码的行动作错误，由路由层翻译成响应。"""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


# ==================== 可见性 ====================

def is_visible(action, role, is_superuser, row_data):
    """按钮对该角色 + 该行是否可见。前端与后端共用同一判断。"""
    if not action.get('enabled', True):
        return False
    roles = action.get('roles') or []
    if roles and not is_superuser and role not in roles:
        return False
    return evaluate(action.get('visibleWhen'), row_data or {})


# ==================== 行读写 ====================

def _row_updated_at(collection, record_id, branch_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT updated_at FROM dynamic_data '
            'WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, record_id, branch_id),
        )
        row = cur.fetchone()
    return row[0] if row else None


def write_back(collection, record_id, branch_id, values):
    """把 {字段名: 值} 一次性写回该行（嵌套 jsonb_set，参数化）。"""
    if not values:
        return 0
    expr = 'data'
    params = []
    for col, val in values.items():
        expr = f'jsonb_set({expr}, ARRAY[%s], to_jsonb(%s::text))'
        params.extend([col, '' if val is None else str(val)])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'UPDATE dynamic_data SET data = {expr}, updated_at = now(), '
                'version = version + 1 '
                'WHERE id = %s AND collection = %s AND branch_id = %s',
                params + [record_id, collection, branch_id],
            )
            n = cur.rowcount
        conn.commit()
    return n


def write_status(collection, record_id, branch_id, field, value):
    return write_back(collection, record_id, branch_id, {field: value})


# ==================== 陈旧阈值 ====================

def _rule_stale_after_seconds(action):
    """webhook 动作的陈旧阈值 = timeout × (retries + 1)，下限 MIN_STALE_SECONDS。

    AI 动作不走这条（它由 ai_scan_engine.sweep_orphans 兜底），直接返回下限。
    """
    if action.get('actionType') != 'webhook':
        return MIN_STALE_SECONDS
    rule_id = action.get('webhookRuleId')
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT timeout, retries FROM webhook_rules WHERE id = %s',
                        (rule_id,))
            row = cur.fetchone()
        if row:
            timeout, retries = (row[0] or 30), (row[1] or 0)
            return max(MIN_STALE_SECONDS, timeout * (retries + 1))
    except Exception:
        logger.exception('读取 webhook 规则超时配置失败: %s', rule_id)
    return MIN_STALE_SECONDS


def _is_stale(collection, record_id, branch_id, action):
    ts = _row_updated_at(collection, record_id, branch_id)
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age > _rule_stale_after_seconds(action)


# ==================== 主入口 ====================

def _spawn(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


def run_action(collection, record_id, action, row_data, role, operator,
               branch_id, params):
    """校验 → 写「执行中」→ 分派。返回 'running' 或 'submitted'。

    状态字段的归属：
      - webhook 动作用行动作自己配置的 statusField / 三个状态值
      - AI 动作**一律**用所绑扫描任务的配置。终态回写由
        ai_scan_engine.on_child_finished 完成，它只认扫描任务的配置；行动作
        再叠一套只会在行上留下一个永远卡在「执行中」、无人写终态的字段。

    Raises: RowActionError
    """
    # 1. 后端复核（前端隐藏不算数）
    if not action.get('enabled', True):
        raise RowActionError('该行操作已停用', 400)
    roles = action.get('roles') or []
    is_superuser = (role == 'admin')
    if roles and not is_superuser and role not in roles:
        raise RowActionError('权限不足', 403)
    if not evaluate(action.get('visibleWhen'), row_data or {}):
        raise RowActionError('当前记录不满足该动作的执行条件', 409)

    is_ai = action.get('actionType') == 'aiTask'
    task = None
    if is_ai:
        task = get_task(action.get('scanTaskId'))
        if not task:
            raise RowActionError('动作绑定的执行器已不存在', 400)
        status_field = task.get('statusField')
        running = task.get('runningValue')
    else:
        status_field = action.get('statusField')
        running = action.get('runningValue')

    # 2. 幂等闸门
    #    webhook：陈旧的 runningValue 放行，兜底进程崩溃留下的孤儿行
    #    AI：严格拦截，不看时间 —— AI 常跑超过 5 分钟，按时间放行会给同一条
    #        记录并发出第二个子会话；孤儿由 ai_scan_engine.sweep_orphans 恢复
    if status_field and running:
        current = (row_data or {}).get(status_field)
        if current == running:
            if is_ai or not _is_stale(collection, record_id, branch_id, action):
                raise RowActionError('该行有正在执行的动作，请稍后再试', 409)
        if not is_ai:
            # AI 分支的「执行中」由 claim_one 原子写入，这里不能抢先写，
            # 否则 claim_one 之前的窗口里状态已变但记录尚未真正认领
            write_status(collection, record_id, branch_id, status_field, running)

    # 3. 分派
    if is_ai:
        # 同步执行到「创建批任务」为止（很快），失败要能立刻告诉用户
        _run_ai(record_id, action, task, params)
    else:
        _spawn(_run_webhook, collection, record_id, action, row_data,
               operator, branch_id, params)

    return 'running' if (status_field and running) else 'submitted'


# ==================== webhook 分支 ====================

def _run_webhook(collection, record_id, action, row_data, operator, branch_id, params):
    """后台线程执行。finally 保证落一个终态。"""
    status_field = action.get('statusField')
    values = {}
    ok = False
    try:
        res = fire_webhook_rule(
            action['webhookRuleId'], collection, record_id, row_data or {},
            operator, params or {}, branch_id,
            action_id=action['id'], action_label=action.get('label') or '',
        )
        ok = bool(res.get('success'))
        if ok:
            values.update(_map_response(res.get('responseBody'),
                                        action.get('responseMapping') or []))
    except Exception:
        logger.exception('行操作 webhook 执行失败: action=%s record=%s',
                         action.get('id'), record_id)
        ok = False
    finally:
        if status_field:
            values[status_field] = (action.get('doneValue') if ok
                                    else action.get('failedValue'))
        if values:
            try:
                write_back(collection, record_id, branch_id, values)
            except Exception:
                logger.exception('行操作结果回写失败: record=%s', record_id)


def _map_response(body, mapping):
    """按 responseMapping 从响应 JSON 取值。解析不了就返回空 dict（不算失败）。"""
    if not mapping or not body:
        return {}
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out = {}
    for m in mapping:
        key, col = m.get('jsonKey'), m.get('column')
        if key and col and key in parsed:
            out[col] = parsed[key]
    return out


# ==================== AI 分支 ====================

def append_params_to_context(rel_path, params):
    """把参数表单的值以「## 本次执行参数」段落追加进 record.md。

    prompt 是 batch 级共享的，params 是单行的；写进上下文文件语义更正确，
    而且 assemble_prompt 不用动。
    """
    if not params:
        return
    md = Path(_workspace_root()) / rel_path / 'record.md'
    if not md.exists():
        return
    lines = ['', '## 本次执行参数', '']
    for k, v in params.items():
        lines.append(f'- {k}: {v}')
    with md.open('a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def _run_ai(record_id, action, task, params):
    """认领该行 → 铺上下文目录 → 建单子会话批任务。

    `task` 由 run_action 预先取好（闸门也要用它的状态配置），不在这里重复取。
    collection / branch_id 都以 task 里的为准 —— 扫描任务本来就绑定了集合与分支。
    """
    rec = claim_one(task, record_id)
    if not rec:
        raise RowActionError('记录不存在或正被其他操作占用', 409)

    rel = build_context_dir(task, rec)
    append_params_to_context(rel, params)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    create_batch(
        task['ownerUserId'],
        name=f"行操作·{action.get('label')}·{stamp}",
        prompt=assemble_prompt(task),
        template_id=None,
        files=[{'name': record_id, 'path': rel, 'recordId': record_id}],
        scan_task_id=task['id'],
        agent=task.get('agent') or None,
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_row_action_engine.py -v
```

预期：19 passed

- [ ] **Step 5: 提交**

```bash
git add server/utils/row_action_engine.py server/tests/test_row_action_engine.py
git commit -m "feat(row-actions): 行动作执行引擎（校验/幂等闸门/webhook 与 AI 分派/回写）"
```

---

### Task 7: REST 路由

**Files:**
- Modify: `server/routes/dynamic.py`（新增路由 + import）
- Test: `server/tests/test_row_action_routes.py`

**Interfaces:**
- Consumes: Task 6 的 `row_action_engine.run_action` / `RowActionError`；既有的 `require_page_action`、`_get_current_user_branch`、`log_operation`
- Produces: `POST /<collection>/<record_id>/row-actions/<action_id>/run`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_row_action_routes.py`：

```python
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

ACTION = {
    'id': 'ra-1', 'label': '推送外部', 'actionType': 'webhook',
    'enabled': True, 'webhookRuleId': 'wh-1',
    'statusField': 'syncStatus', 'runningValue': '同步中',
    'doneValue': '已同步', 'failedValue': '同步失败',
}

URL = '/orders/rec-1/row-actions/ra-1/run'


def _patch_lookup(row_actions=(ACTION,), row_data=None):
    """patch 掉路由内部的配置/行数据读取。"""
    return patch('routes.dynamic._load_row_action_context',
                 return_value=(list(row_actions),
                               {'status': '待审核'} if row_data is None else row_data))


def test_requires_auth(client):
    resp = client.post(URL, json={})
    assert resp.status_code == 401


def test_guest_is_rejected(client, dev_token):
    from auth import create_token
    guest = create_token({'id': 'u-g', 'username': 'g', 'role': 'guest'})
    resp = client.post(URL, headers={'Authorization': f'Bearer {guest}'}, json={})
    assert resp.status_code == 403


def test_unknown_action_404(client, admin_headers):
    with _patch_lookup(row_actions=()):
        resp = client.post(URL, headers=admin_headers, json={})
    assert resp.status_code == 404


def test_record_not_found_404(client, admin_headers):
    with patch('routes.dynamic._load_row_action_context', return_value=([ACTION], None)):
        resp = client.post(URL, headers=admin_headers, json={})
    assert resp.status_code == 404


def test_happy_path_returns_running(client, admin_headers):
    with _patch_lookup(), \
         patch('routes.dynamic.run_action', return_value='running') as ra, \
         patch('routes.dynamic.log_operation'):
        resp = client.post(URL, headers=admin_headers, json={'params': {'reason': 'x'}})
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'status': 'running'}
    assert ra.call_args[0][0] == 'orders'
    assert ra.call_args[0][1] == 'rec-1'
    assert ra.call_args[1]['params'] == {'reason': 'x'}


def test_engine_error_is_translated_to_its_status(client, admin_headers):
    from utils.row_action_engine import RowActionError
    with _patch_lookup(), \
         patch('routes.dynamic.run_action',
               side_effect=RowActionError('该行有正在执行的动作，请稍后再试', 409)):
        resp = client.post(URL, headers=admin_headers, json={})
    assert resp.status_code == 409
    assert '正在执行' in resp.get_json()['error']


def test_operation_log_written_on_success(client, admin_headers):
    with _patch_lookup(), \
         patch('routes.dynamic.run_action', return_value='running'), \
         patch('routes.dynamic.log_operation') as lo:
        client.post(URL, headers=admin_headers, json={})
    assert lo.call_args[0][1] == 'row_action'
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_row_action_routes.py -v
```

预期：FAIL（404 —— 路由尚不存在）

- [ ] **Step 3: 实现路由**

在 `server/routes/dynamic.py` 顶部 import 区加：

```python
from utils.row_action_engine import run_action, RowActionError
```

在文件末尾（或紧随 `batch-delete` 路由之后）追加：

```python
def _load_row_action_context(collection, record_id, branch_id):
    """一次性取出该页面的行操作配置和该行数据。

    抽成独立函数是为了让路由测试能整体 patch 掉 DB 访问。
    Returns: (row_actions: list, row_data: dict | None)
    """
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT row_actions FROM page_configs WHERE id = %s', (page_id,))
        row = cur.fetchone()
        row_actions = (row[0] if row else None) or []
        cur.execute(
            'SELECT data FROM dynamic_data '
            'WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, record_id, branch_id),
        )
        rec = cur.fetchone()
    return row_actions, (rec[0] if rec else None)


@dynamic_bp.route('/<collection>/<record_id>/row-actions/<action_id>/run', methods=['POST'])
@write_required
def run_row_action(collection, record_id, action_id):
    """触发一个自定义行级操作按钮。

    行动作会改这一行的数据，所以权限等同于 update。实际执行是异步的，
    这里立即返回；结果通过行上的状态字段回写体现。
    """
    if collection in RESERVED:
        return jsonify({'error': 'Not found'}), 404
    denied = require_page_action(collection, 'update')
    if denied:
        return denied

    branch_id = _get_current_user_branch(collection)
    row_actions, row_data = _load_row_action_context(collection, record_id, branch_id)

    action = next((a for a in row_actions if a.get('id') == action_id), None)
    if not action:
        return jsonify({'error': '行操作不存在'}), 404
    if row_data is None:
        return jsonify({'error': '记录不存在'}), 404

    body = request.get_json(silent=True) or {}
    user = getattr(flask_g, 'current_user', {}) or {}

    try:
        status = run_action(
            collection, record_id, action, row_data,
            role=user.get('role'),
            operator=user.get('username') or user.get('userId') or '',
            branch_id=branch_id,
            params=body.get('params') or {},
        )
    except RowActionError as e:
        return jsonify({'error': e.message}), e.http_status

    log_operation('update', 'row_action', record_id, action.get('label'),
                  f'执行行操作「{action.get("label")}」（记录 {record_id}）',
                  branch_id=branch_id)
    return jsonify({'ok': True, 'status': status})
```

> 注意：`run_action` 的前 4 个参数按位置传（测试断言 `call_args[0][0]` / `[0][1]`），其余按关键字传。

- [ ] **Step 4: 运行测试确认通过**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_row_action_routes.py -v
```

预期：7 passed

- [ ] **Step 5: 跑全量后端测试确认路由未遮挡现有端点**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -q
```

预期：全部通过。特别确认 `test_dynamic_page_permissions.py` 无回归（新路由是 4 段，不会被 `/<collection>/<item_id>` 抢走，反之亦然）。

- [ ] **Step 6: 提交**

```bash
git add server/routes/dynamic.py server/tests/test_row_action_routes.py
git commit -m "feat(row-actions): 新增行操作触发端点 POST /<collection>/<id>/row-actions/<aid>/run"
```

---

### Task 8: 前端 API 层 + `RowActionRunner` + `DynamicPage` 接线

**Files:**
- Create: `src/api/rowAction.ts`
- Create: `src/components/dynamic/RowActionRunner.vue`
- Modify: `src/views/dynamic/DynamicPage.vue`（`extra-actions` 插槽约 270 行 + script 区）
- Test: `src/components/dynamic/__tests__/RowActionRunner.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `RowActionConfig` / `RunRowActionResult` / `evaluateRowActionCondition`；Task 7 的 REST 端点
- Produces:
  - `runRowAction(collection, recordId, actionId, params): Promise<RunRowActionResult>`
  - `RowActionRunner` 组件，暴露 `run(action: RowActionConfig, row: Record<string, unknown>)` 方法（`defineExpose`），`emit('refresh')` 通知父组件刷新列表

- [ ] **Step 1: 写 API 层**

创建 `src/api/rowAction.ts`：

```ts
/**
 * 自定义行级操作按钮的 API
 */
import request from './request'
import type { RunRowActionResult } from '@/types/rowAction'

/** 触发一个行操作。立即返回，实际执行在后端异步进行 */
export function runRowAction(
  collection: string,
  recordId: string,
  actionId: string,
  params: Record<string, unknown> = {},
): Promise<RunRowActionResult> {
  return request.post(
    `/${collection}/${recordId}/row-actions/${actionId}/run`,
    { params },
  )
}
```

> 若 `src/api/request.ts` 的默认导出名不是 `request`，照该目录里其他文件（如 `src/api/aiChat.ts`）的写法对齐 import 与响应解包方式。

- [ ] **Step 2: 写失败的组件测试**

创建 `src/components/dynamic/__tests__/RowActionRunner.test.ts`：

```ts
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import RowActionRunner from '../RowActionRunner.vue'
import type { RowActionConfig } from '@/types/rowAction'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

const runRowAction = vi.fn()
vi.mock('@/api/rowAction', () => ({
  runRowAction: (...args: unknown[]) => runRowAction(...args),
}))

const confirmMock = vi.fn()
vi.mock('element-plus', async (orig) => {
  const actual = await (orig() as Promise<Record<string, unknown>>)
  return {
    ...actual,
    ElMessageBox: { confirm: (...a: unknown[]) => confirmMock(...a) },
    ElMessage: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  }
})

function makeAction(over: Partial<RowActionConfig> = {}): RowActionConfig {
  return {
    id: 'ra-1',
    label: '推送外部',
    actionType: 'webhook',
    enabled: true,
    webhookRuleId: 'wh-1',
    statusField: 'syncStatus',
    runningValue: '同步中',
    doneValue: '已同步',
    failedValue: '同步失败',
    ...over,
  }
}

function mountRunner() {
  return mount(RowActionRunner, {
    props: { collection: 'orders', fields: [] },
    global: { stubs: { teleport: true } },
  })
}

describe('RowActionRunner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
    runRowAction.mockResolvedValue({ ok: true, status: 'submitted' })
    confirmMock.mockResolvedValue('confirm')
  })

  it('无 confirmText、无 paramFields 时直接提交', async () => {
    const w = mountRunner()
    await (w.vm as any).run(makeAction(), { id: 'rec-1' })
    await flushPromises()
    expect(confirmMock).not.toHaveBeenCalled()
    expect(runRowAction).toHaveBeenCalledWith('orders', 'rec-1', 'ra-1', {})
  })

  it('配了 confirmText 时先弹确认', async () => {
    const w = mountRunner()
    await (w.vm as any).run(makeAction({ confirmText: '确定要推送吗？' }), { id: 'rec-1' })
    await flushPromises()
    expect(confirmMock).toHaveBeenCalled()
    expect(runRowAction).toHaveBeenCalled()
  })

  it('取消确认时不提交', async () => {
    confirmMock.mockRejectedValue(new Error('cancel'))
    const w = mountRunner()
    await (w.vm as any).run(makeAction({ confirmText: '确定？' }), { id: 'rec-1' })
    await flushPromises()
    expect(runRowAction).not.toHaveBeenCalled()
  })

  it('配了 paramFields 时打开参数对话框而不是直接提交', async () => {
    const w = mountRunner()
    const action = makeAction({
      paramFields: [
        { fieldName: 'reason', label: '原因', controlType: 'textarea' } as any,
      ],
    })
    await (w.vm as any).run(action, { id: 'rec-1' })
    await flushPromises()
    expect(runRowAction).not.toHaveBeenCalled()
    expect((w.vm as any).paramDialogVisible).toBe(true)
  })

  it('status=running 时启动轮询并在超时后停止', async () => {
    vi.useFakeTimers()
    runRowAction.mockResolvedValue({ ok: true, status: 'running' })
    const w = mountRunner()
    await (w.vm as any).run(makeAction(), { id: 'rec-1' })
    await flushPromises()
    expect((w.vm as any).isPolling).toBe(true)
    // 5 分钟上限
    vi.advanceTimersByTime(5 * 60 * 1000 + 5000)
    await flushPromises()
    expect((w.vm as any).isPolling).toBe(false)
  })

  it('status=submitted 时不轮询', async () => {
    const w = mountRunner()
    await (w.vm as any).run(makeAction({ statusField: undefined }), { id: 'rec-1' })
    await flushPromises()
    expect((w.vm as any).isPolling).toBe(false)
  })

  it('卸载时清理定时器', async () => {
    vi.useFakeTimers()
    runRowAction.mockResolvedValue({ ok: true, status: 'running' })
    const w = mountRunner()
    await (w.vm as any).run(makeAction(), { id: 'rec-1' })
    await flushPromises()
    const clearSpy = vi.spyOn(globalThis, 'clearInterval')
    w.unmount()
    expect(clearSpy).toHaveBeenCalled()
  })
})
```

- [ ] **Step 3: 运行测试确认失败**

```bash
npx vitest run src/components/dynamic/__tests__/RowActionRunner.test.ts
```

预期：FAIL，无法解析 `../RowActionRunner.vue`

- [ ] **Step 4: 实现 `RowActionRunner.vue`**

创建 `src/components/dynamic/RowActionRunner.vue`：

```vue
<template>
  <el-dialog
    v-model="paramDialogVisible"
    :title="pendingAction?.label || '执行操作'"
    width="520px"
    append-to-body
  >
    <DynamicForm
      v-if="pendingAction?.paramFields?.length"
      ref="paramFormRef"
      v-model="paramValues"
      :fields="pendingAction.paramFields"
    />
    <template #footer>
      <el-button @click="paramDialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submitWithParams">
        确定
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 行操作执行器
 *
 * 负责：二次确认 → 参数表单 → 提交 → 轮询该行状态直至终态。
 * 挂在 DynamicPage 上，由行「⋯」菜单里的按钮调用 run()。
 */
import { ref, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import DynamicForm from '@/components/dynamic-form/DynamicForm.vue'
import { runRowAction } from '@/api/rowAction'
import type { RowActionConfig } from '@/types/rowAction'

const props = defineProps<{
  collection: string
}>()

const emit = defineEmits<{
  (e: 'refresh'): void
}>()

/** 轮询间隔 5s，上限 5 分钟 —— AI 动作常跑几十秒到数分钟 */
const POLL_INTERVAL_MS = 5000
const POLL_MAX_MS = 5 * 60 * 1000

const paramDialogVisible = ref(false)
const pendingAction = ref<RowActionConfig | null>(null)
const pendingRow = ref<Record<string, unknown> | null>(null)
const paramValues = ref<Record<string, unknown>>({})
const submitting = ref(false)
const isPolling = ref(false)

let pollTimer: ReturnType<typeof setInterval> | null = null
let pollStopTimer: ReturnType<typeof setTimeout> | null = null

function stopPolling(notice = false) {
  if (pollTimer) clearInterval(pollTimer)
  if (pollStopTimer) clearTimeout(pollStopTimer)
  pollTimer = null
  pollStopTimer = null
  const wasPolling = isPolling.value
  isPolling.value = false
  if (notice && wasPolling) {
    ElMessage.info('执行时间较长，请稍后手动刷新查看')
  }
}

function startPolling() {
  stopPolling()
  isPolling.value = true
  pollTimer = setInterval(() => emit('refresh'), POLL_INTERVAL_MS)
  pollStopTimer = setTimeout(() => stopPolling(true), POLL_MAX_MS)
}

/** 由父组件在该行离开 runningValue 后调用，提前结束轮询 */
function notifySettled() {
  stopPolling()
}

async function run(action: RowActionConfig, row: Record<string, unknown>) {
  if (action.confirmText) {
    try {
      await ElMessageBox.confirm(action.confirmText, action.label, {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }

  pendingAction.value = action
  pendingRow.value = row

  if (action.paramFields?.length) {
    paramValues.value = {}
    paramDialogVisible.value = true
    return
  }
  await submit({})
}

async function submitWithParams() {
  await submit({ ...paramValues.value })
}

async function submit(params: Record<string, unknown>) {
  const action = pendingAction.value
  const row = pendingRow.value
  if (!action || !row) return

  submitting.value = true
  try {
    const res = await runRowAction(
      props.collection,
      String(row.id),
      action.id,
      params,
    )
    paramDialogVisible.value = false
    ElMessage.success('已提交')
    emit('refresh')
    if (res?.status === 'running') startPolling()
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
    ElMessage.error(msg || '执行失败')
  } finally {
    submitting.value = false
  }
}

onUnmounted(() => stopPolling())

defineExpose({ run, notifySettled, isPolling, paramDialogVisible })
</script>
```

- [ ] **Step 5: 运行测试确认通过**

```bash
npx vitest run src/components/dynamic/__tests__/RowActionRunner.test.ts
```

预期：7 passed

- [ ] **Step 6: 接进 `DynamicPage.vue`**

在 `src/views/dynamic/DynamicPage.vue` 的 `extra-actions` 插槽内（约 279 行 `</el-dropdown-item>` 之后、`</template>` 之前）追加：

```vue
          <el-dropdown-item
            v-for="a in visibleRowActionsFor(row)"
            :key="a.id"
            @click="rowActionRunnerRef?.run(a, row)"
          >
            {{ a.label }}
          </el-dropdown-item>
```

在 `</el-card>`（表格卡片结束）之后、看板卡片之前，挂上执行器：

```vue
    <RowActionRunner
      ref="rowActionRunnerRef"
      :collection="collection"
      @refresh="loadData"
    />
```

在 script 区加 import 与逻辑（放在 `boundRowExportScripts` 计算属性附近）：

```ts
import RowActionRunner from '@/components/dynamic/RowActionRunner.vue'
import { evaluateRowActionCondition } from '@/utils/rowActionCondition'
import type { RowActionConfig } from '@/types/rowAction'

const rowActionRunnerRef = ref<InstanceType<typeof RowActionRunner> | null>(null)

/** 该页面配置的行操作（未过滤） */
const rowActions = computed<RowActionConfig[]>(
  () => pageConfig.value?.rowActions || [],
)

/**
 * 对某一行可见的行操作。
 * 前端过滤只是 UX，后端会用同一套规则复核一次。
 */
function visibleRowActionsFor(row: Record<string, unknown>): RowActionConfig[] {
  const role = authStore.userRole
  const superuser = authStore.isSuperuser
  return rowActions.value.filter((a) => {
    if (!a.enabled) return false
    if (a.roles?.length && !superuser && (!role || !a.roles.includes(role))) return false
    return evaluateRowActionCondition(a.visibleWhen, row)
  })
}
```

> `collection` / `loadData` / `pageConfig` / `authStore` 在该文件中已存在；若 `collection` 的变量名不同（例如是 `collectionName`），按文件里的实际命名对齐。

- [ ] **Step 7: 类型检查 + 跑相关前端测试**

```bash
npx vue-tsc --noEmit
npx vitest run src/components/dynamic src/utils/__tests__/rowActionCondition.test.ts
```

预期：均通过

- [ ] **Step 8: 提交**

```bash
git add src/api/rowAction.ts src/components/dynamic/RowActionRunner.vue src/components/dynamic/__tests__/RowActionRunner.test.ts src/views/dynamic/DynamicPage.vue
git commit -m "feat(row-actions): 数据页行菜单渲染自定义按钮，含确认/参数表单/状态轮询"
```

---

### Task 9: 配置 UI —— `RowActionsEditor` 与页面配置标签页

**Files:**
- Create: `src/components/admin/RowActionsEditor.vue`
- Modify: `src/views/admin/PageConfigManager.vue`（新增标签页 + 表单字段读写）
- Test: `src/components/admin/__tests__/RowActionsEditor.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `RowActionConfig`；既有的 webhook 规则 API 与 AI 扫描任务 API
- Produces: `RowActionsEditor` 组件，`v-model` 绑 `RowActionConfig[]`，props 含 `fields: FieldConfig[]`

**关键约束**：`PageConfigManager.vue` 已 1500+ 行，编辑器**必须**抽成独立组件，不要往里塞。

- [ ] **Step 1: 写失败的测试**

创建 `src/components/admin/__tests__/RowActionsEditor.test.ts`：

```ts
import { describe, it, expect, vi, beforeAll } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import RowActionsEditor from '../RowActionsEditor.vue'
import type { RowActionConfig } from '@/types/rowAction'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

vi.mock('@/api/webhook', () => ({
  getWebhookRules: vi.fn().mockResolvedValue({
    rules: [{ id: 'wh-1', name: '推送外部', triggerEvent: 'manual', enabled: true }],
  }),
}))
vi.mock('@/api/aiScanTask', () => ({
  listScanTasks: vi.fn().mockResolvedValue([{ id: 'st-1', name: '自动审核' }]),
}))

const FIELDS = [
  { fieldName: 'status', label: '状态', controlType: 'select' },
  { fieldName: 'result', label: '结果', controlType: 'text' },
] as any

function mountEditor(modelValue: RowActionConfig[] = []) {
  return mount(RowActionsEditor, {
    props: { modelValue, fields: FIELDS },
  })
}

describe('RowActionsEditor', () => {
  it('空配置时展示空态', async () => {
    const w = mountEditor()
    await flushPromises()
    expect(w.text()).toContain('尚未配置行操作')
  })

  it('新增动作会 emit 一条带 id 的默认配置', async () => {
    const w = mountEditor()
    await flushPromises()
    ;(w.vm as any).addAction()
    await flushPromises()
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const list = emitted![emitted!.length - 1][0] as RowActionConfig[]
    expect(list).toHaveLength(1)
    expect(list[0].id).toBeTruthy()
    expect(list[0].actionType).toBe('webhook')
    expect(list[0].enabled).toBe(true)
  })

  it('删除动作会从列表移除', async () => {
    const existing: RowActionConfig[] = [
      { id: 'ra-1', label: 'A', actionType: 'webhook', enabled: true },
      { id: 'ra-2', label: 'B', actionType: 'aiTask', enabled: true },
    ]
    const w = mountEditor(existing)
    await flushPromises()
    ;(w.vm as any).removeAction(0)
    await flushPromises()
    const emitted = w.emitted('update:modelValue')!
    const list = emitted[emitted.length - 1][0] as RowActionConfig[]
    expect(list.map((a) => a.id)).toEqual(['ra-2'])
  })

  it('只把本页面的字段作为状态字段候选', async () => {
    const w = mountEditor([
      { id: 'ra-1', label: 'A', actionType: 'webhook', enabled: true },
    ])
    await flushPromises()
    expect((w.vm as any).fieldOptions.map((o: any) => o.value)).toEqual([
      'status',
      'result',
    ])
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
npx vitest run src/components/admin/__tests__/RowActionsEditor.test.ts
```

预期：FAIL，无法解析 `../RowActionsEditor.vue`

- [ ] **Step 3: 确认依赖的 API 函数名**

打开 `src/api/` 目录，确认 webhook 规则列表与 AI 扫描任务列表的实际导出名与返回结构，把上面 mock 里的 `getWebhookRules` / `listScanTasks` 换成真实名字（测试与实现都要改成一致）。

```bash
npx vitest run src/components/admin/__tests__/RowActionsEditor.test.ts --reporter verbose
```

- [ ] **Step 4: 实现 `RowActionsEditor.vue`**

创建 `src/components/admin/RowActionsEditor.vue`。结构：左侧动作列表（`el-table` 或可拖拽列表，顺序即菜单顺序），右侧编辑面板分四组。骨架如下（把 API 名换成 Step 3 确认的真实名）：

```vue
<template>
  <div class="row-actions-editor">
    <div class="ra-list">
      <el-button type="primary" plain size="small" @click="addAction">新增行操作</el-button>
      <el-empty v-if="!list.length" description="尚未配置行操作" :image-size="60" />
      <ul v-else class="ra-items">
        <li
          v-for="(a, i) in list"
          :key="a.id"
          :class="{ active: i === activeIndex }"
          @click="activeIndex = i"
        >
          <span class="ra-name">{{ a.label || '(未命名)' }}</span>
          <el-tag size="small" :type="a.actionType === 'webhook' ? 'primary' : 'success'">
            {{ a.actionType === 'webhook' ? 'Webhook' : 'AI 任务' }}
          </el-tag>
          <el-button link size="small" @click.stop="moveUp(i)" :disabled="i === 0">上移</el-button>
          <el-button link size="small" type="danger" @click.stop="removeAction(i)">删除</el-button>
        </li>
      </ul>
    </div>

    <div v-if="current" class="ra-detail">
      <el-divider content-position="left">基本</el-divider>
      <el-form label-width="120px">
        <el-form-item label="按钮名称" required>
          <el-input v-model="current.label" placeholder="显示在行「⋯」菜单里的文案" />
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="current.actionType">
            <el-radio label="webhook">Webhook</el-radio>
            <el-radio label="aiTask">AI 任务</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="current.enabled" />
        </el-form-item>

        <el-divider content-position="left">绑定</el-divider>
        <el-form-item v-if="current.actionType === 'webhook'" label="Webhook 规则">
          <el-select v-model="current.webhookRuleId" clearable filterable>
            <el-option v-for="r in manualRules" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
          <div class="ra-hint">
            只列出触发事件为「手动」的规则。去
            <router-link to="/admin/webhooks">Webhook 管理</router-link> 新建。
          </div>
        </el-form-item>
        <el-form-item v-else label="AI 扫描任务">
          <el-select v-model="current.scanTaskId" clearable filterable>
            <el-option v-for="t in scanTasks" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
          <div class="ra-hint">
            去 <router-link to="/admin/ai-scan-tasks">AI 定时任务</router-link> 新建；
            只想手动用可把任务设为「未启用」。
          </div>
        </el-form-item>

        <el-divider content-position="left">显示条件</el-divider>
        <el-form-item label="可见角色">
          <el-select v-model="current.roles" multiple clearable placeholder="留空 = 所有角色">
            <el-option v-for="r in roleOptions" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="仅当">
          <el-select v-model="conditionField" clearable placeholder="字段" style="width: 140px">
            <el-option v-for="f in fieldOptions" :key="f.value" :label="f.label" :value="f.value" />
          </el-select>
          <el-select v-model="conditionOperator" style="width: 110px; margin: 0 8px">
            <el-option v-for="o in OPERATOR_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-input
            v-if="!['empty', 'notEmpty'].includes(conditionOperator)"
            v-model="conditionValueText"
            placeholder="值（in / notIn 用逗号分隔）"
            style="width: 220px"
          />
        </el-form-item>
        <el-form-item label="二次确认文案">
          <el-input v-model="current.confirmText" placeholder="留空 = 不弹确认框" />
        </el-form-item>

        <template v-if="current.actionType === 'webhook'">
        <el-divider content-position="left">执行结果</el-divider>
        <el-form-item label="状态字段">
          <el-select v-model="current.statusField" clearable placeholder="留空 = 不写状态">
            <el-option v-for="f in fieldOptions" :key="f.value" :label="f.label" :value="f.value" />
          </el-select>
        </el-form-item>
        <template v-if="current.statusField">
          <el-form-item label="执行中值">
            <el-input v-model="current.runningValue" placeholder="如：执行中" />
          </el-form-item>
          <el-form-item label="成功值">
            <el-input v-model="current.doneValue" placeholder="如：已完成" />
          </el-form-item>
          <el-form-item label="失败值">
            <el-input v-model="current.failedValue" placeholder="如：执行失败" />
          </el-form-item>
        </template>
        <el-form-item label="响应字段映射">
          <ResponseMappingEditor v-model="current.responseMapping" :field-options="fieldOptions" />
        </el-form-item>
        </template>
        <el-alert
          v-else
          type="info"
          :closable="false"
          title="AI 类动作的执行状态与结果回写，一律使用所绑「AI 扫描任务」里的状态字段配置，此处无需重复设置。"
        />
      </el-form>

      <el-divider content-position="left">参数表单</el-divider>
      <FieldConfigEditor v-model="current.paramFields" :allow-controls="ALLOWED_PARAM_CONTROLS" />
    </div>
  </div>
</template>
```

script 要点：

- `list` 是 `props.modelValue` 的本地深拷贝，任何变更后 `emit('update:modelValue', list)`。
- `addAction()` 用 `nanoid()`（项目已有依赖，参考其他配置编辑器的用法）生成 id，默认 `{ actionType: 'webhook', enabled: true, label: '' }`。
- `fieldOptions` 由 `props.fields` 映射成 `{ label: '<label>（<fieldName>）', value: fieldName }`。
- `manualRules` 拉全部 webhook 规则后 `filter(r => r.triggerEvent === 'manual')`。
- `conditionField` / `conditionOperator` / `conditionValueText` 是三个 computed（get/set），读写 `current.visibleWhen`；`in`/`notIn` 时按逗号分割成数组；字段清空时把 `visibleWhen` 置为 `undefined`。
- `OPERATOR_OPTIONS`：`等于/不等于/属于/不属于/为空/不为空` 对应 `eq/ne/in/notIn/empty/notEmpty`。
- `ALLOWED_PARAM_CONTROLS` 排除 `relation`/`reference`/`quoteSelect`/`autoSequence`，与后端 `row_action_validate._FORBIDDEN_PARAM_CONTROLS` 一致。
- **切到 `aiTask` 类型时，必须清空 `statusField` / `runningValue` / `doneValue` / `failedValue` / `responseMapping` 五项**（置为 `undefined`），不能只是隐藏 —— 留在 JSON 里会让人以为它们生效。加一个 `watch(() => current.value?.actionType)` 做这件事。
- `ResponseMappingEditor` 若项目里已有等价的映射编辑组件（AI 扫描任务页的 `fieldMapping` 编辑器），直接复用；否则在本文件内实现一个极简的「新增行 / jsonKey 输入 / 目标字段下拉 / 删除」表格。
- `FieldConfigEditor` 复用 `deleteBinding.fields` 用的那个字段配置编辑器组件；若它不支持 `allow-controls` prop，改为在本组件内过滤可选控件类型，不要去改公共组件。

- [ ] **Step 5: 运行测试确认通过**

```bash
npx vitest run src/components/admin/__tests__/RowActionsEditor.test.ts
```

预期：4 passed

- [ ] **Step 6: 接进 `PageConfigManager.vue`**

在 `el-tabs` 中，「删除绑定」标签页（约 399–524 行）之后插入：

```vue
              <el-tab-pane label="行操作" name="rowActions">
                <RowActionsEditor
                  v-model="formData.rowActions"
                  :fields="currentFields"
                />
              </el-tab-pane>
```

script 区加 import：

```ts
import RowActionsEditor from '@/components/admin/RowActionsEditor.vue'
```

在三处表单数据装填的地方补 `rowActions`（照现有 `rowExportScripts` 的位置，约 1014、1172、1430 行）：

```ts
      rowActions: config.rowActions || [],
```

```ts
      rowActions: formData.value.rowActions || [],
```

```ts
      rowActions: currentConfig.rowActions || [],
```

> `currentFields` 是该页面当前编辑中的字段数组；若文件里的变量名不同，用实际名字。

- [ ] **Step 7: 类型检查 + 构建**

```bash
npx vue-tsc --noEmit
npm run build
```

预期：均通过

- [ ] **Step 8: 提交**

```bash
git add src/components/admin/RowActionsEditor.vue src/components/admin/__tests__/RowActionsEditor.test.ts src/views/admin/PageConfigManager.vue
git commit -m "feat(row-actions): 页面配置新增「行操作」标签页与配置编辑器"
```

---

### Task 10: 端到端验证（Playwright + proxy.py）

**Files:**
- Create: `.playwright-mcp/row-actions-webhook.png`、`.playwright-mcp/row-actions-ai.png`（截图证据）

**Interfaces:**
- Consumes: 全部前置任务
- Produces: 可写进 PR 的验证结论

**这是 CLAUDE.md 的强制要求**：单元测试和 `vue-tsc` 必要但不充分。

- [ ] **Step 1: 起服务**

```bash
cd server && python init_db.py
npm run dev:all
```

后端改了很多模块，**必须重启**（Werkzeug/waitress 启动时加载模块，不会热更新后端代码）。

- [ ] **Step 2: 准备一条 manual webhook 规则**

用 Playwright 登录（`admin` / `admin123`），进 Webhook 管理页新建一条规则：触发事件选「手动」，URL 指向一个本地回声服务。回声服务可用一次性脚本：

```bash
cd server && python -c "
from flask import Flask, request, jsonify
a = Flask(__name__)
@a.post('/echo')
def e():
    print('GOT', request.get_json())
    return jsonify({'code': 'X9', 'msg': 'ok'})
a.run(port=5599)
"
```

规则 URL 填 `http://127.0.0.1:5599/echo`。

- [ ] **Step 3: 配置 webhook 行操作并实跑**

进「页面配置管理」→ 选一个有数据的页面 → 「行操作」标签页 → 新增：名称「推送外部」、类型 Webhook、绑刚建的规则、状态字段选一个文本字段、执行中/成功/失败值分别填「同步中/已同步/同步失败」、响应映射 `code → 某文本字段`、二次确认文案填「确定推送？」、参数表单加一个 `reason` 文本框。保存。

进该数据页 → 点某行「⋯」→ 应看到「推送外部」→ 点击 → 确认框 → 填 reason → 提交。

断言：

1. toast 出现「已提交」
2. 该行状态字段变为「同步中」，随后（轮询后）变为「已同步」
3. 响应映射的字段被写入 `X9`
4. 回声服务控制台打印出的 payload 含 `event: manual`、`actionId`、`params.reason`

截图存 `.playwright-mcp/row-actions-webhook.png`。

- [ ] **Step 4: 交叉核对数据库**

```bash
cd server && python -c "
from db import get_db
with get_db() as c:
    cur = c.cursor()
    cur.execute(\"SELECT event_type, success, response_status FROM webhook_logs ORDER BY created_at DESC LIMIT 3\")
    print(cur.fetchall())
"
```

预期：最新一条 `event_type='manual'`、`success=True`、`response_status=200`。

- [ ] **Step 5: 配置 AI 行操作并实跑**

前提：OpenCode 与 MCP 服务在跑。进「AI 定时任务」新建一个任务（可 `enabled=false`，只当手动模板用），绑定同一个 collection，配好 `statusField` 与 `fieldMapping`。回到页面配置的「行操作」标签页新增一个 AI 类型动作绑它。

在数据页点该按钮，断言：

1. 行状态变为任务的 `runningValue`
2. AI 助手侧边栏出现一个名为「行操作·<按钮名>·<时间戳>」的批任务，含 1 个子会话
3. 子会话工作区的 `uploads/record.md` 末尾含「## 本次执行参数」段落（若配了参数表单）
4. 完成后行状态变为 `doneValue`，`fieldMapping` 的目标字段被写入

截图存 `.playwright-mcp/row-actions-ai.png`。

- [ ] **Step 6: 通过 proxy.py 验证**

```bash
npm run build
cd server && python proxy.py
```

浏览器开 `http://localhost:8080`，重跑 Step 3 的 webhook 行操作流程一遍。

**这一步不可跳过**：Vite 开发代理不是生产环境。本期虽然没有新增 SSE（所以 `_is_sse()` 无需改动），但新增了路由形状 `/<collection>/<id>/row-actions/<aid>/run`，要确认 `_proxy_to_backend` 正常转发且 `Authorization` 头没被丢掉。

也可以直接用 curl 确认：

```bash
curl -s -X POST "http://localhost:8080/api/<collection>/<recordId>/row-actions/<actionId>/run" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"params":{"reason":"x"}}'
```

预期返回 `{"ok":true,"status":"running"}`，而不是 401/404。

- [ ] **Step 7: 跑全量测试**

```bash
npm run test:all
```

预期：前后端全绿。

- [ ] **Step 8: 提交截图证据**

```bash
git add .playwright-mcp/row-actions-webhook.png .playwright-mcp/row-actions-ai.png
git commit -m "test(row-actions): 补 Playwright 实跑截图证据"
```

---

### Task 11: 用户文档

**Files:**
- Create: `docs/user-guide/data/row-actions.md`
- Create: `docs/user-guide/_images/row-actions-config.png`、`docs/user-guide/_images/row-actions-menu.png`
- Modify: `docs/user-guide/README.md`、`docs/user-guide/integration/webhooks.md`、`docs/user-guide/ai/scan-tasks.md`、`CLAUDE.md`

**Interfaces:**
- Consumes: 全部前置任务
- Produces: 用户可读的功能文档

**这是 CLAUDE.md 的强制要求**：功能变更没有配套用户文档 = 未完成。

- [ ] **Step 1: 截配置页与运行时截图**

沿用 Task 10 的环境，截两张图存进 `docs/user-guide/_images/`：
- `row-actions-config.png`：页面配置的「行操作」标签页（含一个配好的 webhook 动作）
- `row-actions-menu.png`：数据页行「⋯」菜单展开、能看到自定义按钮

- [ ] **Step 2: 写 `docs/user-guide/data/row-actions.md`**

按现有 `docs/user-guide/` 下其他文档的风格（中文、任务导向、含截图）写，覆盖：

1. **这是什么** —— 在数据页的行「⋯」菜单里加自定义按钮，点一下对该行触发 Webhook 或 AI 任务，结果回写到这行的字段上
2. **先决条件** —— webhook 类型要先在「Webhook 管理」建一条**触发事件为「手动」**的规则；AI 类型要先在「AI 定时任务」建一个任务（可以不启用定时，只当手动模板）
3. **配置步骤** —— 页面配置管理 → 选页面 → 「行操作」标签页 → 逐项说明四组配置（基本 / 绑定 / 显示条件 / 执行结果），配 `row-actions-config.png`
4. **参数表单** —— 什么时候用，支持哪些控件（说明为什么不支持关联/引用/引用选择/自动编号）
5. **状态回写怎么工作** —— 执行中 → 成功/失败；不配状态字段会怎样（只触发，不轮询）
6. **AI 类型的行为** —— 手动触发**不看**「待处理」状态，已处理的行也能重跑；参数会以「## 本次执行参数」段落进上下文
7. **在哪看执行详情** —— Webhook 看 Webhook 日志；AI 看 AI 助手里的批任务子会话
8. **常见问题** —— 按钮不显示（角色白名单 / 显示条件 / 未启用）；提示「该行有正在执行的动作」（等它跑完，或超过陈旧阈值后可重触发）；webhook 回写没生效（响应不是 JSON / jsonKey 对不上）
9. **限制** —— 不支持批量；条件只支持单条；进程崩溃可能留下卡在「执行中」的行，超过阈值后重新点击即可

- [ ] **Step 3: 更新索引与相关文档**

- `docs/user-guide/README.md`：在 `data/` 分组下加一行指向 `row-actions.md`
- `docs/user-guide/integration/webhooks.md`：补一节说明 `manual`（手动）触发事件 —— 这类规则不会被增删改自动触发，只能被数据页的行操作按钮点名调用；其 `triggerCondition` / 触发时机 / 失败回滚三项对手动规则无效
- `docs/user-guide/ai/scan-tasks.md`：补一节「手动对单行触发」—— 说明怎么把扫描任务绑到行操作按钮上，以及与定时扫描的差别（不看待处理状态、可重跑）

- [ ] **Step 4: 更新 `CLAUDE.md`**

在 "Webhook Rules System" 与 "AI Scheduled Tasks" 之间插入一节：

```markdown
### 数据页自定义行级操作按钮（Row Actions）

数据页可在行「⋯」菜单里配置自定义按钮，点击后对该行**异步**触发一条 Webhook 规则或一个 AI 扫描任务，执行状态与结果回写到该行字段。

* **配置**：`page_configs.row_actions`（JSONB 列，与 `view_config` / `delete_binding` 同构），在「页面配置管理 → 行操作」标签页编辑。每个动作是一层**薄绑定**：绑哪个执行器 + 角色可见性 + 单条件显示 (`visibleWhen`) + 二次确认 + 参数表单 (`paramFields`，复用 `FieldConfig`/`DynamicForm`) + 状态字段与回写映射。
* **执行器全部复用**：webhook 走 `webhook_engine.fire_webhook_rule`（新增 `triggerEvent='manual'`，这类规则**永不**被 create/update/delete 自动触发，规则自身的 `triggerCondition` 被忽略——条件由 `visibleWhen` 单点负责）；AI 走 `ai_scan_engine.claim_one`（`claim_records` 的单行版，**不带 `pendingValue` 谓词**，所以已处理的行能重跑）+ `create_batch` 单子会话，回写仍由 `on_child_finished` 完成。
* **编排** `server/utils/row_action_engine.py`：后端复核角色/条件 → 写 `runningValue`（幂等闸门，卡住超过 `timeout × (retries+1)`、下限 5 分钟视为陈旧可重触发）→ 分派 → `finally` 落终态。**刻意不建执行记录表**：webhook 审计看 `webhook_logs`，AI 审计看子会话对话。
* **端点** `POST /<collection>/<recordId>/row-actions/<actionId>/run`（`dynamic_bp`，`write_required` + `require_page_action(collection,'update')`）。前端 `RowActionRunner.vue` 提交后按 5s 间隔轮询该行状态，上限 5 分钟。
* 条件求值双份实现（`server/utils/row_action_condition.py` + `src/utils/rowActionCondition.ts`），共读夹具 `server/tests/fixtures/row_action_conditions.json` 防漂移。
* 用户文档：`docs/user-guide/data/row-actions.md`。
```

- [ ] **Step 5: 提交**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(row-actions): 新增行操作用户指南，更新 webhook/扫描任务文档与 CLAUDE.md"
```

---

## 自查记录

**Spec 覆盖检查**：设计文档 §3 配置模型 → Task 2/3；§3.4 保存校验 → Task 3；§4.1 入口 → Task 7；§4.2 编排 → Task 6；§4.3 webhook → Task 4 + 6；§4.4 AI → Task 5 + 6；§4.5 不建表 → Task 6（已在模块 docstring 写明理由）；§5 错误处理 → Task 6/7 的测试逐条覆盖；§6.1 配置 UI → Task 9；§6.2 运行时 UI → Task 8；§7 权限 → Task 6/7；§8 测试 → 各任务内嵌；§9 验证 → Task 10；§10 文档 → Task 11。无遗漏。

**已知需在实现时对齐的实际命名**（计划中已逐处标注，不是占位符）：
- `src/api/request.ts` 的默认导出名与响应解包方式（Task 8 Step 1）
- webhook 规则列表 / AI 扫描任务列表的前端 API 导出名（Task 9 Step 3）
- `DynamicPage.vue` 中 `collection` / `loadData` / `currentFields` 的实际变量名（Task 8 Step 6、Task 9 Step 6）
- 字段配置编辑器组件是否支持 `allow-controls` prop（Task 9 Step 4）

**类型一致性**：`FieldMappingRow` 在 `src/types/aiScanTask.ts` 已存在，`rowAction.ts` 直接复用；后端 `responseMapping` 读 `jsonKey`/`column` 两个键，与 `ai_scan_tasks.fieldMapping` 完全同构。`claim_one` 与 `build_context_dir` 统一用 camelCase task dict（`ai_scan_repo.get_task()` 的形状），已在 Task 5 顶部标红。
