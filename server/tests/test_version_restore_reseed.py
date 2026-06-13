"""Task 11: 从项目版本快照还原后，必须重播种当前分支的 autoSequence 计数器。

restore_from_project_version 会 DELETE 当前分支记录，再从 project_version_snapshots
把快照记录（携带其 autoSequence 编号，如 IC-090）INSERT 回当前分支（默认 main）。
但目标分支的 dynamic_sequences 计数器不会被推进。若不重播种，还原后第一次 create_item
仍从陈旧计数（如 5）开始分配 → 立刻撞上已还原记录的编号。

本测试以集成方式直接调用真实还原函数 restore_from_project_version，构造最小 DB 状态：
  - 一个 project 菜单 + 一个 data 子菜单 → page_configs（含 autoSequence 字段）
  - 一个 snapshot 版本（project_versions），其快照在 project_version_snapshots
    中携带 IC-090
  - 目标分支 'main' 上一个陈旧的 dynamic_sequences 行（current_value=5）
然后断言：还原后 main 计数器被推进到 >=90，且 allocate_sequence 返回非撞号的 IC-091。
"""
import psycopg2.extras
from db import get_db
from utils.sequences import allocate_sequence
from utils.project_version import restore_from_project_version


PROJECT_MENU_ID = 'menu-zzrestoreproj'
DATA_MENU_ID = 'menu-zzrestoredata'
COLL = 'zzrestoreseq'
PAGE_ID = f'page-{COLL}'
SEQ_FIELD = 'code'
PREFIX = 'IC-'
VERSION_ID = 'pv-zzrestore1'


def _cleanup(cur):
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (COLL,))
    cur.execute("DELETE FROM data_relations WHERE collection=%s", (COLL,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (COLL,))
    cur.execute("DELETE FROM project_version_snapshots WHERE version_id=%s", (VERSION_ID,))
    cur.execute("DELETE FROM project_version_relations WHERE version_id=%s", (VERSION_ID,))
    cur.execute("DELETE FROM project_versions WHERE project_menu_id=%s", (PROJECT_MENU_ID,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (PAGE_ID,))
    cur.execute("DELETE FROM menus WHERE id IN (%s,%s)", (DATA_MENU_ID, PROJECT_MENU_ID))
    cur.execute(
        "DELETE FROM user_current_project_branch WHERE project_menu_id=%s",
        (PROJECT_MENU_ID,),
    )


def _setup(cur, snapshot_code='IC-090', stale_counter=5):
    _cleanup(cur)

    # 项目菜单 + 数据子菜单
    cur.execute(
        'INSERT INTO menus (id, name, menu_type, "order") VALUES (%s,%s,%s,%s)',
        (PROJECT_MENU_ID, '还原测试项目', 'project', 901),
    )
    cur.execute(
        'INSERT INTO menus (id, name, page_id, parent_id, menu_type, "order") '
        'VALUES (%s,%s,%s,%s,%s,%s)',
        (DATA_MENU_ID, '还原测试数据', PAGE_ID, PROJECT_MENU_ID, 'data', 1),
    )

    # page_configs：含 autoSequence 字段
    fields = [{
        'fieldName': SEQ_FIELD, 'controlType': 'autoSequence',
        'sequenceConfig': {'prefix': PREFIX, 'max': 999, 'padding': 3},
        'isPrimaryKey': True,
    }]
    cur.execute(
        "INSERT INTO page_configs (id, name, fields) VALUES (%s,%s,%s)",
        (PAGE_ID, COLL, psycopg2.extras.Json(fields)),
    )

    # snapshot 版本
    cur.execute(
        'INSERT INTO project_versions '
        '(id, project_menu_id, name, version_type, status, created_by, records_count) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s)',
        (VERSION_ID, PROJECT_MENU_ID, '快照V1', 'snapshot', 'active', 'tester', 1),
    )

    # 快照记录：携带较大的 autoSequence 编号
    cur.execute(
        'INSERT INTO project_version_snapshots '
        '(version_id, collection, record_id, record_data) VALUES (%s,%s,%s,%s)',
        (VERSION_ID, COLL, 'rec-snap', psycopg2.extras.Json({SEQ_FIELD: snapshot_code})),
    )

    # 目标分支 'main' 上一个陈旧计数器（远小于快照编号）
    cur.execute(
        "INSERT INTO dynamic_sequences (collection, branch_id, field_name, current_value) "
        "VALUES (%s,'main',%s,%s)",
        (COLL, SEQ_FIELD, stale_counter),
    )


def test_restore_reseeds_current_branch_counter():
    """restore_from_project_version 后，main 计数器被推进到 >=90，
    下一次分配为 IC-091，不与已还原的 IC-090 撞号。"""
    with get_db() as conn:
        cur = conn.cursor()
        _setup(cur, snapshot_code='IC-090', stale_counter=5)
        conn.commit()

    result = restore_from_project_version(
        version_id=VERSION_ID,
        restored_by='tester',
        user_id='user-tester',
        project_menu_id=PROJECT_MENU_ID,
    )
    assert result['success'] is True

    with get_db() as conn:
        cur = conn.cursor()
        # 还原把快照记录写进了 main
        cur.execute(
            "SELECT data->>%s FROM dynamic_data WHERE collection=%s AND id='rec-snap' AND branch_id='main'",
            (SEQ_FIELD, COLL),
        )
        row = cur.fetchone()
        assert row is not None and row[0] == 'IC-090'

        # 计数器被重播种到 >= 90
        cur.execute(
            "SELECT current_value FROM dynamic_sequences "
            "WHERE collection=%s AND branch_id='main' AND field_name=%s",
            (COLL, SEQ_FIELD),
        )
        counter = cur.fetchone()[0]
        assert counter >= 90, f'还原后 main 计数器应 >=90，实际 {counter}（未重播种 → 会重号）'

        # 下一次分配不撞号
        nxt = allocate_sequence(cur, COLL, 'main', SEQ_FIELD, PREFIX, 3, count=1)[0]
        conn.commit()
        assert nxt == 'IC-091', f'下一编号应为 IC-091，实际 {nxt}'

        _cleanup(cur)
        conn.commit()
