# 数据页菜单名称全局唯一 — Design Spec

## Background

MCP 工具（`query_collection`/`export_collection_excel`）目前必须传精确的
`collection` 标识，AI 需要先调用 `list_collections` 拿到"菜单名 → collection"
的映射才能定位数据页。根源是 `menus.name`（数据类型菜单的显示名，MCP 里叫
`label`）当前完全没有唯一性保证——`server/routes/menus.py` 的
`create_menu`/`update_menu` 对名称零查重，`server/init_db.py` 的 `menus` 表
定义（第 13-22 行）也没有唯一约束。

本次目标：让 `menu_type='data'` 的菜单名称在系统内全局唯一，为后续"MCP 工具
直接按名称定位数据页"打基础（那部分是后续工作，本次只做唯一性保证本身）。

## Decisions (confirmed with user)

1. **强制方式**：数据库唯一索引（真正的机制保证，杜绝并发竞态）+ 接口层
   提交前查重（给出友好错误提示，不让用户看到原始 DB 异常）。
2. **范围**：只对 `menu_type = 'data'` 的菜单名称做唯一约束（工作空间/项目
   菜单不受影响），用 Postgres 部分唯一索引
   `CREATE UNIQUE INDEX ... ON menus(name) WHERE menu_type = 'data'`。
3. **迁移安全**：`init_db.py` 每次都会执行这段 DDL。如果某个环境的数据库
   已经存在重名的数据类型菜单（开发库目前没有，已用真实查询确认），直接
   `CREATE UNIQUE INDEX` 会失败并可能中断整个初始化流程。改为：先检测是否
   有重名，有则打印警告列出具体重名的菜单（名称 + id 列表）并跳过建索引，
   不报错、不中断；没有重名则正常建索引。管理员手动改名后重跑 `init_db.py`
   即可补上索引（`CREATE UNIQUE INDEX IF NOT EXISTS` 天然幂等）。

## Scope

- `server/init_db.py`：新增 DDL（唯一索引 + 前置检测）。
- `server/routes/menus.py`：`create_menu`/`update_menu` 新增提交前查重 +
  唯一约束冲突（数据库兜底）的友好错误转换。
- **前端不需要改动**：`src/views/admin/MenuManager.vue` 的提交错误处理
  （`src/views/admin/MenuManager.vue:759-761`）已经是
  `ElMessage.error(error.response?.data?.error || ...)`，后端返回的 400
  `{"error": "..."}` 会被直接展示，不需要新增任何前端逻辑。
- MCP 工具改成直接按名称定位数据页——不在本次范围内，是后续工作。

## Architecture

### `server/init_db.py`

不放进顶部那个无条件执行的 `DDL = """..."""` 大字符串（唯一索引创建可能会
失败，不能跟其它必定成功的建表语句混在一起）。放进已有的、逐条判断执行的
"迁移"代码区（跟 `webhook_logs` 那种 `information_schema` 判断式迁移同一
风格，文件里已有先例），新增一段：

```python
# 迁移：数据类型菜单名称全局唯一（为 MCP 按名称定位数据页做准备）
cur.execute("""
    SELECT indexname FROM pg_indexes
    WHERE tablename = 'menus' AND indexname = 'idx_menus_data_name_unique'
""")
if not cur.fetchone():
    cur.execute("""
        SELECT name, array_agg(id) FROM menus
        WHERE menu_type = 'data' GROUP BY name HAVING COUNT(*) > 1
    """)
    dup_rows = cur.fetchall()
    if dup_rows:
        print(f"[WARN] 发现 {len(dup_rows)} 组重名数据页菜单，跳过唯一索引创建：")
        for name, ids in dup_rows:
            print(f"  - 「{name}」: {ids}")
        print("  请手动改名消除重复后重跑 init_db.py 补上唯一索引。")
    else:
        cur.execute("""
            CREATE UNIQUE INDEX idx_menus_data_name_unique
            ON menus(name) WHERE menu_type = 'data'
        """)
        print("Created idx_menus_data_name_unique (数据页菜单名称全局唯一).")
```

（用 `pg_indexes` 判断索引是否已存在，而不是 `CREATE UNIQUE INDEX IF NOT
EXISTS`，是因为重名时要跳过整个创建动作走警告分支，不能直接无条件
`IF NOT EXISTS`——两者行为不同：`IF NOT EXISTS` 在索引不存在时仍会尝试创建
并在重名时报错，这里需要自己先判断存在性再决定是否尝试。)

### `server/routes/menus.py`

`create_menu`：在 `_validate_menu_type` 通过之后、真正 `INSERT` 之前，
`menu_type == 'data'` 时查重：

```python
if menu_type == 'data':
    name = body.get('name')
    cur.execute(
        "SELECT id FROM menus WHERE menu_type = 'data' AND name = %s",
        (name,)
    )
    if cur.fetchone():
        return jsonify({"error": f"数据页名称「{name}」已被占用，请换一个名称"}), 400
```

`update_menu`：在计算出最终 `menu_type`（`body.get('menuType', current_menu_type)`）
之后、真正 `UPDATE` 之前，同样查重，**排除自身**：

```python
if menu_type == 'data':
    name = body.get('name')
    cur.execute(
        "SELECT id FROM menus WHERE menu_type = 'data' AND name = %s AND id != %s",
        (name, menu_id)
    )
    if cur.fetchone():
        return jsonify({"error": f"数据页名称「{name}」已被占用，请换一个名称"}), 400
```

**数据库唯一索引冲突兜底**（并发下两个请求同时查重都通过、同时 INSERT/UPDATE
的极端情况）：把 `create_menu`/`update_menu` 里原本裸露的 `with get_db() as
conn: ... INSERT/UPDATE ...` 包一层 `try/except psycopg2.errors.
UniqueViolation`，转成同样格式的友好 400（而不是让原始 psycopg2 异常冒泡成
500）。`get_db()`（`server/db.py:18-26`）在 `with` 块内抛异常时自己会先
`rollback()` 再重新抛出，所以外层 try/except 能接到一个事务已经正确回滚过的
异常，直接返回 jsonify 即可，不需要自己再手动 rollback。

文件顶部新增 `import psycopg2`。

## Testing

- **后端（pytest，扩展 `server/tests/test_routes_menus.py`，先看现有测试
  怎么组织再跟随其模式——如果该文件不存在，用这个仓库其它 routes 测试文件
  的真实-DB 集成测试惯例）**：
  - `create_menu`：两个 `menu_type='data'` 的菜单同名 → 第二个返回 400，
    错误信息包含名称；不同 `menu_type`（如两个都叫"设置"的 workspace/
    project 菜单）同名不受影响，能正常创建。
  - `update_menu`：把菜单改名成另一个已存在的数据页菜单名 → 400；改成
    自己原来的名字（无变化）→ 正常成功，不误报"已占用"。
  - 并发/DB 兜底：直接绕过应用层查重、手动构造两次几乎同时的 INSERT（或
    直接测 DB 唯一索引本身：两次 `INSERT INTO menus` 同名同
    `menu_type='data'`，第二次应该抛 `UniqueViolation`）验证索引真的存在
    且生效。
- **`server/init_db.py` 迁移逻辑**：手动验证（不一定需要自动化测试，
  跟随这个文件里其它迁移代码块的既有覆盖程度）——在开发库上重跑
  `python init_db.py`，确认幂等（索引已存在时不报错、不重复创建），并用
  真实查询确认索引确实创建成功（`SELECT indexname FROM pg_indexes WHERE
  indexname = 'idx_menus_data_name_unique'`）。
