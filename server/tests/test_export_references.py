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
from utils.export_references import resolve_references
from utils.script_runner import run_menu_export_script
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
            cur.execute("INSERT INTO export_scripts (id,name,script,output_format,scope) "
                        "VALUES (%s,%s,%s,'json','menu')", (script_id, 'ref-join', script))
            cur.execute("DELETE FROM menus WHERE id=%s", (menu_id,))
            cur.execute("INSERT INTO menus (id,name,page_id,export_script_id) VALUES (%s,%s,%s,%s)",
                        (menu_id, 'zzMenu', f'page-{src}', script_id))
            conn.commit()
        with get_db() as conn:
            zip_bytes, fname, errors = execute_menu_export(conn, [menu_id], None, 'main')
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
