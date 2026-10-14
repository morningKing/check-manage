"""log_tail 跟尾纯函数测试（2026-10-14 spec §6）。

覆盖：缺失态、哨兵尾窗、增量不重不漏、半行缓冲、轮转、limit/truncated
续读、超长行截断、整块无换行防活锁、CRLF。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.log_tail import (  # noqa: E402
    MAX_READ_BYTES, TRUNC_SUFFIX, read_tail,
)


def _write(tmp_path, content: bytes) -> str:
    p = tmp_path / 'a.log'
    p.write_bytes(content)
    return str(p)


def test_missing_file(tmp_path):
    r = read_tail(str(tmp_path / 'nope.log'))
    assert r['missing'] is True
    assert r['lines'] == []
    assert r['offset'] == 0


def test_sentinel_reads_tail_window(tmp_path):
    path = _write(tmp_path, b''.join(f'l{i}\n'.encode() for i in range(10)))
    r = read_tail(path, -1, limit=3)
    assert r['lines'] == ['l7', 'l8', 'l9']
    assert r['offset'] == os.path.getsize(path)
    assert r['rotated'] is False


def test_incremental_appends_only_new_lines(tmp_path):
    path = _write(tmp_path, b'one\ntwo\n')
    first = read_tail(path, -1)
    assert first['lines'] == ['one', 'two']
    with open(path, 'ab') as f:
        f.write(b'three\n')
    second = read_tail(path, first['offset'])
    assert second['lines'] == ['three']
    third = read_tail(path, second['offset'])
    assert third['lines'] == []


def test_partial_line_buffered_until_newline(tmp_path):
    path = _write(tmp_path, b'abc')
    r = read_tail(path, 0)
    assert r['lines'] == []
    assert r['offset'] == 0
    with open(path, 'ab') as f:
        f.write(b'def\n')
    r2 = read_tail(path, r['offset'])
    assert r2['lines'] == ['abcdef']


def test_rotation_offset_beyond_size(tmp_path):
    path = _write(tmp_path, b'old1\nold2\nold3\n')
    first = read_tail(path, -1)
    _write(tmp_path, b'new1\nnew2\n')          # 同路径重写 = 轮转后新文件
    r = read_tail(path, first['offset'])
    assert r['rotated'] is True
    assert r['lines'] == ['new1', 'new2']
    assert r['offset'] == os.path.getsize(path)


def test_limit_truncated_then_continue(tmp_path):
    path = _write(tmp_path, b''.join(f'l{i}\n'.encode() for i in range(10)))
    r1 = read_tail(path, 0, limit=4)
    assert r1['lines'] == ['l0', 'l1', 'l2', 'l3']
    assert r1['truncated'] is True
    r2 = read_tail(path, r1['offset'], limit=4)
    assert r2['lines'] == ['l4', 'l5', 'l6', 'l7']
    r3 = read_tail(path, r2['offset'], limit=4)
    assert r3['lines'] == ['l8', 'l9']
    assert r3['truncated'] is False


def test_long_line_truncated_but_offset_advances(tmp_path):
    long = 'x' * 5000
    path = _write(tmp_path, (long + '\n').encode())
    r = read_tail(path, 0)
    assert r['lines'] == [long[:4000] + TRUNC_SUFFIX]
    assert r['offset'] == os.path.getsize(path)


def test_giant_line_without_newline_skips_no_livelock(tmp_path):
    path = _write(tmp_path, b'x' * (MAX_READ_BYTES + 100))
    r = read_tail(path, 0)
    assert r['offset'] == MAX_READ_BYTES          # 已推进：不会重读同块
    assert r['lines'] and r['lines'][0].startswith('…[超长行已跳过')
    r2 = read_tail(path, r['offset'])
    # 残段 100B 无换行 = 半行：缓冲等待换行，offset 只推进到完整行末（不重不漏）
    assert r2['offset'] == MAX_READ_BYTES
    assert r2['lines'] == []


def test_crlf_stripped(tmp_path):
    path = _write(tmp_path, b'a\r\nb\r\n')
    assert read_tail(path, 0)['lines'] == ['a', 'b']
