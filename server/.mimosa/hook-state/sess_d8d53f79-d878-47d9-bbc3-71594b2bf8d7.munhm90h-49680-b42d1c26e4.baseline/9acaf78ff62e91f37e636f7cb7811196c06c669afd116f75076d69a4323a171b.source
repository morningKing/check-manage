"""
Webhook 调用工具模块

提供 webhook 配置读取、调用、日志记录等功能。
"""

import uuid
import json
import time
import hashlib
import hmac
import requests
from datetime import datetime, timezone
from typing import Optional
from db import get_db


def get_webhook_settings() -> dict:
    """
    获取 webhook 配置

    Returns
    -------
    dict
        {
            'enabled': bool,
            'name': str,
            'webhookUrl': str,
            'secret': str,
            'events': list[str],
            'timeout': int,
            'retries': int,
        }
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT enabled, name, webhook_url, secret, events, timeout, retries '
            'FROM webhook_settings WHERE id = 1'
        )
        row = cur.fetchone()
        if not row:
            return {
                'enabled': False,
                'name': '',
                'webhookUrl': '',
                'secret': '',
                'events': [],
                'timeout': 30,
                'retries': 3,
            }
        return {
            'enabled': row[0],
            'name': row[1],
            'webhookUrl': row[2],
            'secret': row[3],
            'events': row[4] or [],
            'timeout': row[5],
            'retries': row[6],
        }


def update_webhook_settings(
    enabled: Optional[bool] = None,
    name: Optional[str] = None,
    webhook_url: Optional[str] = None,
    secret: Optional[str] = None,
    events: Optional[list] = None,
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
    updated_by: Optional[str] = None,
) -> dict:
    """
    更新 webhook 配置

    Parameters
    ----------
    enabled : bool, optional
        是否启用
    name : str, optional
        Webhook 名称
    webhook_url : str, optional
        Webhook URL
    secret : str, optional
        签名密钥
    events : list, optional
        触发事件列表
    timeout : int, optional
        超时时间（秒）
    retries : int, optional
        重试次数
    updated_by : str, optional
        更新者

    Returns
    -------
    dict
        更新后的配置
    """
    with get_db() as conn:
        cur = conn.cursor()

        updates = []
        params = []

        if enabled is not None:
            updates.append('enabled = %s')
            params.append(enabled)
        if name is not None:
            updates.append('name = %s')
            params.append(name)
        if webhook_url is not None:
            updates.append('webhook_url = %s')
            params.append(webhook_url)
        if secret is not None:
            updates.append('secret = %s')
            params.append(secret)
        if events is not None:
            updates.append('events = %s')
            params.append(json.dumps(events))
        if timeout is not None:
            updates.append('timeout = %s')
            params.append(timeout)
        if retries is not None:
            updates.append('retries = %s')
            params.append(retries)
        if updated_by is not None:
            updates.append('updated_by = %s')
            params.append(updated_by)

        updates.append('updated_at = NOW()')

        if updates:
            cur.execute(
                f'UPDATE webhook_settings SET {", ".join(updates)} WHERE id = 1',
                params
            )
            conn.commit()

        return get_webhook_settings()


def fire_webhook(event_type: str, payload: dict) -> dict:
    """
    触发 webhook 调用

    Parameters
    ----------
    event_type : str
        事件类型（如 'merge', 'create', 'update'）
    payload : dict
        发送的数据

    Returns
    -------
    dict
        {
            'success': bool,
            'logId': str,
            'responseStatus': int,
            'errorMessage': str,
        }
    """
    settings = get_webhook_settings()

    if not settings['enabled']:
        return {'success': True, 'logId': None, 'message': 'Webhook 未启用'}

    if event_type not in settings['events']:
        return {'success': True, 'logId': None, 'message': f'事件 {event_type} 未配置触发'}

    webhook_url = settings['webhookUrl']
    if not webhook_url:
        return {'success': False, 'logId': None, 'message': 'Webhook URL 未配置'}

    log_id = f'wh-{uuid.uuid4().hex[:12]}'
    timeout = settings['timeout']
    retries = settings['retries']
    secret = settings['secret']

    # 计算签名
    timestamp = int(time.time())
    payload_json = json.dumps(payload, ensure_ascii=False)
    signature = _compute_signature(timestamp, payload_json, secret)

    headers = {
        'Content-Type': 'application/json',
        'X-Webhook-Timestamp': str(timestamp),
        'X-Webhook-Signature': signature,
        'X-Webhook-Event': event_type,
    }

    success = False
    response_status = None
    response_body = None
    error_message = None
    duration_ms = 0
    retry_count = 0

    for attempt in range(retries + 1):
        start_time = time.time()

        try:
            resp = requests.post(
                webhook_url,
                data=payload_json.encode('utf-8'),
                headers=headers,
                timeout=timeout,
            )
            response_status = resp.status_code
            response_body = resp.text[:2000]  # 限制响应体长度
            duration_ms = int((time.time() - start_time) * 1000)

            if resp.status_code >= 200 and resp.status_code < 300:
                success = True
                break
            else:
                error_message = f'HTTP {resp.status_code}: {resp.text[:200]}'

        except requests.exceptions.Timeout:
            duration_ms = timeout * 1000
            error_message = '请求超时'
        except requests.exceptions.RequestException as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_message = str(e)[:200]

        if not success and attempt < retries:
            retry_count += 1
            time.sleep(1)  # 重试间隔

    # 记录日志
    _log_webhook_call(
        log_id, webhook_url, event_type, payload,
        response_status, response_body, error_message,
        duration_ms, retry_count, success
    )

    return {
        'success': success,
        'logId': log_id,
        'responseStatus': response_status,
        'errorMessage': error_message,
        'retryCount': retry_count,
    }


def _compute_signature(timestamp: int, payload: str, secret: str) -> str:
    """
    计算 webhook 签名

    使用 HMAC-SHA256 签名：timestamp + payload
    """
    if not secret:
        return ''
    message = f'{timestamp}.{payload}'
    signature = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


def _log_webhook_call(
    log_id: str,
    webhook_url: str,
    event_type: str,
    payload: dict,
    response_status: Optional[int],
    response_body: Optional[str],
    error_message: Optional[str],
    duration_ms: int,
    retry_count: int,
    success: bool,
) -> None:
    """
    记录 webhook 调用日志
    """
    import psycopg2.extras

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            '''INSERT INTO webhook_logs
               (id, webhook_url, event_type, request_payload, response_status,
                response_body, error_message, duration_ms, retry_count, success)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
            (log_id, webhook_url, event_type, psycopg2.extras.Json(payload),
             response_status, response_body, error_message, duration_ms, retry_count, success)
        )
        conn.commit()


def get_webhook_logs(
    event_type: Optional[str] = None,
    success: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    获取 webhook 调用日志列表

    Parameters
    ----------
    event_type : str, optional
        过滤事件类型
    success : bool, optional
        过滤成功/失败
    limit : int
        返回数量限制
    offset : int
        偏移量

    Returns
    -------
    list[dict]
        日志列表
    """
    with get_db() as conn:
        cur = conn.cursor()

        conditions = []
        params = []

        if event_type:
            conditions.append('event_type = %s')
            params.append(event_type)
        if success is not None:
            conditions.append('success = %s')
            params.append(success)

        where_clause = f'WHERE {" AND ".join(conditions)}' if conditions else ''

        cur.execute(
            f'''SELECT id, webhook_url, event_type, request_payload, response_status,
                  response_body, error_message, duration_ms, retry_count, success, created_at
               FROM webhook_logs {where_clause}
               ORDER BY created_at DESC
               LIMIT %s OFFSET %s''',
            params + [limit, offset]
        )
        rows = cur.fetchall()

        return [
            {
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
            }
            for row in rows
        ]


def build_merge_webhook_payload(
    merge_result: dict,
    project_menu_id: str,
    version_id: str,
    version_name: str,
    target_branch: str,
    target_branch_name: str,
    merged_by: str,
    project_name: Optional[str] = None,
) -> dict:
    """
    构建合并事件的 webhook payload

    Parameters
    ----------
    merge_result : dict
        合并结果（包含 mergeId, collections 等）
    project_menu_id : str
        项目菜单 ID
    version_id : str
        源版本 ID
    version_name : str
        源版本名称
    target_branch : str
        目标分支 ID
    target_branch_name : str
        目标分支名称
    merged_by : str
        合并执行者
    project_name : str, optional
        项目名称

    Returns
    -------
    dict
        Webhook payload
    """
    # 计算汇总统计
    total_created = sum(c.get('recordsCreated', 0) for c in merge_result.get('collections', []))
    total_updated = sum(c.get('recordsUpdated', 0) for c in merge_result.get('collections', []))
    total_deleted = sum(c.get('recordsDeleted', 0) for c in merge_result.get('collections', []))

    # 构建变更详情
    collection_details = [
        {
            'collection': c.get('collection'),
            'pageName': c.get('pageName'),
            'recordsCreated': c.get('recordsCreated', 0),
            'recordsUpdated': c.get('recordsUpdated', 0),
            'recordsDeleted': c.get('recordsDeleted', 0),
        }
        for c in merge_result.get('collections', [])
    ]

    return {
        'event': 'merge',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'mergeId': merge_result.get('mergeId'),
        'project': {
            'menuId': project_menu_id,
            'name': project_name,
        },
        'version': {
            'id': version_id,
            'name': version_name,
        },
        'targetBranch': {
            'id': target_branch,
            'name': target_branch_name,
        },
        'mergedBy': merged_by,
        'summary': {
            'totalRecords': total_created + total_updated + total_deleted,
            'recordsCreated': total_created,
            'recordsUpdated': total_updated,
            'recordsDeleted': total_deleted,
        },
        'collections': collection_details,
    }