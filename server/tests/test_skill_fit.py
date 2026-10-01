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


def test_match_steps_out_of_order_is_miss():
    steps = parse_fit_steps(SKILL_MD)
    # write 先于 bash：贪心顺序下 clone 错过 bash（被 write 消耗前的指针…）
    # 指针扫描：clone 找 bash——轨迹里 bash 在 write 之后出现仍可命中（指针
    # 只前进不回头，但 clone 是第一步，bash 在位置 1 命中）——真正乱序用例：
    trace = [_t('write', 'outputs/r.md', 1), _t('bash', 'git clone x', 2),
             _t('glob', '**/*', 3)]
    r = match_steps(steps, trace)
    # clone 命中位置 2；inspect 命中位置 3；report 需要的 write 在位置 1
    # （指针已过）→ miss
    statuses = {p['id']: p['status'] for p in r['per_step']}
    assert statuses == {'clone': 'hit', 'inspect': 'hit', 'report': 'miss'}
    assert r['status'] == 'partial'


def test_match_steps_no_trace():
    r = match_steps(parse_fit_steps(SKILL_MD), [])
    assert r['status'] == 'no_trace' and r['score'] == 0


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
