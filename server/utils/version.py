"""
版本管理核心逻辑 - 精简版

职责：
- 提供 get_user_current_branch 函数（改用项目级分支查询）
- 提供 set_user_current_branch 函数（用于项目级切换同步）
- 提供 clear_user_current_branch 函数
- 主分支常量定义

注意：数据级版本管理的其他功能已迁移到 project_version.py
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from db import get_db
import psycopg2.extras


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


# ==================== 分支管理函数 ====================

# 主分支的 branch_id 常量
MAIN_BRANCH_ID = 'main'


def get_user_current_branch(user_id, collection):
    """
    获取用户在指定集合的当前工作分支（改造版）

    新逻辑：通过 collection 反查所属项目，查询项目级分支

    Parameters
    ----------
    user_id : str
        用户 ID
    collection : str
        集合名称

    Returns
    -------
    str
        当前分支 ID，'main' 表示主分支
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 1. 通过 collection 找到对应的 page_config
        page_id = f'page-{collection}'

        # 2. 通过 page_id 找到对应的 data 菜单
        cur.execute(
            'SELECT parent_id FROM menus WHERE page_id = %s AND menu_type = %s',
            (page_id, 'data')
        )
        row = cur.fetchone()

        if not row or not row[0]:
            # 没有找到项目菜单，返回主分支
            return MAIN_BRANCH_ID

        project_menu_id = row[0]

        # 3. 验证父级是 project 类型
        cur.execute(
            'SELECT menu_type FROM menus WHERE id = %s',
            (project_menu_id,)
        )
        parent_row = cur.fetchone()
        if not parent_row or parent_row[0] != 'project':
            return MAIN_BRANCH_ID

        # 4. 查询项目级分支
        cur.execute(
            'SELECT branch_id FROM user_current_project_branch '
            'WHERE user_id = %s AND project_menu_id = %s',
            (user_id, project_menu_id)
        )
        branch_row = cur.fetchone()

        return branch_row[0] if branch_row else MAIN_BRANCH_ID


def set_user_current_branch(user_id, username, collection, branch_id):
    """
    设置用户在指定集合的当前工作分支

    注意：此函数由项目级分支切换调用，同步更新所有 collection 的分支状态

    Parameters
    ----------
    user_id : str
        用户 ID
    username : str
        用户名
    collection : str
        集合名称
    branch_id : str
        分支 ID，'main' 表示切换到主分支
    """
    now = datetime.now(timezone.utc)
    record_id = f'ucb-{user_id}-{collection}'
    actual_branch_id = branch_id or MAIN_BRANCH_ID

    with get_db() as conn:
        cur = conn.cursor()
        # 使用 upsert
        cur.execute(
            'INSERT INTO user_current_branch (id, user_id, username, collection, branch_id, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s) '
            'ON CONFLICT (user_id, collection) DO UPDATE SET branch_id = %s, updated_at = %s',
            (record_id, user_id, username, collection, actual_branch_id, now, actual_branch_id, now),
        )


def clear_user_current_branch(user_id, collection):
    """
    清除用户在指定集合的当前分支设置（切换回主分支）

    Parameters
    ----------
    user_id : str
        用户 ID
    collection : str
        集合名称
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'DELETE FROM user_current_branch WHERE user_id = %s AND collection = %s',
            (user_id, collection),
        )


def copy_data_to_branch(source_branch, target_branch, collection):
    """
    复制分支数据到另一个分支

    Parameters
    ----------
    source_branch : str
        源分支 ID
    target_branch : str
        目标分支 ID
    collection : str
        集合名称

    Returns
    -------
    int
        复制的记录数量
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 复制数据
        cur.execute(
            'SELECT id, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND branch_id = %s',
            (collection, source_branch)
        )
        records = cur.fetchall()

        for record_id, data, created_at in records:
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, branch_id, created_at) '
                'VALUES (%s, %s, %s, %s, %s) '
                'ON CONFLICT (id, branch_id) DO UPDATE SET data = EXCLUDED.data',
                (record_id, collection, psycopg2.extras.Json(data), target_branch, created_at)
            )

        # 复制关联数据
        cur.execute(
            'SELECT collection, record_id, field_name, related_collection, related_id '
            'FROM data_relations WHERE collection = %s AND branch_id = %s',
            (collection, source_branch)
        )
        relations = cur.fetchall()

        for rel_coll, record_id, field_name, related_coll, related_id in relations:
            cur.execute(
                'INSERT INTO data_relations '
                '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (rel_coll, record_id, field_name, related_coll, related_id, target_branch)
            )

    return len(records)


def get_branch_data_count(collection, branch_id):
    """
    获取分支数据记录数量

    Parameters
    ----------
    collection : str
        集合名称
    branch_id : str
        分支 ID

    Returns
    -------
    int
        记录数量
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection, branch_id)
        )
        return cur.fetchone()[0]


def get_users_on_branch(collection, branch_id):
    """
    获取正在使用指定分支的用户列表

    Parameters
    ----------
    collection : str
        集合名称
    branch_id : str
        分支 ID

    Returns
    -------
    list[str]
        用户名列表
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT username FROM user_current_branch WHERE collection = %s AND branch_id = %s',
            (collection, branch_id)
        )
        return [row[0] for row in cur.fetchall()]