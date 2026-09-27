"""Admin REST endpoints for managing OpenCode's GLOBAL skills & agents.

Files under OPENCODE_GLOBAL_DIR are the single source of truth (no mirror DB
table — the running serve reads these at startup, so a mirror would only add
a drift risk). All changes take effect after a serve restart; POST /restart
drives that from the page, with an active-workload guard because a restart
interrupts every running agent turn.

Permissions are split per the runtime-config governance spec (§12) instead of
one blanket admin.ai_settings (backend-enforced; the frontend uses the same
keys purely for UI affordance):

  admin.ai_runtime_read     overview / lists / effect status / GET /runtime
  admin.ai_skill_write      skill + auxiliary file create/update/delete
  admin.ai_agent_write      agent create/update/delete/enable/disable
  admin.ai_runtime_publish  platform skill -> global publish
  admin.ai_runtime_apply    POST /runtime/apply (apply pending config)
  admin.ai_runtime_restart  POST /restart
  admin.ai_runtime_force    restart's force flag (interrupt running turns)

Roles holding the legacy admin.ai_settings keep working via the one-off
migration migrations/2026_09_15_split_ai_runtime_permissions.py.
"""
import json
import logging
import os

from flask import Blueprint, g, jsonify, request

from auth import require_permission
from utils import opencode_global as ocg
from utils.operation_log import log_operation

logger = logging.getLogger(__name__)

ai_opencode_admin_bp = Blueprint('ai_opencode_admin', __name__,
                                 url_prefix='/ai/opencode')

_ERROR_STATUS = {'NOT_FOUND': 404, 'ALREADY_EXISTS': 409}


def _err_response(e: ocg.OpenCodeGlobalError):
    return jsonify({'error': e.message, 'code': e.code}), e.status


def _audit(action: str, target_id: str, target_name: str, description: str):
    log_operation(action, 'ai_opencode_runtime', target_id, target_name, description)


@ai_opencode_admin_bp.get('/overview')
@require_permission('admin.ai_runtime_read')
def overview():
    health = ocg.serve_health()
    skills = ocg.merged_skills()
    agents = ocg.merged_agents()
    from config import OPENCODE_RESTART_POLICY
    from utils import opencode_launch
    return jsonify({
        'globalDir': ocg.global_dir(),
        'skillsDir': ocg.skills_dir(),
        'agentsDir': ocg.agents_dir(),
        'skillsDirExists': os.path.isdir(ocg.skills_dir()),
        'agentsDirExists': os.path.isdir(ocg.agents_dir()),
        'serve': health,
        'pendingChanges': skills['pendingCount'] + agents['pendingCount'],
        'restartPolicy': OPENCODE_RESTART_POLICY,
        'serveCmd': opencode_launch.serve_cmd_display(),
        'activeWorkload': ocg.active_workload(),
    })


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

@ai_opencode_admin_bp.get('/skills')
@require_permission('admin.ai_runtime_read')
def list_skills():
    return jsonify(ocg.merged_skills())


@ai_opencode_admin_bp.get('/skills/<name>')
@require_permission('admin.ai_runtime_read')
def get_skill(name):
    try:
        return jsonify(ocg.read_skill(name))
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)


@ai_opencode_admin_bp.post('/skills')
@require_permission('admin.ai_skill_write')
def create_skill():
    if request.files.get('file'):
        f = request.files['file']
        data = f.read()
        overwrite = (request.form.get('overwrite') or '').strip().lower() in ('true', '1', 'on')
        try:
            name = ocg.install_skill_zip_bytes(data, f.filename or '',
                                               overwrite=overwrite)
        except ocg.OpenCodeGlobalError as e:
            return _err_response(e)
        _audit('create', name, name,
               f'安装 OpenCode 全局技能 zip:{name}' + ('（覆盖同名）' if overwrite else ''))
        return jsonify({'name': name, 'changed': True}), 201

    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    try:
        result = ocg.write_skill(name, body.get('description'),
                                 body.get('body') or body.get('content') or '',
                                 content=None)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('create', name, name, f'新建 OpenCode 全局技能:{name}')
    return jsonify(result), 201


@ai_opencode_admin_bp.put('/skills/<name>')
@require_permission('admin.ai_skill_write')
def update_skill(name):
    body = request.get_json(silent=True) or {}
    try:
        if body.get('content') is not None:
            result = ocg.write_skill(name, content=body['content'])
        else:
            result = ocg.write_skill(name, body.get('description'),
                                     body.get('body') or '')
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    if result.get('changed'):
        _audit('update', name, name, f'修改 OpenCode 全局技能:{name}（重启 serve 后生效）')
    return jsonify(result)


@ai_opencode_admin_bp.delete('/skills/<name>')
@require_permission('admin.ai_skill_write')
def delete_skill(name):
    try:
        ocg.delete_skill(name)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('delete', name, name, f'删除 OpenCode 全局技能:{name}')
    return '', 204


@ai_opencode_admin_bp.post('/skills/publish')
@require_permission('admin.ai_runtime_publish')
def publish_platform_skill():
    """Copy a platform global-skill (utils.global_skills storage) into the
    OpenCode global skill dir."""
    body = request.get_json(silent=True) or {}
    skill_id = (body.get('skillId') or '').strip()
    overwrite = bool(body.get('overwrite'))
    if not skill_id:
        return jsonify({'error': 'skillId required'}), 400

    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM global_skills WHERE id = %s", (skill_id,))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': '平台技能不存在'}), 404
    name = row[0]
    from config import AI_WORKSPACE_ROOT
    from utils.global_skills import global_skills_root
    src = os.path.join(global_skills_root(AI_WORKSPACE_ROOT), name)
    try:
        installed = ocg.publish_platform_skill(src, name, overwrite=overwrite)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('create', installed, installed,
           f'发布平台技能到 OpenCode 全局:{installed}')
    return jsonify({'name': installed, 'changed': True}), 201


# --- skill auxiliary files (scripts/templates beyond SKILL.md) ---

@ai_opencode_admin_bp.get('/skills/<name>/files')
@require_permission('admin.ai_runtime_read')
def skill_files(name):
    try:
        return jsonify({'files': ocg.skill_dir_files(name)})
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)


@ai_opencode_admin_bp.get('/skills/<name>/files/<path:rel_path>')
@require_permission('admin.ai_runtime_read')
def skill_file_content(name, rel_path):
    try:
        return jsonify(ocg.read_skill_file(name, rel_path))
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)


@ai_opencode_admin_bp.put('/skills/<name>/files/<path:rel_path>')
@require_permission('admin.ai_skill_write')
def skill_file_write(name, rel_path):
    body = request.get_json(silent=True) or {}
    if body.get('content') is None:
        return jsonify({'error': 'content required'}), 400
    try:
        result = ocg.write_skill_file(name, rel_path, body['content'])
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('update', f'{name}/{result["path"]}', result['path'],
           f'在线编辑 OpenCode 全局技能文件:{name}/{result["path"]}')
    return jsonify(result)


@ai_opencode_admin_bp.delete('/skills/<name>/files/<path:rel_path>')
@require_permission('admin.ai_skill_write')
def skill_file_delete(name, rel_path):
    try:
        ocg.delete_skill_file(name, rel_path)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('delete', f'{name}/{rel_path}', rel_path,
           f'删除 OpenCode 全局技能文件:{name}/{rel_path}')
    return '', 204


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@ai_opencode_admin_bp.get('/agents')
@require_permission('admin.ai_runtime_read')
def list_agents():
    return jsonify(ocg.merged_agents())


@ai_opencode_admin_bp.get('/agents/<name>')
@require_permission('admin.ai_runtime_read')
def get_agent(name):
    try:
        return jsonify(ocg.read_agent(name))
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)


def _agent_write_from_body(body: dict, name: str):
    if body.get('content') is not None:
        return ocg.write_agent(name, content=body['content'])
    fields = {
        'description': body.get('description'),
        'mode': body.get('mode'),
        'model': body.get('model'),
        'temperature': body.get('temperature'),
        'top_p': body.get('topP', body.get('top_p')),
    }
    return ocg.write_agent(name, fields=fields, body=body.get('body') or '')


@ai_opencode_admin_bp.post('/agents')
@require_permission('admin.ai_agent_write')
def create_agent():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    try:
        result = _agent_write_from_body(body, name)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('create', name, name, f'新建 OpenCode 全局 Agent:{name}')
    return jsonify(result), 201


@ai_opencode_admin_bp.put('/agents/<name>')
@require_permission('admin.ai_agent_write')
def update_agent(name):
    body = request.get_json(silent=True) or {}
    try:
        result = _agent_write_from_body(body, name)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    if result.get('changed'):
        _audit('update', name, name, f'修改 OpenCode 全局 Agent:{name}（重启 serve 后生效）')
    return jsonify(result)


@ai_opencode_admin_bp.delete('/agents/<name>')
@require_permission('admin.ai_agent_write')
def delete_agent(name):
    try:
        ocg.delete_agent(name)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('delete', name, name,
           f'删除 OpenCode 全局 Agent 文件:{name}（若为内置开关文件即恢复启用）')
    return '', 204


@ai_opencode_admin_bp.post('/agents/<name>/disable')
@require_permission('admin.ai_agent_write')
def disable_agent(name):
    try:
        result = ocg.set_agent_disabled(name, True)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('update', name, name, f'禁用 Agent:{name}（重启 serve 后生效）')
    return jsonify(result)


@ai_opencode_admin_bp.post('/agents/<name>/enable')
@require_permission('admin.ai_agent_write')
def enable_agent(name):
    try:
        result = ocg.set_agent_disabled(name, False)
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    _audit('update', name, name, f'启用 Agent:{name}（重启 serve 后生效）')
    return jsonify(result)


# ---------------------------------------------------------------------------
# Runtime status, apply & restart
# ---------------------------------------------------------------------------

@ai_opencode_admin_bp.get('/runtime')
@require_permission('admin.ai_runtime_read')
def runtime_status():
    """运行时总状态（Spec §9 GET /ai/opencode/runtime）。

    汇总 serve 健康、进程 ownership（platform=平台托管可安全重启 /
    external=外部进程不可杀 / unknown=无托管记录）、配置代次（受控目录内容
    哈希）、待生效数量与运行中负载。runtimeInSync=True 表示运行时已加载的
    内容与磁盘一致（pendingCount==0 且 serve 健康）——「已保存 ≠ 已生效」
    的判定就在这里。"""
    from utils import opencode_ownership as ownership
    serve = ocg.serve_health()
    skills = ocg.merged_skills()
    agents = ocg.merged_agents()
    pending = skills['pendingCount'] + agents['pendingCount']
    return jsonify({
        'serve': serve,
        'ownership': ownership.status(ocg.serve_port()),
        'configGeneration': ocg.config_generation(),
        'pendingCount': pending,
        'runtimeInSync': bool(serve.get('healthy')) and pending == 0,
        'restartPolicy': _restart_policy(),
        'activeWorkload': ocg.active_workload(),
    })


def _restart_policy():
    from config import OPENCODE_RESTART_POLICY
    return OPENCODE_RESTART_POLICY


@ai_opencode_admin_bp.post('/runtime/apply')
@require_permission('admin.ai_runtime_apply')
def runtime_apply():
    """应用已发布到磁盘的配置（Spec §10.1 手动模式）。

    OpenCode 无热加载，应用 = 受控重启 serve。有运行中负载时拒绝
    （409 ACTIVE_WORKLOAD）——不自动重启、不静默中断，由管理员择时执行。
    本接口等价于 /restart 且不接受 force；强制中断走 /restart + force
    （需 admin.ai_runtime_force）。"""
    workload = ocg.active_workload()
    busy = workload['batchChildren'] + workload['interactiveSessions']
    if busy:
        return jsonify({'error': '当前有正在运行的 AI 会话/批任务，暂不能应用配置',
                        'code': 'ACTIVE_WORKLOAD', 'activeWorkload': workload}), 409
    _audit('update', 'opencode-serve', 'opencode serve',
           f'应用 OpenCode 配置（受控重启 serve，运行中会话 {busy} 个）')
    return _do_restart(force=False)


@ai_opencode_admin_bp.post('/restart')
@require_permission('admin.ai_runtime_restart')
def restart():
    """Restart `opencode serve`. Refuses while agent work is in flight unless
    `force` — a restart kills every running turn (interactive + batch).
    `force` additionally requires admin.ai_runtime_force (Spec §10.3 强制模式:
    默认禁止, 仅高危权限管理员可中断运行中的任务并立即切换配置)."""
    body = request.get_json(silent=True) or {}
    force = bool(body.get('force'))
    if force:
        from utils.permissions import can_admin
        if not can_admin(g.current_user.get('role'), 'admin.ai_runtime_force'):
            return jsonify({'error': '强制重启需要「OpenCode 强制重启」权限',
                            'code': 'FORCE_FORBIDDEN'}), 403
    workload = ocg.active_workload()
    busy = workload['batchChildren'] + workload['interactiveSessions']
    if busy and not force:
        return jsonify({'error': '当前有正在运行的 AI 会话/批任务，重启会中断它们',
                        'code': 'ACTIVE_WORKLOAD', 'activeWorkload': workload}), 409
    _audit('update', 'opencode-serve', 'opencode serve',
           f'重启 OpenCode serve（force={force}，运行中会话 {busy} 个）')
    return _do_restart(force=force)


def _do_restart(force: bool):
    try:
        result = ocg.restart_serve()
    except ocg.OpenCodeGlobalError as e:
        return _err_response(e)
    return jsonify(result)
