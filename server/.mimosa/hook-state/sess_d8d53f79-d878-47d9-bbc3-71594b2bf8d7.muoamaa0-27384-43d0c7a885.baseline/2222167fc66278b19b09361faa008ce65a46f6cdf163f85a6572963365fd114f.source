"""
ETL 任务管理路由

职责：
- ETL 任务 CRUD
- 任务执行（支持 dry_run 测试运行）
- 执行日志查询
"""

from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import require_permission
from utils.operation_log import log_operation
from datetime import datetime, timezone
import psycopg2.extras
import uuid
import json

etl_tasks_bp = Blueprint('etl_tasks', __name__)


def format_ts(dt):
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def task_row_to_dict(row):
    return {
        'id': row[0],
        'name': row[1],
        'description': row[2],
        'steps': row[3] or [],
        'enabled': row[4],
        'lastRunAt': format_ts(row[5]),
        'lastRunStatus': row[6],
        'createdAt': format_ts(row[7]),
        'updatedAt': format_ts(row[8]),
    }


def log_row_to_dict(row):
    return {
        'id': row[0],
        'taskId': row[1],
        'taskName': row[2],
        'status': row[3],
        'startedAt': format_ts(row[4]),
        'finishedAt': format_ts(row[5]),
        'totalRecords': row[6],
        'successCount': row[7],
        'errorCount': row[8],
        'stepResults': row[9] or [],
        'errorDetail': row[10],
        'progressCurrent': row[11],
        'currentStepName': row[12],
    }


TASK_COLS = 'id, name, description, steps, enabled, last_run_at, last_run_status, created_at, updated_at'
LOG_COLS = ('id, task_id, task_name, status, started_at, finished_at, total_records, '
            'success_count, error_count, step_results, error_detail, '
            'progress_current, current_step_name')

ETL_FILE_UPLOAD_EXTENSIONS = {'csv', 'xlsx', 'xls'}


@etl_tasks_bp.route('/etlTasks/files/upload', methods=['POST'])
@require_permission('admin.etl_tasks')
def upload_etl_file():
    """file_upload 步骤专用的文件上传：落到 data_files 表 + 磁盘（复用既有
    存储 helper），权限门禁与本蓝图其余路由一致（admin.etl_tasks），而非
    /data-files/upload 那套按数据页 CRUD 权限判断的模型。"""
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file required'}), 400
    ext = f.filename.lower().rsplit('.', 1)[-1] if '.' in f.filename else ''
    if ext not in ETL_FILE_UPLOAD_EXTENSIONS:
        return jsonify({'error': f'不支持的文件格式: .{ext}（仅支持 csv/xlsx/xls）'}), 400

    from routes.data_files import save_data_file
    meta, err = save_data_file(f, uploaded_by=g.current_user['userId'])
    if err:
        return err
    return jsonify(meta), 201


@etl_tasks_bp.route('/etlTasks', methods=['GET'])
@require_permission('admin.etl_tasks')
def list_tasks():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT {TASK_COLS} FROM etl_tasks ORDER BY created_at DESC')
        rows = cur.fetchall()
    return jsonify([task_row_to_dict(r) for r in rows])


@etl_tasks_bp.route('/etlTasks', methods=['POST'])
@require_permission('admin.etl_tasks')
def create_task():
    body = request.get_json(force=True)
    task_id = f'etl-{uuid.uuid4().hex[:12]}'
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO etl_tasks (id, name, description, steps, enabled, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (
                task_id,
                body.get('name', ''),
                body.get('description', ''),
                psycopg2.extras.Json(body.get('steps', [])),
                body.get('enabled', True),
                now, now,
            ),
        )
    log_operation('create', 'etl_task', task_id, body.get('name', ''),
                  f'新增ETL任务「{body.get("name", "")}」')

    return jsonify({
        'id': task_id,
        'name': body.get('name', ''),
        'description': body.get('description', ''),
        'steps': body.get('steps', []),
        'enabled': body.get('enabled', True),
        'lastRunAt': None,
        'lastRunStatus': None,
        'createdAt': format_ts(now),
        'updatedAt': format_ts(now),
    }), 201


@etl_tasks_bp.route('/etlTasks/<task_id>', methods=['PUT'])
@require_permission('admin.etl_tasks')
def update_task(task_id):
    body = request.get_json(force=True)
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE etl_tasks SET name = %s, description = %s, steps = %s, '
            'enabled = %s, updated_at = %s WHERE id = %s',
            (
                body.get('name', ''),
                body.get('description', ''),
                psycopg2.extras.Json(body.get('steps', [])),
                body.get('enabled', True),
                now, task_id,
            ),
        )
    log_operation('update', 'etl_task', task_id, body.get('name', ''),
                  f'修改ETL任务「{body.get("name", "")}」')

    return jsonify({'id': task_id, **body, 'updatedAt': format_ts(now)})


@etl_tasks_bp.route('/etlTasks/<task_id>', methods=['DELETE'])
@require_permission('admin.etl_tasks')
def delete_task(task_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM etl_tasks WHERE id = %s', (task_id,))
        row = cur.fetchone()
        task_name = row[0] if row else task_id
        cur.execute('DELETE FROM etl_logs WHERE task_id = %s', (task_id,))
        cur.execute('DELETE FROM etl_tasks WHERE id = %s', (task_id,))
    log_operation('delete', 'etl_task', task_id, task_name,
                  f'删除ETL任务「{task_name}」')
    return jsonify({})


DEFAULT_SAMPLE_SIZE = 50


@etl_tasks_bp.route('/etlTasks/<task_id>/run', methods=['POST'])
@require_permission('admin.etl_tasks')
def run_task(task_id):
    body = request.get_json(force=True) if request.data else {}
    dry_run = body.get('dryRun', False)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT {TASK_COLS} FROM etl_tasks WHERE id = %s', (task_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '任务不存在'}), 404

    task = task_row_to_dict(row)

    if dry_run:
        sample_size = body.get('sampleSize') or DEFAULT_SAMPLE_SIZE
        from db import pool
        exec_conn = pool.getconn()
        try:
            from utils.etl_engine import execute_task
            context = execute_task(task, exec_conn, dry_run=True, sample_limit=sample_size)
            exec_conn.rollback()

            status = 'success'
            if context['error'] > 0 and context['success'] > 0:
                status = 'partial'
            elif context['error'] > 0 and context['success'] == 0:
                status = 'error'

            return jsonify({
                'status': status,
                'totalRecords': context['total'],
                'successCount': context['success'],
                'errorCount': context['error'],
                'stepResults': context['step_results'],
                'errors': context['errors'],
            })
        except Exception as e:
            exec_conn.rollback()
            return jsonify({
                'status': 'error',
                'totalRecords': 0,
                'successCount': 0,
                'errorCount': 0,
                'stepResults': [],
                'errors': [str(e)],
            }), 500
        finally:
            pool.putconn(exec_conn)

    # 正式运行：插入一条 pending 日志行立即返回，真正执行交给后台调度器
    # （utils/etl_scheduler.py）。
    log_id = f'etl-log-{uuid.uuid4().hex[:12]}'
    started_at = datetime.now(timezone.utc)
    with get_db() as conn2:
        cur2 = conn2.cursor()
        cur2.execute(
            'INSERT INTO etl_logs (id, task_id, task_name, status, started_at, '
            "total_records, success_count, error_count, step_results, progress_current, cancel_requested) "
            "VALUES (%s, %s, %s, 'pending', %s, 0, 0, 0, '[]'::jsonb, 0, FALSE)",
            (log_id, task_id, task['name'], started_at),
        )

    return jsonify({'logId': log_id, 'status': 'pending'}), 202


@etl_tasks_bp.route('/etlTasks/<task_id>/logs', methods=['GET'])
@require_permission('admin.etl_tasks')
def list_logs(task_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT {LOG_COLS} FROM etl_logs WHERE task_id = %s ORDER BY started_at DESC LIMIT 20',
            (task_id,),
        )
        rows = cur.fetchall()
    return jsonify([log_row_to_dict(r) for r in rows])


@etl_tasks_bp.route('/etlTasks/<task_id>/logs/<log_id>', methods=['GET'])
@require_permission('admin.etl_tasks')
def get_log_detail(task_id, log_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT {LOG_COLS} FROM etl_logs WHERE id = %s AND task_id = %s',
            (log_id, task_id),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '日志不存在'}), 404
    return jsonify(log_row_to_dict(row))


@etl_tasks_bp.route('/etlTasks/<task_id>/logs/<log_id>/cancel', methods=['POST'])
@require_permission('admin.etl_tasks')
def cancel_log(task_id, log_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT status FROM etl_logs WHERE id = %s AND task_id = %s', (log_id, task_id))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '日志不存在'}), 404
        if row[0] not in ('pending', 'running'):
            return jsonify({'error': '任务已结束，无法取消'}), 409
        cur.execute('UPDATE etl_logs SET cancel_requested = TRUE WHERE id = %s', (log_id,))
    return jsonify({'ok': True})
