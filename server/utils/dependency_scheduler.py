"""
依赖定期校验调度器

定期校验所有依赖声明，发现断裂时发送通知。
"""

import time
import threading
from db import get_db
from utils.cross_project_dependency import validate_project_dependency


def start_dependency_scheduler(app):
    """
    启动依赖定期校验调度器

    Parameters
    ----------
    app : Flask app
        Flask 应用实例
    """
    def scheduler_loop():
        while True:
            time.sleep(3600)  # 每小时检查一次
            with app.app_context():
                try:
                    validate_all_dependencies()
                except Exception:
                    pass  # 调度器失败不影响主应用

    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()


def validate_all_dependencies():
    """
    校验所有依赖声明

    查询所有依赖声明，对每个声明调用 validate_project_dependency()。
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 查询所有依赖声明
        cur.execute(
            'SELECT id FROM project_dependencies ORDER BY updated_at DESC'
        )
        rows = cur.fetchall()

        for row in rows:
            dependency_id = row[0]
            try:
                validate_project_dependency(dependency_id, send_notification=True)
            except Exception:
                pass  # 单个校验失败不影响其他


def validate_dependencies_for_project(project_menu_id: str):
    """
    校验指定项目的所有依赖声明

    Parameters
    ----------
    project_menu_id : str
        项目菜单ID
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 查询该项目的所有依赖声明
        cur.execute(
            'SELECT id FROM project_dependencies WHERE source_project = %s OR target_project = %s',
            (project_menu_id, project_menu_id)
        )
        rows = cur.fetchall()

        for row in rows:
            dependency_id = row[0]
            try:
                validate_project_dependency(dependency_id, send_notification=True)
            except Exception:
                pass