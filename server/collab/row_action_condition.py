"""行动作可见性条件求值（后端半边）。

同一个 condition 对象会在前端 (src/utils/rowActionCondition.ts) 求值一次决定
按钮显不显示，在这里求值一次决定准不准执行。两边共读
server/tests/fixtures/row_action_conditions.json 这份夹具，防止语义漂移。

比较一律走文本：dynamic_data 的值来自 JSONB，同一个字段可能是字符串也可能是
数字/布尔，按文本比较才能和前端 String(v) 的结果对齐。
"""

from decimal import Decimal

_OPERATORS = ('eq', 'ne', 'in', 'notIn', 'empty', 'notEmpty')

# JS Number.prototype.toString() 的定点/科学计数法切换阈值：|v| < 1e-6 或
# |v| >= 1e21 时用科学计数法，中间一律定点小数——即使 Python 自己的 str()/repr()
# 切换阈值早得多（约 1e-5 / 1e16），也要按这个阈值来，否则两边对同一个值会给出
# 不同的文本（例如 1e16 前端是 '10000000000000000'，Python str() 却已经是
# '1e+16'）。
_SCI_LOW = Decimal('1e-6')
_SCI_HIGH = Decimal('1e21')


def _format_float(v):
    """把 float 渲染成与 JS `String(Number)` 完全一致的文本。

    Python 的 str()/repr() 切到科学计数法的阈值（约 <1e-5 或 >=1e16）比 JS
    （<1e-6 或 >=1e21）激进得多，直接复用会在这两段区间打架（如 1e16、1e-5）。
    做法：先用 repr(v) 拿到"最短可还原"的十进制数字串（Python 的 repr 本身就是
    最短往返表示，数字序列和 JS 引擎会选的数字序列是一致的，只是记法阈值不同），
    再按 JS 的阈值重新决定用定点还是科学计数法、以及指数的书写方式（不补零，
    如 'e-7' 而不是 Python 的 'e-07'）。
    """
    if v == 0:
        return '0'
    sign = '-' if v < 0 else ''
    _, digits, exponent = Decimal(repr(abs(v))).as_tuple()
    digits_str = ''.join(map(str, digits))
    trimmed = digits_str.rstrip('0') or '0'
    exponent += len(digits_str) - len(trimmed)
    digits_str = trimmed
    e10 = len(digits_str) - 1 + exponent  # 首位有效数字的十进制指数

    if -6 <= e10 < 21:
        point = e10 + 1  # 小数点前的数字位数
        if point <= 0:
            out = '0.' + '0' * (-point) + digits_str
        elif point >= len(digits_str):
            out = digits_str + '0' * (point - len(digits_str))
        else:
            out = digits_str[:point] + '.' + digits_str[point:]
    else:
        mantissa = digits_str[0] + ('.' + digits_str[1:] if len(digits_str) > 1 else '')
        out = f'{mantissa}e{"+" if e10 >= 0 else "-"}{abs(e10)}'
    return sign + out


def _as_text(v):
    """与前端 asText() 保持一致：None/缺失 -> ''，布尔 -> 'true'/'false'，
    数组/对象（非标量）-> ''。

    非标量归一：multiSelect/checkbox/file/image 等控件在 JSONB 里存的是数组，
    relation/reference/quoteSelect 干脆不落在这行的 data 里。Python 的
    str([...]) / str({...}) 与 JS 的 String([...])（对单元素数组会退化成元素
    本身，如 String(['a'])==='a'）/String({...})（'[object Object]'）在语义
    上都对不上、彼此也对不上，索性两边统一按"不是可比较的标量"处理成 ''——
    empty/notEmpty 依然按直觉工作（空数组=空），eq/in 这类精确比较则总是不
    命中，宁可按钮不出现也不能出现後却认错行。

    浮点数额外走 _format_float 归一：JSONB 里的 3.0 到 Python 是
    float(3.0)（str() 得 '3.0'），到浏览器是 number 3（String() 得 '3'）；
    极大/极小值还有科学计数法记法分叉（见 _format_float 的说明）。不归一的话，
    同一条数据前端判定「满足条件」而后端判定「不满足」，用户会看到按钮却点不动。

    整数同样要走这条路径，而不能停在 str(v)：JSONB 里没有小数点的数字，
    psycopg2 解析出来是 Python int（任意精度），但浏览器里的 JSON.parse 一律
    产出 float64 number——超过 2^53 就会丢精度、超过 1e21 就会切到科学计数法。
    直接 str(int) 会原样吐出任意精度整数（如 '1000000000000000000000' 或精确的
    '9007199254740993'），跟前端 String(Number(...)) 的结果（'1e+21' /
    '9007199254740992'）对不上，是与浮点数同一类"前端显示按钮、后端判不满足"
    的分叉。所以要先 float(v) 把整数也砸扁成 float64（模拟 JS 那次精度损失），
    再走同一套 _format_float。bool 是 int 的子类，必须在这条分支之前已经被
    上面的 isinstance(v, bool) 拦掉，否则 True 会被当成 1 处理。
    """
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (list, dict)):
        return ''
    if isinstance(v, (int, float)):
        return _format_float(float(v))
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
