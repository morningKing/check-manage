# NOTE: copied from server/utils/mongo_query.py — keep the two in sync.
"""MongoDB-style query syntax to PostgreSQL JSONB WHERE clause translator.

Supports:
- Implicit equality: {"field": "value"}
- Comparison: $eq, $ne, $gt, $gte, $lt, $lte
- Array: $in, $nin
- String: $regex, $like
- Element: $exists, $size
- Logical: $and, $or, $not, $nor

Examples:
    {"name": "test"}
        → data->>'name' = %s   params=['test']

    {"age": {"$gte": 18}}
        → (data->>'age')::numeric >= %s   params=[18]

    {"name": {"$regex": "test"}}
        → data->>'name' ~* %s   params=['test']

    {"$or": [{"name": "a"}, {"name": "b"}]}
        → (data->>'name' = %s OR data->>'name' = %s)   params=['a', 'b']

    {"status": {"$in": ["active", "pending"]}}
        → data->>'status' IN (%s, %s)   params=['active', 'pending']
"""

import re

_FIELD_RE = re.compile(r'^[\w][\w.]*$', re.UNICODE)

COMPARISON_OPS = {
    '$eq': '=',
    '$ne': '!=',
    '$gt': '>',
    '$gte': '>=',
    '$lt': '<',
    '$lte': '<=',
}


class MongoQueryError(Exception):
    """Invalid MongoDB query syntax."""
    pass


def _validate_field(name):
    if not name or len(name) > 200:
        raise MongoQueryError(f"Invalid field name: {name}")
    if not _FIELD_RE.match(name):
        raise MongoQueryError(f"Invalid field name: {name}")


def _esc(name):
    """Escape single quotes in field name for safe JSONB accessor."""
    return name.replace("'", "''")


def _text_acc(field):
    """JSONB text accessor: data->>'field' or data->'a'->>'b' for nested."""
    parts = field.split('.')
    if len(parts) == 1:
        return f"data->>'{_esc(parts[0])}'"
    path = "data"
    for p in parts[:-1]:
        path += f"->'{_esc(p)}'"
    path += f"->>'{_esc(parts[-1])}'"
    return path


def _json_acc(field):
    """JSONB accessor (returns jsonb): data->'field'."""
    parts = field.split('.')
    if len(parts) == 1:
        return f"data->'{_esc(parts[0])}'"
    path = "data"
    for p in parts:
        path += f"->'{_esc(p)}'"
    return path


def _translate_condition(field, op, value, params):
    """Translate a single operator condition to SQL."""
    _validate_field(field)
    ta = _text_acc(field)
    ja = _json_acc(field)

    if op in COMPARISON_OPS:
        sql_op = COMPARISON_OPS[op]
        if value is None:
            if op == '$eq':
                return f"({ja} IS NULL OR {ta} = 'null')"
            elif op == '$ne':
                return f"({ja} IS NOT NULL AND {ta} != 'null')"
            else:
                raise MongoQueryError(f"Cannot use {op} with null")
        elif isinstance(value, bool):
            params.append('true' if value else 'false')
            return f"{ja}::text {sql_op} %s"
        elif isinstance(value, (int, float)):
            params.append(value)
            return f"({ta})::numeric {sql_op} %s"
        else:
            params.append(str(value))
            return f"{ta} {sql_op} %s"

    elif op == '$in':
        if not isinstance(value, list):
            raise MongoQueryError("$in requires an array")
        if not value:
            return "FALSE"
        ph = ', '.join(['%s'] * len(value))
        params.extend(str(v) for v in value)
        return f"{ta} IN ({ph})"

    elif op == '$nin':
        if not isinstance(value, list):
            raise MongoQueryError("$nin requires an array")
        if not value:
            return "TRUE"
        ph = ', '.join(['%s'] * len(value))
        params.extend(str(v) for v in value)
        return f"({ta} NOT IN ({ph}) OR {ja} IS NULL)"

    elif op == '$regex':
        if not isinstance(value, str):
            raise MongoQueryError("$regex requires a string")
        params.append(value)
        return f"{ta} ~* %s"

    elif op == '$like':
        if not isinstance(value, str):
            raise MongoQueryError("$like requires a string")
        params.append(f"%{value}%")
        return f"{ta} ILIKE %s"

    elif op == '$exists':
        if value:
            return f"{ja} IS NOT NULL"
        else:
            return f"{ja} IS NULL"

    elif op == '$not':
        if isinstance(value, dict):
            inner = _translate_field(field, value, params)
            return f"NOT ({inner})"
        else:
            raise MongoQueryError("$not requires an object")

    elif op == '$size':
        if not isinstance(value, int):
            raise MongoQueryError("$size requires an integer")
        params.append(value)
        return f"jsonb_array_length({ja}) = %s"

    else:
        raise MongoQueryError(f"Unknown operator: {op}")


def _translate_field(field, expr, params):
    """Translate a field expression: plain value or {$op: val, ...}."""
    if not isinstance(expr, dict):
        return _translate_condition(field, '$eq', expr, params)

    has_ops = any(k.startswith('$') for k in expr)
    if not has_ops:
        return _translate_condition(field, '$eq', expr, params)

    conds = []
    for op, val in expr.items():
        if not op.startswith('$'):
            raise MongoQueryError(
                f"Cannot mix operators and fields in '{field}'"
            )
        conds.append(_translate_condition(field, op, val, params))

    if len(conds) == 1:
        return conds[0]
    return '(' + ' AND '.join(conds) + ')'


def translate(query):
    """Translate a MongoDB-style query dict to (sql_where, params).

    The returned sql_where does NOT include the 'WHERE' keyword.
    Returns ('TRUE', []) for empty/None query.
    """
    if not query:
        return 'TRUE', []

    if not isinstance(query, dict):
        raise MongoQueryError("Query must be a JSON object")

    params = []
    conds = []

    for key, value in query.items():
        if key == '$and':
            if not isinstance(value, list):
                raise MongoQueryError("$and requires an array")
            subs = []
            for sub in value:
                sql, p = translate(sub)
                subs.append(sql)
                params.extend(p)
            if subs:
                conds.append('(' + ' AND '.join(subs) + ')')

        elif key == '$or':
            if not isinstance(value, list):
                raise MongoQueryError("$or requires an array")
            subs = []
            for sub in value:
                sql, p = translate(sub)
                subs.append(sql)
                params.extend(p)
            if subs:
                conds.append('(' + ' OR '.join(subs) + ')')

        elif key == '$nor':
            if not isinstance(value, list):
                raise MongoQueryError("$nor requires an array")
            subs = []
            for sub in value:
                sql, p = translate(sub)
                subs.append(sql)
                params.extend(p)
            if subs:
                conds.append('NOT (' + ' OR '.join(subs) + ')')

        elif key == '$not':
            if not isinstance(value, dict):
                raise MongoQueryError("$not requires an object")
            sql, p = translate(value)
            conds.append(f'NOT ({sql})')
            params.extend(p)

        elif key.startswith('$'):
            raise MongoQueryError(f"Unknown top-level operator: {key}")

        else:
            conds.append(_translate_field(key, value, params))

    if not conds:
        return 'TRUE', []

    return ' AND '.join(conds), params


def remap_labels(query, fields):
    """Remap field labels in query keys to their fieldNames.

    Allows users to write queries using display labels (e.g. "用例ID")
    instead of internal fieldNames (e.g. "caseid").
    Keys that are already valid fieldNames or operators ($...) are kept as-is.
    """
    if not query or not fields:
        return query

    label_map = {}
    field_names = set()
    for f in fields:
        fn = f.get('fieldName', '')
        lb = f.get('label', '')
        if fn:
            field_names.add(fn)
            if lb and lb != fn:
                label_map[lb] = fn

    if not label_map:
        return query

    return _remap_recursive(query, label_map, field_names)


def _remap_recursive(obj, label_map, field_names):
    if isinstance(obj, dict):
        result = {}
        for key, val in obj.items():
            if key.startswith('$'):
                # Logical operators: recurse into their values
                if isinstance(val, list):
                    result[key] = [_remap_recursive(v, label_map, field_names) for v in val]
                elif isinstance(val, dict):
                    result[key] = _remap_recursive(val, label_map, field_names)
                else:
                    result[key] = val
            else:
                # Field key: remap if it's a label
                mapped_key = label_map.get(key, key)
                result[mapped_key] = val
        return result
    return obj
