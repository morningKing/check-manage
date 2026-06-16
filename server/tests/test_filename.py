"""Unicode-preserving safe_filename sanitizer tests."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.filename import safe_filename  # noqa: E402


def test_preserves_chinese_name():
    # the regression: werkzeug.secure_filename would drop the Chinese stem
    assert safe_filename('巡检报告 2026.pdf') == '巡检报告 2026.pdf'
    assert safe_filename('报告.pdf') == '报告.pdf'
    assert safe_filename('设备清单.xlsx') == '设备清单.xlsx'


def test_strips_path_components_and_traversal():
    assert safe_filename('../../etc/passwd') == 'passwd'
    assert safe_filename('C:\\Users\\x\\报告.docx') == '报告.docx'
    assert safe_filename('a/b/c/名单.csv') == '名单.csv'


def test_replaces_illegal_and_control_chars():
    assert safe_filename('a<b>c:"d".txt') == 'a_b_c__d_.txt'
    assert safe_filename('线\x00上\x1f.txt') == '线上.txt'


def test_empty_or_dotonly_falls_back():
    assert safe_filename('').startswith('upload_')
    assert safe_filename('   ').startswith('upload_')
    assert safe_filename('...').startswith('upload_')


def test_guards_windows_reserved_names():
    assert safe_filename('CON.txt') == '_CON.txt'
    assert safe_filename('com1.log') == '_com1.log'
    # a Chinese name that merely contains those letters is untouched
    assert safe_filename('CONTRACT合同.pdf') == 'CONTRACT合同.pdf'


def test_caps_overlong_name_but_keeps_extension():
    long_name = '报' * 300 + '.pdf'
    out = safe_filename(long_name)
    assert out.endswith('.pdf')
    assert len(out.encode('utf-8')) <= 200
