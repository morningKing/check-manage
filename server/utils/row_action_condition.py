"""行动作可见性条件求值（后端半边）。

同一个 condition 对象会在前端 (src/utils/rowActionCondition.ts) 求值一次决定
按钮显不显示，在这里求值一次决定准不准执行。两边共读
server/tests/fixtures/row_action_conditions.json 这份夹具，防止语义漂移。

比较一律走文本：dynamic_data 的值来自 JSONB，同一个字段可能是字符串也可能是
数字/布尔，按文本比较才能和前端 String(v) 的结果对齐。
"""

_OPERATORS = ('eq', 'ne', 'in', 'notIn', 'empty', 'notEmpty')


def _as_text(v):
    """与前端 asText() 保持一致：None/缺失 -> ''，布尔 -> 'true'/'false'。

    浮点数要额外归一：JSONB 里的 3.0 到 Python 是 float(3.0)（str() 得 '3.0'），
    到浏览器是 number 3（String() 得 '3'）。不归一的话，同一条数据前端判定
    「满足条件」而后端判定「不满足」，用户会看到按钮却点不动。
    """
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def evaluate(condition, data):
    """条件是否满足。空条件 / 无 field 视为「总是通过」；未知算子失败关闭。"""
    if not condition:
        return True
    field = condition.get('field')
    if not field:
        return True
    op = condition.get('operator')
    if op not in _OPERATORS:
        return False

    actual = _as_text((data or {}).get(field))
    expected = condition.get('value')

    if op == 'empty':
        return actual == ''
    if op == 'notEmpty':
        return actual != ''
    if op == 'eq':
        return actual == _as_text(expected)
    if op == 'ne':
        return actual != _as_text(expected)
    if op in ('in', 'notIn'):
        if not isinstance(expected, list):
            return False
        hit = actual in [_as_text(x) for x in expected]
        return hit if op == 'in' else not hit
    return False
