"""AI natural-language query route.

POST /ai/query  — translates a natural-language question to a MongoDB-style
filter JSON without executing the query itself.

GET  /ai/settings — retrieve AI configuration (api_key masked).
PUT  /ai/settings — update AI configuration.
"""

from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required, admin_required
from utils.ai_query import nl_to_mongo_filter, get_ai_settings, update_ai_settings
from utils.mongo_query import translate as mongo_translate, remap_labels, MongoQueryError

ai_bp = Blueprint('ai', __name__, url_prefix='/ai')


@ai_bp.route('/query', methods=['POST'])
@login_required
def ai_query():
    body = request.get_json(force=True)
    collection = body.get('collection', '').strip()
    question = body.get('question', '').strip()

    if not collection or not question:
        return jsonify({'error': 'collection 和 question 不能为空'}), 400

    # Fetch field schema from page_configs
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
        row = cur.fetchone()

    if not row or not row[0]:
        return jsonify({'error': f'未找到页面配置: {collection}'}), 404

    fields = row[0]

    # Call LLM to translate NL → filter
    try:
        raw_filter = nl_to_mongo_filter(question, fields, collection)
    except RuntimeError as e:
        status = 503 if 'API Key' in str(e) else 500
        return jsonify({'error': str(e)}), status

    # Safety fallback: remap any Chinese labels the LLM may have used
    safe_filter = remap_labels(raw_filter, fields)

    # Validate filter syntax via mongo_translate (dry run)
    try:
        mongo_translate(safe_filter)
    except MongoQueryError as e:
        return jsonify({'error': f'AI 生成的查询语法无效: {e}', 'raw_filter': safe_filter}), 422

    return jsonify({'filter': safe_filter})


@ai_bp.route('/settings', methods=['GET'])
@admin_required
def get_settings():
    """获取 AI 配置（api_key 掩码处理）"""
    settings = get_ai_settings()
    # Mask api_key: show only last 4 chars
    key = settings.get('apiKey', '')
    if len(key) > 4:
        settings['apiKey'] = '*' * (len(key) - 4) + key[-4:]
    return jsonify(settings)


@ai_bp.route('/settings', methods=['PUT'])
@admin_required
def put_settings():
    """更新 AI 配置"""
    body = request.get_json(force=True)

    enabled = body.get('enabled', False)
    api_key = body.get('apiKey', '').strip()
    endpoint = body.get('endpoint', '').strip()
    model = body.get('model', '').strip()
    timeout = body.get('timeout', 30)
    max_tokens = body.get('maxTokens', 1024)

    if not endpoint:
        return jsonify({'error': 'API 端点不能为空'}), 400
    if not model:
        return jsonify({'error': '模型名称不能为空'}), 400
    if not isinstance(timeout, int) or timeout < 1:
        return jsonify({'error': '超时时间必须为正整数'}), 400
    if not isinstance(max_tokens, int) or max_tokens < 1:
        return jsonify({'error': 'max_tokens 必须为正整数'}), 400

    # If api_key is all-masked (unchanged from frontend), keep the old value
    current = get_ai_settings()
    if api_key and set(api_key[:-4]) == {'*'}:
        api_key = current['apiKey']

    settings = update_ai_settings(enabled, api_key, endpoint, model, timeout, max_tokens)
    # Mask before returning
    key = settings.get('apiKey', '')
    if len(key) > 4:
        settings['apiKey'] = '*' * (len(key) - 4) + key[-4:]
    return jsonify(settings)
