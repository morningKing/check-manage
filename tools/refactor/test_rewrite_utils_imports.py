#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression suite for rewrite_utils_imports.py (plain asserts, no pytest).

Run from anywhere:
    python tools/refactor/test_rewrite_utils_imports.py
Exit 0 = all cases pass; 1 = at least one failure.

Synthetic files are written (LF endings; one CRLF case locks terminator
preservation) into a temp dir created INSIDE the repo — analyze() only
accepts paths under REPO — and removed afterwards. The end-to-end main()
checks temporarily repoint the module's SERVER and SCAN_* constants (and,
for the validation-preview case, MAPPING) at the temp dir and restore them.
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rewrite_utils_imports as rw


def _write(path, text):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def _read(path):
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


# (name, source, expected) — expected is the exact post-rewrite text, or
# None when the case expects manual handling: analyze() must return no
# edits, a problems entry naming the file and the clashing binding(s), and
# the file on disk must stay byte-identical.
CASES = [
    # ---- rewrite behaviour locked in batch 0 ----
    ('chain_mapped',
     'import utils\n'
     'utils.permissions.check()\n'
     'utils.rbac_guard.guard(a, b)\n',
     'from common import permissions as permissions\n'
     'from common import rbac_guard as rbac_guard\n'
     '\n'
     'permissions.check()\n'
     'rbac_guard.guard(a, b)\n'),
    # NOTE: uses synthetic never-mapped names — field_indexes /
    # page_config_relations entered MAPPING in batch 1, which silently
    # turned the original batch-0 expectations stale (suite red since).
    ('chain_unmapped_kept',
     'import utils\n'
     'utils.totally_unmapped_mod.sync()\n'
     'utils.totally_unmapped_other.get()\n',
     'import utils\n'
     'utils.totally_unmapped_mod.sync()\n'
     'utils.totally_unmapped_other.get()\n'),
    ('chain_mixed_mapped_and_unmapped',
     'import utils\n'
     'utils.permissions.check()\n'
     'utils.totally_unmapped_mod.sync()\n',
     'from common import permissions as permissions\n'
     'import utils\n'
     'permissions.check()\n'
     'utils.totally_unmapped_mod.sync()\n'),
    ('from_bare_multi_name',
     'from utils import permissions, log_tail, json\n'
     'permissions.check()\n',
     'from utils import json\n'
     'from common import permissions as permissions\n'
     'from common import log_tail as log_tail\n'
     'permissions.check()\n'),
    ('from_mod',
     'from utils.permissions import check\n'
     'check()\n',
     'from common.permissions import check\n'
     'check()\n'),
    ('noop_plain_imports_untouched',
     'import logging\n'
     'import sys\n'
     'import os\n'
     'sys.path.insert(0, ".")\n',
     'import logging\n'
     'import sys\n'
     'import os\n'
     'sys.path.insert(0, ".")\n'),
    ('semicolon_same_line_untouched',
     'def f():\n'
     '    import logging; logging.exception("x")\n'
     '    return 1\n',
     'def f():\n'
     '    import logging; logging.exception("x")\n'
     '    return 1\n'),
    ('trailing_comment_preserved',
     'from utils.permissions import check  # legacy gate\n'
     'check()\n',
     'from common.permissions import check  # legacy gate\n'
     'check()\n'),
    ('importline_aliased',
     'import utils.permissions as perms\n'
     'perms.check()\n',
     'from common import permissions as perms\n'
     'perms.check()\n'),
    ('chain_multi_segment',
     'import utils\n'
     'v = utils.permissions.Guard().name\n'
     'print(v)\n',
     'from common import permissions as permissions\n'
     '\n'
     'v = permissions.Guard().name\n'
     'print(v)\n'),
    ('cjk_comment_same_line',
     'import utils\n'
     'x = "中文注释"\n'
     'utils.permissions.check(x)  # 中文调用\n',
     'from common import permissions as permissions\n'
     '\n'
     'x = "中文注释"\n'
     'permissions.check(x)  # 中文调用\n'),
    # was written unmapped in batch 0 ("untouched"); page_config_relations
    # entered MAPPING in batch 1 — the reload pattern now rewrites, binding
    # name stays the old module name (reload() target follows the binding).
    ('deferred_reload_point_rewritten',
     'def reload_mod():\n'
     '    import utils.page_config_relations\n'
     '    importlib.reload(utils.page_config_relations)\n'
     '    from utils.page_config_relations import get_f as g\n'
     '    return g\n',
     'def reload_mod():\n'
     '    from data import page_config_relations as page_config_relations\n'
     '    importlib.reload(page_config_relations)\n'
     '    from data.page_config_relations import get_f as g\n'
     '    return g\n'),
    # ---- binding already emitted by a rewritten import: NO false positive ----
    ('emitted_binding_not_flagged',
     'from utils import permissions\n'
     'utils.permissions.check()\n',
     'from common import permissions as permissions\n'
     'permissions.check()\n'),
    # ---- collision detection: chain binding vs file-local names (exit 2) ----
    ('shadow_function_local_store',
     'import utils\n'
     '\n'
     'def handler():\n'
     '    permissions = load()\n'
     '    return permissions\n'
     '\n'
     'utils.permissions.check()\n',
     None),
    ('shadow_top_level_def',
     'import utils\n'
     '\n'
     'def permissions():\n'
     "    return 'local'\n"
     '\n'
     'utils.permissions.check()\n',
     None),
    ('shadow_class_name',
     'import utils\n'
     '\n'
     'class rbac_guard:\n'
     '    pass\n'
     '\n'
     'utils.rbac_guard.guard()\n',
     None),
    ('shadow_arg_name',
     'import utils\n'
     '\n'
     'def f(permissions):\n'
     '    return permissions\n'
     '\n'
     'utils.permissions.check()\n',
     None),
    ('shadow_other_import_binding',
     'import utils\n'
     'from other import permissions\n'
     '\n'
     'utils.permissions.check()\n',
     None),
    ('shadow_importline_unaliased_new_binding',
     'import utils.permissions\n'
     '\n'
     'def permissions():\n'
     "    return 'local'\n"
     '\n'
     'permissions.check()\n',
     None),
    # ---- fix round 1: multi-name expansion of an INDENTED import inherits
    # ---- the host line's indent (batch 4a: 9 sites across 3 files exited 2
    # ---- because continuation lines landed at column 0)
    ('indent_multiname_all_mapped',
     'import os\n'
     '\n'
     'def f():\n'
     '    from utils import permissions, log_tail\n'
     '    return permissions, log_tail\n',
     'import os\n'
     '\n'
     'def f():\n'
     '    from common import permissions as permissions\n'
     '    from common import log_tail as log_tail\n'
     '    return permissions, log_tail\n'),
    ('indent_multiname_mixed_unmapped',
     'def g():\n'
     '    from utils import permissions, totally_unmapped_mod\n'
     '    return permissions\n',
     'def g():\n'
     '    from utils import totally_unmapped_mod\n'
     '    from common import permissions as permissions\n'
     '    return permissions\n'),
    ('indent_importline_multiname',
     'def h():\n'
     '    import utils.permissions as p, utils.log_tail as lt\n'
     '    return p, lt\n',
     'def h():\n'
     '    from common import permissions as p\n'
     '    from common import log_tail as lt\n'
     '    return p, lt\n'),
]

COLLIDE_SRC = CASES[13][1]  # shadow_function_local_store


def run_analyze_case(scratch, name, src, expected):
    path = Path(scratch) / (name + '.py')
    _write(path, src)
    edits, probs = rw.analyze(path)
    if expected is None:
        assert not edits, '%s: expected no edits, got %r' % (name, edits)
        assert probs, '%s: expected a problems entry' % name
        assert any('shadow file-local' in p and name + '.py' in p
                   for p in probs), '%s: problems lack file/clash info: %r' % (name, probs)
        assert _read(path) == src, '%s: file was modified despite clash' % name
        return
    assert not probs, '%s: unexpected problems %r' % (name, probs)
    out = rw.apply_edits(path, edits) if edits else src
    assert out == expected, '%s:\n  expected %r\n  got      %r' % (name, expected, out)
    assert _read(path) == src, '%s: analyze case must not touch disk' % name


def run_main_exit_codes(scratch):
    """End-to-end main(): collision file -> exit 2 untouched; rewrite -> 1;
    rerun on rewritten file -> exit 0 (idempotent)."""
    saved = (rw.SERVER, rw.SCAN_FILES, rw.SCAN_DIRS)
    old_argv = sys.argv
    try:
        rw.SERVER = Path(scratch)
        rw.SCAN_DIRS = ()
        rw.SCAN_FILES, rw.SCAN_DIRS = ('collide.py',), ()
        _write(Path(scratch) / 'collide.py', COLLIDE_SRC)
        sys.argv = ['rewrite_utils_imports']
        assert rw.main() == 2, 'collision file must exit 2'
        assert _read(Path(scratch) / 'collide.py') == COLLIDE_SRC

        rw.SCAN_FILES, rw.SCAN_DIRS = ('clean.py',), ()
        clean_src = CASES[0][1]
        clean_out = CASES[0][2]
        _write(Path(scratch) / 'clean.py', clean_src)
        assert rw.main() == 1, 'rewritable file must exit 1'
        assert _read(Path(scratch) / 'clean.py') == clean_out
        assert rw.main() == 0, 'rerun on rewritten file must exit 0'
    finally:
        rw.SERVER, rw.SCAN_FILES, rw.SCAN_DIRS = saved
        sys.argv = old_argv


def run_crlf_multiname(scratch):
    """fix round 1 (i)b: a CRLF host file's generated block keeps CRLF —
    no bare LF may be introduced by the expansion."""
    path = Path(scratch) / 'crlf_multiname.py'
    src = ('def g():\r\n'
           '    from utils import permissions, totally_unmapped_mod\r\n'
           '    return permissions\r\n')
    _write(path, src)
    edits, probs = rw.analyze(path)
    assert not probs, 'unexpected problems %r' % (probs,)
    out = rw.apply_edits(path, edits)
    expected = ('def g():\r\n'
                '    from utils import totally_unmapped_mod\r\n'
                '    from common import permissions as permissions\r\n'
                '    return permissions\r\n')
    assert out == expected, 'expected %r\n  got      %r' % (expected, out)
    assert out.count('\n') == out.count('\r\n'), \
        'bare LF introduced into CRLF file'
    assert _read(path) == src, 'analyze/apply must not touch disk'


def run_analyze_validation_preview(scratch):
    """fix round 1 (ii): analyze must reject at preview time what apply would
    reject at run time — no edits, a 'rewrite failed validation' problem, and
    --check exits 2 (MANUAL) with the file untouched."""
    path = Path(scratch) / 'validation_fail.py'
    src = ('def f():\n'
           '    from utils import alpha\n'
           '    return alpha\n')
    _write(path, src)
    saved_map = rw.MAPPING
    saved_srv = (rw.SERVER, rw.SCAN_FILES, rw.SCAN_DIRS)
    old_argv = sys.argv
    try:
        # 'class' is a keyword: the generated `from pkg.class import class
        # as alpha` can never parse — a deterministic apply-validation
        # failure simulation.
        rw.MAPPING = {'alpha': 'pkg.class'}
        edits, probs = rw.analyze(path)
        assert not edits, 'validation failure must yield no edits: %r' % (edits,)
        assert any('rewrite failed validation' in p and 'validation_fail.py' in p
                   for p in probs), \
            'analyze must report the validation failure: %r' % (probs,)
        assert _read(path) == src, 'file must stay untouched'

        # end-to-end: --check previews the MANUAL (exit 2), same message.
        rw.SERVER = Path(scratch)
        rw.SCAN_FILES, rw.SCAN_DIRS = ('validation_fail.py',), ()
        sys.argv = ['rewrite_utils_imports', '--check']
        assert rw.main() == 2, '--check must preview the MANUAL (exit 2)'
        assert _read(path) == src, '--check must not touch the file'
    finally:
        rw.MAPPING = saved_map
        rw.SERVER, rw.SCAN_FILES, rw.SCAN_DIRS = saved_srv
        sys.argv = old_argv


DEDICATED_CHECKS = (
    ('main_exit_codes', run_main_exit_codes),
    ('crlf_multiname_indent', run_crlf_multiname),
    ('analyze_validation_preview', run_analyze_validation_preview),
)


def main():
    scratch = tempfile.mkdtemp(dir=str(rw.REPO))
    failures = 0
    try:
        for name, src, expected in CASES:
            try:
                run_analyze_case(scratch, name, src, expected)
                print('PASS %s' % name)
            except AssertionError as e:
                failures += 1
                print('FAIL %s: %s' % (name, e))
        for name, fn in DEDICATED_CHECKS:
            try:
                fn(scratch)
                print('PASS %s' % name)
            except AssertionError as e:
                failures += 1
                print('FAIL %s: %s' % (name, e))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    total = len(CASES) + len(DEDICATED_CHECKS)
    print('%d/%d passed' % (total - failures, total))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
