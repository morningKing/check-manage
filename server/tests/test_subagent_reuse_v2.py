"""subagent_reuse V2 版单测：模板形态、部署幂等、路径防逃逸。

V2 事件形状与真机实测依据见模块 docstring（v2.2.6 探针）。
"""
import os

from ai.opencode.subagent_reuse_plugin import (ensure_subagent_reuse_plugin_v2,
                                         plugin_source_v2)


def test_v2_template_is_v2_api_shape():
    src = plugin_source_v2('http://ep', 'tok')
    # V2 对象入口（{id, setup}），不得出现 V1 函数入口/钩子键
    assert "id: 'baize-subagent-reuse'" in src
    assert 'async setup(ctx)' in src
    assert 'ctx.tool.hook(\'execute.before\'' in src
    assert "'tool.execute.before'(" not in src          # V1 钩子键形态
    assert 'event.tool !== \'subagent\'' in src         # V2 工具名
    assert 'BaizeSubagentReusePlugin' not in src        # V1 导出函数名
    assert '__ENDPOINT__' not in src and '__TOKEN__' not in src
    assert src.count('http://ep') >= 1 and 'tok' in src


def test_v2_deploy_writes_directory_layout(tmp_path):
    root = str(tmp_path / 'plugins')
    out = ensure_subagent_reuse_plugin_v2(root, 'http://ep', 'tok')
    assert out == os.path.join(root, 'baize-subagent-reuse', 'index.js')
    assert os.path.isfile(out)
    src = open(out, encoding='utf-8').read()
    assert "id: 'baize-subagent-reuse'" in src


def test_v2_deploy_idempotent(tmp_path):
    root = str(tmp_path / 'plugins')
    p1 = ensure_subagent_reuse_plugin_v2(root, 'http://ep', 'tok')
    mtime1 = os.path.getmtime(p1)
    p2 = ensure_subagent_reuse_plugin_v2(root, 'http://ep', 'tok')
    assert p1 == p2
    assert os.path.getmtime(p2) == mtime1  # 内容无变化不重写


def test_v2_deploy_rejects_escape(tmp_path):
    # plugins_root 内命名固定（PLUGIN_NAME_V2 常量），防逃逸护栏兜底校验
    root = str(tmp_path / 'plugins')
    os.makedirs(root, exist_ok=True)
    out = ensure_subagent_reuse_plugin_v2(root, 'http://ep', 'tok')
    assert os.path.realpath(out).startswith(os.path.realpath(root))


def test_v2_deploy_requires_endpoint(tmp_path):
    assert ensure_subagent_reuse_plugin_v2(str(tmp_path), '') is None
    assert ensure_subagent_reuse_plugin_v2('', 'http://ep') is None
