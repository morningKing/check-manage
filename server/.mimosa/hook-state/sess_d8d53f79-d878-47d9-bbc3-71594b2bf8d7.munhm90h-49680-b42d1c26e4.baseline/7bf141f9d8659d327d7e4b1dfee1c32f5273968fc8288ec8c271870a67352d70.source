"""Tests for utils/mention_files.py — @<path> file-mention parsing in chat messages.

Agent mentions (@<name>, validated separately against the agent list and sent
structured as agentMentions) must NOT be reported here; only path-like tokens
that could reference a workspace file are returned (existence/permission checks
happen later in the route).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.mention_files import find_file_mentions, inline_file_mentions


def test_basic_uploads_path():
    assert find_file_mentions('帮我分析 @uploads/data.csv 这个文件') == ['uploads/data.csv']


def test_outputs_and_workspace_root_paths():
    out = find_file_mentions('看 @outputs/report.json 和 @main.ts')
    assert out == ['outputs/report.json', 'main.ts']


def test_dedupes_repeated_mentions_preserving_order():
    out = find_file_mentions('@uploads/a.txt 再看 @uploads/b.txt 还有 @uploads/a.txt')
    assert out == ['uploads/a.txt', 'uploads/b.txt']


def test_start_of_string_and_after_newline():
    assert find_file_mentions('@uploads/a.txt\n下一行 @outputs/b.json') == [
        'uploads/a.txt', 'outputs/b.json']


def test_agent_name_without_slash_or_dot_is_excluded():
    # bare word → treated as an agent/command mention, not a file
    assert find_file_mentions('问问 @build 怎么构建', agent_names={'build'}) == []
    # even without the agent list, a bare token (no / or .) is not a file candidate
    assert find_file_mentions('问问 @build 怎么构建') == []


def test_agent_names_are_skipped_even_if_path_like():
    # agent name that happens to contain a dot shouldn't be inlined as a file
    assert find_file_mentions('@some.agent 干活', agent_names={'some.agent'}) == []


def test_email_is_not_a_mention():
    # the '@' must follow start-of-string or whitespace; a@b.com has none
    assert find_file_mentions('联系 a@b.com 谢谢') == []


def test_trailing_sentence_period_is_trimmed():
    # "@data.csv." at sentence end still resolves to data.csv
    assert find_file_mentions('请看 @uploads/data.csv。') == ['uploads/data.csv']
    assert find_file_mentions('请看 @uploads/data.csv.') == ['uploads/data.csv']


def test_nested_path_with_subdir():
    assert find_file_mentions('@src/utils/helper.ts 里有问题') == ['src/utils/helper.ts']


def test_cjk_path_supported():
    assert find_file_mentions('分析 @uploads/数据报表.csv') == ['uploads/数据报表.csv']


def test_caps_number_of_mentions():
    text = ' '.join(f'@outputs/f{i}.txt' for i in range(20))
    out = find_file_mentions(text)
    assert len(out) == 10  # MAX_MENTIONED_FILES


# ---------------------------------------------------------------------------
# inline_file_mentions: FS resolution + text inlining / binary notes
# ---------------------------------------------------------------------------

def _ws(tmp_path):
    (tmp_path / 'uploads').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'outputs').mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_inline_text_file_content(tmp_path):
    ws = _ws(tmp_path)
    (ws / 'uploads' / 'a.txt').write_text('HELLO-DATA', encoding='utf-8')
    block = inline_file_mentions(str(ws), '看 @uploads/a.txt')
    assert 'HELLO-DATA' in block
    assert 'uploads/a.txt' in block


def test_inline_binary_file_emits_tool_pointer(tmp_path):
    ws = _ws(tmp_path)
    (ws / 'outputs' / 'b.bin').write_bytes(b'\x00\x01\x02\xff\xfe')
    block = inline_file_mentions(str(ws), '处理 @outputs/b.bin')
    assert 'b.bin' in block
    # binary content is NOT inlined as text
    assert 'HELLO' not in block
    # pointer tells the agent to use tools
    assert '工具' in block or '读取' in block


def test_missing_path_is_skipped_silently(tmp_path):
    ws = _ws(tmp_path)
    assert inline_file_mentions(str(ws), '看 @uploads/nope.csv') == ''


def test_path_traversal_is_blocked(tmp_path):
    ws = _ws(tmp_path)
    secret = tmp_path.parent / 'secret.txt'
    secret.write_text('TOPSECRET', encoding='utf-8')
    try:
        block = inline_file_mentions(str(ws), '偷看 @../secret.txt')
        assert 'TOPSECRET' not in block
        assert block == ''
    finally:
        secret.unlink(missing_ok=True)


def test_attachments_not_double_inlined(tmp_path):
    ws = _ws(tmp_path)
    (ws / 'uploads' / 'a.txt').write_text('DUP', encoding='utf-8')
    block = inline_file_mentions(
        str(ws), '见 @uploads/a.txt', already_attached={'uploads/a.txt'})
    assert block == ''


def test_oversized_text_file_treated_as_binary_pointer(tmp_path):
    ws = _ws(tmp_path)
    big = 'x' * 999
    (ws / 'uploads' / 'big.txt').write_text(big, encoding='utf-8')
    block = inline_file_mentions(str(ws), '@uploads/big.txt', max_bytes=100)
    # content beyond cap is not inlined; a tool pointer is emitted instead
    assert big not in block
    assert 'big.txt' in block
