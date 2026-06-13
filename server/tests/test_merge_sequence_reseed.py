"""Task 8: 分支合并后必须重播种目标分支的 autoSequence 计数器，防止合并重号。

合并会把源分支记录（携带其 autoSequence 编号，如 IC-080）直接 INSERT 进目标分支
'main'，但目标分支的 dynamic_sequences 计数器不会被推进。若不重播种，合并后第一次
create_item 仍从陈旧计数（如 5）开始分配 → 立刻撞上已合并记录的编号。

本测试以集成方式直接调用真实合并函数（merge_project_version /
merge_project_version_detailed），构造最小 DB 状态：
  - 一个 project 菜单 + 一个 data 子菜单 → page_configs（含 autoSequence 字段）
  - 源版本 project_versions 行（version_type='branch'），其 branch 数据在
    dynamic_data 中以 branch_id=version_id 存储，携带 IC-080
  - 目标分支 'main' 上一个陈旧的 dynamic_sequences 行（current_value=5）
然后断言：合并后 main 计数器 >= 80，且 allocate_sequence 返回非撞号的 IC-081。
"""
import psycopg2.extras
from db import get_db
from utils.sequences import allocate_sequence
from utils.project_version import (
    merge_project_version,
    merge_project_version_detailed,
)


PROJECT_MENU_ID = 'menu-zzmergeproj'
DATA_MENU_ID = 'menu-zzmergedata'
COLL = 'zzmergeseq'
PAGE_ID = f'page-{COLL}'
SEQ_FIELD = 'code'
PREFIX = 'IC-'


def _cleanup(cur):
    cur.execute("DELETE FROM merge_backups WHERE collection=%s", (COLL,))
    cur.execute("DELETE FROM merge_records WHERE project_menu_id=%s", (PROJECT_MENU_ID,))
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (COLL,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (COLL,))
    cur.execute("DELETE FROM project_versions WHERE project_menu_id=%s", (PROJECT_MENU_ID,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (PAGE_ID,))
    cur.execute("DELETE FROM menus WHERE id IN (%s,%s)", (DATA_MENU_ID, PROJECT_MENU_ID))


def _setup(cur, version_id, merged_code='IC-080', stale_counter=5):
    """构建最小合并场景，返回 None（直接写库）。"""
    _cleanup(cur)

    # 项目菜单 + 数据子菜单
    cur.execute(
        'INSERT INTO menus (id, name, menu_type, "order") VALUES (%s,%s,%s,%s)',
        (PROJECT_MENU_ID, '合并测试项目', 'project', 900),
    )
    cur.execute(
        'INSERT INTO menus (id, name, page_id, parent_id, menu_type, "order") '
        'VALUES (%s,%s,%s,%s,%s,%s)',
        (DATA_MENU_ID, '合并测试数据', PAGE_ID, PROJECT_MENU_ID, 'data', 1),
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

    # 源版本（分支类型）
    cur.execute(
        'INSERT INTO project_versions '
        '(id, project_menu_id, name, version_type, status, created_by) '
        'VALUES (%s,%s,%s,%s,%s,%s)',
        (version_id, PROJECT_MENU_ID, '功能分支', 'branch', 'active', 'tester'),
    )

    # 源分支数据：branch_id = version_id，携带较大的 autoSequence 编号
    cur.execute(
        "INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,%s)",
        ('rec-merged', COLL, psycopg2.extras.Json({SEQ_FIELD: merged_code}), version_id),
    )

    # 目标分支 'main' 上一个陈旧计数器（远小于已合并编号）
    cur.execute(
        "INSERT INTO dynamic_sequences (collection, branch_id, field_name, current_value) "
        "VALUES (%s,'main',%s,%s)",
        (COLL, SEQ_FIELD, stale_counter),
    )


def test_merge_reseeds_target_branch_counter():
    """merge_project_version（strategy=theirs）后，main 计数器被推进到 >=80，
    下一次分配为 IC-081，不与已合并的 IC-080 撞号。"""
    version_id = 'pv-zzmerge1'
    with get_db() as conn:
        cur = conn.cursor()
        _setup(cur, version_id, merged_code='IC-080', stale_counter=5)
        conn.commit()

    result = merge_project_version(
        version_id=version_id,
        target_branch='main',
        strategy='theirs',
        merged_by='tester',
        user_id='user-tester',
        project_menu_id=PROJECT_MENU_ID,
    )
    assert result['success'] is True

    with get_db() as conn:
        cur = conn.cursor()
        # 合并把记录复制进了 main
        cur.execute(
            "SELECT data->>%s FROM dynamic_data WHERE collection=%s AND id='rec-merged' AND branch_id='main'",
            (SEQ_FIELD, COLL),
        )
        row = cur.fetchone()
        assert row is not None and row[0] == 'IC-080'

        # 计数器被重播种到 >= 80
        cur.execute(
            "SELECT current_value FROM dynamic_sequences "
            "WHERE collection=%s AND branch_id='main' AND field_name=%s",
            (COLL, SEQ_FIELD),
        )
        counter = cur.fetchone()[0]
        assert counter >= 80, f'merge 后 main 计数器应 >=80，实际 {counter}（未重播种 → 会重号）'

        # 下一次分配不撞号
        nxt = allocate_sequence(cur, COLL, 'main', SEQ_FIELD, PREFIX, 3, count=1)[0]
        conn.commit()
        assert nxt == 'IC-081', f'下一编号应为 IC-081，实际 {nxt}'

        _cleanup(cur)
        conn.commit()


def test_merge_detailed_reseeds_target_branch_counter():
    """merge_project_version_detailed（按记录决策）后同样重播种 main 计数器。"""
    version_id = 'pv-zzmerge2'
    with get_db() as conn:
        cur = conn.cursor()
        _setup(cur, version_id, merged_code='IC-080', stale_counter=5)
        conn.commit()

    result = merge_project_version_detailed(
        version_id=version_id,
        target_branch='main',
        collection_decisions=[{
            'collection': COLL,
            'added': ['rec-merged'],
            'removed': [],
            'modified': [],
        }],
        merged_by='tester',
        user_id='user-tester',
        project_menu_id=PROJECT_MENU_ID,
    )
    assert result['success'] is True

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT data->>%s FROM dynamic_data WHERE collection=%s AND id='rec-merged' AND branch_id='main'",
            (SEQ_FIELD, COLL),
        )
        row = cur.fetchone()
        assert row is not None and row[0] == 'IC-080'

        cur.execute(
            "SELECT current_value FROM dynamic_sequences "
            "WHERE collection=%s AND branch_id='main' AND field_name=%s",
            (COLL, SEQ_FIELD),
        )
        counter = cur.fetchone()[0]
        assert counter >= 80, f'detailed merge 后 main 计数器应 >=80，实际 {counter}'

        nxt = allocate_sequence(cur, COLL, 'main', SEQ_FIELD, PREFIX, 3, count=1)[0]
        conn.commit()
        assert nxt == 'IC-081', f'下一编号应为 IC-081，实际 {nxt}'

        _cleanup(cur)
        conn.commit()
