"""会话大纲/接管页批指令污染修复（2026-11-16 缺陷）。

缺陷：批子会话的用户消息落的是拼装 prompt（开头无人值守系统规则样板），
大纲端点 _turn_preview 取 text 开头 → 大纲/接管页全是系统提示词，用户
原文被挤掉。修复两层：落库改存用户原文（batch_engine）+ 预览剥样板前缀
兜底存量行（_strip_batch_directive）。
"""
from unittest.mock import patch, MagicMock

from routes.ai_chat import _strip_batch_directive, _turn_preview
from utils.batch_engine import _BATCH_DIRECTIVE

_DIRECTIVE_HEAD = _BATCH_DIRECTIVE.replace('\n\n', ' ').strip()[:60]
_ASSEMBLED = (_BATCH_DIRECTIVE + '【记忆块】\n' +
              '本任务的输入文件已放在工作区 uploads/a.csv，请完成下面的要求：\n\n'
              '分析这份销售数据并给出结论')


def test_strip_batch_directive_removes_prefix():
    """剥系统规则后应露出派发期组装物或用户内容——验收线：不含系统规则
    样板（【记忆块】提示行也是组装物，但剥离以'露出用户内容'为验收线）。"""
    out = _strip_batch_directive(' '.join(_ASSEMBLED.split()))
    assert '系统规则' not in out, out[:60]
    assert '本任务的输入文件已放在工作区' in out or \
        '【记忆块】' in out, out[:60]


def test_strip_batch_directive_plain_text_untouched():
    assert _strip_batch_directive('分析这份销售数据') == '分析这份销售数据'


def test_strip_batch_directive_no_marker_keeps_text():
    """样板开头但截断无结束标记（极限截断行）——原样返回不误伤。"""
    assert _strip_batch_directive('[系统规则] 本会话是无人值守') == \
        '[系统规则] 本会话是无人值守'


def test_turn_preview_shows_user_text_not_directive():
    """存量拼装行：大纲预览不得显示系统规则样板（剥到露出用户内容/组装
    提示行即达标——验收线 = 系统提示词不再占满大纲）。"""
    preview = _turn_preview([{'type': 'text', 'text': _ASSEMBLED}])
    assert '系统规则' not in preview
    assert '本任务的输入文件已放在工作区' in preview


def test_turn_preview_plain_user_text_unchanged():
    preview = _turn_preview([{'type': 'text', 'text': '普通用户输入'}])
    assert preview == '普通用户输入'


def test_persist_user_prompt_writes_plain_text():
    """落库侧：BatchWorker._persist_user_prompt 落的是传入文本本身
    （调用点已改传用户原文——此处钉住函数不二次拼装、原样入 content）。

    mock 形状：get_db() 是 CM 返回 conn；`with conn.cursor() as cur` 的
    cur 是 cursor().__enter__() 的返回（自动 MagicMock），所以从
    conn.cursor.return_value.__enter__.return_value 取 execute。"""
    conn = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    cur = conn.cursor.return_value.__enter__.return_value
    with patch('utils.batch_engine.get_db', return_value=ctx):
        from utils.batch_engine import BatchWorker
        BatchWorker()._persist_user_prompt('sid1', '用户原文XYZ',
                                           generation=0)
    assert cur.execute.call_count == 1
    sql, params = cur.execute.call_args.args
    assert "'user'" in sql
    import json
    content = json.loads(params[2])
    assert content == [{'type': 'text', 'text': '用户原文XYZ'}]


def test_run_one_persists_plain_prompt_not_assembled():
    """接线断言（静态源码护栏）：_run_one 的落库调用必须传
    user_prompt_for_thread（用户原文），且派发拼装链路保留。"""
    import inspect
    import utils.batch_engine as eng
    src = inspect.getsource(eng)
    assert 'self._persist_user_prompt(sid, user_prompt_for_thread' in src, \
        '_run_one 落库调用必须传 user_prompt_for_thread（用户原文）'
    assert '_BATCH_DIRECTIVE + mem_block + prompt' in src, \
        '派发拼装链路必须保留（发给模型的仍是指令+原文）'
    assert 'user_prompt_plain = prompt' in src, \
        '非 continue 轮必须先取用户原文（hint 之后、指令拼装之前）'
