"""Prometheus 文本格式监控端点（P1-B3）。全部指标从既有表聚合，零新依赖。"""
from flask import Blueprint, Response
from db import get_db

metrics_bp = Blueprint('metrics', __name__, url_prefix='/metrics')

@metrics_bp.route('', methods=['GET'])
def metrics():
    lines = []
    with get_db() as conn:
        cur = conn.cursor()
        pairs = [
            ('ai_batch_queue_depth', 'Pending batch children',
             "SELECT count(*) FROM ai_chat_sessions WHERE status='pending' AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL) AND deleted_at IS NULL"),
            ('ai_batch_running', 'Currently running batch children',
             "SELECT count(*) FROM ai_chat_sessions WHERE status='running' AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL)"),
            ('ai_batch_failed_last_hour', 'Failed in last hour',
             "SELECT count(*) FROM ai_chat_sessions WHERE status='failed' AND created_at > NOW() - interval '1 hour'"),
            ('ai_orchestration_runs_active', 'Active orchestration runs',
             "SELECT count(*) FROM ai_orchestration_runs WHERE status IN ('running','waiting_approval')"),
            ('ai_outbox_pending', 'Undelivered outbox rows',
             "SELECT count(*) FROM ai_delivery_outbox WHERE status IN ('pending','failed')"),
            ('ai_batch_needs_review', 'Children needing review',
             "SELECT count(*) FROM ai_chat_sessions WHERE status='needs_review'"),
        ]
        for name, help_text, sql in pairs:
            cur.execute(sql)
            val = cur.fetchone()[0]
            lines.append(f'# HELP {name} {help_text}')
            lines.append(f'# TYPE {name} gauge')
            lines.append(f'{name} {val}')
        cur.execute("SELECT lease_key, EXTRACT(EPOCH FROM (NOW()-heartbeat_at))::int FROM ai_batch_worker_leases")
        for row in cur.fetchall():
            lines.append(f'ai_worker_lease_heartbeat_seconds{{kind="{row[0]}"}} {row[1]}')
    return Response('\n'.join(lines) + '\n', mimetype='text/plain')
