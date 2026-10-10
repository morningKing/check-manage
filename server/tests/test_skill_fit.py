# -*- coding: utf-8 -*-
"""SkillOpt 任务拟合单元（设计 docs/design/ai/SkillOpt任务拟合设计.md §3-§4）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.skill_fit import (FitParseError, match_steps, parse_cached,
                             parse_fit_steps)

SKILL_MD = """---
description: 数据拉取
fit:
  steps:
    - id: clone
      name: 克隆仓库
      expect:
        - tool: bash
          args_pattern: 'git clone'
    - id: inspect
      name: 检查结构
      expect:
        - tool: glob
    - id: report
      name: 写报告
      expect:
        - tool: write
          args_pattern: 'outputs?/'
---
正文内容忽略。
"""

NO_FIT_MD = """---
description: 无步骤声明
---
正文。
"""


def test_parse_fit_steps_ok():
    steps = parse_fit_steps(SKILL_MD)
    assert [s['id'] for s in steps] == ['clone', 'inspect', 'report']
    assert steps[0]['expect'] == [{'tool': 'bash', 'args_pattern': 'git clone'}]


def test_parse_fit_steps_no_fit_block():
    assert parse_fit_steps(NO_FIT_MD) == []


def test_parse_fit_steps_bad_yaml_raises():
    with pytest.raises(FitParseError):
        parse_fit_steps("---\nfit: [unclosed\n---\nbody")


def test_parse_cached_by_hash(tmp_path):
    f = tmp_path / 'SKILL.md'
    f.write_text(SKILL_MD, encoding='utf-8')
    import hashlib
    h = hashlib.sha256(SKILL_MD.encode('utf-8')).hexdigest()
    assert len(parse_cached(str(f), h)) == 3
    f.unlink()                       # 文件删除后按 hash 命中缓存
    assert len(parse_cached(str(f), h)) == 3
    with pytest.raises(FitParseError):
        parse_cached(str(tmp_path / 'gone-other.md'), 'deadbeef')


def _t(tool, args='', at=0):
    return {'tool': tool, 'args': args, 'occurredAt': f'2026-10-01T00:00:{at:02d}'}


def test_match_steps_all_hit_in_order():
    steps = parse_fit_steps(SKILL_MD)
    trace = [_t('bash', 'git clone https://x', 1), _t('glob', '**/*.py', 2),
             _t('write', 'outputs/report.md', 3)]
    r = match_steps(steps, trace)
    assert r['status'] == 'fit' and r['score'] == 100
    assert all(p['status'] == 'hit' for p in r['per_step'])
    assert r['per_step'][0]['evidence'][0]['args'] == 'git clone https://x'


def test_match_steps_out_of_order_tail():
    """重排对齐（2026-10-01 非目标②转正）：指针可命中的步骤照常 hit；
    指针已过但期望调用存在于轨迹早段 → out_of_order（不再误报 miss）。"""
    steps = parse_fit_steps(SKILL_MD)
    trace = [_t('write', 'outputs/r.md', 1), _t('bash', 'git clone x', 2),
             _t('glob', '**/*', 3)]
    r = match_steps(steps, trace)
    statuses = {p['id']: p['status'] for p in r['per_step']}
    # clone 命中位置 2 的 bash；inspect 命中位置 3 的 glob；
    # report 的 write 在位置 1（指针已过）→ out_of_order
    assert statuses == {'clone': 'hit', 'inspect': 'hit', 'report': 'out_of_order'}
    assert r['order_violations'] == 1
    assert r['status'] == 'partial'


def test_match_steps_no_trace():
    r = match_steps(parse_fit_steps(SKILL_MD), [])
    assert r['status'] == 'no_trace' and r['score'] == 0


def test_match_steps_py_invalid_regex_is_miss_not_raise():
    """终审 Fix 2：match_steps 防御——PG 合法但 Python re 非法的模式
    （PG 独有断言 \\y）在匹配时按 miss 处理（log.warning 一次），绝不抛。"""
    steps = [{'id': 'a', 'name': 'A',
              'expect': [{'tool': 'bash', 'args_pattern': r'\y'}]}]
    r = match_steps(steps, [_t('bash', 'git clone x', 1)])
    assert r['status'] == 'diverged' and r['score'] == 0
    assert r['per_step'][0]['status'] == 'miss' and not r['per_step'][0]['evidence']


def test_parse_cached_binary_garbage_raises_fit_parse_error(tmp_path):
    """终审 Fix 4：二进制坏字节文件（OSError/UnicodeDecodeError 族）统一
    收敛为 FitParseError——compute 路径据此落 parse_error 行，不中断整个
    attempt。"""
    import hashlib
    f = tmp_path / 'SKILL.md'
    f.write_bytes(b'\xff\xfe\x00\x01binary\x9c\xd3garbage')
    with pytest.raises(FitParseError):
        parse_cached(str(f), hashlib.sha256(f.read_bytes()).hexdigest())


def test_match_steps_skipped_not_scored():
    steps = parse_fit_steps(SKILL_MD)
    steps.append({'id': 'free', 'name': '无期望步骤', 'expect': []})
    trace = [_t('bash', 'git clone x', 1), _t('glob', '**/*', 2),
             _t('write', 'outputs/r.md', 3)]
    r = match_steps(steps, trace)
    assert r['steps_total'] == 3                    # skipped 不计分母
    assert {p['id']: p['status'] for p in r['per_step']}['free'] == 'skipped'


def test_compute_attempt_fit_end_to_end(db_conn, tmp_path, monkeypatch):
    """编排：manifests 定义 + 账本轨迹 → 结果行落库（attempt + def_name 冲突覆盖）。

    种子链路：users → batches → sessions → attempts → manifests → 账本轨迹；
    定义文件写到 tmp_path/demo-skill/SKILL.md（frontmatter 两步：bash
    'git clone' 命中 + glob miss），manifest 的 path 指向该文件、content_hash
    用文件 sha256。清理顺序按 FK 依赖：fit_results → manifests → attempts →
    账本 → sessions → batches → users。
    """
    import hashlib
    import uuid as _uuid
    from db import get_db
    from utils import skill_fit

    uid, sid, bid, attempt = (str(_uuid.uuid4()) for _ in range(4))
    # 定义文件：一个 expect 命中（bash git clone）、一个 expect 缺失（无 glob 调用）
    skill_dir = os.path.join(str(tmp_path), 'demo-skill')
    os.makedirs(skill_dir, exist_ok=True)
    md_path = os.path.join(skill_dir, 'SKILL.md')
    md_text = """---
description: 拟合演示
fit:
  steps:
    - id: clone
      name: 克隆仓库
      expect:
        - tool: bash
          args_pattern: 'git clone'
    - id: inspect
      name: 检查结构
      expect:
        - tool: glob
---
正文内容忽略。
"""
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_text)
    md_hash = hashlib.sha256(md_text.encode('utf-8')).hexdigest()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'SF', 'developer')", (uid, f'sf_{uid[:8]}'))
                cur.execute(
                    "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                    "VALUES (%s, %s, 'sf', 'p', 1)", (bid, uid))
                cur.execute(
                    "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                    "  batch_seq, workspace_path, session_token) "
                    "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                    (sid, uid, bid, f'C:\\sf\\{sid}', f'tok-{sid[:12]}'))
                # started_at/finished_at 与账本 occurred_at（事务内 now()）同处
                # 一个事务，now() 为事务起点 → 轨迹调用恰好落在时窗右端点
                # （BETWEEN 含端点），不依赖时钟推进。
                cur.execute(
                    "INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW() - interval '5 minutes', NOW())",
                    (attempt, sid))
                cur.execute(
                    "INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                    "  source, path, content_hash, injected) "
                    "VALUES (%s, %s, 'skill', 'demo-skill', 'session', %s, %s, true)",
                    ('man_' + _uuid.uuid4().hex[:8], attempt, md_path, md_hash))
                cur.execute(
                    "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                    "  subtask_id, agent, part_id, tool, args_text, state) "
                    "VALUES (%s, %s, NULL, NULL, 'p1', 'bash', 'git clone x', 'completed')",
                    (f'oc-sf-{attempt[:8]}', sid))

        rows = skill_fit.compute_attempt_fit(attempt)
        assert len(rows) == 1
        r = rows[0]
        assert r['status'] == 'partial' and r['score'] == 50
        assert r['per_step'][0]['status'] == 'hit'
        assert r['per_step'][1]['status'] == 'miss'
        assert r['attempt_id'] == attempt and r['session_id'] == sid
        assert r['def_kind'] == 'skill' and r['def_name'] == 'demo-skill'
        assert r['def_hash'] == md_hash

        # 重复调用幂等：attempt_id + def_name 冲突覆盖，仍 1 行
        rows2 = skill_fit.compute_attempt_fit(attempt)
        assert len(rows2) == 1 and rows2[0]['id'] == r['id']
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_skill_fit_results "
                        "WHERE attempt_id = %s", (attempt,))
            assert cur.fetchone()[0] == 1

        # compute_for_session：现查最新 attempt 后委托，返回同结果
        rows3 = skill_fit.compute_for_session(sid)
        assert rows3 and len(rows3) == 1
        assert rows3[0]['status'] == 'partial' and rows3[0]['score'] == 50
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id = %s",
                            (attempt,))
                cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id = %s",
                            (attempt,))
                cur.execute("DELETE FROM ai_execution_attempts WHERE id = %s", (attempt,))
                cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id = %s",
                            (sid,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def test_compute_attempt_fit_ignores_error_state_calls(db_conn, tmp_path):
    """终审 Fix 1：轨迹只认 completed——state='error' 的调用不算「按定义
    执行」（与动作门禁 require_state='completed' 惯例一致），且不会被贪心
    指针消耗污染后续匹配。

    种子：定义两步——A: bash（无 pattern，任意 bash 即命中）、B: bash
    'echo ok'；轨迹两条同参调用：error 在前、completed 在后。若 error
    参与匹配，A 会先消耗 error 行，B 命中 completed 行 → fit 100；过滤后
    轨迹只剩 completed 行，A 消耗之、B miss → partial 50。"""
    import hashlib
    import uuid as _uuid
    from db import get_db
    from utils import skill_fit

    uid, sid, bid, attempt = (str(_uuid.uuid4()) for _ in range(4))
    skill_dir = os.path.join(str(tmp_path), 'err-skill')
    os.makedirs(skill_dir, exist_ok=True)
    md_path = os.path.join(skill_dir, 'SKILL.md')
    md_text = """---
description: 错误调用过滤演示
fit:
  steps:
    - id: run
      name: 跑命令
      expect:
        - tool: bash
    - id: probe
      name: 探活
      expect:
        - tool: bash
          args_pattern: 'echo ok'
---
"""
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_text)
    md_hash = hashlib.sha256(md_text.encode('utf-8')).hexdigest()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'SF', 'developer')", (uid, f'sfe_{uid[:8]}'))
                cur.execute(
                    "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                    "VALUES (%s, %s, 'sf', 'p', 1)", (bid, uid))
                cur.execute(
                    "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                    "  batch_seq, workspace_path, session_token) "
                    "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                    (sid, uid, bid, f'C:\\sfe\\{sid}', f'tok-{sid[:12]}'))
                cur.execute(
                    "INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW() - interval '5 minutes', NOW())",
                    (attempt, sid))
                cur.execute(
                    "INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                    "  source, path, content_hash, injected) "
                    "VALUES (%s, %s, 'skill', 'err-skill', 'session', %s, %s, true)",
                    ('man_' + _uuid.uuid4().hex[:8], attempt, md_path, md_hash))
                cur.execute(
                    "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                    "  subtask_id, agent, part_id, tool, args_text, state, occurred_at) "
                    "VALUES (%s, %s, NULL, NULL, 'p1', 'bash', 'echo ok', 'error', "
                    "  NOW() - interval '1 minute')",
                    (f'oc-sfe-{attempt[:8]}', sid))
                cur.execute(
                    "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                    "  subtask_id, agent, part_id, tool, args_text, state, occurred_at) "
                    "VALUES (%s, %s, NULL, NULL, 'p2', 'bash', 'echo ok', 'completed', NOW())",
                    (f'oc-sfe-{attempt[:8]}', sid))

        rows = skill_fit.compute_attempt_fit(attempt)
        assert len(rows) == 1
        r = rows[0]
        assert r['status'] == 'partial' and r['score'] == 50
        statuses = [p['status'] for p in r['per_step']]
        assert statuses == ['hit', 'miss']
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id = %s",
                            (attempt,))
                cur.execute("DELETE FROM ai_skill_def_versions WHERE content_hash = %s "
                            "AND def_name = 'err-skill'", (md_hash,))
                cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id = %s",
                            (attempt,))
                cur.execute("DELETE FROM ai_execution_attempts WHERE id = %s", (attempt,))
                cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id = %s",
                            (sid,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def test_compute_for_session_hook_invoked(db_conn, monkeypatch):
    """收敛钩子入口：会话无 attempt → None 且不抛（非审计来源不触发拟合）；
    有 attempt → 委托 compute_attempt_fit（打桩计数，入参是 attempt id）。"""
    import uuid as _uuid
    from db import get_db
    from utils import skill_fit

    uid, sid, att_sid, bid, attempt = (str(_uuid.uuid4()) for _ in range(5))
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'SF', 'developer')", (uid, f'sfh_{uid[:8]}'))
                cur.execute(
                    "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                    "VALUES (%s, %s, 'sf', 'p', 2)", (bid, uid))
                for s, seq in ((sid, 0), (att_sid, 1)):
                    cur.execute(
                        "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, workspace_path, session_token) "
                        "VALUES (%s, %s, 'completed', %s, %s, %s, %s)",
                        (s, uid, bid, seq, f'C:\\sf\\{s}', f'tok-{s[:12]}'))
                cur.execute(
                    "INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW() - interval '5 minutes', NOW())",
                    (attempt, att_sid))

        called = []
        monkeypatch.setattr(skill_fit, 'compute_attempt_fit',
                            lambda a, get_db=None: called.append(a) or [])
        # 无 attempt 的会话 → None（不委托）
        assert skill_fit.compute_for_session(sid, get_db=get_db) is None
        # 有 attempt 的会话 → 委托 compute_attempt_fit(attempt_id)（打桩返回 []）
        assert skill_fit.compute_for_session(att_sid, get_db=get_db) == []
        assert called == [attempt]
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_execution_attempts WHERE id = %s", (attempt,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id IN %s",
                            ((sid, att_sid),))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def test_compute_registers_def_versions(db_conn, tmp_path):
    """spec §3.4 定义版本自动注册：拟合计算遇到新 (def_kind, def_name,
    content_hash) → ai_skill_def_versions 出现对应行（first_seen_at 只记
    首次）；重复 compute 幂等——版本行不翻倍、时间戳不变。"""
    import hashlib
    import uuid as _uuid
    from db import get_db
    from utils import skill_fit

    uid, sid, bid, attempt = (str(_uuid.uuid4()) for _ in range(4))
    skill_dir = os.path.join(str(tmp_path), 'reg-skill')
    os.makedirs(skill_dir, exist_ok=True)
    md_path = os.path.join(skill_dir, 'SKILL.md')
    md_text = """---
description: 版本注册演示
fit:
  steps:
    - id: ping
      name: 探活
      expect:
        - tool: bash
          args_pattern: 'echo'
---
"""
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_text)
    md_hash = hashlib.sha256(md_text.encode('utf-8')).hexdigest()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'SF', 'developer')", (uid, f'sfr_{uid[:8]}'))
                cur.execute(
                    "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                    "VALUES (%s, %s, 'sfr', 'p', 1)", (bid, uid))
                cur.execute(
                    "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                    "  batch_seq, workspace_path, session_token) "
                    "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                    (sid, uid, bid, f'C:\sfr\{sid}', f'tok-{sid[:12]}'))
                cur.execute(
                    "INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW() - interval '5 minutes', NOW())",
                    (attempt, sid))
                cur.execute(
                    "INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                    "  source, path, content_hash, injected) "
                    "VALUES (%s, %s, 'skill', 'reg-skill', 'session', %s, %s, true)",
                    ('man_' + _uuid.uuid4().hex[:8], attempt, md_path, md_hash))
                cur.execute(
                    "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                    "  subtask_id, agent, part_id, tool, args_text, state) "
                    "VALUES (%s, %s, NULL, NULL, 'p1', 'bash', 'echo ok', 'completed')",
                    (f'oc-sfr-{attempt[:8]}', sid))

        rows = skill_fit.compute_attempt_fit(attempt)
        assert len(rows) == 1 and rows[0]['status'] == 'fit'

        def _fetch_versions(cur):
            cur.execute(
                "SELECT id, def_kind, def_name, content_hash, first_seen_at "
                "FROM ai_skill_def_versions "
                "WHERE def_kind = 'skill' AND def_name = 'reg-skill' "
                "  AND content_hash = %s", (md_hash,))
            return cur.fetchall()

        with db_conn.cursor() as cur:
            vrows = _fetch_versions(cur)
        assert len(vrows) == 1                       # 新 hash 自动注册
        assert vrows[0][1] == 'skill' and vrows[0][2] == 'reg-skill'

        # 重复 compute：幂等——版本行不翻倍，first_seen_at 不变
        skill_fit.compute_attempt_fit(attempt)
        with db_conn.cursor() as cur:
            vrows2 = _fetch_versions(cur)
        assert len(vrows2) == 1
        assert vrows2[0][0] == vrows[0][0]
        assert vrows2[0][4] == vrows[0][4]
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id = %s",
                            (attempt,))
                cur.execute("DELETE FROM ai_skill_def_versions WHERE content_hash = %s "
                            "AND def_name = 'reg-skill'", (md_hash,))
                cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id = %s",
                            (attempt,))
                cur.execute("DELETE FROM ai_execution_attempts WHERE id = %s", (attempt,))
                cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id = %s",
                            (sid,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def test_match_steps_out_of_order_detected():
    """重排对齐：期望调用存在但次序错乱 → out_of_order（不再误报 miss）；
    全部错序 → status='reordered'。"""
    steps = parse_fit_steps(SKILL_MD)
    # 次序全错：write(1) → glob(2) → bash clone(3)
    trace = [_t('write', 'outputs/r.md', 1), _t('glob', '**/*.py', 2),
             _t('bash', 'git clone x', 3)]
    r = match_steps(steps, trace)
    statuses = {p['id']: p['status'] for p in r['per_step']}
    # 贪心：clone 消耗位置 3 的 bash（指针前扫），inspect/report 的 glob/write
    # 在指针之前 → out_of_order
    assert statuses == {'clone': 'hit', 'inspect': 'out_of_order',
                        'report': 'out_of_order'}
    assert r['order_violations'] == 2 and r['status'] == 'partial'
    assert r['score'] == 33                          # 1/3


def test_match_steps_partial_with_reorder_tail():
    """混合：clone 顺序命中，report 错序出现 → partial + out_of_order。"""
    steps = parse_fit_steps(SKILL_MD)
    trace = [_t('bash', 'git clone x', 1), _t('write', 'outputs/r.md', 2)]
    r = match_steps(steps, trace)
    statuses = {p['id']: p['status'] for p in r['per_step']}
    assert statuses['clone'] == 'hit'
    assert statuses['inspect'] == 'miss'             # glob 全程未出现
    assert statuses['report'] == 'out_of_order'      # write 存在但指针已过
    assert r['order_violations'] == 1
    assert r['status'] == 'partial'


def test_match_steps_completely_absent_still_miss():
    """期望工具全程未出现 → 仍 miss（重排对齐不虚构证据）。"""
    steps = parse_fit_steps(SKILL_MD)
    trace = [_t('bash', 'echo hi', 1), _t('grep', 'x', 2)]
    r = match_steps(steps, trace)
    assert r['status'] == 'diverged'
    assert all(p['status'] == 'miss' for p in r['per_step'])


# ── 定义路径自动定位（生成器预填，2026-10-07） ─────────────────────────────

def _seed_manifests(db_conn, name, entries):
    """最小种子链：user + session + attempt，再按 entries 插 manifest 行。
    entries = [(path, content_hash, exists_on_disk 已由调用方保证,
    created_at 按序递减)]，返回 (uid, sid, attempt)。"""
    import uuid as _uuid
    from db import get_db
    uid, sid, attempt = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, display_name, role) "
                "VALUES (%s, %s, 'x', 'RV', 'developer')", (uid, f'rv_{uid[:8]}'))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                "VALUES (%s, %s, 'completed', %s)", (sid, uid, f'tok-{sid[:12]}'))
            cur.execute(
                "INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                "  operation, started_at, finished_at) "
                "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
            for i, (path, chash, minutes_ago) in enumerate(entries):
                cur.execute(
                    "INSERT INTO ai_execution_manifests (id, attempt_id, kind, "
                    "  name, source, path, content_hash, injected, created_at) "
                    "VALUES (%s, %s, 'skill', %s, 'platform_global', %s, %s, "
                    "  true, NOW() - (%s || ' minutes')::interval)",
                    (f'man_{_uuid.uuid4().hex[:8]}', attempt, name, path, chash,
                     str(minutes_ago)))
    db_conn.commit()
    return uid, sid, attempt


def _cleanup_manifest_seed(db_conn, uid, sid, attempt):
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    db_conn.commit()


def test_resolve_definition_path_prefers_hash_then_existing(db_conn, tmp_path):
    """hash 一致且文件在 → hash_match；hash 指向已删文件 → 回落最新仍存在
    的注入（manifest_latest）；全部文件缺失/无记录 → None。"""
    import hashlib
    import uuid as _uuid
    from utils.skill_fit import resolve_definition_path
    old_md = tmp_path / 'old'
    old_md.mkdir()
    (old_md / 'SKILL.md').write_text('old version', encoding='utf-8')
    gone_md = tmp_path / 'gone'           # 曾被注入、现已被删
    h_old = hashlib.sha256(b'old version').hexdigest()
    h_gone = hashlib.sha256(b'gone version').hexdigest()
    name = f'rslv-{_uuid.uuid4().hex[:6]}'
    uid, sid, attempt = _seed_manifests(db_conn, name, [
        (str(gone_md / 'SKILL.md'), h_gone, 0),          # 最新：文件已删
        (str(old_md / 'SKILL.md'), h_old, 10),           # 较旧：文件仍在
    ])
    try:
        # hash 命中仍在盘的旧版本（走默认 _default_get_db 工厂，与生产一致）
        r = resolve_definition_path('skill', name, h_old)
        assert r == {'path': str(old_md / 'SKILL.md'), 'contentHash': h_old,
                     'source': 'manifest_hash_match'}
        # hash 指向已删文件 → 跳过，回落最新仍存在的注入
        r2 = resolve_definition_path('skill', name, h_gone)
        assert r2['path'] == str(old_md / 'SKILL.md')
        assert r2['source'] == 'manifest_latest'
        # 不带 hash → 同样回落
        r3 = resolve_definition_path('skill', name)
        assert r3['source'] == 'manifest_latest'
        # 全部候选文件缺失：把旧文件也删掉 → None
        (old_md / 'SKILL.md').unlink()
        assert resolve_definition_path('skill', name) is None
        # 未知定义 → None；缺参 → None
        assert resolve_definition_path('skill', 'no-such-name') is None
        assert resolve_definition_path('skill', '') is None
    finally:
        _cleanup_manifest_seed(db_conn, uid, sid, attempt)


def test_resolve_definition_path_falls_back_to_oc_global(tmp_path, monkeypatch):
    """系统技能兜底定位（2026-10-11）：系统技能在 2026-10-11 之前没有
    manifest 注入记录，生成器/回滚的路径定位回落 OC 受管技能根——按名字
    找到技能目录里的 SKILL.md 即返回（source='oc_global'），带正文 hash。"""
    import os as _os
    import utils.opencode_global as ocg
    gdir = tmp_path / 'oc-global'
    (gdir / 'skill' / 'my-oc-skill').mkdir(parents=True)
    body = '---\nname: my-oc-skill\n---\nbody'
    # write_bytes：Windows 文本模式会把 \n 转成 \r\n，sha256 就对不上正文了
    (gdir / 'skill' / 'my-oc-skill' / 'SKILL.md').write_bytes(body.encode('utf-8'))
    monkeypatch.setattr(ocg.config, 'OPENCODE_GLOBAL_DIR', str(gdir))
    from utils.skill_fit import resolve_definition_path
    r = resolve_definition_path('skill', 'my-oc-skill')
    assert r is not None
    assert r['source'] == 'oc_global'
    assert r['path'] == str(gdir / 'skill' / 'my-oc-skill' / 'SKILL.md')
    import hashlib as _h
    assert r['contentHash'] == _h.sha256(body.encode('utf-8')).hexdigest()
    # OC 根里没有的名字 → 仍为 None；agent 类不走该兜底
    assert resolve_definition_path('skill', 'absent-oc-skill') is None
    assert resolve_definition_path('agent', 'my-oc-skill') is None
