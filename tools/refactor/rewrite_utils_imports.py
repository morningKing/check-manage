#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""utils.* import rewriter: AST locate + text-level precise replace.

Spec: docs/superpowers/specs/2026-11-03-backend-restructure-design.md SS6
- Touches ONLY import statements and `utils.<mod>` attribute-chain prefixes;
  every other byte is untouched (diff limited to import regions).
- Binding name always stays the OLD module name (split packages bind via
  `from <parent> import <leaf> as <old>`), so call sites never learn the
  new layout.
- Idempotent: rewritten files no longer reference mapped modules.
- A chain binding colliding with file-local names -> file reported for
  manual handling (exit 2); safe files still rewritten.

Usage (repo root):
  python tools/refactor/rewrite_utils_imports.py           # rewrite in place
  python tools/refactor/rewrite_utils_imports.py --check   # report only
Exit codes: 0 nothing to do; 1 rewrite done (or pending under --check);
            2 manual-fix files present
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / 'server'

# old module name (first segment after `utils.`) -> new dotted path.
# Split targets are package paths; their __init__ re-exports every original
# symbol (underscore-prefixed included). Entries are appended per batch.
MAPPING = {
    # ---- batch 0: common/ ----
    'operation_log': 'common.operation_log',
    'permissions': 'common.permissions',
    'rbac_guard': 'common.rbac_guard',
    'notifier': 'common.notifier',
    'api_errors': 'common.api_errors',
    'errors': 'common.errors',
    'logging_setup': 'common.logging_setup',
    'log_tail': 'common.log_tail',
    'log_retention': 'common.log_retention',
    'rate_limit': 'common.rate_limit',
    'upload_limits': 'common.upload_limits',
    'filename': 'common.filename',
    'zip_unicode': 'common.zip_unicode',
    'search_text': 'common.search_text',
    'py_runner': 'common.py_runner',
}

SCAN_FILES = ('app.py', 'proxy.py', 'init_db.py', 'auth.py')
SCAN_DIRS = (
    'routes', 'utils', 'tests', 'scripts', 'test_scripts', 'migrations',
    'common', 'ai', 'data', 'projects', 'editor', 'collab',
    '../mcp-server/tools', '../mcp-server/tests', '../scripts',
)


def iter_targets():
    for name in SCAN_FILES:
        p = SERVER / name
        if p.is_file():
            yield p
    for d in SCAN_DIRS:
        base = (SERVER / d).resolve()
        if base.is_dir():
            for q in sorted(base.rglob('*.py')):
                if '__pycache__' not in q.parts:
                    yield q


def _parent_leaf(dotted):
    parent, _, leaf = dotted.rpartition('.')
    return parent, leaf


def _binding_line(mod):
    parent, leaf = _parent_leaf(MAPPING[mod])
    return 'from %s import %s as %s' % (parent, leaf, mod)


def _stmt_span(src, node):
    # Precise, char-accurate span of the statement itself (never the whole
    # physical line): ast col offsets are UTF-8 BYTE offsets, so convert.
    # Whole-line spans made `text != chunk` true even for no-op rewrites
    # (chunk carried the trailing newline the generated text lacks), which
    # deleted the newline and glued the next statement on; and on
    # `import x; y()` lines they silently swallowed the rest of the line.
    lines = src.splitlines(keepends=True)

    def _char_pos(lineno, byte_col):
        base = sum(len(x) for x in lines[:lineno - 1])
        line = lines[lineno - 1]
        return base + len(line.encode('utf-8')[:byte_col].decode('utf-8', 'replace'))

    start = _char_pos(node.lineno, node.col_offset)
    end = _char_pos(node.end_lineno, node.end_col_offset)
    return start, end


def _trailing_comment(chunk):
    first = chunk.split('\n', 1)[0]
    for marker in ('  #', ' #'):
        idx = first.find(marker)
        if idx != -1:
            return first[idx:].rstrip()
    return ''


def _alias_text(a):
    return a.name + (' as ' + a.asname if a.asname else '')


def analyze(path):
    """-> (edits, problems). edits = [(start, end, text)], non-overlapping."""
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        src = f.read()
    rel = path.relative_to(REPO).as_posix()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [], ['%s: SyntaxError %s' % (rel, e)]

    edits = []
    problems = []
    emitted = set()        # old names already bound by a rewritten import
    unmoved_chain = False  # file still uses utils.<unmoved> via attribute chains

    import_nodes = []      # (node, kind, payload); sorted by source order later
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == 'utils':
                import_nodes.append((node, 'from_bare', None))
            elif node.module.startswith('utils.'):
                import_nodes.append((node, 'from_mod', node.module[6:]))
        elif isinstance(node, ast.Import):
            import_nodes.append((node, 'importline', None))
    import_nodes.sort(key=lambda t: (t[0].lineno, t[0].col_offset))

    def _root_is_utils(n):
        while isinstance(n, ast.Attribute):
            n = n.value
        return isinstance(n, ast.Name) and n.id == 'utils'

    # ---- attribute chains: utils.<mod>.<rest> -> <mod>.<rest> ----
    chain_mods = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and _root_is_utils(node)):
            continue
        if isinstance(node.value, ast.Attribute) and _root_is_utils(node.value):
            continue  # inner link of a chain; the utils.<mod> link (whose
            # value is the bare Name) is the one processed below
        inner = node
        while isinstance(inner.value, ast.Attribute):
            inner = inner.value
        mod = inner.attr  # utils.<mod>.<rest...>
        chain_mods.add(mod)
        if mod not in MAPPING:
            unmoved_chain = True
            continue
        # Absolute char span of the `utils.<mod>` link (apply_edits works in
        # absolute file offsets; raw col_offset is line-relative UTF-8 bytes).
        cs, ce = _stmt_span(src, inner)
        edits.append((cs, ce, mod))

    # ---- import statements ----
    import_edits = []
    for node, kind, payload in import_nodes:
        span = _stmt_span(src, node)
        chunk = src[span[0]:span[1]]
        cmt = _trailing_comment(chunk)
        suffix = ('  ' + cmt.lstrip()) if cmt else ''

        if kind == 'from_mod':
            if payload not in MAPPING:
                continue
            text = chunk.replace('utils.' + payload, MAPPING[payload], 1)
        elif kind == 'from_bare':
            moved = [a for a in node.names if a.name in MAPPING]
            if not moved:
                continue
            parts = []
            unmoved = [a for a in node.names if a.name not in MAPPING]
            if unmoved:
                parts.append('from utils import ' +
                             ', '.join(_alias_text(a) for a in unmoved) + suffix)
                suffix = ''
            for a in moved:
                bind = a.asname or a.name
                parent, leaf = _parent_leaf(MAPPING[a.name])
                parts.append('from %s import %s as %s' % (parent, leaf, bind))
                if bind == a.name:
                    emitted.add(a.name)
            text = '\n'.join(parts)
        else:  # importline: import utils[, x] / import utils.X[ as y]
            plain = []
            froms = []  # mapped mods become their own `from ...` statements
            for a in node.names:
                if a.name == 'utils':
                    if unmoved_chain:
                        plain.append('utils')  # keep bare: unmoved chains need it
                    continue
                if a.name.startswith('utils.'):
                    mod = a.name[6:]
                    if mod in MAPPING:
                        parent, leaf = _parent_leaf(MAPPING[mod])
                        bind = a.asname or mod
                        froms.append('from %s import %s as %s' % (parent, leaf, bind))
                        if bind == mod:
                            emitted.add(mod)
                    else:
                        plain.append(_alias_text(a))
                else:
                    plain.append(_alias_text(a))
            parts = list(froms)
            if plain:
                parts.append('import ' + ', '.join(plain))
            text = '' if not parts else '\n'.join(parts) + suffix
        if text != chunk:
            import_edits.append((span[0], span[1], text))

    # ---- chain bindings not provided by any rewritten import ----
    pending = [_binding_line(m) for m in sorted(set(MAPPING) & chain_mods)
               if m not in emitted]
    if pending:
        blk = '\n'.join(pending) + '\n'
        host = import_edits[0] if import_edits else None
        if host is not None and host[0] == min(
                (s for s, _, _ in import_edits), default=None):
            import_edits[0] = (host[0], host[1], blk + host[2])
        else:
            first_import = import_nodes[0][0]
            pos = _stmt_span(src, first_import)[0]
            import_edits.append((pos, pos, blk))
    edits.extend(import_edits)
    return edits, problems


def apply_edits(path, edits):
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        src = f.read()
    edits = sorted(edits, key=lambda x: (x[0], x[1]))
    for (s1, e1, _), (s2, e2, _) in zip(edits, edits[1:]):
        if e1 > s2:
            return None  # overlapping edits: refuse
    out = src
    for s, e, text in reversed(edits):
        out = out[:s] + text + out[e:]
    try:
        ast.parse(out)
    except SyntaxError:
        return None
    return out


def main():
    check = '--check' in sys.argv
    changed = 0
    problems = []
    for path in iter_targets():
        edits, probs = analyze(path)
        problems.extend(probs)
        if not edits:
            continue
        if check:
            changed += 1
            print('WOULD REWRITE %s (%d edits)'
                  % (path.relative_to(REPO).as_posix(), len(edits)))
            continue
        new = apply_edits(path, edits)
        if new is None:
            problems.append('%s: rewrite failed validation, left unchanged'
                            % path.relative_to(REPO).as_posix())
            continue
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(new)
        changed += 1
        print('REWRITTEN %s' % path.relative_to(REPO).as_posix())
    for p in problems:
        print('MANUAL: %s' % p, file=sys.stderr)
    print('%d files %s, %d manual' % (
        changed, 'to rewrite' if check else 'rewritten', len(problems)))
    if problems:
        return 2
    return 1 if changed else 0


if __name__ == '__main__':
    sys.exit(main())
