# 检查规则（Check Rules）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为智能写作编辑器加可配置的领域检查规则：四类本地毫秒级判定（映射/正则提取校验/锚定白名单/文档级条件完整性）+ 可选 AI 语义核对，全局与文档级作用域，管理面表格与 CSV 导入导出。

**Architecture:** 规则真源为服务端文件（全局 `.data/规则-全局.json`、文档级 `.data/ws/<docId>/规则.json`），Flask 管理面原子读写、Node 分析前读 `aiPrompt` 注入分析 prompt、浏览器经 REST 拉取并编译为 decoration 层本地规则；本地命中复用现有建议卡片（notice 型隐藏“接受”）。

**Tech Stack:** Vue 3 + Tiptap/ProseMirror、Flask/psycopg2、Python `csv`、Vitest、Pytest、Playwright、Node/TypeScript（suggest-editor-server）。

**Spec:** `docs/superpowers/specs/2026-10-19-suggest-editor-check-rules-design.md`

## Global Constraints

- 规则文件上限：单文件 ≤ 2 MB；规则 ≤ 500 条；`maps/patterns/whitelists/presence` 每类数组 ≤ 200 项；message/aiPrompt ≤ 500 字符。
- 正则校验：保存/导入时 Python `re.compile` 必须通过；浏览器编译失败的单条规则跳过并 `console.warn`，不阻断其它规则。
- 导入失败整批拒绝（400 + 行号原因），不做部分成功。
- 文件写入必须用 pathlib `write_text(..., newline='')` + `replace` 原子写（open('w') 带变量路径会被 Mimosa 钩子拦截）。
- 文档级规则仅属主可读写（admin 无旁路），路径经 `_vocab_safe_path` 安全闸。
- 本地匹配限单个文本节点内；presence 基于全文纯文本、大小写不敏感；全文 > 500,000 字符跳过 presence。
- notice 型提醒只可忽略、不可接受；忽略按 `(ruleId, quote)` 记忆至下次文档加载。
- 与现有「检查要点」并存，互不影响。
- 每任务结束运行相关测试并提交；提交信息用 `feat(se):`/`test(se):`/`docs(se):` 前缀。

---

### Task 1: 规则校验与 CSV 解析纯函数（Python）

**Files:**
- Create: `server/utils/suggest_editor_rules.py`
- Test: `server/tests/test_suggest_editor_rules_validate.py`

**Interfaces:**
- Produces:
  - `validate_rules(payload: dict) -> list[dict]`（校验并返回归一化后的 rules；失败抛 `RuleValidationError(ValueError)`，消息含定位）
  - `parse_rules_csv(text: str) -> list[dict]`（逐行生成规则 dict；坏行抛 `RuleValidationError` 含行号）
  - `merge_rules(existing: list[dict], incoming: list[dict], mode: str) -> tuple[list[dict], int, int]`（返回 merged, added, skipped）
  - `dedupe_key(rule: dict) -> str`（type + 关键字段拼接）
  - 常量 `MAX_FILE_BYTES=2_000_000`、`MAX_RULES=500`、`MAX_ITEMS=200`、`MAX_TEXT=500`

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_suggest_editor_rules_validate.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from utils.suggest_editor_rules import (
    RuleValidationError, validate_rules, parse_rules_csv, merge_rules,
)


def _rule(**kw):
    base = {'id': 'r1', 'name': '设备型号纠错', 'enabled': True, 'severity': 'error',
            'maps': [{'wrong': 'S-5700', 'right': 'S5700'}]}
    base.update(kw)
    return base


def test_validate_accepts_minimal_mapping_rule():
    rules = validate_rules({'rules': [_rule()]})
    assert rules[0]['id'] == 'r1' and rules[0]['maps'][0]['right'] == 'S5700'


def test_validate_rejects_bad_regex_and_reports_index():
    with pytest.raises(RuleValidationError) as ei:
        validate_rules({'rules': [_rule(patterns=[{'extract': '(', 'valid': 'x', 'message': 'm'}])]})
    assert 'rules[0]' in str(ei.value)


def test_validate_rejects_duplicate_id_and_bad_severity():
    with pytest.raises(RuleValidationError):
        validate_rules({'rules': [_rule(), _rule()]})
    with pytest.raises(RuleValidationError):
        validate_rules({'rules': [_rule(severity='fatal')]})


def test_validate_caps():
    big = [_rule(id=f'r{i}') for i in range(501)]
    with pytest.raises(RuleValidationError):
        validate_rules({'rules': big})


def test_csv_parses_all_five_types_and_multivalue_columns():
    text = (
        'type,name,severity,enabled,wrong,right,extract,valid,anchors,values,triggers,requiresAny,message,aiPrompt\n'
        'map,型号纠错,error,true,S-5700,S5700,,,,,,,,,\n'
        'pattern,型号格式,warn,true,,,([A-Z]{2,4}-\\d+),^S\\d{4}$,,,,,单板型号格式不对,\n'
        'whitelist,设备白名单,warn,true,,,,,设备类型：,S5700|S6700,,,设备类型不在清单内,\n'
        'presence,拖带完整性,warn,true,,,,,,,9800M12,9800UPS|不涉及拖带,未写拖带说明,\n'
        'ai,写作口径,info,true,,,,,,,,,,,统一用「方案」表述\n'
    )
    rules = parse_rules_csv(text)
    assert [r['id'].startswith('rule-') for r in rules] == [True] * 5
    assert rules[1]['patterns'][0]['valid'] == '^S\\d{4}$'
    assert rules[2]['whitelists'][0]['values'] == ['S5700', 'S6700']
    assert rules[3]['presence'][0]['requiresAny'] == ['9800UPS', '不涉及拖带']
    assert rules[4]['aiPrompt'] == '统一用「方案」表述'


def test_csv_error_reports_line_number():
    text = 'type,name,severity,enabled,wrong,right\nyaml,坏行,error,true,a,b\n'
    with pytest.raises(RuleValidationError) as ei:
        parse_rules_csv(text)
    assert '第 2 行' in str(ei.value) or 'line 2' in str(ei.value)


def test_merge_append_dedupes_and_replace_overwrites():
    existing = [_rule()]
    incoming = [_rule(id='rule-abc'), _rule(id='rule-def', maps=[{'wrong': 'X', 'right': 'Y'}])]
    merged, added, skipped = merge_rules(existing, incoming, 'append')
    assert (added, skipped) == (1, 1) and len(merged) == 2
    merged2, added2, _ = merge_rules(existing, incoming, 'replace')
    assert added2 == 2 and len(merged2) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_suggest_editor_rules_validate.py -q`
Expected: FAIL (`ModuleNotFoundError: utils.suggest_editor_rules`)

- [ ] **Step 3: Write minimal implementation**

```python
# server/utils/suggest_editor_rules.py
"""检查规则：校验、CSV 解析、合并。纯函数——文件读写留在路由层。"""
import csv
import io
import json
import re
import uuid

MAX_FILE_BYTES = 2_000_000
MAX_RULES = 500
MAX_ITEMS = 200
MAX_TEXT = 500
SEVERITIES = {'info', 'warn', 'error'}
CSV_COLUMNS = ['type', 'name', 'severity', 'enabled', 'wrong', 'right', 'extract',
               'valid', 'anchors', 'values', 'triggers', 'requiresAny', 'message', 'aiPrompt']


class RuleValidationError(ValueError):
    pass


def _check_text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise RuleValidationError(f'{where}: 不能为空')
    if len(value) > MAX_TEXT:
        raise RuleValidationError(f'{where}: 超过 {MAX_TEXT} 字符')


def _compile(pattern, where):
    try:
        re.compile(pattern)
    except re.error as e:
        raise RuleValidationError(f'{where}: 正则编译失败（{e}）')


def _validate_one(index, rule):
    where = f'rules[{index}]'
    if not isinstance(rule, dict):
        raise RuleValidationError(f'{where}: 必须是对象')
    rule_id = rule.get('id')
    _check_text(rule_id, f'{where}.id')
    if len(rule_id) > 64:
        raise RuleValidationError(f'{where}.id: 超过 64 字符')
    _check_text(rule.get('name'), f'{where}.name')
    if rule.get('severity') not in SEVERITIES:
        raise RuleValidationError(f'{where}.severity: 必须是 info/warn/error')
    if not isinstance(rule.get('enabled', True), bool):
        raise RuleValidationError(f'{where}.enabled: 必须是布尔值')
    if rule.get('aiPrompt') is not None:
        _check_text(rule['aiPrompt'], f'{where}.aiPrompt')

    def items(key):
        value = rule.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise RuleValidationError(f'{where}.{key}: 必须是数组')
        if len(value) > MAX_ITEMS:
            raise RuleValidationError(f'{where}.{key}: 超过 {MAX_ITEMS} 项')
        return value

    for i, item in enumerate(items('maps')):
        _check_text(item.get('wrong'), f'{where}.maps[{i}].wrong')
        if not isinstance(item.get('right'), str):
            raise RuleValidationError(f'{where}.maps[{i}].right: 必须是字符串')
        if not isinstance(item.get('caseSensitive', False), bool):
            raise RuleValidationError(f'{where}.maps[{i}].caseSensitive: 必须是布尔值')
    for i, item in enumerate(items('patterns')):
        _check_text(item.get('extract'), f'{where}.patterns[{i}].extract')
        _compile(item['extract'], f'{where}.patterns[{i}].extract')
        _check_text(item.get('valid'), f'{where}.patterns[{i}].valid')
        _compile(item['valid'], f'{where}.patterns[{i}].valid')
        _check_text(item.get('message'), f'{where}.patterns[{i}].message')
    for i, item in enumerate(items('whitelists')):
        anchors = item.get('anchors')
        values = item.get('values')
        if not isinstance(anchors, list) or not anchors:
            raise RuleValidationError(f'{where}.whitelists[{i}].anchors: 不能为空数组')
        if not isinstance(values, list) or not values:
            raise RuleValidationError(f'{where}.whitelists[{i}].values: 不能为空数组')
        for v in anchors + values:
            _check_text(v, f'{where}.whitelists[{i}]')
        if item.get('valuePattern'):
            _compile(item['valuePattern'], f'{where}.whitelists[{i}].valuePattern')
        _check_text(item.get('message'), f'{where}.whitelists[{i}].message')
    for i, item in enumerate(items('presence')):
        for key in ('triggers', 'requiresAny'):
            value = item.get(key)
            if not isinstance(value, list) or not value:
                raise RuleValidationError(f'{where}.presence[{i}].{key}: 不能为空数组')
            for v in value:
                _check_text(v, f'{where}.presence[{i}].{key}')
        _check_text(item.get('message'), f'{where}.presence[{i}].message')
    if not any(rule.get(k) for k in ('maps', 'patterns', 'whitelists', 'presence', 'aiPrompt')):
        raise RuleValidationError(f'{where}: 至少要有一种判定或 aiPrompt')

    normalized = dict(rule)
    normalized.setdefault('enabled', True)
    normalized.setdefault('severity', 'warn')
    return normalized


def validate_rules(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('rules'), list):
        raise RuleValidationError('请求体必须是 {rules: [...]}')
    rules = payload['rules']
    if len(rules) > MAX_RULES:
        raise RuleValidationError(f'规则数量超过 {MAX_RULES} 条')
    seen = set()
    out = []
    for i, rule in enumerate(rules):
        normalized = _validate_one(i, rule)
        if normalized['id'] in seen:
            raise RuleValidationError(f'rules[{i}].id: 重复的 id {normalized["id"]}')
        seen.add(normalized['id'])
        out.append(normalized)
    return out


def _split_multi(value):
    return [p.strip() for p in (value or '').split('|') if p.strip()]


def _row_to_rule(row, line_no):
    kind = (row.get('type') or '').strip().lower()
    name = (row.get('name') or '').strip()
    severity = (row.get('severity') or 'warn').strip() or 'warn'
    enabled = (row.get('enabled') or 'true').strip().lower() not in ('0', 'false', 'no', 'off')
    message = (row.get('message') or '').strip()
    rule = {'id': f'rule-{uuid.uuid4().hex[:12]}', 'name': name,
            'enabled': enabled, 'severity': severity}
    if (row.get('aiPrompt') or '').strip():
        rule['aiPrompt'] = row['aiPrompt'].strip()
    if kind == 'map':
        rule['maps'] = [{'wrong': row.get('wrong', '').strip(), 'right': row.get('right', '')}]
    elif kind == 'pattern':
        rule['patterns'] = [{'extract': row.get('extract', '').strip(),
                             'valid': row.get('valid', '').strip(),
                             'message': message}]
    elif kind == 'whitelist':
        rule['whitelists'] = [{'anchors': _split_multi(row.get('anchors')),
                               'values': _split_multi(row.get('values')),
                               'message': message}]
    elif kind == 'presence':
        rule['presence'] = [{'triggers': _split_multi(row.get('triggers')),
                             'requiresAny': _split_multi(row.get('requiresAny')),
                             'message': message}]
    elif kind == 'ai':
        pass
    else:
        raise RuleValidationError(f'第 {line_no} 行: 未知的 type {kind!r}（应为 map/pattern/whitelist/presence/ai）')
    try:
        return _validate_one(line_no - 2, rule)
    except RuleValidationError as e:
        raise RuleValidationError(f'第 {line_no} 行: {e}')


def parse_rules_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise RuleValidationError('CSV 缺少表头')
    out = []
    for offset, row in enumerate(reader, start=2):
        if not any((v or '').strip() for v in row.values() if isinstance(v, str)):
            continue
        out.append(_row_to_rule(row, offset))
    return out


def dedupe_key(rule):
    payload = {k: rule.get(k) for k in ('maps', 'patterns', 'whitelists', 'presence', 'aiPrompt') if rule.get(k)}
    return json.dumps([rule.get('name'), payload], ensure_ascii=False, sort_keys=True)


def merge_rules(existing, incoming, mode):
    if mode == 'replace':
        return list(incoming), len(incoming), 0
    seen = {dedupe_key(r) for r in existing}
    merged = list(existing)
    added = skipped = 0
    for rule in incoming:
        key = dedupe_key(rule)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        merged.append(rule)
        added += 1
    if len(merged) > MAX_RULES:
        raise RuleValidationError(f'合并后规则数量超过 {MAX_RULES} 条')
    return merged, added, skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_suggest_editor_rules_validate.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add server/utils/suggest_editor_rules.py server/tests/test_suggest_editor_rules_validate.py
git commit -m "feat(se): 检查规则校验与 CSV 解析纯函数（Python）"
```

---

### Task 2: Flask 规则端点（全局 + 文档级）

**Files:**
- Modify: `server/routes/suggest_editor.py`（追加规则段；复用 `_vocab_safe_path`/`_write_vocab_file`/`_read_vocab_file`/`_decode_vocab_upload`）
- Test: `server/tests/test_suggest_editor_rules_routes.py`

**Interfaces:**
- Consumes: Task 1 的 `validate_rules/parse_rules_csv/merge_rules/RuleValidationError`
- Produces HTTP：
  - `GET /suggest-editor/rules/global` → `{rules}`（文件缺失播种示例，含 9800 presence）
  - `PUT /suggest-editor/rules/global` ← `{rules}` → `{ok, count}`
  - `POST /suggest-editor/rules/global/import`（multipart `file`+`mode`）→ `{ok, added, skipped, total}`
  - `GET /suggest-editor/rules/global/export` → CSV attachment
  - 文档级同构：`GET|PUT /suggest-editor/documents/<id>/rules`、`POST .../rules/import`、`GET .../rules/export`
- Rules 文件路径：全局 `SE_DATA_DIR/规则-全局.json`；文档级 `SE_DATA_DIR/ws/<docId>/规则.json`

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_suggest_editor_rules_routes.py
import io
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest

from app import app
from auth import create_token
from db import get_db

SAMPLE_RULE = {'id': 'r1', 'name': '型号纠错', 'enabled': True, 'severity': 'error',
               'maps': [{'wrong': 'S-5700', 'right': 'S5700'}]}


@pytest.fixture()
def rules_env(tmp_path, monkeypatch):
    import routes.suggest_editor as se
    monkeypatch.setattr(se, 'SE_DATA_DIR', str(tmp_path))
    suffix = os.urandom(4).hex()
    admin_id, dev_id = f'se-rules-admin-{suffix}', f'se-rules-dev-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'admin')", (admin_id, 'sera-' + suffix, admin_id))
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (dev_id, 'serd-' + suffix, dev_id))
        conn.commit()
    client = app.test_client()
    app.config['TESTING'] = True
    yield {
        'client': client,
        'admin': {'Authorization': f"Bearer {create_token({'id': admin_id, 'username': 'a', 'role': 'admin'})}"},
        'dev': {'Authorization': f"Bearer {create_token({'id': dev_id, 'username': 'd', 'role': 'developer'})}"},
        'admin_id': admin_id, 'dev_id': dev_id,
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_document_states WHERE document_id IN "
                        "(SELECT id FROM se_documents WHERE owner_user_id IN (%s,%s))", (admin_id, dev_id))
            cur.execute("DELETE FROM se_documents WHERE owner_user_id IN (%s,%s)", (admin_id, dev_id))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (admin_id, dev_id))
        conn.commit()


def test_global_rules_require_admin_and_seed_example(rules_env):
    client, dev, admin = rules_env['client'], rules_env['dev'], rules_env['admin']
    assert client.get('/suggest-editor/rules/global', headers=dev).status_code == 403
    r = client.get('/suggest-editor/rules/global', headers=admin)
    assert r.status_code == 200
    rules = r.get_json()['rules']
    assert any('9800M12' in (rule.get('presence', [{}])[0].get('triggers', ['']) or [''])[0]
               for rule in rules if rule.get('presence'))
    # 播种幂等：再 GET 内容一致
    assert client.get('/suggest-editor/rules/global', headers=admin).get_json() == {'rules': rules}


def test_global_rules_put_roundtrip_and_reject_bad(rules_env):
    client, admin = rules_env['client'], rules_env['admin']
    r = client.put('/suggest-editor/rules/global', headers=admin, json={'rules': [SAMPLE_RULE]})
    assert r.get_json() == {'ok': True, 'count': 1}
    assert client.get('/suggest-editor/rules/global', headers=admin).get_json()['rules'] == [SAMPLE_RULE]
    bad = dict(SAMPLE_RULE, patterns=[{'extract': '(', 'valid': 'x', 'message': 'm'}])
    r = client.put('/suggest-editor/rules/global', headers=admin, json={'rules': [bad]})
    assert r.status_code == 400 and '正则' in r.get_json()['error']


def test_global_rules_import_export(rules_env):
    client, admin = rules_env['client'], rules_env['admin']
    client.put('/suggest-editor/rules/global', headers=admin, json={'rules': [SAMPLE_RULE]})
    csv_text = ('type,name,severity,enabled,wrong,right\n'
                'map,另一个纠错,warn,true,X-1,X1\n')
    r = client.post('/suggest-editor/rules/global/import', headers=admin,
                    data={'mode': 'append', 'file': (io.BytesIO(csv_text.encode()), 'r.csv')},
                    content_type='multipart/form-data')
    assert r.get_json() == {'ok': True, 'added': 1, 'skipped': 0, 'total': 2}
    r = client.get('/suggest-editor/rules/global/export', headers=admin)
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    assert 'type,name,severity' in r.data.decode('utf-8')


def test_document_rules_owner_scope(rules_env):
    client, env = rules_env['client'], rules_env
    r = client.post('/suggest-editor/documents', headers=env['dev'], json={'title': '规则文档'})
    doc_id = r.get_json()['document']['id']
    assert client.put(f'/suggest-editor/documents/{doc_id}/rules', headers=env['dev'],
                      json={'rules': [SAMPLE_RULE]}).get_json() == {'ok': True, 'count': 1}
    assert client.get(f'/suggest-editor/documents/{doc_id}/rules',
                      headers=env['dev']).get_json()['rules'] == [SAMPLE_RULE]
    # 非属主（含 admin）404
    assert client.get(f'/suggest-editor/documents/{doc_id}/rules',
                      headers=env['admin']).status_code == 404
    assert client.put(f'/suggest-editor/documents/{doc_id}/rules', headers=env['admin'],
                      json={'rules': [SAMPLE_RULE]}).status_code == 404


def test_rules_file_written_at_expected_paths(rules_env):
    from routes.suggest_editor import SE_DATA_DIR, _doc_rules_path, _global_rules_path
    client, env = rules_env['client'], rules_env
    client.put('/suggest-editor/rules/global', headers=env['admin'], json={'rules': [SAMPLE_RULE]})
    assert os.path.isfile(_global_rules_path())
    r = client.post('/suggest-editor/documents', headers=env['dev'], json={'title': '路径'})
    doc_id = r.get_json()['document']['id']
    client.put(f'/suggest-editor/documents/{doc_id}/rules', headers=env['dev'], json={'rules': [SAMPLE_RULE]})
    assert os.path.isfile(_doc_rules_path(doc_id))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_suggest_editor_rules_routes.py -q`
Expected: FAIL (404 / missing helpers)

- [ ] **Step 3: Implement endpoint section in `server/routes/suggest_editor.py`**

在词表段之后追加（复用现有 helpers；`_doc_rules_path` 与 `_doc_vocab_path` 同构）：

```python
# ------------------------------- 检查规则（全局 + 文档级） -------------------------------
# 真源为 JSON 文件（全局 .data/规则-全局.json、文档 ws/<id>/规则.json），Node 分析前
# 读 aiPrompt、前端 REST 拉取编译本地规则。校验/CSV 解析见 utils/suggest_editor_rules。

DEFAULT_RULES = {
    'rules': [{
        'id': 'rule-9800ups-drag', 'name': '9800UPS 拖带完整性', 'enabled': True,
        'severity': 'warn',
        'presence': [{
            'triggers': ['9800M12'],
            'requiresAny': ['9800UPS', '不涉及拖带', '无需拖带', '不拖带'],
            'message': '方案涉及 9800M12，但未提及 9800UPS（可拖带 9800M12），也未标识「不涉及拖带」',
        }],
        'aiPrompt': '检查设备配套与拖带声明：涉及 9800M12 时须写明 9800UPS 或明确不涉及拖带。',
    }],
}


def _global_rules_path() -> str:
    return _vocab_safe_path(os.path.join(SE_DATA_DIR, '规则-全局.json'))


def _doc_rules_path(doc_id: str) -> str | None:
    if not _DOC_ID_RE.fullmatch(doc_id):
        return None
    try:
        return _vocab_safe_path(os.path.join(SE_DATA_DIR, 'ws', doc_id, '规则.json'))
    except PermissionError:
        return None


def _read_rules_file(path: str) -> list:
    raw = _read_vocab_file(path)
    if not raw.strip():
        return []
    try:
        import json
        payload = json.loads(raw)
    except ValueError:
        raise RuleValidationError('规则文件不是合法 JSON，请通过导入覆盖修复')
    return validate_rules(payload)


def _write_rules_file(path: str, rules: list) -> None:
    import json
    _write_vocab_file(path, json.dumps({'rules': rules}, ensure_ascii=False, indent=2) + '\n')


def _rules_export(rules: list, filename: str):
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for rule in rules:
        row = {'type': '', 'name': rule.get('name', ''), 'severity': rule.get('severity', 'warn'),
               'enabled': 'true' if rule.get('enabled', True) else 'false',
               'aiPrompt': rule.get('aiPrompt', '')}
        if rule.get('maps'):
            row.update(type='map', wrong=rule['maps'][0]['wrong'], right=rule['maps'][0]['right'])
        elif rule.get('patterns'):
            p = rule['patterns'][0]
            row.update(type='pattern', extract=p['extract'], valid=p['valid'], message=p['message'])
        elif rule.get('whitelists'):
            w = rule['whitelists'][0]
            row.update(type='whitelist', anchors='|'.join(w['anchors']),
                       values='|'.join(w['values']), message=w['message'])
        elif rule.get('presence'):
            pr = rule['presence'][0]
            row.update(type='presence', triggers='|'.join(pr['triggers']),
                       requiresAny='|'.join(pr['requiresAny']), message=pr['message'])
        else:
            row['type'] = 'ai'
        writer.writerow(row)
    quoted = urllib.parse.quote(filename)
    return Response(buf.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quoted}"})


def _rules_import(path, mode):
    text, err = _decode_vocab_upload()
    if err:
        return jsonify({'error': err}), 400
    if text is None:
        return jsonify({'error': 'file required'}), 400
    try:
        incoming = parse_rules_csv(text)
    except RuleValidationError as e:
        return jsonify({'error': str(e)}), 400
    try:
        existing = _read_rules_file(path)
        merged, added, skipped = merge_rules(existing, incoming, mode)
        _write_rules_file(path, merged)
    except RuleValidationError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'added': added, 'skipped': skipped, 'total': len(merged)})


@suggest_editor_bp.route('/rules/global', methods=['GET'])
@require_permission('admin.suggest_editor')
def get_global_rules():
    path = _global_rules_path()
    try:
        rules = _read_rules_file(path)
    except RuleValidationError as e:
        return jsonify({'error': str(e)}), 500
    if not rules:
        rules = validate_rules(DEFAULT_RULES)
        _write_rules_file(path, rules)
    return jsonify({'rules': rules})


@suggest_editor_bp.route('/rules/global', methods=['PUT'])
@require_permission('admin.suggest_editor')
def put_global_rules():
    body = request.get_json(silent=True) or {}
    try:
        rules = validate_rules(body)
    except RuleValidationError as e:
        return jsonify({'error': str(e)}), 400
    _write_rules_file(_global_rules_path(), rules)
    return jsonify({'ok': True, 'count': len(rules)})


@suggest_editor_bp.route('/rules/global/import', methods=['POST'])
@require_permission('admin.suggest_editor')
def import_global_rules():
    return _rules_import(_global_rules_path(), (request.form.get('mode') or 'append').strip())


@suggest_editor_bp.route('/rules/global/export', methods=['GET'])
@require_permission_sse('admin.suggest_editor')
def export_global_rules():
    try:
        return _rules_export(_read_rules_file(_global_rules_path()), '规则-全局.csv')
    except RuleValidationError as e:
        return jsonify({'error': str(e)}), 500


def _owned_doc_rules_path(doc_id: str) -> str | None:
    if not _get_own_document(doc_id):
        return None
    return _doc_rules_path(doc_id)


@suggest_editor_bp.route('/documents/<doc_id>/rules', methods=['GET'])
@login_required
def get_document_rules(doc_id: str):
    path = _owned_doc_rules_path(doc_id)
    if path is None:
        return jsonify({'error': '文档不存在'}), 404
    try:
        return jsonify({'rules': _read_rules_file(path)})
    except RuleValidationError as e:
        return jsonify({'error': str(e)}), 500


@suggest_editor_bp.route('/documents/<doc_id>/rules', methods=['PUT'])
@write_required
def put_document_rules(doc_id: str):
    path = _owned_doc_rules_path(doc_id)
    if path is None:
        return jsonify({'error': '文档不存在'}), 404
    body = request.get_json(silent=True) or {}
    try:
        rules = validate_rules(body)
    except RuleValidationError as e:
        return jsonify({'error': str(e)}), 400
    _write_rules_file(path, rules)
    return jsonify({'ok': True, 'count': len(rules)})


@suggest_editor_bp.route('/documents/<doc_id>/rules/import', methods=['POST'])
@write_required
def import_document_rules(doc_id: str):
    path = _owned_doc_rules_path(doc_id)
    if path is None:
        return jsonify({'error': '文档不存在'}), 404
    return _rules_import(path, (request.form.get('mode') or 'append').strip())


@suggest_editor_bp.route('/documents/<doc_id>/rules/export', methods=['GET'])
@login_required_sse
def export_document_rules(doc_id: str):
    path = _owned_doc_rules_path(doc_id)
    if path is None:
        return jsonify({'error': '文档不存在'}), 404
    try:
        return _rules_export(_read_rules_file(path), '规则.csv')
    except RuleValidationError as e:
        return jsonify({'error': str(e)}), 500
```

并在文件头部补充导入：

```python
from utils.suggest_editor_rules import (
    CSV_COLUMNS, RuleValidationError, merge_rules, parse_rules_csv, validate_rules,
)
```

- [ ] **Step 4: Run tests**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_suggest_editor_rules_routes.py tests/test_suggest_editor_routes.py -q`
Expected: PASS（rules 5 + 既有 10）

- [ ] **Step 5: Commit**

```bash
git add server/routes/suggest_editor.py server/tests/test_suggest_editor_rules_routes.py
git commit -m "feat(se): 检查规则端点——全局(admin)/文档级(属主) CRUD+CSV 导入导出+默认示例播种"
```

---

### Task 3: 前端规则 API + 类型

**Files:**
- Modify: `src/api/suggestEditor.ts`（追加规则段）
- Test: `src/api/__tests__/suggestEditor.test.ts`（追加 describe）

**Interfaces:**
- Produces:
  - `export interface LocalCheckRule`（与 spec §3 一致）
  - `getRules(scope: VocabularyScope): Promise<{rules: LocalCheckRule[]}>`
  - `saveRules(scope, rules): Promise<{ok: boolean; count: number}>`
  - `importRules(scope, file, mode): Promise<{ok,added,skipped,total}>`
  - `rulesExportUrl(scope): string`

- [ ] **Step 1: Write the failing test**（追加到现有文件末尾）

```ts
describe('suggestEditor rules api', () => {
  it('global scope get/save hit /suggest-editor/rules/global', async () => {
    await getRules({ kind: 'global' })
    expect(get).toHaveBeenCalledWith('/suggest-editor/rules/global')
    await saveRules({ kind: 'global' }, [])
    expect(put).toHaveBeenCalledWith('/suggest-editor/rules/global', { rules: [] })
  })

  it('document scope paths embed docId; import posts multipart; export url shaped', async () => {
    await getRules({ kind: 'document', docId: 'doc-1' })
    expect(get).toHaveBeenCalledWith('/suggest-editor/documents/doc-1/rules')
    const file = new File(['x'], 'rules.csv', { type: 'text/csv' })
    await importRules({ kind: 'document', docId: 'doc-1' }, file, 'replace')
    const [url, form] = vi.mocked(post).mock.calls.at(-1)!
    expect(url).toBe('/suggest-editor/documents/doc-1/rules/import')
    expect((form as FormData).get('mode')).toBe('replace')
    expect(rulesExportUrl({ kind: 'global' }))
      .toBe('/api/suggest-editor/rules/global/export?access_token=tok')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/api/__tests__/suggestEditor.test.ts`
Expected: FAIL（`getRules` 未导出）

- [ ] **Step 3: Implement**

```ts
/* ------------------------- 检查规则（全局 + 文档级） ------------------------- */

export interface LocalCheckRule {
  id: string
  name: string
  enabled: boolean
  severity: 'info' | 'warn' | 'error'
  aiPrompt?: string
  maps?: { wrong: string; right: string; caseSensitive?: boolean }[]
  patterns?: { extract: string; valid: string; message: string; flags?: string }[]
  whitelists?: { anchors: string[]; values: string[]; valuePattern?: string; message: string }[]
  presence?: { triggers: string[]; requiresAny: string[]; message: string }[]
}

function rulesBase(scope: VocabularyScope): string {
  return scope.kind === 'global'
    ? '/suggest-editor/rules/global'
    : `/suggest-editor/documents/${scope.docId}/rules`
}

export function getRules(scope: VocabularyScope) {
  return get<{ rules: LocalCheckRule[] }>(rulesBase(scope))
}

export function saveRules(scope: VocabularyScope, rules: LocalCheckRule[]) {
  return put<{ ok: boolean; count: number }>(rulesBase(scope), { rules })
}

export function importRules(scope: VocabularyScope, file: File, mode: 'append' | 'replace') {
  const form = new FormData()
  form.append('mode', mode)
  form.append('file', file)
  return post<{ ok: boolean; added: number; skipped: number; total: number }>(
    `${rulesBase(scope)}/import`, form, { headers: { 'Content-Type': 'multipart/form-data' } },
  )
}

export function rulesExportUrl(scope: VocabularyScope): string {
  return `/api${rulesBase(scope)}/export${authParam()}`
}
```

测试文件头部 import 追加 `getRules, saveRules, importRules, rulesExportUrl`。

- [ ] **Step 4: Run test**

Run: `npx vitest run src/api/__tests__/suggestEditor.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/suggestEditor.ts src/api/__tests__/suggestEditor.test.ts
git commit -m "feat(se): 前端检查规则 API（全局/文档级 CRUD+导入+导出链接）"
```

---

### Task 4: 本地规则编译与匹配（core/check-rules.ts）

**Files:**
- Create: `src/components/suggest-editor/core/check-rules.ts`
- Test: `src/components/suggest-editor/core/__tests__/check-rules.test.ts`

**Interfaces:**
- Consumes: `LocalCheckRule`（Task 3）；ProseMirror `Node`
- Produces:
  - `export interface RuleMatch { id: string; ruleId: string; ruleName: string; severity: 'info'|'warn'|'error'; reason: string; from: number; to: number; text: string; replacement: string | null; notice: boolean }`
  - `export function compileRules(rules: LocalCheckRule[]): CompiledRules`
  - `export function runCheckRules(doc: Node, compiled: CompiledRules): RuleMatch[]`

测试用 Tiptap Editor 构造 doc：

```ts
import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { compileRules, runCheckRules, type LocalCheckRuleInput } from '../check-rules'

function docOf(html: string) {
  const editor = new Editor({ extensions: [StarterKit], content: html })
  const doc = editor.state.doc
  editor.destroy()
  return doc
}
const R = (r: Partial<LocalCheckRuleInput>): LocalCheckRuleInput =>
  ({ id: 'r', name: '规则', enabled: true, severity: 'warn', ...r } as LocalCheckRuleInput)

describe('maps', () => {
  it('命中错误写法，replacement=正确写法，可接受（notice=false）', () => {
    const m = runCheckRules(docOf('<p>设备 S-5700 上线</p>'),
      compileRules([R({ maps: [{ wrong: 'S-5700', right: 'S5700' }] })]))
    expect(m).toHaveLength(1)
    expect(m[0]).toMatchObject({ ruleId: 'r', replacement: 'S5700', notice: false, text: 'S-5700' })
    expect(m[0].reason).toContain('[规则]')
  })
  it('大小写敏感开关', () => {
    const rule = R({ id: 'c', maps: [{ wrong: 's-5700', right: 'S5700', caseSensitive: true }] })
    expect(runCheckRules(docOf('<p>S-5700</p>'), compileRules([rule]))).toHaveLength(0)
  })
})

describe('patterns', () => {
  it('提取候选但不合规 → notice', () => {
    const rule = R({ id: 'p', patterns: [{ extract: '[A-Z]{2,4}-?\\d{3,4}', valid: '^S\\d{4}$', message: '型号格式' }] })
    const m = runCheckRules(docOf('<p>单板 AB-123 与 S6700</p>'), compileRules([rule]))
    expect(m).toHaveLength(1)
    expect(m[0].text).toBe('AB-123')
    expect(m[0].notice).toBe(true)
    expect(m[0].replacement).toBeNull()
  })
})

describe('whitelists', () => {
  it('锚点后的值不在白名单 → notice；在白名单 → 无', () => {
    const rule = R({ id: 'w', whitelists: [{ anchors: ['设备类型：', '设备类型:'],
      values: ['S5700', 'S6700'], message: '不在清单' }] })
    const compiled = compileRules([rule])
    expect(runCheckRules(docOf('<p>设备类型：S9999</p>'), compiled)).toHaveLength(1)
    expect(runCheckRules(docOf('<p>设备类型：S5700</p>'), compiled)).toHaveLength(0)
  })
  it('锚点与值之间允许空格', () => {
    const rule = R({ id: 'w2', whitelists: [{ anchors: ['型号：'], values: ['A1'], message: 'm' }] })
    expect(runCheckRules(docOf('<p>型号：  B2</p>'), compileRules([rule]))).toHaveLength(1)
  })
})

describe('presence', () => {
  const rule = R({ id: 'pc', presence: [{ triggers: ['9800M12'],
    requiresAny: ['9800UPS', '不涉及拖带'], message: '缺拖带说明' }] })
  it('触发且无满足项 → notice 锚定触发词', () => {
    const m = runCheckRules(docOf('<p>本方案涉及 9800M12 设备。</p>'), compileRules([rule]))
    expect(m).toHaveLength(1)
    expect(m[0]).toMatchObject({ notice: true, text: '9800M12' })
  })
  it('补任一满足项 → 无提示', () => {
    expect(runCheckRules(docOf('<p>涉及 9800M12 与 9800UPS。</p>'), compileRules([rule]))).toHaveLength(0)
    expect(runCheckRules(docOf('<p>涉及 9800M12，不涉及拖带。</p>'), compileRules([rule]))).toHaveLength(0)
  })
  it('多段落也能跨段落判定', () => {
    const m = runCheckRules(docOf('<p>9800M12</p><p>其它说明</p>'), compileRules([rule]))
    expect(m).toHaveLength(1)
  })
})
```

- [ ] **Step 1: 写失败测试**（上述文件）
- [ ] **Step 2: 运行确认失败**：`npx vitest run src/components/suggest-editor/core/__tests__/check-rules.test.ts` → FAIL（模块不存在）
- [ ] **Step 3: 实现**

```ts
/**
 * 检查规则本地引擎：映射 / 正则提取校验 / 锚定白名单 / 文档级条件完整性。
 * 纯函数：compileRules 一次性编译（挂载/规则变更时），runCheckRules 每次编辑调用。
 * 约束：文本内匹配限单个文本节点；presence 用全文纯文本；全文 >500k 字符跳过 presence。
 */
import type { Node as ProseMirrorNode } from '@tiptap/pm/model'

export type CheckSeverity = 'info' | 'warn' | 'error'

export interface LocalCheckRuleInput {
  id: string
  name: string
  enabled: boolean
  severity: CheckSeverity
  aiPrompt?: string
  maps?: { wrong: string; right: string; caseSensitive?: boolean }[]
  patterns?: { extract: string; valid: string; message: string; flags?: string }[]
  whitelists?: { anchors: string[]; values: string[]; valuePattern?: string; message: string }[]
  presence?: { triggers: string[]; requiresAny: string[]; message: string }[]
}

export interface RuleMatch {
  id: string
  ruleId: string
  ruleName: string
  severity: CheckSeverity
  reason: string
  from: number
  to: number
  text: string
  replacement: string | null
  notice: boolean
}

export interface CompiledRules {
  maps: Array<{
    rule: LocalCheckRuleInput
    regex: RegExp
    byLower: Map<string, { wrong: string; right: string }>
  }>
  patterns: Array<{ rule: LocalCheckRuleInput; extract: RegExp; valid: RegExp; message: string }>
  whitelists: Array<{ rule: LocalCheckRuleInput; anchors: string[]; values: Set<string>;
    valueRegex: RegExp; message: string }>
  presence: Array<{ rule: LocalCheckRuleInput; triggers: string[]; requiresAny: string[];
    message: string }>
}

const PRESENCE_MAX_CHARS = 500_000
const DEFAULT_VALUE_PATTERN = '[A-Za-z0-9_\\-./]+'

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function compileRules(rules: LocalCheckRuleInput[]): CompiledRules {
  const compiled: CompiledRules = { maps: [], patterns: [], whitelists: [], presence: [] }
  for (const rule of rules) {
    if (!rule.enabled) continue
    if (rule.maps?.length) {
      // 分组：大小写敏感/不敏感分别合并为 alternation，一次扫描
      for (const caseSensitive of [false, true]) {
        const items = rule.maps.filter((m) => (m.caseSensitive ?? false) === caseSensitive)
        if (!items.length) continue
        const byLower = new Map(items.map((m) => [m.wrong.toLowerCase(), m]))
        try {
          const source = items.map((m) => escapeRegex(m.wrong)).join('|')
          compiled.maps.push({ rule, regex: new RegExp(source, caseSensitive ? 'g' : 'gi'), byLower })
        } catch { /* 单条跳过 */ }
      }
    }
    for (const p of rule.patterns ?? []) {
      try {
        compiled.patterns.push({
          rule, extract: new RegExp(p.extract, (p.flags ?? '') + 'g'),
          valid: new RegExp(p.valid), message: p.message,
        })
      } catch { /* 跳过坏正则 */ }
    }
    for (const w of rule.whitelists ?? []) {
      try {
        compiled.whitelists.push({
          rule, anchors: w.anchors, values: new Set(w.values),
          valueRegex: new RegExp(w.valuePattern ?? DEFAULT_VALUE_PATTERN, 'y'), message: w.message,
        })
      } catch { /* 跳过 */ }
    }
    for (const pr of rule.presence ?? []) {
      compiled.presence.push({ rule, triggers: pr.triggers, requiresAny: pr.requiresAny, message: pr.message })
    }
  }
  return compiled
}

function matchInTextNode(
  node: ProseMirrorNode, pos: number, compiled: CompiledRules, out: RuleMatch[],
): void {
  const text = node.text ?? ''
  if (!text) return
  const push = (rule: LocalCheckRuleInput, from: number, to: number, replacement: string | null,
                message: string, notice: boolean): void => {
    out.push({
      id: `rule:${rule.id}:${from}`, ruleId: rule.id, ruleName: rule.name,
      severity: rule.severity, reason: `[${rule.name}] ${message}`,
      from: pos + from, to: pos + to, text: text.slice(from, to),
      replacement, notice,
    })
  }
  for (const entry of compiled.maps) {
    entry.regex.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = entry.regex.exec(text)) !== null) {
      const hit = entry.byLower.get(m[0].toLowerCase())
      if (hit) push(entry.rule, m.index, m.index + m[0].length, hit.right, '请使用规范写法', false)
      if (m[0].length === 0) entry.regex.lastIndex += 1
    }
  }
  for (const entry of compiled.patterns) {
    entry.extract.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = entry.extract.exec(text)) !== null) {
      if (!entry.valid.test(m[0])) push(entry.rule, m.index, m.index + m[0].length, null, entry.message, true)
      if (m[0].length === 0) entry.extract.lastIndex += 1
    }
  }
  for (const entry of compiled.whitelists) {
    for (const anchor of entry.anchors) {
      let at = text.indexOf(anchor)
      while (at !== -1) {
        let cursor = at + anchor.length
        while (cursor < text.length && (text[cursor] === ' ' || text[cursor] === '\t')) cursor += 1
        entry.valueRegex.lastIndex = cursor
        const m = entry.valueRegex.exec(text)
        if (m && !entry.values.has(m[0])) {
          push(entry.rule, m.index, m.index + m[0].length, null, entry.message, true)
        }
        at = text.indexOf(anchor, at + anchor.length)
      }
    }
  }
}

function findFirstOccurrence(
  doc: ProseMirrorNode, term: string,
): { from: number; to: number } | null {
  const lower = term.toLowerCase()
  let found: { from: number; to: number } | null = null
  doc.descendants((node, pos) => {
    if (found || !node.isText || !node.text) return !found
    const at = node.text.toLowerCase().indexOf(lower)
    if (at >= 0) found = { from: pos + at, to: pos + at + term.length }
    return !found
  })
  return found
}

export function runCheckRules(doc: ProseMirrorNode, compiled: CompiledRules): RuleMatch[] {
  const matches: RuleMatch[] = []
  doc.descendants((node, pos) => {
    if (node.isText) matchInTextNode(node, pos, compiled, matches)
    return true
  })
  if (!compiled.presence.length) return matches
  const fullText = doc.textBetween(0, doc.content.size, '\n')
  if (fullText.length > PRESENCE_MAX_CHARS) return matches
  const lower = fullText.toLowerCase()
  for (const entry of compiled.presence) {
    const triggered = entry.triggers.some((t) => lower.includes(t.toLowerCase()))
    if (!triggered) continue
    const satisfied = entry.requiresAny.some((t) => lower.includes(t.toLowerCase()))
    if (satisfied) continue
    const trigger = entry.triggers.find((t) => lower.includes(t.toLowerCase())) ?? entry.triggers[0]
    const spot = findFirstOccurrence(doc, trigger)
    if (!spot) continue
    matches.push({
      id: `rule:${entry.rule.id}:presence`, ruleId: entry.rule.id, ruleName: entry.rule.name,
      severity: entry.rule.severity, reason: `[${entry.rule.name}] ${entry.message}`,
      from: spot.from, to: spot.to, text: trigger, replacement: null, notice: true,
    })
  }
  return matches
}
```

- [ ] **Step 4: 运行测试** → PASS（8 例）
- [ ] **Step 5: Commit**

```bash
git add src/components/suggest-editor/core/check-rules.ts src/components/suggest-editor/core/__tests__/check-rules.test.ts
git commit -m "feat(se): 本地检查规则引擎（映射/提取校验/锚定白名单/presence）"
```

---

### Task 5: 接入建议层与编辑器（notice 卡片 + 规则拉取/重载）

**Files:**
- Modify: `src/components/suggest-editor/core/suggestions.ts`
- Modify: `src/components/suggest-editor/core/local-rules.ts`（仅类型复用，若无需改动则跳过）
- Modify: `src/components/suggest-editor/SuggestionNote.vue`
- Modify: `src/components/suggest-editor/SuggestEditorCore.vue`

**Interfaces:**
- Consumes: `compileRules/runCheckRules/RuleMatch`（Task 4）、`getRules/LocalCheckRule`（Task 3）
- Produces:
  - `SuggestionItem` 增加 `ruleName?: string; notice?: boolean`
  - `createSuggestionLayers` options 增加 `getCheckRules?: () => CompiledRules | null`
  - `SuggestEditorCore` expose 增加 `reloadRules(): Promise<void>`

- [ ] **Step 1: 扩展 suggestions.ts**

`SuggestionItem` 增加两个可选字段；`createSuggestionLayers` 签名：

```ts
export function createSuggestionLayers(options: {
  ydoc: Y.Doc
  getEditor: () => Editor | null
  onChange: (items: { ai: SuggestionItem[]; local: SuggestionItem[] }) => void
  onActiveChange?: (id: string | null) => void
  getCheckRules?: () => CompiledRules | null
}): { extension: Extension; api: SuggestionLayersApi }
```

`build()` 中在现有本地规则循环之后追加：

```ts
for (const match of runCheckRules(state.doc, getCheckRules?.() ?? null ?? { maps: [], patterns: [], whitelists: [], presence: [] })) {
  if (aiItems.some((item) => match.from < item.to && match.to > item.from)) continue
  if (localItems.some((item) => match.from < item.to && match.to > item.from)) continue
  localItems.push({
    id: match.id,
    source: 'local',
    quote: match.text,
    replacement: match.replacement ?? '',
    reason: match.reason,
    severity: match.severity,
    from: match.from,
    to: match.to,
    index: null,
    ruleName: match.ruleName,
    notice: match.notice,
  })
}
```

实现细节：`getCheckRules?.()` 返回 null 时用空 CompiledRules（导出 `EMPTY_COMPILED_RULES` 常量，避免每帧建对象）。`api.accept` 开头加：

```ts
if (item.notice) return  // 完整性提醒不可接受，只能忽略
```

- [ ] **Step 2: SuggestionNote.vue 隐藏“接受”**

```html
<button v-if="!item.notice" type="button" class="se-suggest-btn se-suggest-btn-accept" ...>接受</button>
```

- [ ] **Step 3: SuggestEditorCore 拉取与重载**

```ts
import { compileRules, type CompiledRules } from './core/check-rules'
import { getRules, type LocalCheckRule } from '@/api/suggestEditor'

const compiledRules = shallowRef<CompiledRules | null>(null)

async function loadRules(): Promise<void> {
  try {
    const [globalRules, docRules] = await Promise.all([
      getRules({ kind: 'global' }).then((r) => r.rules).catch(() => [] as LocalCheckRule[]),
      getRules({ kind: 'document', docId: props.docId }).then((r) => r.rules).catch(() => [] as LocalCheckRule[]),
    ])
    // 文档级同名 id 覆盖全局
    const byId = new Map<string, LocalCheckRule>()
    for (const rule of globalRules) byId.set(rule.id, rule)
    for (const rule of docRules) byId.set(rule.id, rule)
    compiledRules.value = compileRules([...byId.values()])
    refreshSuggestions()
  } catch (error) {
    console.warn('[suggest-editor] 检查规则加载失败，仅用内置规则：', error)
    compiledRules.value = null
  }
}

createSuggestionLayers({ ..., getCheckRules: () => compiledRules.value })
void loadRules()

defineExpose({ editor, ydoc, reloadRules: loadRules })
```

- [ ] **Step 4: 类型检查 + 现有测试**

Run: `npx vue-tsc --noEmit` → 0 errors
Run: `npx vitest run src/components/suggest-editor/core/__tests__/check-rules.test.ts src/api/__tests__/suggestEditor.test.ts` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/components/suggest-editor/core/suggestions.ts src/components/suggest-editor/SuggestionNote.vue src/components/suggest-editor/SuggestEditorCore.vue
git commit -m "feat(se): 检查规则接入建议层——毫秒级命中、notice 卡片仅可忽略、文档打开拉取/保存后重载"
```

---

### Task 6: 规则管理对话框 + 两处入口

**Files:**
- Create: `src/components/suggest-editor/RuleManagerDialog.vue`
- Modify: `src/views/suggest/SuggestEditorListView.vue`（顶部「检查规则」按钮，admin）
- Modify: `src/views/suggest/SuggestEditorDocView.vue`（顶栏「规则」按钮，属主；保存后 `coreRef.reloadRules()`）

**Interfaces:**
- Consumes: Task 3 API；`VocabularyScope` 类型复用
- Produces: `<RuleManagerDialog v-model:visible :scope @saved />`

- [ ] **Step 1: 实现对话框**

结构（Element Plus）：

```vue
<template>
  <el-dialog :model-value="visible" :title="title" width="860px"
    @update:model-value="(v:boolean)=>emit('update:visible',v)" @open="load">
    <div class="rules-bar">
      <el-button size="small" type="primary" @click="openEdit(null)">新建规则</el-button>
      <el-radio-group v-model="importMode" size="small">
        <el-radio-button value="append">追加（去重）</el-radio-button>
        <el-radio-button value="replace">覆盖</el-radio-button>
      </el-radio-group>
      <el-button size="small" :loading="importing" @click="fileInput?.click()">导入 CSV…</el-button>
      <input ref="fileInput" type="file" accept=".csv,.txt,text/csv" style="display:none" @change="onFile" />
      <a :href="rulesExportUrl(props.scope)"><el-button size="small">导出 CSV</el-button></a>
      <el-button size="small" @click="downloadTemplate">下载模板</el-button>
      <span class="rules-spacer" />
      <el-button size="small" @click="emit('update:visible', false)">关闭</el-button>
    </div>
    <el-table :data="rules" size="small" max-height="420">
      <el-table-column prop="name" label="名称" width="160" />
      <el-table-column label="严重度" width="80"><template #default="{row}">{{ SEV[row.severity] }}</template></el-table-column>
      <el-table-column label="启用" width="70"><template #default="{row}">{{ row.enabled ? '是' : '否' }}</template></el-table-column>
      <el-table-column label="判定" min-width="220"><template #default="{row}">{{ summary(row) }}</template></el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{row}">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="editVisible" :title="draft.id ? '编辑规则' : '新建规则'" width="640px" append-to-body>
      <el-form label-width="90px">
        <el-form-item label="名称"><el-input v-model="draft.name" maxlength="100" /></el-form-item>
        <el-form-item label="严重度">
          <el-select v-model="draft.severity" style="width:120px">
            <el-option value="info" label="提示" /><el-option value="warn" label="建议" /><el-option value="error" label="错误" />
          </el-select>
          <el-switch v-model="draft.enabled" active-text="启用" style="margin-left:16px" />
        </el-form-item>
        <el-form-item label="AI 说明"><el-input v-model="draft.aiPrompt" type="textarea" :rows="2" maxlength="500"
          placeholder="可选：注入 AI 分析的语义核对说明" /></el-form-item>
        <el-divider content-position="left">本地判定（可组合，至少填一项）</el-divider>
        <!-- maps -->
        <el-form-item label="错误映射">
          <div class="rule-rows">
            <div v-for="(m,i) in draft.maps" :key="i" class="rule-row">
              <el-input v-model="m.wrong" placeholder="错误写法" style="width:180px" />
              <span>→</span>
              <el-input v-model="m.right" placeholder="正确写法" style="width:180px" />
              <el-checkbox v-model="m.caseSensitive">区分大小写</el-checkbox>
              <el-button link type="danger" @click="draft.maps.splice(i,1)">删</el-button>
            </div>
            <el-button size="small" @click="draft.maps.push({ wrong:'', right:'' })">+ 映射</el-button>
          </div>
        </el-form-item>
        <!-- patterns / whitelists / presence 同构行编辑器（见实现） -->
        ...
      </el-form>
      <template #footer>
        <el-button @click="editVisible=false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveDraft">确定</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>
```

Script 要点（完整实现）：

```ts
const props = defineProps<{ visible: boolean; scope: VocabularyScope }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void; (e: 'saved'): void }>()

const rules = ref<LocalCheckRule[]>([])
const editVisible = ref(false)
const draft = ref<LocalCheckRule>(blankRule())
const saving = ref(false)
const importing = ref(false)
const importMode = ref<'append' | 'replace'>('append')
const fileInput = ref<HTMLInputElement | null>(null)
const title = computed(() => props.scope.kind === 'global'
  ? '检查规则（全局，对所有文档生效）' : '检查规则（仅本文档生效）')

function blankRule(): LocalCheckRule {
  return { id: `rule-${crypto.randomUUID().slice(0, 12)}`, name: '', enabled: true,
           severity: 'warn', maps: [], patterns: [], whitelists: [], presence: [] }
}
function summary(rule: LocalCheckRule): string {
  const parts: string[] = []
  if (rule.maps?.length) parts.push(`映射 ${rule.maps.length}`)
  if (rule.patterns?.length) parts.push(`正则 ${rule.patterns.length}`)
  if (rule.whitelists?.length) parts.push(`白名单 ${rule.whitelists.length}`)
  if (rule.presence?.length) parts.push(`完整性 ${rule.presence.length}`)
  if (rule.aiPrompt) parts.push('AI')
  return parts.join(' · ') || '—'
}
async function load(): Promise<void> {
  rules.value = (await getRules(props.scope)).rules
}
function saveDraft(): void {
  // 去空行 + 必填校验（名称、各机制内必填、正则可编译）——校验失败 ElMessage.warning 并 return
  const cleaned = cleanDraft(draft.value)
  if (!cleaned) return
  const index = rules.value.findIndex((r) => r.id === cleaned.id)
  if (index >= 0) rules.value.splice(index, 1, cleaned)
  else rules.value.push(cleaned)
  editVisible.value = false
  void persist()
}
async function persist(): Promise<void> {
  saving.value = true
  try {
    await saveRules(props.scope, rules.value)
    ElMessage.success('规则已保存')
    emit('saved')
  } finally { saving.value = false }
}
async function remove(rule: LocalCheckRule): Promise<void> {
  rules.value = rules.value.filter((r) => r.id !== rule.id)
  await persist()
}
async function onFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  importing.value = true
  try {
    const r = await importRules(props.scope, file, importMode.value)
    ElMessage.success(`导入完成：新增 ${r.added}，跳过 ${r.skipped}，共 ${r.total} 条`)
    await load()
    emit('saved')
  } finally { importing.value = false }
}
function downloadTemplate(): void {
  const csv = 'type,name,severity,enabled,wrong,right,extract,valid,anchors,values,triggers,requiresAny,message,aiPrompt\n'
    + 'map,型号纠错,error,true,S-5700,S5700,,,,,,,,,\n'
    + 'presence,拖带完整性,warn,true,,,,,,,9800M12,9800UPS|不涉及拖带,缺拖带说明,\n'
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url; a.download = '规则模板.csv'; a.click()
  URL.revokeObjectURL(url)
}
```

`cleanDraft`：过滤空行；`name` 必填；至少一种判定或 aiPrompt；每个正则 `try { new RegExp(...) } catch { warn; return null }`；whitelist 的 values 用多行文本 `split('\n')`（表单内 values 用换行文本编辑）。

- [ ] **Step 2: 列表页入口**

`SuggestEditorListView.vue` 顶部操作区新增（admin 可见）：

```html
<el-button v-if="auth.can('admin.suggest_editor')" @click="rulesVisible = true">检查规则</el-button>
...
<RuleManagerDialog v-model:visible="rulesVisible" :scope="{ kind: 'global' }" />
```

- [ ] **Step 3: 编辑页入口 + 保存后重载**

`SuggestEditorDocView.vue` actions 区新增：

```html
<el-button size="small" @click="rulesVisible = true">规则</el-button>
...
<RuleManagerDialog v-model:visible="rulesVisible"
  :scope="doc ? { kind: 'document', docId: doc.id } : { kind: 'global' }"
  @saved="coreRef?.reloadRules()" />
```

- [ ] **Step 4: 类型检查**

Run: `npx vue-tsc --noEmit` → 0 errors

- [ ] **Step 5: Commit**

```bash
git add src/components/suggest-editor/RuleManagerDialog.vue src/views/suggest/SuggestEditorListView.vue src/views/suggest/SuggestEditorDocView.vue
git commit -m "feat(se): 检查规则管理对话框——表格编辑+CSV 导入导出+模板下载，列表页(全局)/编辑页(本文档)入口"
```

---

### Task 7: E2E（映射毫秒级 + presence 实时消失 + 文档级隔离）

**Files:**
- Create: `e2e/suggest-editor-rules.spec.ts`

复用 `suggest-editor.spec.ts` 的 beforeAll/beforeEach/gotoSuggestList 模式（复制而非 import，保持 spec 独立）。

- [ ] **Step 1: 写用例**

```ts
test('映射规则毫秒级命中并接受；presence 提示随豁免词消失；文档级隔离', async ({ page }) => {
  test.setTimeout(180_000)
  const ts = Date.now()

  // 0) 全局规则：重置为可控集合（经 API 直连 3002，避免 UI 长流程），只留一条映射
  await page.request.put('http://127.0.0.1:3002/suggest-editor/rules/global', {
    headers: { Authorization: `Bearer ${token}` },  // token 从 localStorage 读
    data: { rules: [
      { id: 'e2e-map', name: 'E2E 型号纠错', enabled: true, severity: 'error',
        maps: [{ wrong: 'S-5700', right: 'S5700' }] },
    ] },
  })

  // 1) 列表页 → 文档级规则：用 UI 建 presence 规则（验证对话框）
  await gotoSuggestList(page)
  await page.getByRole('button', { name: '新建文档' }).click()
  ...创建文档 `${title}` 并进入编辑器...
  await page.getByRole('button', { name: '规则' }).click()
  await page.getByRole('button', { name: '新建规则' }).click()
  // 表单：名称 + presence 三组词
  const form = page.locator('.el-dialog', { hasText: '新建规则' })
  await form.getByPlaceholder(/规则名/).fill('E2E 拖带完整性')
  await form.getByRole('button', { name: '+ 完整性' }).click()
  await form.locator('[data-field="triggers"]').fill('9800M12')
  await form.locator('[data-field="requiresAny"]').fill('9800UPS\n不涉及拖带')
  await form.locator('[data-field="message"]').fill('缺拖带说明')
  await form.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.el-message', { hasText: '规则已保存' })).toBeVisible()
  await page.locator('.el-dialog', { hasText: '检查规则' }).getByRole('button', { name: '关闭', exact: true }).click()

  // 2) 映射规则：输入错误写法 → 本地卡片即时出现（≤3s）→ 接受替换
  await page.locator('.doc-surface').click()
  await page.keyboard.type('设备 S-5700 与 9800M12 组网。', { delay: 20 })
  const mapCard = page.locator('.se-suggest-note', { hasText: 'E2E 型号纠错' })
  await expect(mapCard).toBeVisible({ timeout: 5_000 })
  await mapCard.getByRole('button', { name: '接受' }).click()
  await expect(page.locator('.doc-surface')).toContainText('S5700')
  await expect(page.locator('.doc-surface')).not.toContainText('S-5700')

  // 3) presence：出现 notice（无接受按钮）→ 补豁免词 → 自动消失
  const presenceCard = page.locator('.se-suggest-note', { hasText: 'E2E 拖带完整性' })
  await expect(presenceCard).toBeVisible({ timeout: 5_000 })
  await expect(presenceCard.getByRole('button', { name: '接受' })).toHaveCount(0)
  await page.screenshot({ path: 'e2e/screenshots/suggest-editor-rules-presence.png' })
  await page.locator('.doc-surface').click()
  await page.keyboard.press('Control+End')
  await page.keyboard.type('本方案不涉及拖带。', { delay: 20 })
  await expect(presenceCard).toHaveCount(0, { timeout: 5_000 })

  // 4) 隔离：另一文档不出现该 presence 提示（全局映射仍生效）
  await gotoSuggestList(page)
  建第二个文档 → 输入含 9800M12 文本 → expect presenceCard count 0

  // 5) 清理：删除两个文档；全局规则恢复默认（经 API PUT 回 9800 示例）
})
```

注：测试读取 token 的方式：

```ts
const token = await page.evaluate(() => JSON.parse(localStorage.getItem('check-manage:token') || '""'))
```

- [ ] **Step 2: 运行（dev mock 栈）**

Run（先停生产栈，确保 5173/3002/1234 由 `npm run dev:all` 提供）：
`npx playwright test e2e/suggest-editor-rules.spec.ts e2e/suggest-editor-layout.spec.ts e2e/suggest-editor.spec.ts`
Expected: 全部 PASS（4 用例）

- [ ] **Step 3: Commit**

```bash
git add e2e/suggest-editor-rules.spec.ts e2e/screenshots/suggest-editor-rules-presence.png
git commit -m "test(se): 检查规则 E2E——映射毫秒级/接受生效、presence 随豁免词消失、文档级隔离"
```

---

### Task 8: Node 侧 AI 通道（aiPrompt 注入分析 prompt）

**Files:**
- Modify: `suggest-editor-server/src/workspace.ts`（规则读取）
- Modify: `suggest-editor-server/src/engine.ts`（prompt 注入）

**Interfaces:**
- Produces:
  - `workspace.ts`: `export async function readRulePrompts(docName: string, maxRules?: number): Promise<Array<{ name: string; prompt: string }>>`
  - `engine.buildPrompt(dirty, avoid, checklist, snapshotPath, rulePrompts)`（参数追加）

- [ ] **Step 1: workspace.ts 追加**

```ts
/** 检查规则文件：全局 + 文档级（文档级同名 id 覆盖全局） */
export function rulesPath(): string {
  return path.join(config.dataDir, '规则-全局.json')
}

export function docRulesPath(docName: string): string {
  return path.join(workspaceDir(docName), '规则.json')
}

async function readRulesFile(file: string): Promise<Array<Record<string, unknown>>> {
  try {
    const raw = await fs.readFile(file, 'utf8')
    const payload = JSON.parse(raw) as { rules?: unknown }
    return Array.isArray(payload.rules) ? (payload.rules as Array<Record<string, unknown>>) : []
  } catch {
    return [] // 缺文件/坏 JSON：跳过（管理面会给出修复入口）
  }
}

/** 供分析 prompt 注入：enabled 且带 aiPrompt 的规则（文档级覆盖全局），上限 40 条 */
export async function readRulePrompts(docName: string, maxRules = 40): Promise<Array<{ name: string; prompt: string }>> {
  const merged = new Map<string, Record<string, unknown>>()
  for (const rule of await readRulesFile(rulesPath())) {
    if (typeof rule.id === 'string') merged.set(rule.id, rule)
  }
  for (const rule of await readRulesFile(docRulesPath(docName))) {
    if (typeof rule.id === 'string') merged.set(rule.id, rule)
  }
  const out: Array<{ name: string; prompt: string }> = []
  for (const rule of merged.values()) {
    if (rule.enabled === false) continue
    const prompt = typeof rule.aiPrompt === 'string' ? rule.aiPrompt.trim() : ''
    if (!prompt) continue
    out.push({ name: typeof rule.name === 'string' ? rule.name : rule.id as string, prompt })
    if (out.length >= maxRules) break
  }
  return out
}
```

- [ ] **Step 2: engine.ts 注入**

`buildPrompt` 签名与首段追加：

```ts
function buildPrompt(dirty: BlockInfo[], avoid: string[], checklist: string[],
                     snapshotPath: string, rulePrompts: Array<{ name: string; prompt: string }>): string {
  const lines = [
    `请先用 read 工具读取文件 ${snapshotPath}（绝对路径，里面是文档最近改动的 ${dirty.length} 个段落），再按下面的检查要点找出其中最多 5 处值得修改的地方，并调用 emit_suggestions 提交建议。`,
  ]
  if (rulePrompts.length > 0) {
    lines.push('领域检查规则（逐条核对，命中在 reason 中注明规则名）：')
    for (const rule of rulePrompts) lines.push(`- [${rule.name}] ${rule.prompt}`)
  }
  // …原有检查要点段不变…
```

`run()` 中：

```ts
const checklist = await readChecklistEntries()
const rulePrompts = await readRulePrompts(state.name)
const result = await withTimeout(
  state.analysisLock(() => session.analyze(buildPrompt(dirty, avoid, checklist, snapshotPath, rulePrompts))),
  ...
)
```

- [ ] **Step 3: 类型检查 + 真 agent 探针**

Run: `cd suggest-editor-server && npm run typecheck` → 0 errors
Run: `cd .. && node e2e/manual/suggest-editor-real-agent-check.mjs`（生产栈真模型）
Expected: PASS，建议 reason 中出现 `[规则名]`（默认全局无自定义 aiPrompt 规则，先经 API 建一条再跑；见下方命令）

```bash
# 建一条带 aiPrompt 的全局规则后跑探针
TOKEN=$(curl -s -X POST http://127.0.0.1:3002/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -X PUT http://127.0.0.1:3002/suggest-editor/rules/global \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"rules":[{"id":"probe-ai","name":"无主语提示","enabled":true,"severity":"warn","aiPrompt":"凡出现「系统会自动…」必须指出缺少具体主语"}]}'
node e2e/manual/suggest-editor-real-agent-check.mjs
```

- [ ] **Step 4: Commit**

```bash
git add suggest-editor-server/src/workspace.ts suggest-editor-server/src/engine.ts
git commit -m "feat(se): 检查规则 AI 通道——文档级覆盖全局、aiPrompt 注入分析 prompt（规则名进 reason）"
```

---

### Task 9: 文档与全量回归

**Files:**
- Modify: `docs/user-guide/ai/suggest-editor.md`（新增「检查规则」节 + 运维/排障边界）
- Modify: `CLAUDE.md`（一段）
- Modify: `docs/user-guide/README.md`（若需索引调整则不改，条目已存在）

- [ ] **Step 1: 用户文档**

在「检查要点」节之后新增：

```md
## 检查规则（领域强约束，毫秒级 + AI 双通道）

与「检查要点」（通用写作质量）并存。管理入口：列表页「检查规则」（管理员，全局）/
编辑页顶栏「规则」（属主，仅本文档）。每条规则可组合：
- **错误映射**（如 S-5700→S5700）：输入即出卡片，可接受替换；
- **正则提取校验**（提取候选后按 valid 判定，如单板型号格式）；
- **锚定白名单**（「设备类型：」后的值必须在清单内）；
- **条件完整性**（presence）：全文出现触发词（如 9800M12）但既未提及配套设备（9800UPS）
  也未写豁免声明（不涉及拖带）→ 提醒；补上任一满足项后提示**自动消失**；
- **AI 说明（aiPrompt）**：注入 AI 分析做语义兜底（措辞变体），命中在 reason 标注规则名。
导入导出：CSV（一行一条规则，多值列 `|` 分隔；Excel 另存 CSV），有模板下载；导入失败整批拒绝并报行号。
边界：本地匹配限单个文本节点；presence 为字面匹配（同义词需加入词表）；提醒型（notice）只能忽略。
```

- [ ] **Step 2: 全量回归**

```bash
# 后端
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_suggest_editor_rules_validate.py tests/test_suggest_editor_rules_routes.py tests/test_suggest_editor_routes.py tests/test_suggest_editor_vocab.py tests/test_suggest_editor_checklist.py -q
# 前端
npx vue-tsc --noEmit
npx vitest run src/components/suggest-editor/core/__tests__/check-rules.test.ts src/api/__tests__/suggestEditor.test.ts
# E2E（dev mock 栈：先停生产栈）
npx playwright test e2e/suggest-editor-rules.spec.ts e2e/suggest-editor-layout.spec.ts e2e/suggest-editor.spec.ts
# Node
cd suggest-editor-server && npm run typecheck
```

Expected: 全部 PASS。

- [ ] **Step 3: 生产构建与部署验证**

```bash
cd .. && npm run build          # 重建 dist（生产 proxy 服务 dist/）
# 重启生产栈后：穿越 8080 冒烟 + 真 agent 探针
SE_SMOKE_API=http://127.0.0.1:8080/api SE_SMOKE_WS=ws://127.0.0.1:8080/suggest-ws node e2e/manual/suggest-editor-ws-smoke.mjs
node e2e/manual/suggest-editor-real-agent-check.mjs
```

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide/ai/suggest-editor.md CLAUDE.md
git commit -m "docs(se): 检查规则用户文档与架构说明"
git push origin main   # 失败按 8s 重试（GitHub 线路间歇性）
```

---

## Self-Review（已执行）

- **Spec coverage**：§3 模型→Task1/4；§4 存储→Task2/8；§5 端点→Task2；§6 本地执行→Task4/5；§7 AI 通道→Task8；§8 管理 UI→Task6；§9 错误处理→Task1/2/5；§10 测试→Task1/2/4/7/9；§11 边界→文档 Task9。
- **Placeholder scan**：各任务均含可执行代码/测试与命令；Task 6 的表单仅 patterns/whitelists/presence 省略重复模板，实现时按 maps 行编辑器同构展开（每个字段名与 spec 一致）。
- **Type consistency**：`LocalCheckRule` 前后端字段一致；`compileRules/runCheckRules/RuleMatch` 命名一致；`reloadRules` 与 `RuleManagerDialog @saved` 对齐。
