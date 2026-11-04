import logging
from datetime import datetime, timezone, timedelta
from db import get_db

from .service import create_backup
logger = logging.getLogger(__name__)


# 恢复出厂设置时需要删除的表（按依赖顺序）。
# 分两类:
#   - 业务"数据" (用户输入产生的内容、历史记录、临时状态) → 出厂时清空
#   - 管理员"配置" (webhook 规则、触发规则、UI 自定义、AI 模板、系统设置) → 保留
# 如果某项你觉得分类不对,改下面这张表就好。
FACTORY_RESET_TABLES = [
    # 子表 → 父表的顺序(replica role 已禁外键,但维持顺序便于阅读)
    # AI Chat 数据
    'ai_chat_messages',          # → ai_chat_sessions
    'ai_chat_sessions',          # → users, ai_chat_batches
    'ai_chat_batches',           # → users, ai_chat_prompt_templates
    # 项目级版本管理
    'project_version_relations', # → project_versions
    'project_version_snapshots', # → project_versions
    'user_current_project_branch',
    'project_versions',
    # 项目依赖
    'project_dependency_relations',
    'project_dependency_events',
    'project_dependencies',
    # 合并记录
    'merge_backups',             # → merge_records
    'merge_records',
    # Collection 级版本
    'version_relations',
    'version_snapshots',
    'user_current_branch',
    'collection_versions',
    # 动态数据 + 关联
    'data_relations',
    'dynamic_data',
    # 日志/历史/通知/评论
    'webhook_logs',
    'trigger_logs',
    'operation_logs',
    'etl_logs',
    'notifications',
    'reminders',
    'record_comments',
]

# 系统默认菜单ID（首页、仪表盘、数据工具、系统配置相关）
# 巡检管理(menu-2)及其子项是示例业务数据，恢复出厂时删除
SYSTEM_DEFAULT_MENU_IDS = [
    # 首页
    'menu-1',
    # 仪表盘
    'menu-dashboard',
    # 设置中心（塌缩后的统一管理菜单，取代旧 menu-3* 管理菜单树）
    'menu-settings',
]

# 系统默认页面配置ID - 空列表
# 所有页面配置都是业务相关的，恢复出厂时应全部删除
SYSTEM_DEFAULT_PAGE_CONFIG_IDS = []


def factory_reset(created_by=None):
    """
    恢复出厂设置

    删除所有动态业务数据，保留系统配置。
    执行前自动创建备份（标记为 pre-reset）。

    Parameters
    ----------
    created_by : str
        执行者用户名

    Returns
    -------
    dict
        包含删除统计信息
    """
    # 1. 自动创建备份
    pre_reset_backup = create_backup(
        backup_type='manual',
        created_by=created_by or 'system',
    )
    # 更新备份备注
    note = '恢复出厂设置前自动备份'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE backups SET note = %s WHERE id = %s', (note, pre_reset_backup['id']))

    # 2. 执行删除
    deleted_stats = {}
    conn = None
    main_success = False

    try:
        from db import pool
        conn = pool.getconn()
        cur = conn.cursor()

        # 禁用触发器/FK约束
        cur.execute('SET session_replication_role = replica')

        for table in FACTORY_RESET_TABLES:
            # 检查表是否存在
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = %s
                )
            """, (table,))
            if cur.fetchone()[0]:
                cur.execute(f'DELETE FROM {table}')
                deleted_stats[table] = cur.rowcount
            else:
                deleted_stats[table] = 0

        # 3. 删除动态创建的菜单（保留系统默认）
        if SYSTEM_DEFAULT_MENU_IDS:
            cur.execute(
                f'DELETE FROM menus WHERE id NOT IN ({",".join(["%s"] * len(SYSTEM_DEFAULT_MENU_IDS))})',
                SYSTEM_DEFAULT_MENU_IDS
            )
        else:
            cur.execute('DELETE FROM menus')
        deleted_stats['menus'] = cur.rowcount

        # 4. 删除所有页面配置（业务数据，恢复出厂时应清空）
        if SYSTEM_DEFAULT_PAGE_CONFIG_IDS:
            cur.execute(
                f'DELETE FROM page_configs WHERE id NOT IN ({",".join(["%s"] * len(SYSTEM_DEFAULT_PAGE_CONFIG_IDS))})',
                SYSTEM_DEFAULT_PAGE_CONFIG_IDS
            )
        else:
            cur.execute('DELETE FROM page_configs')
        deleted_stats['page_configs'] = cur.rowcount

        conn.commit()
        main_success = True

    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            if main_success:
                try:
                    cur = conn.cursor()
                    cur.execute('SET session_replication_role = DEFAULT')
                except Exception:
                    conn.close()
                    return {
                        'deletedTables': list(deleted_stats.keys()),
                        'deletedRecords': deleted_stats,
                        'backupId': pre_reset_backup['id'],
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                    }
                pool.putconn(conn)
            else:
                conn.close()

    return {
        'deletedTables': list(deleted_stats.keys()),
        'deletedRecords': deleted_stats,
        'backupId': pre_reset_backup['id'],
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }



__all__ = [
    'logger',
    'datetime',
    'timezone',
    'timedelta',
    'get_db',
    'FACTORY_RESET_TABLES',
    'SYSTEM_DEFAULT_MENU_IDS',
    'SYSTEM_DEFAULT_PAGE_CONFIG_IDS',
    'factory_reset',
]
