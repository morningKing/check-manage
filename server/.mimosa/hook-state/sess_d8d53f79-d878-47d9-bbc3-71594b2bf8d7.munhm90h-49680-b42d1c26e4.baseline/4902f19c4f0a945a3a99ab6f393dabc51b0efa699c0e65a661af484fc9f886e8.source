"""
Seed test data for the "stable import id by primary key" fix (方向 C).

Creates two data pages under the existing 测试数据 project (menu-test-data):

  - pkmaster (主键主表): fields code(主键 isPrimaryKey) + title(显示字段)
  - pkref    (引用表):   fields refName + masterRef(reference -> pkmaster, displayField=title)

Use it to reproduce / verify the bug-and-fix end to end:
  1. Import master rows -> ids are pk-derived (contain "-pk-").
  2. Create a pkref row referencing one master.
  3. Delete that master row.
  4. Re-import the SAME master row (same code) -> reuses the SAME id.
  5. The pkref reference stays valid (label renders again).

Run:    cd server && python create_test_pk_reimport_data.py
Clean:  cd server && python create_test_pk_reimport_data.py --clean
"""
import sys
import json
import psycopg2
from config import DB_CONFIG

MASTER_FIELDS = [
    {"label": "编码", "fieldName": "code", "controlType": "text", "isPrimaryKey": True},
    {"label": "名称", "fieldName": "title", "controlType": "text"},
]
REF_FIELDS = [
    {"label": "名称", "fieldName": "refName", "controlType": "text"},
    {
        "label": "引用主表",
        "fieldName": "masterRef",
        "controlType": "reference",
        "referenceConfig": {
            "displayField": "title",
            "inheritFields": [],
            "targetCollection": "pkmaster",
        },
    },
]

PAGES = [
    ("page-pkmaster", "PK主表(测试)", MASTER_FIELDS),
    ("page-pkref", "PK引用表(测试)", REF_FIELDS),
]
MENUS = [
    # id, name, page_id, parent_id, order, path
    ("menu-pk-master", "PK主表(测试)", "page-pkmaster", "menu-test-data", 10, "/test/pkmaster"),
    ("menu-pk-ref", "PK引用表(测试)", "page-pkref", "menu-test-data", 11, "/test/pkref"),
]


def clean(cur):
    cur.execute("DELETE FROM dynamic_data WHERE collection IN ('pkmaster','pkref')")
    cur.execute("DELETE FROM menus WHERE id IN ('menu-pk-master','menu-pk-ref')")
    cur.execute("DELETE FROM page_configs WHERE id IN ('page-pkmaster','page-pkref')")
    print("cleaned: pages, menus, and dynamic_data for pkmaster/pkref")


def seed(cur):
    clean(cur)
    for pid, name, fields in PAGES:
        cur.execute(
            "INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)",
            (pid, name, json.dumps(fields, ensure_ascii=False)),
        )
    for mid, name, page_id, parent, order, path in MENUS:
        cur.execute(
            'INSERT INTO menus (id, name, page_id, parent_id, "order", path, roles, menu_type) '
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'data')",
            (mid, name, page_id, parent, order, path, json.dumps(["admin"])),
        )
    print("seeded pages: page-pkmaster, page-pkref")
    print("seeded menus: /test/pkmaster, /test/pkref (under 测试数据)")


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()
    if "--clean" in sys.argv:
        clean(cur)
    else:
        seed(cur)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
