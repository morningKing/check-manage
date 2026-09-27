"""分支锁定检查工具"""

from flask import g
from db import get_db
from utils.version import MAIN_BRANCH_ID


def check_branch_lock(collection: str) -> tuple[str, str] | None:
    """
    检查当前用户在该 collection 所在项目的分支是否锁定。

    Args:
        collection: 数据 collection 名称

    Returns:
        None: 未锁定，可继续操作
        (branch_id, locked_by): 已锁定，返回锁定信息用于错误提示
    """
    # 获取当前用户信息
    user = getattr(g, 'current_user', {}) if hasattr(g, 'current_user') else {}
    user_id = user.get('userId')
    if not user_id:
        return None  # 未登录用户不检查锁定

    # 获取用户在该 collection 的当前分支
    from utils.version import get_user_current_branch
    branch_id = get_user_current_branch(user_id, collection)

    # 获取 collection 对应的项目菜单 ID
    with get_db() as conn:
        cur = conn.cursor()
        # 通过 page_id 查找数据菜单，再获取其 parent_id（项目菜单）
        cur.execute(
            'SELECT parent_id FROM menus WHERE page_id = %s AND menu_type = %s',
            (f'page-{collection}', 'data')
        )
        menu_row = cur.fetchone()
        if not menu_row or not menu_row[0]:
            return None  # 未找到项目菜单，不检查锁定

        project_menu_id = menu_row[0]

        if branch_id == MAIN_BRANCH_ID:
            # 检查 main 分支锁定状态（存储在 menus 表的项目菜单上）
            cur.execute(
                'SELECT is_main_locked, main_locked_by FROM menus WHERE id = %s',
                (project_menu_id,)
            )
            project_row = cur.fetchone()
            if project_row and project_row[0]:
                return (MAIN_BRANCH_ID, project_row[1] or '管理员')
            return None

        # 检查非 main 分支锁定状态
        cur.execute(
            'SELECT is_locked, locked_by FROM project_versions WHERE id = %s',
            (branch_id,)
        )
        version_row = cur.fetchone()
        if not version_row:
            return None  # 分支不存在，不检查锁定

        is_locked = version_row[0]
        locked_by = version_row[1]

        if is_locked:
            return (branch_id, locked_by or '管理员')

    return None


def check_branch_lock_by_project(user_id: str, project_menu_id: str) -> tuple[str, str] | None:
    """
    检查用户在指定项目的当前分支是否锁定。

    Args:
        user_id: 用户 ID
        project_menu_id: 项目菜单 ID

    Returns:
        None: 未锁定
        (branch_id, locked_by): 已锁定
    """
    from utils.project_version import get_user_project_branch

    branch_id = get_user_project_branch(user_id, project_menu_id)

    with get_db() as conn:
        cur = conn.cursor()

        if branch_id == MAIN_BRANCH_ID:
            # 检查 main 分支锁定状态
            cur.execute(
                'SELECT is_main_locked, main_locked_by FROM menus WHERE id = %s',
                (project_menu_id,)
            )
            project_row = cur.fetchone()
            if project_row and project_row[0]:
                return (MAIN_BRANCH_ID, project_row[1] or '管理员')
            return None

        # 检查非 main 分支
        cur.execute(
            'SELECT is_locked, locked_by FROM project_versions WHERE id = %s',
            (branch_id,)
        )
        version_row = cur.fetchone()
        if not version_row:
            return None

        is_locked = version_row[0]
        locked_by = version_row[1]

        if is_locked:
            return (branch_id, locked_by or '管理员')

    return None


def check_main_branch_lock(project_menu_id: str) -> tuple[bool, str] | None:
    """
    检查项目的 main 分支是否锁定。

    Args:
        project_menu_id: 项目菜单 ID

    Returns:
        None: 未锁定
        (True, locked_by): 已锁定
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT is_main_locked, main_locked_by FROM menus WHERE id = %s AND menu_type = %s',
            (project_menu_id, 'project')
        )
        row = cur.fetchone()
        if not row:
            return None
        if row[0]:
            return (True, row[1] or '管理员')
    return None