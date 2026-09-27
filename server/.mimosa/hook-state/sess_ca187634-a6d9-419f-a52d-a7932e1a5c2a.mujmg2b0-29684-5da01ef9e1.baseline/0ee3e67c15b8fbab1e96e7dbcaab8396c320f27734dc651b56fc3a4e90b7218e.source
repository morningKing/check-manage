"""
创建大量子数据页菜单 - 用于测试菜单展开性能（极端场景）

创建一个测试工作空间，包含：
- 1 个工作空间
- 3 个项目（每个项目下有 30+ 数据页）
- 1 个分组（分组下有 25+ 子数据页）

运行方式：python server/test_scripts/create_extreme_menu_test_data.py
"""

import sys
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_CONFIG

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


FIELD_TEMPLATES = {
    'text': {'controlType': 'text', 'required': True, 'placeholder': '请输入内容'},
    'number': {'controlType': 'number', 'required': False},
    'textarea': {'controlType': 'textarea', 'required': False},
    'select': {
        'controlType': 'select',
        'required': True,
        'options': [{'label': '选项一', 'value': 'opt1'}, {'label': '选项二', 'value': 'opt2'}],
        'optionsSource': {'type': 'static'}
    },
    'date': {'controlType': 'date', 'required': True},
    'datetime': {'controlType': 'datetime', 'required': False},
}

ICONS = ['Document', 'Folder', 'Calendar', 'Clock', 'User', 'Setting', 'DataLine',
         'Monitor', 'Ticket', 'Flag', 'Star', 'Bell', 'Message', 'Phone',
         'Location', 'Search', 'Edit', 'Upload', 'Download', 'Share', 'Link',
         'Image', 'Video', 'File', 'Archive', 'Key', 'Lock', 'Unlock']


def generate_random_fields(count=3):
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

    print("=== 开始创建极端菜单测试数据 ===\n")

    # 配置参数 - 每个项目下30+数据页
    PROJECT_COUNT = 3
    DATA_PAGES_PER_PROJECT = 35  # 每个项目35个数据页
    WORKSPACE_ID = 'extreme-test-workspace'
    WORKSPACE_NAME = '极端测试工作空间'

    # 1. 创建测试工作空间
    cur.execute(f"SELECT id FROM menus WHERE id = '{WORKSPACE_ID}'")
    if not cur.fetchone():
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            f"VALUES ('{WORKSPACE_ID}', '{WORKSPACE_NAME}', 'FolderOpened', NULL, NULL, 97, '/extreme-test', %s, 'workspace')",
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
        project_id = f'extreme-project-{p_idx}'
        project_name = f'极端项目{p_idx}'
        project_icon = random.choice(ICONS)

        cur.execute(f"SELECT id FROM menus WHERE id = '{project_id}'")
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                f"VALUES ('{project_id}', '{project_name}', '{project_icon}', NULL, '{WORKSPACE_ID}', {p_idx}, NULL, %s, 'project')",
                (psycopg2.extras.Json(['admin', 'developer']),)
            )
            conn.commit()
            created_menus += 1
            print(f"  [{p_idx}/{PROJECT_COUNT}] 创建项目: {project_name}")

        # 为每个项目创建35个数据页
        for d_idx in range(1, DATA_PAGES_PER_PROJECT + 1):
            page_id = f'extreme-page-{p_idx}-{d_idx}'
            page_name = f'数据页{p_idx}-{d_idx}'
            menu_id = f'extreme-menu-{p_idx}-{d_idx}'

            field_count = random.randint(2, 4)
            fields = generate_random_fields(field_count)

            cur.execute(f"SELECT id FROM page_configs WHERE id = '{page_id}'")
            if not cur.fetchone():
                cur.execute(
                    'INSERT INTO page_configs (id, name, fields, description) '
                    f"VALUES ('{page_id}', '{page_name}', %s, '极端测试数据页')",
                    (psycopg2.extras.Json(fields),)
                )
                conn.commit()
                created_pages += 1

            cur.execute(f"SELECT id FROM menus WHERE id = '{menu_id}'")
            if not cur.fetchone():
                menu_icon = random.choice(ICONS)
                menu_path = f'/extreme-test/{p_idx}/{d_idx}'
                cur.execute(
                    'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                    f"VALUES ('{menu_id}', '{page_name}', '{menu_icon}', '{page_id}', '{project_id}', {d_idx}, '{menu_path}', %s, 'data')",
                    (psycopg2.extras.Json(['admin', 'developer']),)
                )
                conn.commit()
                created_menus += 1

    print(f"\n[OK] 创建了 {PROJECT_COUNT} 个项目，每个 {DATA_PAGES_PER_PROJECT} 个数据页")

    # 3. 创建一个分组，分组下有25个子数据页
    group_project_id = 'extreme-project-1'
    group_id = 'extreme-group-1'
    group_name = '数据分组A'
    SUB_PAGES_COUNT = 28  # 分组下28个子数据页

    cur.execute(f"SELECT id FROM menus WHERE id = '{group_id}'")
    if not cur.fetchone():
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            f"VALUES ('{group_id}', '{group_name}', 'Folder', NULL, '{group_project_id}', 0, NULL, %s, 'project')",
            (psycopg2.extras.Json(['admin', 'developer']),)
        )
        conn.commit()
        created_menus += 1
        print(f"\n[OK] 创建分组: {group_name} (在极端项目1下，包含 {SUB_PAGES_COUNT} 个子数据页)")

        for g_idx in range(1, SUB_PAGES_COUNT + 1):
            sub_page_id = f'extreme-subpage-{g_idx}'
            sub_page_name = f'子数据页A-{g_idx}'
            sub_menu_id = f'extreme-submenu-{g_idx}'

            cur.execute(f"SELECT id FROM page_configs WHERE id = '{sub_page_id}'")
            if not cur.fetchone():
                fields = generate_random_fields(random.randint(2, 3))
                cur.execute(
                    'INSERT INTO page_configs (id, name, fields, description) '
                    f"VALUES ('{sub_page_id}', '{sub_page_name}', %s, '分组下的子数据页')",
                    (psycopg2.extras.Json(fields),)
                )
                conn.commit()
                created_pages += 1

            cur.execute(f"SELECT id FROM menus WHERE id = '{sub_menu_id}'")
            if not cur.fetchone():
                cur.execute(
                    'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                    f"VALUES ('{sub_menu_id}', '{sub_page_name}', 'Document', '{sub_page_id}', '{group_id}', {g_idx}, '/extreme-test/1/group/{g_idx}', %s, 'data')",
                    (psycopg2.extras.Json(['admin', 'developer']),)
                )
                conn.commit()
                created_menus += 1

    # 4. 再创建一个分组在项目2下，有30个子数据页
    group_project_id2 = 'extreme-project-2'
    group_id2 = 'extreme-group-2'
    group_name2 = '数据分组B'
    SUB_PAGES_COUNT2 = 30

    cur.execute(f"SELECT id FROM menus WHERE id = '{group_id2}'")
    if not cur.fetchone():
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            f"VALUES ('{group_id2}', '{group_name2}', 'Folder', NULL, '{group_project_id2}', 0, NULL, %s, 'project')",
            (psycopg2.extras.Json(['admin', 'developer']),)
        )
        conn.commit()
        created_menus += 1
        print(f"[OK] 创建分组: {group_name2} (在极端项目2下，包含 {SUB_PAGES_COUNT2} 个子数据页)")

        for g_idx in range(1, SUB_PAGES_COUNT2 + 1):
            sub_page_id = f'extreme-subpage-b-{g_idx}'
            sub_page_name = f'子数据页B-{g_idx}'
            sub_menu_id = f'extreme-submenu-b-{g_idx}'

            cur.execute(f"SELECT id FROM page_configs WHERE id = '{sub_page_id}'")
            if not cur.fetchone():
                fields = generate_random_fields(random.randint(2, 3))
                cur.execute(
                    'INSERT INTO page_configs (id, name, fields, description) '
                    f"VALUES ('{sub_page_id}', '{sub_page_name}', %s, '分组下的子数据页')",
                    (psycopg2.extras.Json(fields),)
                )
                conn.commit()
                created_pages += 1

            cur.execute(f"SELECT id FROM menus WHERE id = '{sub_menu_id}'")
            if not cur.fetchone():
                cur.execute(
                    'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
                    f"VALUES ('{sub_menu_id}', '{sub_page_name}', 'Document', '{sub_page_id}', '{group_id2}', {g_idx}, '/extreme-test/2/group/{g_idx}', %s, 'data')",
                    (psycopg2.extras.Json(['admin', 'developer']),)
                )
                conn.commit()
                created_menus += 1

    # 5. 统计最终结果
    cur.execute(f"SELECT COUNT(*) FROM menus WHERE parent_id = '{WORKSPACE_ID}'")
    project_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM menus m1
        WHERE m1.menu_type = 'data' AND (
            m1.parent_id IN (SELECT id FROM menus WHERE parent_id = '%s')
            OR m1.parent_id IN (SELECT id FROM menus WHERE parent_id IN (SELECT id FROM menus WHERE parent_id = '%s'))
        )
    """ % (WORKSPACE_ID, WORKSPACE_ID))
    data_page_count = cur.fetchone()[0]

    print(f"\n=== 最终统计 ===")
    print(f"工作空间 '{WORKSPACE_NAME}' 下:")
    print(f"  - 项目数量: {project_count}")
    print(f"  - 数据页总数: {data_page_count}")
    print(f"  - 新建页面配置: {created_pages}")
    print(f"  - 新建菜单数: {created_menus}")

    conn.close()
    print(f"\n=== 测试数据创建完成 ===")
    print(f"\n测试场景:")
    print(f"  - 项目1: 35个直接数据页 + 28个分组子数据页 = 63个")
    print(f"  - 项目2: 35个直接数据页 + 30个分组子数据页 = 65个")
    print(f"  - 项目3: 35个直接数据页 = 35个")
    print(f"  - 总计: 约163个数据页菜单项")


if __name__ == '__main__':
    main()