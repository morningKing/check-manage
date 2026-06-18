"""菜单级导出引用解析（方案 A）回归测试。

覆盖：内联 reference / quoteSelect 解析、data_relations 回挂 + 解析、缺失引用标记、
跨项目 read-only 钉版本按 pinned_version 分支解析、run_menu_export_script 注入 references 端到端。
直连真实 DB（casemanage），与 test_workflow_* 同模式。
"""
import io
import json
import zipfile
import psycopg2.extras
from db import get_db
from utils.export_references import resolve_references, resolve_page_references
from utils.script_runner import run_menu_export_script, run_export_script
from utils.menu_export import execute_menu_export


def _seed_page(cur, coll, fields):
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                (f'page-{coll}', coll, psycopg2.extras.Json(fields)))


def _ins(cur, coll, rid, data, branch='main'):
    cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES (%s,%s,%s,%s)",
                (rid, coll, psycopg2.extras.Json(data), branch))


def _cleanup(colls=(), branches=('main',)):
    with get_db() as conn:
        cur = conn.cursor()
        for c in colls:
            cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (c,))
            cur.execute("DELETE FROM data_relations WHERE collection=%s OR related_collection=%s", (c, c))
            cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{c}',))
        conn.commit()


def test_inline_reference_resolved():
    src, tgt = 'zzrefsrc', 'zzreftgt'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, src, [{'fieldName': 'ref', 'controlType': 'reference',
                                   'referenceConfig': {'targetCollection': tgt}}])
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, tgt, 'zzi-t1', {'name': '产品A'})
            conn.commit()
            menu_data = [{'collection': src,
                          'fields': [{'fieldName': 'ref', 'controlType': 'reference',
                                      'referenceConfig': {'targetCollection': tgt}}],
                          'records': [{'id': 's1', 'ref': 'zzi-t1'}]}]
            refs = resolve_references(cur, menu_data, export_branch='main')
        assert refs[tgt]['zzi-t1']['name'] == '产品A'
    finally:
        _cleanup([src, tgt])


def test_quoteselect_multi_resolved():
    src, tgt = 'zzqsrc', 'zzqtgt'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, tgt, 'zzq-q1', {'name': '标签1'})
            _ins(cur, tgt, 'zzq-q2', {'name': '标签2'})
            conn.commit()
            menu_data = [{'collection': src,
                          'fields': [{'fieldName': 'tags', 'controlType': 'quoteSelect',
                                      'quoteConfig': {'targetCollection': tgt}}],
                          'records': [{'id': 's1', 'tags': ['zzq-q1', 'zzq-q2']}]}]
            refs = resolve_references(cur, menu_data, export_branch='main')
        assert refs[tgt]['zzq-q1']['name'] == '标签1'
        assert refs[tgt]['zzq-q2']['name'] == '标签2'
    finally:
        _cleanup([src, tgt])


def test_relation_attached_and_resolved():
    src, tgt = 'zzrelsrc', 'zzreltgt'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, src, [{'fieldName': 'links', 'controlType': 'relation',
                                   'relationConfig': {'targetCollection': tgt}}])
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, tgt, 'zzr-r1', {'name': '关联A'})
            cur.execute("DELETE FROM data_relations WHERE collection=%s", (src,))
            cur.execute("INSERT INTO data_relations (collection,record_id,field_name,related_collection,related_id,branch_id) "
                        "VALUES (%s,%s,%s,%s,%s,'main')", (src, 's1', 'links', tgt, 'zzr-r1'))
            conn.commit()
            menu_data = [{'collection': src, 'fields': [], 'records': [{'id': 's1'}]}]
            refs = resolve_references(cur, menu_data, export_branch='main')
        # 记录被就地补上 _relations
        assert menu_data[0]['records'][0]['_relations'] == {'links': ['zzr-r1']}
        # 被关联记录进入 references
        assert refs[tgt]['zzr-r1']['name'] == '关联A'
    finally:
        _cleanup([src, tgt])


def test_missing_reference_marked_none():
    src, tgt = 'zzmisssrc', 'zzmisstgt'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            conn.commit()
            menu_data = [{'collection': src,
                          'fields': [{'fieldName': 'ref', 'controlType': 'reference',
                                      'referenceConfig': {'targetCollection': tgt}}],
                          'records': [{'id': 's1', 'ref': 'nope'}]}]
            refs = resolve_references(cur, menu_data, export_branch='main')
        assert tgt in refs and refs[tgt]['nope'] is None  # 已尝试、缺失
    finally:
        _cleanup([src, tgt])


def test_cross_project_readonly_pinned_version():
    """跨项目 read-only 钉版本：被引用记录只在 pinned_version 分支，按依赖解析到该分支。"""
    src, tgt = 'zzcpsrc', 'zzcptgt'
    ver = 'zzcp-ver1'
    dep_id, rel_id = 'zzcp-dep', 'zzcp-rel'
    m_src, m_tgt = 'zzcp-mproj-src', 'zzcp-mproj-tgt'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            # 被引用记录只在 pinned 版本分支，main 没有
            _ins(cur, tgt, 'zzc-c1', {'name': '钉版本记录'}, branch=ver)
            # 支撑 FK：menus + project_versions
            cur.execute("INSERT INTO menus (id,name) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING", (m_src, '源项目'))
            cur.execute("INSERT INTO menus (id,name) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING", (m_tgt, '目标项目'))
            cur.execute("DELETE FROM project_versions WHERE id=%s", (ver,))
            cur.execute("INSERT INTO project_versions (id,project_menu_id,name,version_type,status) "
                        "VALUES (%s,%s,%s,'snapshot','active')", (ver, m_tgt, 'v1'))
            cur.execute("DELETE FROM project_dependency_relations WHERE id=%s", (rel_id,))
            cur.execute("DELETE FROM project_dependencies WHERE id=%s", (dep_id,))
            cur.execute("INSERT INTO project_dependencies (id,source_project,source_branch,target_project,target_branch,relation_type,pinned_version) "
                        "VALUES (%s,%s,'main',%s,'main','read-only',%s)", (dep_id, m_src, m_tgt, ver))
            cur.execute("INSERT INTO project_dependency_relations (id,dependency_id,source_collection,source_field,target_collection) "
                        "VALUES (%s,%s,%s,'ref',%s)", (rel_id, dep_id, src, tgt))
            conn.commit()
            menu_data = [{'collection': src,
                          'fields': [{'fieldName': 'ref', 'controlType': 'reference',
                                      'referenceConfig': {'targetCollection': tgt}}],
                          'records': [{'id': 's1', 'ref': 'zzc-c1'}]}]
            # 导出分支是 main，但被引用记录在 ver 分支——必须按依赖解析到 ver
            refs = resolve_references(cur, menu_data, export_branch='main')
        assert refs[tgt]['zzc-c1'] is not None, '未按 pinned_version 解析到跨项目记录'
        assert refs[tgt]['zzc-c1']['name'] == '钉版本记录'
    finally:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM project_dependency_relations WHERE id=%s", (rel_id,))
            cur.execute("DELETE FROM project_dependencies WHERE id=%s", (dep_id,))
            cur.execute("DELETE FROM project_versions WHERE id=%s", (ver,))
            cur.execute("DELETE FROM menus WHERE id IN (%s,%s)", (m_src, m_tgt))
            cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (tgt,))
            conn.commit()
        _cleanup([src, tgt])


def test_run_menu_export_script_injects_references():
    """端到端：菜单脚本用注入的 references join 被引用页（未被选中）。"""
    src, tgt = 'zzesrc', 'zzetgt'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, tgt, 'zzp-p1', {'name': '苹果'})
            conn.commit()
            menu_data = [{'collection': src,
                          'fields': [{'fieldName': 'ref', 'controlType': 'reference',
                                      'referenceConfig': {'targetCollection': tgt}}],
                          'records': [{'id': 's1', 'ref': 'zzp-p1'}], 'recordCount': 1}]
            refs = resolve_references(cur, menu_data, export_branch='main')
        script = (
            "out = []\n"
            "for t in menu_data:\n"
            "    for r in t['records']:\n"
            "        ref = references.get('zzetgt', {}).get(r.get('ref'))\n"
            "        out.append({'id': r['id'], 'refName': ref['name'] if ref else None})\n"
            "result = json.dumps(out, ensure_ascii=False)\n"
        )
        files = run_menu_export_script(script, menu_data, '测试菜单', 'json', references=refs)
        parsed = json.loads(files[0][0])
        assert parsed == [{'id': 's1', 'refName': '苹果'}]
    finally:
        _cleanup([src, tgt])


def test_resolve_page_references_resolves_inline_reference():
    """页面级便捷封装：单页 records 的内联 reference 能解析到被引用记录。"""
    src, tgt = 'zzpgsrc', 'zzpgtgt'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, tgt, 'zzpg-t1', {'name': '客户A'})
            conn.commit()
            fields = [{'fieldName': 'ref', 'controlType': 'reference',
                       'referenceConfig': {'targetCollection': tgt}}]
            records = [{'id': 's1', 'ref': 'zzpg-t1'}]
            refs = resolve_page_references(cur, src, records, fields, export_branch='main')
        assert refs[tgt]['zzpg-t1']['name'] == '客户A'
    finally:
        _cleanup([src, tgt])


def test_run_export_script_injects_references():
    """端到端（页面级）：page 脚本用注入的 references join 被引用页（修复前 references 缺失）。"""
    src, tgt = 'zzpesrc', 'zzpetgt'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, tgt, 'zzpe-t1', {'name': '香蕉'})
            conn.commit()
            fields = [{'fieldName': 'ref', 'controlType': 'reference',
                       'referenceConfig': {'targetCollection': tgt}}]
            data = [{'id': 's1', 'ref': 'zzpe-t1'}]
            refs = resolve_page_references(cur, src, data, fields, export_branch='main')
        script = (
            "out = []\n"
            "for r in data:\n"
            "    ref = references.get('zzpetgt', {}).get(r.get('ref'))\n"
            "    out.append({'id': r['id'], 'refName': ref['name'] if ref else None})\n"
            "result = json.dumps(out, ensure_ascii=False)\n"
        )
        result_bytes, _, _ = run_export_script(script, data, fields, '测试页', 'json', references=refs)
        assert json.loads(result_bytes) == [{'id': 's1', 'refName': '香蕉'}]
    finally:
        _cleanup([src, tgt])


def test_run_export_script_references_defaults_empty():
    """未传 references 时脚本内 references 为空字典（向后兼容，不报 NameError）。"""
    script = (
        "result = json.dumps({'hasRefs': bool(references), 'n': len(references)})\n"
    )
    result_bytes, _, _ = run_export_script(script, [], [], '测试页', 'json')
    assert json.loads(result_bytes) == {'hasRefs': False, 'n': 0}


def test_execute_menu_export_resolves_cross_page_reference():
    """端到端走 execute_menu_export：菜单只含 src 页，被引用的 tgt 页未在菜单内，
    菜单脚本仍能借 references join 到 tgt 数据。覆盖 menu_export 的接线缝。"""
    src, tgt = 'zzmesrc', 'zzmetgt'
    menu_id, script_id = 'zzme-menu', 'zzme-script'
    script = (
        "out = []\n"
        "for t in menu_data:\n"
        "    for r in t['records']:\n"
        "        ref = references.get('zzmetgt', {}).get(r.get('ref'))\n"
        "        out.append({'id': r['id'], 'refName': ref['name'] if ref else None})\n"
        "result = json.dumps(out, ensure_ascii=False)\n"
    )
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, src, [{'fieldName': 'ref', 'controlType': 'reference',
                                   'referenceConfig': {'targetCollection': tgt}}])
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, src, 'zzme-s1', {'ref': 'zzme-t1'})
            _ins(cur, tgt, 'zzme-t1', {'name': '苹果'})
            cur.execute("DELETE FROM export_scripts WHERE id=%s", (script_id,))
            # 菜单级脚本绑定到菜单（bound_menu_id），菜单导出无需指定脚本
            cur.execute("INSERT INTO export_scripts (id,name,script,output_format,scope,bound_menu_id) "
                        "VALUES (%s,%s,%s,'json','menu',%s)", (script_id, 'ref-join', script, menu_id))
            cur.execute("DELETE FROM menus WHERE id=%s", (menu_id,))
            cur.execute("INSERT INTO menus (id,name,page_id) VALUES (%s,%s,%s)",
                        (menu_id, 'zzMenu', f'page-{src}'))
            conn.commit()
        with get_db() as conn:
            zip_bytes, fname, errors = execute_menu_export(conn, [menu_id], None, 'main', role='admin')
        assert zip_bytes is not None, f'导出失败：{errors}'
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        # 单文件输出，路径形如 zzMenu/zzMenu.json
        payload = json.loads(zf.read(names[0]))
        assert payload == [{'id': 'zzme-s1', 'refName': '苹果'}], payload
    finally:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM menus WHERE id=%s", (menu_id,))
            cur.execute("DELETE FROM export_scripts WHERE id=%s", (script_id,))
            conn.commit()
        _cleanup([src, tgt])


def test_execute_menu_export_page_scope_resolves_reference():
    """端到端走 execute_menu_export 的「表级(page)脚本」分支：菜单导出时逐页跑页面级脚本，
    也应注入 references（修复前该分支用 run_export_script 不传 references → NameError）。"""
    src, tgt = 'zzmpsrc', 'zzmptgt'
    menu_id, script_id = 'zzmp-menu', 'zzmp-script'
    # 页面级写法：直接用 data + references（非 menu_data）
    script = (
        "out = []\n"
        "for r in data:\n"
        "    ref = references.get('zzmptgt', {}).get(r.get('ref'))\n"
        "    out.append({'id': r['id'], 'refName': ref['name'] if ref else None})\n"
        "result = json.dumps(out, ensure_ascii=False)\n"
    )
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, src, [{'fieldName': 'ref', 'controlType': 'reference',
                                   'referenceConfig': {'targetCollection': tgt}}])
            _seed_page(cur, tgt, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, src, 'zzmp-s1', {'ref': 'zzmp-t1'})
            _ins(cur, tgt, 'zzmp-t1', {'name': '橙子'})
            cur.execute("DELETE FROM export_scripts WHERE id=%s", (script_id,))
            # 绑定驱动：脚本绑定到该 collection（bound_collection），菜单导出无需指定脚本
            cur.execute("INSERT INTO export_scripts (id,name,script,output_format,scope,bound_collection) "
                        "VALUES (%s,%s,%s,'json','page',%s)", (script_id, 'ref-join-page', script, src))
            cur.execute("DELETE FROM menus WHERE id=%s", (menu_id,))
            cur.execute("INSERT INTO menus (id,name,page_id) VALUES (%s,%s,%s)",
                        (menu_id, 'zzMenuP', f'page-{src}'))
            conn.commit()
        with get_db() as conn:
            zip_bytes, fname, errors = execute_menu_export(conn, [menu_id], None, 'main', role='admin')
        assert zip_bytes is not None, f'导出失败：{errors}'
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        payload = json.loads(zf.read(zf.namelist()[0]))
        assert payload == [{'id': 'zzmp-s1', 'refName': '橙子'}], payload
    finally:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM menus WHERE id=%s", (menu_id,))
            cur.execute("DELETE FROM export_scripts WHERE id=%s", (script_id,))
            conn.commit()
        _cleanup([src, tgt])


def test_execute_menu_export_skips_unbound_pages():
    """绑定驱动菜单导出：父菜单下两页，一页绑定脚本→导出，一页无绑定→跳过并计入 errors。"""
    bound_c, unbound_c = 'zzmebound', 'zzmeunbound'
    parent, c1, c2, sid = 'zzme-parent', 'zzme-c1', 'zzme-c2', 'zzme-bscript'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, bound_c, [{'fieldName': 'name', 'controlType': 'text'}])
            _seed_page(cur, unbound_c, [{'fieldName': 'name', 'controlType': 'text'}])
            _ins(cur, bound_c, 'b1', {'name': 'A'})
            _ins(cur, unbound_c, 'u1', {'name': 'B'})
            cur.execute("DELETE FROM export_scripts WHERE id=%s", (sid,))
            cur.execute("INSERT INTO export_scripts (id,name,script,output_format,scope,bound_collection) "
                        "VALUES (%s,%s,%s,'json','page',%s)",
                        (sid, 'bound', "result = json.dumps([r['id'] for r in data])", bound_c))
            for mid in (parent, c1, c2):
                cur.execute("DELETE FROM menus WHERE id=%s", (mid,))
            cur.execute("INSERT INTO menus (id,name) VALUES (%s,%s)", (parent, 'zz父菜单'))
            cur.execute("INSERT INTO menus (id,name,page_id,parent_id) VALUES (%s,%s,%s,%s)",
                        (c1, '已绑定页', f'page-{bound_c}', parent))
            cur.execute("INSERT INTO menus (id,name,page_id,parent_id) VALUES (%s,%s,%s,%s)",
                        (c2, '未绑定页', f'page-{unbound_c}', parent))
            conn.commit()
        with get_db() as conn:
            zip_bytes, fname, errors = execute_menu_export(conn, [parent], None, 'main', role='admin')
        # 绑定页产出文件
        assert zip_bytes is not None, f'导出失败：{errors}'
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        assert len(names) == 1, names
        assert json.loads(zf.read(names[0])) == ['b1']
        # 未绑定页被跳过并记入 errors 摘要（页名取自 page_configs，即 collection 名）
        assert any('已跳过' in e and unbound_c in e for e in errors), errors
    finally:
        with get_db() as conn:
            cur = conn.cursor()
            for mid in (parent, c1, c2):
                cur.execute("DELETE FROM menus WHERE id=%s", (mid,))
            cur.execute("DELETE FROM export_scripts WHERE id=%s", (sid,))
            conn.commit()
        _cleanup([bound_c, unbound_c])
