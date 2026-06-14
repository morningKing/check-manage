"""Permission catalog + role permission resolution with in-memory cache.

Single source of truth for RBAC. The JWT only carries the role slug; every
request resolves the permission set from this module (cached), so editing a
role takes effect immediately without re-login.
"""
import threading
from db import get_db

# Admin-feature capability catalog. Rendered as toggles in the role manager.
PERMISSION_CATALOG = [
    {'key': 'admin.users',              'label': '用户管理',   'group': '平台管理'},
    {'key': 'admin.roles',              'label': '角色权限',   'group': '平台管理'},
    {'key': 'admin.menus',              'label': '菜单管理',   'group': '平台管理'},
    {'key': 'admin.page_configs',       'label': '页面配置',   'group': '平台管理'},
    {'key': 'admin.api_keys',           'label': 'Open API',  'group': '平台管理'},
    {'key': 'admin.ai_settings',        'label': 'AI 配置',   'group': '平台管理'},
    {'key': 'admin.workflows',          'label': '工作流',     'group': '平台管理'},
    {'key': 'admin.export_scripts',     'label': '导出脚本',   'group': '数据工具'},
    {'key': 'admin.validation_scripts', 'label': '校验脚本',   'group': '数据工具'},
    {'key': 'admin.etl_tasks',          'label': 'ETL 管理',  'group': '数据工具'},
    {'key': 'admin.ai_scan',            'label': 'AI 定时任务', 'group': '数据工具'},
    {'key': 'admin.query',              'label': '数据查询',   'group': '数据工具'},
    {'key': 'admin.trigger_rules',      'label': '触发规则',   'group': '数据工具'},
    {'key': 'admin.webhooks',           'label': 'Webhook',   'group': '数据工具'},
    {'key': 'admin.dependencies',       'label': '依赖管理',   'group': '数据工具'},
    {'key': 'admin.dashboards',         'label': '仪表盘管理', 'group': '数据工具'},
    {'key': 'admin.column_views',       'label': '列视图管理', 'group': '数据工具'},
    {'key': 'admin.comments',           'label': '评论管理',   'group': '数据工具'},
    {'key': 'admin.project_versions',   'label': '版本管理',   'group': '数据工具'},
    {'key': 'admin.operation_logs',     'label': '操作日志',   'group': '系统运维'},
    {'key': 'admin.backup',             'label': '系统备份',   'group': '系统运维'},
    {'key': 'admin.system_config',      'label': '系统设置',   'group': '系统运维'},
    {'key': 'admin.home_widgets',       'label': '首页区块',   'group': '系统运维'},
]


def catalog_keys():
    return [e['key'] for e in PERMISSION_CATALOG]


_cache = {}            # role_id -> resolved dict
_lock = threading.Lock()


def _load(role_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, is_superuser, default_page_access FROM roles WHERE id = %s',
            (role_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        # A superuser bypasses every admin/page check, so skip loading the
        # detailed permission rows entirely.
        if bool(row[1]):
            return {
                'is_superuser': True,
                'default_page_access': row[2],
                'admin_keys': set(),
                'page_perms': {},
            }
        cur.execute(
            'SELECT permission_key FROM role_permissions WHERE role_id = %s',
            (role_id,),
        )
        admin_keys = {r[0] for r in cur.fetchall()}
        cur.execute(
            'SELECT page_id, can_read, can_create, can_update, can_delete '
            'FROM role_page_permissions WHERE role_id = %s',
            (role_id,),
        )
        page_perms = {
            r[0]: {'read': r[1], 'create': r[2], 'update': r[3], 'delete': r[4]}
            for r in cur.fetchall()
        }
    return {
        'is_superuser': bool(row[1]),
        'default_page_access': row[2],
        'admin_keys': admin_keys,
        'page_perms': page_perms,
    }


def get_role_perms(role_id):
    """Return the resolved permission dict for a role (cached), or None if unknown."""
    # Lock-free fast path: safe under CPython's GIL since cache entries are only added, never mutated in place.
    if role_id in _cache:
        return _cache[role_id]
    with _lock:
        if role_id in _cache:
            return _cache[role_id]
        resolved = _load(role_id)
        if resolved is not None:
            _cache[role_id] = resolved
        return resolved


def invalidate_cache(role_id=None):
    """Clear cache for one role, or all roles when role_id is None."""
    with _lock:
        if role_id is None:
            _cache.clear()
        else:
            _cache.pop(role_id, None)


def can_admin(role_id, key):
    p = get_role_perms(role_id)
    if not p:
        return False
    return p['is_superuser'] or key in p['admin_keys']


def _default_allows(default_page_access, action):
    if default_page_access == 'none':
        return False
    if default_page_access == 'read':
        return action == 'read'
    # 'write'
    return True


def can_page(role_id, page_id, action):
    p = get_role_perms(role_id)
    if not p:
        return False
    if p['is_superuser']:
        return True
    row = p['page_perms'].get(page_id)
    if row is not None:
        return bool(row.get(action, False))
    return _default_allows(p['default_page_access'], action)
