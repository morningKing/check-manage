"""行操作对外触发（/api/v1/collections/<collection>/<recordId>/row-actions/<actionId>/run）。

独立成一个蓝图文件，不并进 routes/open_api.py：本端点直接透传
utils/row_action_engine.py::RowActionError.message（该模块里硬编码的是中文），
和 open-api.md 里"全英文 error"的既有承诺冲突；单独成文档更容易讲清楚这一处例外。

API Key 没有角色概念，run_action 的 role-白名单校验按"密钥属主本人的角色"解析
（SELECT users.role），等价于"这把密钥的操作权限=它绑定的用户本人"，和批任务
API（/api/v1/ai-batches/*）的归属模型一致。

不提供行操作发现/列表端点：actionId 需要调用方从配置该行操作的管理员那里线下
拿到，和 scanTaskId/webhookRuleId 的既有获取方式一致，控制范围。
"""

from flask import Blueprint, jsonify, request

from auth import api_key_required, require_bound_key
from db import get_db
from routes.open_api import check_collection_writable, get_request_branch_id
from utils.api_errors import (CONFLICT, FORBIDDEN, INTERNAL_ERROR, INVALID_ARGUMENT,
                              NOT_FOUND, err, register_error_handlers)
from utils.operation_log import log_api_operation
from utils.row_action_engine import run_action, resolve_status_gate, RowActionError

open_api_row_actions_bp = Blueprint(
    'open_api_row_actions', __name__, url_prefix='/v1/collections')
register_error_handlers(open_api_row_actions_bp)


def _current_key() -> dict:
    from flask import g
    return getattr(g, 'api_key_info', {}) or {}


def _load_row_action_context(collection, record_id, branch_id):
    """同构于 routes/dynamic.py::_load_row_action_context，独立一份而不是
    跨路由文件 import——那是一处已经在生产上跑的内部路由，为复用而改动它的
    风险高于复制这十来行 SQL。"""
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT row_actions FROM page_configs WHERE id = %s', (page_id,))
        row = cur.fetchone()
        row_actions = (row[0] if row else None) or []
        cur.execute(
            'SELECT data FROM dynamic_data '
            'WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, record_id, branch_id),
        )
        rec = cur.fetchone()
    return row_actions, (rec[0] if rec else None)


_STATUS_TO_CODE = {400: INVALID_ARGUMENT, 403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT}


@open_api_row_actions_bp.route(
    '/<collection>/<record_id>/row-actions/<action_id>/run', methods=['POST'])
@api_key_required
@require_bound_key
def run_row_action(collection, record_id, action_id):
    key = _current_key()

    with get_db() as conn:
        cur = conn.cursor()
        error_resp = check_collection_writable(cur, collection)
        if error_resp:
            return error_resp
        branch_id, branch_error = get_request_branch_id(cur)
        if branch_error:
            return branch_error

    row_actions, row_data = _load_row_action_context(collection, record_id, branch_id)
    action = next((a for a in row_actions if a.get('id') == action_id), None)
    if not action:
        return err('行操作不存在', NOT_FOUND, 404)
    if row_data is None:
        return err('记录不存在', NOT_FOUND, 404)

    role = key.get('ownerRole')
    body = request.get_json(silent=True) or {}

    try:
        status = run_action(
            collection, record_id, action, row_data,
            role=role,
            operator=f"api-key:{key.get('name') or key['id']}",
            branch_id=branch_id,
            params=body.get('params') or {},
        )
    except RowActionError as e:
        return err(e.message, _STATUS_TO_CODE.get(e.http_status, INTERNAL_ERROR), e.http_status)

    status_field = running_value = None
    if status == 'running':
        try:
            status_field, running_value, _task, _is_ai = resolve_status_gate(action)
        except RowActionError:
            pass

    log_api_operation('update', 'dynamic_data', record_id, action.get('name'),
                      f'通过 API Key 触发行操作「{action.get("name")}」（{collection}/{record_id}）')
    return jsonify({
        'ok': True, 'status': status,
        'statusField': status_field, 'runningValue': running_value,
    })
