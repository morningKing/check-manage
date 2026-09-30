"""
Webhook 规则 API 路由

端点：
- GET    /webhook/rules             # 获取 webhook 规则列表
- POST   /webhook/rules             # 创建 webhook 规则
- GET    /webhook/rules/<rule_id>   # 获取规则详情
- PUT    /webhook/rules/<rule_id>   # 更新规则
- DELETE /webhook/rules/<rule_id>   # 删除规则
- POST   /webhook/rules/<rule_id>/test  # 测试规则
- GET    /webhook/rules/<rule_id>/logs  # 获取规则日志

Legacy endpoints (kept for backward compatibility):
- GET    /webhook/settings          # 获取旧配置（映射到第一个规则）
- PUT    /webhook/settings          # 更新旧配置（映射到第一个规则）
- POST   /webhook/test              # 测试旧配置
- GET    /webhook/logs              # 获取所有日志
"""

from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required, require_permission
import psycopg2.extras
import uuid

webhook_bp = Blueprint('webhooks', __name__)


def row_to_dict(row):
    """Convert webhook_rule row to dict"""
    return {
        'id': row[0],
        'name': row[1],
        'description': row[2],
        'enabled': row[3],
        'sourceCollections': row[4] or [],  # JSONB array
        'triggerEvent': row[5],
        'triggerTiming': row[6] or 'after',  # 新增字段
        'triggerCondition': row[7] or {},
        'webhookUrl': row[8],
        'secret': row[9],
        'timeout': row[10],
        'retries': row[11],
        'executionOrder': row[12],
        'rollbackOnFailure': row[13] or False,  # 新增字段
        'createdAt': row[14].isoformat() if row[14] else None,
        'updatedAt': row[15].isoformat() if row[15] else None,
        'createdBy': row[16],
        'updatedBy': row[17],
    }


# ==================== Rules CRUD ====================

@webhook_bp.route('/webhook/rules', methods=['GET'])
@login_required
def list_rules():
    """
    获取 webhook 规则列表
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, name, description, enabled, source_collections, trigger_event, '
                'trigger_timing, trigger_condition, webhook_url, secret, timeout, retries, '
                'execution_order, rollback_on_failure, created_at, updated_at, created_by, updated_by '
                'FROM webhook_rules ORDER BY execution_order'
            )
            rows = cur.fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/rules', methods=['POST'])
@require_permission('admin.webhooks')
def create_rule():
    """
    创建 webhook 规则

    Body:
    {
        "name": "订单创建通知",
        "description": "订单创建时发送通知",
        "enabled": true,
        "sourceCollections": ["orders", "products"],
        "triggerEvent": "create",
        "triggerCondition": {},
        "webhookUrl": "https://example.com/webhook",
        "secret": "",
        "timeout": 30,
        "retries": 3,
        "executionOrder": 0
    }
    """
    try:
        username = g.current_user.get('username', '')
    except (AttributeError, KeyError):
        username = ''

    body = request.get_json(force=True)
    rule_id = body.get('id') or f'whrule-{uuid.uuid4().hex[:12]}'

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO webhook_rules (id, name, description, enabled, source_collections, '
                'trigger_event, trigger_timing, trigger_condition, webhook_url, secret, timeout, retries, '
                'execution_order, rollback_on_failure, created_by) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (rule_id, body.get('name', ''), body.get('description', ''),
                 body.get('enabled', True), psycopg2.extras.Json(body.get('sourceCollections', [])),
                 body.get('triggerEvent', 'create'), body.get('triggerTiming', 'after'),
                 psycopg2.extras.Json(body.get('triggerCondition', {})),
                 body.get('webhookUrl', ''), body.get('secret', ''),
                 body.get('timeout', 30), body.get('retries', 3),
                 body.get('executionOrder', 0), body.get('rollbackOnFailure', False), username)
            )
            conn.commit()

        # Return created rule
        body['id'] = rule_id
        body['createdBy'] = username
        return jsonify(body), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/rules/<rule_id>', methods=['GET'])
@login_required
def get_rule(rule_id):
    """
    获取 webhook 规则详情
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, name, description, enabled, source_collections, trigger_event, '
                'trigger_timing, trigger_condition, webhook_url, secret, timeout, retries, '
                'execution_order, rollback_on_failure, created_at, updated_at, created_by, updated_by '
                'FROM webhook_rules WHERE id = %s',
                (rule_id,)
            )
            row = cur.fetchone()
        if not row:
            return jsonify({'error': '规则不存在'}), 404
        return jsonify(row_to_dict(row))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/rules/<rule_id>', methods=['PUT'])
@require_permission('admin.webhooks')
def update_rule(rule_id):
    """
    更新 webhook 规则
    """
    try:
        username = g.current_user.get('username', '')
    except (AttributeError, KeyError):
        username = ''

    body = request.get_json(force=True)

    sets = []
    params = []

    for key, col in [
        ('name', 'name'),
        ('description', 'description'),
        ('enabled', 'enabled'),
        ('triggerEvent', 'trigger_event'),
        ('triggerTiming', 'trigger_timing'),
        ('rollbackOnFailure', 'rollback_on_failure'),
        ('webhookUrl', 'webhook_url'),
        ('secret', 'secret'),
        ('timeout', 'timeout'),
        ('retries', 'retries'),
        ('executionOrder', 'execution_order'),
    ]:
        if key in body:
            sets.append(f'{col} = %s')
            params.append(body[key])

    for key, col in [
        ('triggerCondition', 'trigger_condition'),
        ('sourceCollections', 'source_collections'),
    ]:
        if key in body:
            sets.append(f'{col} = %s')
            params.append(psycopg2.extras.Json(body[key]))

    sets.append('updated_at = NOW()')
    sets.append('updated_by = %s')
    params.append(username)
    params.append(rule_id)

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                f'UPDATE webhook_rules SET {", ".join(sets)} WHERE id = %s',
                params
            )
            conn.commit()

        # Return updated rule
        return jsonify(body)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/rules/<rule_id>', methods=['DELETE'])
@require_permission('admin.webhooks')
def delete_rule(rule_id):
    """
    删除 webhook 规则
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM webhook_rules WHERE id = %s', (rule_id,))
            conn.commit()
        return jsonify({})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/rules/<rule_id>/test', methods=['POST'])
@require_permission('admin.webhooks')
def test_rule(rule_id):
    """
    测试 webhook 规则

    Body (optional):
    {
        "customPayload": {...}  # 自定义测试 payload
    }
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT name, webhook_url, secret, timeout, retries, source_collections '
                'FROM webhook_rules WHERE id = %s',
                (rule_id,)
            )
            row = cur.fetchone()
        if not row:
            return jsonify({'error': '规则不存在'}), 404

        name, webhook_url, secret, timeout, retries, source_collections = row

        body = request.get_json(force=True) or {}

        # Build test payload
        test_payload = body.get('customPayload') or {
            'event': 'test',
            'timestamp': '2024-01-01T00:00:00.000Z',
            'ruleId': rule_id,
            'ruleName': name,
            'message': '这是一个测试 webhook 调用',
            'collections': source_collections or [],
        }

        # Import and call webhook engine
        from utils.webhook_engine import _fire_single_webhook
        result = _fire_single_webhook(
            rule_id, name, webhook_url, secret,
            'test', test_payload, timeout, retries
        )

        return jsonify({
            'success': result['success'],
            'logId': result['logId'],
            'responseStatus': result['responseStatus'],
            'errorMessage': result['errorMessage'],
            'retryCount': result['retryCount'],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/rules/<rule_id>/logs', methods=['GET'])
@login_required
def get_rule_logs(rule_id):
    """
    获取 webhook 规则的调用日志

    Query params:
    - success: 过滤成功/失败（true/false）
    - limit: 返回数量限制（默认 50）
    """
    success_param = request.args.get('success')
    limit = min(int(request.args.get('limit', 50)), 200)

    conditions = ['rule_id = %s']
    params = [rule_id]

    if success_param == 'true':
        conditions.append('success = TRUE')
    elif success_param == 'false':
        conditions.append('success = FALSE')

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                f'''SELECT id, rule_id, rule_name, webhook_url, event_type, request_payload,
                      response_status, response_body, error_message, duration_ms,
                      retry_count, success, created_at
                   FROM webhook_logs WHERE {"".join(conditions)}
                   ORDER BY created_at DESC LIMIT %s''',
                params + [limit]
            )
            rows = cur.fetchall()

        return jsonify({
            'logs': [
                {
                    'id': r[0],
                    'ruleId': r[1],
                    'ruleName': r[2],
                    'webhookUrl': r[3],
                    'eventType': r[4],
                    'requestPayload': r[5],
                    'responseStatus': r[6],
                    'responseBody': r[7],
                    'errorMessage': r[8],
                    'durationMs': r[9],
                    'retryCount': r[10],
                    'success': r[11],
                    'createdAt': r[12].isoformat() if r[12] else None,
                }
                for r in rows
            ]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== Legacy Endpoints ====================
# Kept for backward compatibility with old single-setting approach

@webhook_bp.route('/webhook/settings', methods=['GET'])
@login_required
def get_settings():
    """
    获取旧 webhook 配置（映射到第一个启用的 merge 规则）
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, name, webhook_url, secret, timeout, retries, enabled '
                'FROM webhook_rules WHERE trigger_event = %s AND enabled = TRUE '
                'ORDER BY execution_order LIMIT 1',
                ('merge',)
            )
            row = cur.fetchone()

        if row:
            return jsonify({
                'enabled': row[6],
                'name': row[1],
                'webhookUrl': row[2],
                'secret': row[3],
                'events': ['merge'],
                'timeout': row[4],
                'retries': row[5],
            })
        else:
            # Return default empty settings
            return jsonify({
                'enabled': False,
                'name': '',
                'webhookUrl': '',
                'secret': '',
                'events': ['merge'],
                'timeout': 30,
                'retries': 3,
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/settings', methods=['PUT'])
@require_permission('admin.webhooks')
def update_settings():
    """
    更新旧 webhook 配置（创建或更新第一个 merge 规则）
    """
    try:
        username = g.current_user.get('username', '')
    except (AttributeError, KeyError):
        username = ''

    body = request.get_json(force=True)

    try:
        with get_db() as conn:
            cur = conn.cursor()

            # Check if merge rule exists
            cur.execute(
                'SELECT id FROM webhook_rules WHERE trigger_event = %s '
                'ORDER BY execution_order LIMIT 1',
                ('merge',)
            )
            existing = cur.fetchone()

            if existing:
                # Update existing
                rule_id = existing[0]
                cur.execute(
                    'UPDATE webhook_rules SET name = %s, webhook_url = %s, secret = %s, '
                    'timeout = %s, retries = %s, enabled = %s, updated_at = NOW(), updated_by = %s '
                    'WHERE id = %s',
                    (body.get('name', '合并通知'), body.get('webhookUrl', ''),
                     body.get('secret', ''), body.get('timeout', 30),
                     body.get('retries', 3), body.get('enabled', False),
                     username, rule_id)
                )
            else:
                # Create new
                rule_id = f'whrule-{uuid.uuid4().hex[:12]}'
                cur.execute(
                    'INSERT INTO webhook_rules (id, name, enabled, trigger_event, webhook_url, '
                    'secret, timeout, retries, created_by) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (rule_id, body.get('name', '合并通知'), body.get('enabled', False),
                     'merge', body.get('webhookUrl', ''), body.get('secret', ''),
                     body.get('timeout', 30), body.get('retries', 3), username)
                )

            conn.commit()

        return jsonify(body)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/test', methods=['POST'])
@require_permission('admin.webhooks')
def test_webhook():
    """
    测试 webhook 调用（使用 merge 规则）
    """
    body = request.get_json(force=True) or {}

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

    try:
        with get_db() as conn:
            cur = conn.cursor()

            # Use provided URL or get from merge rule
            webhook_url = body.get('webhookUrl')
            if not webhook_url:
                cur.execute(
                    'SELECT id, name, webhook_url, secret, timeout, retries '
                    'FROM webhook_rules WHERE trigger_event = %s AND enabled = TRUE '
                    'ORDER BY execution_order LIMIT 1',
                    ('merge',)
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({'success': False, 'message': '未找到启用的 merge webhook 规则'})

                rule_id, name, webhook_url, secret, timeout, retries = row
            else:
                # Use first rule's settings with custom URL
                cur.execute(
                    'SELECT id, name, secret, timeout, retries '
                    'FROM webhook_rules WHERE trigger_event = %s '
                    'ORDER BY execution_order LIMIT 1',
                    ('merge',)
                )
                row = cur.fetchone()
                if row:
                    rule_id, name, secret, timeout, retries = row
                else:
                    # Create temporary test config
                    rule_id = 'test'
                    name = 'Test'
                    secret = ''
                    timeout = 30
                    retries = 3

        from utils.webhook_engine import _fire_single_webhook
        result = _fire_single_webhook(
            rule_id, name, webhook_url, secret,
            'test', test_payload, timeout, retries
        )

        return jsonify({
            'success': result['success'],
            'logId': result['logId'],
            'responseStatus': result['responseStatus'],
            'errorMessage': result['errorMessage'],
            'retryCount': result['retryCount'],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/logs', methods=['GET'])
@login_required
def list_logs():
    """
    获取所有 webhook 调用日志

    Query params:
    - eventType: 过滤事件类型
    - success: 过滤成功/失败（true/false）
    - limit: 返回数量限制（默认 50）
    - offset: 偏移量
    """
    event_type = request.args.get('eventType')
    success_param = request.args.get('success')
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    conditions = []
    params = []

    if event_type:
        conditions.append('event_type = %s')
        params.append(event_type)
    if success_param == 'true':
        conditions.append('success = TRUE')
    elif success_param == 'false':
        conditions.append('success = FALSE')

    where_clause = f'WHERE {" AND ".join(conditions)}' if conditions else ''

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                f'''SELECT id, rule_id, rule_name, webhook_url, event_type, request_payload,
                      response_status, response_body, error_message, duration_ms,
                      retry_count, success, created_at
                   FROM webhook_logs {where_clause}
                   ORDER BY created_at DESC
                   LIMIT %s OFFSET %s''',
                params + [limit, offset]
            )
            rows = cur.fetchall()

            # Get total count
            cur.execute(
                f'SELECT COUNT(*) FROM webhook_logs {where_clause}',
                params
            )
            total = cur.fetchone()[0]

        return jsonify({
            'logs': [
                {
                    'id': r[0],
                    'ruleId': r[1],
                    'ruleName': r[2],
                    'webhookUrl': r[3],
                    'eventType': r[4],
                    'requestPayload': r[5],
                    'responseStatus': r[6],
                    'responseBody': r[7],
                    'errorMessage': r[8],
                    'durationMs': r[9],
                    'retryCount': r[10],
                    'success': r[11],
                    'createdAt': r[12].isoformat() if r[12] else None,
                }
                for r in rows
            ],
            'total': total,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/webhook/logs/<log_id>', methods=['GET'])
@login_required
def get_log_detail(log_id):
    """
    获取 webhook 调用日志详情
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                '''SELECT id, rule_id, rule_name, webhook_url, event_type, request_payload,
                      response_status, response_body, error_message, duration_ms,
                      retry_count, success, created_at
                   FROM webhook_logs WHERE id = %s''',
                (log_id,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': '日志不存在'}), 404

            return jsonify({
                'id': row[0],
                'ruleId': row[1],
                'ruleName': row[2],
                'webhookUrl': row[3],
                'eventType': row[4],
                'requestPayload': row[5],
                'responseStatus': row[6],
                'responseBody': row[7],
                'errorMessage': row[8],
                'durationMs': row[9],
                'retryCount': row[10],
                'success': row[11],
                'createdAt': row[12].isoformat() if row[12] else None,
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500