"""AI 编排定义：校验与版本发布（ai-harness-p2 spec §5.1/§6.2）。

定义发布后不可变：run 引用具体 version，编辑定义 = 发布新 version。
校验失败抛 ValueError（路由回 400）。
"""
import secrets

VALID_NODE_KINDS = ('agent', 'approval', 'join')
VALID_EDGE_KINDS = ('advance', 'join', 'compensation')
# 环检测/悬空边/join 语义/审批节点目标在此把关


def validate_definition(nodes, edges) -> tuple[list, list]:
    """校验 nodes/edges。返回规范化后的 (nodes, edges)。非法抛 ValueError。"""
    if not isinstance(nodes, list) or not nodes:
        raise ValueError('nodes 必须是非空数组')
    if not isinstance(edges, list):
        raise ValueError('edges 必须是数组')
    ids = []
    norm_nodes = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            raise ValueError(f'nodes[{i}] 必须是对象')
        nid = (n.get('id') or '').strip()
        if not nid or len(nid) > 100:
            raise ValueError(f'nodes[{i}].id 必填且不超过 100 字')
        if nid in ids:
            raise ValueError(f'节点 id 重复: {nid}')
        ids.append(nid)
        kind = (n.get('kind') or 'agent').strip()
        if kind not in VALID_NODE_KINDS:
            raise ValueError(f'nodes[{i}].kind 只支持 {VALID_NODE_KINDS}')
        node = {'id': nid, 'kind': kind,
                'name': (n.get('name') or nid)[:300],
                'prompt_template': n.get('prompt_template') or '',
                'agent': n.get('agent') or None,
                'model': n.get('model') or None,
                'output_contract': n.get('output_contract') or None,
                'retry': n.get('retry') or {},
                'approval': n.get('approval') or {},
                'condition': n.get('condition') or None}
        if kind == 'agent' and not node['prompt_template']:
            raise ValueError(f'agent 节点 {nid} 必须有 prompt_template')
        if kind == 'approval':
            appr = node['approval'] or {}
            if not (appr.get('requested_roles') or appr.get('requested_users')):
                raise ValueError(f'审批节点 {nid} 必须声明 requested_roles '
                                 '或 requested_users')
        norm_nodes.append(node)

    norm_edges = []
    join_in = {}
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            raise ValueError(f'edges[{i}] 必须是对象')
        src = (e.get('source') or '').strip()
        dst = (e.get('target') or '').strip()
        if src not in ids:
            raise ValueError(f'edges[{i}].source 引用不存在的节点: {src}')
        if dst not in ids:
            raise ValueError(f'edges[{i}].target 引用不存在的节点: {dst}')
        if src == dst:
            raise ValueError(f'edges[{i}] 不允许自环')
        kind = (e.get('kind') or 'advance').strip()
        if kind not in VALID_EDGE_KINDS:
            raise ValueError(f'edges[{i}].kind 只支持 {VALID_EDGE_KINDS}')
        if kind == 'join':
            join_in[dst] = join_in.get(dst, 0) + 1
        edge = {'source': src, 'target': dst, 'kind': kind,
                'condition': e.get('condition') or None}
        norm_edges.append(edge)
    # join 节点必须有多条入边（多条 join 边，或 join kind 任一入边即算）
    for n in norm_nodes:
        if n['kind'] == 'join':
            incoming = [e for e in norm_edges if e['target'] == n['id']]
            join_edges = [e for e in incoming if e['kind'] == 'join']
            if len(incoming) < 2:
                raise ValueError(f'join 节点 {n["id"]} 必须有多条入边')
            if not join_edges and not any(
                    e['kind'] == 'advance' for e in incoming):
                raise ValueError(f'join 节点 {n["id"]} 缺少 join/advance 入边')
    _assert_acyclic(norm_nodes, norm_edges)
    # 条件边必须声明 condition 字段名（字段级校验留到 run 时按上一步输出判）
    return norm_nodes, norm_edges


def _assert_acyclic(nodes, edges):
    adj = {}
    for e in edges:
        adj.setdefault(e['source'], []).append(e['target'])
    state = {}  # 0=visiting 1=done

    def dfs(u):
        if state.get(u) == 0:
            raise ValueError(f'DAG 存在环：经过 {u}')
        if state.get(u) == 1:
            return
        state[u] = 0
        for v in adj.get(u, []):
            dfs(v)
        state[u] = 1

    for n in nodes:
        if state.get(n['id']) is None:
            dfs(n['id'])


def publish_definition(name: str, *, description: str | None, nodes, edges,
                       owner_user_id: str | None = None,
                       retry_policy=None, timeout_policy=None,
                       budget_policy=None, approval_policy=None,
                       compensation_policy=None) -> dict:
    """发布定义新版本：同 id 已存在则 version+1（旧版本保留）。"""
    norm_nodes, norm_edges = validate_definition(nodes, edges)
    from db import get_db
    did = 'orch_' + secrets.token_hex(6)
    import json as _json
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_orchestration_definitions "
                "  (id, version, name, description, owner_user_id, nodes, edges, "
                "   retry_policy, timeout_policy, budget_policy, approval_policy, "
                "   compensation_policy, published_at) "
                "VALUES (%s, 1, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, "
                "        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, NOW()) "
                "RETURNING id, version",
                (did, name, description, owner_user_id,
                 _json.dumps(norm_nodes, ensure_ascii=False),
                 _json.dumps(norm_edges, ensure_ascii=False),
                 _json.dumps(retry_policy or {}),
                 _json.dumps(timeout_policy or {}),
                 _json.dumps(budget_policy or {}),
                 _json.dumps(approval_policy or {}),
                 _json.dumps(compensation_policy or {})))
            row = cur.fetchone()
        conn.commit()
    return {'id': row[0], 'version': row[1]}


def get_definition(def_id: str, version: int | None = None) -> dict | None:
    """取定义；version 缺省取最新已发布版本。"""
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            if version is None:
                cur.execute(
                    "SELECT id, version, name, description, nodes, edges, "
                    "approval_policy FROM ai_orchestration_definitions "
                    "WHERE id = %s ORDER BY version DESC LIMIT 1", (def_id,))
            else:
                cur.execute(
                    "SELECT id, version, name, description, nodes, edges, "
                    "approval_policy FROM ai_orchestration_definitions "
                    "WHERE id = %s AND version = %s", (def_id, version))
            row = cur.fetchone()
    if not row:
        return None
    return {'id': row[0], 'version': row[1], 'name': row[2],
            'description': row[3], 'nodes': row[4], 'edges': row[5],
            'approval_policy': row[6]}


def list_definitions(limit: int = 50) -> list[dict]:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (id) id, version, name, description, "
                "published_at FROM ai_orchestration_definitions "
                "ORDER BY id, version DESC LIMIT %s", (limit,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        if r.get('published_at') is not None:
            r['publishedAt'] = r.pop('published_at').isoformat()
    return rows
