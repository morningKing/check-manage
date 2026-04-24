"""
Webhook 规则引擎模块

根据 webhook_rules 配置，在数据变更时触发外部 webhook 调用。
"""

import uuid
import json
import time
import hashlib
import hmac
import requests
import psycopg2.extras
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List


def fire_webhooks(
    event: str,
    collection: Optional[str],
    record_id: Optional[str],
    old_data: Optional[dict],
    new_data: Optional[dict],
    operator: str,
    cur=None,
    branch_id: Optional[str] = None,
) -> List[dict]:
    """
    查找并执行匹配的 webhook 规则

    Args:
        event: 'create' | 'update' | 'delete' | 'merge'
        collection: source collection name (None for merge)
        record_id: source record ID (None for merge)
        old_data: previous record data (None for create)
        new_data: current record data (None for delete)
        operator: operator username
        cur: database cursor (optional, for transaction)
        branch_id: branch ID for data isolation

    Returns:
        list of webhook errors (empty if all succeeded)
    """
    webhook_errors = []

    # Query matching rules
    try:
        if cur:
            # Use provided cursor (within transaction)
            # Match rules where:
            # - source_collections is empty (global rule like merge)
            # - OR collection is in the source_collections array
            cur.execute(
                'SELECT id, name, trigger_event, trigger_condition, webhook_url, secret, '
                'timeout, retries, source_collections '
                'FROM webhook_rules WHERE enabled = TRUE AND trigger_event = %s '
                'AND (source_collections = \'[]\'::jsonb OR source_collections @> \'[%s]\'::jsonb) '
                'ORDER BY execution_order',
                (event, collection)
            )
            rules = cur.fetchall()
        else:
            # Use standalone connection
            from db import get_db
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    'SELECT id, name, trigger_event, trigger_condition, webhook_url, secret, '
                    'timeout, retries, source_collections '
                    'FROM webhook_rules WHERE enabled = TRUE AND trigger_event = %s '
                    'AND (source_collections = \'[]\'::jsonb OR source_collections @> \'[%s]\'::jsonb) '
                    'ORDER BY execution_order',
                    (event, collection)
                )
                rules = cur.fetchall()
    except Exception:
        # Database not available or query failed - return empty (no webhooks triggered)
        return []

    for rule in rules:
        rule_id, rule_name, trigger_event, trigger_condition, webhook_url, secret, \
            timeout, retries, source_collections = rule

        # Check trigger condition
        if trigger_condition and not _check_condition(trigger_condition, old_data, new_data, event):
            continue

        # Build payload
        payload = _build_payload(
            event, collection, record_id, old_data, new_data,
            operator, rule_id, rule_name, branch_id, cur
        )

        # Fire webhook
        result = _fire_single_webhook(
            rule_id, rule_name, webhook_url, secret,
            event, payload, timeout, retries
        )

        if not result['success']:
            webhook_errors.append({
                'rule_id': rule_id,
                'rule_name': rule_name,
                'error': result.get('errorMessage', 'Unknown error'),
            })

    return webhook_errors


def _check_condition(condition: dict, old_data: Optional[dict], new_data: Optional[dict], event: str) -> bool:
    """
    Check if trigger condition is satisfied

    Args:
        condition: trigger condition config (e.g., {'field': 'status', 'value': 'completed'})
        old_data: previous data (for update/delete)
        new_data: current data (for create/update)
        event: event type

    Returns:
        True if condition matches
    """
    cond_field = condition.get('field')
    cond_value = condition.get('value')

    if not cond_field:
        return True

    # For update, check field change
    if event == 'update':
        old_val = (old_data or {}).get(cond_field)
        new_val = (new_data or {}).get(cond_field)
        if new_val == old_val:
            return False
        if cond_value is not None and str(new_val) != str(cond_value):
            return False
        return True

    # For create, check field value
    if event == 'create':
        new_val = (new_data or {}).get(cond_field)
        if cond_value is not None and str(new_val) != str(cond_value):
            return False
        return True

    # For delete, check old field value
    if event == 'delete':
        old_val = (old_data or {}).get(cond_field)
        if cond_value is not None and str(old_val) != str(cond_value):
            return False
        return True

    return True


def _build_payload(
    event: str,
    collection: Optional[str],
    record_id: Optional[str],
    old_data: Optional[dict],
    new_data: Optional[dict],
    operator: str,
    rule_id: str,
    rule_name: str,
    branch_id: Optional[str],
    cur,
) -> dict:
    """
    Build webhook payload for different event types
    """
    payload = {
        'event': event,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'ruleId': rule_id,
        'ruleName': rule_name,
        'operator': operator,
        'branchId': branch_id,
    }

    # Data events (create/update/delete)
    if event in ('create', 'update', 'delete') and collection:
        payload['collection'] = collection

        # Get page name from page_configs
        page_name = None
        try:
            if cur:
                cur.execute(
                    "SELECT name FROM page_configs WHERE id = %s",
                    (collection,)
                )
                row = cur.fetchone()
                if row:
                    page_name = row[0]
        except Exception:
            pass

        payload['pageName'] = page_name
        payload['recordId'] = record_id

        if event == 'create':
            payload['record'] = new_data
            payload['oldRecord'] = None
        elif event == 'update':
            payload['record'] = new_data
            payload['oldRecord'] = old_data
        elif event == 'delete':
            payload['record'] = None
            payload['oldRecord'] = old_data

    # Merge event - payload should be passed directly
    # This function handles data events; merge payload is built separately

    return payload


def _fire_single_webhook(
    rule_id: str,
    rule_name: str,
    webhook_url: str,
    secret: str,
    event_type: str,
    payload: dict,
    timeout: int,
    retries: int,
) -> dict:
    """
    Fire a single webhook call with retry logic
    """
    log_id = f'wh-{uuid.uuid4().hex[:12]}'

    # Compute signature
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
            response_body = resp.text[:2000]
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
            time.sleep(1)

    # Log the call
    _log_webhook_call(
        log_id, rule_id, rule_name, webhook_url, event_type, payload,
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
    Compute HMAC-SHA256 signature
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
    rule_id: str,
    rule_name: str,
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
    Record webhook execution log
    """
    try:
        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                '''INSERT INTO webhook_logs
                   (id, rule_id, rule_name, webhook_url, event_type, request_payload,
                    response_status, response_body, error_message, duration_ms,
                    retry_count, success)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                (log_id, rule_id, rule_name, webhook_url, event_type,
                 psycopg2.extras.Json(payload), response_status, response_body,
                 error_message, duration_ms, retry_count, success)
            )
            conn.commit()
    except Exception:
        pass


def build_merge_webhook_payload(
    merge_id: str,
    merge_result: dict,
    project_menu_id: str,
    project_name: str,
    version_id: str,
    version_name: str,
    target_branch_id: str,
    target_branch_name: str,
    merged_by: str,
) -> dict:
    """
    Build payload for merge event

    Args:
        merge_id: merge operation ID
        merge_result: merge result containing collections info
        project_menu_id: project menu ID
        project_name: project name
        version_id: source version ID
        version_name: source version name
        target_branch_id: target branch ID
        target_branch_name: target branch name
        merged_by: operator username

    Returns:
        Webhook payload dict
    """
    # Calculate summary statistics
    total_created = sum(c.get('recordsCreated', 0) for c in merge_result.get('collections', []))
    total_updated = sum(c.get('recordsUpdated', 0) for c in merge_result.get('collections', []))
    total_deleted = sum(c.get('recordsDeleted', 0) for c in merge_result.get('collections', []))

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
        'mergeId': merge_id,
        'project': {
            'menuId': project_menu_id,
            'name': project_name,
        },
        'version': {
            'id': version_id,
            'name': version_name,
        },
        'targetBranch': {
            'id': target_branch_id,
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