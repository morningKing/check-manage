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
