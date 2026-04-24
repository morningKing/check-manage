"""
Webhook 配置 API 路由

端点：
- GET    /webhook/settings         # 获取 webhook 配置
- PUT    /webhook/settings         # 更新 webhook 配置
- POST   /webhook/test             # 测试 webhook 调用
- GET    /webhook/logs             # 获取 webhook 调用日志
"""

from flask import Blueprint, request, jsonify, g
from auth import login_required, admin_required
from utils.webhook import (
    get_webhook_settings,
    update_webhook_settings,
    fire_webhook,
    get_webhook_logs,
)

webhook_bp = Blueprint('webhooks', __name__)


@webhook_bp.route('/webhook/settings', methods=['GET'])
@login_required
def get_settings():
    """
    获取 webhook 配置
    """
    try:
        settings = get_webhook_settings()
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/settings', methods=['PUT'])
@admin_required
def update_settings():
    """
    更新 webhook 配置

    Body:
    {
        "enabled": true,
        "name": "合并通知",
        "webhookUrl": "https://example.com/webhook",
        "secret": "my-secret",
        "events": ["merge"],
        "timeout": 30,
        "retries": 3
    }
    """
    try:
        username = g.current_user.get('username')
    except (AttributeError, KeyError):
        username = None

    body = request.get_json(force=True)

    try:
        settings = update_webhook_settings(
            enabled=body.get('enabled'),
            name=body.get('name'),
            webhook_url=body.get('webhookUrl'),
            secret=body.get('secret'),
            events=body.get('events'),
            timeout=body.get('timeout'),
            retries=body.get('retries'),
            updated_by=username,
        )
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/test', methods=['POST'])
@admin_required
def test_webhook():
    """
    测试 webhook 调用

    Body:
    {
        "webhookUrl": "https://example.com/webhook",  # 可选，不提供则使用配置的 URL
        "event": "merge"  # 可选，默认 merge
    }
    """
    body = request.get_json(force=True) or {}

    # 构建测试 payload
    test_payload = {
        'event': 'test',
        'timestamp': '2024-01-01T00:00:00.000Z',
        'message': '这是一个测试 webhook 调用',
        'project': {
            'menuId': 'test-project',
            'name': '测试项目',
        },
        'version': {
            'id': 'test-version',
            'name': '测试版本',
        },
        'summary': {
            'totalRecords': 0,
            'recordsCreated': 0,
            'recordsUpdated': 0,
            'recordsDeleted': 0,
        },
    }

    # 如果提供了自定义 URL，临时修改配置
    custom_url = body.get('webhookUrl')
    if custom_url:
        # 临时更新 URL
        original_settings = get_webhook_settings()
        update_webhook_settings(webhook_url=custom_url, enabled=True)

    try:
        result = fire_webhook('merge', test_payload)
        return jsonify(result)
    finally:
        # 如果使用了自定义 URL，恢复原配置
        if custom_url:
            update_webhook_settings(webhook_url=original_settings['webhookUrl'])


@webhook_bp.route('/webhook/logs', methods=['GET'])
@login_required
def list_logs():
    """
    获取 webhook 调用日志列表

    Query params:
    - eventType: 过滤事件类型
    - success: 过滤成功/失败（true/false）
    - limit: 返回数量限制（默认 50，最大 200）
    - offset: 偏移量
    """
    event_type = request.args.get('eventType')
    success_param = request.args.get('success')
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    success = None
    if success_param == 'true':
        success = True
    elif success_param == 'false':
        success = False

    try:
        logs = get_webhook_logs(event_type, success, limit, offset)
        return jsonify({'logs': logs, 'total': len(logs)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/logs/<log_id>', methods=['GET'])
@admin_required
def get_log_detail(log_id):
    """
    获取 webhook 调用日志详情
    """
    from db import get_db

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                '''SELECT id, webhook_url, event_type, request_payload, response_status,
                      response_body, error_message, duration_ms, retry_count, success, created_at
                   FROM webhook_logs WHERE id = %s''',
                (log_id,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': '日志不存在'}), 404

            return jsonify({
                'id': row[0],
                'webhookUrl': row[1],
                'eventType': row[2],
                'requestPayload': row[3],
                'responseStatus': row[4],
                'responseBody': row[5],
                'errorMessage': row[6],
                'durationMs': row[7],
                'retryCount': row[8],
                'success': row[9],
                'createdAt': row[10].isoformat() if row[10] else None,
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500