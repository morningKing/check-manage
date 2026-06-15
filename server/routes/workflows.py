"""工作流定义 CRUD + 实例（启动/列表/详情）+ 收件箱。"""
import uuid
from flask import Blueprint, request, jsonify, g as flask_g
from db import get_db
from auth import login_required, write_required, require_permission  # server/auth.py
from utils import workflow_repo as repo

workflows_bp = Blueprint('workflows', __name__)


@workflows_bp.route('/workflow/definitions', methods=['GET'])
@login_required
def list_defs():
    with get_db() as conn:
        return jsonify(repo.list_definitions(conn.cursor()))


@workflows_bp.route('/workflow/definitions', methods=['POST'])
@require_permission('admin.workflows')
def save_def():
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        wid = repo.save_definition(cur, body)
        warnings = repo.validate_definition(cur, body)
        conn.commit()
        result = repo.get_definition(cur, wid)
        result['warnings'] = warnings  # 非阻断：提示可能导致流程无法推进的配置问题
        return jsonify(result), 200


@workflows_bp.route('/workflow/definitions/<wid>', methods=['DELETE'])
@require_permission('admin.workflows')
def delete_def(wid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM workflow_instances WHERE workflow_id=%s AND status='running'", (wid,))
        if cur.fetchone()[0] > 0:
            return jsonify({'error': '该工作流存在运行中的实例，无法删除（请先终止或等待其完成）'}), 409
        repo.delete_definition(cur, wid); conn.commit()
    return jsonify({'ok': True})


@workflows_bp.route('/workflow/instances', methods=['POST'])
@write_required
def start_instance():
    body = request.get_json(force=True)
    workflow_id = body['workflowId']; collection = body['collection']; record_id = body['recordId']
    user = getattr(flask_g, 'current_user', {})
    with get_db() as conn:
        cur = conn.cursor()
        d = repo.get_definition(cur, workflow_id)
        if not d or not d.get('stages'):
            return jsonify({'error': '工作流不存在或无阶段'}), 404
        # 同一记录已有运行中的实例则拒绝重复启动（否则会产生永远推进不了的孤儿实例）
        existing = repo.find_running_instance_by_record(cur, collection, record_id)
        if existing:
            return jsonify({'error': '该记录已有运行中的工作流实例'}), 409
        first = d['stages'][0]
        inst_id = f'wfi-{uuid.uuid4().hex[:12]}'
        inst = repo.create_instance(cur, inst_id, workflow_id, first['id'], collection, record_id,
                                    user.get('username', ''))
        conn.commit()
        from utils.workflow_engine import _notify_roles
        with get_db() as c2:
            _notify_roles(c2.cursor(), first.get('assignedRoles', []), f'工作流待办：{first["name"]}',
                          '新流程已启动', collection, record_id)
            c2.commit()
        return jsonify(inst), 201


@workflows_bp.route('/workflow/instances', methods=['GET'])
@login_required
def list_instances():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id,workflow_id,status,current_stage_id,chain,history,started_by "
                    "FROM workflow_instances ORDER BY updated_at DESC LIMIT 200")
        out = [{'id': r[0], 'workflowId': r[1], 'status': r[2], 'currentStageId': r[3],
                'chain': r[4] or [], 'history': r[5] or [], 'startedBy': r[6]} for r in cur.fetchall()]
    return jsonify(out)


@workflows_bp.route('/workflow/inbox', methods=['GET'])
@login_required
def inbox():
    user = getattr(flask_g, 'current_user', {})
    role = user.get('role', '')
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT i.id, i.workflow_id, i.current_stage_id, i.chain, d.name, d.stages "
                    "FROM workflow_instances i JOIN workflow_definitions d ON i.workflow_id=d.id "
                    "WHERE i.status='running'")
        items = []
        for iid, wid, csid, chain, wname, stages in cur.fetchall():
            stage = next((s for s in (stages or []) if s['id'] == csid), None)
            if not stage:
                continue
            roles = stage.get('assignedRoles', [])
            if roles and role not in roles:
                continue
            cur_entry = next((e for e in reversed(chain or []) if e.get('stageId') == csid), None)
            items.append({'instanceId': iid, 'workflowName': wname, 'stageName': stage.get('name'),
                          'collection': cur_entry and cur_entry['collection'],
                          'recordId': cur_entry and cur_entry['recordId'],
                          'enteredAt': cur_entry and cur_entry.get('enteredAt')})
    return jsonify(items)
