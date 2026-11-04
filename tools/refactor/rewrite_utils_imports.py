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
    # ---- batch 1: data/ + editor/ ----
    'mongo_query': 'data.mongo_query',
    'script_runner': 'data.script_runner',
    'data_export': 'data.data_export',
    'export_runner': 'data.export_runner',
    'export_references': 'data.export_references',
    'etl_engine': 'data.etl_engine',
    'etl_scheduler': 'data.etl_scheduler',
    'field_indexes': 'data.field_indexes',
    'field_index_scheduler': 'data.field_index_scheduler',
    'dependency_scheduler': 'data.dependency_scheduler',
    'scan_writeback_validator': 'data.scan_writeback_validator',
    'mcp_servers': 'data.mcp_servers',
    'mcp_tool_rules': 'data.mcp_tool_rules',
    'sequences': 'data.sequences',
    'page_config_relations': 'data.page_config_relations',
    'suggest_editor_repo': 'editor.suggest_editor_repo',
    'suggest_editor_rules': 'editor.suggest_editor_rules',
    'suggest_editor_rule_ai': 'editor.suggest_editor_rule_ai',
    'suggest_editor_attachments': 'editor.suggest_editor_attachments',
    # ---- batch 2: ai/opencode/ + ai/skills/ ----
    'opencode_client': 'ai.opencode.opencode_client',
    'opencode_ownership': 'ai.opencode.opencode_ownership',
    'opencode_launch': 'ai.opencode.opencode_launch',
    'opencode_parts': 'ai.opencode.opencode_parts',
    'subagent_reuse_plugin': 'ai.opencode.subagent_reuse_plugin',
    'tool_timeout_plugin': 'ai.opencode.tool_timeout_plugin',
    'plugin_registry': 'ai.opencode.plugin_registry',
    'opencode_global': 'ai.opencode.opencode_global',
    'global_skills': 'ai.skills.global_skills',
    'skill_fit': 'ai.skills.skill_fit',
    'skill_fit_ai': 'ai.skills.skill_fit_ai',
    'skillopt': 'ai.skills.skillopt',
    'skill_candidate_eval': 'ai.skills.skill_candidate_eval',
    'skill_upload': 'ai.skills.skill_upload',
    # ---- batch 3: ai/sessions/ + ai/gate/ + ai/scan/ ----
    'chat_persist': 'ai.sessions.chat_persist',
    'ai_session_repo': 'ai.sessions.ai_session_repo',
    'session_admin_repo': 'ai.sessions.session_admin_repo',
    'session_history': 'ai.sessions.session_history',
    'session_file_import': 'ai.sessions.session_file_import',
    'session_token': 'ai.sessions.session_token',
    'ai_message_meta': 'ai.sessions.ai_message_meta',
    'ai_query': 'ai.sessions.ai_query',
    'prompt_template': 'ai.sessions.prompt_template',
    'memory': 'ai.sessions.memory',
    'takeover_repo': 'ai.sessions.takeover_repo',
    'mention_files': 'ai.sessions.mention_files',
    'artifact_store': 'ai.sessions.artifact_store',
    'status_badge_timeout_scheduler': 'ai.sessions.status_badge_timeout_scheduler',
    'gate_policy_repo': 'ai.gate.gate_policy_repo',
    'action_check_extractor': 'ai.gate.action_check_extractor',
    'ai_scan_engine': 'ai.scan.ai_scan_engine',
    'ai_scan_repo': 'ai.scan.ai_scan_repo',
    'ai_scan_scheduler': 'ai.scan.ai_scan_scheduler',
    'ai_schema_designer': 'ai.scan.ai_schema_designer',
    # ---- batch 4a: ai/batch/ ----
    'batch_events': 'ai.batch.batch_events',
    'execution_audit': 'ai.batch.execution_audit',
    'execution_budget': 'ai.batch.execution_budget',
    'execution_effect': 'ai.batch.execution_effect',
    'execution_contract': 'ai.batch.execution_contract',
    'execution_lease': 'ai.batch.execution_lease',
    'execution_commands': 'ai.batch.execution_commands',
    'execution_checkpoint': 'ai.batch.execution_checkpoint',
    'verifier': 'ai.batch.verifier',
    'oc_watchdog': 'ai.batch.oc_watchdog',
    'todo_trace': 'ai.batch.todo_trace',
    'trace_auditor': 'ai.batch.trace_auditor',
    'run_diagnosis': 'ai.batch.run_diagnosis',
    'delivery_outbox': 'ai.batch.delivery_outbox',
    'subtask_repo': 'ai.batch.subtask_repo',
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


def _local_binding_names(tree):
    """Names the file binds in ANY scope — conservative shadow-collision set.

    Covers: Store/Del Name targets (assignments, for targets, with-as,
    comprehension and walrus targets), def/class names, function args,
    except-handler names, and import bindings (`import a[.b]` binds the root
    `a`; `import a as b` / `from m import n [as c]` bind the alias).
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != '*':
                    names.add(a.asname or a.name)
    return names


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
    newly_bound = set()    # names the rewrite binds that the file did NOT bind before
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
                            if a.asname is None:
                                # `import utils.X` originally bound the root
                                # `utils`; the rewrite binds X — a NEW name.
                                newly_bound.add(mod)
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
    # An injected `from common import X as X` introduces the name X into the
    # file. If X is already bound anywhere in the file (arg, local Store,
    # def/class, except name, another import...), the injection would shadow
    # it — syntax-legal, so the apply-time ast.parse backstop cannot catch
    # it. Same hazard for a rewritten unaliased `import utils.X`: the
    # original line bound the root `utils`, the rewrite binds X. Docstring
    # contract: report the file for manual handling (exit 2), leave the file
    # completely untouched (no partial rewrites).
    pending_mods = [m for m in sorted(set(MAPPING) & chain_mods)
                    if m not in emitted]
    new_names = set(pending_mods) | newly_bound
    if new_names:
        clashes = sorted(new_names & _local_binding_names(tree))
        if clashes:
            return [], ['%s: injected binding would shadow file-local '
                        'name(s): %s' % (rel, ', '.join(clashes))]
    pending = [_binding_line(m) for m in pending_mods]
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
