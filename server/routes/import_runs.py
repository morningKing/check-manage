"""
导入历史 API 路由

端点：
- POST   /importRuns                        创建一条导入历史记录（含失败明细）
- GET    /importRuns                        按数据页分页列出历史记录（不含失败明细）
- GET    /importRuns/<run_id>               单条记录详情（含失败明细）
- POST   /importRuns/<run_id>/retry-result  重试后同步：删除已解决的失败行，更新计数
"""
from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required
from utils.rbac_guard import require_page_action
import psycopg2.extras
import uuid

import_runs_bp = Blueprint('import_runs', __name__)


def _run_to_dict(row):
    return {
        'id': row[0],
        'pageId': row[1],
        'collection': row[2],
        'branchId': row[3],
        'fileName': row[4],
        'successCount': row[5],
        'createdCount': row[6],
        'updatedCount': row[7],
        'failedCount': row[8],
        'status': row[9],
        'operator': row[10],
        'createdAt': row[11].isoformat() if row[11] else None,
    }


@import_runs_bp.route('/importRuns', methods=['POST'])
@login_required
def create_import_run():
    body = request.get_json(force=True)
    collection = body.get('collection', '')
    denied = require_page_action(collection, 'create')
    if denied:
        return denied

    try:
        username = g.current_user.get('username', '')
    except (AttributeError, KeyError):
        username = ''

    failed_count = int(body.get('failedCount', 0))
    status = 'success' if failed_count == 0 else 'partial'
    run_id = f'imprun-{uuid.uuid4().hex[:12]}'
    failures = body.get('failures') or []

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO import_runs (id, page_id, collection, branch_id, file_name, '
                'success_count, created_count, updated_count, failed_count, status, operator) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (run_id, body.get('pageId', ''), collection, body.get('branchId') or 'main',
                 body.get('fileName', ''), int(body.get('successCount', 0)),
                 int(body.get('createdCount', 0)), int(body.get('updatedCount', 0)),
                 failed_count, status, username)
            )
            if failures:
                values = [
                    (f'imprunf-{uuid.uuid4().hex[:12]}', run_id, f.get('recordId', ''),
                     psycopg2.extras.Json(f.get('originalRecord', {})),
                     psycopg2.extras.Json(f.get('payload', {})), f.get('reason', ''))
                    for f in failures
                ]
                psycopg2.extras.execute_values(
                    cur,
                    'INSERT INTO import_run_failures (id, run_id, record_id, original_record, payload, reason) VALUES %s',
                    values
                )
            conn.commit()
        return jsonify({'id': run_id}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@import_runs_bp.route('/importRuns', methods=['GET'])
@login_required
def list_import_runs():
    page_id = request.args.get('pageId', '')
    collection = request.args.get('collection', '')
    denied = require_page_action(collection, 'read')
    if denied:
        return denied

    limit = min(int(request.args.get('limit', 20)), 100)
    offset = int(request.args.get('offset', 0))

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, page_id, collection, branch_id, file_name, success_count, '
                'created_count, updated_count, failed_count, status, operator, created_at '
                'FROM import_runs WHERE page_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s',
                (page_id, limit, offset)
            )
            rows = cur.fetchall()
            cur.execute('SELECT COUNT(*) FROM import_runs WHERE page_id = %s', (page_id,))
            total = cur.fetchone()[0]
        return jsonify({'runs': [_run_to_dict(r) for r in rows], 'total': total})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@import_runs_bp.route('/importRuns/<run_id>', methods=['GET'])
@login_required
def get_import_run_detail(run_id):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, page_id, collection, branch_id, file_name, success_count, '
                'created_count, updated_count, failed_count, status, operator, created_at '
                'FROM import_runs WHERE id = %s',
                (run_id,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': '记录不存在'}), 404

            denied = require_page_action(row[2], 'read')
            if denied:
                return denied

            cur.execute(
                'SELECT record_id, original_record, payload, reason, created_at '
                'FROM import_run_failures WHERE run_id = %s ORDER BY created_at',
                (run_id,)
            )
            failure_rows = cur.fetchall()
        return jsonify({
            'run': _run_to_dict(row),
            'failures': [
                {
                    'recordId': fr[0], 'originalRecord': fr[1], 'payload': fr[2],
                    'reason': fr[3], 'createdAt': fr[4].isoformat() if fr[4] else None,
                }
                for fr in failure_rows
            ],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@import_runs_bp.route('/importRuns/<run_id>/retry-result', methods=['POST'])
@login_required
def sync_retry_result(run_id):
    body = request.get_json(force=True)
    resolved_ids = body.get('resolvedRecordIds') or []
    success_delta = int(body.get('successDelta', 0))
    created_delta = int(body.get('createdDelta', 0))
    updated_delta = int(body.get('updatedDelta', 0))

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT collection FROM import_runs WHERE id = %s', (run_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': '记录不存在'}), 404

            denied = require_page_action(row[0], 'create')
            if denied:
                return denied

            if resolved_ids:
                cur.execute(
                    'DELETE FROM import_run_failures WHERE run_id = %s AND record_id = ANY(%s)',
                    (run_id, resolved_ids)
                )
                cur.execute(
                    'UPDATE import_runs SET '
                    'success_count = success_count + %s, created_count = created_count + %s, '
                    'updated_count = updated_count + %s, '
                    'failed_count = GREATEST(failed_count - %s, 0), '
                    "status = CASE WHEN failed_count - %s <= 0 THEN 'success' ELSE 'partial' END "
                    'WHERE id = %s',
                    (success_delta, created_delta, updated_delta, len(resolved_ids), len(resolved_ids), run_id)
                )
                conn.commit()

            cur.execute(
                'SELECT success_count, created_count, updated_count, failed_count, status '
                'FROM import_runs WHERE id = %s',
                (run_id,)
            )
            updated = cur.fetchone()
        return jsonify({
            'successCount': updated[0], 'createdCount': updated[1],
            'updatedCount': updated[2], 'failedCount': updated[3], 'status': updated[4],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
