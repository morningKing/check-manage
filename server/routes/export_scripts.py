from flask import Blueprint, request, jsonify, Response
from db import get_db
from datetime import datetime, timezone
from auth import login_required, require_permission
from utils.operation_log import log_operation
from utils.script_runner import run_export_script, validate_export_script_scope
import psycopg2.extras
import uuid
import zipfile
import io
from urllib.parse import quote

export_scripts_bp = Blueprint('export_scripts', __name__)


def format_ts(dt):
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def row_to_dict(row):
    return {
        'id': row[0],
        'name': row[1],
        'description': row[2],
        'language': row[3],
        'script': row[4],
        'outputFormat': row[5],
        'createdAt': format_ts(row[6]),
        'updatedAt': format_ts(row[7]),
        'scope': row[8] if len(row) > 8 else 'page',
        'boundCollection': row[9] if len(row) > 9 else None,
        'boundMenuId': row[10] if len(row) > 10 else None,
    }


def _normalize_binding(scope, body):
    """返回 (bound_collection, bound_menu_id)；新建必绑、与 scope 一致，否则抛 ValueError。"""
    bc = (body.get('boundCollection') or '').strip() or None
    bm = (body.get('boundMenuId') or '').strip() or None
    if scope == 'menu':
        if bc:
            raise ValueError('菜单维度脚本只能绑定菜单，不能绑定数据页')
        if not bm:
            raise ValueError('菜单维度脚本必须绑定一个菜单')
        return None, bm
    else:
        if bm:
            raise ValueError('数据页维度脚本只能绑定数据页，不能绑定菜单')
        if not bc:
            raise ValueError('数据页维度脚本必须绑定一个数据页')
        return bc, None


@export_scripts_bp.route('/exportScripts', methods=['GET'])
@login_required
def list_scripts():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description, language, script, output_format, created_at, updated_at, scope, bound_collection, bound_menu_id '
            'FROM export_scripts ORDER BY created_at'
        )
        rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@export_scripts_bp.route('/exportScripts', methods=['POST'])
@require_permission('admin.export_scripts')
def create_script():
    body = request.get_json(force=True)
    scope = body.get('scope', 'page')
    try:
        validate_export_script_scope(scope, body.get('script', ''))
        bound_collection, bound_menu_id = _normalize_binding(scope, body)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    script_id = body.get('id') or f'script-{uuid.uuid4().hex[:8]}'
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO export_scripts (id, name, description, language, script, output_format, '
            'created_at, updated_at, scope, bound_collection, bound_menu_id) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (script_id, body.get('name', ''), body.get('description', ''),
             body.get('language', 'python'), body.get('script', ''),
             body.get('outputFormat', 'json'), now, now, scope, bound_collection, bound_menu_id),
        )
    log_operation('create', 'export_script', script_id, body.get('name', ''),
                  f'新增导出脚本「{body.get("name", "")}」')
    return jsonify({
        'id': script_id, 'name': body.get('name', ''), 'description': body.get('description', ''),
        'language': body.get('language', 'python'), 'script': body.get('script', ''),
        'outputFormat': body.get('outputFormat', 'json'), 'scope': scope,
        'boundCollection': bound_collection, 'boundMenuId': bound_menu_id,
        'createdAt': format_ts(now), 'updatedAt': format_ts(now),
    }), 201


@export_scripts_bp.route('/exportScripts/<script_id>', methods=['PUT'])
@require_permission('admin.export_scripts')
def update_script(script_id):
    body = request.get_json(force=True)
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        cur = conn.cursor()
        # Validate scope/code consistency. For partial updates, fall back to the
        # stored value for whichever of scope/script isn't in the body.
        if 'script' in body or 'scope' in body:
            eff_scope = body.get('scope')
            eff_script = body.get('script')
            if eff_scope is None or eff_script is None:
                cur.execute('SELECT script, scope FROM export_scripts WHERE id = %s', (script_id,))
                existing = cur.fetchone()
                if not existing:
                    return jsonify({'error': 'Not found'}), 404
                if eff_script is None:
                    eff_script = existing[0]
                if eff_scope is None:
                    eff_scope = existing[1]
            try:
                validate_export_script_scope(eff_scope, eff_script)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        sets = ['updated_at=%s']
        params = [now]
        if 'name' in body:
            sets.append('name=%s')
            params.append(body['name'])
        if 'description' in body:
            sets.append('description=%s')
            params.append(body['description'])
        if 'language' in body:
            sets.append('language=%s')
            params.append(body['language'])
        if 'script' in body:
            sets.append('script=%s')
            params.append(body['script'])
        if 'outputFormat' in body:
            sets.append('output_format=%s')
            params.append(body['outputFormat'])
        if 'scope' in body:
            sets.append('scope=%s')
            params.append(body['scope'])
        if 'boundCollection' in body:
            sets.append('bound_collection=%s')
            params.append((body.get('boundCollection') or '').strip() or None)
        if 'boundMenuId' in body:
            sets.append('bound_menu_id=%s')
            params.append((body.get('boundMenuId') or '').strip() or None)
        params.append(script_id)
        cur.execute(
            f'UPDATE export_scripts SET {", ".join(sets)} WHERE id=%s', params
        )
        cur.execute(
            'SELECT id, name, description, language, script, output_format, created_at, updated_at, scope, bound_collection, bound_menu_id '
            'FROM export_scripts WHERE id = %s', (script_id,)
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    result = row_to_dict(row)
    log_operation('update', 'export_script', script_id, result['name'],
                  f'修改导出脚本「{result["name"]}」')
    return jsonify(result)


@export_scripts_bp.route('/exportScripts/<script_id>', methods=['DELETE'])
@require_permission('admin.export_scripts')
def delete_script(script_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT name FROM export_scripts WHERE id = %s', (script_id,))
        row = cur.fetchone()
        script_name = row[0] if row else script_id
        cur.execute('DELETE FROM export_scripts WHERE id = %s', (script_id,))
        # Remove script from all page_configs bindings
        cur.execute('SELECT id, export_scripts, row_export_scripts FROM page_configs')
        for pc_id, scripts, row_scripts in cur.fetchall():
            changed = False
            if scripts and script_id in scripts:
                scripts = [s for s in scripts if s != script_id]
                changed = True
            if row_scripts and script_id in row_scripts:
                row_scripts = [s for s in row_scripts if s != script_id]
                changed = True
            if changed:
                cur.execute(
                    'UPDATE page_configs SET export_scripts = %s, row_export_scripts = %s WHERE id = %s',
                    (psycopg2.extras.Json(scripts or []), psycopg2.extras.Json(row_scripts or []), pc_id),
                )
    log_operation('delete', 'export_script', script_id, script_name,
                  f'删除导出脚本「{script_name}」')
    return jsonify({})


@export_scripts_bp.route('/exportScripts/<script_id>/test', methods=['POST'])
@require_permission('admin.export_scripts')
def test_script(script_id):
    """Test a script with sample data or real collection data."""
    from utils.script_runner import run_menu_export_script
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT script, output_format, scope FROM export_scripts WHERE id = %s',
            (script_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404

        script_code = row[0]
        output_format = row[1]
        scope = row[2] or 'page'

        collection = (body.get('collection') or '').strip()
        branch_id = body.get('branchId', 'main')
        menu_id = (body.get('menuId') or '').strip()

        # 菜单级脚本 + 指定菜单：用真实 menu_data（该菜单下所有数据页）+ 引用解析，
        # 与生产 execute_menu_export 一致——菜单级脚本本应针对菜单测试，而非单个数据页。
        if scope == 'menu' and menu_id:
            from utils.menu_export import get_menu_collections
            from utils.export_references import resolve_references
            from datetime import timezone as _tz
            cur.execute('SELECT name FROM menus WHERE id = %s', (menu_id,))
            mrow = cur.fetchone()
            if not mrow:
                return jsonify({'success': False, 'error': '菜单不存在'}), 404
            menu_name = mrow[0]
            collections = get_menu_collections(cur, menu_id)
            if not collections:
                return jsonify({'success': False, 'error': '该菜单下没有数据页'}), 400
            menu_data = []
            total = 0
            for coll in collections:
                cur.execute('SELECT name, fields FROM page_configs WHERE id = %s', (f'page-{coll}',))
                pc = cur.fetchone()
                recs = []
                cur.execute('SELECT id, data, created_at FROM dynamic_data '
                            'WHERE collection = %s AND branch_id = %s ORDER BY created_at',
                            (coll, branch_id))
                for r in cur.fetchall():
                    rec = {'id': r[0]}
                    if r[1]:
                        rec.update(r[1])
                    if r[2] and hasattr(r[2], 'astimezone'):
                        rec['createdAt'] = r[2].astimezone(_tz.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                    recs.append(rec)
                menu_data.append({'collection': coll, 'pageName': pc[0] if pc else coll,
                                  'records': recs, 'fields': pc[1] if pc else [], 'recordCount': len(recs)})
                total += len(recs)
            try:
                refs = resolve_references(cur, menu_data, export_branch=branch_id)
            except Exception:
                refs = {}
            try:
                files = run_menu_export_script(script_code, menu_data, menu_name, output_format, references=refs)
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)}), 400
            if not files:
                return jsonify({'success': False, 'error': '脚本未生成任何文件'}), 400
            result_bytes, filename, content_type = files[0]
            return jsonify({
                'success': True,
                'preview': result_bytes[:5000].decode('utf-8', errors='replace'),
                'filename': filename, 'contentType': content_type, 'size': len(result_bytes),
                'recordCount': total,
                'extra': f'（{len(menu_data)} 个数据页 / {len(files)} 个文件）',
            })

        if collection:
            # 从真实数据页取数据
            page_id = f'page-{collection}'
            cur.execute('SELECT name, fields FROM page_configs WHERE id = %s', (page_id,))
            pc = cur.fetchone()
            page_name = pc[0] if pc else collection
            fields = pc[1] if pc else []

            cur.execute(
                'SELECT id, data, created_at FROM dynamic_data '
                'WHERE collection = %s AND branch_id = %s ORDER BY created_at LIMIT 100',
                (collection, branch_id),
            )
            from datetime import timezone as _tz
            data = []
            for r in cur.fetchall():
                rec = {'id': r[0]}
                if r[1]:
                    rec.update(r[1])
                if r[2]:
                    ts = r[2]
                    if hasattr(ts, 'astimezone'):
                        ts = ts.astimezone(_tz.utc)
                    rec['createdAt'] = ts.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                data.append(rec)
        else:
            # 硬编码样例数据（兜底）
            sample_data = body.get('data', [])
            sample_fields = body.get('fields', [])
            page_name = body.get('pageName', '测试')
            data = sample_data
            fields = sample_fields

        # 页面级脚本：解析引用以便预览时也能 join（菜单级在上面分支单独处理）
        page_refs = {}
        if scope != 'menu' and collection:
            from utils.export_references import resolve_page_references
            try:
                page_refs = resolve_page_references(cur, collection, data, fields, export_branch=branch_id)
            except Exception:
                page_refs = {}

    try:
        if scope == 'menu':
            menu_data = [{
                'collection': collection or 'sample',
                'pageName': page_name,
                'records': data,
                'fields': fields,
                'recordCount': len(data),
            }]
            files = run_menu_export_script(script_code, menu_data, page_name, output_format)
            # 返回第一个文件的预览
            if files:
                result_bytes, filename, content_type = files[0]
            else:
                return jsonify({'success': False, 'error': '脚本未生成任何文件'}), 400
        else:
            result_bytes, filename, content_type = run_export_script(
                script_code, data, fields, page_name, output_format, references=page_refs
            )

        preview = result_bytes[:5000].decode('utf-8', errors='replace')
        extra = f'（共 {len(files)} 个文件）' if scope == 'menu' else ''
        return jsonify({
            'success': True,
            'preview': preview,
            'filename': filename,
            'contentType': content_type,
            'size': len(result_bytes),
            'recordCount': len(data),
            'extra': extra,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@export_scripts_bp.route('/exportScripts/<script_id>/debug', methods=['POST'])
@require_permission('admin.export_scripts')
def debug_script(script_id):
    """追踪式断点调试：在指定行下断点，跑一次脚本，回传每次命中的局部变量快照 + 行执行轨迹。
    body: { breakpoints:[行号], collection?/data?/fields?/pageName?, branchId? }"""
    from utils.script_runner import debug_export_script
    from datetime import timezone as _tz
    body = request.get_json(force=True)
    breakpoints = body.get('breakpoints') or []
    menu_id = (body.get('menuId') or '').strip()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT script, output_format, scope FROM export_scripts WHERE id = %s', (script_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        script_code, output_format, scope = row[0], row[1], (row[2] or 'page')
        collection = (body.get('collection') or '').strip()
        branch_id = body.get('branchId', 'main')

        # 菜单级脚本 + 指定菜单：用真实多表 menu_data + 引用解析（与 test_script 一致）
        if scope == 'menu' and menu_id:
            from utils.menu_export import get_menu_collections
            from utils.export_references import resolve_references
            cur.execute('SELECT name FROM menus WHERE id = %s', (menu_id,))
            mrow = cur.fetchone()
            if not mrow:
                return jsonify({'success': False, 'error': '菜单不存在'}), 404
            menu_name = mrow[0]
            collections = get_menu_collections(cur, menu_id)
            if not collections:
                return jsonify({'success': False, 'error': '该菜单下没有数据页'}), 400
            menu_data = []
            total = 0
            for coll in collections:
                cur.execute('SELECT name, fields FROM page_configs WHERE id = %s', (f'page-{coll}',))
                pc = cur.fetchone()
                recs = []
                cur.execute('SELECT id, data, created_at FROM dynamic_data '
                            'WHERE collection = %s AND branch_id = %s ORDER BY created_at',
                            (coll, branch_id))
                for r in cur.fetchall():
                    rec = {'id': r[0]}
                    if r[1]:
                        rec.update(r[1])
                    if r[2] and hasattr(r[2], 'astimezone'):
                        rec['createdAt'] = r[2].astimezone(_tz.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                    recs.append(rec)
                menu_data.append({'collection': coll, 'pageName': pc[0] if pc else coll,
                                  'records': recs, 'fields': pc[1] if pc else [], 'recordCount': len(recs)})
                total += len(recs)
            try:
                refs = resolve_references(cur, menu_data, export_branch=branch_id)
            except Exception:
                refs = {}
            script_locals = {'menu_data': menu_data, 'menu_name': menu_name, 'total_records': total,
                             'references': refs, 'result': None, 'filename': None, 'content_type': None}
            try:
                return jsonify(debug_export_script(script_code, 'menu', script_locals, breakpoints))
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)}), 400

        page_refs = {}
        if collection:
            cur.execute('SELECT name, fields FROM page_configs WHERE id = %s', (f'page-{collection}',))
            pc = cur.fetchone()
            page_name = pc[0] if pc else collection
            fields = pc[1] if pc else []
            cur.execute('SELECT id, data, created_at FROM dynamic_data '
                        'WHERE collection = %s AND branch_id = %s ORDER BY created_at LIMIT 100',
                        (collection, branch_id))
            data = []
            for r in cur.fetchall():
                rec = {'id': r[0]}
                if r[1]:
                    rec.update(r[1])
                if r[2] and hasattr(r[2], 'astimezone'):
                    rec['createdAt'] = r[2].astimezone(_tz.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                data.append(rec)
            # 页面级脚本：解析引用，调试时也能看到 references
            if scope != 'menu':
                from utils.export_references import resolve_page_references
                try:
                    page_refs = resolve_page_references(cur, collection, data, fields, export_branch=branch_id)
                except Exception:
                    page_refs = {}
        else:
            data = body.get('data', [])
            fields = body.get('fields', [])
            page_name = body.get('pageName', '测试')

    if scope == 'menu':
        profile = 'menu'
        menu_data = [{'collection': collection or 'sample', 'pageName': page_name,
                      'records': data, 'fields': fields, 'recordCount': len(data)}]
        script_locals = {'menu_data': menu_data, 'menu_name': page_name, 'total_records': len(data),
                         'references': {}, 'result': None, 'filename': None, 'content_type': None}
    else:
        profile = 'export'
        script_locals = {'data': data, 'fields': fields, 'page_name': page_name,
                         'references': page_refs, 'result': None, 'filename': None, 'content_type': None}

    try:
        return jsonify(debug_export_script(script_code, profile, script_locals, breakpoints))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@export_scripts_bp.route('/exportScripts/execute', methods=['POST'])
@login_required
def execute_script():
    """Execute an export script and return the generated file."""
    body = request.get_json(force=True)
    script_id = body.get('scriptId')
    collection = body.get('collection')
    record_id = body.get('recordId')  # optional: for row-level export
    branch_id = body.get('branchId', 'main')  # NEW: branch filter, default main

    if not script_id or not collection:
        return jsonify({'error': '缺少参数 scriptId 或 collection'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        # Fetch script
        cur.execute(
            'SELECT script, output_format FROM export_scripts WHERE id = %s',
            (script_id,),
        )
        script_row = cur.fetchone()
        if not script_row:
            return jsonify({'error': '脚本不存在'}), 404

        script_code = script_row[0]
        output_format = script_row[1]

        # Fetch collection data (single record or all), filtered by branch
        if record_id:
            cur.execute(
                'SELECT id, collection, data, created_at FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                (collection, record_id, branch_id),
            )
        else:
            cur.execute(
                'SELECT id, collection, data, created_at FROM dynamic_data WHERE collection = %s AND branch_id = %s ORDER BY created_at',
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

        # Fetch page config fields
        page_id = f'page-{collection}'
        cur.execute(
            'SELECT name, fields FROM page_configs WHERE id = %s', (page_id,)
        )
        pc_row = cur.fetchone()
        page_name = pc_row[0] if pc_row else collection
        fields = pc_row[1] if pc_row else []

        # 解析跨页/跨项目引用，注入脚本 references（同时给记录补 _relations）
        from utils.export_references import resolve_page_references
        try:
            refs = resolve_page_references(cur, collection, data, fields, export_branch=branch_id)
        except Exception:
            refs = {}

    try:
        result_bytes, filename, content_type = run_export_script(
            script_code, data, fields, page_name, output_format, references=refs
        )
    except Exception as e:
        return jsonify({'error': f'脚本执行失败：{str(e)}'}), 400

    return Response(
        result_bytes,
        mimetype=content_type,
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename)}",
            'Content-Length': str(len(result_bytes)),
        },
    )


@export_scripts_bp.route('/exportScripts/batchExport', methods=['POST'])
@login_required
def batch_export():
    """Execute multiple export scripts and return results as a zip file."""
    body = request.get_json(force=True)
    tasks = body.get('tasks', [])
    default_branch_id = body.get('branchId', 'main')  # NEW: default branch filter
    if not tasks:
        return jsonify({'error': '未选择导出任务'}), 400

    buf = io.BytesIO()
    seen_filenames = {}
    file_count = 0
    errors = []

    with get_db() as conn:
        cur = conn.cursor()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for idx, task in enumerate(tasks):
                script_id = task.get('scriptId')
                collection = task.get('collection')
                branch_id = task.get('branchId', default_branch_id)  # NEW: per-task branch override
                if not script_id or not collection:
                    errors.append(f'任务 {idx + 1}: 缺少参数')
                    continue

                # Fetch script
                cur.execute(
                    'SELECT script, output_format FROM export_scripts WHERE id = %s',
                    (script_id,),
                )
                script_row = cur.fetchone()
                if not script_row:
                    errors.append(f'任务 {idx + 1}: 脚本 {script_id} 不存在')
                    continue

                script_code = script_row[0]
                output_format = script_row[1]

                # Fetch collection data, filtered by branch
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

                # Fetch page config
                page_id = f'page-{collection}'
                cur.execute(
                    'SELECT name, fields FROM page_configs WHERE id = %s', (page_id,)
                )
                pc_row = cur.fetchone()
                page_name = pc_row[0] if pc_row else collection
                fields = pc_row[1] if pc_row else []

                # 解析跨页/跨项目引用，注入脚本 references
                from utils.export_references import resolve_page_references
                try:
                    refs = resolve_page_references(cur, collection, data, fields, export_branch=branch_id)
                except Exception:
                    refs = {}

                # Execute script
                try:
                    result_bytes, filename, content_type = run_export_script(
                        script_code, data, fields, page_name, output_format, references=refs
                    )
                except Exception as e:
                    errors.append(f'任务 {idx + 1} ({page_name}): {str(e)}')
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

                zf.writestr(filename, result_bytes)
                file_count += 1

    if file_count == 0:
        return jsonify({'error': '所有导出任务均失败', 'details': errors}), 400

    zip_bytes = buf.getvalue()
    zip_filename = '批量导出.zip'

    return Response(
        zip_bytes,
        mimetype='application/zip',
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(zip_filename)}",
            'Content-Length': str(len(zip_bytes)),
        },
    )
