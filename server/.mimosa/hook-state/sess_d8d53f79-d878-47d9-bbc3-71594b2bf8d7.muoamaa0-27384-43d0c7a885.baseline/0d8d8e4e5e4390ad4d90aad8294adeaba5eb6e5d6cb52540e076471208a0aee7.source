"""utils/upload_limits 扩展面（D3 修复回归）：全部 AI 对外 JSON 端点
纳入 1 MB 请求体门，而 /v1/collections 数据接口与备份还原保持不限。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.upload_limits import (body_limit_for_path, MAX_JSON_BODY_BYTES,
                                 MAX_UPLOAD_REQUEST_BYTES)


def test_ai_sessions_family_limited():
    for p in ('/api/v1/ai-sessions', '/v1/ai-sessions',
              '/api/v1/ai-sessions/some-id',
              '/api/v1/ai-sessions/some-id/cancel'):
        assert body_limit_for_path(p) == MAX_JSON_BODY_BYTES, p


def test_memories_and_templates_and_scan_tasks_limited():
    for p in ('/api/v1/memories', '/v1/memories/m-1',
              '/api/v1/prompt-templates', '/v1/prompt-templates/t-1',
              '/api/v1/ai-scan-tasks', '/v1/ai-scan-tasks/t-1/run-now'):
        assert body_limit_for_path(p) == MAX_JSON_BODY_BYTES, p


def test_row_actions_run_limited_but_collections_untouched():
    run_path = '/api/v1/collections/orders/rec-1/row-actions/a-1/run'
    assert body_limit_for_path(run_path) == MAX_JSON_BODY_BYTES
    assert body_limit_for_path('/v1/collections/orders/r-1/row-actions/a/run') \
        == MAX_JSON_BODY_BYTES
    # 通用数据接口（可能大 JSON 批量导入）必须保持不限
    assert body_limit_for_path('/api/v1/collections/orders') is None
    assert body_limit_for_path('/v1/collections/orders/batch') is None


def test_ai_batches_original_contract_unchanged():
    assert body_limit_for_path('/api/v1/ai-batches/uploads') \
        == MAX_UPLOAD_REQUEST_BYTES
    assert body_limit_for_path('/v1/ai-batches/uploads') \
        == MAX_UPLOAD_REQUEST_BYTES
    assert body_limit_for_path('/api/v1/ai-batches') == MAX_JSON_BODY_BYTES


def test_non_ai_paths_stay_unlimited():
    assert body_limit_for_path('/api/backups/upload-restore') is None
    assert body_limit_for_path('/ai/chat/sessions') is None
    assert body_limit_for_path('/') is None
    assert body_limit_for_path('') is None
    assert body_limit_for_path(None) is None


def test_query_string_and_fragment_stripped():
    assert body_limit_for_path('/api/v1/ai-sessions?x=1') == MAX_JSON_BODY_BYTES
