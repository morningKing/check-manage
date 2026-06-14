"""跨页工作流引擎：阶段推进/回退 + 下游记录生成 + 实例追踪。
挂在 update_item 状态转换之后。复用 sequences/pk-lock/notifier/workflow_repo。"""
import uuid
import psycopg2.extras
from utils.sequences import allocate_sequence


def _resolve(expr, source_data, source_id, operator):
    """解析 $source.<field> / $source.id / $operator / $NOW / 字面量。"""
    from datetime import datetime, timezone
    if not isinstance(expr, str):
        return expr
    if expr.startswith('$source.'):
        f = expr[len('$source.'):]
        return source_id if f == 'id' else source_data.get(f, '')
    if expr == '$operator':
        return operator
    if expr == '$NOW':
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return expr


def spawn_record(cur, target_collection, branch_id, field_mapping, source_data, source_id, operator):
    """在 target_collection 生成一条下游记录：原子分配 autoSequence + 字段映射 + 主键锁 + 插入。
    返回新记录 id。复用 routes.dynamic 的并发原语。"""
    from routes.dynamic import get_page_info, get_primary_key_fields, acquire_pk_lock
    data = {}
    for tgt, expr in (field_mapping or {}).items():
        data[tgt] = _resolve(expr, source_data, source_id, operator)
    page_name, fields = get_page_info(cur, target_collection)
    for fcfg in (fields or []):
        if fcfg.get('controlType') == 'autoSequence':
            cfg = fcfg.get('sequenceConfig') or {}
            prefix = cfg.get('prefix', '')
            pad = len(str(cfg.get('max', 999)))
            data[fcfg['fieldName']] = allocate_sequence(
                cur, target_collection, branch_id, fcfg['fieldName'], prefix, pad, count=1)[0]
    autoseq = {f['fieldName'] for f in (fields or []) if f.get('controlType') == 'autoSequence'}
    pk_fields = get_primary_key_fields(cur, target_collection)
    manual_pk = {f: data.get(f) for f in (pk_fields or []) if f not in autoseq}
    acquire_pk_lock(cur, target_collection, manual_pk)
    new_id = f'{target_collection[:8]}-{uuid.uuid4().hex[:12]}'
    cur.execute(
        'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,%s)',
        (new_id, target_collection, psycopg2.extras.Json(data), branch_id),
    )
    return new_id


def _stage_index(stages, stage_id):
    for i, s in enumerate(stages):
        if s['id'] == stage_id:
            return i
    return -1


def _notify_roles(cur, roles, title, content, collection, record_id):
    """通知拥有 roles 之一的所有用户（create_notification 自开连接，独立事务）。"""
    if not roles:
        return
    from utils.notifier import create_notification
    cur.execute("SELECT id FROM users WHERE role = ANY(%s)", (list(roles),))
    for (uid,) in cur.fetchall():
        create_notification(uid, 'workflow', title, content, collection, record_id)


def on_transition(cur, collection, record_id, status_field, from_value, to_value,
                  old_data, new_data, operator, role, comment=None):
    """update_item 状态转换成功后调用。匹配当前实例阶段的 advance/reject 转换并编排。
    无匹配实例则静默返回（普通状态变更不受影响）。"""
    from utils import workflow_repo as repo
    inst = repo.find_running_instance_by_record(cur, collection, record_id, for_update=True)
    if not inst:
        return
    definition = repo.get_definition(cur, inst['workflow_id'])
    if not definition or not definition.get('enabled'):
        return
    stages = definition['stages']
    idx = _stage_index(stages, inst['current_stage_id'])
    if idx < 0:
        return
    stage = stages[idx]
    if stage.get('statusField') != status_field:
        return
    adv = stage.get('advanceTransition') or {}
    rej = stage.get('rejectTransition') or {}

    if stage.get('assignedRoles') and role not in stage['assignedRoles']:
        return

    chain = inst['chain']; history = inst['history']
    if adv and from_value == adv.get('from') and to_value == adv.get('to'):
        chain[-1]['completedBy'] = operator
        history.append({'ts': repo._now(), 'action': 'advance', 'stageId': stage['id'],
                        'by': operator, 'comment': comment})
        if idx + 1 < len(stages):
            nxt = stages[idx + 1]
            spawn = stage.get('spawn') or {}
            down_id = spawn_record(cur, target_collection=nxt['collection'], branch_id='main',
                                   field_mapping=spawn.get('fieldMapping', {}),
                                   source_data=new_data, source_id=record_id, operator=operator)
            chain.append({'stageId': nxt['id'], 'collection': nxt['collection'], 'recordId': down_id,
                          'enteredAt': repo._now(), 'completedBy': None})
            repo.update_instance(cur, inst['id'], current_stage_id=nxt['id'], chain=chain, history=history)
            _notify_roles(cur, nxt.get('assignedRoles', []), f'工作流待办：{nxt["name"]}',
                          f'{operator} 推进了流程，请处理「{nxt["name"]}」', nxt['collection'], down_id)
        else:
            history.append({'ts': repo._now(), 'action': 'complete', 'stageId': stage['id'],
                            'by': operator, 'comment': None})
            repo.update_instance(cur, inst['id'], status='completed', chain=chain, history=history)
        return
    if rej and from_value == rej.get('from') and to_value == rej.get('to'):
        if idx == 0:
            return  # 首阶段无可退回
        prev = stages[idx - 1]
        history.append({'ts': repo._now(), 'action': 'reject', 'stageId': stage['id'],
                        'by': operator, 'comment': comment})
        prev_entry = None
        for entry in reversed(chain[:-1]):
            if entry['stageId'] == prev['id']:
                prev_entry = entry
                break
        if prev_entry is None:
            return
        prev_entry['completedBy'] = None
        prev_adv = (prev.get('advanceTransition') or {})
        reset_to = prev_adv.get('from')
        if reset_to and prev.get('statusField'):
            cur.execute("SELECT data FROM dynamic_data WHERE collection=%s AND id=%s AND branch_id='main'",
                        (prev_entry['collection'], prev_entry['recordId']))
            row = cur.fetchone()
            if row:
                pdata = row[0] or {}
                pdata[prev['statusField']] = reset_to
                pdata['_rejectComment'] = comment
                cur.execute("UPDATE dynamic_data SET data=%s, updated_at=NOW() WHERE collection=%s AND id=%s AND branch_id='main'",
                            (psycopg2.extras.Json(pdata), prev_entry['collection'], prev_entry['recordId']))
        repo.update_instance(cur, inst['id'], current_stage_id=prev['id'], chain=chain, history=history)
        _notify_roles(cur, prev.get('assignedRoles', []), f'工作流驳回：{prev["name"]}',
                      f'{operator} 驳回到「{prev["name"]}」：{comment or ""}', prev_entry['collection'], prev_entry['recordId'])
        return
