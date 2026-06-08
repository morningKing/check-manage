"""
菜单级导出工具函数

职责：
- 递归获取菜单下所有叶子页面的 collection
- 获取菜单下所有页面的数据统计信息
- 执行菜单级导出

脚本 scope 说明：
- 'page': 表级脚本，逐表执行，每个表生成一个文件
- 'row': 行级脚本，不适用于菜单级导出
- 'menu': 菜单级脚本，接收所有数据，可生成单文件或多文件
"""

import zipfile
import io
from urllib.parse import quote
from datetime import datetime, timezone
from utils.script_runner import run_export_script, run_menu_export_script


def get_menu_collections(cur, menu_id):
    """
    递归获取菜单下所有叶子页面的 collection 列表

    参数：
    - cur: 数据库游标
    - menu_id: 菜单ID

    返回：collection 字符串列表
    """
    collections = []

    # 检查当前菜单是否有 page_id（叶子菜单）
    cur.execute('SELECT page_id FROM menus WHERE id = %s', (menu_id,))
    row = cur.fetchone()
    if row and row[0]:
        # 提取 collection（page-{collection} -> collection）
        page_id = row[0]
        if page_id.startswith('page-'):
            collection = page_id[5:]  # Remove 'page-' prefix
            collections.append(collection)

    # 递归获取子菜单
    cur.execute('SELECT id FROM menus WHERE parent_id = %s', (menu_id,))
    for child_row in cur.fetchall():
        collections.extend(get_menu_collections(cur, child_row[0]))

    return collections


def get_menu_collections_with_info(cur, menu_id, branch_id='main'):
    """
    递归获取菜单下所有叶子页面的详细信息（包含记录数）

    参数：
    - cur: 数据库游标
    - menu_id: 菜单ID
    - branch_id: 分支ID（默认 'main'）

    返回：[{collection, pageName, recordCount}, ...]
    """
    pages = []

    # 检查当前菜单是否有 page_id（叶子菜单）
    cur.execute('SELECT page_id FROM menus WHERE id = %s', (menu_id,))
    row = cur.fetchone()
    if row and row[0]:
        page_id = row[0]
        if page_id.startswith('page-'):
            collection = page_id[5:]  # Remove 'page-' prefix

            # Get page name from page_configs
            cur.execute('SELECT name FROM page_configs WHERE id = %s', (page_id,))
            pc_row = cur.fetchone()
            page_name = pc_row[0] if pc_row else collection

            # Get record count (filtered by branch)
            cur.execute('SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s', (collection, branch_id))
            count_row = cur.fetchone()
            record_count = count_row[0] if count_row else 0

            pages.append({
                'collection': collection,
                'pageName': page_name,
                'recordCount': record_count
            })

    # 递归获取子菜单
    cur.execute('SELECT id FROM menus WHERE parent_id = %s', (menu_id,))
    for child_row in cur.fetchall():
        pages.extend(get_menu_collections_with_info(cur, child_row[0], branch_id))

    return pages


def execute_menu_export(conn, menu_ids, script_id=None, branch_id='main'):
    """
    执行菜单级导出

    参数：
    - conn: 数据库连接
    - menu_ids: 菜单ID列表
    - script_id: 可选，指定的导出脚本ID（覆盖菜单绑定）
    - branch_id: 分支ID（默认 'main'）

    返回：(zip_bytes, filename, error_messages)

    脚本执行模式：
    - scope='page': 逐表执行，每个表生成一个文件
    - scope='menu': 菜单级执行，所有数据一次性注入，脚本可生成单文件或多文件
    """
    cur = conn.cursor()
    buf = io.BytesIO()
    errors = []
    file_count = 0
    seen_filenames = {}

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for menu_id in menu_ids:
            # Get menu info
            cur.execute('SELECT name, export_script_id FROM menus WHERE id = %s', (menu_id,))
            menu_row = cur.fetchone()
            if not menu_row:
                errors.append(f'菜单 {menu_id} 不存在')
                continue

            menu_name = menu_row[0]
            menu_script_id = menu_row[1]

            # Determine which script to use (parameter > menu binding)
            effective_script_id = script_id or menu_script_id

            if not effective_script_id:
                errors.append(f'菜单「{menu_name}」未绑定导出脚本且未指定脚本')
                continue

            # Get script info including scope
            cur.execute(
                'SELECT id, name, script, output_format, scope FROM export_scripts WHERE id = %s',
                (effective_script_id,),
            )
            script_row = cur.fetchone()
            if not script_row:
                errors.append(f'导出脚本 {effective_script_id} 不存在')
                continue

            script_code = script_row[2]
            output_format = script_row[3]
            script_scope = script_row[4] if len(script_row) > 4 else 'page'

            # Get all collections under this menu
            collections = get_menu_collections(cur, menu_id)
            if not collections:
                errors.append(f'菜单「{menu_name}」下没有数据页面')
                continue

            if script_scope == 'menu':
                # ========== 菜单级脚本模式 ==========
                # 收集所有数据表的信息
                menu_data = []
                for collection in collections:
                    # Get page config
                    page_id = f'page-{collection}'
                    cur.execute(
                        'SELECT name, fields FROM page_configs WHERE id = %s', (page_id,)
                    )
                    pc_row = cur.fetchone()
                    page_name = pc_row[0] if pc_row else collection
                    fields = pc_row[1] if pc_row else []

                    # Fetch collection data (filtered by branch)
                    cur.execute(
                        'SELECT id, collection, data, created_at FROM dynamic_data '
                        'WHERE collection = %s AND branch_id = %s ORDER BY created_at',
                        (collection, branch_id),
                    )
                    rows = cur.fetchall()
                    records = []
                    for r in rows:
                        record = {'id': r[0]}
                        if r[2]:
                            record.update(r[2])
                        if r[3]:
                            ts = r[3]
                            if hasattr(ts, 'astimezone'):
                                ts = ts.astimezone(timezone.utc)
                            record['createdAt'] = ts.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                        records.append(record)

                    menu_data.append({
                        'collection': collection,
                        'pageName': page_name,
                        'records': records,
                        'fields': fields,
                        'recordCount': len(records)
                    })

                # Execute menu-level export script
                try:
                    files = run_menu_export_script(
                        script_code, menu_data, menu_name, output_format
                    )
                except Exception as e:
                    errors.append(f'「{menu_name}」: {str(e)}')
                    continue

                # Add all files to zip
                for result_bytes, filename, content_type in files:
                    # Deduplicate filenames
                    if filename in seen_filenames:
                        seen_filenames[filename] += 1
                        name_part, dot, ext = filename.rpartition('.')
                        if dot:
                            filename = f'{name_part}_{seen_filenames[filename]}.{ext}'
                        else:
                            filename = f'{filename}_{seen_filenames[filename]}'
                    else:
                        seen_filenames[filename] = 0

                    # Add to zip with menu folder structure
                    zip_path = f'{menu_name}/{filename}'
                    zf.writestr(zip_path, result_bytes)
                    file_count += 1

            else:
                # ========== 表级脚本模式（逐表执行） ==========
                for collection in collections:
                    # Get page config
                    page_id = f'page-{collection}'
                    cur.execute(
                        'SELECT name, fields FROM page_configs WHERE id = %s', (page_id,)
                    )
                    pc_row = cur.fetchone()
                    page_name = pc_row[0] if pc_row else collection
                    fields = pc_row[1] if pc_row else []

                    # Fetch collection data (filtered by branch)
                    cur.execute(
                        'SELECT id, collection, data, created_at FROM dynamic_data '
                        'WHERE collection = %s AND branch_id = %s ORDER BY created_at',
                        (collection, branch_id),
                    )
                    rows = cur.fetchall()
                    data = []
                    for r in rows:
                        record = {'id': r[0]}
                        if r[2]:
                            record.update(r[2])
                        if r[3]:
                            ts = r[3]
                            if hasattr(ts, 'astimezone'):
                                ts = ts.astimezone(timezone.utc)
                            record['createdAt'] = ts.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                        data.append(record)

                    # Execute export script
                    try:
                        result_bytes, filename, content_type = run_export_script(
                            script_code, data, fields, page_name, output_format
                        )
                    except Exception as e:
                        errors.append(f'「{menu_name}」→「{page_name}」: {str(e)}')
                        continue

                    # Deduplicate filenames
                    if filename in seen_filenames:
                        seen_filenames[filename] += 1
                        name_part, dot, ext = filename.rpartition('.')
                        if dot:
                            filename = f'{name_part}_{seen_filenames[filename]}.{ext}'
                        else:
                            filename = f'{filename}_{seen_filenames[filename]}'
                    else:
                        seen_filenames[filename] = 0

                    # Add to zip with menu folder structure
                    zip_path = f'{menu_name}/{filename}'
                    zf.writestr(zip_path, result_bytes)
                    file_count += 1

    if file_count == 0:
        return None, None, errors

    zip_bytes = buf.getvalue()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_filename = f'菜单导出_{timestamp}.zip'

    return zip_bytes, zip_filename, errors


def batch_clear(conn, collections, branch_id='main'):
    """
    清空多个 collection 在指定分支的全部记录，并清理悬挂的 M:N 关系。

    返回：{'perCollection': {coll: deleted_count}, 'totalDeleted': int, 'relationsDeleted': int}
    单事务：调用方负责 commit/rollback（get_db 上下文管理器已处理）。
    """
    cur = conn.cursor()
    per = {}
    total = 0
    for coll in collections:
        cur.execute(
            'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (coll, branch_id),
        )
        per[coll] = cur.rowcount
        total += cur.rowcount

    cur.execute(
        'DELETE FROM data_relations WHERE branch_id = %s '
        'AND (collection = ANY(%s) OR related_collection = ANY(%s))',
        (branch_id, list(collections), list(collections)),
    )
    relations_deleted = cur.rowcount

    return {'perCollection': per, 'totalDeleted': total, 'relationsDeleted': relations_deleted}