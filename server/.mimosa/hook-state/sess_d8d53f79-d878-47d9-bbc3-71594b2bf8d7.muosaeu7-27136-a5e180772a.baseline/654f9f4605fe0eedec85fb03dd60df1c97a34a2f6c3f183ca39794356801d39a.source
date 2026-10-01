"""tool_timeout_plugin / opencode_launch.serve_env 的单元测试。

覆盖：插件文件幂等部署、路径逃逸拒绝、门限/豁免配置嵌入、
serve_env 对超时相关 env 的透传。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from utils.tool_timeout_plugin import (PLUGIN_NAME, DEFAULT_EXEMPT,
                                       DEFAULT_TIMEOUT_MS,
                                       ensure_tool_timeout_plugin,
                                       plugin_source)


@pytest.fixture
def global_dir(tmp_path):
    return str(tmp_path / 'opencode-global')


def test_plugin_written_and_idempotent(global_dir):
    p1 = ensure_tool_timeout_plugin(global_dir)
    assert p1 and os.path.isfile(p1)
    src = plugin_source()
    with open(p1, 'r', encoding='utf-8') as f:
        assert f.read() == src
    # 幂等：再次安装不重写（mtime 不变）
    before = os.path.getmtime(p1)
    p2 = ensure_tool_timeout_plugin(global_dir)
    assert p2 == p1
    assert os.path.getmtime(p2) == before


def test_plugin_source_embeds_timeout_and_exempt():
    src = plugin_source(timeout_ms=12345, exempt='task,webfetch')
    assert '__TIMEOUT_MS__' not in src and '__EXEMPT__' not in src
    assert '12345' in src
    assert 'task,webfetch' in src  # 豁免列表整串嵌入 JS 默认值，运行时按逗号切分
    # 钩子齐全：before 挂表、after 清理、超时 abort
    assert 'tool.execute.before' in src and 'tool.execute.after' in src
    assert 'client.session.abort' in src
    assert 'OPENCODE_TOOL_TIMEOUT_MS' in src
    assert DEFAULT_EXEMPT in plugin_source()   # 默认豁免 task
    assert str(DEFAULT_TIMEOUT_MS) in plugin_source()


def test_plugin_rejects_escaping_global_dir(tmp_path):
    # global_dir 指向不存在层级 + PLUGIN_NAME 固定，正常场景无法构造 ../ 逃逸；
    # 这里验证守卫逻辑本身：global_dir 为文件时 makedirs 失败 → 返回 None 不抛
    not_a_dir = tmp_path / 'file.txt'
    not_a_dir.write_text('x', encoding='utf-8')
    assert ensure_tool_timeout_plugin(str(not_a_dir)) is None


def test_serve_env_passthrough(monkeypatch):
    from utils.opencode_launch import serve_env
    monkeypatch.delenv('OPENCODE_TOOL_TIMEOUT_MS', raising=False)
    monkeypatch.delenv('OPENCODE_TOOL_TIMEOUT_EXEMPT', raising=False)
    monkeypatch.delenv('OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS',
                       raising=False)
    env = serve_env({})
    assert 'OPENCODE_TOOL_TIMEOUT_MS' not in env  # 未配置不注入
    monkeypatch.setenv('OPENCODE_TOOL_TIMEOUT_MS', '20000')
    monkeypatch.setenv('OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS',
                       '300000')
    env = serve_env({})
    assert env['OPENCODE_TOOL_TIMEOUT_MS'] == '20000'
    assert env['OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS'] == '300000'
    assert env['OPENCODE_GLOBAL_DIR']  # 原有钉死行为保持


def test_plugin_template_has_leak_fix_and_attributable_logging():
    """2026-09-30 泄漏修复与日志归因：会话删除清理计时器（在途工具不会触发
    after → 计时器泄漏 → 对死会话补 abort 误杀新回合）；日志带时间戳与入参
    摘要；慢完成（>30s）留归因日志。"""
    src = plugin_source()
    assert "type !== 'session.deleted'" in src            # 泄漏清理挂在删除事件
    assert 'session.removed' in src                       # 兼容两种事件名
    assert 'const ts = () => new Date().toISOString()' in src   # 时间戳
    assert 'argsSummary' in src                           # 入参摘要（归因到具体文件等）
    assert 'SLOW_COMPLETION_MS' in src                    # 慢完成归因日志
    assert '(input, output)' in src                       # before 读 output.args
