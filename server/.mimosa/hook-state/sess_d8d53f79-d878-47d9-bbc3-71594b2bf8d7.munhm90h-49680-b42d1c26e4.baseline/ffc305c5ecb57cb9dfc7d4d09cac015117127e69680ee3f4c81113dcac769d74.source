"""导出脚本追踪式断点调试器回归测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.script_runner import debug_export_script  # noqa: E402


def _page_locals(data):
    return {'data': data, 'fields': [], 'page_name': 'p',
            'result': None, 'filename': None, 'content_type': None}


def test_breakpoint_snapshots_and_line_hits():
    script = (
        "total = 0\n"            # 1
        "for d in data:\n"        # 2
        "    total += d['v']\n"   # 3
        "result = str(total)\n"   # 4
    )
    res = debug_export_script(script, 'export',
                              _page_locals([{'v': 1}, {'v': 2}, {'v': 3}]),
                              breakpoints=[3, 4])
    assert res['success'] is True
    # 行执行次数（执行轨迹）：line3 命中 3 次、line4 命中 1 次
    assert res['lineHits'].get(3) == 3
    assert res['lineHits'].get(4) == 1
    # line3 断点三次快照：line 事件在行执行前触发，故 total 累加序列为 0,1,3
    bp3 = [s for s in res['breakpointHits'] if s['line'] == 3]
    assert len(bp3) == 3
    assert [s['vars']['total'] for s in bp3] == [0, 1, 3]
    assert [s['vars']['d'] for s in bp3] == [{'v': 1}, {'v': 2}, {'v': 3}]
    # line4 快照：total 已累加到 6
    bp4 = [s for s in res['breakpointHits'] if s['line'] == 4]
    assert bp4 and bp4[0]['vars']['total'] == 6


def test_snapshot_excludes_injected_modules():
    script = "x = json.dumps([1])\nresult = x\n"
    res = debug_export_script(script, 'export', _page_locals([]), breakpoints=[2])
    assert res['success'] is True
    snap = res['breakpointHits'][0]['vars']
    assert 'json' not in snap          # 注入模块不出现在快照
    assert snap.get('x') == '[1]'      # 脚本变量出现


def test_runtime_error_reports_line():
    script = (
        "a = 1\n"          # 1
        "b = a + missing\n"  # 2  NameError
        "result = str(b)\n"  # 3
    )
    res = debug_export_script(script, 'export', _page_locals([]), breakpoints=[])
    assert res['success'] is False
    assert 'NameError' in res['error']
    assert res['errorLine'] == 2


def test_custom_function_traced():
    """脚本内定义的函数体也应被追踪并能在其行下断点。"""
    script = (
        "def fmt(rec):\n"            # 1
        "    out = rec['v'] * 2\n"    # 2
        "    return out\n"           # 3
        "result = str([fmt(d) for d in data])\n"  # 4
    )
    res = debug_export_script(script, 'export',
                              _page_locals([{'v': 5}, {'v': 7}]), breakpoints=[2])
    assert res['success'] is True
    bp2 = [s for s in res['breakpointHits'] if s['line'] == 2]
    assert len(bp2) == 2  # 函数体行随每次调用命中
    assert [s['vars']['rec'] for s in bp2] == [{'v': 5}, {'v': 7}]
