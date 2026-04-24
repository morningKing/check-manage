"""
创建项目依赖测试数据

演示三种依赖类型：
1. track-main: 跟随主干 - 自动接收目标项目main分支更新
2. read-write: 配套分支 - 需要联合合并
3. read-only: 精确钉住 - 引用特定历史版本

运行方式：python server/create_test_dependency_data.py
"""

import sys
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG

# Fix Windows console encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    now = datetime.now(timezone.utc)

    print("=== 开始创建测试数据 ===\n")

    # ==================== 1. 创建测试项目菜单 ====================

    # 检查测试项目是否已存在
    cur.execute("SELECT id FROM menus WHERE id = 'test-project-A'")
    if not cur.fetchone():
        # 项目A - 被依赖方（上游项目）- 直接作为 menu-2 的子菜单
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            "VALUES ('test-project-A', '项目A（上游）', 'Document', NULL, 'menu-2', 10, NULL, %s, 'project')",
            (psycopg2.extras.Json(['admin', 'developer']),)
        )

        # 项目B - 依赖方（下游项目）
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            "VALUES ('test-project-B', '项目B（下游）', 'Document', NULL, 'menu-2', 11, NULL, %s, 'project')",
            (psycopg2.extras.Json(['admin', 'developer']),)
        )

        # 项目C - 另一个依赖方
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            "VALUES ('test-project-C', '项目C（依赖方）', 'Document', NULL, 'menu-2', 12, NULL, %s, 'project')",
            (psycopg2.extras.Json(['admin', 'developer']),)
        )

        conn.commit()
        print("[OK] 创建了3个测试项目: 项目A（上游）、项目B（下游）、项目C（依赖方）")
    else:
        print("[OK] 测试项目已存在，跳过创建")

    # ==================== 2. 创建测试版本 ====================

    # 项目A的版本
    versions_to_create = [
        # 项目A版本
        ('ver-a-v1.0', 'test-project-A', 'v1.0', '初始版本', 'merged', now, True),
        ('ver-a-v2.0', 'test-project-A', 'v2.0', '功能更新', 'merged', now, False),
        ('ver-a-feat-x', 'test-project-A', 'feature-x', '特性X开发', 'active', None, False),

        # 项目B版本
        ('ver-b-v1.0', 'test-project-B', 'v1.0', '初始版本', 'merged', now, False),
        ('ver-b-feat-y', 'test-project-B', 'feature-y', '特性Y开发', 'active', None, False),

        # 项目C版本
        ('ver-c-v1.0', 'test-project-C', 'v1.0', '初始版本', 'merged', now, False),
        ('ver-c-feat-z', 'test-project-C', 'feature-z', '特性Z开发', 'active', None, False),
    ]

    created_versions = []
    for ver_id, project_id, name, desc, status, merged_at, is_protected in versions_to_create:
        cur.execute("SELECT id FROM project_versions WHERE id = %s", (ver_id,))
        if not cur.fetchone():
            cur.execute(
                '''INSERT INTO project_versions
                   (id, project_menu_id, name, description, status, created_at, merged_at, is_protected, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'admin')''',
                (ver_id, project_id, name, desc, status, now, merged_at, is_protected)
            )
            created_versions.append((ver_id, project_id, name, status))
            conn.commit()

    if created_versions:
        print(f"✓ 创建了 {len(created_versions)} 个版本:")
        for ver_id, project_id, name, status in created_versions:
            print(f"  - {ver_id}: {name} ({status})")
    else:
        print("✓ 测试版本已存在，跳过创建")

    # ==================== 3. 创建依赖声明 ====================

    dependencies_to_create = [
        # (dep_id, source_project, source_branch, target_project, target_branch, relation_type, pinned_version)
        # track-main: 项目B 跟随 项目A 的 main 分支
        ('dep-1-track', 'test-project-B', 'main', 'test-project-A', 'main', 'track-main', None),

        # read-write: 项目B 的 feature-y 分支 配套 项目A 的 feature-x 分支
        ('dep-2-rw-active', 'test-project-B', 'ver-b-feat-y', 'test-project-A', 'ver-a-feat-x', 'read-write', None),

        # read-write: 项目C 的 feature-z 分支 配套 项目A 的 v2.0（已合并）
        ('dep-3-rw-ready', 'test-project-C', 'ver-c-feat-z', 'test-project-A', 'ver-a-v2.0', 'read-write', None),

        # read-only: 项目C 钉住 项目A 的 v1.0 版本
        ('dep-4-ro', 'test-project-C', 'main', 'test-project-A', 'ver-a-v1.0', 'read-only', 'ver-a-v1.0'),
    ]

    created_deps = []
    for dep_id, source, source_branch, target, target_branch, rel_type, pinned in dependencies_to_create:
        cur.execute("SELECT id FROM project_dependencies WHERE id = %s", (dep_id,))
        if not cur.fetchone():
            cur.execute(
                '''INSERT INTO project_dependencies
                   (id, source_project, source_branch, target_project, target_branch,
                    relation_type, pinned_version, declared_by, declared_at, is_validated)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'admin', %s, FALSE)''',
                (dep_id, source, source_branch, target, target_branch, rel_type, pinned, now)
            )
            created_deps.append((dep_id, source, target, rel_type, source_branch, target_branch))
            conn.commit()

    if created_deps:
        print(f"\n✓ 创建了 {len(created_deps)} 个依赖声明:")
        print("\n依赖类型说明:")
        print("  track-main  : 跟随主干 - 自动接收目标项目main分支更新")
        print("  read-write  : 配套分支 - 需要联合合并（阻塞/就绪）")
        print("  read-only   : 精确钉住 - 引用特定历史版本")
        print("\n创建的依赖:")
        for dep_id, source, target, rel_type, src_branch, tgt_branch in created_deps:
            print(f"  {dep_id}: {source.split('-')[-1]}[{src_branch}] → {target.split('-')[-1]}[{tgt_branch}] ({rel_type})")
    else:
        print("✓ 依赖声明已存在，跳过创建")

    # ==================== 4. 创建测试数据页 ====================
    # 为项目创建一些测试 collection 和数据，用于测试外键关联

    # 创建测试数据页配置
    test_pages = [
        ('test-orders', '测试订单', 'test-project-B'),
        ('test-products', '测试产品', 'test-project-A'),
        ('test-customers', '测试客户', 'test-project-A'),
    ]

    for page_id, name, project_id in test_pages:
        cur.execute("SELECT id FROM page_configs WHERE id = %s", (page_id,))
        if not cur.fetchone():
            # 创建包含 relation 字段的配置
            fields = []
            if page_id == 'test-orders':
                fields = [
                    {'fieldName': 'orderNo', 'label': '订单号', 'controlType': 'text', 'required': True},
                    {'fieldName': 'productId', 'label': '产品', 'controlType': 'relation', 'required': True, 'targetCollection': 'test-products'},
                    {'fieldName': 'customerId', 'label': '客户', 'controlType': 'relation', 'required': False, 'targetCollection': 'test-customers'},
                    {'fieldName': 'status', 'label': '状态', 'controlType': 'select', 'options': ['待处理', '已完成', '已取消']},
                ]
            elif page_id == 'test-products':
                fields = [
                    {'fieldName': 'productNo', 'label': '产品编号', 'controlType': 'text', 'required': True},
                    {'fieldName': 'name', 'label': '产品名称', 'controlType': 'text', 'required': True},
                    {'fieldName': 'price', 'label': '价格', 'controlType': 'number', 'required': False},
                ]
            elif page_id == 'test-customers':
                fields = [
                    {'fieldName': 'customerNo', 'label': '客户编号', 'controlType': 'text', 'required': True},
                    {'fieldName': 'name', 'label': '客户名称', 'controlType': 'text', 'required': True},
                ]

            cur.execute(
                'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
                (page_id, name, psycopg2.extras.Json(fields))
            )

            # 为数据页创建菜单
            cur.execute(
                '''INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type)
                   VALUES (%s, %s, 'Document', %s, %s, 1, %s, %s, 'data')''',
                (f'menu-{page_id}', name, page_id, project_id, f'/test/{page_id}', psycopg2.extras.Json(['admin', 'developer']))
            )
            conn.commit()

    print("\n✓ 创建了测试数据页: 测试订单(项目B)、测试产品(项目A)、测试客户(项目A)")
    print("  - 订单页有 relation 字段指向产品和客户（用于测试外键断裂）")

    # ==================== 5. 创建测试数据记录 ====================

    # 产品数据（项目A main分支）
    test_products = [
        ('prod-001', 'test-products', {'productNo': 'P-001', 'name': '产品1', 'price': 100}),
        ('prod-002', 'test-products', {'productNo': 'P-002', 'name': '产品2', 'price': 200}),
    ]

    for record_id, collection, data in test_products:
        cur.execute(
            '''INSERT INTO dynamic_data (id, collection, data, branch_id, created_at)
               VALUES (%s, %s, %s, 'main', %s)
               ON CONFLICT (id, branch_id) DO NOTHING''',
            (record_id, collection, psycopg2.extras.Json(data), now)
        )
        conn.commit()

    # 订单数据（项目B main分支）- 包含有效关联
    test_orders = [
        ('order-001', 'test-orders', {'orderNo': 'O-001', 'productId': ['prod-001'], 'customerId': [], 'status': '已完成'}),
    ]

    for record_id, collection, data in test_orders:
        cur.execute(
            '''INSERT INTO dynamic_data (id, collection, data, branch_id, created_at)
               VALUES (%s, %s, %s, 'main', %s)
               ON CONFLICT (id, branch_id) DO NOTHING''',
            (record_id, collection, psycopg2.extras.Json(data), now)
        )
        # 创建关联关系
        cur.execute(
            '''INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id)
               VALUES (%s, %s, 'productId', %s, %s, 'main')
               ON CONFLICT DO NOTHING''',
            (collection, record_id, 'test-products', 'prod-001')
        )
        conn.commit()

    print("\n✓ 创建了测试数据记录:")
    print("  - 产品: prod-001, prod-002 (项目A main)")
    print("  - 订单: order-001 → 关联 prod-001 (项目B main)")

    # ==================== 6. 创建依赖关联关系 ====================
    # 记录依赖声明涉及的字段关联

    for dep_id, source, target in [
        ('dep-2-rw-active', 'test-project-B', 'test-project-A'),
        ('dep-3-rw-ready', 'test-project-C', 'test-project-A'),
    ]:
        # 订单.productId → 产品
        cur.execute(
            '''INSERT INTO project_dependency_relations
               (id, dependency_id, source_collection, source_field, target_collection, created_at)
               VALUES (%s, %s, 'test-orders', 'productId', 'test-products', %s)
               ON CONFLICT DO NOTHING''',
            (f'pdr-{dep_id}-1', dep_id, now)
        )
        conn.commit()

    print("\n✓ 创建了依赖关联关系记录")

    # ==================== 7. 显示测试场景 ====================

    print("\n" + "="*60)
    print("测试场景说明")
    print("="*60)

    print("""
【场景1: track-main 依赖】
  dep-1-track: 项目B[main] → 项目A[main] (track-main)
  测试点：
  - 项目A main 分支更新后，项目B 自动接收通知
  - 项目B 合并前不会阻塞（track-main 不阻塞）

【场景2: read-write 依赖 - 阻塞】
  dep-2-rw-active: 项目B[feature-y] → 项目A[feature-x] (read-write)
  测试点：
  - 项目A 的 feature-x 分支状态为 active（未合并）
  - 项目B 的 feature-y 分支合并时会被阻塞
  - 需要先合并项目A 的 feature-x，才能合并项目B 的 feature-y

【场景3: read-write 依赖 - 就绪】
  dep-3-rw-ready: 项目C[feature-z] → 项目A[v2.0] (read-write)
  测试点：
  - 项目A 的 v2.0 分支状态为 merged（已合并）
  - 项目C 的 feature-z 分支可以合并（依赖就绪）

【场景4: read-only 依赖】
  dep-4-ro: 项目C[main] → 项目A[v1.0] (read-only)
  测试点：
  - 精确钉住 v1.0 版本
  - 项目A 的任何更新不会影响此依赖
  - 合并时不阻塞

【场景5: 外键断裂】
  - 订单.order-001 关联 产品.prod-001
  - 如果删除 prod-001，校验时会发现外键断裂并发出警告通知

【场景6: 分支删除保护】
  - 尝试删除项目A 的 feature-x 分支
  - 由于项目B 依赖此分支，删除会被阻止
""")

    # ==================== 8. 显示访问路径 ====================

    print("\n" + "="*60)
    print("测试数据访问路径")
    print("="*60)

    print("""
前端页面：
  - 依赖管理: /admin/dependency-manager
  - 项目A数据页: 在左侧菜单 工作空间 → 项目A（上游）
  - 项目B数据页: 在左侧菜单 工作空间 → 项目B（下游）
  - 项目C数据页: 在左侧菜单 工作空间 → 项目C（依赖方）

菜单结构（标准3层）：
  Level 1: 工作空间 (menu-2)
  Level 2: 项目 (test-project-A, test-project-B, test-project-C)
  Level 3: 数据页 (测试客户、测试产品、测试订单、测试任务)

API端点：
  GET  /projects/test-project-B/dependencies     - 查看项目B的依赖列表
  GET  /projects/test-project-A/dependents       - 查看依赖项目A的项目
  GET  /projects/test-project-B/merge-check?sourceBranch=ver-b-feat-y
       - 检查项目B feature-y 合并前的依赖状态（应该阻塞）
  GET  /projects/test-project-C/merge-check?sourceBranch=ver-c-feat-z
       - 检查项目C feature-z 合并前的依赖状态（应该就绪）
  POST /dependencies/<dep_id>/validate           - 触发校验
  GET  /projects/test-project-A/branches/ver-a-feat-x/delete-check
       - 检查能否删除 feature-x 分支（应该被阻塞）
""")

    conn.close()
    print("\n=== 测试数据创建完成 ===")


if __name__ == '__main__':
    main()