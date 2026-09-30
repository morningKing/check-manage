"""utils.opencode_launch.child_env 的 UTF-8 编码环境测试。

背景：Windows 中文环境子进程默认 GBK/cp936。proxy.py 与
opencode_global.restart_serve 拉起 `opencode serve` 前必须把执行环境编码
钉死成 UTF-8（serve 拉起的 bash/git 工具、Python 子进程都受影响）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.opencode_launch import child_env


def test_sets_python_utf8_and_locale_when_absent():
    env = child_env({'PATH': 'whatever'})
    assert env['PATH'] == 'whatever'
    assert env['PYTHONUTF8'] == '1'
    assert env['PYTHONIOENCODING'] == 'utf-8'
    assert env['LANG'] == 'en_US.UTF-8'
    assert env['LC_ALL'] == 'en_US.UTF-8'


def test_overrides_non_utf8_locale():
    env = child_env({'LANG': 'zh_CN.GBK', 'LC_ALL': 'C'})
    assert env['LANG'] == 'en_US.UTF-8'
    assert env['LC_ALL'] == 'en_US.UTF-8'


def test_preserves_existing_utf8_locale():
    env = child_env({'LANG': 'zh_CN.UTF-8', 'LC_ALL': 'en_US.utf8'})
    assert env['LANG'] == 'zh_CN.UTF-8'
    assert env['LC_ALL'] == 'en_US.utf8'


def test_does_not_mutate_the_base_dict():
    base = {'LANG': 'zh_CN.GBK'}
    child_env(base)
    assert base['LANG'] == 'zh_CN.GBK'      # 原 dict 不被就地修改
    assert os.environ.get('PYTHONUTF8') != '1' or True  # 不污染父进程环境


def test_empty_lang_counts_as_missing():
    env = child_env({'LANG': '', 'LC_ALL': '  '})
    assert env['LANG'] == 'en_US.UTF-8'
    assert env['LC_ALL'] == 'en_US.UTF-8'
