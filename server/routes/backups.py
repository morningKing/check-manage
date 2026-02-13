"""
备份管理 API 路由

所有端点仅管理员可访问。
"""

import os
import tempfile
from flask import Blueprint, request, jsonify, send_file
from db import get_db
from auth import admin_required
from utils.backup import (
    create_backup,
    restore_backup,
    delete_backup_file,
    get_backup_settings,
    update_backup_settings,
    BACKUP_DIR,
)

backups_bp = Blueprint('backups', __name__)


def _row_to_dict(row):
    """将备份记录行转为 dict"""
    return {
        'id': row[0],
        'name': row[1],
        'type': row[2],
        'status': row[3],
        'fileSize': row[5] or 0,
        'tablesCount': row[6] or 0,
        'recordsCount': row[7] or 0,
        'createdBy': row[8],
        'createdAt': row[9].isoformat() if row[9] else None,
        'note': row[10],
    }


@backups_bp.route('/backups', methods=['GET'])
@admin_required
def list_backups():
    """获取备份列表（按时间倒序）"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, type, status, file_path, file_size, '
            'tables_count, records_count, created_by, created_at, note '
            'FROM backups ORDER BY created_at DESC'
        )
        rows = cur.fetchall()
    return jsonify([_row_to_dict(r) for r in rows])


@backups_bp.route('/backups', methods=['POST'])
@admin_required
def create_manual_backup():
    """创建手动备份"""
    from flask import g
    body = request.get_json(silent=True) or {}
    note = body.get('note')
    created_by = g.current_user.get('username', 'admin')

    try:
        result = create_backup(backup_type='manual', created_by=created_by)
        # 更新备注
        if note:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute('UPDATE backups SET note = %s WHERE id = %s', (note, result['id']))
            result['note'] = note
        return jsonify(result), 201
    except Exception as e:
        return jsonify({'error': f'备份失败: {str(e)}'}), 500


@backups_bp.route('/backups/<backup_id>', methods=['DELETE'])
@admin_required
def delete_backup(backup_id):
    """删除备份（文件+记录）"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_path FROM backups WHERE id = %s', (backup_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '备份不存在'}), 404
        delete_backup_file(row[0])
        cur.execute('DELETE FROM backups WHERE id = %s', (backup_id,))
    return jsonify({})


@backups_bp.route('/backups/<backup_id>/download', methods=['GET'])
@admin_required
def download_backup(backup_id):
    """下载备份 ZIP 文件"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_path, name FROM backups WHERE id = %s', (backup_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '备份不存在'}), 404
    file_path = row[0]
    if not file_path or not os.path.isfile(file_path):
        return jsonify({'error': '备份文件不存在'}), 404
    filename = f'{row[1]}.zip'
    return send_file(file_path, as_attachment=True, download_name=filename)


@backups_bp.route('/backups/<backup_id>/restore', methods=['POST'])
@admin_required
def restore_from_backup(backup_id):
    """从已有备份还原"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_path FROM backups WHERE id = %s', (backup_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '备份不存在'}), 404
    file_path = row[0]
    if not file_path or not os.path.isfile(file_path):
        return jsonify({'error': '备份文件不存在'}), 404

    try:
        manifest = restore_backup(file_path)
        return jsonify({
            'message': '还原成功',
            'manifest': manifest,
        })
    except Exception as e:
        return jsonify({'error': f'还原失败: {str(e)}'}), 500


@backups_bp.route('/backups/upload-restore', methods=['POST'])
@admin_required
def upload_and_restore():
    """上传外部 ZIP 并还原"""
    if 'file' not in request.files:
        return jsonify({'error': '请上传备份文件'}), 400
    file = request.files['file']
    if not file.filename or not file.filename.endswith('.zip'):
        return jsonify({'error': '请上传 ZIP 格式的备份文件'}), 400

    # 保存到临时文件
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.zip')
    try:
        os.close(tmp_fd)
        file.save(tmp_path)
        manifest = restore_backup(tmp_path)
        return jsonify({
            'message': '还原成功',
            'manifest': manifest,
        })
    except Exception as e:
        return jsonify({'error': f'还原失败: {str(e)}'}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@backups_bp.route('/backups/settings', methods=['GET'])
@admin_required
def get_settings():
    """获取定时备份设置"""
    settings = get_backup_settings()
    return jsonify(settings)


@backups_bp.route('/backups/settings', methods=['PUT'])
@admin_required
def update_settings():
    """更新定时备份设置"""
    body = request.get_json(force=True)
    enabled = body.get('enabled', False)
    interval = body.get('interval', 'daily')
    retention_count = body.get('retentionCount', 10)

    if interval not in ('daily', 'weekly', 'monthly'):
        return jsonify({'error': '无效的备份周期'}), 400
    if not isinstance(retention_count, int) or retention_count < 1:
        return jsonify({'error': '保留数量必须为正整数'}), 400

    settings = update_backup_settings(enabled, interval, retention_count)
    return jsonify(settings)
