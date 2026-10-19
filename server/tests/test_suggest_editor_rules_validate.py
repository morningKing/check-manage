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


def test_validate_nested_array_caps():
    # 每类机制内部的数组同样受 MAX_ITEMS=200 约束
    anchors = [f'a{i}' for i in range(250)]
    with pytest.raises(RuleValidationError) as ei:
        validate_rules({'rules': [_rule(whitelists=[{'anchors': anchors, 'values': ['v'], 'message': 'm'}])]})
    assert 'anchors' in str(ei.value)

    values = [f'v{i}' for i in range(250)]
    with pytest.raises(RuleValidationError) as ei:
        validate_rules({'rules': [_rule(whitelists=[{'anchors': ['a'], 'values': values, 'message': 'm'}])]})
    assert 'values' in str(ei.value)

    for key in ('triggers', 'requiresAny'):
        item = {'triggers': ['t'], 'requiresAny': ['r'], 'message': 'm'}
        item[key] = [f'{key}{i}' for i in range(250)]
        with pytest.raises(RuleValidationError) as ei:
            validate_rules({'rules': [_rule(presence=[item])]})
        assert key in str(ei.value)


def test_validate_map_right_allows_empty_but_caps_length_and_type():
    # 空串=删除该错误写法，保持允许
    rules = validate_rules({'rules': [_rule(maps=[{'wrong': 'S-5700', 'right': ''}])]})
    assert rules[0]['maps'][0]['right'] == ''
    with pytest.raises(RuleValidationError):
        validate_rules({'rules': [_rule(maps=[{'wrong': 'X', 'right': 'y' * 201}])]})
    with pytest.raises(RuleValidationError):
        validate_rules({'rules': [_rule(maps=[{'wrong': 'X', 'right': 123}])]})


def test_validate_pattern_flags_must_be_short_string():
    bad_type = {'extract': 'a', 'valid': 'a', 'message': 'm', 'flags': 1}
    with pytest.raises(RuleValidationError) as ei:
        validate_rules({'rules': [_rule(patterns=[bad_type])]})
    assert 'flags' in str(ei.value)
    too_long = {'extract': 'a', 'valid': 'a', 'message': 'm', 'flags': 'i' * 11}
    with pytest.raises(RuleValidationError) as ei:
        validate_rules({'rules': [_rule(patterns=[too_long])]})
    assert 'flags' in str(ei.value)


def test_csv_parses_all_five_types_and_multivalue_columns():
    text = (
        'type,name,severity,enabled,wrong,right,extract,valid,anchors,values,triggers,requiresAny,message,aiPrompt\n'
        'map,型号纠错,error,true,S-5700,S5700,,,,,,,,,\n'
        'pattern,型号格式,warn,true,,,"([A-Z]{2,4}-\\d+)","^S\\d{4}$",,,,,单板型号格式不对,\n'
        'whitelist,设备白名单,warn,true,,,,,设备类型：,S5700|S6700,,,设备类型不在清单内,\n'
        'presence,拖带完整性,warn,true,,,,,,,9800M12,9800UPS|不涉及拖带,未写拖带说明,\n'
        'ai,写作口径,info,true,,,,,,,,,,统一用「方案」表述\n'
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


def test_txt_simple_mapping_import():
    text = '# 注释行\nS-5700=>S5700\n\nX-1 => X1\n'
    rules = parse_rules_csv(text)
    assert len(rules) == 2
    assert rules[0]['maps'] == [{'wrong': 'S-5700', 'right': 'S5700'}]
    assert rules[1]['maps'] == [{'wrong': 'X-1', 'right': 'X1'}]
    assert all(r['severity'] == 'warn' and r['enabled'] is True for r in rules)
    assert rules[0]['name'] == 'TXT 映射：S-5700'


def test_txt_bad_line_rejects_whole_batch():
    with pytest.raises(RuleValidationError) as ei:
        parse_rules_csv('A=>B\nNO ARROW\nC=>D\n')
    assert '第 2 行' in str(ei.value)
    assert '错误写法=>正确写法' in str(ei.value)


def test_format_detection_keeps_csv_branch():
    text = 'type,name,severity,wrong,right\nmap,型号纠错,error,A,B\n'
    rules = parse_rules_csv(text)
    assert len(rules) == 1
    assert rules[0]['name'] == '型号纠错'
    assert rules[0]['maps'] == [{'wrong': 'A', 'right': 'B'}]


def test_merge_append_dedupes_and_replace_overwrites():
    existing = [_rule()]
    incoming = [_rule(id='rule-abc'), _rule(id='rule-def', maps=[{'wrong': 'X', 'right': 'Y'}])]
    merged, added, skipped = merge_rules(existing, incoming, 'append')
    assert (added, skipped) == (1, 1) and len(merged) == 2
    merged2, added2, _ = merge_rules(existing, incoming, 'replace')
    assert added2 == 2 and len(merged2) == 2
