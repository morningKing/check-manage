"""
创建大量测试菜单数据 - 用于验证菜单展开性能

创建一个测试工作空间，包含大量项目和数据页菜单
运行方式：python server/test_scripts/create_menu_performance_test_data.py
"""

import sys
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
import random
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_CONFIG

# Fix Windows console encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


# 丰富的字段类型配置
FIELD_TEMPLATES = {
    'text': {'controlType': 'text', 'required': True, 'placeholder': '请输入内容'},
    'number': {'controlType': 'number', 'required': False},
    'textarea': {'controlType': 'textarea', 'required': False, 'placeholder': '请输入详细描述'},
    'richText': {'controlType': 'richText', 'required': False},
    'select': {
        'controlType': 'select',
        'required': True,
        'options': [
            {'label': '选项一', 'value': 'opt1'},
            {'label': '选项二', 'value': 'opt2'},
            {'label': '选项三', 'value': 'opt3'},
        ],
        'optionsSource': {'type': 'static'}
    },
    'multiSelect': {
        'controlType': 'multiSelect',
        'required': False,
        'options': [
            {'label': '项目A', 'value': 'a'},
            {'label': '项目B', 'value': 'b'},
            {'label': '项目C', 'value': 'c'},
        ],
        'optionsSource': {'type': 'static'}
    },
    'date': {'controlType': 'date', 'required': True},
    'datetime': {'controlType': 'datetime', 'required': False},
    'checkbox': {'controlType': 'checkbox', 'required': False},
    'radio': {
        'controlType': 'radio',
        'required': True,
        'options': [
            {'label': '是', 'value': 'yes'},
            {'label': '否', 'value': 'no'},
        ],
        'optionsSource': {'type': 'static'}
    },
    'file': {'controlType': 'file', 'required': False},
    'image': {'controlType': 'image', 'required': False},
    'autoTimestamp': {'controlType': 'autoTimestamp'},
    'autoSequence': {'controlType': 'autoSequence', 'prefix': 'SEQ', 'digits': 4},
}

# 图标列表
ICONS = ['Document', 'Folder', 'Calendar', 'Clock', 'User', 'Setting', 'DataLine',
         'Monitor', 'Ticket', 'Flag', 'Star', 'Bell', 'Message', 'Phone',
         'Location', 'Search', 'Edit', 'Delete', 'Upload', 'Download',
         'Share', 'Link', 'Image', 'Video', 'Audio', 'File', 'Archive']


def generate_random_fields(count=5):
    """生成随机字段配置"""
    field_types = list(FIELD_TEMPLATES.keys())
    fields = []
    for i in range(count):
        field_type = random.choice(field_types)
        template = FIELD_TEMPLATES[field_type].copy()
        template['id'] = f'field-{i+1}'
        template['label'] = f'{field_type}字段{i+1}'
        template['fieldName'] = f'{field_type}Field{i+1}'
        template['order'] = i + 1
        fields.append(template)
    return fields


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    now = datetime.now(timezone.utc)

    print("=== 开始创建菜单性能测试数据 ===\n")

    # 配置参数
    PROJECT_COUNT = 15  # 项目数量
    DATA_PAGES_PER_PROJECT = 5  # 每个项目下的数据页数量
    WORKSPACE_ID = 'perf-test-workspace'
    WORKSPACE_NAME = '性能测试工作空间'

    # 1. 创建测试工作空间
    cur.execute(f"SELECT id FROM menus WHERE id = '{WORKSPACE_ID}'")
    if not cur.fetchone():
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            f"VALUES ('{WORKSPACE_ID}', '{WORKSPACE_NAME}', 'FolderOpened', NULL, NULL, 98, '/perf-test', %s, 'workspace')",
            (psycopg2.extras.Json(['admin', 'developer']),)
        )
        conn.commit()
        print(f"[OK] 创建工作空间: {WORKSPACE_NAME}")
    else:
        print(f"[OK] 工作空间已存在: {WORKSPACE_NAME}")

    # 2. 创建项目和数据页
    created_pages = 0
    created_menus = 0

    for p_idx in range(1, PROJECT_COUNT + 1):
        project_id = f'perf-project-{p_idx}'
        project_name = f'测试项目{p_idx}'
        project_icon = random.choice(ICONS)

        # 检查项目是否已存在
        cur.execute(f"SELECT id FROM menus WHERE id = '{project_id}'")
        if not cur.fetchone():
            # 创建项目菜单
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                f"VALUES ('{project_id}', '{project_name}', '{project_icon}', NULL, '{WORKSPACE_ID}', {p_idx}, NULL, %s, 'project')",
                (psycopg2.extras.Json(['admin', 'developer']),)
            )
            conn.commit()
            created_menus += 1
            print(f"  [{p_idx}/{PROJECT_COUNT}] 创建项目: {project_name}")

        # 为每个项目创建数据页
        for d_idx in range(1, DATA_PAGES_PER_PROJECT + 1):
            page_id = f'perf-page-{p_idx}-{d_idx}'
            page_name = f'数据页{p_idx}-{d_idx}'
            menu_id = f'perf-menu-{p_idx}-{d_idx}'

            # 随机选择字段数量和类型组合
            field_count = random.randint(3, 8)
            fields = generate_random_fields(field_count)

            # 检查页面配置是否已存在
            cur.execute(f"SELECT id FROM page_configs WHERE id = '{page_id}'")
            if not cur.fetchone():
                # 创建页面配置
                cur.execute(
                    'INSERT INTO page_configs (id, name, fields, description, api_endpoint) '
                    f"VALUES ('{page_id}', '{page_name}', %s, '性能测试数据页', '/api/data/{page_id}')",
                    (psycopg2.extras.Json(fields),)
                )
                conn.commit()
                created_pages += 1

            # 检查菜单是否已存在
            cur.execute(f"SELECT id FROM menus WHERE id = '{menu_id}'")
            if not cur.fetchone():
                menu_icon = random.choice(ICONS)
                menu_path = f'/perf-test/{p_idx}/{d_idx}'
                cur.execute(
                    'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                    f"VALUES ('{menu_id}', '{page_name}', '{menu_icon}', '{page_id}', '{project_id}', {d_idx}, '{menu_path}', %s, 'data')",
                    (psycopg2.extras.Json(['admin', 'developer']),)
                )
                conn.commit()
                created_menus += 1

    print(f"\n[OK] 创建完成:")
    print(f"  - 项目数量: {PROJECT_COUNT}")
    print(f"  - 每项目数据页: {DATA_PAGES_PER_PROJECT}")
    print(f"  - 总数据页数: {PROJECT_COUNT * DATA_PAGES_PER_PROJECT}")
    print(f"  - 新建页面配置: {created_pages}")
    print(f"  - 新建菜单数: {created_menus}")

    # 3. 创建一些额外的深层嵌套菜单（测试展开性能）
    # 在部分项目下创建子分组
    for p_idx in [1, 5, 10]:
        group_id = f'perf-group-{p_idx}'
        group_name = f'数据分组{p_idx}'
        project_id = f'perf-project-{p_idx}'

        # 检查分组是否已存在
        cur.execute(f"SELECT id FROM menus WHERE id = '{group_id}'")
        if not cur.fetchone():
            # 创建分组菜单（作为项目下的中间层）
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                f"VALUES ('{group_id}', '{group_name}', 'Folder', NULL, '{project_id}', 0, NULL, %s, 'project')",
                (psycopg2.extras.Json(['admin', 'developer']),)
            )
            conn.commit()
            created_menus += 1
            print(f"  创建分组: {group_name} (在项目{p_idx}下)")

            # 在分组下创建更多数据页
            for g_idx in range(1, 6):
                sub_page_id = f'perf-subpage-{p_idx}-{g_idx}'
                sub_page_name = f'子数据页{p_idx}-{g_idx}'
                sub_menu_id = f'perf-submenu-{p_idx}-{g_idx}'

                cur.execute(f"SELECT id FROM page_configs WHERE id = '{sub_page_id}'")
                if not cur.fetchone():
                    fields = generate_random_fields(random.randint(3, 6))
                    cur.execute(
                        'INSERT INTO page_configs (id, name, fields, description) '
                        f"VALUES ('{sub_page_id}', '{sub_page_name}', %s, '分组下的数据页')",
                        (psycopg2.extras.Json(fields),)
                    )
                    conn.commit()
                    created_pages += 1

                cur.execute(f"SELECT id FROM menus WHERE id = '{sub_menu_id}'")
                if not cur.fetchone():
                    cur.execute(
                        'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                        f"VALUES ('{sub_menu_id}', '{sub_page_name}', 'Document', '{sub_page_id}', '{group_id}', {g_idx}, '/perf-test/{p_idx}/group/{g_idx}', %s, 'data')",
                        (psycopg2.extras.Json(['admin', 'developer']),)
                    )
                    conn.commit()
                    created_menus += 1

    # 4. 统计最终结果
    cur.execute(f"SELECT COUNT(*) FROM menus WHERE parent_id = '{WORKSPACE_ID}'")
    project_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM menus m1
        WHERE m1.parent_id IN (
            SELECT id FROM menus WHERE parent_id = '%s'
        )
    """ % WORKSPACE_ID)
    data_page_count = cur.fetchone()[0]

    print(f"\n=== 最终统计 ===")
    print(f"工作空间 '{WORKSPACE_NAME}' 下:")
    print(f"  - 直接子菜单（项目）: {project_count}")
    print(f"  - 数据页总数: {data_page_count}")

    # 显示菜单树结构示例
    print(f"\n=== 菜单结构示例 ===")
    cur.execute("""
        SELECT id, name, menu_type FROM menus
        WHERE id = '%s' OR parent_id = '%s'
        ORDER BY "order"
        LIMIT 10
    """ % (WORKSPACE_ID, WORKSPACE_ID))
    rows = cur.fetchall()
    for row in rows:
        print(f"  {row[2]}: {row[1]} ({row[0]})")

    conn.close()
    print(f"\n=== 测试数据创建完成 ===")
    print(f"\n请在前端点击 '{WORKSPACE_NAME}' 菜单，测试展开性能")
    print(f"前端地址: http://localhost:5174")


if __name__ == '__main__':
    main()