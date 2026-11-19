"""自定义模型端点（OC2 阶段五）：后台管理 → 写入各会话工作区 opencode.json。

端点由管理员维护（routes/ai_opencode_admin.py 的 CRUD），派发会话时由
write_opencode_config 合并为 provider 段——**V1 格式**（provider.<id>.npm/api/
options.models）。兼容性依据官方迁移指南：V2 对受支持的 V1 字段做内存规范化
（provider.<id>.npm→package、api→settings.baseURL、options→settings），不改写
源文件——同一份配置两栈通吃，无需按栈分叉。

安全：api_key 直接写入工作区 opencode.json（与既有 mcp url 携带 session_token
同安全域——工作区与会话一一对应，token 泄露面等价）。
"""
import logging

logger = logging.getLogger(__name__)

# @ai-sdk/openai-compatible 包：自定义 OpenAI 兼容端点的标准载体
_NPM_PACKAGE = '@ai-sdk/openai-compatible'


def enabled_endpoints() -> list[dict]:
    """启用的自定义端点清单（db-backed）。失败返回空清单（best-effort：
    端点服务故障不阻断会话创建，只是工作区不带自定义 provider）。"""
    try:
        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT provider_id, display_name, base_url, api_key, "
                        "models FROM ai_oc_endpoints WHERE enabled = TRUE "
                        "ORDER BY id")
            return [
                {'provider_id': r[0], 'display_name': r[1] or r[0],
                 'base_url': r[2], 'api_key': r[3] or '',
                 'models': r[4] or []}
                for r in cur.fetchall()
            ]
    except Exception as e:  # noqa: BLE001
        logger.warning('oc endpoints fetch failed: %s', e)
        return []


def provider_config_v1(endpoints: list[dict]) -> dict:
    """渲染 V1 格式 provider map（V1 直读；V2 内存规范化兼容）。

    形状：{"<provider_id>": {"npm": "@ai-sdk/openai-compatible",
            "api": "<base_url>", "options": {"apiKey": "<key>"},
            "models": {"<modelID>": {"name": "<display>"}}}}"""
    out = {}
    for ep in endpoints or []:
        pid = (ep.get('provider_id') or '').strip()
        if not pid or not ep.get('base_url'):
            continue
        models = {}
        for m in (ep.get('models') or []):
            mid = (m.get('id') if isinstance(m, dict) else None) or ''
            if not mid:
                continue
            models[mid] = {'name': (m.get('name') if isinstance(m, dict)
                                    else None) or mid}
        out[pid] = {
            'npm': _NPM_PACKAGE,
            'api': ep['base_url'],
            'options': {'apiKey': ep.get('api_key') or ''},
            'models': models,
        }
    return out
