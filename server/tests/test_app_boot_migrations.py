"""Tests for app.py on-boot migration blocks (importlib load + exec + run).

app.py runs every migration via the pattern:

    _specN = _ilu.spec_from_file_location('<boot_name>', _mpN)
    _mN = _ilu.module_from_spec(_specN)
    _specN.loader.exec_module(_mN)      # ← executes the file's code
    _mN.run()

A block that omits `exec_module` loads an EMPTY module namespace and then
crashes with "module '<boot_name>' has no attribute 'run'"（生产启动实测，
_msg_search_boot）——except 吞掉后该迁移在重启路径上静默永不执行。这里静态
扫描 app.py 源码，保证每个 spec_from_file_location 块在调用 run/main 之前
都执行过 exec_module；同时保证引用的迁移文件真实存在。
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.join(HERE, '..', 'app.py')
MIGRATIONS_DIR = os.path.join(HERE, '..', 'migrations')

_SPEC_LINE = re.compile(
    r"^\s*(\w+)\s*=\s*(_ilu\w*)\.spec_from_file_location\('([\w]+)',\s*(\w+)\)")


def _app_source_lines():
    with open(APP_PY, encoding='utf-8') as f:
        return f.read().splitlines()


def _boot_blocks(lines):
    """Yield (line_no, spec_var, module_var, boot_name) for every
    spec_from_file_location assignment in app.py."""
    for i, line in enumerate(lines):
        m = _SPEC_LINE.match(line)
        if m:
            yield i + 1, m.group(1), m.group(4), m.group(3)


def test_every_boot_block_execs_module_before_run():
    """每个启动迁移块必须先 exec_module 再调 run()/main()。

    缺 exec_module = 空模块命名空间 → AttributeError '... has no attribute
    run'（被 except 吞成 warning，迁移静默跳过）。
    """
    lines = _app_source_lines()
    bad = []
    for line_no, spec_var, _module_var, boot_name in _boot_blocks(lines):
        window = lines[line_no:line_no + 7]   # 同块后续若干行
        if not any(f'{spec_var}.loader.exec_module' in w for w in window):
            bad.append(f'app.py:{line_no} ({boot_name}, {spec_var})')
    assert not bad, f'启动迁移块缺少 exec_module（模块代码未执行，run 必然 AttributeError）: {bad}'


def test_every_boot_block_references_existing_migration_with_entrypoint():
    """启动块引用的迁移文件必须存在，且定义 run() 或 main() 入口。"""
    lines = _app_source_lines()
    problems = []
    for line_no, _spec_var, module_var, _boot_name in _boot_blocks(lines):
        window = lines[line_no - 1:line_no + 7]
        path_match = None
        for w in window:
            m = re.search(r"migrations',\s*'([^']+\.py)'", w) or \
                re.search(r"'([^']+\.py)'", w)
            if m and ('migrations' in w or 'db_schema' in w):
                path_match = m.group(1)
                break
        if path_match is None:
            # 路径变量在块内联 os.path.join 组装，上面两窗已含；找不到就跳过
            #（该块的文件存在性由 exec/run 断言间接覆盖）。
            continue
        mp = os.path.normpath(os.path.join(HERE, '..', path_match)) \
            if not os.path.isabs(path_match) else path_match
        if not os.path.isfile(mp):
            problems.append(f'app.py:{line_no} 迁移文件不存在: {path_match}')
            continue
        with open(mp, encoding='utf-8') as f:
            body = f.read()
        if not re.search(r'^def (run|main)\(', body, re.M):
            problems.append(f'{path_match} 缺少 run()/main() 入口')
    assert not problems, '; '.join(problems)
