"""dynamic_data.search_text —— 关键字搜索的预计算列。

配合 pg_trgm GIN 索引，把原本对每个"直接可搜索"字段做 data->>field ILIKE
的多条件全表扫描，折叠成一次 search_text ILIKE，使千万级数据下的关键字
搜索能走 GIN trigram 索引而不是逐行扫描每个候选字段。

只拼接与 routes/dynamic.py::build_keyword_conditions 语义完全一致的字段
类型，保证搜索结果集不变；relation/reference/quoteSelect 字段不在此列
（它们本来就是通过 JOIN/EXISTS 查询目标记录，不属于本行自身的文本）。
"""

DIRECT_SEARCHABLE_TYPES = {
    'text', 'textarea', 'markdown', 'number', 'autoSequence',
    'select', 'radio', 'date', 'datetime', 'autoTimestamp', 'compositeText',
}


def compute_search_text(data, fields):
    """拼接 data 中所有直接可搜索字段的值，返回供写入 search_text 列的字符串。"""
    parts = []
    for field in (fields or []):
        if field.get('controlType', 'text') not in DIRECT_SEARCHABLE_TYPES:
            continue
        value = (data or {}).get(field.get('fieldName'))
        if value is None or value == '':
            continue
        parts.append(str(value))
    return ' '.join(parts)
