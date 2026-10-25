"""第一方插件注册表（plugin-architecture spec §3/§4）——唯一真相源。

manifest 声明挂载点；开关状态存 platform_plugins（迁移种子 ON CONFLICT DO
NOTHING，不覆盖管理员选择）。plugin_enabled 进程内 TTL 缓存 3s——当前部署
为 proxy 受管单 Flask 实例，安全；多实例需换集中缓存（spec §4 注）。
"""
import time
from dataclasses import dataclass

from flask import jsonify

from db import get_db

CACHE_TTL_S = 3.0


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    description: str
    version: str
    default_enabled: bool = False
    routes: tuple[str, ...] = ()
    menu_prefixes: tuple[str, ...] = ()
    api_prefixes: tuple[str, ...] = ()
    process: str | None = None


PLUGINS: dict[str, PluginManifest] = {
    'smart-writing': PluginManifest(
        id='smart-writing', name='智能写作',
        description='协作式智能文档编辑（词表/规则/检查/导入导出）',
        version='1.0.0', default_enabled=True,
        routes=('SuggestEditorList', 'SuggestEditorDoc'),
        menu_prefixes=('/suggest-editor',),
        api_prefixes=('/suggest-editor',),
        process='se-node',
    ),
}

_cache: dict[str, tuple[float, bool]] = {}


def invalidate(plugin_id: str | None = None) -> None:
    if plugin_id is None:
        _cache.clear()
    else:
        _cache.pop(plugin_id, None)


def plugin_enabled(plugin_id: str) -> bool:
    m = PLUGINS.get(plugin_id)
    if not m:
        return False
    now = time.monotonic()
    hit = _cache.get(plugin_id)
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM platform_plugins WHERE plugin_id = %s",
                        (plugin_id,))
            row = cur.fetchone()
    enabled = bool(row[0]) if row else m.default_enabled
    _cache[plugin_id] = (now, enabled)
    return enabled


def disabled_menu_prefixes() -> tuple[str, ...]:
    out: list[str] = []
    for m in PLUGINS.values():
        if not plugin_enabled(m.id):
            out.extend(m.menu_prefixes)
    return tuple(out)


def filter_menu_items(items: list[dict]) -> list[dict]:
    """GET /menus 出参过滤（扁平行）：禁用插件的 path 前缀命中项整行剔除。"""
    prefixes = disabled_menu_prefixes()
    if not prefixes:
        return items
    return [i for i in items
            if not any((i.get('path') or '').startswith(p) for p in prefixes)]


def gate_plugin(bp, plugin_id: str):
    """蓝图 before_request 门：禁用 → 404 PLUGIN_DISABLED（spec §2 小默认）。
    挂载即校验 url_prefix ∈ api_prefixes，不一致启动即抛（spec §10）。"""
    m = PLUGINS.get(plugin_id)
    if not m:
        raise RuntimeError(f'unknown plugin: {plugin_id}')
    prefix = getattr(bp, 'url_prefix', None)
    if prefix and prefix not in m.api_prefixes:
        raise RuntimeError(
            f'plugin {plugin_id}: blueprint url_prefix {prefix} '
            f'不在 api_prefixes {m.api_prefixes}')

    @bp.before_request
    def _plugin_gate():
        if not plugin_enabled(plugin_id):
            return jsonify({'error': {'code': 'PLUGIN_DISABLED',
                                      'plugin': plugin_id}}), 404
        return None
