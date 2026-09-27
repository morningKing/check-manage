"""扫描回写业务结果校验器（execution-audit Spec §TV-03/TV-04 确定性覆盖）。

- TV-03 回写失败：UPDATE 匹配 0 行 / 回读值与预期不一致
- TV-04 结果不完整：必需字段缺失、空值、或非对象解析

纯函数 + 证据结构，供 ai_scan_engine 在回写后调用；校验失败会把记录置为
failed_value 并把 violations 追加到执行审计（attempt 存在时）。
"""

import json
import logging

logger = logging.getLogger(__name__)


def validate_writeback(task, record_id, parsed, rowcount, recheck) -> dict:
    """Deterministic post-writeback validation.

    recheck: () -> dict —— 回读 dynamic_data.data（写后读校验）。
    返回 {ok, violations:[{type,severity,message,evidence_refs}]}。
    """
    violations = []
    if rowcount == 0:
        violations.append({
            'type': 'TV-03',
            'severity': 'high',
            'message': f'回写匹配 0 行：记录 {record_id} 可能已被删除/移动',
            'evidence_refs': [f'record:{record_id}'],
        })
        return {'ok': False, 'violations': violations}

    row = None
    try:
        row = recheck() if recheck else None
    except Exception as e:
        logger.warning('writeback recheck failed record=%s: %s', record_id, e)

    if isinstance(row, dict):
        data = row.get('data') or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                data = {}
        for m in task.get('field_mapping') or []:
            col = m.get('column')
            expected = parsed.get(m.get('jsonKey'))
            actual = data.get(col)
            if expected is not None and str(actual or '') != str(expected):
                violations.append({
                    'type': 'TV-03',
                    'severity': 'high',
                    'message': (f"字段 {col} 回写不一致：期望「{expected}」"
                                f"实际「{actual}」"),
                    'evidence_refs': [f'record:{record_id}:{col}'],
                })
        status_actual = data.get(task.get('status_field'))
        if str(status_actual or '') != str(task.get('done_value')):
            violations.append({
                'type': 'TV-03',
                'severity': 'medium',
                'message': (f"状态字段 {task.get('status_field')} 未落为 "
                            f"{task.get('done_value')}（实际 {status_actual}）"),
                'evidence_refs': [f'record:{record_id}:{task.get("status_field")}'],
            })

    # TV-04 结果不完整：必需 jsonKey 在 parsed 中缺失/空（回写虽成功但结果残缺）
    for m in task.get('field_mapping') or []:
        if not m.get('required'):
            continue
        if parsed.get(m.get('jsonKey')) in (None, ''):
            violations.append({
                'type': 'TV-04',
                'severity': 'high',
                'message': f"必需结果字段 {m.get('jsonKey')} 为空，结果不完整",
                'evidence_refs': [f'record:{record_id}:{m.get("jsonKey")}'],
            })

    return {'ok': not violations, 'violations': violations}


def record_violations_to_audit(session_id: str, violations: list) -> None:
    """把校验违例追加进执行审计事件通道（attempt 存在时 best-effort）。"""
    if not violations:
        return
    try:
        from utils import execution_audit
        attempts = execution_audit.get_attempts(session_id, limit=1)
        if not attempts:
            return
        for v in violations:
            execution_audit.record_event(
                attempts[0]['id'], 'scan.writeback.violation',
                session_id=session_id, status=v['type'],
                payload={'message': v['message'],
                         'severity': v['severity'],
                         'evidence_refs': v.get('evidence_refs', [])})
    except Exception as e:
        logger.warning('record violations to audit failed: %s', e)
