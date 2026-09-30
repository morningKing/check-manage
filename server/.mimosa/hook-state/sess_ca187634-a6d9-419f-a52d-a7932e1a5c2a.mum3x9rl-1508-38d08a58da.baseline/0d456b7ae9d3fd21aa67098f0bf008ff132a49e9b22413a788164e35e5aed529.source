"""幂等迁移：OpenCode 运行时治理权限拆分（ai_opencode_admin Spec P0 §12）。

原 admin.ai_settings 一把管 Skill/Agent/MCP/重启；现拆为 8 个细粒度权限
（admin.ai_runtime_read / ai_skill_write / ai_agent_write / ai_runtime_publish /
ai_runtime_apply / ai_runtime_rollback / ai_runtime_restart / ai_runtime_force）。

本脚本把**当前拥有 admin.ai_settings 的角色**平移授予全部新权限——拆分后已有
管理员的有效操作面不变（随后可在「角色权限」里按需回收细分项），超管不受影响。
可重复执行：NOT EXISTS 防重，角色权限缓存清理后立即生效。
用法（在 server/ 目录下）：
    python -m migrations.2026_09_15_split_ai_runtime_permissions
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
from utils.permissions import invalidate_cache

NEW_KEYS = [
    'admin.ai_runtime_read',
    'admin.ai_skill_write',
    'admin.ai_agent_write',
    'admin.ai_runtime_publish',
    'admin.ai_runtime_apply',
    'admin.ai_runtime_rollback',
    'admin.ai_runtime_restart',
    'admin.ai_runtime_force',
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT role_id FROM role_permissions WHERE permission_key = %s",
            ('admin.ai_settings',),
        )
        role_ids = [r[0] for r in cur.fetchall()]
        granted = 0
        for role_id in role_ids:
            for key in NEW_KEYS:
                cur.execute(
                    "INSERT INTO role_permissions (role_id, permission_key) "
                    "SELECT %s, %s WHERE NOT EXISTS ( "
                    "  SELECT 1 FROM role_permissions "
                    "  WHERE role_id = %s AND permission_key = %s)",
                    (role_id, key, role_id, key),
                )
                granted += cur.rowcount
    # 权限解析带进程内缓存，平移后立即失效让新权限可见
    invalidate_cache()
    print(f"roles with admin.ai_settings: {len(role_ids)}; "
          f"granted {granted} new runtime permission(s)")


if __name__ == '__main__':
    run()
