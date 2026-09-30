"""字段级索引的资格判定 + 与 page_configs.fields 的同步逻辑。

管理员在字段配置里把某个字段标记为"加速筛选/排序"后（FieldConfig.indexed），
这里负责把期望状态（哪些字段该有索引）同步进 field_indexes 表；真正的
CREATE/DROP INDEX CONCURRENTLY 由 utils/field_index_scheduler.py 的后台
任务异步执行——建索引在千万级表上可能耗时很久，不能卡在保存页面配置的
请求里。

实测（EXPLAIN ANALYZE 对照）：psycopg2 的 %s 参数替换是客户端侧 mogrify，
发给 Postgres 的是字面量 SQL 文本，所以 data->>%s 这类查询本来就能被
(data->>'field') 表达式索引命中——不需要改写 routes/dynamic.py 或
utils/mongo_query.py 里的查询构造，只要索引真的建出来即可生效。
"""
import hashlib

# 值是标量、适合做等值/范围/排序表达式索引的控件类型。
# textarea/markdown（长文本）、relation/reference/quoteSelect（存的是别的记录的
# ID，语义上要匹配的是目标记录）、file/image（对象数组）都不适合。
INDEXABLE_TYPES = {
    'text', 'number', 'select', 'radio', 'date', 'datetime',
    'autoSequence', 'autoTimestamp', 'compositeText', 'statusBadge', 'checkbox',
}


def sql_literal(value):
    """把字符串安全地拼成 SQL 字面量（DDL 里的表达式索引字段名不能走 bind 参数）。"""
    return "'" + str(value).replace("'", "''") + "'"


def _hash_key(collection, field_name):
    raw = f'{collection}\x00{field_name}'.encode('utf-8')
    return hashlib.md5(raw).hexdigest()[:16]


def index_name_for(collection, field_name):
    """确定性生成合法的 Postgres 索引标识符（哈希后固定长度，规避字符集/长度限制）。"""
    return f'idx_dyn_fld_{_hash_key(collection, field_name)}'


def indexed_field_names(fields):
    """返回该页面字段配置里，标记了 indexed 且控件类型可索引的字段名集合。"""
    return {
        f['fieldName']
        for f in (fields or [])
        if f.get('indexed') and f.get('controlType') in INDEXABLE_TYPES and f.get('fieldName')
    }


def sync_field_indexes(cur, collection, fields):
    """把 field_indexes 表同步到当前字段配置期望的索引集合。

    新增需要索引的字段 -> 插入一行 status='pending'，等后台任务去建。
    不再需要索引的字段（取消勾选/字段被删）-> 标记 status='dropping'，
    后台任务会 DROP INDEX CONCURRENTLY 并删除这一行。
    已经在期望集合里的字段不动（不打断正在进行的 building/已完成的 ready）。
    """
    desired = indexed_field_names(fields)

    cur.execute(
        'SELECT field_name, status FROM field_indexes WHERE collection = %s',
        (collection,),
    )
    existing = {row[0]: row[1] for row in cur.fetchall()}

    for field_name in desired:
        if field_name not in existing:
            cur.execute(
                'INSERT INTO field_indexes (collection, field_name, index_name, status) '
                'VALUES (%s, %s, %s, %s)',
                (collection, field_name, index_name_for(collection, field_name), 'pending'),
            )

    for field_name, status in existing.items():
        if field_name not in desired and status != 'dropping':
            cur.execute(
                "UPDATE field_indexes SET status = 'dropping', error = NULL "
                'WHERE collection = %s AND field_name = %s',
                (collection, field_name),
            )


def mark_all_dropping(cur, collection):
    """页面配置整个被删除时，该 collection 下所有字段索引都要清理掉。"""
    cur.execute(
        "UPDATE field_indexes SET status = 'dropping', error = NULL "
        "WHERE collection = %s AND status != 'dropping'",
        (collection,),
    )
