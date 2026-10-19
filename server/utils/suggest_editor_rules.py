"""检查规则：校验、CSV/TXT 解析、合并。纯函数——文件读写留在路由层。"""
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
    if not isinstance(pattern, str):
        raise RuleValidationError(f'{where}: 必须是字符串正则')
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
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                raise RuleValidationError(f'{where}.{key}[{i}]: 必须是对象')
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
        rule['maps'] = [{'wrong': (row.get('wrong') or '').strip(),
                         'right': row.get('right') or ''}]
    elif kind == 'pattern':
        rule['patterns'] = [{'extract': (row.get('extract') or '').strip(),
                             'valid': (row.get('valid') or '').strip(),
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


def _parse_rules_txt(text):
    """TXT 简版：每行 `错误写法=>正确写法` 生成一条 map 规则（# 注释/空行跳过）。"""
    out = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if len(out) >= MAX_RULES:
            raise RuleValidationError(f'第 {line_no} 行: 规则数量超过 {MAX_RULES} 条')
        wrong, sep, right = line.partition('=>')
        wrong, right = wrong.strip(), right.strip()
        if not sep or not wrong or not right:
            raise RuleValidationError(f'第 {line_no} 行: 需要 错误写法=>正确写法')
        rule = {'id': f'rule-{uuid.uuid4().hex[:12]}',
                'name': f'TXT 映射：{wrong}', 'enabled': True, 'severity': 'warn',
                'maps': [{'wrong': wrong, 'right': right}]}
        try:
            out.append(_validate_one(len(out), rule))
        except RuleValidationError as e:
            raise RuleValidationError(f'第 {line_no} 行: {e}')
    return out


def parse_rules_csv(text):
    """解析导入文本：首行 `type,`（不区分大小写）走 CSV，否则按 TXT 简版。

    路由层已按 utf-8-sig 解码；这里再去一次 BOM，保证直接调用方同样可用。
    两种格式都是整批解析：任一行出错即抛 RuleValidationError，不做部分导入。
    """
    if text.startswith('\ufeff'):
        text = text[1:]
    first = next((line.strip() for line in text.splitlines() if line.strip()), '')
    if not first.lower().startswith('type,'):
        return _parse_rules_txt(text)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise RuleValidationError('CSV 缺少表头')
    out = []
    for offset, row in enumerate(reader, start=2):
        if not any((v or '').strip() for v in row.values() if isinstance(v, str)):
            continue
        if len(out) >= MAX_RULES:
            raise RuleValidationError(f'第 {offset} 行: 规则数量超过 {MAX_RULES} 条')
        out.append(_row_to_rule(row, offset))
    return out


def dedupe_key(rule):
    payload = {k: rule.get(k) for k in ('maps', 'patterns', 'whitelists', 'presence', 'aiPrompt') if rule.get(k)}
    return json.dumps([rule.get('name'), payload], ensure_ascii=False, sort_keys=True)


def merge_rules(existing, incoming, mode):
    if mode == 'replace':
        if len(incoming) > MAX_RULES:
            raise RuleValidationError(f'合并后规则数量超过 {MAX_RULES} 条')
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
