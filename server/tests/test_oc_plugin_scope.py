"""OC 插件作用域（2026-11-24）：平台插件不得影响非平台会话。

背景：baize-trace / baize-tool-timeout / baize-subagent-reuse 三个插件装在
OpenCode **全局**目录（<global>/plugin/），随本机每一个 OpenCode 实例加载——
用户自己的工作会话（其它项目的目录）同样会加载它们。修复前钩子对**所有**
会话生效：每次工具调用前后各打一个后端请求（pre-check / post-check）、
task 委派查后端、每次工具调用挂 15 分钟 abort 看门狗；后端没开时这些请求
全部拒连，错误行刷进用户会话的 stderr。

实测事实（OpenCode 1.19.3，隔离实例探针）：V1 插件按「目录实例」各初始化
一份——init 入参的 directory 就是该实例会话的目录；V2 为
ctx.location.directory。故按平台工作区根做作用域：实例目录不在任何根下 →
插件初始化返回空钩子。

两层测试：Python 层（根清单 / 占位符替换 / 默认根）；Node 层把渲染出的插件
JS 当真 ES 模块 import 进来驱动（oc_plugin_scope_runner.mjs，无 node 则跳过）。
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import oc_plugin_scope as scope                  # noqa: E402
from utils.skillopt import (ensure_runtime_plugin,          # noqa: E402
                            ensure_runtime_plugin_v2)
from utils.subagent_reuse_plugin import (                   # noqa: E402
    ensure_subagent_reuse_plugin, ensure_subagent_reuse_plugin_v2)
from utils.tool_timeout_plugin import (                     # noqa: E402
    ensure_tool_timeout_plugin, ensure_tool_timeout_plugin_v2)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'oc_plugin_scope_runner.mjs')
NODE = shutil.which('node')


def _render_all(tmp_path, **kwargs):
    """六个插件全部渲染进临时目录 → {name: (kind, path)}（走真实部署函数）。"""
    g = str(tmp_path / 'global')
    v2 = str(tmp_path / 'v2-plugins')
    ep, tok = 'http://127.0.0.1:1/ai/memory/internal/runtime-events', 'tok'
    return {
        'baize-tool-timeout': ('v1', ensure_tool_timeout_plugin(g, **kwargs)),
        'baize-subagent-reuse': ('v1', ensure_subagent_reuse_plugin(
            g, 'http://127.0.0.1:1/ai/subagent-internal', tok, **kwargs)),
        'baize-trace': ('v1', ensure_runtime_plugin(
            g, ep, tok, gate_endpoint='http://127.0.0.1:1/pre', **kwargs)),
        'baize-tool-timeout-v2': ('v2', ensure_tool_timeout_plugin_v2(v2, **kwargs)),
        'baize-subagent-reuse-v2': ('v2', ensure_subagent_reuse_plugin_v2(
            v2, 'http://127.0.0.1:1/ai/subagent-internal', tok, **kwargs)),
        'baize-trace-v2': ('v2', ensure_runtime_plugin_v2(
            v2, ep, tok, 'http://127.0.0.1:1/pre', 'http://127.0.0.1:1/post', **kwargs)),
    }


# ── Python 层：平台工作区根清单 ────────────────────────────────────────────

def test_roots_cover_platform_session_roots(monkeypatch, tmp_path):
    """交互/批任务/独立会话的根 + 旧版批任务根 + 智能写作 agent 工作区根。"""
    import config
    root = str(tmp_path / 'ai-workspaces')
    se = str(tmp_path / 'se-data')
    monkeypatch.setattr(config, 'AI_WORKSPACE_ROOT', root, raising=False)
    monkeypatch.setattr(config, 'SE_DATA_DIR', se, raising=False)
    monkeypatch.delenv(scope.EXTRA_ROOTS_ENV, raising=False)
    monkeypatch.delenv('AI_CHAT_WORKSPACE_ROOT', raising=False)
    from utils.workspace import legacy_batch_workspace_root
    roots = scope.platform_workspace_roots()
    assert os.path.abspath(root) in roots
    assert os.path.abspath(os.path.join(se, 'ws')) in roots
    assert os.path.abspath(legacy_batch_workspace_root()) in roots
    # 去重：AI_CHAT_WORKSPACE_ROOT 未设时批任务根 = AI_WORKSPACE_ROOT，只出现一次
    key = lambda p: os.path.normcase(os.path.abspath(p))     # noqa: E731
    keys = [key(p) for p in roots]
    assert len(keys) == len(set(keys))
    assert all(p for p in roots)


def test_roots_batch_override_is_included(monkeypatch, tmp_path):
    """老部署显式设了 AI_CHAT_WORKSPACE_ROOT：批任务子会话在那里，必须在清单里。"""
    import config
    monkeypatch.setattr(config, 'AI_WORKSPACE_ROOT', str(tmp_path / 'chat'), raising=False)
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path / 'batch'))
    monkeypatch.delenv(scope.EXTRA_ROOTS_ENV, raising=False)
    roots = scope.platform_workspace_roots()
    assert os.path.abspath(str(tmp_path / 'batch')) in roots
    assert os.path.abspath(str(tmp_path / 'chat')) in roots


def test_roots_extra_env_appends(monkeypatch, tmp_path):
    """自定义布局经 BAIZE_PLUGIN_EXTRA_ROOTS 追加（os.pathsep 分隔，空项忽略）。"""
    import config
    monkeypatch.setattr(config, 'AI_WORKSPACE_ROOT', str(tmp_path / 'root'), raising=False)
    a, b = str(tmp_path / 'extra-a'), str(tmp_path / 'extra-b')
    monkeypatch.setenv(scope.EXTRA_ROOTS_ENV, os.pathsep.join([a, '', b]))
    roots = scope.platform_workspace_roots()
    assert os.path.abspath(a) in roots and os.path.abspath(b) in roots


# ── Python 层：模板渲染 ───────────────────────────────────────────────────

def test_sources_embed_scope_and_roots(tmp_path):
    r1, r2 = str(tmp_path / 'root-a'), str(tmp_path / 'root-b')
    for name, (kind, path) in _render_all(tmp_path, workspace_roots=[r1, r2]).items():
        src = open(path, encoding='utf-8').read()
        assert 'inPlatformWorkspace' in src, name
        assert json.dumps(os.path.abspath(r1)) in src, name
        assert json.dumps(os.path.abspath(r2)) in src, name
        # 占位符全部被消费
        assert '__WORKSPACE_ROOTS__' not in src, name
        assert '__BAIZE_SCOPE__' not in src, name
        if kind == 'v2':
            assert 'ctx.location.directory' in src, name


def test_sources_default_to_platform_roots(tmp_path, monkeypatch):
    """不显式传 roots → 用平台根清单（app.py 的调用形态）。"""
    import config
    root = str(tmp_path / 'default-root')
    monkeypatch.setattr(config, 'AI_WORKSPACE_ROOT', root, raising=False)
    monkeypatch.delenv(scope.EXTRA_ROOTS_ENV, raising=False)
    for name, (kind, path) in _render_all(tmp_path).items():
        assert json.dumps(os.path.abspath(root)) in open(path, encoding='utf-8').read(), name


def test_scope_js_embeds_roots_as_escaped_json():
    """根路径以 JSON 字面量嵌入：非 ASCII 路径转义成 \\uXXXX，原样路径不进文件
    （JS 字符串字面量对编码/转义敏感，JSON 是它的安全子集）。"""
    roots = [os.path.abspath('C:/中文 目录/根'), os.path.abspath('/srv/平台')]
    js = scope.scope_js(roots)
    assert json.dumps(roots) in js
    assert roots[0] not in js          # 原样（未转义）的路径没有出现


def test_inject_scope_requires_exactly_one_placeholder():
    """模板缺占位符 / 占位符重复 → 渲染期直接抛。

    教训：占位符曾是裸标识符 __BAIZE_SCOPE__，只替换它会把注入的代码留在模板
    的 /* */ 里——Python 层字符串断言全过，JS 实际整段被注释掉（运行期
    ReferenceError）。占位符改为连注释定界符一起整块替换，并在此钉死。"""
    good = 'before\n/*__BAIZE_SCOPE__*/\nafter'
    out = scope.inject_scope(good, ['C:/r'])
    assert out.startswith('before\n// ── 作用域')
    assert 'const inPlatformWorkspace' in out and '\nafter' in out
    assert scope.SCOPE_PLACEHOLDER not in out
    with pytest.raises(ValueError, match='exactly one'):
        scope.inject_scope('no placeholder here', ['C:/r'])
    with pytest.raises(ValueError, match='found 2'):
        scope.inject_scope(scope.SCOPE_PLACEHOLDER + scope.SCOPE_PLACEHOLDER, ['C:/r'])


# ── Node 层：真 import 渲染出的插件并驱动 ─────────────────────────────────

@pytest.mark.skipif(NODE is None, reason='node 不在 PATH（跳过 JS 执行层）')
def test_plugins_inert_outside_platform_roots(tmp_path):
    managed_root = os.path.abspath(str(tmp_path / 'managed-root'))
    rendered = _render_all(tmp_path, workspace_roots=[managed_root])

    mjs_dir = tmp_path / 'mjs'
    mjs_dir.mkdir()
    plugins = []
    for name, (kind, path) in rendered.items():
        dest = mjs_dir / (name + '.mjs')
        dest.write_text(open(path, encoding='utf-8').read(), encoding='utf-8')
        plugins.append({'name': name.replace('-v2', ''), 'kind': kind,
                        'file': str(dest)})

    def fwd(p):
        return str(p).replace(os.sep, '/')

    managed_dir = fwd(managed_root + os.sep + os.path.join('u-1', 'sess_1'))
    if os.name == 'nt':
        # Windows 路径大小写不敏感：根部分换大小写后仍必须识别为平台目录
        managed_dir = managed_dir.replace(fwd(managed_root), fwd(managed_root).upper())
    cfg = {
        'managedDir': managed_dir,
        'otherDir': fwd(tmp_path / 'other-project'),
        'otherDirs': [
            fwd(tmp_path / 'other-project'),          # 毫不相干的目录
            fwd(managed_root) + '-evil',              # 前缀相近的兄弟目录（边界）
            fwd(tmp_path),                            # 平台根的父目录（边界）
        ],
        'plugins': plugins,
    }
    cfg_path, report_path = tmp_path / 'cfg.json', tmp_path / 'report.json'
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')

    proc = subprocess.run([NODE, RUNNER, str(cfg_path), str(report_path)],
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=120)
    try:
        report = json.loads(report_path.read_text(encoding='utf-8'))
    except Exception:
        pytest.fail('runner 没有产出报告:\n' + proc.stdout[-2000:] + '\n' + proc.stderr[-2000:])
    failed = [c for c in report['cases'] if not c['ok']]
    assert proc.returncode == 0 and report['ok'], '\n'.join(
        f"FAIL {c['name']} :: {c['detail']}" for c in failed) or proc.stderr[-2000:]
    # 平台目录下门禁/复用钩子确实打了 sink（惰性不是「全都不工作」）
    assert report['hits'] > 0
