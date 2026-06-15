"""跨页工作流引擎：阶段推进/回退 + 下游记录生成 + 实例追踪。
挂在 update_item 状态转换之后。复用 sequences/pk-lock/notifier/workflow_repo。"""
import uuid
import psycopg2.extras
from utils.sequences import allocate_sequence


class WorkflowError(Exception):
    """工作流编排失败（如无权推进当前阶段）。调用方应回滚事务并向用户返回错误。"""


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


def spawn_record(cur, target_collection, branch_id, field_mapping, source_data, source_id, operator,
                 next_stage=None, link_back_field=None):
    """在 target_collection 生成一条下游记录：原子分配 autoSequence + 字段映射 + 主键锁 + 插入。
    返回新记录 id。复用 routes.dynamic 的并发原语。
    next_stage：下游阶段配置，用于把下游状态字段播种为该阶段 advanceTransition.from（否则下游
    永远不会进入「可推进」态，且首次写状态时 old_val=None 不触发引擎）。
    link_back_field：把源记录 id 写回下游该字段（实现 spawn.linkBackField 反向引用）。"""
    from routes.dynamic import get_page_info, get_primary_key_fields, acquire_pk_lock
    data = {}
    for tgt, expr in (field_mapping or {}).items():
        data[tgt] = _resolve(expr, source_data, source_id, operator)
    if link_back_field:
        data[link_back_field] = source_id
    if next_stage:
        sf = next_stage.get('statusField')
        from_v = (next_stage.get('advanceTransition') or {}).get('from')
        if sf and from_v is not None:
            data.setdefault(sf, from_v)
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


def _reset_downstream_status(cur, stage, collection, record_id, branch_id):
    """把（重新进入的）下游记录的状态字段重置回该阶段 advanceTransition.from，使其再次可推进。"""
    sf = stage.get('statusField')
    from_v = (stage.get('advanceTransition') or {}).get('from')
    if not (sf and from_v is not None):
        return
    cur.execute("SELECT data FROM dynamic_data WHERE collection=%s AND id=%s AND branch_id=%s",
                (collection, record_id, branch_id))
    row = cur.fetchone()
    if row:
        d = row[0] or {}
        d[sf] = from_v
        cur.execute("UPDATE dynamic_data SET data=%s, updated_at=NOW() WHERE collection=%s AND id=%s AND branch_id=%s",
                    (psycopg2.extras.Json(d), collection, record_id, branch_id))


def _notify_roles(cur, roles, title, content, collection, record_id):
    """通知拥有 roles 之一的所有用户（create_notification 自开连接，独立事务）。"""
    if not roles:
        return
    from utils.notifier import create_notification
    cur.execute("SELECT id FROM users WHERE role = ANY(%s)", (list(roles),))
    for (uid,) in cur.fetchall():
        create_notification(uid, 'workflow', title, content, collection, record_id)


def on_transition(cur, collection, record_id, status_field, from_value, to_value,
                  old_data, new_data, operator, role, comment=None, branch_id='main'):
    """update_item 状态转换成功后调用。匹配当前实例阶段的 advance/reject 转换并编排。
    无匹配实例则静默返回（普通状态变更不受影响）。
    branch_id：源记录所在分支——下游 spawn / 回退重置都在同一分支内，保证分支隔离。
    角色不符时抛 WorkflowError，由调用方回滚事务并报错（避免「状态已改、实例未推进」的分裂）。"""
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

    # 仅当本次转换确实命中当前阶段的 advance/reject 时才做角色校验，
    # 否则与本阶段无关的普通状态变更会被误拦。
    matches_adv = bool(adv) and from_value == adv.get('from') and to_value == adv.get('to')
    matches_rej = bool(rej) and from_value == rej.get('from') and to_value == rej.get('to')
    if (matches_adv or matches_rej) and stage.get('assignedRoles') and role not in stage['assignedRoles']:
        raise WorkflowError(f'您的角色（{role}）无权推进工作流阶段「{stage.get("name", stage["id"])}」')

    chain = inst['chain']; history = inst['history']
    cur_entry = next((e for e in chain if e['stageId'] == stage['id']), None)
    if matches_adv:
        if cur_entry is not None:
            cur_entry['completedBy'] = operator
        history.append({'ts': repo._now(), 'action': 'advance', 'stageId': stage['id'],
                        'by': operator, 'comment': comment})
        if idx + 1 < len(stages):
            nxt = stages[idx + 1]
            spawn = stage.get('spawn') or {}
            # 回退后重新推进：复用已存在的下游 chain 项，避免重复 spawn + chain 无限增长。
            existing = next((e for e in chain if e['stageId'] == nxt['id']), None)
            if existing is not None:
                down_id = existing['recordId']
                existing['completedBy'] = None
                existing['enteredAt'] = repo._now()
                _reset_downstream_status(cur, nxt, nxt['collection'], down_id, branch_id)
            else:
                down_id = spawn_record(cur, target_collection=nxt['collection'], branch_id=branch_id,
                                       field_mapping=spawn.get('fieldMapping', {}),
                                       source_data=new_data, source_id=record_id, operator=operator,
                                       next_stage=nxt, link_back_field=spawn.get('linkBackField'))
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
    if matches_rej:
        if idx == 0:
            return  # 首阶段无可退回
        prev = stages[idx - 1]
        history.append({'ts': repo._now(), 'action': 'reject', 'stageId': stage['id'],
                        'by': operator, 'comment': comment})
        prev_entry = None
        for entry in reversed(chain):
            if entry['stageId'] == prev['id']:
                prev_entry = entry
                break
        if prev_entry is None:
            return
        prev_entry['completedBy'] = None
        prev_adv = (prev.get('advanceTransition') or {})
        reset_to = prev_adv.get('from')
        if reset_to and prev.get('statusField'):
            cur.execute("SELECT data FROM dynamic_data WHERE collection=%s AND id=%s AND branch_id=%s",
                        (prev_entry['collection'], prev_entry['recordId'], branch_id))
            row = cur.fetchone()
            if row:
                pdata = row[0] or {}
                pdata[prev['statusField']] = reset_to
                pdata['_rejectComment'] = comment
                cur.execute("UPDATE dynamic_data SET data=%s, updated_at=NOW() WHERE collection=%s AND id=%s AND branch_id=%s",
                            (psycopg2.extras.Json(pdata), prev_entry['collection'], prev_entry['recordId'], branch_id))
        repo.update_instance(cur, inst['id'], current_stage_id=prev['id'], chain=chain, history=history)
        _notify_roles(cur, prev.get('assignedRoles', []), f'工作流驳回：{prev["name"]}',
                      f'{operator} 驳回到「{prev["name"]}」：{comment or ""}', prev_entry['collection'], prev_entry['recordId'])
        return
