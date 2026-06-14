"""workflow_definitions / workflow_instances 数据访问。调用方负责提交。"""
import uuid
import psycopg2.extras


def save_definition(cur, d):
    """upsert 一个工作流定义。d: {id?, name, description?, enabled, stages:list}"""
    wid = d.get('id') or f'wf-{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO workflow_definitions (id, name, description, enabled, stages, updated_at) "
        "VALUES (%s,%s,%s,%s,%s, NOW()) "
        "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, description=EXCLUDED.description, "
        "enabled=EXCLUDED.enabled, stages=EXCLUDED.stages, updated_at=NOW()",
        (wid, d['name'], d.get('description'), d.get('enabled', True),
         psycopg2.extras.Json(d.get('stages', []))),
    )
    return wid


def get_definition(cur, wid):
    cur.execute("SELECT id,name,description,enabled,stages FROM workflow_definitions WHERE id=%s", (wid,))
    r = cur.fetchone()
    if not r:
        return None
    return {'id': r[0], 'name': r[1], 'description': r[2], 'enabled': r[3], 'stages': r[4] or []}


def list_definitions(cur):
    cur.execute("SELECT id,name,description,enabled,stages FROM workflow_definitions ORDER BY updated_at DESC")
    return [{'id': r[0], 'name': r[1], 'description': r[2], 'enabled': r[3], 'stages': r[4] or []}
            for r in cur.fetchall()]


def delete_definition(cur, wid):
    cur.execute("DELETE FROM workflow_definitions WHERE id=%s", (wid,))


def validate_definition(cur, definition):
    """保存期校验：检查每个阶段绑定的状态字段确实能驱动推进，返回中文告警列表（非阻断）。
    无告警 = 该工作流可正常推进；有告警说明阶段配置会导致「推进按钮永不出现」之类的死流程。"""
    from utils.operation_log import get_page_info
    warnings = []
    for st in definition.get('stages', []):
        name = st.get('name') or st.get('id') or '?'
        coll = st.get('collection'); sf = st.get('statusField')
        if not coll or not sf:
            continue  # 阶段名/数据页缺失由前端必填校验拦截
        _, fields = get_page_info(cur, coll)
        fcfg = next((f for f in (fields or []) if f.get('fieldName') == sf), None)
        if not fcfg:
            warnings.append(f'阶段「{name}」的状态字段 {sf} 在数据页 {coll} 中不存在')
            continue
        wf = fcfg.get('workflowConfig')
        if not (wf and wf.get('enabled')):
            warnings.append(f'阶段「{name}」的状态字段 {sf} 未启用工作流配置，将无法显示推进按钮')
            continue
        trans = wf.get('transitions', []) or []
        for key, label in (('advanceTransition', '推进'), ('rejectTransition', '回退')):
            t = st.get(key)
            if not t:
                continue
            ok = any((x.get('from') == t.get('from') or x.get('from') == '*') and x.get('to') == t.get('to')
                     for x in trans)
            if not ok:
                warnings.append(
                    f'阶段「{name}」的{label}转换 {t.get("from")}→{t.get("to")} 不在字段 {sf} 的合法转换中')
    return warnings


def create_instance(cur, inst_id, workflow_id, stage_id, collection, record_id, started_by):
    chain = [{'stageId': stage_id, 'collection': collection, 'recordId': record_id,
              'enteredAt': _now(), 'completedBy': None}]
    history = [{'ts': _now(), 'action': 'start', 'stageId': stage_id, 'by': started_by, 'comment': None}]
    cur.execute(
        "INSERT INTO workflow_instances (id, workflow_id, status, current_stage_id, chain, history, started_by) "
        "VALUES (%s,%s,'running',%s,%s,%s,%s)",
        (inst_id, workflow_id, stage_id, psycopg2.extras.Json(chain), psycopg2.extras.Json(history), started_by),
    )
    return get_instance(cur, inst_id)


def get_instance(cur, inst_id, for_update=False):
    sql = "SELECT id,workflow_id,status,current_stage_id,chain,history,started_by FROM workflow_instances WHERE id=%s"
    if for_update:
        sql += " FOR UPDATE"
    cur.execute(sql, (inst_id,))
    r = cur.fetchone()
    if not r:
        return None
    return {'id': r[0], 'workflow_id': r[1], 'status': r[2], 'current_stage_id': r[3],
            'chain': r[4] or [], 'history': r[5] or [], 'started_by': r[6]}


def find_running_instance_by_record(cur, collection, record_id, for_update=False):
    """找到「当前阶段对应的 chain 项」== (collection, record_id) 的运行中实例。
    用 current_stage_id 而非 chain 末项定位——回退后当前阶段≠末项，故必须按当前阶段匹配。"""
    sql = ("SELECT id FROM workflow_instances wi WHERE status='running' "
           "AND EXISTS (SELECT 1 FROM jsonb_array_elements(chain) e "
           "WHERE e->>'stageId' = wi.current_stage_id "
           "AND e->>'collection' = %s AND e->>'recordId' = %s) LIMIT 1")
    cur.execute(sql, (collection, record_id))
    r = cur.fetchone()
    if not r:
        return None
    return get_instance(cur, r[0], for_update=for_update)


def update_instance(cur, inst_id, *, status=None, current_stage_id=None, chain=None, history=None):
    sets, params = ['updated_at=NOW()'], []
    if status is not None:
        sets.append('status=%s'); params.append(status)
    if current_stage_id is not None:
        sets.append('current_stage_id=%s'); params.append(current_stage_id)
    if chain is not None:
        sets.append('chain=%s'); params.append(psycopg2.extras.Json(chain))
    if history is not None:
        sets.append('history=%s'); params.append(psycopg2.extras.Json(history))
    params.append(inst_id)
    cur.execute(f"UPDATE workflow_instances SET {', '.join(sets)} WHERE id=%s", params)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
