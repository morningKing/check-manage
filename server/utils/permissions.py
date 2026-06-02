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
    {'key': 'admin.export_scripts',     'label': '导出脚本',   'group': '数据工具'},
    {'key': 'admin.validation_scripts', 'label': '校验脚本',   'group': '数据工具'},
    {'key': 'admin.etl_tasks',          'label': 'ETL 管理',  'group': '数据工具'},
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
